from __future__ import annotations

import random
import secrets

import discord

from strife.config import AppConfig
from strife.config.text import TextConfig
from strife.engine.metadata import GameMetadata, OptionType
from strife.engine.players import Player
from strife.engine.registry import GameRegistry
from strife.engine.roles import order_players
from strife.engine.session import GameSession
from strife.logging import get_logger
from strife.matchmaking.lobby import Lobby, LobbyMember, QueuedBot
from strife.matchmaking.lobby_view import build_lobby_view
from strife.matchmaking.registries import SessionRegistries, UserLocation
from strife.matchmaking.settings_view import build_settings_view
from strife.persistence.repositories import FinishedMatch, GuildRepository, MatchRepository, PlayerResult, UserRepository
from strife.presentation.compiler import Compiler
from strife.presentation.emoji import EmojiResolver
from strife.presentation.components import Container, LayoutView, TextDisplay, TextSize
from strife.presentation.message import ViewSurface
from strife.routing import prefixes as P
from strife.routing.custom_id import Route

log = get_logger("matchmaking.service")


class SessionFinalizer:
    def __init__(
        self,
        *,
        registries: SessionRegistries,
        matches: MatchRepository,
        users: UserRepository,
        guilds: GuildRepository,
    ) -> None:
        self.registries = registries
        self.matches = matches
        self.users = users
        self.guilds = guilds

    async def persist_and_release(self, finished: FinishedMatch, outcome) -> tuple[int, str]:
        match_id = await self.matches.create_finished(finished)
        results = [
            PlayerResult(user_id=p.user_id, display_name=p.display_name, result=p.result or "loss")
            for p in finished.players
            if p.user_id and not p.is_bot and p.result
        ]
        if results:
            await self.users.apply_results(results, finished.game_key)
        detail = await self.matches.get(match_id)
        code = detail.code if detail else str(match_id)
        return match_id, code

    async def session_complete(self, session: GameSession) -> None:
        self.registries.active_games.pop(session.thread_id, None)
        for player in session.players:
            if player.user_id and not player.is_bot:
                await self.registries.release_user(player.user_id)


