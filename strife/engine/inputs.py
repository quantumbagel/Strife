from __future__ import annotations

from strife.engine.players import Move
from strife.engine.requests import BotRequest
from strife.presentation.components import LayoutView, move_sources, query_sources


class InvalidBotMove(RuntimeError):
    """Bot returned a move that does not match the request."""


def resolve_sources(
    view: LayoutView,
    sources: set[str] | None,
    *,
    per_seat_sources: dict[int, set[str]] | None = None,
    seat: int | None = None,
) -> set[str] | None:
    queries = query_sources(view)
    if per_seat_sources is not None and seat is not None:
        base = per_seat_sources.get(seat, sources)
    else:
        base = sources
    if base is None:
        allowed = move_sources(view)
    else:
        allowed = set(base)
    return allowed - queries if allowed else allowed


def make_bot_request(
    players,
    view: LayoutView,
    seat: int,
    *,
    sources: set[str] | None,
    per_seat_sources: dict[int, set[str]] | None = None,
    description: str | None = None,
) -> BotRequest:
    allowed = resolve_sources(
        view, sources, per_seat_sources=per_seat_sources, seat=seat
    )
    resolved = frozenset(allowed) if allowed is not None else None
    difficulty = players[seat].bot_difficulty or "medium"
    return BotRequest(
        seat=seat,
        difficulty=difficulty,
        sources=resolved,
        description=description,
    )


def validate_bot_move(request: BotRequest, move: Move) -> None:
    if move.actor_seat != request.seat:
        raise InvalidBotMove("Bot returned a move for the wrong seat")
    if request.sources is not None and move.source not in request.sources:
        raise InvalidBotMove("Bot returned a move with a disallowed source")
