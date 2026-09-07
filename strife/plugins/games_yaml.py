from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from strife.logging import get_logger
from strife.plugins.errors import PluginError

log = get_logger("plugins.games_yaml")


def set_game_enabled(path: Path, key: str, *, enabled: bool) -> bool:
    """Set ``games.<key>.enabled``. Creates the key if missing. Preserves other fields.

    Returns True when the file was written.
    """
    if not path.exists():
        return False
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise PluginError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PluginError(f"{path} must be a mapping")

    games = data.get("games")
    if games is None:
        games = {}
        data["games"] = games
    if not isinstance(games, dict):
        raise PluginError(f"{path}: 'games' must be a mapping")

    entry = games.get(key)
    changed = False
    if not isinstance(entry, dict):
        games[key] = {"enabled": enabled}
        changed = True
    elif entry.get("enabled") is not enabled:
        entry["enabled"] = enabled
        changed = True
    if not changed:
        return False

    _write_yaml(path, data)
    log.info("Set games.%s.enabled = %s in %s", key, enabled, path)
    return True


def ensure_game_enabled_entry(path: Path, key: str) -> bool:
    """Set ``games.<key>.enabled: true``, creating the key if needed."""
    return set_game_enabled(path, key, enabled=True)


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    try:
        path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
    except OSError as exc:
        raise PluginError(f"Cannot write {path}: {exc}") from exc
