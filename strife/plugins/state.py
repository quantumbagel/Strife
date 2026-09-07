from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from strife.plugins.errors import PluginError


@dataclass
class InstalledSource:
    source: str
    ref: str | None = None


@dataclass
class PluginState:
    removed: list[str] = field(default_factory=list)
    installed: dict[str, InstalledSource] = field(default_factory=dict)
    path: Path | None = None

    def is_removed(self, key: str) -> bool:
        return key in self.removed

    def mark_removed(self, key: str) -> None:
        if key not in self.removed:
            self.removed.append(key)
        self.installed.pop(key, None)

    def unmark_removed(self, key: str) -> None:
        self.removed = [item for item in self.removed if item != key]


def load_state(path: Path) -> PluginState:
    if not path.exists():
        return PluginState(path=path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise PluginError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PluginError(f"{path} must be a mapping")

    removed_raw = data.get("removed") or []
    removed = [str(item) for item in removed_raw]

    installed: dict[str, InstalledSource] = {}
    raw_installed = data.get("installed") or {}
    if not isinstance(raw_installed, dict):
        raise PluginError(f"{path}: 'installed' must be a mapping")
    for key, value in raw_installed.items():
        if isinstance(value, str):
            installed[str(key)] = InstalledSource(source=value)
            continue
        if not isinstance(value, dict):
            raise PluginError(f"{path}: installed.{key} must be a mapping or URL string")
        source = str(value.get("source") or "")
        if not source:
            raise PluginError(f"{path}: installed.{key} is missing 'source'")
        ref = value.get("ref")
        installed[str(key)] = InstalledSource(
            source=source,
            ref=str(ref) if ref else None,
        )

    return PluginState(removed=removed, installed=installed, path=path)


def save_state(state: PluginState) -> None:
    if state.path is None:
        raise PluginError("Plugin state has no path")
    installed: dict[str, Any] = {}
    for key, src in state.installed.items():
        entry: dict[str, Any] = {"source": src.source}
        if src.ref:
            entry["ref"] = src.ref
        installed[key] = entry
    payload: dict[str, Any] = {
        "removed": list(state.removed),
        "installed": installed,
    }
    state.path.parent.mkdir(parents=True, exist_ok=True)
    state.path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
