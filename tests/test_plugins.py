from __future__ import annotations

import importlib.metadata
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from strife.engine.registry import GameRegistry
from strife.plugins.deps import (
    distribution_name,
    host_protected_distributions,
    missing_dependencies,
    normalize_dist,
    orphan_distributions,
)
from strife.plugins.errors import PluginError
from strife.plugins.games_yaml import ensure_game_entry, set_game_enabled
from strife.plugins.manager import PluginManager, looks_like_source
from strife.plugins.manifest import load_manifest
from strife.plugins.state import load_state, save_state


def _write_manifest(root: Path, key: str, *, deps: list[str] | None = None, version: str = "1.0.0") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    dep_lines = ""
    if deps:
        inner = ", ".join(f'"{item}"' for item in deps)
        dep_lines = f"dependencies = [{inner}]\n"
    (root / "plugin.toml").write_text(
        f'key = "{key}"\nversion = "{version}"\nplatform_version = "1.0.0"\n{dep_lines}',
        encoding="utf-8",
    )
    return root / "plugin.toml"


def _write_game_package(root: Path, key: str, *, version: str = "1.0.0", name: str | None = None) -> None:
    cls = "".join(part.capitalize() for part in key.split("_"))
    title = name or cls
    _write_manifest(root, key, version=version)
    (root / "__init__.py").write_text(
        f"from .game import {cls}\nGAME = {cls}\n",
        encoding="utf-8",
    )
    (root / "game.py").write_text(
        f'''
from strife.engine import Game, GameOutcome, PlayerCount, game_metadata_from

@game_metadata_from(
    key="{key}",
    name="{title}",
    version="9.9.9",
    platform_version="0.0.1",
    player_count=PlayerCount(fixed=2),
    supports_replay=False,
)
class {cls}(Game):
    async def play(self, ctx):
        return GameOutcome(results={{}}, summary={{}}, description="", player_descriptions={{}})
''',
        encoding="utf-8",
    )


def _run_git(cwd: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr or result.stdout}")


def _git_plugin_repo(root: Path, key: str, *, version: str = "1.0.0") -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is required for this test")
    _write_game_package(root, key, version=version)
    _run_git(root, "init")
    _run_git(root, "config", "user.email", "test@example.com")
    _run_git(root, "config", "user.name", "Test")
    _run_git(root, "add", "-A")
    _run_git(root, "commit", "-m", f"{key} {version}")
    return root


def _manager(tmp_path: Path, **kwargs: Path) -> PluginManager:
    builtins = kwargs.get("builtins_dir", tmp_path / "empty_builtins")
    builtins.mkdir(parents=True, exist_ok=True)
    plugins = kwargs.get("plugins_dir", tmp_path / "plugins")
    plugins.mkdir(parents=True, exist_ok=True)
    games_yaml = kwargs.get("games_yaml_path", tmp_path / "games.yaml")
    if not games_yaml.exists():
        games_yaml.write_text("defaults: {}\ngames: {}\n", encoding="utf-8")
    return PluginManager(
        builtins_dir=builtins,
        plugins_dir=plugins,
        state_path=kwargs.get("state_path", tmp_path / "plugins.yaml"),
        games_yaml_path=games_yaml,
    )


def test_load_manifest_and_requirement_name(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path / "chess", "chess", deps=["chess>=1.11.2", "resvg-py>=0.3.3"])
    manifest = load_manifest(path)
    assert manifest.key == "chess"
    assert manifest.dependencies == ("chess>=1.11.2", "resvg-py>=0.3.3")
    assert distribution_name("resvg-py>=0.3.3") == "resvg-py"
    assert normalize_dist("resvg_py") == "resvg-py"


def test_invalid_key_rejected(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path / "bad", "Not Valid")
    path.write_text('key = "Not Valid"\n', encoding="utf-8")
    with pytest.raises(PluginError):
        load_manifest(path)


def test_looks_like_source(tmp_path: Path) -> None:
    assert looks_like_source("https://github.com/you/game")
    assert looks_like_source("git@github.com:you/game.git")
    assert not looks_like_source("chess")
    repo = tmp_path / "local"
    repo.mkdir()
    (repo / ".git").mkdir()
    assert looks_like_source(str(repo))


