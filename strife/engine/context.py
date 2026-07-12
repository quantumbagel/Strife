from __future__ import annotations

from datetime import datetime
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from strife.engine.players import Move, Player
from strife.presentation.components import LayoutView
from strife.presentation.emoji import EmojiResolver


class GameContext(Protocol):
    rng: random.Random
    players: Sequence[Player]
    settings: Mapping[str, object]
    emoji: EmojiResolver

    @property
    def started_at(self) -> datetime | None: ...

    @property
    def is_replay(self) -> bool: ...

    def is_bot(self, seat: int) -> bool: ...

    async def update(self, view: LayoutView) -> None: ...

    async def request_input(
        self,
        view: LayoutView,
        *,
        actor: int,
        sources: set[str] | None = None,
        record: bool = True,
        description: str | None = None,
    ) -> Move: ...

    async def request_inputs(
        self,
        view: LayoutView,
        *,
        actors: set[int],
        sources: set[str] | None = None,
        until: Literal["all", "any"] = "all",
        per_seat_sources: dict[int, set[str]] | None = None,
        record: bool = True,
        description: str | None = None,
        descriptions: dict[int, str] | None = None,
    ) -> dict[int, Move]: ...

    async def send_private(self, seat: int, view: LayoutView) -> None: ...

    async def record_event(self, source: str, arguments: dict[str, Any]) -> None: ...


class LiveContext:
    def __init__(self, session: object) -> None:
        self._session = session

    @property
    def started_at(self) -> datetime | None:
        return getattr(self._session, "_started_at", None)  # type: ignore[attr-defined]

    @property
    def is_replay(self) -> bool:
        return False

    @property
    def rng(self) -> random.Random:
        return self._session.game.rng  # type: ignore[attr-defined]

    @property
    def players(self) -> Sequence[Player]:
        return self._session.players  # type: ignore[attr-defined]

    @property
    def settings(self) -> Mapping[str, object]:
        return self._session.settings  # type: ignore[attr-defined]

    @property
    def emoji(self) -> EmojiResolver:
        return self._session.surface.compiler.emoji  # type: ignore[attr-defined]

    def is_bot(self, seat: int) -> bool:
        return self._session.players[seat].is_bot  # type: ignore[attr-defined]

    async def update(self, view: LayoutView) -> None:
        await self._session._update_surface(view)  # type: ignore[attr-defined]

    async def request_input(
        self,
        view: LayoutView,
        *,
        actor: int,
        sources: set[str] | None = None,
        record: bool = True,
        description: str | None = None,
    ) -> Move:
        return await self._session._request_input(  # type: ignore[attr-defined]
            view, actor=actor, sources=sources, record=record, description=description
        )

    async def request_inputs(
        self,
        view: LayoutView,
        *,
        actors: set[int],
        sources: set[str] | None = None,
        until: Literal["all", "any"] = "all",
        per_seat_sources: dict[int, set[str]] | None = None,
        record: bool = True,
        description: str | None = None,
        descriptions: dict[int, str] | None = None,
    ) -> dict[int, Move]:
        return await self._session._request_inputs(  # type: ignore[attr-defined]
            view,
            actors=actors,
            sources=sources,
            until=until,
            per_seat_sources=per_seat_sources,
            record=record,
            description=description,
            descriptions=descriptions,
        )

    async def send_private(self, seat: int, view: LayoutView) -> None:
        await self._session._send_private(seat, view)  # type: ignore[attr-defined]

    async def record_event(self, source: str, arguments: dict[str, Any]) -> None:
        self._session._append_log_entry(source, arguments)  # type: ignore[attr-defined]


@dataclass
class ReplayFrame:
    index: int
    turn_label: str
    actor_seat: int | None
    view: LayoutView
    takeover_info: dict | None = None
    timestamp: datetime | None = None


class ReplayContext:
    def __init__(
        self,
        *,
        rng: random.Random,
        players: Sequence[Player],
        settings: Mapping[str, object],
        emoji: EmojiResolver,
        started_at: datetime | None = None,
    ) -> None:
        self.rng = rng
        self.players = players
        self.settings = settings
        self.emoji = emoji
        self._started_at = started_at

    @property
    def is_replay(self) -> bool:
        return True

    @property
    def started_at(self) -> datetime | None:
        return self._started_at

    def is_bot(self, seat: int) -> bool:
        return self.players[seat].is_bot

    async def update(self, view: LayoutView) -> None:
        raise NotImplementedError("Replays do not support update()")

    async def request_input(
        self, view: LayoutView, *, actor: int, sources: set[str] | None = None
    ) -> Move:
        raise NotImplementedError("Replays do not support request_input()")

    async def request_inputs(
        self,
        view: LayoutView,
        *,
        actors: set[int],
        sources: set[str] | None = None,
        until: Literal["all", "any"] = "all",
    ) -> dict[int, Move]:
        raise NotImplementedError("Replays do not support request_inputs()")

    async def send_private(self, seat: int, view: LayoutView) -> None:
        raise NotImplementedError("Replays do not support send_private()")

    async def record_event(self, source: str, arguments: dict[str, Any]) -> None:
        pass

