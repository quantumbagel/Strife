from __future__ import annotations

from typing import TYPE_CHECKING
from strife.engine.players import Move

if TYPE_CHECKING:
    from strife.games.spyfall.game import Spyfall


def choose_move(game: Spyfall, difficulty: str, seat: int) -> Move:
    if game.accused_player is not None:
        if seat == game.accused_player:
            return Move(actor_seat=seat, source="vote_innocent", args={})
        if seat == game.spy:
            return Move(actor_seat=seat, source="vote_guilty", args={})
        source = "vote_guilty" if game.bot_rng.random() < 0.6 else "vote_innocent"
        return Move(actor_seat=seat, source=source, args={})

    is_spy = seat == game.spy
    pressure = 0.2 if difficulty == "easy" else 0.35 if difficulty == "medium" else 0.5
    pressure += 0.12 * max(0, game.turn - 1)

    if not is_spy and seat not in game._accused_seats and game.bot_rng.random() < pressure:
        others = [s for s in game.alive if s != seat]
        if others:
            target = game.bot_rng.choice(others)
            return Move(actor_seat=seat, source="accuse", args={"target": str(target)})

    return Move(actor_seat=seat, source="pass", args={})
