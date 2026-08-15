from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from strife.presentation.emoji import EmojiResolver

_active_emoji: contextvars.ContextVar[EmojiResolver | None] = contextvars.ContextVar(
    "active_emoji_resolver",
    default=None,
)


def active_emoji() -> EmojiResolver | None:
    return _active_emoji.get()


def bind_emoji(resolver: EmojiResolver) -> contextvars.Token:
    return _active_emoji.set(resolver)


def reset_emoji(token: contextvars.Token) -> None:
    _active_emoji.reset(token)
