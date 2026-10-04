from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from strife.engine.players import Move
from strife.engine.requests import TimeoutConsequence
from strife.presentation.components import LayoutView
from strife.presentation.emoji import EmojiResolver


class GameContext(Protocol):
    emoji: EmojiResolver

    @property
    def started_at(self) -> datetime | None: ...

    @property
    def is_replay(self) -> bool: ...

    @property
    def turn_timeout_seconds(self) -> float | None:
        """Host's configured per-turn budget; None when the host has no clock."""
        ...

    def is_bot(self, seat: int) -> bool: ...

    async def update(self, view: LayoutView) -> None: ...

    async def request_input(
        self,
        view: LayoutView,
        *,
        actor: int,
        sources: set[str] | None = None,
        description: str | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: TimeoutConsequence | None = None,
    ) -> Move: ...

    async def request_inputs(
        self,
        view: LayoutView,
        *,
        actors: set[int],
        sources: set[str] | None = None,
        until: Literal["all", "any"] = "all",
        per_seat_sources: dict[int, set[str]] | None = None,
        description: str | None = None,
        descriptions: dict[int, str] | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: TimeoutConsequence | None = None,
    ) -> dict[int, Move]: ...

    async def send_private(self, seat: int, view: LayoutView) -> None: ...

    async def record_event(self, source: str, arguments: dict[str, Any]) -> None: ...

    async def respond_query(self, view: LayoutView) -> None: ...


@dataclass
class ReplayFrame:
    index: int
    turn_label: str
    actor_seat: int | None
    view: LayoutView
    takeover_info: dict | None = None
    timestamp: datetime | None = None
