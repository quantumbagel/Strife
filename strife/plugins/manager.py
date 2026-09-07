from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from typing import TYPE_CHECKING

from strife.engine.platform import platform_satisfies
from strife.logging import get_logger
from strife.plugins.deps import (
    collect_requirements,
    missing_dependencies,
    pip_install,
)
from strife.plugins.errors import PluginError
from strife.plugins.games_yaml import ensure_game_entry
from strife.plugins.loader import (
    game_class,
    import_plugin,
    stamp_versions,
    unload_plugin_modules,
)
from strife.plugins.manifest import (
    KEY_RE,
    PluginManifest,
    PluginRecord,
    load_manifest,
    scan_plugin_toml,
)
from strife.plugins.state import InstalledSource, PluginState, load_state, save_state

if TYPE_CHECKING:
    from strife.engine.registry import GameRegistry

log = get_logger("plugins")

_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)


@dataclass(frozen=True)
class UninstallResult:
    key: str
    origin: str


class PluginManager:
    def __init__(
        self,
        *,
        builtins_dir: Path,
        plugins_dir: Path,
        state_path: Path,
        games_yaml_path: Path | None = None,
    ) -> None:
        self.builtins_dir = builtins_dir
        self.plugins_dir = plugins_dir
        self.state_path = state_path
        self.games_yaml_path = games_yaml_path
        self._state: PluginState | None = None

    @classmethod
    def from_paths(
        cls,
        *,
        config_dir: Path,
        plugins_dir: Path,
        repo_root: Path | None = None,
    ) -> PluginManager:
        root = repo_root or Path(__file__).resolve().parents[2]
        return cls(
            builtins_dir=root / "strife" / "games",
            plugins_dir=plugins_dir,
            state_path=config_dir / "plugins.yaml",
            games_yaml_path=config_dir / "games.yaml",
        )

    @property
    def state(self) -> PluginState:
        if self._state is None:
            self._state = load_state(self.state_path)
        return self._state

    def reload_state(self) -> PluginState:
        self._state = load_state(self.state_path)
        return self._state

    def save(self) -> None:
        save_state(self.state)

    def builtin_records(self, *, include_removed: bool = False) -> list[PluginRecord]:
        records: list[PluginRecord] = []
        for toml_path in scan_plugin_toml(self.builtins_dir):
            try:
                manifest = load_manifest(toml_path)
            except PluginError as exc:
                log.error("%s", exc)
                continue
            if not include_removed and self.state.is_removed(manifest.key):
                continue
            records.append(PluginRecord(manifest=manifest, origin="builtin", root=toml_path.parent))
        return records

    def installed_records(self) -> list[PluginRecord]:
        records: list[PluginRecord] = []
        for toml_path in scan_plugin_toml(self.plugins_dir):
            try:
                manifest = load_manifest(toml_path)
            except PluginError as exc:
                log.error("%s", exc)
                continue
            records.append(PluginRecord(manifest=manifest, origin="installed", root=toml_path.parent))
        return records

    def active_records(self) -> list[PluginRecord]:
        by_key: dict[str, PluginRecord] = {}
        for record in self.builtin_records():
            by_key[record.key] = record
        for record in self.installed_records():
            if record.key in by_key:
                log.error(
                    "Installed plugin %s collides with builtin %s; skipping installed copy",
                    record.root,
                    record.key,
                )
                continue
            by_key[record.key] = record
        return list(by_key.values())

    def record_for(self, key: str, *, include_removed_builtins: bool = False) -> PluginRecord | None:
        for record in self.builtin_records(include_removed=include_removed_builtins):
            if record.key == key:
                return record
        for record in self.installed_records():
            if record.key == key:
                return record
        return None

    def builtin_keys(self) -> set[str]:
        keys: set[str] = set()
        for toml_path in scan_plugin_toml(self.builtins_dir):
            try:
                keys.add(load_manifest(toml_path).key)
            except PluginError:
                continue
        return keys

    def emoji_sources(self) -> list[tuple[str, Path]]:
        """``(game_key, emoji_dir)`` for every active plugin that ships an ``emoji/`` folder."""
        sources: list[tuple[str, Path]] = []
        for record in self.active_records():
            folder = record.root / "emoji"
            if folder.is_dir():
                sources.append((record.key, folder))
        return sources

    def ensure_dependencies(self, *, builtins_only: bool = False) -> list[str]:
        """pip-install missing extras for plugins that should be active. Returns installed reqs.

        Call this at process start (or ``python -m strife.plugins sync-deps``), not from
        live install/uninstall. Adding packages to a running bot is deferred to the next boot.
        """
        if builtins_only:
            reqs = collect_requirements([self.builtins_dir])
        else:
            reqs = []
            seen: set[str] = set()
            for record in self.active_records():
                for dep in record.dependencies:
                    if dep not in seen:
                        seen.add(dep)
                        reqs.append(dep)
        missing = missing_dependencies(reqs)
        if missing:
            pip_install(missing)
        return missing

    def load(self, registry: GameRegistry) -> None:
        for record in self.active_records():
            self._load_record(registry, record)

    def load_one(self, registry: GameRegistry, key: str) -> None:
        record = self.record_for(key)
        if record is None:
            raise PluginError(f"Plugin '{key}' is not installed")
        self._load_record(registry, record, strict=True)

    def reload_one(self, registry: GameRegistry, key: str) -> None:
        record = self.record_for(key)
        if record is None:
            raise PluginError(f"Plugin '{key}' is not installed")
        previous = registry.get(key) if registry.contains(key) else None
        registry.unregister(key)
        unload_plugin_modules(record.origin, record.key, record.root.name)
        try:
            self._load_record(registry, record, strict=True)
        except Exception:
            unload_plugin_modules(record.origin, record.key, record.root.name)
            if previous is not None:
                registry.register(previous)
            raise

    def _load_record(
        self, registry: GameRegistry, record: PluginRecord, *, strict: bool = False
    ) -> None:
        def fail(message: str, *, cause: BaseException | None = None) -> None:
            log.error("%s", message)
            if strict:
                if cause is not None:
                    raise PluginError(message) from cause
                raise PluginError(message)

        if not platform_satisfies(record.manifest.platform_version):
            fail(
                f"Plugin {record.key} targets platform {record.manifest.platform_version}; "
                f"this host cannot load it"
            )
            return
        missing = missing_dependencies(record.dependencies)
        if missing:
            fail(
                f"Plugin {record.key} is missing declared dependencies: {', '.join(missing)}"
            )
            return
        try:
            module = import_plugin(record.origin, record.root, record.key)
        except Exception as exc:
            log.exception("Failed to import plugin %s from %s", record.key, record.root)
            fail(f"Failed to import plugin {record.key}: {exc}", cause=exc)
            return
        try:
            game_cls = game_class(module)
        except PluginError as exc:
            fail(str(exc), cause=exc)
            return
        if game_cls is None:
            fail(f"Plugin {record.key} did not export GAME")
            return
        meta_key = getattr(game_cls.metadata, "key", None)
        if meta_key != record.key:
            fail(
                f"Plugin {record.key} metadata.key {meta_key!r} does not match plugin.toml key"
            )
            return
        stamp_versions(
            game_cls,
            version=record.manifest.version,
            platform_version=record.manifest.platform_version,
        )
        registry.register(game_cls)
        if not registry.contains(record.key):
            fail(
                f"Plugin {record.key} did not register "
                "(invalid metadata, missing capabilities, or duplicate key)"
            )

    def install_from_git(self, url: str, ref: str | None = None) -> PluginManifest:
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="strife-plugin-"))
        src = tmp / "src"
        dest: Path | None = None
        try:
            _git_clone(url, src, ref)
            toml_path = src / "plugin.toml"
            if not toml_path.is_file():
                raise PluginError("plugin.toml not found at the repository root")
            manifest = load_manifest(toml_path)
            self._reject_key_collision(manifest.key, restoring_builtin=False)

            dest = self.plugins_dir / manifest.key
            if dest.exists():
                raise PluginError(
                    f"Plugin directory already exists: {dest}. "
                    f"Remove that folder, then retry."
                )

            was_removed = self.state.is_removed(manifest.key)
            try:
                shutil.move(str(src), str(dest))
                self.state.installed[manifest.key] = InstalledSource(source=url, ref=ref)
                self.state.unmark_removed(manifest.key)
                self.save()
            except Exception:
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                self.state.installed.pop(manifest.key, None)
                if was_removed:
                    self.state.mark_removed(manifest.key)
                raise

            self._ensure_game_entry(manifest.key)
            return load_manifest(dest / "plugin.toml")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def update_from_git(self, key: str, ref: str | None = None) -> PluginManifest:
        if not KEY_RE.match(key):
            raise PluginError(f"Invalid plugin key {key!r}")
        record = self.record_for(key)
        if record is None or record.origin != "installed":
            raise PluginError(f"'{key}' is not a git-installed plugin")
        src_info = self.state.installed.get(key)
        if src_info is None or not src_info.source:
            raise PluginError(
                f"'{key}' has no recorded git source. Reinstall with `strife/install <git-url>`."
            )
        use_ref = ref if ref is not None else src_info.ref
        url = src_info.source

        tmp = Path(tempfile.mkdtemp(prefix="strife-plugin-"))
        src = tmp / "src"
        try:
            _git_clone(url, src, use_ref)
            toml_path = src / "plugin.toml"
            if not toml_path.is_file():
                raise PluginError("plugin.toml not found at the repository root")
            manifest = load_manifest(toml_path)
            if manifest.key != key:
                raise PluginError(
                    f"Updated repo key is {manifest.key!r}, expected {key!r}"
                )

            dest = record.root
            _swap_dir(dest, src)
            self.state.installed[key] = InstalledSource(source=url, ref=use_ref)
            self.save()
            self._ensure_game_entry(key)
            return load_manifest(dest / "plugin.toml")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def restore_builtin(self, key: str) -> PluginManifest:
        if not KEY_RE.match(key):
            raise PluginError(f"Invalid plugin key {key!r}")
        record = self.record_for(key, include_removed_builtins=True)
        if record is None or record.origin != "builtin":
            raise PluginError(f"'{key}' is not a builtin plugin")
        if any(item.key == key for item in self.installed_records()):
            raise PluginError(
                f"'{key}' is installed from git; uninstall that copy before restoring the builtin"
            )
        if not self.state.is_removed(key):
            raise PluginError(f"'{key}' is already installed")
        self.state.unmark_removed(key)
        self.save()
        self._ensure_game_entry(key)
        return record.manifest

    def uninstall(self, key: str) -> UninstallResult:
        if not KEY_RE.match(key):
            raise PluginError(f"Invalid plugin key {key!r}")
        if self.state.is_removed(key) and self.record_for(key) is None:
            raise PluginError(f"'{key}' is already uninstalled")
        record = self.record_for(key)
        if record is None:
            raise PluginError(f"Unknown plugin '{key}'")

        origin = record.origin

        if origin == "installed":
            try:
                shutil.rmtree(record.root)
            except OSError as exc:
                raise PluginError(
                    f"Could not delete plugin directory {record.root}: {exc}"
                ) from exc
            if record.root.exists():
                raise PluginError(
                    f"Plugin directory still exists after delete: {record.root}"
                )
            self.state.installed.pop(key, None)
        else:
            self.state.mark_removed(key)
        self.save()
        unload_plugin_modules(origin, key, record.root.name)
        return UninstallResult(key=key, origin=origin)

    def status_lines(self) -> list[str]:
        lines: list[str] = []
        removed = set(self.state.removed)
        for record in self.builtin_records(include_removed=True):
            flag = "uninstalled" if record.key in removed else "active"
            extra = ""
            if record.dependencies:
                extra = f"  deps: {', '.join(record.dependencies)}"
            lines.append(f"{record.key}: builtin ({flag}){extra}")
        for record in self.installed_records():
            src = self.state.installed.get(record.key)
            origin = f" from {src.source}" if src else ""
            extra = f"  deps: {', '.join(record.dependencies)}" if record.dependencies else ""
            lines.append(f"{record.key}: installed{origin}{extra}")
        return lines

    def _reject_key_collision(self, key: str, *, restoring_builtin: bool) -> None:
        if key in self.builtin_keys() and not restoring_builtin:
            raise PluginError(
                f"'{key}' is a builtin plugin key. "
                f"Restore it with `strife/install {key}` or pick a different key."
            )
        if self.record_for(key) is not None:
            raise PluginError(
                f"Plugin '{key}' is already installed. "
                f"Update it with `strife/update {key}` or uninstall first."
            )

    def _ensure_game_entry(self, key: str) -> bool:
        """Create a playable ``games.yaml`` row if the key is new. Leave existing tuning alone."""
        if self.games_yaml_path is None:
            return True
        try:
            return ensure_game_entry(self.games_yaml_path, key)
        except PluginError as exc:
            log.error("Failed to update %s for %s: %s", self.games_yaml_path, key, exc)
            return True


