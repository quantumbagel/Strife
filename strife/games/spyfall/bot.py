from __future__ import annotations

from typing import TYPE_CHECKING
from strife.engine.players import Move

if TYPE_CHECKING:
    from strife.games.spyfall.game import Spyfall


def choose_move(game: Spyfall, difficulty: str, seat: int) -> Move:
    # Check if we are in voting phase
    if game.accused_player is not None:
        # We must vote "guilty" or "innocent"
        # If we are the spy, we want to vote innocent if accused, or guilty on villagers
        # To make it simple: vote guilty 60% of the time, except if we are the accused
        if seat == game.accused_player:
            return Move(actor_seat=seat, source="vote", args={"value": "innocent"})
        
        val = "guilty" if game.rng.random() < 0.6 else "innocent"
        return Move(actor_seat=seat, source="vote", args={"value": val})

    is_spy = seat == game.spy
    pressure = 0.2 if difficulty == "easy" else 0.35 if difficulty == "medium" else 0.5
    pressure += 0.12 * max(0, game.turn - 1)

    if is_spy and game.rng.random() < pressure:
        loc = game.rng.choice(game.LOCATIONS)
        game.pending_guess[seat] = loc
        return Move(actor_seat=seat, source="guess_location", args={})

    if game.rng.random() < pressure:
        others = [s for s in game.alive if s != seat]
        if others:
            target = game.rng.choice(others)
            game.pending_accuse[seat] = target
            return Move(actor_seat=seat, source="accuse", args={})

    return Move(actor_seat=seat, source="pass", args={})
