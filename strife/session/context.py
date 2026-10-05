from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from weakref import WeakKeyDictionary

from strife.engine.log_cursor import LogCursor, LogEnded
from strife.engine.players import Move
from strife.engine.requests import SeatPrompt, TimeoutConsequence
from strife.engine.seat_events import apply_seat_event, is_seat_event
from strife.presentation.components import LayoutView
from strife.presentation.emoji import EmojiResolver

if TYPE_CHECKING:
    from strife.session.game_session import GameSession

_HOSTS: WeakKeyDictionary = WeakKeyDictionary()
_QUERY_INTERACTION: ContextVar[object | None] = ContextVar(
    "strife_query_interaction", default=None
)


class _UnarmedDeadline:
    """Turn-deadline budget stored during catch-up; armed in ``_leave_catchup``."""

    __slots__ = ("budget",)

    def __init__(self, budget: float | None) -> None:
        self.budget = budget


class LiveContext:
    """``GameContext`` implementation backed by a live ``GameSession``.

    The host object is not exposed as ``_session`` so game code cannot reach
    Discord, persistence, or occupancy through the context.
    """

    def __init__(self, session: GameSession) -> None:
        _HOSTS[self] = session
        self._query_interaction: object | None = None
        self._applied = 0
        self._turn_deadlines: list[float | None | _UnarmedDeadline] = []
        self._catching_up = False
        self._first_live_prompt = False
        self._log: LogCursor | None = None
        self._deferred_board: LayoutView | None = None
        self._deferred_dms: dict[int, LayoutView] = {}

    def _host(self) -> GameSession:
        return _HOSTS[self]

    def _touch(self) -> None:
        self._host().mark_progress()

    def begin_catchup(self, moves: Sequence[Move]) -> None:
        """Answer play() from stored rows until they run out, then go live."""
        self._catching_up = True
        self._applied = len(moves)
        self._log = LogCursor(moves, self._host().game)
        self._deferred_board = None
        self._deferred_dms = {}

    def _arm_turn_deadlines(self) -> None:
        now = time.monotonic()
        for i, entry in enumerate(self._turn_deadlines):
            if isinstance(entry, _UnarmedDeadline):
                budget = entry.budget
                self._turn_deadlines[i] = now + budget if budget is not None else None

    def _drop_deferred_dm(self, seat: int | None) -> None:
        if seat is not None:
            self._deferred_dms.pop(int(seat), None)

    async def _leave_catchup(
        self,
        *,
        flush_view: bool = True,
        waiting: set[int] | None = None,
    ) -> None:
        if not self._catching_up:
            return
        self._catching_up = False
        self._log = None
        self._host()._resuming = False
        host = self._host()
        if flush_view and self._deferred_board is not None:
            await host._update_surface(self._deferred_board)
        self._deferred_board = None
        await host.refresh_header()
        if waiting is not None:
            await self._flush_deferred_dms(waiting)
        self._arm_turn_deadlines()
        self._first_live_prompt = True

    async def _flush_deferred_dms(self, waiting: set[int]) -> None:
        """Re-send DMs skipped during catch-up to the seats a live request now waits on.

        Kept until the first live request when catch-up ran out on a non-request row.
        """
        if not self._deferred_dms:
            return
        deferred, self._deferred_dms = self._deferred_dms, {}
        for seat in waiting:
            view = deferred.get(seat)
            if view is not None:
                await self._host()._send_private(seat, view)

    async def _after_catchup_consume(self) -> None:
        if self._log is not None and self._log.at_end():
            await self._leave_catchup()

    def _begin_query(self, interaction: object) -> None:
        _QUERY_INTERACTION.set(interaction)

    def _end_query(self) -> None:
        _QUERY_INTERACTION.set(None)

    @property
    def started_at(self) -> datetime | None:
        return self._host().started_at

    @property
    def is_replay(self) -> bool:
        return False

    @property
    def emoji(self) -> EmojiResolver:
        return self._host().surface.compiler.emoji

    def is_bot(self, seat: int) -> bool:
        return self._host().game.players[seat].is_bot

    def _sync(
        self, through: int | None, waiting: set[int] | None = None
    ) -> Move | None:
        entries = self._host().log.entries
        waiting = waiting or set()
        while self._applied < len(entries):
            move = entries[self._applied]
            if through is not None and move.turn_index > through:
                break
            if (
                is_seat_event(move)
                and move.source == "forfeit"
                and move.actor_seat is not None
                and int(move.actor_seat) in waiting
            ):
                apply_seat_event(self._host().game, move)
                self._applied += 1
                return move
            if is_seat_event(move):
                apply_seat_event(self._host().game, move)
            self._applied += 1
        return None

    def _bound_timeout(self, timeout_seconds: float | None) -> float | None:
        deadline = self._turn_deadlines[-1] if self._turn_deadlines else None
        if isinstance(deadline, _UnarmedDeadline):
            deadline = None
        if deadline is None:
            return timeout_seconds
        remaining = deadline - time.monotonic()
        if timeout_seconds is None:
            return remaining
        return min(float(timeout_seconds), remaining)

    @asynccontextmanager
    async def turn_deadline(self, seconds: float | None = None):
        budget = self._host().turn_timeout_seconds if seconds is None else seconds
        budget_f = float(budget) if budget is not None else None
        if self._catching_up:
            self._turn_deadlines.append(_UnarmedDeadline(budget_f))
        else:
            token = time.monotonic() + budget_f if budget_f is not None else None
            self._turn_deadlines.append(token)
        try:
            yield
        finally:
            self._turn_deadlines.pop()

    @property
    def turn_timeout_seconds(self) -> float | None:
        return float(self._host().turn_timeout_seconds)

    async def update(self, view: LayoutView) -> None:
        self._touch()
        if self._catching_up:
            self._deferred_board = view
            return
        await self._host()._update_surface(view)

    async def request_input(
        self,
        view: LayoutView,
        *,
        actor: int,
        sources: set[str] | None = None,
        description: str | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: TimeoutConsequence | None = None,
    ) -> Move:
        self._touch()
        if self._catching_up:
            assert self._log is not None
            try:
                move = self._log.take_input(actor)
                self._drop_deferred_dm(move.actor_seat)
                await self._after_catchup_consume()
                return move
            except LogEnded as exc:
                if exc.match_ended:
                    raise
                await self._leave_catchup(flush_view=False, waiting={actor})
        logged = self._sync(None, waiting={actor})
        if logged is not None:
            return logged
        await self._flush_deferred_dms({actor})
        move = await self._host()._request_input(
            view,
            actor=actor,
            sources=sources,
            description=description,
            timeout_seconds=self._bound_timeout(timeout_seconds),
            timeout_consequence=timeout_consequence,
        )
        self._touch()
        self._sync(move.turn_index)
        return move

    async def request_inputs(
        self,
        view: LayoutView,
        *,
        actors: set[int],
        sources: set[str] | None = None,
        until: Literal["all", "any"] = "all",
        per_seat: Mapping[int, SeatPrompt] | None = None,
        description: str | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: TimeoutConsequence | None = None,
    ) -> dict[int, Move]:
        self._touch()
        if until == "any":
            if self._catching_up:
                assert self._log is not None
                try:
                    moves = self._log.take_inputs_any(actors)
                    for seat in moves:
                        self._drop_deferred_dm(seat)
                    await self._after_catchup_consume()
                    return moves
                except LogEnded as exc:
                    if exc.match_ended:
                        raise
                    await self._leave_catchup(flush_view=False, waiting=actors)
            logged = self._sync(None, waiting=actors)
            if logged is not None:
                return {int(logged.actor_seat): logged}
            await self._flush_deferred_dms(actors)
            timeout_seconds = self._bound_timeout(timeout_seconds)
            moves = await self._host()._request_inputs(
                view,
                actors=actors,
                sources=sources,
                until=until,
                per_seat=per_seat,
                description=description,
                timeout_seconds=timeout_seconds,
                timeout_consequence=timeout_consequence,
            )
            self._touch()
            if moves:
                self._sync(max(move.turn_index for move in moves.values()))
            elif self._host()._phase_timeout_index is not None:
                self._sync(self._host()._phase_timeout_index)
            return moves

        results: dict[int, Move] = {}
        remaining = set(actors)
        if self._catching_up:
            assert self._log is not None
            try:
                caught, remaining = self._log.take_inputs_all_partial(actors)
                results.update(caught)
                for seat in caught:
                    self._drop_deferred_dm(seat)
                if remaining:
                    await self._leave_catchup(flush_view=False, waiting=remaining)
                else:
                    await self._after_catchup_consume()
                    self._touch()
                    return results
            except LogEnded as exc:
                if exc.match_ended:
                    raise
                await self._leave_catchup(flush_view=False, waiting=remaining)
        while remaining:
            logged = self._sync(None, waiting=remaining)
            if logged is None:
                break
            results[int(logged.actor_seat)] = logged
            remaining.discard(int(logged.actor_seat))
        if remaining:
            await self._flush_deferred_dms(remaining)
            timeout_seconds = self._bound_timeout(timeout_seconds)
            moves = await self._host()._request_inputs(
                view,
                actors=remaining,
                sources=sources,
                until=until,
                per_seat=per_seat,
                description=description,
                timeout_seconds=timeout_seconds,
                timeout_consequence=timeout_consequence,
            )
            results.update(moves)
        self._touch()
        if results:
            self._sync(max(move.turn_index for move in results.values()))
        elif self._host()._phase_timeout_index is not None:
            self._sync(self._host()._phase_timeout_index)
        # Log order, the same order catch-up and replay build this dict in.
        return dict(sorted(results.items(), key=lambda item: item[1].turn_index))

    async def send_private(self, seat: int, view: LayoutView) -> None:
        self._touch()
        if self._catching_up:
            self._deferred_dms[seat] = view
            return
        await self._host()._send_private(seat, view)

    async def record_event(self, source: str, arguments: dict[str, Any]) -> None:
        self._touch()
        if self._catching_up:
            assert self._log is not None
            try:
                self._log.take_event(source, arguments)
                await self._after_catchup_consume()
                return
            except LogEnded as exc:
                if exc.match_ended:
                    raise
                await self._leave_catchup()
        self._sync(None)
        self._host().log.event(source, arguments)

    async def respond_query(self, view: LayoutView) -> None:
        self._touch()
        interaction = _QUERY_INTERACTION.get()
        if interaction is None:
            raise RuntimeError("respond_query() is only valid inside handle_query")
        await self._host()._respond_query(interaction, view)
