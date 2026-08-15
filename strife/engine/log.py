from __future__ import annotations

from enum import StrEnum
from typing import Any


class LogEntryKind(StrEnum):
    """Classification for match log entries."""

    GAME = "game"
    SYSTEM = "system"


SYSTEM_SOURCES = frozenset({"forfeit", "game_end", "bot_takeover"})


def infer_log_kind(source: str, arguments: dict[str, Any]) -> LogEntryKind:
    """Infer kind for legacy rows missing an explicit ``kind`` column."""
    if source in SYSTEM_SOURCES:
        return LogEntryKind.SYSTEM
    return LogEntryKind.GAME
