from __future__ import annotations

import random
from typing import Any

from strife.engine.game import Game
from strife.engine.metadata import GameMetadata
from strife.engine.players import Player
from strife.engine.roles import assign_roles


class GameRegistry:
    def __init__(self) -> None:
        self._games: dict[str, type[Game]] = {}

    def register(self, game_cls: type[Game]) -> None:
        key = game_cls.metadata.key
        if key in self._games:
            raise ValueError(f"Duplicate game key: {key}")
        self._games[key] = game_cls

    def get(self, key: str) -> type[Game]:
        return self._games[key]

    def metadata(self, key: str) -> GameMetadata:
        return self._games[key].metadata

    def all(self) -> list[GameMetadata]:
        return [cls.metadata for cls in self._games.values()]

    def create(
        self,
        key: str,
        players: list[Player],
        settings: dict[str, Any],
        seed: int,
        *,
        lobby_selection: dict[int, str] | None = None,
    ) -> Game:
        game_cls = self.get(key)
        meta = game_cls.metadata
        rng = random.Random(seed)
        role_map = assign_roles(meta, players, lobby_selection or {}, rng)
        for player in players:
            if player.seat in role_map:
                player.role_key = role_map[player.seat]
        return game_cls(players, settings, rng)
