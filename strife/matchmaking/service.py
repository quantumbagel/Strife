from __future__ import annotations

import random
import secrets

import discord

from strife.config import AppConfig
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
from strife.persistence.repositories import (
    FinishedMatch,
    GuildRepository,
    MatchRepository,
    PlayerResult,
    UserRepository,
    generate_match_code,
)
from strife.presentation.compiler import Compiler
from strife.presentation.emoji import EmojiResolver
from strife.presentation.components import Container, LayoutView, TextDisplay, TextSize, Separator
from strife.presentation.message import ViewSurface
from strife.presentation.roster import member_line
from strife.presentation.user_error import ErrorContext, UserErrorPresenter
from strife.presentation.user_success import UserSuccessPresenter
from strife.routing import prefixes as P
from strife.routing.custom_id import Route
from strife.settings import get_settings

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

    async def persist_and_release(
        self, finished: FinishedMatch, outcome
    ) -> tuple[int, str]:
        match_id, code = await self.matches.create_finished(finished)
        results = [
            PlayerResult(
                user_id=p.user_id,
                display_name=p.display_name,
                result=p.result or "loss",
            )
            for p in finished.players
            if p.user_id and not p.is_bot and p.result
        ]
        if results:
            await self.users.apply_results(results, finished.game_key)
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
        self.user_errors = UserErrorPresenter(compiler, emoji, config.text, registries)
        self.user_success = UserSuccessPresenter(compiler, emoji, config.text)

    def _error_ctx(
        self,
        interaction: discord.Interaction,
        *,
        lobby: Lobby | None = None,
        user_id: int | None = None,
        reason_key: str | None = None,
        reason_kwargs: dict | None = None,
    ) -> ErrorContext:
        uid = user_id if user_id is not None else interaction.user.id
        loc = self.registries.location_of(uid)
        return ErrorContext(
            interaction=interaction,
            user_id=uid,
            lobby=lobby,
            location=loc,
            reason_key=reason_key,
            reason_kwargs=reason_kwargs,
        )

    async def _error(
        self,
        interaction: discord.Interaction,
        code: str,
        *,
        lobby: Lobby | None = None,
        user_id: int | None = None,
        reason_key: str | None = None,
        reason_kwargs: dict | None = None,
    ) -> None:
        await self.user_errors.send(
            interaction,
            code,
            context=self._error_ctx(
                interaction,
                lobby=lobby,
                user_id=user_id,
                reason_key=reason_key,
                reason_kwargs=reason_kwargs,
            ),
        )

    def _meta(self, game_key: str) -> GameMetadata:
        try:
            return self.registry.metadata(game_key)
        except KeyError:
            raise RuntimeError("unknown_game") from None

    def _default_settings(self, meta: GameMetadata) -> dict:
        settings = {}
        yaml_overrides = self.config.games.for_game(meta.key).settings_overrides
        for option in meta.settings:
            settings[option.key] = yaml_overrides.get(option.key, option.default)
        return settings

    async def create_lobby(
        self, interaction: discord.Interaction, game_key: str, private: bool
    ) -> None:
        if game_key not in self.registry._games:  # noqa: SLF001
            await self._error(interaction, "errors.unknown_game")
            return
        game_cfg = self.config.games.for_game(game_key)
        if not game_cfg.enabled:
            await self._error(interaction, "errors.game_disabled")
            return
        if not await self.registries.reserve_user(
            interaction.user.id, UserLocation("lobby", 0, interaction.guild_id)
        ):
            await self._error(
                interaction,
                "errors.already_in_session",
                user_id=interaction.user.id,
            )
            return

        try:
            meta = self._meta(game_key)
            guild_repo = GuildRepository(self.finalizer.matches._pool)  # noqa: SLF001
            await guild_repo.upsert(interaction.guild_id)
            lobby_id = secrets.randbits(63)
            lobby = Lobby(
                thread_id=lobby_id,
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id or interaction.channel.id,
                game_key=game_key,
                creator_id=interaction.user.id,
                private=private,
                members=[LobbyMember(interaction.user.id, interaction.user.display_name)],
                settings=self._default_settings(meta),
            )
            surface = ViewSurface(self.compiler, prefix=P.LOBBY_JOIN, resource_id=lobby_id)
            surface.set_prefix(P.LOBBY_JOIN)
            lobby.surface = surface
            self.registries.user_location[interaction.user.id] = UserLocation(
                "lobby", lobby_id, interaction.guild_id
            )
            self.registries.add_lobby(lobby)
            view = build_lobby_view(lobby, meta, self.emoji, self.text)
            await surface.send(interaction, view)
            lobby.message_id = surface.message_id
        except Exception:
            await self.registries.release_user(interaction.user.id)
            raise

    async def handle(self, route: Route, interaction: discord.Interaction) -> None:
        lobby = self.registries.get_lobby(route.resource_id)
        if lobby is None:
            await self._disable_and_report_closed(interaction, "lobby.already_dead")
            return
        async with lobby.lock:
            if self.registries.get_lobby(route.resource_id) is not lobby:
                await self._disable_and_report_closed(interaction, "lobby.already_dead")
                return
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
                P.LOBBY_APPROVE: self._approve,
                P.LOBBY_DENY: self._deny,
                P.LOBBY_ADD_BLACKLIST: self._add_blacklist,
                P.LOBBY_REMOVE_BLACKLIST: self._remove_blacklist,
            }.get(route.prefix)
            if handler is None:
                await self._error(interaction, "common.error")
                return
            await handler(lobby, route, interaction)

    async def _refresh(self, lobby: Lobby, interaction: discord.Interaction) -> None:
        meta = self._meta(lobby.game_key)
        try:
            view = build_lobby_view(lobby, meta, self.emoji, self.text)
        except Exception as exc:
            from strife.presentation.compiler import LayoutError

            if isinstance(exc, LayoutError):
                await self._error(interaction, "common.error", lobby=lobby)
                return
            raise
        if not interaction.response.is_done():
            await interaction.response.defer()
        if lobby.surface:
            try:
                await lobby.surface.update(view)
            except Exception as exc:
                from strife.presentation.compiler import LayoutError

                if isinstance(exc, LayoutError):
                    await self._error(interaction, "common.error", lobby=lobby)
                    return
                raise

    async def _seat_member(
        self, lobby: Lobby, user_id: int, display_name: str, interaction: discord.Interaction
    ) -> bool:
        if not await self.registries.reserve_user(
            user_id, UserLocation("lobby", lobby.thread_id, lobby.guild_id)
        ):
            await self._error(
                interaction,
                "errors.already_in_session",
                user_id=user_id,
            )
            return False
        lobby.members.append(LobbyMember(user_id, display_name))
        return True

    async def _join(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        user = interaction.user
        if any(m.user_id == user.id for m in lobby.members):
            await self._error(interaction, "errors.already_in_lobby", lobby=lobby)
            return
        if user.id in lobby.blacklist:
            await self._error(interaction, "errors.blacklisted", lobby=lobby)
            return

        if lobby.private and user.id not in lobby.approved:
            if user.id in lobby.denied:
                await self._error(interaction, "errors.request_denied", lobby=lobby)
                return
            if user.id in lobby.pending_requests:
                await self._error(interaction, "errors.request_pending", lobby=lobby)
                return
            lobby.pending_requests[user.id] = user.display_name
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            await self._refresh(lobby, interaction)
            await self._success(
                interaction,
                "lobby.request_sent",
                creator=f"<@{lobby.creator_id}>",
            )
            return

        if not await self._seat_member(lobby, user.id, user.display_name, interaction):
            return
        await self._refresh(lobby, interaction)

    async def _leave(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        try:
            await self._leave_lobby_inner(lobby, interaction.user.id, interaction)
        except PermissionError:
            await self._error(interaction, "errors.not_in_lobby", lobby=lobby)

    async def leave_lobby(
        self, thread_id: int, user_id: int, interaction: discord.Interaction
    ) -> None:
        lobby = self.registries.get_lobby(thread_id)
        if lobby is None:
            raise RuntimeError("no_session")
        async with lobby.lock:
            await self._leave_lobby_inner(lobby, user_id, interaction)

    async def _leave_lobby_inner(
        self, lobby: Lobby, user_id: int, interaction: discord.Interaction
    ) -> None:
        if not any(m.user_id == user_id for m in lobby.members):
            raise PermissionError
        lobby.members = [m for m in lobby.members if m.user_id != user_id]
        lobby.ready.discard(user_id)
        await self.registries.release_user(user_id)
        if user_id == lobby.creator_id and lobby.members:
            lobby.creator_id = lobby.members[0].user_id

        if interaction.message and (not lobby.surface or interaction.message.id != lobby.surface.message_id):
            try:
                view = discord.ui.LayoutView.from_message(interaction.message)
                for item in view.walk_children():
                    if hasattr(item, "disabled"):
                        item.disabled = True

                if not interaction.response.is_done():
                    await interaction.response.edit_message(view=view)
                else:
                    await interaction.message.edit(view=view)
            except Exception as exc:
                log.warning("Failed to disable leave button: %s", exc)

        if not lobby.members:
            await self._teardown(lobby, interaction)
            return
        await self._refresh(lobby, interaction)

    async def _ready(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id in lobby.ready:
            lobby.ready.discard(interaction.user.id)
            await self._refresh(lobby, interaction)
        else:
            meta = self._meta(lobby.game_key)
            ok, reason_key, reason_kwargs = lobby.can_ready(meta, self.text)
            if not ok:
                await self._error(
                    interaction,
                    reason_key or "common.error",
                    lobby=lobby,
                    reason_key=reason_key,
                    reason_kwargs=reason_kwargs,
                )
                return
            lobby.ready.add(interaction.user.id)
            ok_start, _, _ = lobby.can_start(meta, self.text)
            if ok_start:
                if not interaction.response.is_done():
                    await interaction.response.defer()
                await self._start(lobby, route, interaction)
            else:
                await self._refresh(lobby, interaction)

    async def _assign(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        await self._refresh(lobby, interaction)

    async def _settings(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        meta = self._meta(lobby.game_key)
        view = build_settings_view(lobby, meta, self.emoji, self.text, interaction)
        compiled = self.compiler.compile(
            view, resource_id=lobby.thread_id, prefix=P.LOBBY_SETTINGS
        )
        await interaction.response.send_message(view=compiled, ephemeral=True)

    async def _role(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        player_id = int(route.payload.get("player_id", interaction.user.id))
        if player_id != interaction.user.id:
            await self._error(interaction, "errors.not_a_player", lobby=lobby)
            return
        if not any(m.user_id == player_id for m in lobby.members):
            await self._error(interaction, "errors.not_a_player", lobby=lobby)
            return
        values = interaction.data.get("values") if interaction.data else []
        if values:
            meta = self._meta(lobby.game_key)
            role_key = values[0]
            valid_roles = {r.key for r in meta.roles}
            if role_key not in valid_roles:
                await self._error(interaction, "errors.invalid_roles", lobby=lobby)
                return
            lobby.role_selection[player_id] = role_key
        await self._refresh(lobby, interaction)

    async def _privacy(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        values = interaction.data.get("values") if interaction.data else []
        if values:
            lobby.private = values[0] == "private"
        await interaction.response.defer(ephemeral=True)
        meta = self._meta(lobby.game_key)
        view = build_settings_view(lobby, meta, self.emoji, self.text, interaction)
        compiled = self.compiler.compile(
            view, resource_id=lobby.thread_id, prefix=P.LOBBY_SETTINGS
        )
        await interaction.edit_original_response(view=compiled)
        await self._refresh(lobby, interaction)

    async def _reset_privacy(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        lobby.private = False
        lobby.approved.clear()
        lobby.pending_requests.clear()
        lobby.denied.clear()
        lobby.blacklist.clear()
        await interaction.response.defer(ephemeral=True)
        meta = self._meta(lobby.game_key)
        view = build_settings_view(lobby, meta, self.emoji, self.text, interaction)
        compiled = self.compiler.compile(
            view, resource_id=lobby.thread_id, prefix=P.LOBBY_SETTINGS
        )
        await interaction.edit_original_response(view=compiled)
        await self._refresh(lobby, interaction)

    async def _option(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        key = route.payload.get("option_key")
        opt_type = route.payload.get("option_type")
        values = interaction.data.get("values") if interaction.data else []
        if key and values:
            meta = self._meta(lobby.game_key)
            option = next((o for o in meta.settings if o.key == key), None)
            if option is None:
                await self._error(interaction, "common.error", lobby=lobby)
                return
            raw = values[0]
            if opt_type == OptionType.BOOL.value:
                lobby.settings[key] = raw == "true"
            elif opt_type == OptionType.INT.value:
                try:
                    value = int(raw)
                except ValueError:
                    await self._error(interaction, "common.error", lobby=lobby)
                    return
                minimum = option.minimum if option.minimum is not None else int(option.default)
                maximum = option.maximum if option.maximum is not None else minimum + 5
                lobby.settings[key] = max(minimum, min(maximum, value))
            else:
                if option.type == OptionType.CHOICE and option.choices and raw not in option.choices:
                    await self._error(interaction, "common.error", lobby=lobby)
                    return
                lobby.settings[key] = raw
        await interaction.response.defer(ephemeral=True)
        meta = self._meta(lobby.game_key)
        view = build_settings_view(lobby, meta, self.emoji, self.text, interaction)
        compiled = self.compiler.compile(
            view, resource_id=lobby.thread_id, prefix=P.LOBBY_SETTINGS
        )
        await interaction.edit_original_response(view=compiled)
        await self._refresh(lobby, interaction)

    async def _reset_rules(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        lobby.settings = self._default_settings(self._meta(lobby.game_key))
        await interaction.response.defer(ephemeral=True)
        meta = self._meta(lobby.game_key)
        view = build_settings_view(lobby, meta, self.emoji, self.text, interaction)
        compiled = self.compiler.compile(
            view, resource_id=lobby.thread_id, prefix=P.LOBBY_SETTINGS
        )
        await interaction.edit_original_response(view=compiled)
        await self._refresh(lobby, interaction)

    async def _end(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        await self._teardown(lobby, interaction)

    async def _approve(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        values = interaction.data.get("values") if interaction.data else []
        if values:
            target_id = int(values[0])
            display_name = lobby.pending_requests.pop(target_id, f"User {target_id}")
            lobby.approved.add(target_id)
            lobby.denied.discard(target_id)
            if not any(m.user_id == target_id for m in lobby.members):
                await self._seat_member(lobby, target_id, display_name, interaction)
        await self._refresh(lobby, interaction)

    async def _deny(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        values = interaction.data.get("values") if interaction.data else []
        if values:
            target_id = int(values[0])
            lobby.pending_requests.pop(target_id, None)
            lobby.denied.add(target_id)
        await self._refresh(lobby, interaction)

    async def _add_blacklist(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        values = interaction.data.get("values") if interaction.data else []
        kicked = False
        if values:
            target_id = int(values[0])
            if target_id == lobby.creator_id:
                await self._error(interaction, "errors.cannot_blacklist_self", lobby=lobby)
                return
            lobby.blacklist.add(target_id)
            lobby.approved.discard(target_id)
            lobby.pending_requests.pop(target_id, None)
            if any(m.user_id == target_id for m in lobby.members):
                lobby.members = [m for m in lobby.members if m.user_id != target_id]
                lobby.ready.discard(target_id)
                await self.registries.release_user(target_id)
                kicked = True
        await interaction.response.defer(ephemeral=True)
        meta = self._meta(lobby.game_key)
        view = build_settings_view(lobby, meta, self.emoji, self.text, interaction)
        compiled = self.compiler.compile(
            view, resource_id=lobby.thread_id, prefix=P.LOBBY_SETTINGS
        )
        await interaction.edit_original_response(view=compiled)
        if kicked:
            await self._refresh(lobby, interaction)

    async def _remove_blacklist(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        values = interaction.data.get("values") if interaction.data else []
        if values:
            target_id = int(values[0])
            if target_id not in lobby.blacklist:
                await self._error(interaction, "errors.not_blacklisted", lobby=lobby)
                return
            lobby.blacklist.discard(target_id)
        await interaction.response.defer(ephemeral=True)
        meta = self._meta(lobby.game_key)
        view = build_settings_view(lobby, meta, self.emoji, self.text, interaction)
        compiled = self.compiler.compile(
            view, resource_id=lobby.thread_id, prefix=P.LOBBY_SETTINGS
        )
        await interaction.edit_original_response(view=compiled)

    async def _start(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if lobby.starting:
            return
        meta = self._meta(lobby.game_key)
        ok, reason_key, reason_kwargs = lobby.can_start(meta, self.text)
        if not ok:
            await self._error(
                interaction,
                reason_key or "common.error",
                lobby=lobby,
                reason_key=reason_key,
                reason_kwargs=reason_kwargs,
            )
            return
        lobby.starting = True
        reserved_members = list(lobby.members)
        try:
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
            players = order_players(
                players, meta.player_order.value, rng, creator_id=lobby.creator_id
            )
            game = self.registry.create(
                lobby.game_key,
                players,
                lobby.settings,
                seed,
                lobby_selection=lobby.role_selection,
            )
            if lobby.surface is None:
                await self._error(interaction, "common.error", lobby=lobby)
                return

            match_code = generate_match_code(random.Random())

            channel = (
                interaction.guild.get_channel(lobby.channel_id)
                if lobby.channel_id
                else interaction.channel
            )
            if channel is None:
                channel = interaction.channel
            thread_name = f"{meta.name} (#{match_code})"
            thread = await channel.create_thread(
                name=thread_name, auto_archive_duration=1440
            )

            human_players = [p for p in players if p.user_id and not p.is_bot]
            if human_players:
                mentions = " ".join(f"<@{p.user_id}>" for p in human_players)
                try:
                    await thread.send(mentions)
                except discord.HTTPException:
                    log.warning("Failed to mention players in game thread %s", thread.id)

            ended_view = LayoutView()
            brand = self.emoji.get("logo")
            container = Container()
            container.add_text(
                TextDisplay(
                    markdown_content=f"### {brand} {self.text.get('lobby.title', game_name=meta.name)}",
                    size_style=TextSize.HEADER,
                )
            )
            container.add_text(
                TextDisplay(
                    markdown_content=self.text.get(
                        "lobby.game_started", mention=thread.mention
                    ),
                    size_style=TextSize.BODY,
                )
            )
            ended_view.add_container(container)
            await lobby.surface.update(ended_view)

            header_surface = ViewSurface(
                self.compiler, prefix=P.REPLAY_NOOP, resource_id=thread.id
            )
            game_surface = ViewSurface(
                self.compiler, prefix=P.G_MOVE, resource_id=thread.id
            )
            game_emoji = self.emoji.get_game_emoji(meta.key)
            forward = self.emoji.get("forward")
            starting_view = LayoutView()
            start_container = Container()
            start_container.add_text(
                TextDisplay(
                    markdown_content=f"### {game_emoji} {meta.name} {forward} Match Start",
                    size_style=TextSize.HEADER,
                )
            )
            start_container.add_separator()

            settings = get_settings()
            roster_lines = [
                member_line(
                    self.emoji,
                    user_id=p.user_id,
                    display_name=p.display_name,
                    is_bot=p.is_bot,
                    bot_difficulty=p.bot_difficulty,
                    owner_ids=frozenset(settings.owner_ids),
                )
                for p in players
            ]
            start_container.add_text(
                TextDisplay(
                    markdown_content=f"{self.text.get('lobby.players_title')}\n" + "\n".join(roster_lines),
                    size_style=TextSize.BODY,
                )
            )
            start_container.add_separator(Separator(visible=False))
            start_container.add_text(
                TextDisplay(
                    markdown_content=f"-# {self.emoji.get('loading')} {self.text.get('lobby.game_in_progress')}",
                    size_style=TextSize.BODY,
                )
            )
            starting_view.add_container(start_container)
            await header_surface.send_to_thread(thread, starting_view)

            async def finalize_cb(finished: FinishedMatch, outcome):
                match_id, code = await self.finalizer.persist_and_release(finished, outcome)
                if self.lifecycle:
                    self.lifecycle.register_session_end(
                        thread.id, match_id, outcome, players
                    )
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
                header_surface=header_surface,
            )
            session._match_code = match_code
            session.lobby_surface = lobby.surface
            session.set_bot(self.bot)
            self.registries.promote(lobby.thread_id, session)
            await session.start()
        except Exception:
            lobby.starting = False
            lobby.ready.clear()
            for member in reserved_members:
                await self.registries.release_user(member.user_id)
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            await self._error(interaction, "common.error", lobby=lobby)
            return

    async def _teardown(self, lobby: Lobby, interaction: discord.Interaction) -> None:
        for member in lobby.members:
            await self.registries.release_user(member.user_id)
        self.registries.remove_lobby(lobby.thread_id)
        await self._success(interaction, "lobby.closed")

        if lobby.surface:
            await lobby.surface.delete()

    async def add_bots(
        self, interaction: discord.Interaction, difficulty: str, number: int
    ) -> None:
        loc = self.registries.location_of(interaction.user.id)
        if loc is None or loc.kind != "lobby":
            await self._error(interaction, "errors.not_in_lobby")
            return
        lobby = self.registries.get_lobby(loc.thread_id)
        if lobby is None:
            await self._error(interaction, "lobby.already_dead")
            return
        if lobby.creator_id != interaction.user.id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        for i in range(number):
            lobby.bots.append(
                QueuedBot(
                    name=f"Bot-{difficulty}-{len(lobby.bots) + 1}",
                    difficulty=difficulty,
                )
            )
        meta = self._meta(lobby.game_key)
        view = build_lobby_view(lobby, meta, self.emoji, self.text)
        if lobby.surface:
            await lobby.surface.update(view)
        await self._success(interaction, "lobby.bot_added", count=number)

    async def remove_bot(self, interaction: discord.Interaction, name: str) -> None:
        loc = self.registries.location_of(interaction.user.id)
        if loc is None or loc.kind != "lobby":
            await self._error(interaction, "errors.not_in_lobby")
            return
        lobby = self.registries.get_lobby(loc.thread_id)
        if lobby is None:
            await self._error(interaction, "lobby.already_dead")
            return
        if lobby.creator_id != interaction.user.id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        lobby.bots = [b for b in lobby.bots if b.name != name]
        meta = self._meta(lobby.game_key)
        view = build_lobby_view(lobby, meta, self.emoji, self.text)
        if lobby.surface:
            await lobby.surface.update(view)
        await self._success(interaction, "lobby.bot_removed", name=name)

    async def open_settings(
        self, interaction: discord.Interaction, private: bool | None
    ) -> None:
        loc = self.registries.location_of(interaction.user.id)
        if loc is None or loc.kind != "lobby":
            await self._error(interaction, "errors.not_in_lobby")
            return
        lobby = self.registries.get_lobby(loc.thread_id)
        if lobby is None:
            await self._disable_and_report_closed(interaction, "lobby.already_dead")
            return
        if private is not None:
            lobby.private = private
            meta = self._meta(lobby.game_key)
            view = build_lobby_view(lobby, meta, self.emoji, self.text)
            if lobby.surface:
                await lobby.surface.update(view)
        await self._settings(
            lobby, Route(P.LOBBY_SETTINGS, lobby.thread_id, "settings", {}), interaction
        )

    async def _success(
        self,
        interaction: discord.Interaction,
        code: str,
        **format_kwargs: object,
    ) -> None:
        await self.user_success.send(
            interaction,
            code,
            format_kwargs=format_kwargs or None,
        )

    async def _disable_and_report_closed(
        self, interaction: discord.Interaction, message_key: str
    ) -> None:
        if interaction.message:
            try:
                view = discord.ui.LayoutView.from_message(interaction.message)
                for item in view.walk_children():
                    if hasattr(item, "disabled"):
                        item.disabled = True

                if not interaction.response.is_done():
                    await interaction.response.edit_message(view=view)
                else:
                    await interaction.message.edit(view=view)

                await self._error(interaction, message_key)
                return
            except Exception as exc:
                log.warning(
                    "Failed to disable components on closed lobby message: %s", exc
                )

        await self._error(interaction, message_key)

