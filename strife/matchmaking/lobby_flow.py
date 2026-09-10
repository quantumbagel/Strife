from __future__ import annotations

import random
import secrets

import discord

from strife.engine.players import Player
from strife.engine.roles import order_players
from strife.session import GameSession
from strife.logging import get_logger
from strife.matchmaking.lobby import Lobby, LobbyMember, QueuedBot
from strife.matchmaking.registries import UserLocation
from strife.persistence.repositories import generate_match_code
from strife.presentation.compiler import LayoutError
from strife.presentation.components import Container, LayoutView, TextDisplay, TextSize
from strife.presentation.game_ui import build_game_thread_header_view
from strife.presentation.message import ViewSurface
from strife.routing import prefixes as P
from strife.routing.custom_id import Route

log = get_logger("matchmaking.service")


class NeedTextChannel(Exception):
    """Game threads can only be created from a guild text channel."""


_LOBBY_MEMBER_PREFIXES = frozenset({
    P.LOBBY_LEAVE,
    P.LOBBY_READY,
    P.LOBBY_SETTINGS,
})


class LobbyFlowMixin:
    async def create_lobby(
        self, interaction: discord.Interaction, game_key: str, private: bool
    ) -> None:
        if game_key not in {meta.key for meta in self.registry.all()}:
            await self._error(interaction, "errors.unknown_game")
            return
        game_cfg = self.config.games.for_game(game_key)
        if not game_cfg.enabled:
            await self._error(interaction, "errors.game_disabled")
            return
        if interaction.guild is None or interaction.guild_id is None:
            await self._error(interaction, "errors.need_text_channel")
            return

        channel = interaction.channel
        channel_id = interaction.channel_id or (channel.id if channel is not None else 0)
        default_id = await self.finalizer.guilds.get_default_channel(interaction.guild_id)
        posted_elsewhere = False
        if (
            default_id
            and default_id != channel_id
            and interaction.guild is not None
        ):
            default_channel = interaction.guild.get_channel(default_id)
            if default_channel is None:
                try:
                    default_channel = await self.bot.fetch_channel(default_id)
                except (discord.NotFound, discord.HTTPException):
                    default_channel = None
            if isinstance(default_channel, discord.TextChannel) and not self._is_forum_channel(
                default_channel
            ):
                channel = default_channel
                channel_id = default_channel.id
                posted_elsewhere = True

        if self._is_forum_channel(channel):
            await self._error(interaction, "errors.need_text_channel")
            return

        lobby_id = secrets.randbits(63)
        if not await self.registries.reserve_user(
            interaction.user.id, UserLocation("lobby", lobby_id, interaction.guild_id)
        ):
            await self._error(
                interaction,
                "errors.already_in_session",
                user_id=interaction.user.id,
            )
            return

        try:
            meta = self._meta(game_key)
            await self.finalizer.guilds.upsert(interaction.guild_id)
            lobby = Lobby(
                thread_id=lobby_id,
                guild_id=interaction.guild_id,
                channel_id=channel_id,
                game_key=game_key,
                creator_id=interaction.user.id,
                private=private,
                members=[LobbyMember(interaction.user.id, interaction.user.display_name)],
                settings=self._default_settings(meta),
            )
            surface = ViewSurface(self.compiler, prefix=P.LOBBY_JOIN, resource_id=lobby_id)
            surface.set_prefix(P.LOBBY_JOIN)
            lobby.surface = surface
            self.registries.add_lobby(lobby)
            view = self._build_lobby_view(lobby, meta)
            from_component = interaction.type == discord.InteractionType.component
            if posted_elsewhere:
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True)
                await surface.send(channel, view)
                await self._success(
                    interaction,
                    "lobby.created_in_channel",
                    mention=channel.mention,
                )
            elif from_component:
                await surface.edit_interaction(interaction, view)
                catalog_message = surface.message
                await surface.send(channel, view)
                surface.add_mirror(catalog_message)
            else:
                await surface.send(interaction, view)
            lobby.message_id = surface.message_id
        except Exception:
            self.registries.remove_lobby(lobby_id)
            await self.registries.release_user(interaction.user.id)
            try:
                await self._error(interaction, "errors.lobby_failed_to_start")
            except Exception:
                log.exception("Failed to report lobby creation error to user")
            return

    async def handle(self, route: Route, interaction: discord.Interaction) -> None:
        lobby = self.registries.get_lobby(route.resource_id)
        if lobby is None:
            await self._disable_and_report_closed(interaction, "lobby.already_dead")
            return
        should_start = False
        async with lobby.lock:
            if self.registries.get_lobby(route.resource_id) is not lobby:
                await self._disable_and_report_closed(interaction, "lobby.already_dead")
                return
            if await self._reject_frozen_lobby(lobby, interaction):
                return
            handler = {
                P.LOBBY_JOIN: self._join,
                P.LOBBY_LEAVE: self._leave,
                P.LOBBY_READY: self._ready,
                P.LOBBY_SETTINGS: self._settings,
                P.LOBBY_PRIV: self._privacy,
                P.LOBBY_RESET_PRIV: self._reset_privacy,
                P.LOBBY_OPT: self._option,
                P.LOBBY_OPT_MODAL: self._option_modal,
                P.LOBBY_RESET_RULES: self._reset_rules,
                P.LOBBY_END: self._end,
                P.LOBBY_APPROVE: self._approve,
                P.LOBBY_DENY: self._deny,
                P.LOBBY_ADD_BLACKLIST: self._add_blacklist,
                P.LOBBY_REMOVE_BLACKLIST: self._remove_blacklist,
                P.LOBBY_BOT_ADD: self._bot_add,
                P.LOBBY_BOT_REMOVE: self._bot_remove,
                P.LOBBY_KICK: self._kick,
                P.LOBBY_CLEAR_READY: self._clear_ready,
                P.LOBBY_PRE_APPROVE: self._pre_approve,
                P.LOBBY_REVOKE_APPROVAL: self._revoke_approval,
            }.get(route.prefix)
            if handler is None:
                await self._error(interaction, "common.error")
                return
            if route.prefix == P.LOBBY_JOIN:
                pass
            elif route.prefix in _LOBBY_MEMBER_PREFIXES:
                require_channel = route.prefix != P.LOBBY_LEAVE
                if not await self._require_lobby_member(
                    interaction, lobby, require_channel_access=require_channel
                ):
                    return
            elif not await self._require_lobby_creator(interaction, lobby):
                return
            if route.prefix == P.LOBBY_READY:
                should_start = await self._ready(lobby, route, interaction)
            else:
                await handler(lobby, route, interaction)
        if should_start:
            await self._start(lobby, route, interaction)

    async def _refresh(self, lobby: Lobby, interaction: discord.Interaction) -> None:
        meta = self._meta(lobby.game_key)
        try:
            view = self._build_lobby_view(lobby, meta)
        except Exception as exc:
            from strife.presentation.compiler import LayoutError

            if isinstance(exc, LayoutError):
                await self._error(interaction, "common.error", lobby=lobby)
                return
            raise
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
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

    def lobby_of_user(self, user_id: int) -> Lobby | None:
        loc = self.registries.location_of(user_id)
        if loc is None or loc.kind != "lobby":
            return None
        return self.registries.get_lobby(loc.thread_id)

    def lobby_by_creator(self, creator_id: int) -> Lobby | None:
        lobby = self.lobby_of_user(creator_id)
        if lobby is None or lobby.creator_id != creator_id:
            return None
        return lobby

    async def _check_lobby_channel_access(
        self,
        interaction: discord.Interaction,
        lobby: Lobby,
        *,
        user_id: int | None = None,
    ) -> bool:
        user_id = user_id if user_id is not None else interaction.user.id
        guild = self.bot.get_guild(lobby.guild_id)
        if guild is None:
            try:
                guild = await self.bot.fetch_guild(lobby.guild_id)
            except (discord.NotFound, discord.HTTPException):
                await self._error(interaction, "errors.lobby_location_unavailable", lobby=lobby)
                return False

        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                await self._error(interaction, "errors.lobby_no_server_access", lobby=lobby)
                return False
            except discord.HTTPException:
                await self._error(interaction, "errors.lobby_no_server_access", lobby=lobby)
                return False

        channel = guild.get_channel(lobby.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(lobby.channel_id)
            except (discord.NotFound, discord.HTTPException):
                await self._error(interaction, "errors.lobby_location_unavailable", lobby=lobby)
                return False

        if not isinstance(channel, discord.abc.GuildChannel):
            await self._error(interaction, "errors.lobby_location_unavailable", lobby=lobby)
            return False

        if not channel.permissions_for(member).view_channel:
            code = (
                "errors.lobby_target_no_channel_access"
                if user_id != interaction.user.id
                else "errors.lobby_no_channel_access"
            )
            await self._error(interaction, code, lobby=lobby)
            return False
        return True

    async def _require_lobby_member(
        self,
        interaction: discord.Interaction,
        lobby: Lobby,
        *,
        require_channel_access: bool = True,
    ) -> bool:
        if not self._is_lobby_member(lobby, interaction.user.id):
            await self._error(interaction, "errors.not_in_lobby", lobby=lobby)
            return False
        if require_channel_access:
            return await self._check_lobby_channel_access(interaction, lobby)
        return True

    async def _require_lobby_creator(
        self, interaction: discord.Interaction, lobby: Lobby
    ) -> bool:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return False
        return await self._require_lobby_member(interaction, lobby)

    async def _require_caller_lobby(
        self,
        interaction: discord.Interaction,
        *,
        creator_only: bool = False,
        require_channel_access: bool = True,
    ) -> Lobby | None:
        lobby = self.lobby_of_user(interaction.user.id)
        if lobby is None:
            loc = self.registries.location_of(interaction.user.id)
            if loc is not None and loc.kind == "game":
                await self._error(interaction, "errors.in_game_use_forfeit")
                return None
            await self._error(interaction, "errors.not_in_lobby")
            return None
        if creator_only and lobby.creator_id != interaction.user.id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return None
        if not self._is_lobby_member(lobby, interaction.user.id):
            await self._error(interaction, "errors.not_in_lobby", lobby=lobby)
            return None
        if require_channel_access and not await self._check_lobby_channel_access(
            interaction, lobby
        ):
            return None
        if await self._reject_frozen_lobby(lobby, interaction):
            return None
        return lobby

    async def _join_user(
        self,
        lobby: Lobby,
        interaction: discord.Interaction,
        *,
        announce_join: bool = False,
    ) -> None:
        user = interaction.user
        if not await self._check_lobby_channel_access(interaction, lobby):
            return
        if any(m.user_id == user.id for m in lobby.members):
            await self._error(interaction, "errors.already_in_lobby", lobby=lobby)
            return
        if user.id in lobby.blacklist:
            await self._error(interaction, "errors.blacklisted", lobby=lobby)
            return

        meta = self._meta(lobby.game_key)
        if lobby.is_full(meta):
            await self._error(interaction, "errors.lobby_full", lobby=lobby)
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
        if announce_join:
            await self._success(interaction, "lobby.joined")

    async def _join(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        await self._join_user(lobby, interaction)

    async def _leave(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        try:
            await self._leave_lobby_inner(lobby, interaction.user.id, interaction)
        except PermissionError:
            await self._error(interaction, "errors.not_in_lobby", lobby=lobby)

    async def _reject_frozen_lobby(
        self, lobby: Lobby, interaction: discord.Interaction
    ) -> bool:
        if not (lobby.starting or lobby.launching):
            return False
        await self._error(interaction, "errors.lobby_starting", lobby=lobby)
        return True

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
    ) -> bool:
        if interaction.user.id in lobby.ready:
            lobby.ready.discard(interaction.user.id)
            await self._refresh(lobby, interaction)
            return False
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
            return False
        lobby.ready.add(interaction.user.id)
        ok_start, _, _ = lobby.can_start(meta, self.text)
        if ok_start:
            if not interaction.response.is_done():
                await interaction.response.defer()
            lobby.starting = True
            return True
        await self._refresh(lobby, interaction)
        return False

    def _is_forum_channel(self, channel) -> bool:
        if isinstance(channel, discord.ForumChannel):
            return True
        parent = getattr(channel, "parent", None)
        return isinstance(parent, discord.ForumChannel)

    async def _open_public_game_thread(
        self,
        channel,
        *,
        name: str,
        game_name: str,
        players: list[Player],
    ) -> discord.Thread:
        if self._is_forum_channel(channel):
            raise NeedTextChannel
        starter_text = self.text.get("lobby.thread_starter", game_name=game_name)
        parent = channel
        if isinstance(channel, discord.Thread):
            parent = channel.parent or channel
        starter = await parent.send(starter_text)
        thread = await starter.create_thread(name=name, auto_archive_duration=1440)

        for player in players:
            if not player.user_id or player.is_bot:
                continue
            try:
                await thread.add_user(discord.Object(id=player.user_id))
            except discord.HTTPException:
                log.warning(
                    "Could not add user %s to game thread %s",
                    player.user_id,
                    thread.id,
                )
        return thread

    async def _start(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        start_error: tuple[str | None, dict | None] | None = None
        snapshot_members: list[LobbyMember] = []
        snapshot_bots: list[QueuedBot] = []
        snapshot_settings: dict = {}
        snapshot_creator = 0
        async with lobby.lock:
            if lobby.launching:
                return
            meta = self._meta(lobby.game_key)
            ok, reason_key, reason_kwargs = lobby.can_start(meta, self.text)
            if not ok:
                lobby.starting = False
                start_error = (reason_key, reason_kwargs)
            else:
                lobby.starting = True
                lobby.launching = True
                snapshot_members = list(lobby.members)
                snapshot_bots = list(lobby.bots)
                snapshot_settings = dict(lobby.settings)
                snapshot_creator = lobby.creator_id
        if start_error is not None:
            reason_key, reason_kwargs = start_error
            await self._error(
                interaction,
                reason_key or "common.error",
                lobby=lobby,
                reason_key=reason_key,
                reason_kwargs=reason_kwargs,
            )
            return
        promoted = False
        thread = None
        session = None
        marshal_replaced = False
        try:
            seed = secrets.randbits(63)
            rng = random.Random(seed)
            players: list[Player] = []
            for member in snapshot_members:
                players.append(
                    Player(
                        seat=0,
                        user_id=member.user_id,
                        display_name=member.display_name,
                    )
                )
            for bot in snapshot_bots:
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
                players, meta.player_order.value, rng, creator_id=snapshot_creator
            )
            game_settings = dict(snapshot_settings)
            game_settings["creator_id"] = snapshot_creator
            game = self.registry.create(
                lobby.game_key,
                players,
                game_settings,
                seed,
            )
            if lobby.surface is None:
                lobby.starting = False
                lobby.launching = False
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
            try:
                thread = await self._open_public_game_thread(
                    channel,
                    name=thread_name,
                    game_name=meta.name,
                    players=players,
                )
            except NeedTextChannel:
                lobby.starting = False
                lobby.launching = False
                lobby.ready.clear()
                await self._error(interaction, "errors.need_text_channel", lobby=lobby)
                return

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
            marshal_replaced = True

            game_compiler = self.compiler.for_game(lobby.game_key)
            header_surface = ViewSurface(
                game_compiler, prefix=P.REPLAY_NOOP, resource_id=thread.id
            )
            game_surface = ViewSurface(
                game_compiler, prefix=P.G_MOVE, resource_id=thread.id
            )
            game_cfg = self.config.games.for_game(lobby.game_key)
            starting_view = build_game_thread_header_view(
                players=players,
                text=self.text,
                emoji=self.emoji,
                owner_ids=self.owner_ids(),
            )
            await header_surface.send_to_thread(thread, starting_view)

            session = GameSession(
                thread_id=thread.id,
                guild_id=lobby.guild_id,
                game=game,
                players=players,
                settings=dict(snapshot_settings),
                seed=seed,
                surface=game_surface,
                text=self.text,
                finalizer=self.finalizer,
                game_key=lobby.game_key,
                header_surface=header_surface,
                turn_timeout_seconds=game_cfg.turn_timeout_seconds,
                turn_timeout_max_strikes=game_cfg.turn_timeout_max_strikes,
                turn_timeout_consequence=game_cfg.turn_timeout_consequence,
            )
            session._match_code = match_code
            session.lobby_surface = lobby.surface
            session.lobby_private = lobby.private
            session.lobby_creator_id = snapshot_creator
            session.set_bot(self.bot)
            await self.registries.promote(lobby.thread_id, session)
            promoted = True
            await session.start()
        except Exception:
            lobby.starting = False
            lobby.launching = False
            lobby.ready.clear()
            if session is not None:
                session._ending = True
                session._finalized = True
                if session.task is not None and not session.task.done():
                    session.task.cancel()
            if promoted and session is not None:
                await self.registries.rollback_promote(lobby, session)
            if thread is not None:
                try:
                    await thread.edit(archived=True, locked=True)
                except Exception:
                    log.exception("Failed to archive orphan game thread %s", thread.id)
            if marshal_replaced:
                try:
                    await self._refresh(lobby, interaction)
                except Exception:
                    log.exception("Failed to restore lobby card after start failure")
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            await self._error(interaction, "common.error", lobby=lobby)
            return

    async def _teardown(self, lobby: Lobby, interaction: discord.Interaction) -> None:
        for member in lobby.members:
            await self.registries.release_user(member.user_id)
        self.registries.remove_lobby(lobby.thread_id)
        encoder = getattr(self.compiler, "encoder", None)
        if encoder is not None:
            encoder.invalidate_resource(lobby.thread_id)
        await self._success(interaction, "lobby.closed")

        if lobby.surface:
            await lobby.surface.delete()

