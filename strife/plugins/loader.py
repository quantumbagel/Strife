from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

from strife.engine.game import Game
from strife.logging import get_logger
from strife.plugins.errors import PluginError
from strife.plugins.manifest import Origin

log = get_logger("plugins.loader")

_EXT_NS = "strife_ext"


def _ensure_ext_namespace() -> None:
    existing = sys.modules.get(_EXT_NS)
    if existing is not None:
        return
    pkg = types.ModuleType(_EXT_NS)
    pkg.__path__ = []  # type: ignore[attr-defined]
    pkg.__package__ = _EXT_NS
    sys.modules[_EXT_NS] = pkg


def import_builtin(folder_name: str) -> types.ModuleType:
    return importlib.import_module(f"strife.games.{folder_name}")


def import_from_path(key: str, root: Path) -> types.ModuleType:
    _ensure_ext_namespace()
    full_name = f"{_EXT_NS}.{key}"
    init_path = root / "__init__.py"
    if not init_path.is_file():
        raise PluginError(f"{root} is missing __init__.py")

    for name in list(sys.modules):
        if name == full_name or name.startswith(full_name + "."):
            sys.modules.pop(name, None)

    spec = importlib.util.spec_from_file_location(
        full_name,
        init_path,
        submodule_search_locations=[str(root)],
    )
    if spec is None or spec.loader is None:
        raise PluginError(f"Cannot import plugin {key} from {root}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


def import_plugin(origin: Origin, root: Path, key: str) -> types.ModuleType:
    if origin == "builtin":
        return import_builtin(root.name)
    return import_from_path(key, root)


def unload_plugin_modules(origin: Origin, key: str, folder_name: str | None = None) -> None:
    """Drop the plugin's modules from ``sys.modules`` so the next import is fresh."""
    if origin == "builtin":
        prefix = f"strife.games.{folder_name or key}"
    else:
        prefix = f"{_EXT_NS}.{key}"
    for name in list(sys.modules):
        if name == prefix or name.startswith(prefix + "."):
            sys.modules.pop(name, None)


def game_classes(module: types.ModuleType) -> list[type[Game]]:
    found: list[type[Game]] = []
    for attr_name in dir(module):
        obj = getattr(module, attr_name)
        if (
            isinstance(obj, type)
            and issubclass(obj, Game)
            and obj is not Game
            and hasattr(obj, "metadata")
            and obj.metadata is not None
        ):
            found.append(obj)
    return found
