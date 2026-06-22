from __future__ import annotations

import random
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, ClassVar

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.metadata import GameMetadata
from strife.engine.players import GameOutcome, Move, Player
from strife.persistence.repositories import MoveRecord
from strife.presentation.components import LayoutView


class Game(ABC):
    metadata: ClassVar[GameMetadata]

    def __init__(self, players: list[Player], settings: Mapping[str, Any], rng: random.Random):
        self.players = players
        self.settings = settings
        self.rng = rng

    @abstractmethod
    async def play(self, ctx: GameContext) -> GameOutcome: ...

    async def parse_replay(self, moves: list[MoveRecord], ctx: GameContext) -> list[ReplayFrame]:
        raise NotImplementedError

    async def bot_move(self, difficulty: str, seat: int) -> Move:
        raise NotImplementedError

    def remove_player(self, seat: int) -> None:
        raise NotImplementedError

    def validate_roles(self, assignment: dict[int, str]) -> tuple[bool, str | None]:
        return True, None

    async def final_view(self, ctx: GameContext, outcome: GameOutcome) -> LayoutView | None:
        return None