def test_orphan_distributions_skips_platform_and_shared() -> None:
    removing = ["chess>=1.11.2", "pydantic>=2"]
    remaining = ["resvg-py>=0.3.3"]
    assert orphan_distributions(removing, remaining) == ["chess"]


def test_host_protected_includes_platform_and_transitive() -> None:
    protected = host_protected_distributions()
    assert "pydantic" in protected
    assert "packaging" in protected
    try:
        importlib.metadata.distribution("discord.py")
        importlib.metadata.distribution("aiohttp")
    except importlib.metadata.PackageNotFoundError:
        return
    assert "aiohttp" in protected


def test_missing_dependencies_checks_version_specifier() -> None:
    try:
        importlib.metadata.distribution("packaging")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("packaging is not installed")
    assert missing_dependencies(["packaging>=1"]) == []
    assert missing_dependencies(["packaging>=999"]) == ["packaging>=999"]
    assert missing_dependencies(["this-dist-does-not-exist-strife-xyz"]) == [
        "this-dist-does-not-exist-strife-xyz"
    ]


def test_state_removed_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "plugins.yaml"
    state = load_state(path)
    state.mark_removed("chess")
    save_state(state)
    loaded = load_state(path)
    assert loaded.is_removed("chess")
    loaded.unmark_removed("chess")
    assert not loaded.is_removed("chess")


def test_state_installed_roundtrip(tmp_path: Path) -> None:
    from strife.plugins.state import InstalledSource

    path = tmp_path / "plugins.yaml"
    state = load_state(path)
    state.installed["sample"] = InstalledSource(
        source="https://example.com/game.git",
        ref="main",
    )
    save_state(state)
    loaded = load_state(path)
    src = loaded.installed["sample"]
    assert src.source == "https://example.com/game.git"
    assert src.ref == "main"


def test_manager_skips_removed_builtins(tmp_path: Path) -> None:
    builtins = tmp_path / "builtins"
    _write_manifest(builtins / "chess", "chess")
    _write_manifest(builtins / "tictactoe", "tictactoe")
    manager = _manager(tmp_path, builtins_dir=builtins)
    manager.state.mark_removed("chess")
    manager.save()
    keys = {record.key for record in manager.active_records()}
    assert keys == {"tictactoe"}
    manager.restore_builtin("chess")
    keys = {record.key for record in manager.active_records()}
    assert keys == {"chess", "tictactoe"}


def test_uninstall_builtin_marks_removed(tmp_path: Path) -> None:
    builtins = tmp_path / "builtins"
    _write_manifest(builtins / "chess", "chess")
    manager = _manager(tmp_path, builtins_dir=builtins)
    result = manager.uninstall("chess")
    assert result.origin == "builtin"
    assert manager.state.is_removed("chess")
    assert manager.record_for("chess") is None
    with pytest.raises(PluginError):
        manager.uninstall("chess")


def test_uninstall_does_not_touch_games_yaml(tmp_path: Path) -> None:
    builtins = tmp_path / "builtins"
    _write_manifest(builtins / "chess", "chess")
    games_yaml = tmp_path / "games.yaml"
    games_yaml.write_text(
        "defaults: {}\ngames:\n  chess:\n    enabled: true\n    turn_timeout_seconds: 120\n",
        encoding="utf-8",
    )
    manager = _manager(tmp_path, builtins_dir=builtins, games_yaml_path=games_yaml)
    manager.uninstall("chess")
    data = yaml.safe_load(games_yaml.read_text())
    assert data["games"]["chess"]["enabled"] is True
    assert data["games"]["chess"]["turn_timeout_seconds"] == 120


