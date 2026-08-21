from __future__ import annotations

import discord

from strife.matchmaking.lobby import Lobby, QueuedBot
from strife.presentation.roster import bot_label
from strife.routing.custom_id import Route


class LobbyModerationMixin:
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
            approved_name = await self._display_name(interaction.guild, target_id)
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
            revoked_name = await self._display_name(interaction.guild, target_id)
        await interaction.response.defer(ephemeral=True)
        await self._send_settings(lobby, interaction, tab="access", edit=True)
        if revoked_name:
            await self._success(interaction, "lobby.approval_revoked", name=revoked_name)

