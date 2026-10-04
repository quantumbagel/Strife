from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from strife.engine.platform import parse_version
from strife.logging import get_logger
from strife.plugins.errors import PluginError

log = get_logger("plugins.manifest")

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
RESERVED_KEYS = {"play", "strife"}
Origin = Literal["builtin", "installed"]


@dataclass(frozen=True)
class PluginManifest:
    key: str
    version: str
    platform_version: str
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
    if key in RESERVED_KEYS:
        raise PluginError(f"{path}: 'key' '{key}' is reserved by the platform")

    raw_deps = data.get("dependencies") or ()
    if isinstance(raw_deps, str):
        raw_dep_items = (raw_deps,)
    else:
        try:
            raw_dep_items = tuple(str(item) for item in raw_deps)
        except TypeError as exc:
            raise PluginError(
                f"{path}: 'dependencies' must be a list of requirement strings"
            ) from exc

    from strife.plugins.deps import _parse_requirement

    deps: list[str] = []
    for item in raw_dep_items:
        try:
            _parse_requirement(item)
        except PluginError as exc:
            raise PluginError(f"{path}: {exc}") from exc
        deps.append(item.strip())

    if (
        "version" not in data
        or data.get("version") is None
        or str(data.get("version")).strip() == ""
    ):
        raise PluginError(f"{path}: 'version' is required")
    version = str(data.get("version")).strip()
    if parse_version(version) is None:
        raise PluginError(f"{path}: version '{version}' is not a version")

    platform_version = str(data.get("platform_version") or "").strip()
    if not platform_version:
        raise PluginError(f"{path}: 'platform_version' is required")
    if parse_version(platform_version) is None:
        raise PluginError(
            f"{path}: platform_version '{platform_version}' is not a version"
        )
    return PluginManifest(
        key=key,
        version=version,
        platform_version=platform_version,
        dependencies=tuple(deps),
        path=path.parent,
    )


def scan_plugin_toml(root: Path) -> list[Path]:
    """``*/plugin.toml`` under *root*, skipping dot-folders (update backups, tooling)."""
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.glob("*/plugin.toml")
        if path.is_file() and not path.parent.name.startswith(".")
    )


def collect_plugin_manifests(root: Path) -> list[PluginManifest]:
    """Load manifests under *root*. Duplicate keys are logged and omitted (first path wins)."""
    found: list[PluginManifest] = []
    by_key: dict[str, Path] = {}
    for toml_path in scan_plugin_toml(root):
        try:
            manifest = load_manifest(toml_path)
        except PluginError as exc:
            log.error("%s", exc)
            continue
        previous = by_key.get(manifest.key)
        if previous is not None:
            log.error(
                "Duplicate plugin key '%s': %s and %s",
                manifest.key,
                previous,
                manifest.path or toml_path.parent,
            )
            continue
        by_key[manifest.key] = manifest.path or toml_path.parent
        found.append(manifest)
    return found
