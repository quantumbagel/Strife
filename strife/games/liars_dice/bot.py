from __future__ import annotations

import math
from typing import TYPE_CHECKING
from strife.engine.players import Move

if TYPE_CHECKING:
    from strife.games.liars_dice.game import LiarsDice


def choose_move(game: LiarsDice, difficulty: str, seat: int) -> Move:
    total_dice = game._total_alive_dice()
    my_dice = game.hands.get(seat, [])
    my_count = len(my_dice)
    other_dice_count = total_dice - my_count
    legal_faces = game._legal_face_values()

    # If first bid of the round
    if game.current_bid is None:
        if difficulty == "easy" or not my_dice:
            val = game.rng.choice(my_dice) if my_dice else game.rng.choice(legal_faces)
            if val not in legal_faces:
                val = game.rng.choice(legal_faces)
            return Move(actor_seat=seat, source="bid", args={"quantity": 1, "value": val})

        counts = {v: my_dice.count(v) for v in range(1, 7)}
        wilds = game.settings.get("wild_ones", True)
        best_val = max(
            legal_faces,
            key=lambda x: counts[x] + (counts[1] if wilds else 0),
        )
        q = counts[best_val] + (counts[1] if wilds else 0)
        q = max(1, min(q, total_dice))
        return Move(actor_seat=seat, source="bid", args={"quantity": q, "value": best_val})

    curr_q, curr_v = game.current_bid

    wilds = game.settings.get("wild_ones", True)
    match_count = my_dice.count(curr_v) + (my_dice.count(1) if wilds and curr_v != 1 else 0)

    prob_single = 1.0 / 3.0 if wilds and curr_v != 1 else 1.0 / 6.0
    expected_others = other_dice_count * prob_single
    total_expected = match_count + expected_others

    std_dev = math.sqrt(other_dice_count * prob_single * (1.0 - prob_single)) if other_dice_count > 0 else 0.5
    threshold = total_expected + (0.5 * std_dev if difficulty == "hard" else 0.0)

    if difficulty == "easy":
        if curr_q > total_dice or (curr_q > total_expected + 2 and game.rng.random() < 0.3):
            return Move(actor_seat=seat, source="challenge", args={})
        return _make_raise(game, seat, curr_q, curr_v, total_dice, legal_faces)

    if curr_q > threshold + 1.5 * std_dev or curr_q > total_dice:
        return Move(actor_seat=seat, source="challenge", args={})

    return _make_raise(game, seat, curr_q, curr_v, total_dice, legal_faces)


def _make_raise(
    game: LiarsDice,
    seat: int,
    curr_q: int,
    curr_v: int,
    total_dice: int,
    legal_faces: list[int],
) -> Move:
    my_dice = game.hands.get(seat, [])
    wilds = game.settings.get("wild_ones", True)

    if not my_dice:
        next_q = min(curr_q + 1, total_dice)
        return Move(actor_seat=seat, source="bid", args={"quantity": next_q, "value": curr_v})

    counts = {v: my_dice.count(v) for v in range(1, 7)}
    best_val = max(
        legal_faces,
        key=lambda x: counts[x] + (counts[1] if wilds else 0),
    )

    if curr_v < 6 and game.rng.random() < 0.4:
        return Move(
            actor_seat=seat,
            source="bid",
            args={"quantity": curr_q, "value": curr_v + 1},
        )

    next_q = min(curr_q + 1, total_dice)
    return Move(actor_seat=seat, source="bid", args={"quantity": next_q, "value": best_val})
