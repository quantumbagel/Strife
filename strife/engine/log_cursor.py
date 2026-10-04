from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from strife.engine.game import Game
from strife.engine.players import Move
from strife.engine.seat_events import apply_seat_event, is_seat_event


class ReplayDivergence(RuntimeError):
    """log and the re-run play() disagree"""


class LogEnded(Exception):
    """Stored log exhausted, or a match-ending row was consumed."""

    def __init__(self, *, match_ended: bool = False) -> None:
        self.match_ended = match_ended
        super().__init__("match ended" if match_ended else "log exhausted")


def is_match_ending(move: Move) -> bool:
    """True for a system row that means the match is over (replay/resume stop)."""
    if not move.is_system:
        return False
    if move.source == "game_end":
        return True
    return move.source == "forfeit" and not move.args.get("removed")


def log_ends_match(moves: Sequence[Move]) -> bool:
    if not moves:
        return False
    return is_match_ending(moves[-1])


def _canonical_json(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True))


def args_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    try:
        return _canonical_json(a) == _canonical_json(b)
    except (TypeError, ValueError):
        # Non-JSON args can never match a stored row; report it as a divergence.
        return False


class LogCursor:
    """Walk a stored match log the way replay and live catch-up both need.

    ``consume`` / ``take_*`` raise ``LogEnded`` when the rows run out or a
    match-ending system row is hit, and ``ReplayDivergence`` when play() and
    the log disagree.
    """

    def __init__(
        self,
        moves: Sequence[Move],
        game: Game,
        *,
        on_metadata: Callable[[Move], None] | None = None,
    ) -> None:
        self._game = game
        self._moves = sorted(moves, key=lambda m: m.turn_index)
        self._cursor = 0
        self._on_metadata = on_metadata

    @property
    def position(self) -> int:
        return self._cursor

    @property
    def total(self) -> int:
        return len(self._moves)

    def at_end(self) -> bool:
        return self._cursor >= len(self._moves)

    def peek(self) -> Move | None:
        if self.at_end():
            return None
        return self._moves[self._cursor]

    def diverge(self, message: str) -> None:
        raise ReplayDivergence(f"at log index {self._cursor}: {message}")

    def consume(self) -> Move:
        if self.at_end():
            raise LogEnded()
        move = self._moves[self._cursor]
        self._cursor += 1
        if move.is_system and move.source == "game_end":
            raise LogEnded(match_ended=True)
        return move

    def apply_metadata(self, move: Move, waiting: set[int]) -> bool:
        """Apply one metadata row. Returns True if consumed."""
        if not is_seat_event(move):
            return False
        if (
            move.source == "forfeit"
            and move.actor_seat is not None
            and int(move.actor_seat) in waiting
        ):
            return False
        self._apply_seat_event(move)
        return True

    def _apply_seat_event(self, move: Move) -> None:
        apply_seat_event(self._game, move)
        if self._on_metadata is not None:
            self._on_metadata(move)

    def consume_metadata(self, waiting: set[int]) -> None:
        while not self.at_end():
            move = self.peek()
            assert move is not None
            if is_match_ending(move):
                answers_waiting = (
                    move.source == "forfeit"
                    and move.actor_seat is not None
                    and int(move.actor_seat) in waiting
                )
                if answers_waiting:
                    break
                self.consume()
                raise LogEnded(match_ended=True)
            if self.apply_metadata(move, waiting):
                self.consume()
                continue
            break

    def matches_input(self, move: Move, actor: int) -> bool:
        if move.is_system and move.source in ("forfeit", "timeout"):
            return move.actor_seat == actor
        if not move.is_game:
            return False
        return move.actor_seat == actor

    def consume_answer_row(self, seat: int) -> Move:
        move = self.consume()
        if is_seat_event(move):
            self._apply_seat_event(move)
        return move

    def take_input(self, actor: int) -> Move:
        self.consume_metadata({actor})
        if self.at_end():
            raise LogEnded()
        move = self.peek()
        assert move is not None
        if not self.matches_input(move, actor):
            self.diverge(
                f"expected input for seat {actor}, got {move.source!r} "
                f"(actor_seat={move.actor_seat})"
            )
        return self.consume_answer_row(actor)

    def take_inputs_any(self, actors: set[int]) -> dict[int, Move]:
        self.consume_metadata(actors)
        if self.at_end():
            raise LogEnded()
        move = self.peek()
        assert move is not None
        if move.is_system and move.source == "timeout" and move.actor_seat is None:
            if move.args.get("until") == "any":
                self.consume()
                return {}
        for seat in actors:
            if self.matches_input(move, seat):
                return {seat: self.consume_answer_row(seat)}
        self.diverge(f"expected until=any input for one of {actors}")
        raise ReplayDivergence("unreachable")

    def take_inputs_all(self, actors: set[int]) -> dict[int, Move]:
        results, remaining = self.take_inputs_all_partial(actors)
        if remaining:
            raise LogEnded()
        return results

    def take_inputs_all_partial(
        self, actors: set[int]
    ) -> tuple[dict[int, Move], set[int]]:
        results: dict[int, Move] = {}
        remaining = set(actors)
        while remaining:
            self.consume_metadata(remaining)
            if self.at_end():
                return results, remaining
            peek = self.peek()
            assert peek is not None
            matched: int | None = None
            for seat in remaining:
                if self.matches_input(peek, seat):
                    matched = seat
                    break
            if matched is None:
                self.diverge(
                    f"expected input for one of {remaining}, got {peek.source!r}"
                )
                raise ReplayDivergence("unreachable")
            results[matched] = self.consume_answer_row(matched)
            remaining.discard(matched)
        return results, remaining

    def take_event(self, source: str, arguments: dict[str, Any]) -> None:
        self.consume_metadata(set())
        if self.at_end():
            raise LogEnded()
        move = self.consume()
        if not move.is_game or move.actor_seat is not None:
            self.diverge(f"expected record_event row for {source!r}")
        if move.source != source or not args_equal(move.args, arguments):
            self.diverge(
                f"record_event {source!r} args mismatch: log={move.args!r} live={arguments!r}"
            )
