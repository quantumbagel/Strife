from __future__ import annotations

import random
import secrets
from typing import TYPE_CHECKING

import discord

from strife.config import AppConfig
from strife.engine.metadata import GameMetadata, OptionType, int_setting_bounds
from strife.engine.players import Player
from strife.engine.registry import GameRegistry
from strife.engine.roles import order_players
from strife.engine.session import GameSession
from strife.logging import get_logger
from strife.matchmaking.lobby import Lobby, LobbyMember, QueuedBot
from strife.matchmaking.lobby_view import build_lobby_view
from strife.matchmaking.registries import SessionRegistries, UserLocation
from strife.matchmaking.role_validation import clear_ready_if_roles_invalid, invalid_role_reason
from strife.matchmaking.settings_view import build_settings_view, normalize_settings_tab
from strife.persistence.repositories import (
    FinishedMatch,
    GuildRepository,
    MatchRepository,
    PlayerResult,
    UserRepository,
    generate_match_code,
)
from strife.presentation.compiler import Compiler, LayoutError
from strife.presentation.modals import IntRangeModal, ROLE_ASSIGN_MODAL_BATCH, RoleAssignmentModal
from strife.presentation.emoji import EmojiResolver
from strife.presentation.components import Container, LayoutView, TextDisplay, TextSize
from strife.presentation.game_ui import build_game_thread_header_view
from strife.presentation.message import ViewSurface
from strife.presentation.roster import bot_label
from strife.presentation.user_error import ErrorContext, UserErrorPresenter
from strife.presentation.user_success import UserSuccessPresenter
from strife.routing import prefixes as P
from strife.routing.custom_id import Route

if TYPE_CHECKING:
    from strife.engine.game import Game

log = get_logger("matchmaking.service")

