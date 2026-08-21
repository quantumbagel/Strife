from __future__ import annotations

import random
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, ClassVar

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.metadata import GameMetadata
from strife.engine.players import GameOutcome, Move, Player
from strife.presentation.components import LayoutView


class Game(ABC):
    """Base class for all Strife games.

    Contract summary (see ``docs/game-api.md`` for full details):

    * ``play(ctx)`` — **required** — main game loop.
    * ``parse_replay(moves, ctx)`` — required when ``metadata.supports_replay``.
    * ``bot_move(difficulty, seat)`` — required when ``metadata.supports_bots``.
    * ``remove_player(seat)`` — required when ``metadata.supports_player_removal``.
    * ``final_view``, ``handle_query`` — optional hooks.
    * Query buttons: set ``query=True`` on the control and handle them in
      ``handle_query()``. They are not moves.
    * Group inputs (votes, etc.): ``request_inputs(..., record=False)`` then one
      ``record_event`` for replay — not one log entry per player.
    """

    metadata: ClassVar[GameMetadata]

    def __init__(self, players: list[Player], settings: Mapping[str, Any], rng: random.Random):
        self.players = players
        self.settings = settings
        self.rng = rng

    def active_seats(self) -> set[int]:
        """Seats still in play. Games that track deaths should override this."""
        return {player.seat for player in self.players}

    def setting(self, key: str, default: Any = None) -> Any:
        """Return a lobby setting value, falling back to metadata default then *default*."""
        if key in self.settings:
            return self.settings[key]
        for option in self.metadata.settings:
            if option.key == key:
                return option.default
        return default

    @abstractmethod
    async def play(self, ctx: GameContext) -> GameOutcome: ...

    async def parse_replay(self, moves: list[Move], ctx: GameContext) -> list[ReplayFrame]:
        raise NotImplementedError(
            f"{type(self).__name__} must implement parse_replay() "
            f"(metadata.supports_replay is True)"
        )

    async def bot_move(self, difficulty: str, seat: int) -> Move:
        raise NotImplementedError(
            f"{type(self).__name__} must implement bot_move() "
            f"(metadata declares bot difficulties)"
        )

    def remove_player(self, seat: int) -> None:
        """Called when a player is removed mid-game. Override if ``supports_player_removal``."""

    async def final_view(self, ctx: GameContext, outcome: GameOutcome) -> LayoutView | None:
        return None

    async def handle_query(self, seat: int, source: str, ctx: GameContext) -> bool:
        """Handle non-move button clicks (peek, open ephemeral UI, etc.).

        Return ``True`` when *source* is handled. Respond with
        ``await ctx.respond_query(view)`` — do not touch Discord. Mark query
        controls with ``query=True`` (they are also omitted from inferred
        ``sources``). Handled queries are not recorded and must not appear in
        ``parse_replay``. See ``docs/game-development.md``.
        """
        return False
