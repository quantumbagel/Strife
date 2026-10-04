from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from strife.engine.game import Game
from strife.engine.inputs import InvalidBotMove, apply_bot_form, validate_bot_move
from strife.engine.players import Move
from strife.engine.requests import BotRequest, TimeoutConsequence
from strife.logging import get_logger
from strife.presentation.components import FormField

log = get_logger("session")

BOT_MOVE_TIMEOUT_SECONDS = 10.0
QUERY_TIMEOUT_SECONDS = 5.0


def guard_bot_move(game: Game) -> None:
    """Wrap ``game.bot_move`` so every call (including from play()) is time-boxed."""
    original = game.bot_move

    async def guarded(request: BotRequest) -> Move:
        try:
            move = await asyncio.wait_for(
                original(request),
                timeout=BOT_MOVE_TIMEOUT_SECONDS,
            )
        except Exception as e:
            log.exception("Bot move crashed or timed out for seat %s", request.seat)
            raise RuntimeError(f"Bot failed to make a move: {e}") from e
        try:
            move = apply_bot_form(request, move)
            validate_bot_move(request, move)
        except InvalidBotMove as e:
            log.error("Bot move validation failed for seat %s: %s", request.seat, e)
            raise RuntimeError(str(e)) from e
        return move

    game.bot_move = guarded  # type: ignore[method-assign]


@dataclass
class PendingInput:
    allowed_actors: set[int]
    allowed_sources: set[str] | None
    future: asyncio.Future[Move]
    description: str | None = None
    line_description: str | None = None
    timeout_seconds: float | None = None
    timeout_consequence: TimeoutConsequence | None = None
    deadline_at: float | None = None
    timeout_generation: int = 0
    until: str = "all"
    # Shared by every seat of one until="any" request; resolving it closes the
    # window with no move (see ``SessionInputMixin.expire_phase``).
    phase_timeout: asyncio.Future[None] | None = None
    form: dict[str, FormField] = field(default_factory=dict)
    form_values: dict[str, Any] = field(default_factory=dict)
