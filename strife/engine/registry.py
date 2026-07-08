from __future__ import annotations

import random
from typing import Any

from strife.engine.game import Game
from strife.engine.metadata import GameMetadata
from strife.engine.players import Player
from strife.engine.roles import assign_roles
from strife.logging import get_logger

log = get_logger("engine.registry")


class GameRegistry:
    def __init__(self) -> None:
        self._games: dict[str, type[Game]] = {}

    def register(self, game_cls: type[Game]) -> None:
        try:
            if not hasattr(game_cls, "metadata") or game_cls.metadata is None:
                log.error("Failed to register game module %s: Class is missing 'metadata' attribute.", game_cls.__name__)
                return
            metadata = game_cls.metadata
            if not hasattr(metadata, "key") or not metadata.key:
                log.error("Failed to register game module %s: Metadata is missing 'key' attribute.", game_cls.__name__)
                return
            key = metadata.key
            if key in self._games:
                log.warning("Duplicate game key '%s' detected during registration of %s. Skipping duplicate registration.", key, game_cls.__name__)
                return
            self._games[key] = game_cls
            log.info("Successfully registered game module: %s (%s)", metadata.name, key)
        except Exception as e:
            log.exception("Unexpected error registering game class %s: %s", game_cls.__name__, e)

    def get(self, key: str) -> type[Game]:
        if key not in self._games:
            raise KeyError(f"Game '{key}' is not registered or failed configuration.")
        return self._games[key]

    def metadata(self, key: str) -> GameMetadata:
        if key not in self._games:
            raise KeyError(f"Game '{key}' is not registered or failed configuration.")
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
