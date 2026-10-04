from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class BotRequest:
    seat: int
    difficulty: str
    sources: frozenset[str] | None  # resolved allowed sources; None = any
    description: str | None = None  # description from request_input(s)


class TimeoutConsequence(StrEnum):
    ABANDON = "abandon"
    SKIP = "skip"
    AUTO_PASS = "auto_pass"
    GAME_ENDS = "game_ends"
    STRIKE = "strike"
