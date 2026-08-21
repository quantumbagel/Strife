from __future__ import annotations

import discord

from strife.engine.metadata import OptionType, int_setting_bounds
from strife.matchmaking.lobby import Lobby, QueuedBot
from strife.presentation.roster import bot_label
from strife.routing import prefixes as P
from strife.routing.custom_id import Route


class LobbyCommandsMixin:
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
            approved_name = await self._display_name(interaction.guild, user_id)
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
            revoked_name = await self._display_name(interaction.guild, user_id)
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
            name = await self._display_name(interaction.guild, user_id)
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
            name = await self._display_name(interaction.guild, user_id)
            await self._success(interaction, "lobby.blacklist_removed", name=name)

    async def add_bots(
        self, interaction: discord.Interaction, difficulty: str, number: int
    ) -> None:
        lobby = await self._require_caller_lobby(interaction, creator_only=True)
        if lobby is None:
            return
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
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
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
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
        async with lobby.lock:
            if self.registries.get_lobby(lobby.thread_id) is not lobby:
                await self._error(interaction, "lobby.already_dead")
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

