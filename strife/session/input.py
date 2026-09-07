from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Literal

import discord

from strife.engine.errors import SessionError
from strife.engine.players import Move
from strife.presentation.components import LayoutView, move_sources, query_sources
from strife.routing.router import InteractionInput
from strife.session.types import PendingInput, QUERY_TIMEOUT_SECONDS, log


class SessionInputMixin:
    def _resolve_sources(
        self,
        view: LayoutView,
        sources: set[str] | None,
        *,
        per_seat_sources: dict[int, set[str]] | None = None,
        seat: int | None = None,
    ) -> set[str] | None:
        queries = query_sources(view)
        if per_seat_sources is not None and seat is not None:
            base = per_seat_sources.get(seat, sources)
        else:
            base = sources
        if base is None:
            allowed = move_sources(view)
        else:
            allowed = set(base)
        return allowed - queries if allowed else allowed


    async def submit(self, inp: InteractionInput) -> None:
        async with self.lock:
            seat = self._seat_for_user(inp.actor.id)
            if seat is None:
                raise SessionError("not_a_player")
            pending = self.pending.get(seat)
            if pending is None or seat not in pending.allowed_actors:
                raise SessionError("cannot_act")
            if pending.allowed_sources is not None and inp.source not in pending.allowed_sources:
                raise SessionError("invalid_action")
            move = Move(
                actor_seat=seat,
                source=inp.source,
                args=inp.args,
                created_at=datetime.now(timezone.utc),
            )
            if not pending.future.done():
                pending.future.set_result(move)
            self.pending.pop(seat, None)


    async def handle_slash_command(self, user_id: int, command_name: str, args: dict[str, Any]) -> None:
        async with self.lock:
            seat = self._seat_for_user(user_id)
            if seat is None:
                raise SessionError("not_a_player")
            pending = self.pending.get(seat)
            if pending is None or seat not in pending.allowed_actors:
                raise SessionError("cannot_act")

            source = command_name
            move_args = args

            if pending.allowed_sources is not None and source not in pending.allowed_sources:
                raise SessionError("invalid_action")

            move = Move(
                actor_seat=seat,
                source=source,
                args=move_args,
                created_at=datetime.now(timezone.utc),
            )
            if not pending.future.done():
                pending.future.set_result(move)
            self.pending.pop(seat, None)


    async def handle_query(self, source: str, interaction: discord.Interaction) -> bool:
        seat = self._seat_for_user(interaction.user.id)
        if seat is None:
            raise SessionError("not_a_player")

        self.ctx._begin_query(interaction)
        try:
            handled = await asyncio.wait_for(
                self.game.handle_query(seat, source, self.ctx),
                timeout=QUERY_TIMEOUT_SECONDS,
            )
        except Exception:
            log.exception(
                "Error or timeout in handle_query for game %s (match_id: %s)",
                self.game_key,
                self.id,
            )
            raise SessionError("query_failed") from None
        finally:
            self.ctx._end_query()

        if handled and not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        return handled


    async def force_move(self, seat: int, move: Move) -> None:
        async with self.lock:
            pending = self.pending.get(seat)
            if pending and not pending.future.done():
                pending.future.set_result(move)
                self.pending.pop(seat, None)
        await self.refresh_header()


    async def _request_input(
        self,
        view: LayoutView,
        *,
        actor: int,
        sources: set[str] | None,
        record: bool = True,
        description: str | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: str | None = None,
    ) -> Move:
        allowed = self._resolve_sources(view, sources)
        if self.players[actor].is_bot:
            difficulty = self.players[actor].bot_difficulty or "medium"
            move = await self.game.bot_move(difficulty, actor)
            await self._update_surface(view)
            if record:
                async with self.lock:
                    self._record_move(move)
            return move

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Move] = loop.create_future()
        now = time.monotonic()
        generation = self._timeout_generation.get(actor, 0) + 1
        self._timeout_generation[actor] = generation
        seconds = timeout_seconds if timeout_seconds is not None else self.turn_timeout_seconds
        deadline = now + seconds
        async with self.lock:
            self.pending[actor] = PendingInput(
                {actor},
                allowed,
                future,
                description=description,
                timeout_seconds=timeout_seconds,
                timeout_consequence=timeout_consequence,
                deadline_at=deadline,
                timeout_generation=generation,
            )
            self.last_move_at = now
            self._timeout_warned.pop(actor, None)
            self._timeout_inflight.discard(actor)
        await self._update_surface(view)
        await self.refresh_header()
        move = await future
        if record:
            async with self.lock:
                self._record_move(move)
        return move


    async def _request_inputs(
        self,
        view: LayoutView,
        *,
        actors: set[int],
        sources: set[str] | None,
        until: Literal["all", "any"],
        per_seat_sources: dict[int, set[str]] | None = None,
        record: bool = True,
        description: str | None = None,
        descriptions: dict[int, str] | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: str | None = None,
    ) -> dict[int, Move]:
        results: dict[int, Move] = {}
        humans = {seat for seat in actors if not self.players[seat].is_bot}
        bots = actors - humans

        if until == "any" and not humans:
            # All actors are bots: pick one at random and return only that move.
            bot_seat = self.game.rng.choice(sorted(bots))
            difficulty = self.players[bot_seat].bot_difficulty or "medium"
            move = await self.game.bot_move(difficulty, bot_seat)
            if record:
                self._record_move(move)
            return {bot_seat: move}

        loop = asyncio.get_running_loop()
        futures: dict[int, asyncio.Future[Move]] = {}
        now = time.monotonic()
        seconds = timeout_seconds if timeout_seconds is not None else self.turn_timeout_seconds
        deadline = now + seconds
        async with self.lock:
            for seat in humans:
                future: asyncio.Future[Move] = loop.create_future()
                futures[seat] = future
                seat_sources = self._resolve_sources(
                    view, sources, per_seat_sources=per_seat_sources, seat=seat
                )
                generation = self._timeout_generation.get(seat, 0) + 1
                self._timeout_generation[seat] = generation
                self.pending[seat] = PendingInput(
                    {seat},
                    seat_sources,
                    future,
                    description=description,
                    line_description=(
                        descriptions.get(seat) if descriptions is not None else None
                    ),
                    timeout_seconds=timeout_seconds,
                    timeout_consequence=timeout_consequence,
                    deadline_at=deadline,
                    timeout_generation=generation,
                )
                self._timeout_inflight.discard(seat)
            self.last_move_at = now
            for seat in humans:
                self._timeout_warned.pop(seat, None)
        await self._update_surface(view)
        await self.refresh_header()

        if until == "any":
            if not futures:
                return results
            done, pending_futures = await asyncio.wait(
                futures.values(), return_when=asyncio.FIRST_COMPLETED
            )
            async with self.lock:
                for seat, future in futures.items():
                    if future in done:
                        move = future.result()
                        results[seat] = move
                        if record:
                            self._record_move(move)
                    elif not future.done():
                        future.cancel()
                    self.pending.pop(seat, None)
            await self.refresh_header()
            return results

        # until == "all": collect bot moves alongside human futures.
        for seat in bots:
            difficulty = self.players[seat].bot_difficulty or "medium"
            move = await self.game.bot_move(difficulty, seat)
            results[seat] = move
            if record:
                async with self.lock:
                    self._record_move(move)

        for seat, future in futures.items():
            move = await future
            async with self.lock:
                results[seat] = move
                if record:
                    self._record_move(move)
                self.pending.pop(seat, None)
        await self.refresh_header()
        return results
