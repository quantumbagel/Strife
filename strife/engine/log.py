from __future__ import annotations

from enum import StrEnum

LOG_FORMAT = 3


class LogEntryKind(StrEnum):
    """Classification for match log entries."""

    GAME = "game"
    SYSTEM = "system"


SYSTEM_SOURCES = frozenset(
    {"forfeit", "game_end", "bot_takeover", "timeout", "timeout_strike"}
)


def reject_system_source(source: str) -> None:
    """Games may not emit engine-owned log sources via ``record_event``."""
    if source in SYSTEM_SOURCES:
        raise ValueError(
            f"record_event source {source!r} is reserved for the engine; "
            "use a game-specific name"
        )
