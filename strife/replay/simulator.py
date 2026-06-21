from __future__ import annotations


from strife.engine.context import ReplayContext
from strife.engine.players import Player
from strife.engine.registry import GameRegistry
from strife.persistence.repositories import MatchDetail, MoveRecord
from strife.engine.context import ReplayFrame


class ReplaySimulator:
    def __init__(self, registry: GameRegistry) -> None:
        self._registry = registry

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
        ctx = ReplayContext(rng=game.rng, players=players, settings=match.settings, moves=moves)
        try:
            await game.play(ctx)
        except RuntimeError:
            raise
        return ctx.frames
