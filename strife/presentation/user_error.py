from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

from strife.config.text import TextConfig
from strife.presentation.compiler import Compiler
from strife.presentation.components import ButtonStyle, LayoutView
from strife.presentation.emoji import EmojiResolver
from strife.presentation.feedback import (
    FeedbackAction,
    build_feedback_view,
    disable_feedback_actions,
    send_ephemeral_feedback,
)
from strife.routing import prefixes as P

if TYPE_CHECKING:
    from strife.matchmaking.lobby import Lobby
    from strife.matchmaking.registries import SessionRegistries, UserLocation


@dataclass
class ErrorContext:
    interaction: discord.Interaction
    user_id: int | None = None
    lobby: Lobby | None = None
    location: UserLocation | None = None
    reason_key: str | None = None
    reason_kwargs: dict | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class ErrorAction(FeedbackAction):
    pass


ActionBuilder = Callable[
    ["UserErrorPresenter", ErrorContext],
    list[ErrorAction],
]


def resolve_session_link(
    location: UserLocation | None,
    registries: SessionRegistries,
) -> str | None:
    if location is None:
        return None
    if location.kind == "game":
        return f"https://discord.com/channels/{location.guild_id}/{location.thread_id}"
    lobby = registries.get_lobby(location.thread_id)
    return resolve_lobby_link(lobby, location.guild_id)


def resolve_lobby_link(lobby: Lobby | None, guild_id: int | None = None) -> str | None:
    if lobby is None:
        return None
    gid = guild_id or lobby.guild_id
    if lobby.message_id and lobby.channel_id:
        return f"https://discord.com/channels/{gid}/{lobby.channel_id}/{lobby.message_id}"
    if lobby.channel_id:
        return f"https://discord.com/channels/{gid}/{lobby.channel_id}"
    return f"https://discord.com/channels/{gid}"


def _help_section(code: str) -> str:
    section, _, _ = code.partition(".")
    return f"{section}_help"


def _help_name(code: str) -> str:
    _, _, name = code.partition(".")
    return name


def build_error_view(
    *,
    title: str,
    help_text: str,
    actions: list[ErrorAction],
    emoji: EmojiResolver,
    text: TextConfig,
) -> LayoutView:
    return build_feedback_view(
        icon=emoji.get("error"),
        title=title,
        body=help_text,
        body_heading=text.get("common.error_fix"),
        actions=actions,
        text=text,
    )


async def disable_error_actions(interaction: discord.Interaction) -> None:
    await disable_feedback_actions(interaction)


