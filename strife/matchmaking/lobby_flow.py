from __future__ import annotations

import asyncio
import random
import secrets

import discord

from strife.engine.players import Player
from strife.logging import get_logger
from strife.matchmaking.lobby import (
    Lobby,
    LobbyGone,
    LobbyMember,
    QueuedBot,
    lobby_action,
)
from strife.matchmaking.registries import UserLocation
from strife.matchmaking.seating import order_players
from strife.persistence.repositories import (
    LiveMatchStart,
    LiveThreadConflict,
    MatchPlayer,
    generate_match_code,
)
from strife.presentation.components import Container, LayoutView, TextDisplay, TextSize
from strife.presentation.message import ViewSurface
from strife.routing import prefixes as P
from strife.routing.custom_id import Route
from strife.session import GameSession
from strife.session.header import build_game_thread_header_view

log = get_logger("matchmaking.service")


class NeedTextChannel(Exception):
    """Game threads can only be created from a guild text channel."""


class StartAborted(Exception):
    """Match start cancelled because the lobby closed or Strife is shutting down."""


_LOBBY_MEMBER_PREFIXES = frozenset(
    {
        P.LOBBY_LEAVE,
        P.LOBBY_READY,
        P.LOBBY_SETTINGS,
    }
)

# Actions that can leave every remaining member ready without anyone pressing Ready.
# Adding bots is not one: it clears ready, like any other rules change.
_ROSTER_CHANGE_PREFIXES = frozenset(
    {
        P.LOBBY_LEAVE,
        P.LOBBY_KICK,
        P.LOBBY_ADD_BLACKLIST,
    }
)

# What Strife needs in the lobby channel to post the card and open the game thread.
_GAME_CHANNEL_PERMISSIONS = (
    ("view_channel", "lobby.permission_view_channel"),
    ("send_messages", "lobby.permission_send_messages"),
    ("embed_links", "lobby.permission_embed_links"),
    ("create_public_threads", "lobby.permission_create_public_threads"),
    ("send_messages_in_threads", "lobby.permission_send_messages_in_threads"),
    ("manage_threads", "lobby.permission_manage_threads"),
)

# Upper bound on how long closing every lobby card may take at shutdown.
_CLOSE_ALL_TIMEOUT_SECONDS = 5.0


