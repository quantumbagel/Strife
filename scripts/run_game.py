#!/usr/bin/env python3
"""Run a Strife game locally with a mock context (no Discord required)."""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strife.engine.players import Player
from strife.engine.registry import GameRegistry
from strife.engine.replay import run_replay
from strife.engine.testing import MockContext
from strife.presentation.emoji import EmojiResolver
from strife.session.types import guard_bot_move


def _load_emoji() -> EmojiResolver:
    # Read config straight from the repo; Settings would demand a bot token and database URL.
    from strife.config.emoji import load_emoji_config

    return EmojiResolver(load_emoji_config(ROOT / "config" / "emoji.yaml"))


def _parse_scripted(raw: list[str]) -> list[tuple[int | None, str, dict]]:
    moves: list[tuple[int | None, str, dict]] = []
    for item in raw:
        parts = item.split(":", 2)
        if len(parts) != 3:
            raise ValueError(f"Invalid move format '{item}'. Use seat:source:args_json")
        if parts[0] in ("", "None", "none", "null"):
            seat: int | None = None
        else:
            seat = int(parts[0])
        source = parts[1]
        args = {} if not parts[2] else __import__("json").loads(parts[2])
        moves.append((seat, source, args))
    return moves


async def _run(args: argparse.Namespace) -> None:
    registry = GameRegistry()
    registry.discover()
    game_cls = registry.get(args.game_key)
    meta = game_cls.metadata

    player_count = meta.player_count.fixed or meta.player_count.min_players or 2
    players = [
        Player(seat=i, user_id=1000 + i, display_name=f"Player {i + 1}")
        for i in range(player_count)
    ]
    if meta.supports_bots and args.bot_seat is not None:
        players[args.bot_seat].is_bot = True
        players[args.bot_seat].bot_difficulty = args.bot_difficulty

    settings = {opt.key: opt.default for opt in meta.settings}
    rng = random.Random(args.seed)
    game = game_cls(players, settings, rng)
    guard_bot_move(game)
    ctx = MockContext(
        game,
        emoji=_load_emoji().bind_game(args.game_key),
        script=_parse_scripted(args.move) if args.move else None,
        interactive=True,
        verbose=True,
    )

    print(
        f"Running {meta.name} ({meta.key}) v{meta.version} "
        f"[platform {meta.platform_version}] with seed {args.seed}"
    )
    outcome = await game.play(ctx)  # type: ignore[arg-type]
    print("\n=== Game Over ===")
    print(outcome.description)
    print("Results:", outcome.results)
    if outcome.summary:
        print("Summary:", outcome.summary)

    if args.replay:
        replay_players = [
            Player(
                seat=p.seat,
                user_id=p.user_id,
                display_name=p.display_name,
                is_bot=p.is_bot,
                bot_difficulty=p.bot_difficulty,
            )
            for p in players
        ]
        replay_game = game_cls(replay_players, settings, random.Random(args.seed))
        frames = await run_replay(
            replay_game,
            ctx.recorded,
            emoji=ctx.emoji,
            started_at=None,
        )
        print(f"\n=== Replay ({len(frames)} frame(s)) ===")
        for frame in frames:
            print(f"  [{frame.index}] {frame.turn_label}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Strife game locally")
    parser.add_argument("game_key", help="Registered game key (e.g. tictactoe)")
    parser.add_argument("--seed", type=int, default=1, help="RNG seed")
    parser.add_argument("--bot-seat", type=int, default=None, help="Seat index to mark as bot")
    parser.add_argument("--bot-difficulty", default="easy", help="Bot difficulty label")
    parser.add_argument(
        "--move",
        action="append",
        default=[],
        help="Scripted move as seat:source:args_json (repeatable)",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="After play(), rebuild replay frames from the recorded log",
    )
    args = parser.parse_args()
    # Plugin discovery resolves config/ and plugins/ against the working directory.
    os.chdir(ROOT)
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