class UserErrorPresenter:
    def __init__(
        self,
        compiler: Compiler,
        emoji: EmojiResolver,
        text: TextConfig,
        registries: SessionRegistries,
    ) -> None:
        self.compiler = compiler
        self.emoji = emoji
        self.text = text
        self.registries = registries

    def _browse_games(self, ctx: ErrorContext) -> ErrorAction:
        return ErrorAction(
            label_key="errors.browse_games",
            style=ButtonStyle.PRIMARY,
            emoji="game",
            route_prefix=P.CAT_NAV,
            resource_id=ctx.interaction.user.id,
            source="catalog",
            payload={"page": 0},
        )

    def _go_to_session_link(self, ctx: ErrorContext) -> ErrorAction | None:
        loc = ctx.location
        if loc is None and ctx.user_id is not None:
            loc = self.registries.location_of(ctx.user_id)
        link = resolve_session_link(loc, self.registries)
        if not link:
            return None
        return ErrorAction(
            label_key="errors.go_to_session",
            style=ButtonStyle.LINK,
            emoji="forward",
            link_url=link,
        )

    def _go_to_lobby_link(self, ctx: ErrorContext) -> ErrorAction | None:
        lobby = ctx.lobby
        if lobby is None and ctx.location and ctx.location.kind == "lobby":
            lobby = self.registries.get_lobby(ctx.location.thread_id)
        link = resolve_lobby_link(lobby)
        if not link:
            return None
        return ErrorAction(
            label_key="errors.go_to_lobby",
            style=ButtonStyle.LINK,
            emoji="forward",
            link_url=link,
        )

    def _leave_current(self, ctx: ErrorContext) -> ErrorAction | None:
        loc = ctx.location
        if loc is None and ctx.user_id is not None:
            loc = self.registries.location_of(ctx.user_id)
        if loc is None or loc.kind != "lobby":
            return None
        return ErrorAction(
            label_key="errors.leave_current_game",
            style=ButtonStyle.DANGER,
            emoji="leave",
            route_prefix=P.LOBBY_LEAVE,
            resource_id=loc.thread_id,
            source="leave",
        )

    def _forfeit_current(self, ctx: ErrorContext) -> ErrorAction | None:
        loc = ctx.location
        if loc is None and ctx.user_id is not None:
            loc = self.registries.location_of(ctx.user_id)
        if loc is None or loc.kind != "game":
            return None
        return ErrorAction(
            label_key="errors.forfeit_current_game",
            style=ButtonStyle.DANGER,
            emoji="error",
            route_prefix=P.FORFEIT,
            resource_id=loc.thread_id,
            source="forfeit",
        )

    def _session_fix_actions(self, ctx: ErrorContext) -> list[ErrorAction]:
        loc = ctx.location
        if loc is None and ctx.user_id is not None:
            loc = self.registries.location_of(ctx.user_id)
        actions: list[ErrorAction] = []
        link_action = self._go_to_session_link(ctx)
        if link_action:
            actions.append(link_action)
        if loc and loc.kind == "lobby":
            leave = self._leave_current(ctx)
            if leave:
                actions.append(leave)
        elif loc and loc.kind == "game":
            forfeit = self._forfeit_current(ctx)
            if forfeit:
                actions.append(forfeit)
        return actions

    def _lobby_context_actions(self, ctx: ErrorContext) -> list[ErrorAction]:
        actions: list[ErrorAction] = []
        link = self._go_to_lobby_link(ctx)
        if link:
            actions.append(link)
        return actions

    def _build_actions(self, code: str, ctx: ErrorContext) -> list[ErrorAction]:
        builders: dict[str, ActionBuilder] = {
            "errors.already_in_session": lambda p, c: p._session_fix_actions(c),
            "errors.not_in_lobby": lambda p, c: [p._browse_games(c)],
            "errors.not_in_game": lambda p, c: p._not_in_game_actions(c),
            "errors.not_on_whitelist": lambda p, c: p._lobby_context_actions(c),
            "errors.request_pending": lambda p, c: p._lobby_context_actions(c),
            "errors.request_denied": lambda p, c: p._lobby_context_actions(c),
            "errors.blacklisted": lambda p, c: p._lobby_context_actions(c),
            "errors.game_disabled": lambda p, c: [p._browse_games(c)],
            "errors.unknown_game": lambda p, c: [p._browse_games(c)],
            "lobby.creator_only": lambda p, c: p._lobby_context_actions(c),
            "lobby.closed": lambda p, c: [p._browse_games(c)],
            "lobby.already_dead": lambda p, c: [p._browse_games(c)],
            "common.game_ended": lambda p, c: [p._browse_games(c)],
            "common.button_expired": lambda p, c: [p._browse_games(c)],
            "errors.no_session": lambda p, c: [p._browse_games(c)],
            "errors.rematch_expired": lambda p, c: [p._browse_games(c)],
            "errors.rematch_unavailable": lambda p, c: [p._browse_games(c)],
            "errors.lobby_failed_to_start": lambda p, c: [p._browse_games(c)],
            "errors.need_text_channel": lambda p, c: [p._browse_games(c)],
            "common.match_not_found": lambda p, c: [p._open_profile(c)],
            "common.error": lambda p, c: [p._browse_games(c)],
            "errors.need_players": lambda p, c: p._cannot_ready_actions(c),
            "errors.role_selection_incomplete": lambda p, c: p._cannot_ready_actions(c),
            "errors.invalid_roles": lambda p, c: p._cannot_ready_actions(c),
            "errors.not_all_ready": lambda p, c: p._cannot_ready_actions(c),
        }
        builder = builders.get(code)
        if builder is None:
            return []
        return builder(self, ctx)

    def _not_in_game_actions(self, ctx: ErrorContext) -> list[ErrorAction]:
        loc = ctx.location
        if loc is None and ctx.user_id is not None:
            loc = self.registries.location_of(ctx.user_id)
        if loc and loc.kind == "lobby":
            leave = self._leave_current(
                ErrorContext(
                    interaction=ctx.interaction,
                    user_id=ctx.user_id,
                    location=loc,
                )
            )
            if leave:
                return [leave]
        return [self._browse_games(ctx)]

    def _cannot_ready_actions(self, ctx: ErrorContext) -> list[ErrorAction]:
        actions: list[ErrorAction] = []
        link = self._go_to_lobby_link(ctx)
        if link:
            actions.append(link)
        lobby = ctx.lobby
        if (
            lobby
            and ctx.interaction.user.id == lobby.creator_id
            and ctx.reason_key == "errors.need_players"
        ):
            actions.append(
                ErrorAction(
                    label_key="errors.open_settings",
                    style=ButtonStyle.SECONDARY,
                    emoji="settings",
                    route_prefix=P.LOBBY_SETTINGS,
                    resource_id=lobby.thread_id,
                    source="settings",
                )
            )
        if not actions:
            actions.append(self._browse_games(ctx))
        return actions

    def _open_profile(self, ctx: ErrorContext) -> ErrorAction:
        return ErrorAction(
            label_key="errors.open_profile",
            style=ButtonStyle.SECONDARY,
            emoji="user",
            route_prefix=P.PROF_NAV,
            resource_id=ctx.interaction.user.id,
            source="profile",
            payload={"page": 0},
        )

    def _title(self, code: str, ctx: ErrorContext) -> str:
        kwargs = dict(ctx.reason_kwargs or {})
        if code in {
            "errors.need_players",
            "errors.role_selection_incomplete",
            "errors.invalid_roles",
            "errors.not_all_ready",
        }:
            reason = self.text.get(code, **kwargs)
            return self.text.get("lobby.cannot_start", reason=reason)
        return self.text.get(code, **kwargs)

    def _help(self, code: str, ctx: ErrorContext) -> str:
        help_key = f"{_help_section(code)}.{_help_name(code)}"
        help_text = self.text.get(help_key)
        if help_text == help_key:
            return self.text.get("common_help.error")
        kwargs = dict(ctx.reason_kwargs or {})
        if code in {
            "errors.not_on_whitelist",
            "errors.request_pending",
            "errors.request_denied",
            "errors.blacklisted",
        } and ctx.lobby:
            kwargs["creator"] = f"<@{ctx.lobby.creator_id}>"
        if kwargs:
            try:
                return help_text.format(**kwargs)
            except (KeyError, ValueError):
                pass
        return help_text

    def _compile_prefix(self, code: str, ctx: ErrorContext) -> str:
        actions = self._build_actions(code, ctx)
        for action in actions:
            if action.route_prefix:
                return action.route_prefix
        return P.CAT_NAV

    def _resource_id(self, code: str, ctx: ErrorContext) -> int:
        if ctx.lobby:
            return ctx.lobby.thread_id
        loc = ctx.location
        if loc is None and ctx.user_id is not None:
            loc = self.registries.location_of(ctx.user_id)
        if loc:
            return loc.thread_id
        return ctx.interaction.user.id

    async def send(
        self,
        interaction: discord.Interaction,
        code: str,
        *,
        context: ErrorContext | None = None,
    ) -> None:
        ctx = context or ErrorContext(interaction=interaction)
        if ctx.user_id is None:
            ctx.user_id = interaction.user.id

        title = self._title(code, ctx)
        help_text = self._help(code, ctx)
        actions = self._build_actions(code, ctx)
        view = build_error_view(
            title=title,
            help_text=help_text,
            actions=actions,
            emoji=self.emoji,
            text=self.text,
        )
        prefix = self._compile_prefix(code, ctx)
        resource_id = self._resource_id(code, ctx)
        await send_ephemeral_feedback(
            interaction,
            view,
            compiler=self.compiler,
            prefix=prefix,
            resource_id=resource_id,
        )
