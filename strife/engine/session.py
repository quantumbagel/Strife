from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import discord

from strife.config.text import TextConfig
from strife.engine.context import LiveContext
from strife.engine.game import Game
from strife.engine.players import GameOutcome, Move, Player
from strife.lifecycle.results import build_results_view
from strife.logging import get_logger
from strife.persistence.repositories import (
    FinishedMatch,
    MatchPlayer,
    MoveRecord,
    PlayerResult,
)
from strife.presentation.components import LayoutView
from strife.presentation.message import ViewSurface
from strife.routing.router import InteractionInput

log = get_logger("engine.session")


@dataclass
class PendingInput:
    allowed_actors: set[int]
    allowed_sources: set[str] | None
    future: asyncio.Future[Move]


@dataclass
class RecordedMove:
    turn_index: int
    actor_seat: int | None
    source: str
    arguments: dict


class GameSession:
    def __init__(
        self,
        *,
        thread_id: int,
        guild_id: int,
        game: Game,
        players: list[Player],
        settings: dict,
        seed: int,
        surface: ViewSurface,
        text: TextConfig,
        finalize_cb,
        game_key: str,
    ) -> None:
        self.id = thread_id
        self.thread_id = thread_id
        self.guild_id = guild_id
        self.game = game
        self.players = players
        self.settings = settings
        self.seed = seed
        self.surface = surface
        self.text = text
        self._finalize_cb = finalize_cb
        self.game_key = game_key
        self.ctx = LiveContext(self)
        self.lock = asyncio.Lock()
        self.pending: dict[int, PendingInput] = {}
        self.recorded_moves: list[RecordedMove] = []
        self.last_move_at = time.monotonic()
        self._warned = False
        self.task: asyncio.Task | None = None
        self._turn_index = 0
        self._started_at = datetime.now(timezone.utc)
        self._match_id: int | None = None
        self._match_code: str | None = None
        self._bot = None

    def set_bot(self, bot) -> None:
        self._bot = bot

    async def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            outcome = await self.game.play(self.ctx)
            await self._finalize(outcome, status="completed")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Game session crashed", extra={"match_id": self.id})
            await self._finalize(GameOutcome(results={}, summary={"error": True}), status="abandoned")

    async def submit(self, inp: InteractionInput) -> None:
        async with self.lock:
            seat = self._seat_for_user(inp.actor.id)
            if seat is None:
                raise RuntimeError("not_your_turn")
            pending = self.pending.get(seat)
            if pending is None or seat not in pending.allowed_actors:
                raise RuntimeError("not_your_turn")
            if pending.allowed_sources is not None and inp.source not in pending.allowed_sources:
                raise RuntimeError("not_your_turn")
            move = Move(actor_seat=seat, source=inp.source, args=inp.args)
            if not pending.future.done():
                pending.future.set_result(move)
            self.pending.pop(seat, None)

    async def force_move(self, seat: int, move: Move) -> None:
        async with self.lock:
            pending = self.pending.get(seat)
            if pending and not pending.future.done():
                pending.future.set_result(move)
                self.pending.pop(seat, None)

    async def cancel(self, reason: str) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
        await self._finalize(GameOutcome(results={}, summary={"reason": reason}), status="abandoned")

    def _seat_for_user(self, user_id: int) -> int | None:
        for player in self.players:
            if player.user_id == user_id and not player.is_bot:
                return player.seat
        return None

    async def _request_input(
        self, view: LayoutView, *, actor: int, sources: set[str] | None
    ) -> Move:
        if self.players[actor].is_bot:
            difficulty = self.players[actor].bot_difficulty or "medium"
            move = await self.game.bot_move(difficulty, actor)
            await self.surface.update(view)
            self._record_move(move)
            return move

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Move] = loop.create_future()
        self.pending[actor] = PendingInput({actor}, sources, future)
        self.last_move_at = time.monotonic()
        self._warned = False
        await self.surface.update(view)
        move = await future
        self._record_move(move)
        return move

    async def _request_inputs(
        self,
        view: LayoutView,
        *,
        actors: set[int],
        sources: set[str] | None,
        until: Literal["all", "any"],
    ) -> dict[int, Move]:
        results: dict[int, Move] = {}
        humans = {seat for seat in actors if not self.players[seat].is_bot}
        for seat in actors:
            if self.players[seat].is_bot:
                difficulty = self.players[seat].bot_difficulty or "medium"
                move = await self.game.bot_move(difficulty, seat)
                results[seat] = move
                self._record_move(move)

        loop = asyncio.get_running_loop()
        futures: dict[int, asyncio.Future[Move]] = {}
        for seat in humans:
            future: asyncio.Future[Move] = loop.create_future()
            futures[seat] = future
            self.pending[seat] = PendingInput({seat}, sources, future)
        self.last_move_at = time.monotonic()
        self._warned = False
        await self.surface.update(view)

        if until == "any":
            done, _ = await asyncio.wait(futures.values(), return_when=asyncio.FIRST_COMPLETED)
            for seat, future in futures.items():
                if future in done:
                    move = future.result()
                    results[seat] = move
                    self._record_move(move)
                    if not future.done():
                        future.cancel()
                    self.pending.pop(seat, None)
            return results

        for seat, future in futures.items():
            move = await future
            results[seat] = move
            self._record_move(move)
            self.pending.pop(seat, None)
        return results

    async def _send_private(self, seat: int, view: LayoutView) -> None:
        player = self.players[seat]
        if player.user_id is None or self._bot is None:
            return
        user = self._bot.get_user(player.user_id) or await self._bot.fetch_user(player.user_id)
        compiled_surface = ViewSurface(
            self.surface._compiler, prefix=self.surface._prefix, resource_id=self.surface._resource_id
        )
        try:
            dm = user.dm_channel or await user.create_dm()
            await compiled_surface.send(dm, view)
        except discord.HTTPException:
            pass

    def _record_move(self, move: Move) -> None:
        self.recorded_moves.append(
            RecordedMove(
                turn_index=self._turn_index,
                actor_seat=move.actor_seat,
                source=move.source,
                arguments=move.args,
            )
        )
        self._turn_index += 1
        self.last_move_at = time.monotonic()

    async def _finalize(self, outcome: GameOutcome, *, status: str) -> None:
        from strife.presentation.components import LayoutView as LV

        board = LV()
        await self.surface.disable_all(board)
        finished = FinishedMatch(
            code=None,
            game_key=self.game_key,
            guild_id=self.guild_id,
            thread_id=self.thread_id,
            seed=self.seed,
            settings=self.settings,
            status=status,
            outcome=outcome.summary,
            total_turns=len(self.recorded_moves),
            started_at=self._started_at,
            ended_at=datetime.now(timezone.utc),
            players=[
                MatchPlayer(
                    seat_index=p.seat,
                    user_id=p.user_id,
                    is_bot=p.is_bot,
                    bot_difficulty=p.bot_difficulty,
                    display_name=p.display_name,
                    role_key=p.role_key,
                    result=outcome.results.get(p.seat),
                )
                for p in self.players
            ],
            moves=[
                MoveRecord(
                    turn_index=m.turn_index,
                    actor_seat=m.actor_seat,
                    source=m.source,
                    arguments=m.arguments,
                )
                for m in self.recorded_moves
            ],
        )
        match_id, code = await self._finalize_cb(finished, outcome)
        self._match_id = match_id
        self._match_code = code
        results_view = build_results_view(
            game_name=self.game.metadata.name,
            outcome=outcome,
            players=self.players,
            thread_id=self.thread_id,
            match_id=match_id,
            owner_id=next((p.user_id for p in self.players if p.user_id), 0),
            accent=self.game.metadata.accent_color or 0x5865F2,
            text=self.text,
        )
        await self.surface.update(results_view)
        await self._finalize_cb.session_complete(self)  # type: ignore[attr-defined]
