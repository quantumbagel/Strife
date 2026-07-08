from __future__ import annotations

import math
from typing import TYPE_CHECKING
from strife.engine.players import Move

if TYPE_CHECKING:
    from strife.games.liars_dice.game import LiarsDice


def choose_move(game: LiarsDice, difficulty: str, seat: int) -> Move:
    total_dice = sum(game.dice_counts.values())
    my_dice = game.hands.get(seat, [])
    my_count = len(my_dice)
    other_dice_count = total_dice - my_count

    # If first bid of the round
    if game.current_bid is None:
        # Easy bot bids 1 of a random value in hand
        if difficulty == "easy" or not my_dice:
            val = game.rng.choice(my_dice) if my_dice else game.rng.randint(2, 6)
            return Move(actor_seat=seat, source="bid", args={"quantity": 1, "value": val})
        
        # Medium/Hard bot bids based on its hand
        counts = {v: my_dice.count(v) for v in range(1, 7)}
        best_val = max(range(2, 7), key=lambda x: counts[x] + (counts[1] if game.settings.get("wild_ones", True) else 0))
        q = counts[best_val] + (counts[1] if game.settings.get("wild_ones", True) else 0)
        q = max(1, q)
        return Move(actor_seat=seat, source="bid", args={"quantity": q, "value": best_val})

    curr_q, curr_v = game.current_bid

    # Probability estimation for challenge decision
    # Count how many matching dice we have
    wilds = game.settings.get("wild_ones", True)
    match_count = my_dice.count(curr_v) + (my_dice.count(1) if wilds and curr_v != 1 else 0)
    
    prob_single = 1.0 / 3.0 if wilds and curr_v != 1 else 1.0 / 6.0
    expected_others = other_dice_count * prob_single
    total_expected = match_count + expected_others

    # Calculate standard deviation for binomial distribution: sqrt(N * p * (1-p))
    std_dev = math.sqrt(other_dice_count * prob_single * (1.0 - prob_single)) if other_dice_count > 0 else 0.5
    threshold = total_expected + (0.5 * std_dev if difficulty == "hard" else 0.0)

    # Easy bot challenges randomly (10% of the time) or if bid is absurdly high
    if difficulty == "easy":
        if curr_q > total_dice or (curr_q > total_expected + 2 and game.rng.random() < 0.3):
            return Move(actor_seat=seat, source="challenge", args={})
        # Otherwise raise bid
        return _make_raise(game, seat, curr_q, curr_v)

    # Challenge if bid is higher than threshold
    if curr_q > threshold + 1.5 * std_dev or curr_q > total_dice:
        return Move(actor_seat=seat, source="challenge", args={})

    # Raise
    return _make_raise(game, seat, curr_q, curr_v)


def _make_raise(game: LiarsDice, seat: int, curr_q: int, curr_v: int) -> Move:
    # A raise must increase quantity, or keep quantity and increase value.
    # Simple strategy:
    # If value is less than 6, we can increase value to curr_v + 1 and keep quantity.
    # Or we can increase quantity by 1 and choose a random/our best value.
    my_dice = game.hands.get(seat, [])
    wilds = game.settings.get("wild_ones", True)

    if not my_dice:
        # Fallback
        return Move(actor_seat=seat, source="bid", args={"quantity": curr_q + 1, "value": curr_v})

    counts = {v: my_dice.count(v) for v in range(1, 7)}
    best_val = max(range(2, 7), key=lambda x: counts[x] + (counts[1] if wilds else 0))

    if curr_v < 6 and game.rng.random() < 0.4:
        return Move(actor_seat=seat, source="bid", args={"quantity": curr_q, "value": curr_v + 1})
    else:
        # Increment quantity
        return Move(actor_seat=seat, source="bid", args={"quantity": curr_q + 1, "value": best_val})
