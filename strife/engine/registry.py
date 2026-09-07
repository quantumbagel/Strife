from __future__ import annotations

import importlib
import pkgutil
import random
from typing import Any

from strife.engine.game import Game
from strife.engine.metadata import GameMetadata
from strife.engine.platform import PLATFORM_VERSION, parse_version, platform_satisfies
from strife.engine.players import Player
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


def _validate_versions(metadata: GameMetadata) -> bool:
    if parse_version(metadata.version) is None:
        log.error(
            "Game %s has invalid version %r (expected major.minor.patch)",
            metadata.key,
            metadata.version,
        )
        return False
    if parse_version(metadata.platform_version) is None:
        log.error(
            "Game %s has invalid platform_version %r (expected major.minor.patch)",
            metadata.key,
            metadata.platform_version,
        )
        return False
    if not platform_satisfies(metadata.platform_version):
        log.error(
            "Game %s (%s) v%s targets platform %s; this host is platform %s",
            metadata.name,
            metadata.key,
            metadata.version,
            metadata.platform_version,
            PLATFORM_VERSION,
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
            if not _validate_versions(metadata):
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
            log.info(
                "Successfully registered game module: %s (%s) v%s (platform %s)",
                metadata.name,
                key,
                metadata.version,
                metadata.platform_version,
            )
        except Exception as e:
            log.exception("Unexpected error registering game class %s: %s", game_cls.__name__, e)

    def unregister(self, key: str) -> bool:
        return self._games.pop(key, None) is not None

    def discover(self, package: str = "strife.games") -> None:
        """Load plugins. Default path uses plugin.toml via PluginManager."""
        if package == "strife.games":
            from pathlib import Path

            from strife.plugins.manager import PluginManager

            PluginManager.from_paths(
                config_dir=Path("config"),
                plugins_dir=Path("plugins"),
            ).load(self)
            return
        pkg = importlib.import_module(package)
        for _finder, name, is_pkg in pkgutil.iter_modules(pkg.__path__, pkg.__name__ + "."):
            if not is_pkg:
                continue
            try:
                module = importlib.import_module(name)
            except Exception:
                log.exception("Failed to import game package %s", name)
                continue
            from strife.plugins.errors import PluginError
            from strife.plugins.loader import game_class

            try:
                game_cls = game_class(module)
            except PluginError as exc:
                log.error("%s", exc)
                continue
            if game_cls is None:
                log.error("Package %s did not export GAME", name)
                continue
            self.register(game_cls)

    def contains(self, key: str) -> bool:
        return key in self._games

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
    ) -> Game:
        game_cls = self.get(key)
        rng = random.Random(seed)
        return game_cls(players, settings, rng)
