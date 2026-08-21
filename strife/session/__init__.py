"""Live Discord host for ``game.play()``.

Game plugins should import from ``strife.engine`` and ``strife.presentation``,
not this package.
"""

from strife.session.context import LiveContext
from strife.session.game_session import GameSession, MatchFinalizer
from strife.session.types import (
    BOT_MOVE_TIMEOUT_SECONDS,
    QUERY_TIMEOUT_SECONDS,
    PendingInput,
)

__all__ = [
    "BOT_MOVE_TIMEOUT_SECONDS",
    "QUERY_TIMEOUT_SECONDS",
    "GameSession",
    "LiveContext",
    "MatchFinalizer",
    "PendingInput",
]
