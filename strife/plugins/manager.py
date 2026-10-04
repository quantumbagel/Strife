from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Collection
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
from strife.plugins.games_yaml import (
    ensure_game_entry,
    has_game_entry,
    remove_game_entry,
)
from strife.plugins.loader import (
    game_class,
    import_plugin,
    stamp_versions,
    unload_plugin_modules,
)
from strife.plugins.manifest import (
    KEY_RE,
    Origin,
    PluginManifest,
    PluginRecord,
    collect_plugin_manifests,
    load_manifest,
    scan_plugin_toml,
)
from strife.plugins.state import InstalledSource, PluginState, load_state, save_state

if TYPE_CHECKING:
    from strife.engine.registry import GameRegistry

log = get_logger("plugins")

_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)
_URL_PREFIXES = ("http://", "https://", "git@", "ssh://", "file://", "git://")


@dataclass(frozen=True)
class UninstallResult:
    key: str
    origin: str


@dataclass(frozen=True)
class _InstallUndo:
    origin: Origin
    was_removed: bool
    added_game_row: bool


@dataclass(frozen=True)
class PluginUpdate:
    """Files are swapped in; call ``finish_update`` or ``revert_update`` once reload is known."""

    manifest: PluginManifest
    requested_ref: str | None
    ref: str | None
    commit: str | None
    reused_ref: bool
    previous: InstalledSource
    root: Path
    backup: Path


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
        self._pending_installs: dict[str, _InstallUndo] = {}
        self._builds: dict[str, str] = {}

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
        for manifest in collect_plugin_manifests(self.builtins_dir):
            if manifest.path is None:
                continue
            if not include_removed and self.state.is_removed(manifest.key):
                continue
            records.append(
                PluginRecord(manifest=manifest, origin="builtin", root=manifest.path)
            )
        return records

    def installed_records(self) -> list[PluginRecord]:
        records: list[PluginRecord] = []
        for manifest in collect_plugin_manifests(self.plugins_dir):
            if manifest.path is None:
                continue
            records.append(
                PluginRecord(manifest=manifest, origin="installed", root=manifest.path)
            )
        return records

    def active_records(self) -> list[PluginRecord]:
        by_key: dict[str, PluginRecord] = {}
        for record in self.builtin_records():
            existing = by_key.get(record.key)
            if existing is not None:
                log.error(
                    "Duplicate plugin key '%s': %s and %s",
                    record.key,
                    existing.root,
                    record.root,
                )
                continue
            by_key[record.key] = record
        for record in self.installed_records():
            existing = by_key.get(record.key)
            if existing is not None:
                if existing.origin == "builtin":
                    log.error(
                        "Installed plugin %s collides with builtin %s at %s; skipping installed copy",
                        record.root,
                        record.key,
                        existing.root,
                    )
                else:
                    log.error(
                        "Duplicate plugin key '%s': %s and %s",
                        record.key,
                        existing.root,
                        record.root,
                    )
                continue
            by_key[record.key] = record
        return list(by_key.values())

    def record_for(
        self, key: str, *, include_removed_builtins: bool = False
    ) -> PluginRecord | None:
        for record in self.builtin_records(include_removed=include_removed_builtins):
            if record.key == key:
                return record
        for record in self.installed_records():
            if record.key == key:
                return record
        return None

    def build_for(self, key: str) -> str | None:
        """Content fingerprint of a successfully loaded plugin, or ``None``."""
        return self._builds.get(key)

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
            try:
                self._load_record(registry, record)
            except PluginError as exc:
                log.error("%s", exc)
                continue
            if self.games_yaml_path is not None and registry.contains(record.key):
                try:
                    has_row = has_game_entry(self.games_yaml_path, record.key)
                except PluginError:
                    continue
                if not has_row:
                    log.info(
                        "%s has no row for %s; it is enabled with default tuning",
                        self.games_yaml_path,
                        record.key,
                    )

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
        try:
            missing = missing_dependencies(record.dependencies)
        except PluginError as exc:
            fail(f"Plugin {record.key}: {exc}", cause=exc)
            return
        if missing:
            fail(
                f"Plugin {record.key} is missing declared dependencies: {', '.join(missing)}"
            )
            return
        try:
            module = import_plugin(record.origin, record.root, record.key)
        except Exception as exc:
            log.exception("Failed to import plugin %s from %s", record.key, record.root)
            unload_plugin_modules(record.origin, record.key, record.root.name)
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
            return
        self._builds[record.key] = _plugin_build(record.root)

    def install_from_git(self, url: str, ref: str | None = None) -> PluginManifest:
        """Clone and register a plugin. Call ``confirm_install`` / ``rollback_install`` after loading."""
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="strife-plugin-"))
        src = tmp / "src"
        dest: Path | None = None
        try:
            commit = _git_clone(url, src, ref)
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
                self.state.installed[manifest.key] = InstalledSource(
                    source=url, ref=_ref_to_record(ref, commit)
                )
                self.state.unmark_removed(manifest.key)
                self.save()
            except Exception:
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                self.state.installed.pop(manifest.key, None)
                if was_removed:
                    self.state.mark_removed(manifest.key)
                raise

            added_row = self._ensure_game_entry(manifest.key)
            self._pending_installs[manifest.key] = _InstallUndo(
                origin="installed", was_removed=was_removed, added_game_row=added_row
            )
            return load_manifest(dest / "plugin.toml")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def confirm_install(self, key: str) -> None:
        """The plugin loaded (or will load after a restart); forget the rollback info."""
        self._pending_installs.pop(key, None)

    def rollback_install(self, key: str) -> None:
        """Undo ``install_from_git`` / ``restore_builtin`` for a plugin that failed to load.

        Removes the cloned folder, the ``plugins.yaml`` row, and the ``games.yaml`` row the
        install added (an existing row with the owner's tuning is left alone).
        """
        undo = self._pending_installs.pop(key, None)
        if undo is None:
            raise PluginError(f"No pending install of '{key}' to roll back")
        self._builds.pop(key, None)
        errors: list[str] = []
        if undo.origin == "installed":
            folder = self.plugins_dir / key
            unload_plugin_modules("installed", key, folder.name)
            if folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
                if folder.exists():
                    errors.append(f"could not delete {folder}")
            self.state.installed.pop(key, None)
            if undo.was_removed:
                self.state.mark_removed(key)
        else:
            record = self.record_for(key, include_removed_builtins=True)
            unload_plugin_modules("builtin", key, record.root.name if record else None)
            self.state.mark_removed(key)
        try:
            self.save()
        except Exception as exc:
            errors.append(f"could not write {self.state_path}: {exc}")
        if undo.added_game_row and self.games_yaml_path is not None:
            try:
                remove_game_entry(self.games_yaml_path, key)
            except PluginError as exc:
                errors.append(str(exc))
        if errors:
            raise PluginError(
                f"Rollback of '{key}' was incomplete: " + "; ".join(errors)
            )

    def update_from_git(self, key: str, ref: str | None = None) -> PluginUpdate:
        """Swap in the new files, keeping the old folder as a backup.

        Call ``finish_update`` once the new code is loaded, or ``revert_update`` to put the
        previous files and ref back.
        """
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
        previous = InstalledSource(source=src_info.source, ref=src_info.ref)

        tmp = Path(tempfile.mkdtemp(prefix="strife-plugin-"))
        src = tmp / "src"
        try:
            commit = _git_clone(url, src, use_ref)
            toml_path = src / "plugin.toml"
            if not toml_path.is_file():
                raise PluginError("plugin.toml not found at the repository root")
            manifest = load_manifest(toml_path)
            if manifest.key != key:
                raise PluginError(
                    f"Updated repo key is {manifest.key!r}, expected {key!r}"
                )

            dest = record.root
            backup = _swap_dir(dest, src)
            recorded_ref = _ref_to_record(use_ref, commit)
            try:
                self.state.installed[key] = InstalledSource(
                    source=url, ref=recorded_ref
                )
                self.save()
            except Exception:
                _restore_backup(dest, backup)
                self.state.installed[key] = previous
                raise
            self._ensure_game_entry(key)
            return PluginUpdate(
                manifest=load_manifest(dest / "plugin.toml"),
                requested_ref=ref,
                ref=recorded_ref,
                commit=commit,
                reused_ref=ref is None and use_ref is not None,
                previous=previous,
                root=dest,
                backup=backup,
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def finish_update(self, update: PluginUpdate) -> None:
        shutil.rmtree(update.backup, ignore_errors=True)

    def revert_update(self, update: PluginUpdate) -> None:
        """Put the pre-update files and ref back (the new code failed to load)."""
        _restore_backup(update.root, update.backup)
        self.state.installed[update.manifest.key] = update.previous
        self.save()

    def restore_builtin(self, key: str) -> PluginManifest:
        if not KEY_RE.match(key):
            raise PluginError(
                f"{key!r} is neither a builtin plugin key nor a git URL "
                f"(e.g. `https://github.com/you/repo`)"
            )
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
        added_row = self._ensure_game_entry(key)
        self._pending_installs[key] = _InstallUndo(
            origin="builtin", was_removed=True, added_game_row=added_row
        )
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
        self._builds.pop(key, None)
        return UninstallResult(key=key, origin=origin)

    def status_lines(
        self,
        *,
        loaded: Collection[str] | None = None,
        hidden: Collection[str] = (),
    ) -> list[str]:
        """One line per plugin. With *loaded*, flag active plugins that failed to load."""

        def load_flag(key: str) -> str:
            if loaded is None:
                return ""
            if key not in loaded:
                return ", NOT loaded - check the logs"
            if key in hidden:
                return ", loaded, hidden by games.yaml"
            return ", loaded"

        lines: list[str] = []
        removed = set(self.state.removed)
        for record in self.builtin_records(include_removed=True):
            if record.key in removed:
                flag = "uninstalled"
            else:
                flag = "active" + load_flag(record.key)
            extra = ""
            if record.dependencies:
                extra = f"  deps: {', '.join(record.dependencies)}"
            lines.append(f"{record.key}: builtin ({flag}){extra}")
        for record in self.installed_records():
            src = self.state.installed.get(record.key)
            origin = f" from {src.source}" if src else ""
            if src and src.ref:
                origin += f" @ {src.ref}"
            flag = load_flag(record.key).removeprefix(", ")
            status = f" ({flag})" if flag else ""
            extra = (
                f"  deps: {', '.join(record.dependencies)}"
                if record.dependencies
                else ""
            )
            lines.append(
                f"{record.key}: installed v{record.manifest.version}{origin}{status}{extra}"
            )
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
        """Create a playable ``games.yaml`` row if the key is new. Leave existing tuning alone.

        Returns True when this call added the row.
        """
        if self.games_yaml_path is None:
            return False
        try:
            if has_game_entry(self.games_yaml_path, key):
                return False
            ensure_game_entry(self.games_yaml_path, key)
            return has_game_entry(self.games_yaml_path, key)
        except PluginError as exc:
            log.error("Failed to update %s for %s: %s", self.games_yaml_path, key, exc)
            return False


def normalize_source(value: str) -> str | None:
    """Return a git URL/path for *value*, or ``None`` when it should be a builtin key.

    Accepts scheme-less ``host/owner/repo`` (``github.com/you/repo``) by prepending
    ``https://``. Raises for bare ``owner/repo``, which has no host to clone from.
    """
    text = value.strip()
    if text.startswith(_URL_PREFIXES) or "github.com:" in text:
        return text
    path = Path(text).expanduser()
    if path.is_dir() and (path / ".git").exists():
        return text
    if KEY_RE.fullmatch(text):
        return None
    if text.startswith(("/", "./", "../", "~")):
        return text
    if "/" in text:
        host = text.split("/", 1)[0]
        if "." in host or ":" in host:
            return "https://" + text
        raise PluginError(
            f"`{text}` has no host. Use a full URL, e.g. `strife/install github.com/{text}`."
        )
    if text.endswith(".git"):
        return text
    return None


def _plugin_build(root: Path) -> str:
    """sha256 of sorted relative paths + bytes for ``*.py`` and ``plugin.toml``.

    Skips ``__pycache__`` and any path component that is a dotfile/dot-dir.
    Truncated to 16 hex characters.
    """
    digest = hashlib.sha256()
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part == "__pycache__" or part.startswith(".") for part in rel.parts):
            continue
        if path.suffix == ".py" or path.name == "plugin.toml":
            files.append(rel)
    for rel in sorted(files, key=lambda p: p.as_posix()):
        digest.update(rel.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / rel).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def _ref_to_record(ref: str | None, commit: str | None) -> str | None:
    """Remember branches/tags as given; pin commit refs to the full SHA."""
    if (
        ref
        and commit
        and _COMMIT_RE.fullmatch(ref)
        and commit.lower().startswith(ref.lower())
    ):
        return commit
    return ref


