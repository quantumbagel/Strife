#!/usr/bin/env python3
"""Run a Strife game locally with a mock context (no Discord required)."""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strife.engine.context import ReplayContext
from strife.engine.log import LogEntryKind, reject_system_source
from strife.engine.players import Move, Player
from strife.engine.registry import GameRegistry
from strife.presentation.components import LayoutView, query_sources
from strife.presentation.emoji import EmojiResolver


class MockContext:
    """Minimal GameContext for local game iteration."""

    def __init__(
        self,
        *,
        rng: random.Random,
        players: list[Player],
        settings: dict,
        emoji: EmojiResolver,
        scripted_moves: list[tuple[int, str, dict]] | None = None,
    ) -> None:
        self.rng = rng
        self.players = players
        self.settings = settings
        self.emoji = emoji
        self.started_at = None
        self.is_replay = False
        self._scripted = list(scripted_moves or [])
        self._script_index = 0
        self._update_count = 0
        self.recorded: list[Move] = []
        self._turn_index = 0

    def is_bot(self, seat: int) -> bool:
        return self.players[seat].is_bot

    async def update(self, view: LayoutView) -> None:
        self._update_count += 1
        print(f"[update #{self._update_count}] view with {len(view.children)} top-level node(s)")

    async def request_input(
        self,
        view: LayoutView,
        *,
        actor: int,
        sources: set[str] | None = None,
        record: bool = True,
        description: str | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: str | None = None,
    ) -> Move:
        await self.update(view)
        if sources is not None:
            sources = set(sources) - query_sources(view)
        if self.players[actor].is_bot:
            print(f"[bot] seat {actor} has no bot_move in CLI; using first source")
            source = sorted(sources)[0] if sources else "pass"
            move = Move(actor_seat=actor, source=source, args={}, turn_index=self._turn_index)
            if record:
                self.recorded.append(move)
                self._turn_index += 1
            return move
        if self._script_index < len(self._scripted):
            seat, source, args = self._scripted[self._script_index]
            self._script_index += 1
            if seat != actor:
                print(f"Warning: scripted move actor {seat} != expected {actor}", file=sys.stderr)
            print(f"[input] seat {actor} -> {source} {args}")
            move = Move(actor_seat=actor, source=source, args=args, turn_index=self._turn_index)
            if record:
                self.recorded.append(move)
                self._turn_index += 1
            return move

        label = self.players[actor].display_name
        allowed = sorted(sources) if sources else ["(any)"]
        print(f"\nAction: {label} (seat {actor})")
        print(f"Allowed sources: {', '.join(allowed)}")
        while True:
            source = input("Enter move source (or 'quit'): ").strip()
            if source == "quit":
                raise SystemExit(0)
            if sources is not None and source not in sources:
                print(f"Invalid source. Choose from: {', '.join(sorted(sources))}")
                continue
            move = Move(actor_seat=actor, source=source, args={}, turn_index=self._turn_index)
            if record:
                self.recorded.append(move)
                self._turn_index += 1
            return move

    async def request_inputs(
        self,
        view: LayoutView,
        *,
        actors: set[int],
        sources: set[str] | None = None,
        until: str = "all",
        per_seat_sources: dict[int, set[str]] | None = None,
        record: bool = True,
        description: str | None = None,
        descriptions: dict[int, str] | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: str | None = None,
    ) -> dict[int, Move]:
        await self.update(view)
        results: dict[int, Move] = {}
        for seat in sorted(actors):
            seat_sources = (
                per_seat_sources.get(seat, sources) if per_seat_sources else sources
            )
            results[seat] = await self.request_input(
                view,
                actor=seat,
                sources=seat_sources,
                record=record,
                timeout_seconds=timeout_seconds,
                timeout_consequence=timeout_consequence,
            )
            if until == "any":
                break
        return results

    async def send_private(self, seat: int, view: LayoutView) -> None:
        print(f"[private -> seat {seat}] DM with {len(view.children)} top-level node(s)")

    async def record_event(self, source: str, arguments: dict) -> None:
        reject_system_source(source)
        print(f"[event] {source} {arguments}")
        self.recorded.append(
            Move(
                actor_seat=None,
                source=source,
                args=arguments,
                kind=LogEntryKind.GAME,
                turn_index=self._turn_index,
            )
        )
        self._turn_index += 1

    async def respond_query(self, view: LayoutView) -> None:
        print(f"[query] view with {len(view.children)} top-level node(s)")


def _load_emoji() -> EmojiResolver:
    from strife.config import load_app_config
    from strife.settings import get_settings

    settings = get_settings()
    config = load_app_config(settings.config_dir)
    return EmojiResolver(config.emoji)


def _parse_scripted(raw: list[str]) -> list[tuple[int, str, dict]]:
    moves: list[tuple[int, str, dict]] = []
    for item in raw:
        parts = item.split(":", 2)
        if len(parts) != 3:
            raise ValueError(f"Invalid move format '{item}'. Use seat:source:args_json")
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
    ctx = MockContext(
        rng=rng,
        players=players,
        settings=settings,
        emoji=_load_emoji().bind_game(args.game_key),
        scripted_moves=_parse_scripted(args.move) if args.move else None,
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

    if args.replay and meta.supports_replay:
        replay_game = game_cls(list(players), settings, random.Random(args.seed))
        replay_ctx = ReplayContext(
            rng=random.Random(args.seed),
            players=list(players),
            settings=settings,
            emoji=ctx.emoji,
        )
        frames = await replay_game.parse_replay(ctx.recorded, replay_ctx)
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
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
