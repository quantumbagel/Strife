from __future__ import annotations

from dataclasses import dataclass

import discord

from strife.config.text import TextConfig
from strife.logging import get_logger
from strife.routing import prefixes as P
from strife.engine.errors import SessionError
from strife.routing.custom_id import CustomIdEncoder, CustomIdError, PayloadExpired
from strife.presentation.feedback import disable_feedback_actions
from strife.presentation.user_error import ErrorContext, UserErrorPresenter

log = get_logger("routing.router")


@dataclass
class InteractionInput:
    actor: discord.abc.User
    source: str
    args: dict
    interaction: discord.Interaction
    values: list[str] | None = None


_RUNTIME_ERROR_CODES = {
    "cannot_act": "common.cannot_act",
    "not_a_player": "errors.not_a_player",
    "invalid_action": "errors.invalid_action",
    "rematch_expired": "errors.rematch_expired",
    "rematch_not_eligible": "errors.rematch_not_eligible",
    "rematch_unavailable": "errors.rematch_unavailable",
    "no_session": "errors.no_session",
    "unknown_game": "errors.unknown_game",
    "query_failed": "common.error",
    "already_in_session": "errors.already_in_session",
}


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
        about: object | None = None,
        server_settings: object | None = None,
        encoder: CustomIdEncoder,
        text: TextConfig,
        user_errors: UserErrorPresenter,
    ) -> None:
        self.sessions = sessions
        self.replay = replay
        self.lobby = lobby
        self.lifecycle = lifecycle
        self.profile = profile
        self.catalog = catalog
        self.about = about
        self.server_settings = server_settings
        self.encoder = encoder
        self.text = text
        self.user_errors = user_errors

    async def _error(
        self,
        interaction: discord.Interaction,
        code: str,
        *,
        user_id: int | None = None,
    ) -> None:
        await self.user_errors.send(
            interaction,
            code,
            context=ErrorContext(
                interaction=interaction,
                user_id=user_id or interaction.user.id,
                location=(
                    self.sessions.location_of(user_id or interaction.user.id)
                    if hasattr(self.sessions, "location_of")
                    else None
                ),
            ),
        )

    async def dispatch(self, interaction: discord.Interaction) -> None:
        custom_id = interaction.data.get("custom_id") if interaction.data else None
        if not custom_id:
            return
        try:
            route = self.encoder.decode(custom_id)
        except PayloadExpired:
            await self._disable_and_report_ended(interaction, "common.button_expired")
            return
        except (CustomIdError, KeyError, ValueError, TypeError) as exc:
            log.warning("Bad custom_id %s: %s", custom_id, exc)
            await self._error(interaction, "common.error")
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
            elif route.prefix == P.PROF_NAV:
                await self._handle_profile(route, interaction)
            elif route.prefix == P.ABOUT_NAV:
                await self._handle_about(route, interaction)
            elif route.prefix == P.FORFEIT:
                await self._handle_forfeit(route, interaction)
            elif route.prefix in P.SERVER_PREFIXES:
                await self._handle_server(route, interaction)
            else:
                log.warning("Unknown prefix %s", route.prefix)
                await self._error(interaction, "common.error")
        except PermissionError:
            await self._error(interaction, "common.forbidden")
        except SessionError as exc:
            code = _RUNTIME_ERROR_CODES.get(exc.code)
            if code:
                await self._error(interaction, code)
            else:
                log.exception("Router session error")
                await self._error(interaction, "common.error")
        except Exception:
            log.exception("Unhandled router error")
            await self._error(interaction, "common.error")

    async def _handle_game(self, route, interaction: discord.Interaction) -> None:
        session = self.sessions.get_game(route.resource_id)
        if session is None:
            await self._disable_and_report_ended(interaction, "common.game_ended")
            return

        args = dict(route.payload)
        is_query = bool(args.pop("q", None))
        if is_query:
            if await session.handle_query(route.source, interaction):
                return
            await self._error(interaction, "errors.invalid_action")
            return

        values = interaction.data.get("values") if interaction.data else None
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
            await self._error(interaction, "common.error")
            return
        if route.source == "open" or route.payload.get("fresh"):
            values = interaction.data.get("values") if interaction.data else None
            target = values[0] if values else route.resource_id
            await self.replay.open(interaction, target)
            return
        owner_id = int(route.payload.get("owner", interaction.user.id))
        if "owner" in route.payload and owner_id != interaction.user.id:
            await self._error(interaction, "common.replay_owner_only")
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
            await self._error(interaction, "common.error")
            return
        await self._defer(interaction)
        await self.lifecycle.register_rematch_vote(route.resource_id, interaction.user)

    async def _handle_lobby(self, route, interaction: discord.Interaction) -> None:
        if self.lobby is None:
            await self._error(interaction, "common.error")
            return
        await self.lobby.handle(route, interaction)

    async def _handle_catalog(self, route, interaction: discord.Interaction) -> None:
        if self.catalog is None:
            await self._error(interaction, "common.error")
            return
        if route.payload.get("jump"):
            pages = int(route.payload.get("pages", 1))
            page = int(route.payload.get("page", 0))
            await self.catalog.open_jump_modal(interaction, pages=pages, page=page)
            return
        play_game = route.payload.get("play")
        if play_game:
            if self.lobby is None:
                await self._error(interaction, "common.error")
                return
            await self.lobby.create_lobby(interaction, play_game, private=False)
            return
        page = int(route.payload.get("page", 0))
        if interaction.message is not None:
            await self.catalog.navigate(interaction, page)
        else:
            await self.catalog.show(interaction, page)

    async def _handle_profile(self, route, interaction: discord.Interaction) -> None:
        if self.profile is None:
            await self._error(interaction, "common.error")
            return
        if route.payload.get("jump"):
            await self.profile.open_jump_modal(interaction, route)
            return
        if route.source == "profile" and not interaction.response.is_done():
            await self.profile.show(interaction, interaction.user, None, 0)
        else:
            await self.profile.navigate(interaction, route)

    async def _handle_server(self, route, interaction: discord.Interaction) -> None:
        if self.server_settings is None:
            await self._error(interaction, "common.error")
            return
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.guild_permissions.administrator:
            await self._error(interaction, "common.forbidden")
            return
        if route.prefix == P.SERVER_CHANNEL:
            values = interaction.data.get("values") if interaction.data else []
            if not values:
                await self._defer(interaction)
                return
            await self.server_settings.set_channel(interaction, int(values[0]))
            return
        if route.prefix == P.SERVER_CLEAR:
            await self.server_settings.clear_channel(interaction)
            return
        await self.server_settings.open(interaction, edit=True)

    async def _handle_about(self, route, interaction: discord.Interaction) -> None:
        if self.about is None:
            await self._error(interaction, "common.error")
            return
        await self.about.navigate(interaction, route)

    async def _defer(self, interaction: discord.Interaction) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer()

    async def _disable_and_report_ended(self, interaction: discord.Interaction, message_key: str) -> None:
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
                log.warning("Failed to disable components on old message: %s", exc)

        await self._error(interaction, message_key)

    async def _handle_forfeit(self, route, interaction: discord.Interaction) -> None:
        if self.lifecycle is None:
            await self._error(interaction, "common.error")
            return

        await disable_feedback_actions(interaction)
        if not interaction.response.is_done():
            await self._defer(interaction)

        try:
            await self.lifecycle.forfeit(route.resource_id, interaction.user.id)
            if self.lobby is not None:
                await self.lobby.user_success.send(interaction, "match.forfeited")
            return
        except SessionError as e:
            code = "errors.no_session" if e.code == "no_session" else "common.error"
            await self._error(interaction, code)
        except PermissionError:
            await self._error(interaction, "errors.not_in_game")