def _swap_dir(dest: Path, new_src: Path) -> Path:
    """Move *new_src* into *dest*. Returns the backup of the old folder (a dot-folder that
    plugin discovery skips); delete it with ``shutil.rmtree`` or restore it with
    ``_restore_backup``."""
    backup = dest.parent / f".{dest.name}.previous"
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
    return backup


def _restore_backup(dest: Path, backup: Path) -> None:
    if not backup.exists():
        raise PluginError(f"Backup {backup} is gone; cannot restore {dest}")
    if dest.exists():
        shutil.rmtree(dest)
    backup.rename(dest)


def _run_git(cmd: list[str], *, cwd: Path | None = None, step: str = "command") -> str:
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise PluginError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise PluginError(f"git {step} timed out after 120s") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise PluginError(f"git {step} failed: {detail[-800:]}")
    return result.stdout.strip()


def _git_clone(url: str, dest: Path, ref: str | None) -> str | None:
    """Check out *ref* (branch, tag, or 7-40 char commit SHA) of *url* into *dest*.

    Returns the checked-out commit SHA (``None`` if it could not be read).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if ref and _COMMIT_RE.fullmatch(ref):
        if len(ref) == 40 and _shallow_fetch_commit(url, dest, ref):
            return _git_head(dest)
        # Servers only serve full SHAs by name, so a short SHA needs the history to resolve.
        shutil.rmtree(dest, ignore_errors=True)
        _run_git(["git", "clone", "--no-checkout", url, str(dest)], step="clone")
        try:
            commit = _run_git(
                ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                cwd=dest,
                step="rev-parse",
            )
        except PluginError as exc:
            raise PluginError(
                f"Commit `{ref}` was not found in {url} (or the short SHA is ambiguous). "
                "Use a longer SHA, a tag, or a branch."
            ) from exc
        _run_git(["git", "checkout", "--detach", commit], cwd=dest, step="checkout")
        return commit
    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd.extend(["--branch", ref])
    cmd.extend([url, str(dest)])
    _run_git(cmd, step="clone")
    return _git_head(dest)


def _shallow_fetch_commit(url: str, dest: Path, sha: str) -> bool:
    dest.mkdir(parents=True)
    steps: list[tuple[str, list[str]]] = [
        ("init", ["git", "init"]),
        ("remote add", ["git", "remote", "add", "origin", url]),
        ("fetch", ["git", "fetch", "--depth", "1", "origin", sha]),
        ("checkout", ["git", "checkout", "--detach", "FETCH_HEAD"]),
    ]
    try:
        for step, cmd in steps:
            _run_git(cmd, cwd=dest, step=step)
    except PluginError as exc:
        log.info(
            "Shallow fetch of %s from %s failed (%s); cloning full history",
            sha,
            url,
            exc,
        )
        return False
    return True


def _git_head(repo: Path) -> str | None:
    try:
        return (
            _run_git(["git", "rev-parse", "HEAD"], cwd=repo, step="rev-parse") or None
        )
    except PluginError:
        return None
