from __future__ import annotations

import argparse
import sys
from pathlib import Path

from strife.plugins.deps import collect_requirements, pip_install
from strife.plugins.errors import PluginError
from strife.plugins.manager import PluginManager


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m strife.plugins")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sync = sub.add_parser("sync-deps", help="pip-install declared plugin extras")
    sync.add_argument(
        "--builtins-only",
        action="store_true",
        help="Install extras for every builtin plugin.toml (image build)",
    )
    sync.add_argument("--config-dir", type=Path, default=Path("config"))
    sync.add_argument("--plugins-dir", type=Path, default=Path("plugins"))

    args = parser.parse_args(argv)
    try:
        if args.cmd == "sync-deps":
            if args.builtins_only:
                reqs = collect_requirements([_repo_root() / "strife" / "games"])
                pip_install(reqs)
            else:
                manager = PluginManager.from_paths(
                    config_dir=args.config_dir,
                    plugins_dir=args.plugins_dir,
                    repo_root=_repo_root(),
                )
                manager.ensure_dependencies()
    except PluginError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
