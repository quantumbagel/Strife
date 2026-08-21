from __future__ import annotations

import discord

from strife.engine.metadata import OptionType, int_setting_bounds
from strife.matchmaking.lobby import Lobby
from strife.matchmaking.settings_view import build_settings_view, normalize_settings_tab
from strife.presentation.compiler import LayoutError
from strife.presentation.modals import IntRangeModal, ROLE_ASSIGN_MODAL_BATCH, RoleAssignmentModal
from strife.routing import prefixes as P
from strife.routing.custom_id import Route


class LobbyControlsMixin:
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
            compiler=self.compiler,
            emoji=self.emoji,
            text=self.text,
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

