from __future__ import annotations

from enum import StrEnum
from typing import Any


class LogEntryKind(StrEnum):
    """Classification for match log entries."""

    GAME = "game"
    SYSTEM = "system"


SYSTEM_SOURCES = frozenset({"forfeit", "game_end", "bot_takeover", "timeout"})


def reject_system_source(source: str) -> None:
    """Games may not emit engine-owned log sources via ``record_event``."""
    if source in SYSTEM_SOURCES:
        raise ValueError(
            f"record_event source {source!r} is reserved for the engine; "
            "use a game-specific name"
        )


def infer_log_kind(source: str, arguments: dict[str, Any]) -> LogEntryKind:
    """Infer kind for legacy rows missing an explicit ``kind`` column."""
    if source in SYSTEM_SOURCES:
        return LogEntryKind.SYSTEM
    return LogEntryKind.GAME
