#!/usr/bin/env python3
"""Scaffold a new Strife game package."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strife.engine.platform import PLATFORM_VERSION
from strife.plugins.manifest import KEY_RE, RESERVED_KEYS

GAMES_DIR = ROOT / "strife" / "games"
GAMES_YAML = ROOT / "config" / "games.yaml"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "new_game"


def class_name(key: str) -> str:
    return "".join(part.capitalize() for part in key.split("_"))


def validate_key(key: str) -> str | None:
    if not KEY_RE.match(key):
        return (
            f"Invalid game key '{key}': must be a lowercase identifier "
            "(letter, then letters/digits/underscore, max 32 chars)"
        )
    if key in RESERVED_KEYS:
        return f"Game key '{key}' is reserved (play, strife)"
    return None


def validate_class_name(name: str) -> str | None:
    if not name.isidentifier():
        return f"Derived class name '{name}' is not a valid Python identifier"
    return None


GAME_PY = """from __future__ import annotations

from strife.engine import (
    BotRequest,
    BotSpec,
    GameContext,
    GameOutcome,
    Move,
    PlayerCount,
    PlayerOrder,
    Result,
    TurnBasedGame,
    game_metadata_from,
)
from strife.presentation.components import ActionRow, Button, ButtonStyle, LayoutView
from strife.presentation.game_ui import action_status, add_controls, game_container


@game_metadata_from(
    key="{key}",
    name="{title}",
    summary="TODO: one-line summary",
    description="TODO: longer description for the catalog.",
    tags=("dev",),
    author="Strife",
    time_estimate="5m",
    difficulty=1,
    player_count=PlayerCount(fixed=2),
    player_order=PlayerOrder.RANDOM,
    bots=(BotSpec("easy", "Random legal move"),),
)
class {cls}(TurnBasedGame):
    def __init__(self, players, settings, rng):
        super().__init__(players, settings, rng)
        self.current = 0

    def apply_move(self, move: Move) -> None:
        if move.source == "pass" and move.actor_seat is not None:
            self.current = 1 - move.actor_seat

    def replay_label(self) -> str | None:
        return f"Turn {{self.current + 1}}"

    def render_replay(self, ctx: GameContext, live_view: LayoutView | None) -> LayoutView | None:
        return live_view

    async def play(self, ctx: GameContext) -> GameOutcome:
        while True:
            seat = self.current
            move = await self.take_turn(
                ctx,
                {{"pass"}},
                lead=action_status(ctx, self.players[seat]),
                prefix_emoji="loading",
            )
            if move.source == "pass":
                opponent = 1 - seat
                return GameOutcome(
                    results={{seat: Result.WIN, opponent: Result.LOSS}},
                    summary={{"winner": seat}},
                    description=f"{{self.players[seat]}} wins",
                    player_descriptions={{seat: "Won", opponent: "Lost"}},
                )

    def render(
        self,
        ctx: GameContext,
        *,
        lead: str | None = None,
        prefix_emoji: str | None = None,
    ) -> LayoutView:
        view = LayoutView()
        container = game_container(ctx, lead=lead, prefix_emoji=prefix_emoji)
        row = ActionRow()
        row.add_button(Button(source="pass", label="Pass", style=ButtonStyle.PRIMARY))
        add_controls(container, ctx, row)
        view.add_container(container)
        return view

    async def bot_move(self, request: BotRequest) -> Move:
        return Move(actor_seat=request.seat, source="pass", args={{}})
"""

INIT_PY = """from {module}.game import {cls}

GAME = {cls}
__all__ = ["{cls}", "GAME"]
"""

PLUGIN_TOML = """key = "{key}"
version = "1.0.0"
platform_version = "{platform_version}"
dependencies = []
"""

CHANGELOG_TOML = """[[release]]
version = "1.0.0"
date = "{date}"
summary = "Initial release."
added = [
  "Pass-to-win loop and an easy bot",
]
"""


def _ensure_games_yaml(key: str) -> None:
    if not GAMES_YAML.exists():
        return
    text = GAMES_YAML.read_text(encoding="utf-8")
    marker = f"  {key}:"
    if marker in text:
        return
    addition = f"  {key}:\n    enabled: true\n"
    if not text.endswith("\n"):
        text += "\n"
    GAMES_YAML.write_text(text + addition, encoding="utf-8")
    print(f"  Added {key} to {GAMES_YAML.relative_to(ROOT)} (enabled: true)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scaffold a new Strife game")
    parser.add_argument("key", nargs="?", help="Game key (e.g. my_game)")
    parser.add_argument("title", nargs="?", help="Display name")
    args = parser.parse_args()

    if not args.key:
        parser.print_help()
        return 1

    key = slugify(args.key)
    title = args.title or key.replace("_", " ").title()
    cls = class_name(key)
    module = f"strife.games.{key}"
    game_dir = GAMES_DIR / key

    key_err = validate_key(key)
    if key_err:
        print(f"Error: {key_err}", file=sys.stderr)
        return 1
    cls_err = validate_class_name(cls)
    if cls_err:
        print(f"Error: {cls_err}", file=sys.stderr)
        return 1

    if game_dir.exists():
        print(f"Error: {game_dir} already exists", file=sys.stderr)
        return 1

    game_dir.mkdir(parents=True)

    (game_dir / "game.py").write_text(
        GAME_PY.format(
            key=key,
            title=title,
            cls=cls,
        ),
        encoding="utf-8",
    )
    (game_dir / "__init__.py").write_text(
        INIT_PY.format(module=module, cls=cls),
        encoding="utf-8",
    )
    (game_dir / "plugin.toml").write_text(
        PLUGIN_TOML.format(key=key, platform_version=PLATFORM_VERSION),
        encoding="utf-8",
    )
    from datetime import datetime

    (game_dir / "changelog.toml").write_text(
        CHANGELOG_TOML.format(date=datetime.now().astimezone().date().isoformat()),
        encoding="utf-8",
    )
    _ensure_games_yaml(key)

    print(f"Created {game_dir}")
    print(f"  Run: python scripts/run_game.py {key}")
    print(f"  Replay: python scripts/run_game.py {key} --replay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