_LOBBY_MEMBER_PREFIXES = frozenset({
    P.LOBBY_LEAVE,
    P.LOBBY_READY,
    P.LOBBY_SETTINGS,
    P.LOBBY_ROLE,
})


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

    def _is_lobby_member(self, lobby: Lobby, user_id: int) -> bool:
        return any(member.user_id == user_id for member in lobby.members)

    def _game_cls(self, game_key: str) -> type[Game] | None:
        try:
            return self.registry.get(game_key)
        except KeyError:
            return None

    def _role_invalid_reason(self, lobby: Lobby, meta: GameMetadata) -> str | None:
        return invalid_role_reason(lobby, meta, self._game_cls(lobby.game_key))

    def _clear_ready_if_roles_invalid(self, lobby: Lobby, meta: GameMetadata) -> None:
        clear_ready_if_roles_invalid(lobby, meta, self._game_cls(lobby.game_key))

    def _build_lobby_view(self, lobby: Lobby, meta: GameMetadata):
        return build_lobby_view(
            lobby,
            meta,
            self.emoji,
            self.text,
            game_cls=self._game_cls(lobby.game_key),
            role_invalid_reason=self._role_invalid_reason(lobby, meta),
        )

    async def _send_settings(
        self,
        lobby: Lobby,
        interaction: discord.Interaction,
        *,
        tab: str = "general",
        edit: bool = False,
    ) -> None:
        meta = self._meta(lobby.game_key)
        tab = normalize_settings_tab(tab)
        if tab == "rules" and not meta.settings:
            tab = "general"
        readonly = interaction.user.id != lobby.creator_id
        try:
            view = build_settings_view(
                lobby,
                meta,
                self.emoji,
                self.text,
                interaction,
                tab=tab,
                readonly=readonly,
                role_invalid_reason=self._role_invalid_reason(lobby, meta),
            )
            compiled = self.compiler.compile(
                view, resource_id=lobby.thread_id, prefix=P.LOBBY_SETTINGS
            )
        except LayoutError:
            await self._error(interaction, "common.error", lobby=lobby)
            return
        if edit:
            await interaction.edit_original_response(view=compiled)
        else:
            await interaction.response.send_message(view=compiled, ephemeral=True)

    async def _eject_member(self, lobby: Lobby, user_id: int) -> bool:
        if not any(member.user_id == user_id for member in lobby.members):
            return False
        lobby.members = [member for member in lobby.members if member.user_id != user_id]
        lobby.ready.discard(user_id)
        lobby.role_selection.pop(user_id, None)
        await self.registries.release_user(user_id)
        return True

    async def _sync_lobby_after_creator_edit(
        self,
        lobby: Lobby,
        interaction: discord.Interaction,
        route: Route,
        *,
        default_settings_tab: str | None = None,
    ) -> None:
        settings_tab = route.payload.get("settings_tab", default_settings_tab)
        if settings_tab:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            await self._refresh(lobby, interaction)
            await self._send_settings(lobby, interaction, tab=settings_tab, edit=True)
            return
        await self._refresh(lobby, interaction)

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
            view = self._build_lobby_view(lobby, meta)
            await surface.send(interaction, view)
            lobby.message_id = surface.message_id
        except Exception:
            await self.registries.release_user(interaction.user.id)
            try:
                await self._error(interaction, "errors.lobby_failed_to_start")
            except Exception:
                log.exception("Failed to report lobby creation error to user")
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
                P.LOBBY_ASSIGN: self._assign_roles,
                P.LOBBY_ASSIGN_ROLES: self._assign_roles,
                P.LOBBY_SETTINGS: self._settings,
                P.LOBBY_ROLE: self._role,
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
            await handler(lobby, route, interaction)

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
            game_cls = self._game_cls(lobby.game_key)
            ok, reason_key, reason_kwargs = lobby.can_ready(meta, self.text, game_cls=game_cls)
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
            ok_start, _, _ = lobby.can_start(meta, self.text, game_cls=game_cls)
            if ok_start:
                if not interaction.response.is_done():
                    await interaction.response.defer()
                await self._start(lobby, route, interaction)
            else:
                await self._refresh(lobby, interaction)

    async def _assign_roles(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        if not lobby.members:
            await self._error(interaction, "common.error", lobby=lobby)
            return
        offset = int(route.payload.get("offset", 0))
        await self._open_role_assign_modal(lobby, interaction, offset=offset)

    async def _open_role_assign_modal(
        self,
        lobby: Lobby,
        interaction: discord.Interaction,
        *,
        offset: int = 0,
    ) -> None:
        meta = self._meta(lobby.game_key)
        members = lobby.members[offset : offset + ROLE_ASSIGN_MODAL_BATCH]
        if not members:
            await self._error(interaction, "common.error", lobby=lobby)
            return
        roles = [(role.key, role.name) for role in meta.roles]
        valid_roles = {role.key for role in meta.roles}
        total = len(lobby.members)
        start = offset + 1
        end = offset + len(members)
        title_key = (
            "lobby.role_assign_modal_title_paged"
            if end < total
            else "lobby.role_assign_modal_title"
        )
        title = self.text.get(title_key, start=start, end=end, total=total)

        async def on_submit(
            modal_interaction: discord.Interaction, assignments: dict[int, str]
        ) -> None:
            for user_id, role_key in assignments.items():
                if role_key not in valid_roles:
                    await self._error(modal_interaction, "errors.invalid_roles", lobby=lobby)
                    return
                lobby.role_selection[user_id] = role_key

            self._clear_ready_if_roles_invalid(lobby, meta)

            next_offset = offset + len(assignments)
            if next_offset < total:
                await self._open_role_assign_modal(lobby, modal_interaction, offset=next_offset)
                return

            await modal_interaction.response.defer(ephemeral=True)
            await self._send_settings(lobby, modal_interaction, tab="roles", edit=True)
            await self._refresh(lobby, modal_interaction)

        modal = RoleAssignmentModal(
            title=title,
            members=[(member.user_id, member.display_name) for member in members],
            roles=roles,
            current=lobby.role_selection,
            on_submit_cb=on_submit,
        )
        await interaction.response.send_modal(modal)

    async def _settings(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if not self._is_lobby_member(lobby, interaction.user.id):
            await self._error(interaction, "errors.not_in_lobby", lobby=lobby)
            return
        tab = normalize_settings_tab(route.payload.get("tab"))
        if route.source == "tab":
            await interaction.response.defer(ephemeral=True)
            await self._send_settings(lobby, interaction, tab=tab, edit=True)
            return
        await self._send_settings(lobby, interaction, tab=tab, edit=False)

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
            self._clear_ready_if_roles_invalid(lobby, meta)
        if route.payload.get("settings_tab") == "roles":
            await interaction.response.defer(ephemeral=True)
            await self._send_settings(lobby, interaction, tab="roles", edit=True)
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
        await self._send_settings(lobby, interaction, tab="general", edit=True)
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
        await self._send_settings(lobby, interaction, tab="general", edit=True)
        await self._refresh(lobby, interaction)

    def _apply_int_setting(self, lobby: Lobby, option, value: int) -> bool:
        minimum, maximum = int_setting_bounds(option)
        if value < minimum or value > maximum:
            return False
        lobby.settings[option.key] = value
        return True

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
                if not self._apply_int_setting(lobby, option, value):
                    await self._error(interaction, "common.error", lobby=lobby)
                    return
            else:
                if option.type == OptionType.CHOICE and option.choices and raw not in option.choices:
                    await self._error(interaction, "common.error", lobby=lobby)
                    return
                lobby.settings[key] = raw
        await interaction.response.defer(ephemeral=True)
        await self._send_settings(lobby, interaction, tab="rules", edit=True)
        await self._refresh(lobby, interaction)

    async def _option_modal(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        key = route.payload.get("option_key")
        if not key:
            await self._error(interaction, "common.error", lobby=lobby)
            return
        meta = self._meta(lobby.game_key)
        option = next((o for o in meta.settings if o.key == key), None)
        if option is None or option.type != OptionType.INT:
            await self._error(interaction, "common.error", lobby=lobby)
            return
        minimum, maximum = int_setting_bounds(option)
        current = int(lobby.settings.get(option.key, option.default))

        async def on_submit(modal_interaction: discord.Interaction, value: int) -> None:
            self._apply_int_setting(lobby, option, value)
            await modal_interaction.response.defer(ephemeral=True)
            await self._send_settings(lobby, modal_interaction, tab="rules", edit=True)
            await self._refresh(lobby, modal_interaction)

        modal = IntRangeModal(
            title=self.text.get("lobby.int_option_modal_title", title=option.title),
            label=self.text.get(
                "lobby.int_option_modal_label", minimum=minimum, maximum=maximum
            ),
            placeholder=self.text.get("lobby.int_option_modal_placeholder"),
            default=current,
            minimum=minimum,
            maximum=maximum,
            on_submit_cb=on_submit,
            error_message=self.text.get("lobby.invalid_int_option"),
            range_error_message=self.text.get(
                "lobby.int_option_out_of_range", minimum=minimum, maximum=maximum
            ),
        )
        await interaction.response.send_modal(modal)

    async def _reset_rules(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        lobby.settings = self._default_settings(self._meta(lobby.game_key))
        await interaction.response.defer(ephemeral=True)
        await self._send_settings(lobby, interaction, tab="rules", edit=True)
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
            meta = self._meta(lobby.game_key)
            if lobby.is_full(meta):
                await self._error(interaction, "errors.lobby_full", lobby=lobby)
                return
            target_id = int(values[0])
            if not await self._check_lobby_channel_access(
                interaction, lobby, user_id=target_id
            ):
                return
            display_name = lobby.pending_requests.pop(target_id, f"User {target_id}")
            lobby.approved.add(target_id)
            lobby.denied.discard(target_id)
            if not any(m.user_id == target_id for m in lobby.members):
                await self._seat_member(lobby, target_id, display_name, interaction)
        await self._sync_lobby_after_creator_edit(lobby, interaction, route)

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
        await self._sync_lobby_after_creator_edit(lobby, interaction, route)

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
            kicked = await self._eject_member(lobby, target_id)
        await interaction.response.defer(ephemeral=True)
        await self._send_settings(lobby, interaction, tab="access", edit=True)
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
        await self._send_settings(lobby, interaction, tab="access", edit=True)

    async def _bot_add(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        meta = self._meta(lobby.game_key)
        values = interaction.data.get("values") if interaction.data else []
        if values:
            difficulty = values[0]
            valid = {spec.difficulty for spec in meta.bots}
            if difficulty not in valid:
                await self._error(interaction, "common.error", lobby=lobby)
                return
            if lobby.is_full(meta):
                await self._error(interaction, "errors.lobby_full", lobby=lobby)
                return
            lobby.bots.append(
                QueuedBot(
                    name=f"Bot-{difficulty}-{len(lobby.bots) + 1}",
                    difficulty=difficulty,
                )
            )
        await interaction.response.defer(ephemeral=True)
        await self._send_settings(lobby, interaction, tab="general", edit=True)
        await self._refresh(lobby, interaction)

    async def _bot_remove(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        values = interaction.data.get("values") if interaction.data else []
        if values:
            name = values[0]
            if not any(bot.name == name for bot in lobby.bots):
                await self._error(interaction, "common.error", lobby=lobby)
                return
            lobby.bots = [bot for bot in lobby.bots if bot.name != name]
        await interaction.response.defer(ephemeral=True)
        await self._send_settings(lobby, interaction, tab="general", edit=True)
        await self._refresh(lobby, interaction)

    async def _kick(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        values = interaction.data.get("values") if interaction.data else []
        kicked_name: str | None = None
        if values:
            target_id = int(values[0])
            if target_id == lobby.creator_id:
                await self._error(interaction, "errors.cannot_kick_self", lobby=lobby)
                return
            member = next((m for m in lobby.members if m.user_id == target_id), None)
            if member is None:
                await self._error(interaction, "errors.kick_target_not_seated", lobby=lobby)
                return
            kicked_name = member.display_name
            lobby.approved.discard(target_id)
            lobby.pending_requests.pop(target_id, None)
            await self._eject_member(lobby, target_id)
            if not lobby.members:
                await interaction.response.defer(ephemeral=True)
                await self._teardown(lobby, interaction)
                return
        await interaction.response.defer(ephemeral=True)
        await self._send_settings(lobby, interaction, tab="access", edit=True)
        await self._refresh(lobby, interaction)
        if kicked_name:
            await self._success(interaction, "lobby.player_kicked", name=kicked_name)

    async def _clear_ready(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        lobby.ready.clear()
        await interaction.response.defer(ephemeral=True)
        await self._send_settings(lobby, interaction, tab="general", edit=True)
        await self._refresh(lobby, interaction)
        await self._success(interaction, "lobby.ready_cleared")

    async def _pre_approve(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        if not lobby.private:
            await self._error(interaction, "common.error", lobby=lobby)
            return
        values = interaction.data.get("values") if interaction.data else []
        approved_name: str | None = None
        if values:
            target_id = int(values[0])
            if target_id == lobby.creator_id:
                await self._error(interaction, "common.error", lobby=lobby)
                return
            if target_id in lobby.blacklist:
                await self._error(interaction, "errors.blacklisted", lobby=lobby)
                return
            lobby.approved.add(target_id)
            lobby.denied.discard(target_id)
            lobby.pending_requests.pop(target_id, None)
            member = interaction.guild.get_member(target_id) if interaction.guild else None
            approved_name = member.display_name if member else f"User {target_id}"
        await interaction.response.defer(ephemeral=True)
        await self._send_settings(lobby, interaction, tab="access", edit=True)
        if approved_name:
            await self._success(interaction, "lobby.player_preapproved", name=approved_name)

    async def _revoke_approval(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if interaction.user.id != lobby.creator_id:
            await self._error(interaction, "lobby.creator_only", lobby=lobby)
            return
        values = interaction.data.get("values") if interaction.data else []
        revoked_name: str | None = None
        if values:
            target_id = int(values[0])
            seated_ids = {member.user_id for member in lobby.members}
            if target_id not in lobby.approved or target_id in seated_ids:
                await self._error(interaction, "common.error", lobby=lobby)
                return
            lobby.approved.discard(target_id)
            member = interaction.guild.get_member(target_id) if interaction.guild else None
            revoked_name = member.display_name if member else f"User {target_id}"
        await interaction.response.defer(ephemeral=True)
        await self._send_settings(lobby, interaction, tab="access", edit=True)
        if revoked_name:
            await self._success(interaction, "lobby.approval_revoked", name=revoked_name)

    async def _start(
        self, lobby: Lobby, route: Route, interaction: discord.Interaction
    ) -> None:
        if lobby.starting:
            return
        meta = self._meta(lobby.game_key)
        game_cls = self._game_cls(lobby.game_key)
        ok, reason_key, reason_kwargs = lobby.can_start(meta, self.text, game_cls=game_cls)
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
            game_settings = dict(lobby.settings)
            game_settings["creator_id"] = lobby.creator_id
            game = self.registry.create(
                lobby.game_key,
                players,
                game_settings,
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
            game_cfg = self.config.games.for_game(lobby.game_key)
            starting_view = build_game_thread_header_view(
                players=players,
                text=self.text,
                emoji=self.emoji,
            )
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
                turn_timeout_seconds=game_cfg.turn_timeout_seconds,
                turn_timeout_max_strikes=game_cfg.turn_timeout_max_strikes,
                turn_timeout_consequence=game_cfg.turn_timeout_consequence,
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

    async def join_by_creator(
        self, interaction: discord.Interaction, creator_id: int
    ) -> None:
        lobby = self.lobby_by_creator(creator_id)
        if lobby is None:
            await self._error(interaction, "errors.creator_not_hosting")
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            await self._join_user(lobby, interaction, announce_join=True)

    async def leave_current(self, interaction: discord.Interaction) -> None:
        lobby = await self._require_caller_lobby(
            interaction, require_channel_access=False
        )
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            try:
                await self._leave_lobby_inner(lobby, interaction.user.id, interaction)
            except PermissionError:
                await self._error(interaction, "errors.not_in_lobby", lobby=lobby)
                return
            if self.registries.get_lobby(lobby.thread_id) is lobby:
                await self._success(interaction, "lobby.left")

    async def toggle_ready(self, interaction: discord.Interaction) -> None:
        lobby = await self._require_caller_lobby(interaction)
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            route = Route(P.LOBBY_READY, lobby.thread_id, "ready", {})
            if interaction.user.id in lobby.ready:
                lobby.ready.discard(interaction.user.id)
                await self._refresh(lobby, interaction)
                await self._success(interaction, "lobby.ready_off")
                return
            meta = self._meta(lobby.game_key)
            game_cls = self._game_cls(lobby.game_key)
            ok, reason_key, reason_kwargs = lobby.can_ready(meta, self.text, game_cls=game_cls)
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
            ok_start, _, _ = lobby.can_start(meta, self.text, game_cls=game_cls)
            if ok_start:
                if not interaction.response.is_done():
                    await interaction.response.defer()
                await self._start(lobby, route, interaction)
                return
            await self._refresh(lobby, interaction)
            await self._success(interaction, "lobby.ready_on")

    async def kick_member(self, interaction: discord.Interaction, user_id: int) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            if user_id == lobby.creator_id:
                await self._error(interaction, "errors.cannot_kick_self", lobby=lobby)
                return
            member = next((m for m in lobby.members if m.user_id == user_id), None)
            if member is None:
                await self._error(interaction, "errors.kick_target_not_seated", lobby=lobby)
                return
            kicked_name = member.display_name
            lobby.approved.discard(user_id)
            lobby.pending_requests.pop(user_id, None)
            await self._eject_member(lobby, user_id)
            if not lobby.members:
                await self._teardown(lobby, interaction)
                return
            await self._refresh(lobby, interaction)
            await self._success(interaction, "lobby.player_kicked", name=kicked_name)

    async def end_lobby(self, interaction: discord.Interaction) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            await self._teardown(lobby, interaction)

    async def clear_ready(self, interaction: discord.Interaction) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            lobby.ready.clear()
            await self._refresh(lobby, interaction)
            await self._success(interaction, "lobby.ready_cleared")

    async def set_privacy(self, interaction: discord.Interaction, private: bool) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            lobby.private = private
            await self._refresh(lobby, interaction)
            mode = self.text.get("lobby.private_label" if private else "lobby.public_label")
            await self._success(interaction, "lobby.privacy_updated", mode=mode)

    async def reset_privacy(self, interaction: discord.Interaction) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            lobby.private = False
            lobby.approved.clear()
            lobby.pending_requests.clear()
            lobby.denied.clear()
            lobby.blacklist.clear()
            await self._refresh(lobby, interaction)
            await self._success(interaction, "lobby.privacy_reset")

    async def reset_rules(self, interaction: discord.Interaction) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            lobby.settings = self._default_settings(self._meta(lobby.game_key))
            await self._refresh(lobby, interaction)
            await self._success(interaction, "lobby.rules_reset")

    async def set_own_role(self, interaction: discord.Interaction, role_key: str) -> None:
        lobby = await self._require_caller_lobby(interaction)
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            if not any(m.user_id == interaction.user.id for m in lobby.members):
                await self._error(interaction, "errors.not_a_player", lobby=lobby)
                return
            meta = self._meta(lobby.game_key)
            role = next((r for r in meta.roles if r.key == role_key), None)
            if role is None:
                await self._error(interaction, "errors.invalid_roles", lobby=lobby)
                return
            lobby.role_selection[interaction.user.id] = role_key
            self._clear_ready_if_roles_invalid(lobby, meta)
            await self._refresh(lobby, interaction)
            await self._success(interaction, "lobby.role_set", role=role.name)

    async def assign_member_role(
        self, interaction: discord.Interaction, user_id: int, role_key: str
    ) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            member = next((m for m in lobby.members if m.user_id == user_id), None)
            if member is None:
                await self._error(interaction, "errors.kick_target_not_seated", lobby=lobby)
                return
            meta = self._meta(lobby.game_key)
            role = next((r for r in meta.roles if r.key == role_key), None)
            if role is None:
                await self._error(interaction, "errors.invalid_roles", lobby=lobby)
                return
            lobby.role_selection[user_id] = role_key
            self._clear_ready_if_roles_invalid(lobby, meta)
            await self._refresh(lobby, interaction)
            await self._success(
                interaction,
                "lobby.role_assigned",
                role=role.name,
                name=member.display_name,
            )

    async def set_option(
        self, interaction: discord.Interaction, key: str, value: str
    ) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            meta = self._meta(lobby.game_key)
            option = next((o for o in meta.settings if o.key == key), None)
            if option is None:
                await self._error(interaction, "common.error", lobby=lobby)
                return
            display_value: str
            if option.type == OptionType.BOOL:
                normalized = value.strip().lower()
                if normalized not in {"true", "false", "on", "off", "1", "0"}:
                    await self._error(interaction, "common.error", lobby=lobby)
                    return
                parsed = normalized in {"true", "on", "1"}
                lobby.settings[key] = parsed
                display_value = self.text.get(
                    "lobby.on_label" if parsed else "lobby.off_label"
                )
            elif option.type == OptionType.INT:
                try:
                    int_value = int(value.strip())
                except ValueError:
                    await self._error(interaction, "lobby.invalid_int_option", lobby=lobby)
                    return
                if not self._apply_int_setting(lobby, option, int_value):
                    minimum, maximum = int_setting_bounds(option)
                    await self._error(
                        interaction,
                        "lobby.int_option_out_of_range",
                        lobby=lobby,
                        reason_kwargs={"minimum": minimum, "maximum": maximum},
                    )
                    return
                display_value = str(int_value)
            else:
                if option.choices and value not in option.choices:
                    await self._error(interaction, "common.error", lobby=lobby)
                    return
                lobby.settings[key] = value
                display_value = value.capitalize() if isinstance(value, str) else str(value)
            await self._refresh(lobby, interaction)
            await self._success(
                interaction,
                "lobby.option_set",
                title=option.title,
                value=display_value,
            )

    async def approve_request(self, interaction: discord.Interaction, user_id: int) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            if user_id not in lobby.pending_requests:
                await self._error(interaction, "errors.no_pending_request", lobby=lobby)
                return
            meta = self._meta(lobby.game_key)
            if lobby.is_full(meta):
                await self._error(interaction, "errors.lobby_full", lobby=lobby)
                return
            if not await self._check_lobby_channel_access(
                interaction, lobby, user_id=user_id
            ):
                return
            display_name = lobby.pending_requests.pop(user_id, f"User {user_id}")
            lobby.approved.add(user_id)
            lobby.denied.discard(user_id)
            if not any(m.user_id == user_id for m in lobby.members):
                if not await self._seat_member(lobby, user_id, display_name, interaction):
                    return
            await self._refresh(lobby, interaction)
            await self._success(interaction, "lobby.player_approved", name=display_name)

    async def deny_request(self, interaction: discord.Interaction, user_id: int) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            if user_id not in lobby.pending_requests:
                await self._error(interaction, "errors.no_pending_request", lobby=lobby)
                return
            display_name = lobby.pending_requests.pop(user_id, f"User {user_id}")
            lobby.denied.add(user_id)
            await self._refresh(lobby, interaction)
            await self._success(interaction, "lobby.player_denied", name=display_name)

    async def preapprove_user(self, interaction: discord.Interaction, user_id: int) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            if not lobby.private:
                await self._error(interaction, "errors.lobby_not_private", lobby=lobby)
                return
            if user_id == lobby.creator_id:
                await self._error(interaction, "common.error", lobby=lobby)
                return
            if user_id in lobby.blacklist:
                await self._error(interaction, "errors.blacklisted", lobby=lobby)
                return
            lobby.approved.add(user_id)
            lobby.denied.discard(user_id)
            lobby.pending_requests.pop(user_id, None)
            member = interaction.guild.get_member(user_id) if interaction.guild else None
            approved_name = member.display_name if member else f"User {user_id}"
            await self._refresh(lobby, interaction)
            await self._success(interaction, "lobby.player_preapproved", name=approved_name)

    async def revoke_approval(self, interaction: discord.Interaction, user_id: int) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            seated_ids = {member.user_id for member in lobby.members}
            if user_id not in lobby.approved or user_id in seated_ids:
                await self._error(interaction, "errors.not_preapproved", lobby=lobby)
                return
            lobby.approved.discard(user_id)
            member = interaction.guild.get_member(user_id) if interaction.guild else None
            revoked_name = member.display_name if member else f"User {user_id}"
            await self._refresh(lobby, interaction)
            await self._success(interaction, "lobby.approval_revoked", name=revoked_name)

    async def blacklist_add(self, interaction: discord.Interaction, user_id: int) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            if user_id == lobby.creator_id:
                await self._error(interaction, "errors.cannot_blacklist_self", lobby=lobby)
                return
            lobby.blacklist.add(user_id)
            lobby.approved.discard(user_id)
            lobby.pending_requests.pop(user_id, None)
            kicked = await self._eject_member(lobby, user_id)
            if kicked and not lobby.members:
                await self._teardown(lobby, interaction)
                return
            await self._refresh(lobby, interaction)
            member = interaction.guild.get_member(user_id) if interaction.guild else None
            name = member.display_name if member else f"User {user_id}"
            await self._success(interaction, "lobby.blacklist_added", name=name)

    async def blacklist_remove(self, interaction: discord.Interaction, user_id: int) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
                return
            if user_id not in lobby.blacklist:
                await self._error(interaction, "errors.not_blacklisted", lobby=lobby)
                return
            lobby.blacklist.discard(user_id)
            member = interaction.guild.get_member(user_id) if interaction.guild else None
            name = member.display_name if member else f"User {user_id}"
            await self._success(interaction, "lobby.blacklist_removed", name=name)

    async def add_bots(
        self, interaction: discord.Interaction, difficulty: str, number: int
    ) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        meta = self._meta(lobby.game_key)
        added = 0
        for _ in range(number):
            if lobby.is_full(meta):
                break
            lobby.bots.append(
                QueuedBot(
                    name=f"Bot-{difficulty}-{len(lobby.bots) + 1}",
                    difficulty=difficulty,
                )
            )
            added += 1
        if added == 0:
            await self._error(interaction, "errors.lobby_full", lobby=lobby)
            return
        view = self._build_lobby_view(lobby, meta)
        if lobby.surface:
            await lobby.surface.update(view)
        await self._success(interaction, "lobby.bot_added", count=added)

    async def remove_bot(self, interaction: discord.Interaction, name: str) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        lobby.bots = [b for b in lobby.bots if b.name != name]
        meta = self._meta(lobby.game_key)
        view = self._build_lobby_view(lobby, meta)
        if lobby.surface:
            await lobby.surface.update(view)
        await self._success(interaction, "lobby.bot_removed", name=bot_label(self.emoji, name))

    async def open_settings(
        self, interaction: discord.Interaction, private: bool | None
    ) -> None:
        lobby = await self._require_caller_lobby(interaction)
        if lobby is None:
            return
        if private is not None:
            lobby.private = private
            meta = self._meta(lobby.game_key)
            view = self._build_lobby_view(lobby, meta)
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

