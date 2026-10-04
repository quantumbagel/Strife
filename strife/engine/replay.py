from __future__ import annotations

import json
from collections.abc import Sequence
from copy import deepcopy
from datetime import datetime
from typing import Any, Literal

from strife.engine.context import ReplayFrame
from strife.engine.game import Game
from strife.engine.log import LogEntryKind
from strife.engine.players import Move, Player
from strife.engine.requests import TimeoutConsequence
from strife.logging import get_logger
from strife.presentation.components import LayoutView, disable_all
from strife.presentation.emoji import EmojiResolver

log = get_logger("engine.replay")


def freeze_view(view: LayoutView) -> LayoutView:
    """Deep-copy a layout and disable every control for replay display."""
    frozen = deepcopy(view)
    disable_all(frozen)
    return frozen


def system_replay_info(players: Sequence[Player], move: Move) -> dict | None:
    """Build replay UI metadata for ``kind: system`` log entries (read-only)."""
    if move.kind != LogEntryKind.SYSTEM:
        return None

    if move.source == "bot_takeover":
        seat = move.args.get("seat")
        if seat is not None:
            for player in players:
                if player.seat == seat:
                    return {
                        "user_id": player.user_id,
                        "display_name": player.display_name,
                        "is_bot": True,
                        "type": "bot_takeover",
                        "reason": move.args.get("reason", "timeout"),
                    }
        return None

    if move.source == "forfeit":
        actor = move.actor_seat if move.actor_seat is not None else move.args.get("seat")
        if actor is not None:
            for player in players:
                if player.seat == actor:
                    return {
                        "user_id": player.user_id,
                        "display_name": player.display_name,
                        "is_bot": player.is_bot,
                        "type": "removal",
                        "reason": move.args.get("reason", "forfeit"),
                    }
    return None


class ReplayDivergence(RuntimeError):
    """log and the re-run play() disagree"""


class _ReplayEnded(Exception):
    """Normal end of replay log while play() is still unwinding."""


def _norm_args(args: dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, default=str)


def _args_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return _norm_args(a) == _norm_args(b)