def test_restore_preserves_hidden_games_yaml(tmp_path: Path) -> None:
    builtins = tmp_path / "builtins"
    _write_manifest(builtins / "chess", "chess")
    games_yaml = tmp_path / "games.yaml"
    games_yaml.write_text(
        "defaults: {}\ngames:\n  chess:\n    enabled: false\n    turn_timeout_seconds: 120\n",
        encoding="utf-8",
    )
    manager = _manager(tmp_path, builtins_dir=builtins, games_yaml_path=games_yaml)
    manager.uninstall("chess")
    manager.restore_builtin("chess")
    data = yaml.safe_load(games_yaml.read_text())
    assert data["games"]["chess"]["enabled"] is False
    assert data["games"]["chess"]["turn_timeout_seconds"] == 120


def test_restore_creates_missing_games_yaml_entry(tmp_path: Path) -> None:
    builtins = tmp_path / "builtins"
    _write_manifest(builtins / "chess", "chess")
    games_yaml = tmp_path / "games.yaml"
    games_yaml.write_text("defaults: {}\ngames: {}\n", encoding="utf-8")
    manager = _manager(tmp_path, builtins_dir=builtins, games_yaml_path=games_yaml)
    manager.uninstall("chess")
    manager.restore_builtin("chess")
    data = yaml.safe_load(games_yaml.read_text())
    assert data["games"]["chess"]["enabled"] is True


def test_note_game_reenables_existing_key() -> None:
    from strife.config.games import GameConfig, GameDefaults, GamesConfig

    cfg = GamesConfig(defaults=GameDefaults(), games={"chess": GameConfig(enabled=False)})
    cfg.note_game("chess", enabled=True)
    assert cfg.for_game("chess").enabled is True
    cfg.note_game("hello", enabled=True)
    assert cfg.for_game("hello").enabled is True


def test_ensure_game_entry(tmp_path: Path) -> None:
    path = tmp_path / "games.yaml"
    path.write_text(
        "defaults: {}\ngames:\n  chess:\n    enabled: false\n    turn_timeout_seconds: 120\n"
        "  tictactoe:\n    enabled: true\n",
        encoding="utf-8",
    )
    assert ensure_game_entry(path, "hello") is True
    data = yaml.safe_load(path.read_text())
    assert data["games"]["hello"]["enabled"] is True
    assert data["games"]["tictactoe"]["enabled"] is True
    assert ensure_game_entry(path, "hello") is True
    assert ensure_game_entry(path, "chess") is False
    data = yaml.safe_load(path.read_text())
    assert data["games"]["chess"]["enabled"] is False
    assert data["games"]["chess"]["turn_timeout_seconds"] == 120


def test_set_game_enabled_reenables_and_ignores_prefix(tmp_path: Path) -> None:
    path = tmp_path / "games.yaml"
    path.write_text(
        "defaults: {}\ngames:\n  hello_world:\n    enabled: false\n    turn_timeout_seconds: 15\n"
        "  hello:\n    enabled: false\n    turn_timeout_seconds: 30\n",
        encoding="utf-8",
    )
    assert set_game_enabled(path, "hello", enabled=True) is True
    data = yaml.safe_load(path.read_text())
    assert data["games"]["hello"]["enabled"] is True
    assert data["games"]["hello"]["turn_timeout_seconds"] == 30
    assert data["games"]["hello_world"]["enabled"] is False
    assert data["games"]["hello_world"]["turn_timeout_seconds"] == 15


def test_load_external_plugin(tmp_path: Path) -> None:
    root = tmp_path / "plugins" / "hello"
    _write_game_package(root, "hello")
    manager = _manager(tmp_path)
    registry = GameRegistry()
    manager.load(registry)
    assert registry.contains("hello")
    assert registry.metadata("hello").name == "Hello"


def test_builtin_emoji_sources_are_namespaced_folders() -> None:
    from pathlib import Path

    manager = PluginManager.from_paths(config_dir=Path("config"), plugins_dir=Path("plugins"))
    sources = {key: path for key, path in manager.emoji_sources()}
    assert "tictactoe" in sources
    assert "coup" in sources
    assert (sources["tictactoe"] / "x.webp").is_file()
    assert (sources["coup"] / "duke.webp").is_file()


