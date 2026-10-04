from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone

from strife.engine.log import SYSTEM_SOURCES, LogEntryKind, reject_system_source
from strife.engine.players import Move


class MatchLog:
    def __init__(self, on_append: Callable[[Move], None] | None = None) -> None:
        self.entries: list[Move] = []
        self._turn_index = 0
        self._on_append = on_append

    def _append(
        self,
        actor_seat: int | None,
        source: str,
        args: dict,
        kind: LogEntryKind,
        created_at: datetime | None = None,
    ) -> Move:
        stamped = created_at or datetime.now(timezone.utc)
        logged = Move(
            actor_seat=actor_seat,
            source=source,
            args=args,
            kind=kind,
            turn_index=self._turn_index,
            created_at=stamped,
        )
        self.entries.append(logged)
        self._turn_index += 1
        if self._on_append is not None:
            self._on_append(logged)
        return logged

    def preload(self, entries: Sequence[Move]) -> None:
        """Load stored rows without firing ``on_append`` (they are already persisted)."""
        self.entries = list(entries)
        if self.entries:
            self._turn_index = max(move.turn_index for move in self.entries) + 1
        else:
            self._turn_index = 0

    def record(self, move: Move, *, kind: LogEntryKind | None = None) -> Move:
        entry_kind = kind or (
            LogEntryKind.SYSTEM
            if move.kind == LogEntryKind.SYSTEM or move.source in SYSTEM_SOURCES
            else LogEntryKind.GAME
        )
        stamped = move.created_at or datetime.now(timezone.utc)
        if move.created_at is None:
            move.created_at = stamped
        return self._append(
            move.actor_seat,
            move.source,
            move.args,
            entry_kind,
            created_at=stamped,
        )

    def event(
        self,
        source: str,
        args: dict,
        *,
        actor_seat: int | None = None,
    ) -> Move:
        reject_system_source(source)
        return self._append(
            actor_seat,
            source,
            args,
            LogEntryKind.GAME,
        )

    def system(
        self,
        source: str,
        args: dict,
        *,
        actor_seat: int | None = None,
    ) -> Move:
        return self._append(
            actor_seat,
            source,
            args,
            LogEntryKind.SYSTEM,
        )
