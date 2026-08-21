from __future__ import annotations

import random

from strife.engine.players import Player


def order_players(
    players: list[Player],
    order: str,
    rng: random.Random,
    creator_id: int | None = None,
) -> list[Player]:
    ordered = list(players)
    if order == "random":
        rng.shuffle(ordered)
    elif order == "reversed":
        ordered.reverse()
    elif order == "creator_first" and creator_id is not None:
        ordered.sort(key=lambda p: 0 if p.user_id == creator_id else 1)
    for idx, player in enumerate(ordered):
        player.seat = idx
    return ordered
