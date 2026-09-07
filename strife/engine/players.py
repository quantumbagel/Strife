from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from strife.engine.log import LogEntryKind


@dataclass
class Player:
    seat: int
    user_id: int | None
    display_name: str
    is_bot: bool = False
    bot_difficulty: str | None = None
    role_key: str | None = None
    timeout_strikes: int = 0
    taken_over: bool = False

    def _base_name(self) -> str:
        if self.user_id is not None and not self.is_bot:
            return f"<@{self.user_id}>"
        difficulty = f" ({self.bot_difficulty})" if self.bot_difficulty else ""
        return f"**{self.display_name}**{difficulty}"

    def mention_for(self, emoji: Any = None) -> str:
        from strife.presentation.emoji_context import active_emoji

        resolver = emoji or active_emoji()
        base = self._base_name()
        if (self.is_bot or self.user_id is None) and resolver is not None:
            return f"{resolver.get('bot_indicator', base=True)} {base}"
        return base

    @property
    def mention(self) -> str:
        return self.mention_for()

    def display(
        self,
        emoji: Any = None,
        *,
        owner_ids: frozenset[int] | set[int] | None = None,
        creator_id: int | None = None,
    ) -> str:
        prefix = ""
        if emoji:
            if creator_id is not None and self.user_id == creator_id:
                prefix += f"{emoji.get('creator', base=True)} "
            if owner_ids is not None and self.user_id in owner_ids:
                prefix += f"{emoji.get('admin', base=True)} "

        return f"{prefix}{self.mention_for(emoji)}"

    def __str__(self) -> str:
        return self.mention


@dataclass
class GameOutcome:
    results: dict[int, str]
    summary: dict[str, Any]
    description: str
    player_descriptions: dict[int, str]


@dataclass
class Move:
    """A live input or a recorded log entry.

    Live ``request_input`` results and replay log rows share this type so
    ``play()`` and ``apply_move()`` / ``parse_replay()`` read the same fields.
    Prefer ``args``; ``arguments`` is a compatibility alias.
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
    def arguments(self) -> dict[str, Any]:
        return self.args


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
