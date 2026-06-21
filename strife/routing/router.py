from __future__ import annotations

from dataclasses import dataclass

import discord

from strife.config.text import TextConfig
from strife.logging import get_logger
from strife.routing import prefixes as P
from strife.routing.custom_id import CustomIdEncoder, CustomIdError, PayloadExpired
from strife.presentation.message import send_ephemeral_error

log = get_logger("routing.router")


@dataclass
class InteractionInput:
    actor: discord.abc.User
    source: str
    args: dict
    interaction: discord.Interaction
    values: list[str] | None = None


class InteractionRouter:
    def __init__(
        self,
        *,
        sessions: object,
        replay: object | None,
        lobby: object | None,
        lifecycle: object | None,
        profile: object | None,
        catalog: object | None,
        server_settings: object | None,
        encoder: CustomIdEncoder,
        text: TextConfig,
    ) -> None:
        self.sessions = sessions
        self.replay = replay
        self.lobby = lobby
        self.lifecycle = lifecycle
        self.profile = profile
        self.catalog = catalog
        self.server_settings = server_settings
        self.encoder = encoder
        self.text = text

    async def dispatch(self, interaction: discord.Interaction) -> None:
        custom_id = interaction.data.get("custom_id") if interaction.data else None
        if not custom_id:
            return
        try:
            route = self.encoder.decode(custom_id)
        except PayloadExpired:
            await self._disable_and_report_ended(interaction, "common.game_ended")
            return
        except (CustomIdError, KeyError, ValueError, TypeError) as exc:
            log.warning("Bad custom_id %s: %s", custom_id, exc)
            await self._ephemeral(interaction, self.text.get("common.error"))
            return

        if route.prefix == P.REPLAY_NOOP:
            await self._defer(interaction)
            return

        try:
            if route.prefix in P.GAME_PREFIXES:
                await self._handle_game(route, interaction)
            elif route.prefix == P.R_NAV:
                await self._handle_replay(route, interaction)
            elif route.prefix == P.REMATCH:
                await self._handle_rematch(route, interaction)
            elif route.prefix in P.LOBBY_PREFIXES:
                await self._handle_lobby(route, interaction)
            elif route.prefix == P.CAT_NAV:
                await self._handle_catalog(route, interaction)
            elif route.prefix in {P.PROF_NAV, P.PROF_OPEN}:
                await self._handle_profile(route, interaction)
            elif route.prefix == P.ABOUT_NAV:
                await self._handle_about(route, interaction)
            elif route.prefix == P.FORFEIT:
                await self._handle_forfeit(route, interaction)
            elif route.prefix in P.SERVER_PREFIXES:
                await self._handle_server(route, interaction)
            else:
                log.warning("Unknown prefix %s", route.prefix)
                await self._ephemeral(interaction, self.text.get("common.error"))
        except PermissionError:
            await self._ephemeral(interaction, self.text.get("common.forbidden"))
        except RuntimeError as exc:
            err_str = str(exc)
            if err_str == "not_your_turn":
                await self._ephemeral(interaction, self.text.get("common.not_your_turn"))
            elif err_str == "not_a_player":
                await self._ephemeral(interaction, self.text.get("errors.not_a_player"))
            elif err_str == "invalid_action":
                await self._ephemeral(interaction, self.text.get("errors.invalid_action"))
            elif err_str == "rematch_expired":
                await self._ephemeral(interaction, self.text.get("errors.rematch_expired"))
            elif err_str == "rematch_not_eligible":
                await self._ephemeral(interaction, self.text.get("errors.rematch_not_eligible"))
            elif err_str == "rematch_unavailable":
                await self._ephemeral(interaction, self.text.get("errors.rematch_unavailable"))
            else:
                log.exception("Router error")
                await self._ephemeral(interaction, self.text.get("common.error"))

    async def _handle_game(self, route, interaction: discord.Interaction) -> None:
        session = self.sessions.get_game(route.resource_id)
        if session is None:
            await self._disable_and_report_ended(interaction, "common.game_ended")
            return
        values = interaction.data.get("values") if interaction.data else None
        args = dict(route.payload)
        if values:
            args["values"] = values
            if len(values) == 1:
                args["value"] = values[0]
        await self._defer(interaction)
        await session.submit(
            InteractionInput(
                actor=interaction.user,
                source=route.source,
                args=args,
                interaction=interaction,
                values=values,
            )
        )

    async def _handle_replay(self, route, interaction: discord.Interaction) -> None:
        if self.replay is None:
            await self._ephemeral(interaction, self.text.get("common.error"))
            return
        owner_id = int(route.payload.get("owner", interaction.user.id))
        if owner_id != interaction.user.id:
            await self._ephemeral(interaction, self.text.get("common.replay_owner_only"))
            return
        if route.payload.get("jump"):
            total = int(route.payload.get("total", 1))
            frame = int(route.payload.get("frame", 0))
            await self.replay.open_jump_modal(
                interaction,
                route.resource_id,
                owner_id=owner_id,
                total=total,
                frame=frame,
            )
            return
        frame = int(route.payload.get("frame", 0))
        await self.replay.render_frame(route.resource_id, frame, interaction, owner_id=owner_id)

    async def _handle_rematch(self, route, interaction: discord.Interaction) -> None:
        if self.lifecycle is None:
            await self._ephemeral(interaction, self.text.get("common.error"))
            return
        await self._defer(interaction)
        await self.lifecycle.register_rematch_vote(route.resource_id, interaction.user)

    async def _handle_lobby(self, route, interaction: discord.Interaction) -> None:
        if self.lobby is None:
            await self._ephemeral(interaction, self.text.get("common.error"))
            return
        await self.lobby.handle(route, interaction)

    async def _handle_catalog(self, route, interaction: discord.Interaction) -> None:
        if self.catalog is None:
            await self._ephemeral(interaction, self.text.get("common.error"))
            return
        if route.payload.get("jump"):
            pages = int(route.payload.get("pages", 1))
            page = int(route.payload.get("page", 0))
            await self.catalog.open_jump_modal(interaction, pages=pages, page=page)
            return
        page = int(route.payload.get("page", 0))
        await self.catalog.navigate(interaction, page)

    async def _handle_profile(self, route, interaction: discord.Interaction) -> None:
        if self.profile is None:
            await self._ephemeral(interaction, self.text.get("common.error"))
            return
        if route.payload.get("jump"):
            await self.profile.open_jump_modal(interaction, route)
            return
        await self.profile.navigate(interaction, route)

    async def _handle_server(self, route, interaction: discord.Interaction) -> None:
        if self.server_settings is None:
            await self._ephemeral(interaction, self.text.get("common.error"))
            return
        if route.prefix == P.SERVER_CHANNEL:
            values = interaction.data.get("values") if interaction.data else []
            if not values:
                await self._defer(interaction)
                return
            await self.server_settings.set_channel(interaction, int(values[0]))
            return
        await self.server_settings.open(interaction, edit=True)

    async def _handle_about(self, route, interaction: discord.Interaction) -> None:
        if self.lobby is None:
            await self._ephemeral(interaction, self.text.get("common.error"))
            return
        from strife.presentation.about_view import build_about_view

        active_tab = route.payload.get("tab", "main")
        view = build_about_view(self.lobby.emoji, self.text, active_tab=active_tab)
        compiled = self.lobby.compiler.compile(view, resource_id=interaction.user.id, prefix=P.ABOUT_NAV)
        await interaction.response.edit_message(view=compiled)

    async def _defer(self, interaction: discord.Interaction) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer()

    async def _ephemeral(self, interaction: discord.Interaction, content: str) -> None:
        await send_ephemeral_error(interaction, content)

    async def _disable_and_report_ended(self, interaction: discord.Interaction, message_key: str) -> None:
        content = self.text.get(message_key)
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
                
                await send_ephemeral_error(interaction, content)
                return
            except Exception as exc:
                log.warning("Failed to disable components on old message: %s", exc)

        await send_ephemeral_error(interaction, content)

    async def _handle_forfeit(self, route, interaction: discord.Interaction) -> None:
        if self.lifecycle is None:
            await self._ephemeral(interaction, self.text.get("common.error"))
            return

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
            except Exception as exc:
                log.warning("Failed to disable forfeit button: %s", exc)
                if not interaction.response.is_done():
                    await self._defer(interaction)
        else:
            if not interaction.response.is_done():
                await self._defer(interaction)

        try:
            await self.lifecycle.forfeit(route.resource_id, interaction.user.id)
            await send_ephemeral_error(interaction, self.text.get("match.forfeited"))
        except RuntimeError as e:
            if str(e) == "no_session":
                await send_ephemeral_error(interaction, self.text.get("errors.no_session"))
            else:
                await send_ephemeral_error(interaction, self.text.get("common.error"))
        except PermissionError:
            await send_ephemeral_error(interaction, self.text.get("errors.not_in_game"))
