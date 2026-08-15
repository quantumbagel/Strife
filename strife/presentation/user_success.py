from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import discord

from strife.config.text import TextConfig
from strife.presentation.compiler import Compiler
from strife.presentation.components import ButtonStyle
from strife.presentation.emoji import EmojiResolver
from strife.presentation.feedback import (
    FeedbackAction,
    build_feedback_view,
    send_ephemeral_feedback,
)
from strife.routing import prefixes as P


@dataclass
class SuccessContext:
    interaction: discord.Interaction
    format_kwargs: dict = field(default_factory=dict)


SuccessActionBuilder = Callable[["UserSuccessPresenter", SuccessContext], list[FeedbackAction]]


def _detail_section(code: str) -> str:
    section, _, _ = code.partition(".")
    return f"{section}_success"


def _detail_name(code: str) -> str:
    _, _, name = code.partition(".")
    return name


class UserSuccessPresenter:
    def __init__(
        self,
        compiler: Compiler,
        emoji: EmojiResolver,
        text: TextConfig,
    ) -> None:
        self.compiler = compiler
        self.emoji = emoji
        self.text = text

    def _browse_games(self, ctx: SuccessContext) -> FeedbackAction:
        return FeedbackAction(
            label_key="errors.browse_games",
            style=ButtonStyle.PRIMARY,
            emoji="game",
            route_prefix=P.CAT_NAV,
            resource_id=ctx.interaction.user.id,
            source="catalog",
            payload={"page": 0},
        )

    def _build_actions(self, code: str, ctx: SuccessContext) -> list[FeedbackAction]:
        builders: dict[str, SuccessActionBuilder] = {
            "lobby.left": lambda p, c: [p._browse_games(c)],
            "lobby.closed": lambda p, c: [p._browse_games(c)],
            "match.forfeited": lambda p, c: [p._browse_games(c)],
        }
        builder = builders.get(code)
        if builder is None:
            return []
        return builder(self, ctx)

    def _title(self, code: str, ctx: SuccessContext) -> str:
        return self.text.get(code, **ctx.format_kwargs)

    def _detail(self, code: str, ctx: SuccessContext) -> str | None:
        detail_key = f"{_detail_section(code)}.{_detail_name(code)}"
        detail = self.text.get(detail_key)
        if detail == detail_key:
            return None
        kwargs = dict(ctx.format_kwargs)
        if kwargs:
            try:
                return detail.format(**kwargs)
            except (KeyError, ValueError):
                pass
        return detail

    def _compile_prefix(self, code: str, ctx: SuccessContext) -> str:
        actions = self._build_actions(code, ctx)
        for action in actions:
            if action.route_prefix:
                return action.route_prefix
        return P.REPLAY_NOOP

    async def send(
        self,
        interaction: discord.Interaction,
        code: str,
        *,
        context: SuccessContext | None = None,
        format_kwargs: dict | None = None,
    ) -> None:
        ctx = context or SuccessContext(interaction=interaction)
        if format_kwargs:
            ctx.format_kwargs = format_kwargs

        title = self._title(code, ctx)
        detail = self._detail(code, ctx)
        actions = self._build_actions(code, ctx)
        view = build_feedback_view(
            icon=self.emoji.get("success"),
            title=title,
            body=detail,
            body_heading=self.text.get("common.success_next"),
            actions=actions or None,
            text=self.text,
        )
        await send_ephemeral_feedback(
            interaction,
            view,
            compiler=self.compiler,
            prefix=self._compile_prefix(code, ctx),
            resource_id=interaction.user.id,
        )
