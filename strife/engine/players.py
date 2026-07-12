from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Player:
    seat: int
    user_id: int | None
    display_name: str
    is_bot: bool = False
    bot_difficulty: str | None = None
    role_key: str | None = None

    @property
    def mention(self) -> str:
        if self.user_id is not None and not self.is_bot:
            return f"<@{self.user_id}>"
        difficulty = f" ({self.bot_difficulty})" if self.bot_difficulty else ""
        return f"**{self.display_name}**{difficulty}"

    def display(
        self,
        emoji: Any = None,
        *,
        owner_ids: frozenset[int] | set[int] | None = None,
        creator_id: int | None = None,
    ) -> str:
        prefix = ""
        if emoji:
            # 1. Creator prefix
            if creator_id is not None and self.user_id == creator_id:
                prefix += f"{emoji.get('creator')} "
            # 2. Admin prefix
            if owner_ids is not None and self.user_id in owner_ids:
                prefix += f"{emoji.get('admin')} "
            # 3. Bot prefix
            if self.is_bot or self.user_id is None:
                prefix += f"{emoji.get('bot')} "

        return f"{prefix}{self.mention}"

    def __str__(self) -> str:
        return self.mention


@dataclass
class GameOutcome:
    results: dict[int, str]
    summary: dict
    description: str
    player_descriptions: dict[int, str]


@dataclass
class Move:
    actor_seat: int | None
    source: str
    args: dict
