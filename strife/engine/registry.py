from __future__ import annotations

import importlib
import pkgutil
import random
from typing import Any

from strife.engine.game import Game
from strife.engine.metadata import GameMetadata
from strife.engine.players import Player
from strife.engine.roles import assign_roles
from strife.logging import get_logger

log = get_logger("engine.registry")


def _method_overridden(game_cls: type[Game], name: str) -> bool:
    return getattr(game_cls, name) is not getattr(Game, name)


def _validate_capabilities(game_cls: type[Game], metadata: GameMetadata) -> bool:
    ok = True
    if metadata.supports_replay and not _method_overridden(game_cls, "parse_replay"):
        log.error(
            "Game %s declares supports_replay but does not implement parse_replay()",
            game_cls.__name__,
        )
        ok = False
    if metadata.supports_bots and not _method_overridden(game_cls, "bot_move"):
        log.error(
            "Game %s declares bot difficulties but does not implement bot_move()",
            game_cls.__name__,
        )
        ok = False
    if metadata.supports_player_removal and not _method_overridden(game_cls, "remove_player"):
        log.error(
            "Game %s declares supports_player_removal but does not implement remove_player()",
            game_cls.__name__,
        )
        ok = False
    return ok


def check_dependencies(dependencies: list[str]) -> bool:
    """Return True when every requirement is already installed. Never installs packages."""
    import importlib.metadata
    import importlib.util

    if not dependencies:
        return True

    missing = []
    for dep in dependencies:
        dep_name = dep
        for op in (">=", "==", "<=", ">", "<", "!=", "~="):
            if op in dep_name:
                dep_name = dep_name.split(op)[0].strip()
                break

        try:
            importlib.metadata.distribution(dep_name)
        except importlib.metadata.PackageNotFoundError:
            normalized = dep_name.replace("-", "_")
            spec = None
            try:
                spec = importlib.util.find_spec(normalized)
            except (ModuleNotFoundError, ValueError):
                pass
            if spec is None:
                missing.append(dep)

    if missing:
        log.error(
            "Missing game dependencies (install them at deploy time, not at runtime): %s",
            ", ".join(missing),
        )
        return False
    return True


class GameRegistry:
    def __init__(self) -> None:
        self._games: dict[str, type[Game]] = {}

    def register(self, game_cls: type[Game]) -> None:
        try:
            if not hasattr(game_cls, "metadata") or game_cls.metadata is None:
                log.error(
                    "Failed to register game module %s: Class is missing 'metadata' attribute.",
                    game_cls.__name__,
                )
                return
            metadata = game_cls.metadata
            if not hasattr(metadata, "key") or not metadata.key:
                log.error(
                    "Failed to register game module %s: Metadata is missing 'key' attribute.",
                    game_cls.__name__,
                )
                return
            if not _validate_capabilities(game_cls, metadata):
                return
            key = metadata.key
            if key in self._games:
                log.warning(
                    "Duplicate game key '%s' detected during registration of %s. Skipping duplicate registration.",
                    key,
                    game_cls.__name__,
                )
                return
            self._games[key] = game_cls
            log.info("Successfully registered game module: %s (%s)", metadata.name, key)
        except Exception as e:
            log.exception("Unexpected error registering game class %s: %s", game_cls.__name__, e)

    def discover(self, package: str = "strife.games") -> None:
        """Import every subpackage under *package* and register Game subclasses."""
        pkg = importlib.import_module(package)
        for _finder, name, is_pkg in pkgutil.iter_modules(pkg.__path__, pkg.__name__ + "."):
            if not is_pkg:
                continue
            try:
                module = importlib.import_module(name)
            except Exception:
                log.exception("Failed to import game package %s", name)
                continue
            for attr_name in dir(module):
                obj = getattr(module, attr_name)
                if (
                    isinstance(obj, type)
                    and issubclass(obj, Game)
                    and obj is not Game
                    and hasattr(obj, "metadata")
                    and obj.metadata is not None
                ):
                    self.register(obj)

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
