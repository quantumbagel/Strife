from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from strife.engine.players import Move, Player
from strife.persistence.repositories import MoveRecord
from strife.presentation.components import LayoutView
from strife.presentation.compiler import clone_and_disable


class GameContext(Protocol):
    rng: random.Random
    players: Sequence[Player]
    settings: Mapping[str, object]

    def is_bot(self, seat: int) -> bool: ...

    async def update(self, view: LayoutView) -> None: ...

    async def request_input(
        self, view: LayoutView, *, actor: int, sources: set[str] | None = None
    ) -> Move: ...

    async def request_inputs(
        self,
        view: LayoutView,
        *,
        actors: set[int],
        sources: set[str] | None = None,
        until: Literal["all", "any"] = "all",
    ) -> dict[int, Move]: ...

    async def send_private(self, seat: int, view: LayoutView) -> None: ...


class LiveContext:
    def __init__(self, session: object) -> None:
        self._session = session

    @property
    def rng(self) -> random.Random:
        return self._session.game.rng  # type: ignore[attr-defined]

    @property
    def players(self) -> Sequence[Player]:
        return self._session.players  # type: ignore[attr-defined]

    @property
    def settings(self) -> Mapping[str, object]:
        return self._session.settings  # type: ignore[attr-defined]

    def is_bot(self, seat: int) -> bool:
        return self._session.players[seat].is_bot  # type: ignore[attr-defined]

    async def update(self, view: LayoutView) -> None:
        await self._session.surface.update(view)  # type: ignore[attr-defined]

    async def request_input(
        self, view: LayoutView, *, actor: int, sources: set[str] | None = None
    ) -> Move:
        return await self._session._request_input(view, actor=actor, sources=sources)  # type: ignore[attr-defined]

    async def request_inputs(
        self,
        view: LayoutView,
        *,
        actors: set[int],
        sources: set[str] | None = None,
        until: Literal["all", "any"] = "all",
    ) -> dict[int, Move]:
        return await self._session._request_inputs(  # type: ignore[attr-defined]
            view, actors=actors, sources=sources, until=until
        )

    async def send_private(self, seat: int, view: LayoutView) -> None:
        await self._session._send_private(seat, view)  # type: ignore[attr-defined]


@dataclass
class ReplayFrame:
    index: int
    turn_label: str
    actor_seat: int | None
    view: LayoutView


class ReplayContext:
    def __init__(
        self,
        *,
        rng: random.Random,
        players: Sequence[Player],
        settings: Mapping[str, object],
        moves: list[MoveRecord],
    ) -> None:
        self.rng = rng
        self.players = players
        self.settings = settings
        self._moves = list(moves)
        self._cursor = 0
        self.frames: list[ReplayFrame] = []
        self._turn = 0

    def is_bot(self, seat: int) -> bool:
        return self.players[seat].is_bot

    async def update(self, view: LayoutView) -> None:
        self._capture(view)

    async def request_input(
        self, view: LayoutView, *, actor: int, sources: set[str] | None = None
    ) -> Move:
        self._capture(view, actor_seat=actor)
        return self._next_move(actor)

    async def request_inputs(
        self,
        view: LayoutView,
        *,
        actors: set[int],
        sources: set[str] | None = None,
        until: Literal["all", "any"] = "all",
    ) -> dict[int, Move]:
        self._capture(view)
        moves: dict[int, Move] = {}
        for seat in sorted(actors):
            moves[seat] = self._next_move(seat)
        return moves

    async def send_private(self, seat: int, view: LayoutView) -> None:
        self._capture(view, actor_seat=seat)

    def _capture(self, view: LayoutView, *, actor_seat: int | None = None) -> None:
        cloned = clone_and_disable(view)
        self.frames.append(
            ReplayFrame(
                index=len(self.frames),
                turn_label=f"Turn {self._turn}",
                actor_seat=actor_seat,
                view=cloned,
            )
        )

    def _next_move(self, actor: int) -> Move:
        if self._cursor >= len(self._moves):
            raise RuntimeError("Replay move underflow")
        record = self._moves[self._cursor]
        self._cursor += 1
        self._turn += 1
        return Move(actor_seat=record.actor_seat if record.actor_seat is not None else actor,
                    source=record.source, args=record.arguments)
