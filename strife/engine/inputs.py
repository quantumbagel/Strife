from __future__ import annotations

from collections.abc import Mapping

from strife.engine.players import Move
from strife.engine.requests import BotRequest, SeatPrompt, TimeoutConsequence
from strife.presentation.components import LayoutView, form_fields, move_sources, query_sources


class InvalidBotMove(RuntimeError):
    """Bot returned a move that does not match the request."""


def _prompt(
    per_seat: Mapping[int, SeatPrompt] | None,
    seat: int | None,
) -> SeatPrompt | None:
    if per_seat is None or seat is None:
        return None
    return per_seat.get(seat)


def resolve_description(
    description: str | None,
    *,
    per_seat: Mapping[int, SeatPrompt] | None = None,
    seat: int | None = None,
) -> str | None:
    prompt = _prompt(per_seat, seat)
    if prompt is not None and prompt.description is not None:
        return prompt.description
    return description


def resolve_sources(
    view: LayoutView,
    sources: set[str] | None,
    *,
    per_seat: Mapping[int, SeatPrompt] | None = None,
    seat: int | None = None,
) -> set[str] | None:
    queries = query_sources(view)
    forms = set(form_fields(view))
    prompt = _prompt(per_seat, seat)
    if prompt is not None and prompt.sources is not None:
        base = set(prompt.sources)
    else:
        base = sources
    if base is None:
        allowed = move_sources(view)
    else:
        allowed = set(base)
    if not allowed:
        return allowed
    return allowed - queries - forms


def make_bot_request(
    players,
    view: LayoutView,
    seat: int,
    *,
    sources: set[str] | None,
    per_seat: Mapping[int, SeatPrompt] | None = None,
    description: str | None = None,
) -> BotRequest:
    allowed = resolve_sources(
        view, sources, per_seat=per_seat, seat=seat
    )
    resolved = frozenset(allowed) if allowed is not None else None
    difficulty = players[seat].bot_difficulty or "medium"
    form = {name: field.choices for name, field in form_fields(view).items()}
    return BotRequest(
        seat=seat,
        difficulty=difficulty,
        sources=resolved,
        description=resolve_description(description, per_seat=per_seat, seat=seat),
        form=form,
    )


def validate_form_args(form: Mapping[str, tuple[str, ...]], args: dict) -> None:
    for key, value in args.items():
        if key not in form:
            continue
        choices = form[key]
        items = value if isinstance(value, (list, tuple)) else (value,)
        for item in items:
            if str(item) not in choices:
                raise InvalidBotMove(
                    f"Invalid form value {item!r} for {key!r}"
                )


def validate_bot_move(request: BotRequest, move: Move) -> None:
    if move.actor_seat != request.seat:
        raise InvalidBotMove("Bot returned a move for the wrong seat")
    if request.sources is not None and move.source not in request.sources:
        raise InvalidBotMove("Bot returned a move with a disallowed source")
    if request.form:
        validate_form_args(request.form, move.args)


def check_timeout_consequence(
    consequence: TimeoutConsequence | None,
    allowed_sources: set[str] | None,
) -> None:
    if consequence != TimeoutConsequence.AUTO_PASS:
        return
    if allowed_sources is not None and "pass" not in allowed_sources:
        raise ValueError(
            "timeout_consequence AUTO_PASS requires 'pass' in allowed sources"
        )