class LobbyFlowMixin:
    async def create_lobby(
        self, interaction: discord.Interaction, game_key: str, private: bool
    ) -> None:
        if game_key not in {meta.key for meta in self.registry.all()}:
            await self._error(interaction, "errors.unknown_game")
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        wait = getattr(self.bot, "wait_until_live_resumed", None)
        if wait is not None:
            await wait()
        if self.is_closing_game(game_key):
            await self._error(interaction, "lobby.already_dead")
            return
        game_cfg = self.config.games.for_game(game_key)
        if not game_cfg.enabled:
            await self._error(interaction, "errors.game_disabled")
            return
        if interaction.guild is None or interaction.guild_id is None:
            await self._error(interaction, "errors.guild_only")
            return

        channel = interaction.channel
        channel_id = interaction.channel_id or (
            channel.id if channel is not None else 0
        )
        default_id = await self.finalizer.guilds.get_default_channel(
            interaction.guild_id
        )
        posted_elsewhere = False
        if default_id and default_id != channel_id and interaction.guild is not None:
            default_channel = interaction.guild.get_channel(default_id)
            if default_channel is None:
                try:
                    default_channel = await self.bot.fetch_channel(default_id)
                except discord.NotFound, discord.HTTPException:
                    default_channel = None
            if isinstance(
                default_channel, discord.TextChannel
            ) and not self._is_forum_channel(default_channel):
                channel = default_channel
                channel_id = default_channel.id
                posted_elsewhere = True

        if self._is_forum_channel(channel):
            await self._error(interaction, "errors.need_text_channel")
            return
        if isinstance(channel, discord.Thread):
            await self._error(interaction, "errors.need_text_channel")
            return
        missing_code = (
            "errors.bot_missing_permissions_default"
            if posted_elsewhere
            else "errors.bot_missing_permissions"
        )
        missing = self._missing_bot_permissions(channel, interaction)
        if missing:
            await self._error(
                interaction,
                missing_code,
                reason_kwargs=self._missing_permissions_kwargs(channel, missing),
            )
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
                members=[
                    LobbyMember(interaction.user.id, interaction.user.display_name)
                ],
                settings=self._default_settings(meta),
            )
            surface = ViewSurface(
                self.compiler, prefix=P.LOBBY_JOIN, resource_id=lobby_id
            )
            surface.set_prefix(P.LOBBY_JOIN)
            lobby.surface = surface
            if self.is_closing_game(game_key):
                await self.registries.release_user(interaction.user.id)
                await self._error(interaction, "lobby.already_dead")
                return
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
            elif interaction.response.is_done():
                await surface.send(channel, view)
                try:
                    await interaction.delete_original_response()
                except discord.HTTPException:
                    await self._success(
                        interaction,
                        "lobby.created_in_channel",
                        mention=channel.mention,
                    )
            else:
                await surface.send(interaction, view)
            lobby.message_id = surface.message_id
        except Exception as exc:  # noqa: BLE001
            self.registries.remove_lobby(lobby_id)
            await self.registries.release_user(interaction.user.id)
            try:
                if isinstance(exc, discord.Forbidden):
                    # Permissions we could not see up front (e.g. a channel override).
                    missing = self._missing_bot_permissions(channel, interaction) or [
                        self.text.get(key) for _, key in _GAME_CHANNEL_PERMISSIONS
                    ]
                    await self._error(
                        interaction,
                        missing_code,
                        reason_kwargs=self._missing_permissions_kwargs(
                            channel, missing
                        ),
                    )
                else:
                    await self._error(interaction, "errors.lobby_failed_to_start")
            except Exception:
                log.exception("Failed to report lobby creation error to user")
            return

    def _missing_bot_permissions(
        self, channel, interaction: discord.Interaction | None = None
    ) -> list[str]:
        """Labels of the channel permissions Strife lacks; empty if it can't tell."""
        perms: discord.Permissions | None = None
        channel_id = getattr(channel, "id", None)
        if (
            interaction is not None
            and interaction.guild_id is not None
            and channel_id is not None
            and interaction.channel_id == channel_id
        ):
            perms = interaction.app_permissions
        elif (
            isinstance(channel, discord.abc.GuildChannel)
            and channel.guild.me is not None
        ):
            perms = channel.permissions_for(channel.guild.me)
        if perms is None:
            return []
        return [
            self.text.get(key)
            for attr, key in _GAME_CHANNEL_PERMISSIONS
            if not getattr(perms, attr)
        ]

    def _missing_permissions_kwargs(self, channel, missing: list[str]) -> dict:
        mention = getattr(channel, "mention", None) or f"<#{getattr(channel, 'id', 0)}>"
        return {"channel": mention, "permissions": ", ".join(missing)}

    @lobby_action
    async def handle(self, route: Route, interaction: discord.Interaction) -> None:
        lobby = self.registries.get_lobby(route.resource_id)
        if lobby is None:
            await self._disable_and_report_closed(interaction, "lobby.already_dead")
            return
        # send_modal must be the first response; skip REST and the lobby lock.
        if route.prefix == P.LOBBY_OPT_MODAL:
            if await self._reject_frozen_lobby(lobby, interaction):
                return
            await self._option_modal(lobby, route, interaction)
            return
        if not interaction.response.is_done():
            if route.prefix == P.LOBBY_SETTINGS and route.source != "tab":
                await interaction.response.defer(ephemeral=True, thinking=True)
            else:
                await interaction.response.defer()
        if route.prefix != P.LOBBY_LEAVE and not await self._check_lobby_channel_access(
            interaction, lobby
        ):
            return
        if route.prefix == P.LOBBY_APPROVE:
            values = interaction.data.get("values") if interaction.data else []
            if values:
                target_id = int(values[0])
                if not await self._check_lobby_channel_access(
                    interaction, lobby, user_id=target_id
                ):
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
            elif (
                route.prefix == P.LOBBY_LEAVE
                and interaction.user.id in lobby.pending_requests
                and not self._is_lobby_member(lobby, interaction.user.id)
            ):
                await self._withdraw_request(lobby, interaction)
                return
            elif route.prefix in _LOBBY_MEMBER_PREFIXES:
                if not await self._require_lobby_member(
                    interaction, lobby, require_channel_access=False
                ):
                    return
            elif not await self._require_lobby_creator(
                interaction, lobby, require_channel_access=False
            ):
                return
            if route.prefix == P.LOBBY_READY:
                should_start = await self._ready(lobby, route, interaction)
            else:
                await handler(lobby, route, interaction)
                if route.prefix in _ROSTER_CHANGE_PREFIXES:
                    should_start = self._claim_autostart(lobby)
        if should_start:
            await self._start(lobby, route, interaction)

    async def _refresh(self, lobby: Lobby, interaction: discord.Interaction) -> bool:
        meta = self._meta(lobby.game_key)
        try:
            view = self._build_lobby_view(lobby, meta)
        except Exception as exc:
            from strife.presentation.compiler import LayoutError

            if isinstance(exc, LayoutError):
                await self._error(interaction, "common.error", lobby=lobby)
                return False
            raise
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        if lobby.surface:
            try:
                await lobby.surface.update(view)
            except discord.NotFound:
                # Someone deleted the lobby message; nobody can see or click it anymore.
                await self._discard_lobby(lobby)
                raise LobbyGone from None
            except Exception as exc:
                from strife.presentation.compiler import LayoutError

                if isinstance(exc, LayoutError):
                    await self._error(interaction, "common.error", lobby=lobby)
                    return False
                raise
        return True

    async def _seat_member(
        self,
        lobby: Lobby,
        user_id: int,
        display_name: str,
        interaction: discord.Interaction,
    ) -> bool:
        """Seat ``user_id``; on failure, tell the clicker (who may be the creator approving)."""
        if self.is_closing_game(lobby.game_key):
            await self._error(interaction, "lobby.already_dead")
            return False
        if not await self.registries.reserve_user(
            user_id, UserLocation("lobby", lobby.thread_id, lobby.guild_id)
        ):
            if user_id == interaction.user.id:
                await self._error(
                    interaction,
                    "errors.already_in_session",
                    user_id=user_id,
                )
            else:
                # Their request can't be honoured while they're busy elsewhere; drop it.
                lobby.pending_requests.pop(user_id, None)
                await self._error(
                    interaction,
                    "errors.request_target_busy",
                    lobby=lobby,
                    reason_kwargs={"name": display_name},
                )
            return False
        if (
            self.is_closing_game(lobby.game_key)
            or self.registries.get_lobby(lobby.thread_id) is not lobby
        ):
            await self.registries.release_user(user_id)
            await self._error(interaction, "lobby.already_dead")
            return False
        lobby.pending_requests.pop(user_id, None)
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
            except discord.NotFound, discord.HTTPException:
                await self._error(
                    interaction, "errors.lobby_location_unavailable", lobby=lobby
                )
                return False

        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                code = (
                    "errors.lobby_target_no_channel_access"
                    if user_id != interaction.user.id
                    else "errors.lobby_no_server_access"
                )
                await self._error(interaction, code, lobby=lobby)
                return False

        channel = guild.get_channel(lobby.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(lobby.channel_id)
            except discord.NotFound, discord.HTTPException:
                await self._error(
                    interaction, "errors.lobby_location_unavailable", lobby=lobby
                )
                return False

        if not isinstance(channel, discord.abc.GuildChannel):
            await self._error(
                interaction, "errors.lobby_location_unavailable", lobby=lobby
            )
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
        self,
        interaction: discord.Interaction,
        lobby: Lobby,
        *,
        require_channel_access: bool = True,
    ) -> bool:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return False
        return await self._require_lobby_member(
            interaction, lobby, require_channel_access=require_channel_access
        )

    async def _require_caller_lobby(
        self,
        interaction: discord.Interaction,
        *,
        creator_only: bool = False,
        require_channel_access: bool = True,
    ) -> Lobby | None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
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

    async def _lobby_still_open(
        self, lobby: Lobby, interaction: discord.Interaction
    ) -> bool:
        """Re-check with ``lobby.lock`` held: it may have ended or started while we waited."""
        if self.registries.get_lobby(lobby.thread_id) is not lobby:
            await self._error(interaction, "lobby.already_dead")
            return False
        return not await self._reject_frozen_lobby(lobby, interaction)

    async def _join_user(
        self,
        lobby: Lobby,
        interaction: discord.Interaction,
        *,
        announce_join: bool = False,
        check_channel_access: bool = True,
    ) -> None:
        user = interaction.user
        if check_channel_access and not await self._check_lobby_channel_access(
            interaction, lobby
        ):
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
            if self.registries.location_of(user.id) is not None:
                # Approval could never seat them; make them leave their current spot first.
                await self._error(
                    interaction, "errors.already_in_session", user_id=user.id
                )
                return
            lobby.pending_requests[user.id] = user.display_name
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            if await self._refresh(lobby, interaction):
                await self._success(
                    interaction,
                    "lobby.request_sent",
                    creator=f"<@{lobby.creator_id}>",
                )
            return

        if not await self._seat_member(lobby, user.id, user.display_name, interaction):
            return
        if await self._refresh(lobby, interaction) and announce_join:
            await self._success(interaction, "lobby.joined")

    async def _join(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        await self._join_user(lobby, interaction, check_channel_access=False)

    async def _leave(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        try:
            await self._leave_lobby_inner(lobby, interaction.user.id, interaction)
        except PermissionError:
            await self._error(interaction, "errors.not_in_lobby", lobby=lobby)

    async def _withdraw_request(
        self, lobby: Lobby, interaction: discord.Interaction, *, notify: bool = True
    ) -> bool:
        """Drop the caller's pending join request. Call with ``lobby.lock`` held."""
        if lobby.pending_requests.pop(interaction.user.id, None) is None:
            return False
        if await self._refresh(lobby, interaction) and notify:
            await self._success(interaction, "lobby.request_withdrawn")
        return True

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

        if interaction.message and (
            not lobby.surface or interaction.message.id != lobby.surface.message_id
        ):
            try:
                view = discord.ui.LayoutView.from_message(interaction.message)
                for item in view.walk_children():
                    if hasattr(item, "disabled"):
                        item.disabled = True

                if not interaction.response.is_done():
                    await interaction.response.edit_message(view=view)
                else:
                    await interaction.message.edit(view=view)
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to disable leave button: %s", exc)

        if not lobby.members:
            await self._teardown(lobby, interaction)
            return
        await self._refresh(lobby, interaction)

    async def _ready(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> bool:
        if interaction.user.id in lobby.ready:
            # One shared button toggles; say so, or a double-click silently un-readies.
            lobby.ready.discard(interaction.user.id)
            if await self._refresh(lobby, interaction):
                await self._success(interaction, "lobby.ready_off")
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
        lobby.mark_ready(interaction.user.id)
        ok_start, _, _ = lobby.can_start(meta, self.text)
        if ok_start:
            if not interaction.response.is_done():
                await interaction.response.defer()
            lobby.starting = True
            return True
        await self._refresh(lobby, interaction)
        return False

    def _claim_autostart(self, lobby: Lobby) -> bool:
        """Start when a roster change leaves everyone ready; no Ready click is coming.

        Call with ``lobby.lock`` held, then ``_start`` after releasing it.
        """
        if lobby.starting or lobby.launching:
            return False
        if self.registries.get_lobby(lobby.thread_id) is not lobby:
            return False
        ok, _, _ = lobby.can_start(self._meta(lobby.game_key), self.text)
        if ok:
            lobby.starting = True
        return ok

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
        try:
            thread = await starter.create_thread(name=name, auto_archive_duration=1440)
        except Exception:
            try:
                await starter.delete()
            except discord.HTTPException:
                log.warning("Could not delete orphan thread starter %s", starter.id)
            raise

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

    def _match_start_still_open(self, lobby: Lobby) -> bool:
        return (
            not self.is_closing_game(lobby.game_key)
            and self.registries.get_lobby(lobby.thread_id) is lobby
        )

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
            if not self._match_start_still_open(lobby):
                # Ended or closed while the start was queued; nothing to launch.
                lobby.starting = False
                start_error = ("lobby.already_dead", None)
            elif not ok:
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
                await self._abort_start(lobby, interaction, "common.error")
                return

            match_code = generate_match_code(random.Random())

            channel = (
                interaction.guild.get_channel(lobby.channel_id)
                if lobby.channel_id
                else interaction.channel
            )
            if channel is None:
                channel = interaction.channel
            missing = self._missing_bot_permissions(channel, interaction)
            if missing:
                await self._abort_start(
                    lobby,
                    interaction,
                    "errors.bot_missing_permissions_start",
                    self._missing_permissions_kwargs(channel, missing),
                )
                return
            if not self._match_start_still_open(lobby):
                # Closed (e.g. shutdown) after the snapshot; don't open an orphan thread.
                lobby.starting = False
                lobby.launching = False
                await self._error(interaction, "lobby.already_dead")
                return
            thread_name = f"{meta.name} (#{match_code})"
            try:
                thread = await self._open_public_game_thread(
                    channel,
                    name=thread_name,
                    game_name=meta.name,
                    players=players,
                )
            except NeedTextChannel:
                await self._abort_start(lobby, interaction, "errors.need_text_channel")
                return

            ended_view = self._build_notice_card(
                meta,
                self.text.get("lobby.game_started", mention=thread.mention),
                emoji=self.emoji.get("logo"),
            )
            try:
                await lobby.surface.update(ended_view)
            except discord.NotFound:
                log.warning(
                    "Lobby message for %s was deleted; starting anyway", lobby.thread_id
                )

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
            session.lobby_channel_id = lobby.channel_id
            session.lobby_message_id = (
                lobby.surface.message_id if lobby.surface else None
            )
            session.lobby_blacklist = set(lobby.blacklist)
            session.lobby_denied = set(lobby.denied)
            session.game_version = meta.version
            session.set_bot(self.bot)
            if not self._match_start_still_open(lobby):
                raise StartAborted
            plugin_manager = getattr(self.bot, "plugin_manager", None)
            game_build = (
                plugin_manager.build_for(lobby.game_key)
                if plugin_manager is not None
                else None
            )
            match_id, code = await self.finalizer.start_live(
                LiveMatchStart(
                    code=match_code,
                    game_key=lobby.game_key,
                    guild_id=lobby.guild_id,
                    thread_id=thread.id,
                    seed=seed,
                    settings=game_settings,
                    players=[
                        MatchPlayer(
                            seat_index=p.seat,
                            user_id=p.user_id,
                            is_bot=p.is_bot,
                            bot_difficulty=p.bot_difficulty,
                            display_name=p.display_name,
                            role_key=p.role_key,
                            result=None,
                        )
                        for p in players
                    ],
                    started_at=session.started_at,
                    game_version=meta.version,
                    board_message_id=None,
                    header_message_id=header_surface.message_id,
                    lobby_channel_id=lobby.channel_id,
                    lobby_message_id=lobby.surface.message_id
                    if lobby.surface
                    else None,
                    turn_timeout_seconds=game_cfg.turn_timeout_seconds,
                    turn_timeout_max_strikes=game_cfg.turn_timeout_max_strikes,
                    turn_timeout_consequence=game_cfg.turn_timeout_consequence.value,
                    lobby_private=lobby.private,
                    lobby_creator_id=snapshot_creator,
                    game_build=game_build,
                )
            )
            session._match_id = match_id
            session._match_code = code
            if not self._match_start_still_open(lobby):
                raise StartAborted
            if not await self.registries.promote(lobby.thread_id, session):
                raise StartAborted
            promoted = True
            await session.start()
        except Exception as exc:
            if isinstance(exc, StartAborted):
                log.info("Aborted start of lobby %s", lobby.thread_id)
            elif isinstance(exc, LiveThreadConflict):
                log.warning(
                    "Live thread conflict starting lobby %s (thread %s)",
                    lobby.thread_id,
                    thread.id if thread is not None else "?",
                )
            else:
                log.exception("Failed to start lobby %s", lobby.thread_id)
            if (
                session is not None
                and session._match_id is not None
                and not session._finalized
            ):
                try:
                    await session.cancel("error")
                except Exception:
                    log.exception("Failed to abandon live match after start failure")
            elif session is not None:
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
            if isinstance(exc, StartAborted):
                async with lobby.lock:
                    lobby.starting = False
                    lobby.launching = False
                    if self.registries.get_lobby(lobby.thread_id) is lobby:
                        if self.is_closing_game(lobby.game_key):
                            await self._discard_lobby(lobby)
                        else:
                            lobby.ready.clear()
                if not self.is_closing_game(lobby.game_key):
                    try:
                        await self._error(interaction, "lobby.already_dead")
                    except Exception:
                        log.exception(
                            "Failed to report aborted start for lobby %s",
                            lobby.thread_id,
                        )
                return
            await self._abort_start(lobby, interaction, "common.error")
            return

    async def _abort_start(
        self,
        lobby: Lobby,
        interaction: discord.Interaction,
        code: str,
        reason_kwargs: dict | None = None,
    ) -> None:
        """Unfreeze a lobby whose start failed, clear ready, and redraw its card."""
        async with lobby.lock:
            lobby.starting = False
            lobby.launching = False
            lobby.ready.clear()
            if self.registries.get_lobby(lobby.thread_id) is lobby:
                try:
                    await self._refresh(lobby, interaction)
                except Exception:
                    log.exception("Failed to restore lobby card after start failure")
        # Never raise: callers inside _start's try would abort a second time.
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            await self._error(
                interaction, code, lobby=lobby, reason_kwargs=reason_kwargs
            )
        except Exception:
            log.exception(
                "Failed to report start failure for lobby %s", lobby.thread_id
            )

    def _build_notice_card(self, meta, body: str, *, emoji: str) -> LayoutView:
        """A control-free lobby card: the title plus one line of status."""
        view = LayoutView()
        container = Container()
        container.add_text(
            TextDisplay(
                markdown_content=f"### {emoji} {self.text.get('lobby.title', game_name=meta.name)}",
                size_style=TextSize.HEADER,
            )
        )
        container.add_text(TextDisplay(markdown_content=body, size_style=TextSize.BODY))
        view.add_container(container)
        return view

    def begin_closing_game(self, key: str) -> None:
        self._closing_games.add(key)

    def end_closing_game(self, key: str) -> None:
        self._closing_games.discard(key)

    def is_closing_game(self, key: str) -> bool:
        return bool(getattr(self, "_closing", False)) or key in getattr(
            self, "_closing_games", ()
        )

    async def close_lobbies_for_game(self, key: str) -> int:
        """Discard every lobby for ``key``, including ones already starting.

        Takes each matching lobby's lock (same as ``close_all_lobbies``) and
        loops until none remain. Lobby cards are deleted after discard.
        """
        closed: list[Lobby] = []
        seen: set[int] = set()
        while True:
            batch = [
                lobby
                for lobby in list(self.registries.lobbies.values())
                if lobby.game_key == key and lobby.thread_id not in seen
            ]
            if not batch:
                break
            for lobby in batch:
                seen.add(lobby.thread_id)
                closed.append(lobby)
                try:
                    async with lobby.lock:
                        if self.registries.get_lobby(lobby.thread_id) is lobby:
                            await self._discard_lobby(lobby)
                except Exception:
                    log.exception(
                        "Failed to release lobby %s during close", lobby.thread_id
                    )
        for lobby in closed:
            if lobby.surface:
                try:
                    await lobby.surface.delete()
                except Exception:
                    log.exception(
                        "Failed to delete lobby surface while uninstalling %s", key
                    )
        return len(closed)

    async def close_all_lobbies(self, reason_key: str = "lobby.closed_restart") -> None:
        """Close every open lobby (e.g. at shutdown): release members, mark cards closed.

        Card edits run concurrently and are bounded by ``_CLOSE_ALL_TIMEOUT_SECONDS``;
        a failed edit is logged and skipped.
        """
        self._closing = True
        closed: list[Lobby] = []
        seen: set[int] = set()
        while True:
            batch = [
                lobby
                for lobby in list(self.registries.lobbies.values())
                if lobby.thread_id not in seen
            ]
            if not batch:
                break
            for lobby in batch:
                seen.add(lobby.thread_id)
                closed.append(lobby)
                try:
                    async with lobby.lock:
                        if self.registries.get_lobby(lobby.thread_id) is lobby:
                            await self._discard_lobby(lobby)
                except Exception:
                    log.exception(
                        "Failed to release lobby %s during close", lobby.thread_id
                    )
        cards = [
            self._show_closed_card(lobby, reason_key)
            for lobby in closed
            if lobby.surface
        ]
        if not cards:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*cards, return_exceptions=True),
                timeout=_CLOSE_ALL_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            log.warning("Timed out closing lobby cards; some may still show controls")

    async def _show_closed_card(self, lobby: Lobby, reason_key: str) -> None:
        try:
            meta = self._meta(lobby.game_key)
            view = self._build_notice_card(
                meta,
                self.text.get(reason_key),
                emoji=self.emoji.get_game_emoji(meta.key),
            )
            await lobby.surface.update(view)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to mark lobby %s closed: %s", lobby.thread_id, exc)

    def _on_requests_pruned(self, lobbies: list[Lobby]) -> None:
        """A requester was seated elsewhere; redraw the lobbies that listed them."""
        for lobby in lobbies:
            task = asyncio.create_task(self._refresh_card_quietly(lobby))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    async def _refresh_card_quietly(self, lobby: Lobby) -> None:
        async with lobby.lock:
            if (
                self.registries.get_lobby(lobby.thread_id) is not lobby
                or lobby.starting
                or lobby.launching
                or lobby.surface is None
            ):
                return
            try:
                await lobby.surface.update(
                    self._build_lobby_view(lobby, self._meta(lobby.game_key))
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to refresh lobby %s card: %s", lobby.thread_id, exc)

    async def handle_message_deleted(self, message_id: int) -> None:
        """Close a lobby whose message was deleted so its members aren't stuck in it."""
        lobby = next(
            (l for l in self.registries.lobbies.values() if l.message_id == message_id),
            None,
        )
        if lobby is None:
            return
        async with lobby.lock:
            # A lobby already starting goes ahead; _start tolerates the missing message.
            if (
                self.registries.get_lobby(lobby.thread_id) is not lobby
                or lobby.starting
                or lobby.launching
            ):
                return
            await self._discard_lobby(lobby)
            if lobby.surface:
                await lobby.surface.delete()

    async def handle_channel_deleted(self, channel_id: int) -> None:
        """Close lobbies whose channel (or thread) was deleted so members aren't stuck."""
        lobbies = [
            lobby
            for lobby in list(self.registries.lobbies.values())
            if lobby.channel_id == channel_id
        ]
        for lobby in lobbies:
            async with lobby.lock:
                if (
                    self.registries.get_lobby(lobby.thread_id) is not lobby
                    or lobby.starting
                    or lobby.launching
                ):
                    continue
                await self._discard_lobby(lobby)
                if lobby.surface:
                    try:
                        await lobby.surface.delete()
                    except discord.HTTPException:
                        log.warning(
                            "Could not delete lobby %s surface after channel delete",
                            lobby.thread_id,
                        )

    async def _discard_lobby(self, lobby: Lobby) -> None:
        for member in lobby.members:
            await self.registries.release_user(member.user_id)
        self.registries.remove_lobby(lobby.thread_id)
        encoder = getattr(self.compiler, "encoder", None)
        if encoder is not None:
            encoder.invalidate_resource(lobby.thread_id)

    async def _teardown(self, lobby: Lobby, interaction: discord.Interaction) -> None:
        await self._discard_lobby(lobby)
        await self._success(interaction, "lobby.closed")

        if lobby.surface:
            await lobby.surface.delete()
