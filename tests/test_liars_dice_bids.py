from __future__ import annotations

import random

from strife.engine.players import Player
from strife.games.liars_dice.game import LiarsDice


def _game() -> LiarsDice:
    players = [
        Player(seat=0, user_id=1, display_name="A"),
        Player(seat=1, user_id=2, display_name="B"),
    ]
    game = LiarsDice(players, {"dice_count": 5, "wild_ones": True}, random.Random(0))
    game.dice_counts = {0: 5, 1: 5}
    return game


def test_bid_quantities_increment_when_face_is_six() -> None:
    game = _game()
    game.current_bid = (5, 6)
    assert game._bid_quantities()[0] == 6
    assert 5 not in game._bid_quantities()


def test_bid_values_require_a_raise_on_same_quantity() -> None:
    game = _game()
    game.current_bid = (5, 3)
    assert 5 in game._bid_quantities()
    assert 3 not in game._bid_values(5)
    assert 4 in game._bid_values(5)
    assert 2 in game._bid_values(6)


def test_max_bid_has_no_quantity_choices() -> None:
    game = _game()
    game.current_bid = (10, 6)
    assert game._is_max_bid()
    assert game._bid_quantities() == []
