from __future__ import annotations

from abc import abstractmethod

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.game import Game
from strife.engine.players import Move
from strife.engine.replay import ReplayBuilder, iter_replay
from strife.presentation.components import LayoutView


class TurnBasedGame(Game):
    """Opt-in base for turn-based games with a default ``parse_replay``.

    Implement ``reset``, ``apply_move``, and ``render``. ``play()`` should
    call ``take_turn()`` (or ``apply_move`` after ``request_input``) so live
    play and replay share one rules path. Override ``render_final`` when the
    finished board differs from a normal turn.

    ``render`` is called during replay with ``ctx.is_replay is True``; omit
    controls with ``add_controls`` from ``strife.presentation.game_ui``.
    """

    @abstractmethod
    def reset(self) -> None: ...

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

    def render_final(self, ctx: GameContext) -> LayoutView:
        return self.render(ctx, lead="Game over.", prefix_emoji="error")

    def render_replay(
        self,
        ctx: GameContext,
        *,
        lead: str | None = None,
        prefix_emoji: str | None = None,
        title: str | None = None,
        status: str | None = None,
        status_emoji: str | None = None,
    ) -> LayoutView:
        return self.render(
            ctx,
            lead=lead or status or title,
            prefix_emoji=prefix_emoji or status_emoji,
        )

    def render_final_replay(self, ctx: GameContext) -> LayoutView:
        return self.render_final(ctx)

    def replay_initial_status(self, ctx: GameContext, moves: list[Move]) -> str | None:
        if moves:
            first_actor = moves[0].actor_seat if moves[0].actor_seat is not None else 0
            return self.replay_action_status(ctx, first_actor)
        return None

    def replay_action_status(self, ctx: GameContext, next_actor: int) -> str | None:
        return None

    def replay_label(self, step_index: int) -> str:
        return f"Turn {step_index}"

    async def take_turn(
        self,
        ctx: GameContext,
        sources: set[str] | None = None,
        *,
        lead: str | None = None,
        prefix_emoji: str | None = None,
        description: str | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: str | None = None,
    ) -> Move:
        """Render, wait for the current seat, and ``apply_move`` the result."""
        view = self.render(ctx, lead=lead, prefix_emoji=prefix_emoji)
        move = await ctx.request_input(
            view,
            actor=self.current,  # type: ignore[attr-defined]
            sources=sources,
            description=description,
            timeout_seconds=timeout_seconds,
            timeout_consequence=timeout_consequence,
        )
        if move.is_game:
            self.apply_move(move)
        return move

    async def parse_replay(self, moves: list[Move], ctx: GameContext) -> list[ReplayFrame]:
        self.reset()
        builder = ReplayBuilder(ctx)
        builder.initial(
            self.render(
                ctx,
                lead=self.replay_initial_status(ctx, moves),
                prefix_emoji="loading",
            ),
            label="Start",
        )

        turn = 0
        for step in iter_replay(moves, self.players):
            if step.move.is_game:
                self.apply_move(step.move)
            if not step.frame:
                continue
            turn += 1
            if step.terminal:
                builder.add(step, self.render_final(ctx), label="Final")
                break
            next_actor = getattr(self, "current", step.move.actor_seat)
            lead = (
                self.replay_action_status(ctx, next_actor)
                if isinstance(next_actor, int)
                else None
            )
            builder.add(
                step,
                self.render(ctx, lead=lead, prefix_emoji="loading"),
                label=self.replay_label(turn),
            )

        return builder.build()
