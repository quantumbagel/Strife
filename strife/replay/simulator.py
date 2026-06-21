from __future__ import annotations


from strife.engine.context import ReplayContext, ReplayFrame, ReplayMoveUnderflow
from strife.engine.players import Player
from strife.engine.registry import GameRegistry
from strife.persistence.repositories import MatchDetail, MoveRecord
from strife.presentation.compiler import clone_and_disable
from strife.presentation.emoji import EmojiResolver


class ReplaySimulator:
    def __init__(self, registry: GameRegistry, emoji: EmojiResolver) -> None:
        self._registry = registry
        self._emoji = emoji

    async def simulate(self, match: MatchDetail, moves: list[MoveRecord]) -> list[ReplayFrame]:
        players = [
            Player(
                seat=p.seat_index,
                user_id=p.user_id,
                display_name=p.display_name,
                is_bot=p.is_bot,
                bot_difficulty=p.bot_difficulty,
                role_key=p.role_key,
            )
            for p in match.players
        ]
        game = self._registry.create(match.game_key, players, match.settings, match.seed)
        ctx = ReplayContext(
            rng=game.rng,
            players=players,
            settings=match.settings,
            moves=moves,
            emoji=self._emoji,
        )
        outcome = None
        try:
            outcome = await game.play(ctx)
        except ReplayMoveUnderflow:
            pass
        except RuntimeError:
            raise
        if outcome is not None:
            final = await game.final_view(ctx, outcome)
            if final is not None:
                ctx.frames.append(
                    ReplayFrame(
                        index=len(ctx.frames),
                        turn_label="Final",
                        actor_seat=None,
                        view=clone_and_disable(final),
                    )
                )
        return ctx.frames
