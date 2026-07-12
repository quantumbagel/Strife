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

    # Otherwise, it's normal gameplay phase
    # If the bot is the spy, it might decide to guess the location (10% chance per turn)
    is_spy = (seat == game.spy)
    if is_spy and game.rng.random() < 0.15:
        loc = game.rng.choice(game.LOCATIONS)
        game.pending_guess[seat] = loc
        return Move(actor_seat=seat, source="guess_location", args={})

    # Otherwise, choose a player to accuse (10% chance)
    if game.rng.random() < 0.1:
        others = [s for s in game.alive if s != seat]
        if others:
            target = game.rng.choice(others)
            game.pending_accuse[seat] = target
            return Move(actor_seat=seat, source="accuse", args={})

    # If nothing triggered, bot passes / does nothing
    return Move(actor_seat=seat, source="pass", args={})
