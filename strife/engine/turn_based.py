from __future__ import annotations

from abc import abstractmethod

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.game import Game
from strife.engine.replay import ReplayBuilder, is_terminal_replay_move, system_replay_info
from strife.persistence.repositories import MoveRecord
from strife.presentation.components import LayoutView


class TurnBasedGame(Game):
    """Opt-in base for turn-based games with a default ``parse_replay`` implementation.

    Subclasses implement ``reset``, ``apply_move``, and ``render``. Override
    ``render_final`` when the end-state view differs from a normal action view.
    """

    @abstractmethod
    def reset(self) -> None: ...

    @abstractmethod
    def apply_move(self, move: MoveRecord) -> None: ...

    @abstractmethod
    def render(
        self,
        ctx: GameContext,
        *,
        title: str,
        status: str | None = None,
        status_emoji: str | None = None,
    ) -> LayoutView: ...

    def render_final(self, ctx: GameContext) -> LayoutView:
        return self.render(ctx, title="Final", status="Game over.", status_emoji="error")

    def render_replay(
        self,
        ctx: GameContext,
        *,
        title: str,
        status: str | None = None,
        status_emoji: str | None = None,
    ) -> LayoutView:
        return self.render(ctx, title=title, status=status, status_emoji=status_emoji)

    def render_final_replay(self, ctx: GameContext) -> LayoutView:
        return self.render_final(ctx)

    def replay_initial_status(self, ctx: GameContext, moves: list[MoveRecord]) -> str | None:
        if moves:
            first_actor = moves[0].actor_seat if moves[0].actor_seat is not None else 0
            return self.replay_action_status(ctx, first_actor)
        return None

    def replay_action_status(self, ctx: GameContext, next_actor: int) -> str | None:
        return None

    async def parse_replay(self, moves: list[MoveRecord], ctx: GameContext) -> list[ReplayFrame]:
        self.reset()
        builder = ReplayBuilder(ctx)

        builder.initial_frame(
            self.render_replay(
                ctx,
                title="Start",
                status=self.replay_initial_status(ctx, moves),
                status_emoji="loading",
            ),
            label="Start",
        )

        for index, move in enumerate(moves):
            system_info = system_replay_info(self.players, move)
            if move.is_game:
                self.apply_move(move)

            if is_terminal_replay_move(move, index, len(moves)):
                builder.after_move(
                    move,
                    self.render_final_replay(ctx),
                    label="Final",
                    actor_seat=move.actor_seat,
                    takeover_info=system_info,
                )
                break

            action = index + 1
            builder.after_move(
                move,
                self.render_replay(ctx, title=f"Action {action}"),
                label=f"Action {action}",
                actor_seat=move.actor_seat,
                takeover_info=system_info,
            )

        return builder.build()
