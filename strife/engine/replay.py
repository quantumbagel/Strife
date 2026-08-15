from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.log import LogEntryKind
from strife.engine.players import Player
from strife.persistence.repositories import MoveRecord
from strife.presentation.compiler import clone_and_disable
from strife.presentation.components import LayoutView


def system_replay_info(players: Sequence[Player], move: MoveRecord) -> dict | None:
    """Build replay UI metadata for ``kind: system`` log entries."""
    if move.kind != LogEntryKind.SYSTEM:
        return None

    if move.source == "bot_takeover":
        seat = move.arguments.get("seat")
        if seat is not None:
            for player in players:
                if player.seat == seat:
                    player.is_bot = True
                    player.bot_difficulty = move.arguments.get("bot_difficulty", "hard")
                    return {
                        "user_id": player.user_id,
                        "display_name": player.display_name,
                        "is_bot": player.is_bot,
                        "type": "bot_takeover",
                        "reason": move.arguments.get("reason", "timeout"),
                    }
        return None

    if move.source == "forfeit":
        actor = move.actor_seat if move.actor_seat is not None else move.arguments.get("seat")
        if actor is not None:
            for player in players:
                if player.seat == actor:
                    return {
                        "user_id": player.user_id,
                        "display_name": player.display_name,
                        "is_bot": player.is_bot,
                        "type": "removal",
                        "reason": move.arguments.get("reason", "forfeit"),
                    }
    return None


def is_terminal_replay_move(move: MoveRecord, index: int, total: int) -> bool:
    if move.is_system and move.source in ("forfeit", "game_end"):
        return True
    return index == total - 1


class ReplayBuilder:
    """Accumulates disabled replay frames with consistent indexing."""

    def __init__(self, ctx: GameContext) -> None:
        self._ctx = ctx
        self._frames: list[ReplayFrame] = []

    def initial_frame(
        self,
        view: LayoutView,
        *,
        label: str = "Start",
        actor_seat: int | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        self._frames.append(
            ReplayFrame(
                index=len(self._frames),
                turn_label=label,
                actor_seat=actor_seat,
                view=clone_and_disable(view),
                timestamp=timestamp if timestamp is not None else self._ctx.started_at,
            )
        )

    def after_move(
        self,
        move: MoveRecord,
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
                view=clone_and_disable(view),
                takeover_info=takeover_info,
                timestamp=move.created_at,
            )
        )

    def build(self) -> list[ReplayFrame]:
        return self._frames
