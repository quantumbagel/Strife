from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from strife.engine.log import LogEntryKind


class Interrupt(StrEnum):
    FORFEIT = "forfeit"
    TIMEOUT = "timeout"


class Result(StrEnum):
    WIN = "win"
    LOSS = "loss"
    DRAW = "draw"


@dataclass
class Player:
    seat: int
    user_id: int | None
    display_name: str
    is_bot: bool = False
    bot_difficulty: str | None = None
    role_key: str | None = None

    def mention_for(self, emoji: Any = None) -> str:
        if _mention_formatter is not None:
            return _mention_formatter(self, emoji)
        return _plain_mention(self)

    @property
    def mention(self) -> str:
        return self.mention_for()

    def __str__(self) -> str:
        return self.mention


_mention_formatter: Callable[[Player, Any], str] | None = None


def set_mention_formatter(fn: Callable[[Player, Any], str] | None) -> None:
    """Install host mention markup. ``None`` restores plain display names."""
    global _mention_formatter
    _mention_formatter = fn


def _plain_mention(player: Player) -> str:
    if player.is_bot and player.bot_difficulty:
        return f"{player.display_name} ({player.bot_difficulty})"
    return player.display_name


@dataclass
class GameOutcome:
    results: dict[int, Result]
    summary: dict[str, Any]
    description: str
    player_descriptions: dict[int, str]


@dataclass
class Move:
    """A live input or a recorded log entry.

    Live ``request_input`` results and replay log rows share this type so
    ``play()`` and ``apply_move()`` / replay read the same fields.
    """

    actor_seat: int | None
    source: str
    args: dict[str, Any] = field(default_factory=dict)
    kind: LogEntryKind = LogEntryKind.GAME
    turn_index: int = 0
    created_at: datetime | None = None

    @property
    def is_game(self) -> bool:
        return self.kind == LogEntryKind.GAME

    @property
    def is_system(self) -> bool:
        return self.kind == LogEntryKind.SYSTEM

    @property
    def interrupt(self) -> Interrupt | None:
        if self.kind != LogEntryKind.SYSTEM:
            return None
        if self.source == Interrupt.FORFEIT:
            return Interrupt.FORFEIT
        if self.source == Interrupt.TIMEOUT:
            return Interrupt.TIMEOUT
        return None


def select_value(move: Move, *keys: str) -> Any:
    """Read a select/button argument.

    Checks *keys* first, then ``value``, then ``values[0]``. Discord single
    selects arrive as ``{"value": ...}``; multi-selects as ``{"values": [...]}``.
    """
    for key in keys:
        if key in move.args and move.args[key] is not None:
            return move.args[key]
    if move.args.get("value") is not None:
        return move.args["value"]
    values = move.args.get("values")
    if isinstance(values, (list, tuple)) and values:
        return values[0]
    return None