def test_discover_loads_builtin_tictactoe() -> None:
    registry = GameRegistry()
    registry.discover()
    assert registry.contains("tictactoe")
    meta = registry.metadata("tictactoe")
    assert meta.key == "tictactoe"
    assert meta.version == "1.0.0"
    assert meta.platform_version == "1.0.0"


def test_git_install_update_uninstall_roundtrip(tmp_path: Path) -> None:
    repo = _git_plugin_repo(tmp_path / "repo", "sample", version="1.0.0")
    manager = _manager(tmp_path)
    manifest = manager.install_from_git(str(repo))
    assert manifest.key == "sample"
    dest = tmp_path / "plugins" / "sample"
    assert dest.is_dir()
    data = yaml.safe_load((tmp_path / "games.yaml").read_text())
    assert data["games"]["sample"]["enabled"] is True
    registry = GameRegistry()
    manager.load(registry)
    assert registry.contains("sample")
    assert registry.metadata("sample").version == "1.0.0"

    _write_game_package(repo, "sample", version="1.1.0", name="Sample")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-m", "sample 1.1.0")
    updated = manager.update_from_git("sample")
    assert updated.version == "1.1.0"
    manager.reload_one(registry, "sample")
    assert registry.metadata("sample").version == "1.1.0"
    assert dest.is_dir()

    result = manager.uninstall("sample")
    assert result.origin == "installed"
    assert not dest.exists()
    assert manager.record_for("sample") is None
    data = yaml.safe_load((tmp_path / "games.yaml").read_text())
    assert data["games"]["sample"]["enabled"] is True
    with pytest.raises(PluginError):
        manager.uninstall("sample")


def test_install_rolls_back_directory_if_state_save_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_plugin_repo(tmp_path / "repo", "sample")
    manager = _manager(tmp_path)

    def boom(_state) -> None:
        raise PluginError("disk full")

    monkeypatch.setattr("strife.plugins.manager.save_state", boom)
    with pytest.raises(PluginError, match="disk full"):
        manager.install_from_git(str(repo))
    assert not (tmp_path / "plugins" / "sample").exists()
    assert "sample" not in manager.state.installed


def test_uninstall_git_fails_if_directory_cannot_be_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_plugin_repo(tmp_path / "repo", "sample")
    manager = _manager(tmp_path)
    manager.install_from_git(str(repo))
    dest = tmp_path / "plugins" / "sample"
    real_rmtree = shutil.rmtree

    def boom(path, *args, **kwargs):
        if Path(path) == dest:
            raise OSError("busy")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("strife.plugins.manager.shutil.rmtree", boom)
    with pytest.raises(PluginError, match="Could not delete"):
        manager.uninstall("sample")
    assert dest.is_dir()
    assert manager.record_for("sample") is not None


