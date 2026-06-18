from __future__ import annotations

import random
from collections.abc import Sequence

from strife.engine.metadata import GameMetadata, RoleFlow, RoleMode
from strife.engine.players import Player


def assign_roles(
    metadata: GameMetadata,
    players: Sequence[Player],
    lobby_selection: dict[int, str],
    rng: random.Random,
) -> dict[int, str]:
    if metadata.role_mode == RoleMode.NONE:
        return {}

    role_keys = [role.key for role in metadata.roles]
    if not role_keys:
        return {}

    seats = [p.seat for p in players]

    if metadata.role_mode == RoleMode.CHOSEN:
        assignment: dict[int, str] = {}
        for player in players:
            if player.user_id is None:
                continue
            chosen = lobby_selection.get(player.user_id)
            if chosen:
                assignment[player.seat] = chosen
        return assignment

    # SECRET or RANDOM assignment via game-specific composition happens in game plugins.
    # Default: shuffle role keys across seats.
    if metadata.role_flow == RoleFlow.SELECTABLE:
        assignment = {}
        for player in players:
            if player.user_id and player.user_id in lobby_selection:
                assignment[player.seat] = lobby_selection[player.user_id]
        return assignment

  # For SECRET/RANDOM, games may override via their own roles module.
    shuffled = list(role_keys)
    while len(shuffled) < len(seats):
        shuffled.append(role_keys[0])
    rng.shuffle(shuffled)
    return {seat: shuffled[i] for i, seat in enumerate(seats)}


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
