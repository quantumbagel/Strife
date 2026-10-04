from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from strife.engine.log import SYSTEM_SOURCES, LogEntryKind
from strife.engine.players import Move
from strife.engine.requests import BotRequest, FormSpec, SeatPrompt, TimeoutConsequence
from strife.presentation.components import (
    LayoutView,
    default_form_values,
    form_fields,
    move_sources,
    query_sources,
)


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
    allowed = resolve_sources(view, sources, per_seat=per_seat, seat=seat)
    resolved = frozenset(allowed) if allowed is not None else None
    difficulty = players[seat].bot_difficulty or "medium"
    fields = form_fields(view)
    form = {name: field.choices for name, field in fields.items()}
    specs = {
        name: FormSpec(
            choices=field.choices,
            multi=field.multi,
            min_values=field.min_values,
            max_values=field.max_values,
            default=field.default,
        )
        for name, field in fields.items()
    }
    return BotRequest(
        seat=seat,
        difficulty=difficulty,
        sources=resolved,
        description=resolve_description(description, per_seat=per_seat, seat=seat),
        form=form,
        form_fields=specs,
    )


def validate_form_args(form: Mapping[str, tuple[str, ...]], args: dict) -> None:
    for key, value in args.items():
        if key not in form:
            continue
        choices = form[key]
        items = value if isinstance(value, (list, tuple)) else (value,)
        for item in items:
            if str(item) not in choices:
                raise InvalidBotMove(f"Invalid form value {item!r} for {key!r}")


def _form_value_shape(value, *, multi: bool):
    if multi:
        items = list(value) if isinstance(value, (list, tuple)) else [value]
        return [item if isinstance(item, str) else str(item) for item in items]
    if isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return value


def apply_bot_form(request: BotRequest, move: Move) -> Move:
    """Merge omitted form defaults (same as a human submit) and check types/cardinality.

    Returns a new ``Move``. The session/host must call this before logging the
    bot move; ``validate_bot_move`` does not merge args.
    """
    specs = request.form_fields
    if not specs:
        return move
    merged = {**default_form_values(specs), **dict(move.args)}
    for name, spec in specs.items():
        if name not in merged:
            # Like a human submit: an untouched select only matters to moves that read it.
            continue
        # Coerce to the shape a human submit carries (str / list[str]); 3.0 bots
        # may return ints or a bare value for a multi-select.
        value = _form_value_shape(merged[name], multi=spec.multi)
        merged[name] = value
        if spec.multi:
            if not isinstance(value, list) or not all(
                isinstance(item, str) for item in value
            ):
                raise InvalidBotMove(f"Form field {name!r} must be list[str]")
            if not (spec.min_values <= len(value) <= spec.max_values):
                raise InvalidBotMove(
                    f"Form field {name!r} must have between "
                    f"{spec.min_values} and {spec.max_values} values"
                )
            for item in value:
                if item not in spec.choices:
                    raise InvalidBotMove(f"Invalid form value {item!r} for {name!r}")
        else:
            if not isinstance(value, str):
                raise InvalidBotMove(f"Form field {name!r} must be str")
            if spec.min_values > 1:
                raise InvalidBotMove(
                    f"Form field {name!r} must have at least {spec.min_values} values"
                )
            if value not in spec.choices:
                raise InvalidBotMove(f"Invalid form value {value!r} for {name!r}")
    return replace(move, args=merged)


def validate_bot_move(request: BotRequest, move: Move) -> None:
    if move.actor_seat != request.seat:
        raise InvalidBotMove("Bot returned a move for the wrong seat")
    if move.kind != LogEntryKind.GAME:
        raise InvalidBotMove("Bot returned a non-game move")
    if move.source in SYSTEM_SOURCES:
        raise InvalidBotMove("Bot returned a host/system source")
    if request.sources is not None and move.source not in request.sources:
        raise InvalidBotMove("Bot returned a move with a disallowed source")
    if request.form:
        validate_form_args(request.form, move.args)


def timeout_auto_passes(
    consequence: TimeoutConsequence | None,
    allowed_sources: set[str] | None,
) -> bool:
    """True when AUTO_PASS should inject ``pass`` for this seat's resolved sources."""
    if consequence != TimeoutConsequence.AUTO_PASS:
        return False
    if allowed_sources is None:
        return True
    return "pass" in allowed_sources


def check_timeout_consequence(
    consequence: TimeoutConsequence | None,
    allowed_sources: set[str] | None,
    *more_allowed: set[str] | None,
) -> None:
    """Raise if AUTO_PASS is set and no provided seat has ``pass``.

    Pass every seat's resolved sources in one call. Seats without ``pass``
    still get a normal system timeout; use ``timeout_auto_passes`` per seat.
    """
    if consequence != TimeoutConsequence.AUTO_PASS:
        return
    if any(
        timeout_auto_passes(consequence, sources)
        for sources in (allowed_sources, *more_allowed)
    ):
        return
    raise ValueError("timeout_consequence AUTO_PASS requires 'pass' in allowed sources")