def test_plugin_emoji_stays_in_plugin_folder(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_game_package(repo, "sample")
    emoji_src = repo / "emoji"
    emoji_src.mkdir()
    (emoji_src / "token.webp").write_bytes(b"plugin-token")
    if shutil.which("git") is None:
        pytest.skip("git is required for this test")
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-m", "sample")

    manager = _manager(tmp_path)
    manager.install_from_git(str(repo))
    plugin_emoji = tmp_path / "plugins" / "sample" / "emoji" / "token.webp"
    assert plugin_emoji.read_bytes() == b"plugin-token"
    sources = manager.emoji_sources()
    assert sources == [("sample", tmp_path / "plugins" / "sample" / "emoji")]
    manager.uninstall("sample")
    assert not plugin_emoji.exists()


def test_install_rejects_existing_plugin(tmp_path: Path) -> None:
    repo = _git_plugin_repo(tmp_path / "repo", "sample")
    manager = _manager(tmp_path)
    manager.install_from_git(str(repo))
    with pytest.raises(PluginError, match="already installed"):
        manager.install_from_git(str(repo))


def test_update_rejects_key_mismatch(tmp_path: Path) -> None:
    repo = _git_plugin_repo(tmp_path / "repo", "sample")
    manager = _manager(tmp_path)
    manager.install_from_git(str(repo))
    _write_game_package(repo, "other")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-m", "wrong key")
    with pytest.raises(PluginError, match="expected 'sample'"):
        manager.update_from_git("sample")
    assert (tmp_path / "plugins" / "sample" / "plugin.toml").read_text(encoding="utf-8").startswith(
        'key = "sample"'
    )


def test_load_one_raises_when_plugin_has_no_game(tmp_path: Path) -> None:
    root = tmp_path / "plugins" / "hello"
    _write_manifest(root, "hello")
    (root / "__init__.py").write_text("", encoding="utf-8")
    manager = _manager(tmp_path)
    registry = GameRegistry()
    with pytest.raises(PluginError, match="did not export GAME"):
        manager.load_one(registry, "hello")
    assert not registry.contains("hello")
    manager.load(registry)
    assert not registry.contains("hello")


def test_reload_one_keeps_previous_class_if_new_code_is_broken(tmp_path: Path) -> None:
    repo = _git_plugin_repo(tmp_path / "repo", "sample")
    manager = _manager(tmp_path)
    manager.install_from_git(str(repo))
    registry = GameRegistry()
    manager.load_one(registry, "sample")
    previous = registry.get("sample")

    dest = tmp_path / "plugins" / "sample"
    (dest / "game.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    (dest / "__init__.py").write_text("from .game import Sample\nGAME = Sample\n", encoding="utf-8")

    with pytest.raises(PluginError, match="Failed to import plugin sample"):
        manager.reload_one(registry, "sample")
    assert registry.contains("sample")
    assert registry.get("sample") is previous


def test_git_clone_missing_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr("strife.plugins.manager.subprocess.run", boom)
    manager = _manager(tmp_path)
    with pytest.raises(PluginError, match="git is not installed"):
        manager.install_from_git("https://example.com/game.git")


def test_manifest_version_stamped_over_metadata(tmp_path: Path) -> None:
    root = tmp_path / "plugins" / "hello"
    _write_game_package(root, "hello", version="1.2.3")
    assert 'version="9.9.9"' in (root / "game.py").read_text(encoding="utf-8")
    manager = _manager(tmp_path)
    registry = GameRegistry()
    manager.load_one(registry, "hello")
    meta = registry.metadata("hello")
    assert meta.version == "1.2.3"
    assert meta.platform_version == "1.0.0"


def test_only_GAME_export_is_registered(tmp_path: Path) -> None:
    root = tmp_path / "plugins" / "hello"
    _write_game_package(root, "hello")
    init = (root / "__init__.py").read_text(encoding="utf-8")
    (root / "__init__.py").write_text(
        "from strife.games.tictactoe.game import TicTacToe\n" + init,
        encoding="utf-8",
    )
    manager = _manager(tmp_path)
    registry = GameRegistry()
    manager.load(registry)
    assert registry.contains("hello")
    assert not registry.contains("tictactoe")


def test_invalid_GAME_export_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "plugins" / "hello"
    _write_manifest(root, "hello")
    (root / "__init__.py").write_text("GAME = object\n", encoding="utf-8")
    manager = _manager(tmp_path)
    registry = GameRegistry()
    with pytest.raises(PluginError, match="GAME is not a Game subclass"):
        manager.load_one(registry, "hello")


def test_install_does_not_pip_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[object] = []

    def boom(reqs):
        called.append(list(reqs))
        raise AssertionError("pip_install should not run during live install")

    monkeypatch.setattr("strife.plugins.manager.pip_install", boom)
    repo = _git_plugin_repo(tmp_path / "repo", "sample")
    manager = _manager(tmp_path)
    manager.install_from_git(str(repo))
    assert called == []
    assert (tmp_path / "plugins" / "sample").is_dir()


def test_uninstall_does_not_pip_uninstall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[object] = []

    def boom(names):
        called.append(list(names))

    monkeypatch.setattr("strife.plugins.deps.pip_uninstall", boom)
    repo = _git_plugin_repo(tmp_path / "repo", "sample")
    manager = _manager(tmp_path)
    manager.install_from_git(str(repo))
    result = manager.uninstall("sample")
    assert result.origin == "installed"
    assert not hasattr(result, "pip_removed")
    assert called == []
