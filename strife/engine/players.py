from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Player:
    seat: int
    user_id: int | None
    display_name: str
    is_bot: bool = False
    bot_difficulty: str | None = None
    role_key: str | None = None


@dataclass
class GameOutcome:
    results: dict[int, str]
    summary: dict


@dataclass
class Move:
    actor_seat: int | None
    source: str
    args: dict
