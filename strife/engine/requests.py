from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


@dataclass(frozen=True)
class SeatPrompt:
    """Per-seat overrides for ``request_inputs``. ``None`` fields use the request-wide value."""

    sources: frozenset[str] | set[str] | None = None
    description: str | None = None


@dataclass(frozen=True)
class FormSpec:
    """Bot-facing form field: legal values plus cardinality. ``BotRequest.form`` stays choices-only."""

    choices: tuple[str, ...]
    multi: bool
    min_values: int
    max_values: int
    default: str | tuple[str, ...] | None = None


@dataclass(frozen=True)
class BotRequest:
    seat: int
    difficulty: str
    sources: frozenset[str] | None  # resolved allowed sources; None = any
    description: str | None = None  # description from request_input(s)
    form: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    form_fields: Mapping[str, FormSpec] = field(default_factory=dict)


class TimeoutConsequence(StrEnum):
    ABANDON = "abandon"
    SKIP = "skip"
    AUTO_PASS = "auto_pass"
    GAME_ENDS = "game_ends"
    STRIKE = "strike"
