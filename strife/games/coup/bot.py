from __future__ import annotations

from typing import TYPE_CHECKING
from strife.engine.players import Move

if TYPE_CHECKING:
    from strife.games.coup.game import Coup


def choose_move(game: Coup, difficulty: str, seat: int) -> Move:
    # 1. Check if bot needs to select a card to lose (influence reveal)
    # The game loop will request input with source="lose_influence"
    # Wait, how does the engine query bot_move? It passes the active state or the source.
    # To handle all phases in bot_move, we check game phase or inputs requested.
    
    # We can check what inputs are valid based on the game's active state.
    # In Coup, we have:
    # - "lose_influence": when we must discard a card.
    # - "exchange_keep": when selecting card to keep after Ambassador.
    # - "challenge_block": challenge or block reactions.
    # - "action": normal turn action.

    my_cards = game.hands.get(seat, [])
    my_coins = game.coins.get(seat, 0)

    # State: Blocker/Challenger/Target losing card
    if game.state_phase == "lose_influence":
        # Bot must pick one of its own cards to discard
        if my_cards:
            return Move(actor_seat=seat, source="lose_card", args={"card": my_cards[0]})
        return Move(actor_seat=seat, source="lose_card", args={})

    if game.state_phase == "exchange":
        # Ambassador card exchange: game.exchange_options[seat] contains cards to choose from
        options = game.exchange_options.get(seat, [])
        if len(options) >= len(my_cards):
            keep = options[:len(my_cards)]
            return Move(actor_seat=seat, source="exchange_keep", args={"keep": keep})
        return Move(actor_seat=seat, source="exchange_keep", args={})

    if game.state_phase in ("challenge_window", "block_window", "block_challenge_window"):
        action_type = game.current_action
        target = game.current_target

        if game.state_phase == "challenge_window":
            if action_type == "assassinate" and target == seat:
                if "contessa" in my_cards or game.rng.random() < 0.7:
                    return Move(actor_seat=seat, source="block_contessa", args={})
            if game.rng.random() < 0.1 and game.current_actor != seat:
                return Move(actor_seat=seat, source="challenge", args={})
            return Move(actor_seat=seat, source="pass", args={})

        if game.state_phase == "block_window":
            if action_type == "steal" and target == seat:
                claim = "captain" if "captain" in my_cards else "ambassador"
                return Move(actor_seat=seat, source="block", args={"claim": claim})
            if action_type == "foreign_aid" and seat != game.current_actor:
                if "duke" in my_cards or game.rng.random() < 0.2:
                    return Move(actor_seat=seat, source="block", args={"claim": "duke"})
            return Move(actor_seat=seat, source="pass", args={})

        if game.state_phase == "block_challenge_window":
            if game.rng.random() < 0.2:
                return Move(actor_seat=seat, source="challenge", args={})
            return Move(actor_seat=seat, source="pass", args={})

    # Normal turn action selection
    # If 10+ coins, must Coup
    if my_coins >= 10:
        target = game.rng.choice([s for s in game.alive if s != seat])
        return Move(actor_seat=seat, source="action", args={"type": "coup", "target": target})

    # Deciding action
    # If we have Assassin and 3+ coins, assassinate
    if "assassin" in my_cards and my_coins >= 3:
        target = game.rng.choice([s for s in game.alive if s != seat])
        return Move(actor_seat=seat, source="action", args={"type": "assassinate", "target": target})

    # If we have Captain, steal
    if "captain" in my_cards and game.rng.random() < 0.8:
        # Target someone with coins
        targets_with_coins = [s for s in game.alive if s != seat and game.coins[s] > 0]
        if targets_with_coins:
            target = game.rng.choice(targets_with_coins)
            return Move(actor_seat=seat, source="action", args={"type": "steal", "target": target})

    # Duke: tax
    if "duke" in my_cards or game.rng.random() < 0.5:
        return Move(actor_seat=seat, source="action", args={"type": "tax"})

    # Default to income or foreign aid
    if game.rng.random() < 0.5:
        return Move(actor_seat=seat, source="action", args={"type": "foreign_aid"})
    return Move(actor_seat=seat, source="action", args={"type": "income"})