class LobbyService:
    def __init__(
        self,
        bot: discord.Client,
        registries: SessionRegistries,
        registry: GameRegistry,
        compiler: Compiler,
        config: AppConfig,
        emoji: EmojiResolver,
        finalizer: SessionFinalizer,
        lifecycle=None,
    ) -> None:
        self.bot = bot
        self.registries = registries
        self.registry = registry
        self.compiler = compiler
        self.config = config
        self.emoji = emoji
        self.text = config.text
        self.finalizer = finalizer
        self.lifecycle = lifecycle

    def _meta(self, game_key: str) -> GameMetadata:
        return self.registry.metadata(game_key)


    def _default_settings(self, meta: GameMetadata) -> dict:
        settings = {}
        yaml_overrides = self.config.games.for_game(meta.key).settings_overrides
        for option in meta.settings:
            settings[option.key] = yaml_overrides.get(option.key, option.default)
        return settings

    async def create_lobby(self, interaction: discord.Interaction, game_key: str, private: bool) -> None:
        game_cfg = self.config.games.for_game(game_key)
        if not game_cfg.enabled:
            await interaction.response.send_message(self.text.get("errors.game_disabled"), ephemeral=True)
            return
        if not await self.registries.reserve_user(
            interaction.user.id, UserLocation("lobby", 0, interaction.guild_id)
        ):
            await interaction.response.send_message(self.text.get("errors.already_in_session"), ephemeral=True)
            return

        meta = self._meta(game_key)
        guild_repo = GuildRepository(self.finalizer.matches._pool)  # noqa: SLF001
        await guild_repo.upsert(interaction.guild_id)
        channel_id = await guild_repo.get_default_channel(interaction.guild_id)
        channel = interaction.guild.get_channel(channel_id) if channel_id else interaction.channel
        if channel is None:
            channel = interaction.channel
        lobby_id = secrets.randbits(63)
        lobby = Lobby(
            thread_id=lobby_id,
            guild_id=interaction.guild_id,
            channel_id=channel.id,
            game_key=game_key,
            creator_id=interaction.user.id,
            private=private,
            members=[LobbyMember(interaction.user.id, interaction.user.display_name)],
            settings=self._default_settings(meta),
        )
        surface = ViewSurface(self.compiler, prefix=P.LOBBY_JOIN, resource_id=lobby_id)
        surface.set_prefix(P.LOBBY_JOIN)
        lobby.surface = surface
        self.registries.user_location[interaction.user.id] = UserLocation("lobby", lobby_id, interaction.guild_id)
        self.registries.add_lobby(lobby)
        view = build_lobby_view(lobby, meta, self.emoji, self.text)
        await surface.send(channel, view)
        lobby.message_id = surface.message_id
        await interaction.response.send_message(f"Lobby created in {channel.mention}", ephemeral=True)

    async def handle(self, route: Route, interaction: discord.Interaction) -> None:
        lobby = self.registries.get_lobby(route.resource_id)
        if lobby is None:
            await interaction.response.send_message(self.text.get("lobby.closed"), ephemeral=True)
            return
        async with lobby.lock:
            handler = {
                P.LOBBY_JOIN: self._join,
                P.LOBBY_LEAVE: self._leave,
                P.LOBBY_READY: self._ready,
                P.LOBBY_ASSIGN: self._assign,
                P.LOBBY_SETTINGS: self._settings,
                P.LOBBY_ROLE: self._role,
                P.LOBBY_PRIV: self._privacy,
                P.LOBBY_RESET_PRIV: self._reset_privacy,
                P.LOBBY_OPT: self._option,
                P.LOBBY_RESET_RULES: self._reset_rules,
                P.LOBBY_END: self._end,
            }.get(route.prefix)
            if handler is None:
                await interaction.response.send_message(self.text.get("common.error"), ephemeral=True)
                return
            await handler(lobby, route, interaction)

    async def _refresh(self, lobby: Lobby, interaction: discord.Interaction) -> None:
        meta = self._meta(lobby.game_key)
        view = build_lobby_view(lobby, meta, self.emoji, self.text)
        if not interaction.response.is_done():
            await interaction.response.defer()
        if lobby.surface:
            await lobby.surface.update(view)

    async def _join(self, lobby: Lobby, route: Route, interaction: discord.Interaction) -> None:
        user = interaction.user
        if any(m.user_id == user.id for m in lobby.members):
            await interaction.response.send_message(self.text.get("lobby.joined"), ephemeral=True)
            return
        if lobby.private and lobby.whitelist and user.id not in lobby.whitelist:
            await interaction.response.send_message(self.text.get("common.forbidden"), ephemeral=True)
            return
        if user.id in lobby.blacklist:
            await interaction.response.send_message(self.text.get("common.forbidden"), ephemeral=True)
            return
        if not await self.registries.reserve_user(user.id, UserLocation("lobby", lobby.thread_id, lobby.guild_id)):
            await interaction.response.send_message(self.text.get("errors.already_in_session"), ephemeral=True)
            return
        lobby.members.append(LobbyMember(user.id, user.display_name))
        await self._refresh(lobby, interaction)

    async def _leave(self, lobby: Lobby, route: Route, interaction: discord.Interaction) -> None:
        user = interaction.user
        lobby.members = [m for m in lobby.members if m.user_id != user.id]
        lobby.ready.discard(user.id)
        await self.registries.release_user(user.id)
        if user.id == lobby.creator_id and lobby.members:
            lobby.creator_id = lobby.members[0].user_id
        if not lobby.members:
            await self._teardown(lobby, interaction)
            return
        await self._refresh(lobby, interaction)

    async def _ready(self, lobby: Lobby, route: Route, interaction: discord.Interaction) -> None:
        if interaction.user.id in lobby.ready:
            lobby.ready.discard(interaction.user.id)
            await self._refresh(lobby, interaction)
        else:
            meta = self._meta(lobby.game_key)
            ok, reason = lobby.can_ready(meta)
            if not ok:
                await interaction.response.send_message(
                    self.text.get("lobby.cannot_start", reason=reason or "unknown"), ephemeral=True
                )
                return
            lobby.ready.add(interaction.user.id)
            ok_start, _ = lobby.can_start(meta)
            if ok_start:
                await self._start(lobby, route, interaction)
            else:
                await self._refresh(lobby, interaction)

    async def _assign(self, lobby: Lobby, route: Route, interaction: discord.Interaction) -> None:
        await self._refresh(lobby, interaction)

    async def _settings(self, lobby: Lobby, route: Route, interaction: discord.Interaction) -> None:
        if interaction.user.id != lobby.creator_id:
            await interaction.response.send_message(self.text.get("lobby.creator_only"), ephemeral=True)
            return
        meta = self._meta(lobby.game_key)
        view = build_settings_view(lobby, meta, self.text)
        compiled = self.compiler.compile(view, resource_id=lobby.thread_id, prefix=P.LOBBY_SETTINGS)
        await interaction.response.send_message(view=compiled, ephemeral=True)

    async def _role(self, lobby: Lobby, route: Route, interaction: discord.Interaction) -> None:
        player_id = int(route.payload.get("player_id", interaction.user.id))
        values = interaction.data.get("values") if interaction.data else []
        if values:
            lobby.role_selection[player_id] = values[0]
        await self._refresh(lobby, interaction)

    async def _privacy(self, lobby: Lobby, route: Route, interaction: discord.Interaction) -> None:
        if interaction.user.id != lobby.creator_id:
            await interaction.response.send_message(self.text.get("lobby.creator_only"), ephemeral=True)
            return
        values = interaction.data.get("values") if interaction.data else []
        if values:
            lobby.private = values[0] == "private"
        await interaction.response.defer(ephemeral=True)
        meta = self._meta(lobby.game_key)
        view = build_settings_view(lobby, meta, self.text)
        compiled = self.compiler.compile(view, resource_id=lobby.thread_id, prefix=P.LOBBY_SETTINGS)
        await interaction.edit_original_response(view=compiled)

    async def _reset_privacy(self, lobby: Lobby, route: Route, interaction: discord.Interaction) -> None:
        if interaction.user.id != lobby.creator_id:
            await interaction.response.send_message(self.text.get("lobby.creator_only"), ephemeral=True)
            return
        lobby.private = False
        lobby.whitelist.clear()
        lobby.blacklist.clear()
        await interaction.response.defer(ephemeral=True)
        meta = self._meta(lobby.game_key)
        view = build_settings_view(lobby, meta, self.text)
        compiled = self.compiler.compile(view, resource_id=lobby.thread_id, prefix=P.LOBBY_SETTINGS)
        await interaction.edit_original_response(view=compiled)

    async def _option(self, lobby: Lobby, route: Route, interaction: discord.Interaction) -> None:
        if interaction.user.id != lobby.creator_id:
            await interaction.response.send_message(self.text.get("lobby.creator_only"), ephemeral=True)
            return
        key = route.payload.get("option_key")
        opt_type = route.payload.get("option_type")
        values = interaction.data.get("values") if interaction.data else []
        if key and values:
            raw = values[0]
            if opt_type == OptionType.BOOL.value:
                lobby.settings[key] = raw == "true"
            elif opt_type == OptionType.INT.value:
                lobby.settings[key] = int(raw)
            else:
                lobby.settings[key] = raw
        await interaction.response.defer(ephemeral=True)
        meta = self._meta(lobby.game_key)
        view = build_settings_view(lobby, meta, self.text)
        compiled = self.compiler.compile(view, resource_id=lobby.thread_id, prefix=P.LOBBY_SETTINGS)
        await interaction.edit_original_response(view=compiled)
        await self._refresh(lobby, interaction)

    async def _reset_rules(self, lobby: Lobby, route: Route, interaction: discord.Interaction) -> None:
        if interaction.user.id != lobby.creator_id:
            await interaction.response.send_message(self.text.get("lobby.creator_only"), ephemeral=True)
            return
        lobby.settings = self._default_settings(self._meta(lobby.game_key))
        await interaction.response.defer(ephemeral=True)
        meta = self._meta(lobby.game_key)
        view = build_settings_view(lobby, meta, self.text)
        compiled = self.compiler.compile(view, resource_id=lobby.thread_id, prefix=P.LOBBY_SETTINGS)
        await interaction.edit_original_response(view=compiled)
        await self._refresh(lobby, interaction)

    async def _end(self, lobby: Lobby, route: Route, interaction: discord.Interaction) -> None:
        if interaction.user.id != lobby.creator_id:
            await interaction.response.send_message(self.text.get("lobby.creator_only"), ephemeral=True)
            return
        await self._teardown(lobby, interaction)

    async def _start(self, lobby: Lobby, route: Route, interaction: discord.Interaction) -> None:
        meta = self._meta(lobby.game_key)
        ok, reason = lobby.can_start(meta)
        if not ok:
            await interaction.response.send_message(
                self.text.get("lobby.cannot_start", reason=reason or "unknown"), ephemeral=True
            )
            return
        seed = secrets.randbits(63)
        rng = random.Random(seed)
        players: list[Player] = []
        for member in lobby.members:
            players.append(
                Player(
                    seat=0,
                    user_id=member.user_id,
                    display_name=member.display_name,
                )
            )
        for idx, bot in enumerate(lobby.bots):
            players.append(
                Player(
                    seat=0,
                    user_id=None,
                    display_name=bot.name,
                    is_bot=True,
                    bot_difficulty=bot.difficulty,
                )
            )
        players = order_players(players, meta.player_order.value, rng, creator_id=lobby.creator_id)
        game = self.registry.create(
            lobby.game_key,
            players,
            lobby.settings,
            seed,
            lobby_selection=lobby.role_selection,
        )
        if lobby.surface is None:
            await interaction.response.send_message(self.text.get("common.error"), ephemeral=True)
            return

        # Create thread for the game
        channel = interaction.guild.get_channel(lobby.channel_id) if lobby.channel_id else interaction.channel
        if channel is None:
            channel = interaction.channel
        thread_name = f"{meta.name} Game"
        thread = await channel.create_thread(name=thread_name, auto_archive_duration=1440)

        # Invite players to thread
        for p in players:
            if p.user_id and not p.is_bot:
                member = interaction.guild.get_member(p.user_id)
                if member:
                    try:
                        await thread.add_user(member)
                    except discord.HTTPException:
                        pass

        # Update lobby message in channel to say game started
        ended_view = LayoutView()
        brand = self.emoji.get("brand_logo")
        ended_view.children.append(
            TextDisplay(markdown_content=f"## {brand} {self.text.get('lobby.title', game_name=meta.name)}", size_style=TextSize.HEADER)
        )
        container = Container()
        container.add_text(TextDisplay(markdown_content=f"Game started in {thread.mention}!", size_style=TextSize.BODY))
        ended_view.add_container(container)
        await lobby.surface.update(ended_view)

        # Create game surface and send first message to thread
        game_surface = ViewSurface(self.compiler, prefix=P.G_MOVE, resource_id=thread.id)
        starting_view = LayoutView()
        starting_view.children.append(
            TextDisplay(markdown_content=f"Starting {meta.name}...", size_style=TextSize.BODY)
        )
        await game_surface.send_to_thread(thread, starting_view)

        async def finalize_cb(finished: FinishedMatch, outcome):
            match_id, code = await self.finalizer.persist_and_release(finished, outcome)
            if self.lifecycle:
                self.lifecycle.register_session_end(thread.id, match_id, outcome, players)
            return match_id, code

        finalize_cb.session_complete = self.finalizer.session_complete  # type: ignore[attr-defined]

        session = GameSession(
            thread_id=thread.id,
            guild_id=lobby.guild_id,
            game=game,
            players=players,
            settings=dict(lobby.settings),
            seed=seed,
            surface=game_surface,
            text=self.text,
            finalize_cb=finalize_cb,
            game_key=lobby.game_key,
        )
        session.set_bot(self.bot)
        self.registries.promote(lobby.thread_id, session)
        if not interaction.response.is_done():
            await interaction.response.defer()
        await session.start()

    async def _teardown(self, lobby: Lobby, interaction: discord.Interaction) -> None:
        for member in lobby.members:
            await self.registries.release_user(member.user_id)
        self.registries.remove_lobby(lobby.thread_id)
        if not interaction.response.is_done():
            await interaction.response.defer()
        
        meta = self._meta(lobby.game_key)
        closed_view = LayoutView()
        brand = self.emoji.get("brand_logo")
        closed_view.children.append(
            TextDisplay(markdown_content=f"## {brand} {self.text.get('lobby.title', game_name=meta.name)}", size_style=TextSize.HEADER)
        )
        container = Container()
        container.add_text(TextDisplay(markdown_content=self.text.get("lobby.closed"), size_style=TextSize.BODY))
        closed_view.add_container(container)
        if lobby.surface:
            await lobby.surface.update(closed_view)

    async def add_bots(self, interaction: discord.Interaction, difficulty: str, number: int) -> None:
        loc = self.registries.location_of(interaction.user.id)
        if loc is None or loc.kind != "lobby":
            await interaction.response.send_message(self.text.get("common.forbidden"), ephemeral=True)
            return
        lobby = self.registries.get_lobby(loc.thread_id)
        if lobby is None or lobby.creator_id != interaction.user.id:
            await interaction.response.send_message(self.text.get("lobby.creator_only"), ephemeral=True)
            return
        for i in range(number):
            lobby.bots.append(QueuedBot(name=f"Bot-{difficulty}-{len(lobby.bots)+1}", difficulty=difficulty))
        meta = self._meta(lobby.game_key)
        view = build_lobby_view(lobby, meta, self.emoji, self.text)
        if lobby.surface:
            await lobby.surface.update(view)
        await interaction.response.send_message(self.text.get("lobby.bot_added", count=number), ephemeral=True)

    async def remove_bot(self, interaction: discord.Interaction, name: str) -> None:
        loc = self.registries.location_of(interaction.user.id)
        if loc is None or loc.kind != "lobby":
            await interaction.response.send_message(self.text.get("common.forbidden"), ephemeral=True)
            return
        lobby = self.registries.get_lobby(loc.thread_id)
        if lobby is None or lobby.creator_id != interaction.user.id:
            await interaction.response.send_message(self.text.get("lobby.creator_only"), ephemeral=True)
            return
        lobby.bots = [b for b in lobby.bots if b.name != name]
        meta = self._meta(lobby.game_key)
        view = build_lobby_view(lobby, meta, self.emoji, self.text)
        if lobby.surface:
            await lobby.surface.update(view)
        await interaction.response.send_message(self.text.get("lobby.bot_removed", name=name), ephemeral=True)

    async def open_settings(self, interaction: discord.Interaction, private: bool | None) -> None:
        loc = self.registries.location_of(interaction.user.id)
        if loc is None or loc.kind != "lobby":
            await interaction.response.send_message(self.text.get("common.forbidden"), ephemeral=True)
            return
        lobby = self.registries.get_lobby(loc.thread_id)
        if lobby is None:
            await interaction.response.send_message(self.text.get("lobby.closed"), ephemeral=True)
            return
        if private is not None:
            lobby.private = private
        await self._settings(lobby, Route(P.LOBBY_SETTINGS, lobby.thread_id, "settings", {}), interaction)
