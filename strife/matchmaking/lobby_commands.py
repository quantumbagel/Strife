from __future__ import annotations

import discord

from strife.engine.metadata import OptionType
from strife.presentation.settings import int_setting_bounds
from strife.matchmaking.lobby import (
    Lobby,
    LobbyGone,
    QueuedBot,
    allocate_bot_name,
    lobby_action,
)
from strife.presentation.roster import bot_label
from strife.routing import prefixes as P
from strife.routing.custom_id import Route


class LobbyCommandsMixin:
    @lobby_action
    async def join_by_creator(
        self, interaction: discord.Interaction, creator_id: int
    ) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        lobby = self.lobby_by_creator(creator_id)
        if lobby is None:
            await self._error(interaction, "errors.creator_not_hosting")
            return
        if not await self._check_lobby_channel_access(interaction, lobby):
            return
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            await self._join_user(
                lobby, interaction, announce_join=True, check_channel_access=False
            )

    @lobby_action
    async def leave_current(self, interaction: discord.Interaction) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        if self.registries.location_of(interaction.user.id) is None:
            if await self._withdraw_pending_requests(interaction):
                return
        lobby = await self._require_caller_lobby(
            interaction, require_channel_access=False
        )
        if lobby is None:
            return
        should_start = False
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            try:
                await self._leave_lobby_inner(lobby, interaction.user.id, interaction)
            except PermissionError:
                await self._error(interaction, "errors.not_in_lobby", lobby=lobby)
                return
            if self.registries.get_lobby(lobby.thread_id) is lobby:
                await self._success(interaction, "lobby.left")
                should_start = self._claim_autostart(lobby)
        if should_start:
            await self._start(
                lobby, Route(P.LOBBY_LEAVE, lobby.thread_id, "leave", {}), interaction
            )

    async def _withdraw_pending_requests(
        self, interaction: discord.Interaction
    ) -> bool:
        """Withdraw the caller's join requests in this server; True if any were pending."""
        lobby_ids = self.registries.guild_lobbies.get(interaction.guild_id or 0, set())
        candidates = [
            lobby
            for lobby_id in list(lobby_ids)
            if (lobby := self.registries.get_lobby(lobby_id)) is not None
            and interaction.user.id in lobby.pending_requests
        ]
        withdrawn = False
        for lobby in candidates:
            async with lobby.lock:
                if self.registries.get_lobby(lobby.thread_id) is not lobby:
                    continue
                try:
                    if await self._withdraw_request(lobby, interaction, notify=False):
                        withdrawn = True
                except LobbyGone:
                    withdrawn = True
        if withdrawn:
            await self._success(interaction, "lobby.request_withdrawn")
        return withdrawn

    @lobby_action
    async def toggle_ready(self, interaction: discord.Interaction) -> None:
        lobby = await self._require_caller_lobby(interaction)
        if lobby is None:
            return
        should_start = False
        route = Route(P.LOBBY_READY, lobby.thread_id, "ready", {})
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            if interaction.user.id in lobby.ready:
                lobby.ready.discard(interaction.user.id)
                if await self._refresh(lobby, interaction):
                    await self._success(interaction, "lobby.ready_off")
                return
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
            lobby.mark_ready(interaction.user.id)
            ok_start, _, _ = lobby.can_start(meta, self.text)
            if ok_start:
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True)
                lobby.starting = True
                should_start = True
            else:
                if await self._refresh(lobby, interaction):
                    await self._success(interaction, "lobby.ready_on")
        if should_start:
            await self._start(lobby, route, interaction)
            # The lobby message already links the thread; drop the deferred "thinking…".
            # (Only on a real launch: otherwise the original response holds the error.)
            if self.registries.get_lobby(lobby.thread_id) is None and lobby.launching:
                try:
                    await interaction.delete_original_response()
                except discord.HTTPException:
                    pass

    @lobby_action
    async def kick_member(self, interaction: discord.Interaction, user_id: int) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        should_start = False
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            if user_id == lobby.creator_id:
                await self._error(interaction, "errors.cannot_kick_self", lobby=lobby)
                return
            member = next((m for m in lobby.members if m.user_id == user_id), None)
            if member is None:
                await self._error(
                    interaction, "errors.kick_target_not_seated", lobby=lobby
                )
                return
            kicked_name = member.display_name
            lobby.approved.discard(user_id)
            lobby.pending_requests.pop(user_id, None)
            await self._eject_member(lobby, user_id)
            if not lobby.members:
                await self._teardown(lobby, interaction)
                return
            if await self._refresh(lobby, interaction):
                await self._success(
                    interaction, "lobby.player_kicked", name=kicked_name
                )
            should_start = self._claim_autostart(lobby)
        if should_start:
            await self._start(
                lobby, Route(P.LOBBY_KICK, lobby.thread_id, "kick", {}), interaction
            )

    @lobby_action
    async def end_lobby(self, interaction: discord.Interaction) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            await self._teardown(lobby, interaction)

    @lobby_action
    async def clear_ready(self, interaction: discord.Interaction) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            lobby.ready.clear()
            if await self._refresh(lobby, interaction):
                await self._success(interaction, "lobby.ready_cleared")

    @lobby_action
    async def set_privacy(
        self, interaction: discord.Interaction, private: bool
    ) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            lobby.private = private
            if await self._refresh(lobby, interaction):
                mode = self.text.get(
                    "lobby.private_label" if private else "lobby.public_label"
                )
                await self._success(interaction, "lobby.privacy_updated", mode=mode)

    @lobby_action
    async def reset_privacy(self, interaction: discord.Interaction) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            lobby.private = False
            lobby.approved.clear()
            lobby.pending_requests.clear()
            lobby.denied.clear()
            lobby.blacklist.clear()
            if await self._refresh(lobby, interaction):
                await self._success(interaction, "lobby.privacy_reset")

    @lobby_action
    async def reset_rules(self, interaction: discord.Interaction) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            before = lobby.settings
            lobby.settings = self._default_settings(self._meta(lobby.game_key))
            if lobby.settings != before:
                lobby.reset_ready()
            if await self._refresh(lobby, interaction):
                await self._success(interaction, "lobby.rules_reset")

    @lobby_action
    async def set_option(
        self, interaction: discord.Interaction, key: str, value: str
    ) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            meta = self._meta(lobby.game_key)
            option = next((o for o in meta.settings if o.key == key), None)
            if option is None:
                await self._error(interaction, "lobby.unknown_option", lobby=lobby)
                return
            before = dict(lobby.settings)
            display_value: str
            if option.type == OptionType.BOOL:
                normalized = value.strip().lower()
                if normalized not in {"true", "false", "on", "off", "1", "0"}:
                    await self._error(
                        interaction, "lobby.invalid_option_value", lobby=lobby
                    )
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
                    await self._error(
                        interaction, "lobby.invalid_int_option", lobby=lobby
                    )
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
                    await self._error(
                        interaction, "lobby.invalid_option_value", lobby=lobby
                    )
                    return
                lobby.settings[key] = value
                display_value = (
                    value.capitalize() if isinstance(value, str) else str(value)
                )
            if lobby.settings != before:
                lobby.reset_ready()
            if await self._refresh(lobby, interaction):
                await self._success(
                    interaction,
                    "lobby.option_set",
                    title=option.title,
                    value=display_value,
                )

    @lobby_action
    async def approve_request(
        self, interaction: discord.Interaction, user_id: int
    ) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        if not await self._check_lobby_channel_access(
            interaction, lobby, user_id=user_id
        ):
            return
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            if user_id not in lobby.pending_requests:
                await self._error(interaction, "errors.no_pending_request", lobby=lobby)
                return
            if user_id in lobby.blacklist:
                await self._error(interaction, "errors.blacklisted", lobby=lobby)
                return
            meta = self._meta(lobby.game_key)
            if lobby.is_full(meta):
                await self._error(interaction, "errors.lobby_full_creator", lobby=lobby)
                return
            display_name = lobby.pending_requests.get(user_id)
            if display_name is None:
                await self._error(interaction, "errors.no_pending_request", lobby=lobby)
                await self._refresh(lobby, interaction)
                return
            if not self._is_lobby_member(lobby, user_id):
                if not await self._seat_member(
                    lobby, user_id, display_name, interaction
                ):
                    await self._refresh(lobby, interaction)
                    return
            lobby.pending_requests.pop(user_id, None)
            lobby.approved.add(user_id)
            lobby.denied.discard(user_id)
            if await self._refresh(lobby, interaction):
                await self._success(
                    interaction, "lobby.player_approved", name=display_name
                )

    @lobby_action
    async def deny_request(
        self, interaction: discord.Interaction, user_id: int
    ) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            if user_id not in lobby.pending_requests:
                await self._error(interaction, "errors.no_pending_request", lobby=lobby)
                return
            if self._is_lobby_member(lobby, user_id):
                lobby.pending_requests.pop(user_id, None)
                await self._error(interaction, "errors.no_pending_request", lobby=lobby)
                await self._refresh(lobby, interaction)
                return
            display_name = lobby.pending_requests.pop(user_id, f"User {user_id}")
            lobby.denied.add(user_id)
            if await self._refresh(lobby, interaction):
                await self._success(
                    interaction, "lobby.player_denied", name=display_name
                )

    @lobby_action
    async def preapprove_user(
        self, interaction: discord.Interaction, user_id: int
    ) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
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
            approved_name = await self._display_name(interaction.guild, user_id)
            if await self._refresh(lobby, interaction):
                await self._success(
                    interaction, "lobby.player_preapproved", name=approved_name
                )

    @lobby_action
    async def revoke_approval(
        self, interaction: discord.Interaction, user_id: int
    ) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            seated_ids = {member.user_id for member in lobby.members}
            if user_id not in lobby.approved or user_id in seated_ids:
                await self._error(interaction, "errors.not_preapproved", lobby=lobby)
                return
            lobby.approved.discard(user_id)
            revoked_name = await self._display_name(interaction.guild, user_id)
            if await self._refresh(lobby, interaction):
                await self._success(
                    interaction, "lobby.approval_revoked", name=revoked_name
                )

    @lobby_action
    async def blacklist_add(
        self, interaction: discord.Interaction, user_id: int
    ) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        should_start = False
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            if user_id == lobby.creator_id:
                await self._error(
                    interaction, "errors.cannot_blacklist_self", lobby=lobby
                )
                return
            lobby.blacklist.add(user_id)
            lobby.approved.discard(user_id)
            lobby.pending_requests.pop(user_id, None)
            kicked = await self._eject_member(lobby, user_id)
            if kicked and not lobby.members:
                await self._teardown(lobby, interaction)
                return
            if await self._refresh(lobby, interaction):
                name = await self._display_name(interaction.guild, user_id)
                await self._success(interaction, "lobby.blacklist_added", name=name)
            should_start = self._claim_autostart(lobby)
        if should_start:
            await self._start(
                lobby,
                Route(P.LOBBY_ADD_BLACKLIST, lobby.thread_id, "blacklist", {}),
                interaction,
            )

    @lobby_action
    async def blacklist_remove(
        self, interaction: discord.Interaction, user_id: int
    ) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            if user_id not in lobby.blacklist:
                await self._error(interaction, "errors.not_blacklisted", lobby=lobby)
                return
            lobby.blacklist.discard(user_id)
            name = await self._display_name(interaction.guild, user_id)
            await self._success(interaction, "lobby.blacklist_removed", name=name)

    @lobby_action
    async def add_bots(
        self, interaction: discord.Interaction, difficulty: str | None, number: int
    ) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            meta = self._meta(lobby.game_key)
            allowed = {spec.difficulty for spec in meta.bots or ()}
            if not allowed:
                await self._error(interaction, "lobby.game_has_no_bots", lobby=lobby)
                return
            if difficulty is None:
                difficulty = meta.bots[0].difficulty
            elif difficulty not in allowed:
                await self._error(
                    interaction, "lobby.unknown_bot_difficulty", lobby=lobby
                )
                return
            added = 0
            for _ in range(number):
                if lobby.is_full(meta):
                    break
                lobby.bots.append(
                    QueuedBot(
                        name=allocate_bot_name(lobby.bots, difficulty),
                        difficulty=difficulty,
                    )
                )
                added += 1
            if added == 0:
                await self._error(interaction, "errors.lobby_full_creator", lobby=lobby)
                return
            # A new seat changes the match; everyone confirms again rather than auto-starting.
            lobby.reset_ready()
            if await self._refresh(lobby, interaction):
                await self._success(interaction, "lobby.bot_added", count=added)

    @lobby_action
    async def remove_bot(self, interaction: discord.Interaction, name: str) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            remaining = [b for b in lobby.bots if b.name != name]
            if len(remaining) == len(lobby.bots):
                await self._error(interaction, "lobby.bot_not_in_lobby", lobby=lobby)
                return
            lobby.bots = remaining
            lobby.reset_ready()
            if await self._refresh(lobby, interaction):
                await self._success(
                    interaction, "lobby.bot_removed", name=bot_label(self.emoji, name)
                )

    @lobby_action
    async def open_settings(self, interaction: discord.Interaction) -> None:
        lobby = await self._require_caller_lobby(interaction)
        if lobby is None:
            return
        async with lobby.lock:
            if not await self._lobby_still_open(lobby, interaction):
                return
            await self._settings(
                lobby,
                Route(P.LOBBY_SETTINGS, lobby.thread_id, "settings", {}),
                interaction,
            )
