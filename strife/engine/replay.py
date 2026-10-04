from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime
from typing import Any, Literal

from strife.engine.context import ReplayFrame, noop_turn_deadline
from strife.engine.game import Game
from strife.engine.log import LogEntryKind
from strife.engine.log_cursor import (
    LogCursor,
    LogEnded,
    ReplayDivergence,
    log_ends_match,
)
from strife.engine.players import Move, Player
from strife.engine.requests import SeatPrompt, TimeoutConsequence
from strife.logging import get_logger
from strife.presentation.components import LayoutView, disable_all
from strife.presentation.emoji import EmojiResolver

log = get_logger("engine.replay")

__all__ = [
    "LogEnded",
    "ReplayContext",
    "ReplayDivergence",
    "freeze_view",
    "run_replay",
    "system_replay_info",
]


def freeze_view(view: LayoutView) -> LayoutView:
    """Deep-copy a layout and disable every control for replay display."""
    frozen = deepcopy(view)
    disable_all(frozen)
    return frozen


def system_replay_info(players: Sequence[Player], move: Move) -> dict | None:
    """Build replay UI metadata for ``kind: system`` log entries (read-only)."""
    if move.kind != LogEntryKind.SYSTEM:
        return None
    if move.source == "timeout_strike":
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
        actor = (
            move.actor_seat if move.actor_seat is not None else move.args.get("seat")
        )
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
        self._log = LogCursor(moves, game, on_metadata=self._on_metadata)
        self.emoji = emoji
        self._started_at = started_at
        self._frames = frames
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

    def turn_deadline(self, seconds: float | None = None):
        return noop_turn_deadline(seconds)

    def is_bot(self, seat: int) -> bool:
        return self._game.players[seat].is_bot

    def _on_metadata(self, move: Move) -> None:
        info = system_replay_info(self._game.players, move)
        if info:
            self._pending_banner = info

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
        move = self._log.take_input(actor)
        self._note_answer(move)
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
        self._maybe_frame(view)
        if until == "any":
            results = self._log.take_inputs_any(actors)
        else:
            results = self._log.take_inputs_all(actors)
        for move in results.values():
            self._note_answer(move)
        return results

    async def send_private(self, seat: int, view: LayoutView) -> None:
        return

    async def record_event(self, source: str, arguments: dict[str, Any]) -> None:
        self._log.take_event(source, arguments)

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
    play_returned = False
    try:
        outcome = await game.play(ctx)
        play_returned = True
    except LogEnded as ended:
        if not ended.match_ended and not log_ends_match(moves):
            raise ReplayDivergence("log ends before the match does") from ended
    leftover = ctx._log._moves[ctx._log.position :]
    if play_returned:
        unconsumed_game = [row for row in leftover if row.is_game]
        if unconsumed_game:
            raise ReplayDivergence("play() returned with unconsumed game row(s)")
        if leftover:
            log.warning(
                "Replay for %s ended with %d log row(s) unconsumed",
                type(game).__name__,
                len(leftover),
            )
    elif leftover:
        log.warning(
            "Replay for %s ended with %d log row(s) unconsumed",
            type(game).__name__,
            len(leftover),
        )
    await ctx._finish(outcome)
    return frames
