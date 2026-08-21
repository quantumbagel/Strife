from __future__ import annotations

import asyncio
from dataclasses import dataclass

from strife.engine.game import Game
from strife.engine.players import Move
from strife.logging import get_logger

log = get_logger("session")

BOT_MOVE_TIMEOUT_SECONDS = 10.0
QUERY_TIMEOUT_SECONDS = 5.0


def guard_bot_move(game: Game) -> None:
    """Wrap ``game.bot_move`` so every call (including from play()) is time-boxed."""
    original = game.bot_move

    async def guarded(difficulty: str, seat: int) -> Move:
        try:
            return await asyncio.wait_for(
                original(difficulty, seat),
                timeout=BOT_MOVE_TIMEOUT_SECONDS,
            )
        except Exception as e:
            log.exception("Bot move crashed or timed out for seat %s", seat)
            raise RuntimeError(f"Bot failed to make a move: {e}") from e

    game.bot_move = guarded  # type: ignore[method-assign]


@dataclass
class PendingInput:
    allowed_actors: set[int]
    allowed_sources: set[str] | None
    future: asyncio.Future[Move]
    description: str | None = None
    line_description: str | None = None
    timeout_seconds: float | None = None
    timeout_consequence: str | None = None
