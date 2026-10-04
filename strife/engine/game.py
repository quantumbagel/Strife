from __future__ import annotations

import random
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, ClassVar

from strife.engine.context import GameContext
from strife.engine.metadata import GameMetadata
from strife.engine.outcomes import forfeit_outcome
from strife.engine.players import GameOutcome, Move, Player
from strife.engine.requests import BotRequest
from strife.presentation.components import LayoutView


class Game(ABC):
    """Base class for a Strife game. See ``docs/game-api.md``.

    * ``play(ctx)`` — required. Return ``GameOutcome`` when finished.
    * ``bot_move`` — required when ``metadata.bots`` is set.
    * ``remove_player`` — override to allow mid-match seat removal (inferred at registration).
    * ``final_view``, ``handle_query``, ``forfeit_end_outcome`` — optional.
    * Peek/help buttons: ``query=True`` and ``handle_query``. Not moves.
    * Group inputs: ``request_inputs`` then one ``record_event`` — not one log row per player.
    """

    metadata: ClassVar[GameMetadata]

    def __init__(self, players: list[Player], settings: Mapping[str, Any], rng: random.Random):
        self.players = players
        self.settings = settings
        self.rng = rng
        self.bot_rng = random.Random()

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

    async def bot_move(self, request: BotRequest) -> Move:
        raise NotImplementedError(
            f"{type(self).__name__} must implement bot_move() "
            f"(metadata declares bot difficulties)"
        )

    def remove_player(self, seat: int) -> None:
        """Called when a player is removed mid-game. Override to enable removal."""

    def forfeit_end_outcome(self, forfeiter_seat: int, reason: str = "forfeit") -> GameOutcome:
        """Outcome when the host ends the match because ``forfeiter_seat`` quit.

        Default: the forfeiter and anyone already out of play lose; remaining
        active seats win. Faction games should override this.
        """
        outcome = forfeit_outcome(
            self.players,
            forfeiter_seat,
            alive_seats=self.active_seats(),
            reason=reason,
            must_end=True,
        )
        assert outcome is not None
        return outcome

    async def final_view(self, ctx: GameContext, outcome: GameOutcome) -> LayoutView | None:
        return None

    def render_replay(self, ctx: GameContext, live_view: LayoutView | None) -> LayoutView | None:
        """Replay frame for current state. Default: board play() last showed.

        Hidden-info games override to reveal secrets. Return None to skip frame."""
        return live_view

    def replay_label(self) -> str | None:
        """Optional label for replay frame. Default: None"""
        return None

    async def handle_query(self, seat: int, source: str, ctx: GameContext) -> bool:
        """Handle non-move button clicks (peek, open ephemeral UI, etc.).

        Return ``True`` when *source* is handled. Respond with
        ``await ctx.respond_query(view)`` — do not touch Discord. Mark query
        controls with ``query=True`` (they are also omitted from inferred
        ``sources``). Handled queries are not recorded and must not appear in
        replay. See ``docs/game-development.md``.
        """
        return False
