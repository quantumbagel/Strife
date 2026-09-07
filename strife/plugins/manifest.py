from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from strife.plugins.errors import PluginError

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
Origin = Literal["builtin", "installed"]


@dataclass(frozen=True)
class PluginManifest:
    key: str
    version: str = "0.1.0"
    platform_version: str = "1.0.0"
    dependencies: tuple[str, ...] = ()
    path: Path | None = None

    @property
    def toml_path(self) -> Path | None:
        if self.path is None:
            return None
        return self.path / "plugin.toml"


@dataclass(frozen=True)
class PluginRecord:
    manifest: PluginManifest
    origin: Origin
    root: Path

    @property
    def key(self) -> str:
        return self.manifest.key

    @property
    def dependencies(self) -> tuple[str, ...]:
        return self.manifest.dependencies


def load_manifest(path: Path) -> PluginManifest:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PluginError(f"Cannot read plugin manifest {path}: {exc}") from exc

    key = str(data.get("key") or "").strip()
    if not KEY_RE.match(key):
        raise PluginError(
            f"{path}: 'key' must be a lowercase identifier (letter, then letters/digits/underscore, max 32)"
        )

    raw_deps = data.get("dependencies") or ()
    if isinstance(raw_deps, str):
        deps = (raw_deps,)
    else:
        try:
            deps = tuple(str(item) for item in raw_deps)
        except TypeError as exc:
            raise PluginError(f"{path}: 'dependencies' must be a list of requirement strings") from exc

    version = str(data.get("version") or "0.1.0").strip()
    platform_version = str(data.get("platform_version") or "1.0.0").strip()
    return PluginManifest(
        key=key,
        version=version,
        platform_version=platform_version,
        dependencies=deps,
        path=path.parent,
    )


def scan_plugin_toml(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob("*/plugin.toml") if path.is_file())