def looks_like_source(value: str) -> bool:
    text = value.strip()
    if (
        text.startswith(("http://", "https://", "git@", "ssh://", "file://"))
        or text.endswith(".git")
        or "github.com:" in text
    ):
        return True
    path = Path(text)
    return path.is_dir() and (path / ".git").exists()


def _swap_dir(dest: Path, new_src: Path) -> None:
    backup = dest.parent / f".{dest.name}.updating"
    if backup.exists():
        shutil.rmtree(backup)
    dest.rename(backup)
    try:
        shutil.move(str(new_src), str(dest))
    except Exception as exc:
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        backup.rename(dest)
        raise PluginError(f"Failed to replace plugin directory {dest}: {exc}") from exc
    shutil.rmtree(backup)


def _run_git(cmd: list[str], *, cwd: Path | None = None) -> None:
    try:
        result = subprocess.run(cmd, cwd=cwd, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise PluginError("git is not installed or not on PATH") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise PluginError(f"git clone failed: {detail[-800:]}")


def _git_clone(url: str, dest: Path, ref: str | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if ref and _COMMIT_RE.fullmatch(ref):
        dest.mkdir(parents=True)
        steps = [
            ["git", "init"],
            ["git", "remote", "add", "origin", url],
            ["git", "fetch", "--depth", "1", "origin", ref],
            ["git", "checkout", "FETCH_HEAD"],
        ]
        for cmd in steps:
            _run_git(cmd, cwd=dest)
        return
    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd.extend(["--branch", ref])
    cmd.extend([url, str(dest)])
    _run_git(cmd)
