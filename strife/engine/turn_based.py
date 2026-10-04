from __future__ import annotations

from abc import abstractmethod

from strife.engine.context import GameContext
from strife.engine.game import Game
from strife.engine.players import Interrupt, Move
from strife.engine.requests import TimeoutConsequence
from strife.presentation.components import LayoutView


class TurnBasedGame(Game):
    """Opt-in base for turn-based games.

    Implement ``apply_move`` and ``render``. ``play()`` should call
    ``take_turn()`` (or ``apply_move`` after ``request_input``) so live
    play and replay share one rules path.

    ``render`` is called during replay with ``ctx.is_replay is True``; omit
    controls with ``add_controls`` from ``strife.presentation.game_ui``.
    """

    current: int
    """Subclasses must set this to the seat to act."""

    @abstractmethod
    def apply_move(self, move: Move) -> None: ...

    @abstractmethod
    def render(
        self,
        ctx: GameContext,
        *,
        lead: str | None = None,
        prefix_emoji: str | None = None,
    ) -> LayoutView: ...

    def is_active(self, seat: int) -> bool:
        """True if ``seat`` still takes turns. Default: ``seat`` in ``active_seats()``."""
        return seat in self.active_seats()

    def advance(self) -> None:
        """Move ``current`` to the next seat in seat order, skipping inactive seats."""
        order = [player.seat for player in self.players]
        if not order:
            return
        try:
            idx = order.index(self.current)
        except ValueError:
            idx = -1
        n = len(order)
        for step in range(1, n + 1):
            seat = order[(idx + step) % n]
            if self.is_active(seat):
                self.current = seat
                return

    def on_timeout(self, move: Move) -> None:
        """Called by ``take_turn`` on ``Interrupt.TIMEOUT``. Default: ``advance()``."""
        self.advance()

    def on_forfeit(self, move: Move) -> None:
        """Called by ``take_turn`` on ``Interrupt.FORFEIT``. Default: ``advance()`` if current."""
        if move.actor_seat == self.current:
            self.advance()

    async def take_turn(
        self,
        ctx: GameContext,
        sources: set[str] | None = None,
        *,
        lead: str | None = None,
        prefix_emoji: str | None = None,
        description: str | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: TimeoutConsequence | None = None,
    ) -> Move:
        """Render, wait for the current seat, and ``apply_move`` the result.

        Timeouts call ``on_timeout`` instead of ``apply_move``. Forfeits call
        ``on_forfeit``.
        """
        view = self.render(ctx, lead=lead, prefix_emoji=prefix_emoji)
        move = await ctx.request_input(
            view,
            actor=self.current,
            sources=sources,
            description=description,
            timeout_seconds=timeout_seconds,
            timeout_consequence=timeout_consequence,
        )
        if move.interrupt is Interrupt.TIMEOUT:
            self.on_timeout(move)
        elif move.interrupt is Interrupt.FORFEIT:
            self.on_forfeit(move)
        elif move.is_game:
            self.apply_move(move)
        return move
