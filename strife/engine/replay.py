from __future__ import annotations

from collections.abc import Iterator, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.log import LogEntryKind
from strife.engine.players import Move, Player
from strife.presentation.components import LayoutView, disable_all


def freeze_view(view: LayoutView) -> LayoutView:
    """Deep-copy a layout and disable every control for replay display."""
    frozen = deepcopy(view)
    disable_all(frozen)
    return frozen


def system_replay_info(players: Sequence[Player], move: Move) -> dict | None:
    """Build replay UI metadata for ``kind: system`` log entries."""
    if move.kind != LogEntryKind.SYSTEM:
        return None

    if move.source == "bot_takeover":
        seat = move.args.get("seat")
        if seat is not None:
            for player in players:
                if player.seat == seat:
                    player.is_bot = True
                    player.bot_difficulty = move.args.get("bot_difficulty", "hard")
                    return {
                        "user_id": player.user_id,
                        "display_name": player.display_name,
                        "is_bot": player.is_bot,
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


def is_terminal_replay_move(move: Move, index: int, total: int) -> bool:
    if move.is_system and move.source in ("forfeit", "game_end"):
        return True
    return index == total - 1


@dataclass(frozen=True)
class ReplayStep:
    """One log row, with replay-frame policy attached.

    System entries that only carry metadata (``bot_takeover``) have
    ``frame=False``; apply side effects if needed, then skip rendering.
    The takeover banner is stashed onto the next ``frame=True`` step.
    """

    move: Move
    index: int
    total: int
    takeover: dict | None
    terminal: bool
    frame: bool


def iter_replay(moves: Sequence[Move], players: Sequence[Player]) -> Iterator[ReplayStep]:
    """Walk a match log in order, coalescing system metadata onto game frames."""
    pending_takeover: dict | None = None
    total = len(moves)
    for index, move in enumerate(moves):
        info = system_replay_info(players, move)
        terminal = is_terminal_replay_move(move, index, total)
        if move.is_system and move.source == "bot_takeover" and not terminal:
            if info:
                pending_takeover = info
            yield ReplayStep(
                move=move,
                index=index,
                total=total,
                takeover=info or pending_takeover,
                terminal=False,
                frame=False,
            )
            continue
        takeover = info or pending_takeover
        pending_takeover = None
        yield ReplayStep(
            move=move,
            index=index,
            total=total,
            takeover=takeover,
            terminal=terminal,
            frame=True,
        )


class ReplayBuilder:
    """Accumulates disabled replay frames with consistent indexing.

    Game authors should build frames only through this type — do not import
    the presentation compiler or construct ``ReplayFrame`` by hand.
    """

    def __init__(self, ctx: GameContext) -> None:
        self._ctx = ctx
        self._frames: list[ReplayFrame] = []

    def initial(
        self,
        view: LayoutView,
        *,
        label: str = "Start",
        actor_seat: int | None = None,
        timestamp: datetime | None = None,
    ) -> ReplayBuilder:
        self._frames.append(
            ReplayFrame(
                index=len(self._frames),
                turn_label=label,
                actor_seat=actor_seat,
                view=freeze_view(view),
                timestamp=timestamp if timestamp is not None else self._ctx.started_at,
            )
        )
        return self

    def add(
        self,
        step: ReplayStep,
        view: LayoutView,
        *,
        label: str,
        actor_seat: int | None = None,
    ) -> ReplayBuilder:
        """Append a frame for *step* (no-op when ``step.frame`` is false)."""
        if not step.frame:
            return self
        self._frames.append(
            ReplayFrame(
                index=len(self._frames),
                turn_label=label,
                actor_seat=actor_seat if actor_seat is not None else step.move.actor_seat,
                view=freeze_view(view),
                takeover_info=step.takeover,
                timestamp=step.move.created_at,
            )
        )
        return self

    def initial_frame(
        self,
        view: LayoutView,
        *,
        label: str = "Start",
        actor_seat: int | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        self.initial(view, label=label, actor_seat=actor_seat, timestamp=timestamp)

    def after_move(
        self,
        move: Move,
        view: LayoutView,
        *,
        label: str,
        actor_seat: int | None = None,
        takeover_info: dict | None = None,
    ) -> None:
        self._frames.append(
            ReplayFrame(
                index=len(self._frames),
                turn_label=label,
                actor_seat=actor_seat if actor_seat is not None else move.actor_seat,
                view=freeze_view(view),
                takeover_info=takeover_info,
                timestamp=move.created_at,
            )
        )

    def build(self) -> list[ReplayFrame]:
        return self._frames