class ReplayContext:
    """``GameContext`` that answers from a stored match log."""

    def __init__(
        self,
        game: Game,
        moves: Sequence[Move],
        *,
        emoji: EmojiResolver,
        started_at: datetime | None,
        frames: list[ReplayFrame],
    ) -> None:
        self._game = game
        self._moves = sorted(moves, key=lambda m: m.turn_index)
        self.emoji = emoji
        self._started_at = started_at
        self._frames = frames
        self._cursor = 0
        self._step = 0
        self._last_view: LayoutView | None = None
        self._last_frozen: LayoutView | None = None
        self._pending_banner: dict | None = None
        self._last_answer_actor: int | None = None
        self._last_answer_time: datetime | None = None

    @property
    def is_replay(self) -> bool:
        return True

    @property
    def started_at(self) -> datetime | None:
        return self._started_at

    @property
    def turn_timeout_seconds(self) -> float | None:
        return None

    def is_bot(self, seat: int) -> bool:
        return self._game.players[seat].is_bot

    def _diverge(self, message: str) -> None:
        raise ReplayDivergence(f"at log index {self._cursor}: {message}")

    def _check_end(self, move: Move) -> None:
        if move.is_system and move.source == "game_end":
            raise _ReplayEnded()
        if move.is_system and move.source == "forfeit" and not move.args.get("removed"):
            raise _ReplayEnded()

    def _at_end(self) -> bool:
        return self._cursor >= len(self._moves)

    def _peek(self) -> Move | None:
        if self._at_end():
            return None
        return self._moves[self._cursor]

    def _consume(self) -> Move:
        if self._at_end():
            raise _ReplayEnded()
        move = self._moves[self._cursor]
        self._cursor += 1
        self._check_end(move)
        return move

    def _apply_metadata(self, move: Move, waiting: set[int]) -> bool:
        """Apply one metadata row. Returns True if consumed."""
        if move.source == "bot_takeover" and move.is_system:
            seat = move.args.get("seat")
            if seat is not None:
                self._game.players[int(seat)].is_bot = True
                diff = move.args.get("bot_difficulty")
                if diff is not None:
                    self._game.players[int(seat)].bot_difficulty = str(diff)
            info = system_replay_info(self._game.players, move)
            if info:
                self._pending_banner = info
            return True
        if (
            move.is_system
            and move.source == "forfeit"
            and move.args.get("removed")
        ):
            seat = move.actor_seat
            if seat is not None and int(seat) in waiting:
                return False
            if seat is not None:
                self._game.remove_player(int(seat))
            info = system_replay_info(self._game.players, move)
            if info:
                self._pending_banner = info
            return True
        return False

    def _consume_metadata(self, waiting: set[int]) -> None:
        while not self._at_end():
            move = self._peek()
            assert move is not None
            if move.is_system and move.source == "game_end":
                self._consume()
                raise _ReplayEnded()
            if move.is_system and move.source == "forfeit" and not move.args.get("removed"):
                self._consume()
                raise _ReplayEnded()
            if self._apply_metadata(move, waiting):
                self._consume()
                continue
            break

    def _matches_input(self, move: Move, actor: int) -> bool:
        if move.is_system and move.source in ("forfeit", "timeout"):
            return move.actor_seat == actor
        if not move.is_game:
            return False
        return move.actor_seat == actor

    def _consume_answer_row(self, seat: int) -> Move:
        move = self._consume()
        if move.is_system and move.source == "forfeit" and move.args.get("removed"):
            self._game.remove_player(seat)
        self._note_answer(move)
        return move

    def _note_answer(self, move: Move) -> None:
        self._last_answer_actor = move.actor_seat
        self._last_answer_time = move.created_at

    def _maybe_frame(self, view: LayoutView) -> None:
        self._last_view = view
        rendered = self._game.render_replay(self, view)
        if rendered is None:
            return
        frozen = freeze_view(rendered)
        if frozen == self._last_frozen:
            return
        label = self._game.replay_label()
        if label is None:
            label = "Start" if self._step == 0 else f"Step {self._step}"
        takeover = self._pending_banner
        self._pending_banner = None
        ts = self._last_answer_time
        if self._step == 0 and ts is None:
            ts = self._started_at
        self._frames.append(
            ReplayFrame(
                index=len(self._frames),
                turn_label=label,
                actor_seat=self._last_answer_actor,
                view=frozen,
                takeover_info=takeover,
                timestamp=ts,
            )
        )
        self._last_frozen = frozen
        self._step += 1

    async def _finish(self, outcome: Any) -> None:
        final_live = None
        if outcome is not None:
            final_live = await self._game.final_view(self, outcome)
        view = final_live if final_live is not None else self._last_view
        if view is None:
            return
        rendered = self._game.render_replay(self, view)
        if rendered is None:
            return
        frozen = freeze_view(rendered)
        ts = self._last_answer_time
        actor = self._last_answer_actor
        takeover = self._pending_banner
        self._pending_banner = None
        if self._frames and self._frames[-1].view == frozen:
            last = self._frames[-1]
            self._frames[-1] = ReplayFrame(
                index=last.index,
                turn_label="Final",
                actor_seat=actor,
                view=frozen,
                takeover_info=takeover if takeover is not None else last.takeover_info,
                timestamp=ts,
            )
            return
        self._frames.append(
            ReplayFrame(
                index=len(self._frames),
                turn_label="Final",
                actor_seat=actor,
                view=frozen,
                takeover_info=takeover,
                timestamp=ts,
            )
        )

    async def update(self, view: LayoutView) -> None:
        self._maybe_frame(view)

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
        self._maybe_frame(view)
        self._consume_metadata({actor})
        if self._at_end():
            raise _ReplayEnded()
        move = self._peek()
        assert move is not None
        if not self._matches_input(move, actor):
            self._diverge(
                f"expected input for seat {actor}, got {move.source!r} "
                f"(actor_seat={move.actor_seat})"
            )
        return self._consume_answer_row(actor)

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
    ) -> dict[int, Move]:
        self._maybe_frame(view)
        if until == "any":
            self._consume_metadata(actors)
            if self._at_end():
                raise _ReplayEnded()
            move = self._peek()
            assert move is not None
            if move.is_system and move.source == "timeout" and move.actor_seat is None:
                if move.args.get("until") == "any":
                    move = self._consume()
                    return {}
            for seat in actors:
                if self._matches_input(move, seat):
                    return {seat: self._consume_answer_row(seat)}
            self._diverge(f"expected until=any input for one of {actors}")

        results: dict[int, Move] = {}
        remaining = set(actors)
        while remaining:
            self._consume_metadata(remaining)
            if self._at_end():
                raise _ReplayEnded()
            peek = self._peek()
            assert peek is not None
            matched: int | None = None
            for seat in remaining:
                if self._matches_input(peek, seat):
                    matched = seat
                    break
            if matched is None:
                self._diverge(
                    f"expected input for one of {remaining}, got {peek.source!r}"
                )
            results[matched] = self._consume_answer_row(matched)
            remaining.discard(matched)
        return results

    async def send_private(self, seat: int, view: LayoutView) -> None:
        return

    async def record_event(self, source: str, arguments: dict[str, Any]) -> None:
        self._consume_metadata(set())
        if self._at_end():
            raise _ReplayEnded()
        move = self._consume()
        if not move.is_game or move.actor_seat is not None:
            self._diverge(f"expected record_event row for {source!r}")
        if move.source != source or not _args_equal(move.args, arguments):
            self._diverge(
                f"record_event {source!r} args mismatch: log={move.args!r} live={arguments!r}"
            )

    async def respond_query(self, view: LayoutView) -> None:
        raise RuntimeError("respond_query() is not available during replay")


async def run_replay(
    game: Game,
    moves: Sequence[Move],
    *,
    emoji: EmojiResolver,
    started_at: datetime | None,
) -> list[ReplayFrame]:
    """Run the game's ``play()`` against stored log; return replay frames."""
    frames: list[ReplayFrame] = []
    ctx = ReplayContext(game, moves, emoji=emoji, started_at=started_at, frames=frames)
    outcome = None
    try:
        outcome = await game.play(ctx)
    except _ReplayEnded:
        pass
    if ctx._cursor < len(ctx._moves):
        log.warning(
            "Replay for %s ended with %d log row(s) unconsumed",
            type(game).__name__,
            len(ctx._moves) - ctx._cursor,
        )
    await ctx._finish(outcome)
    return frames
