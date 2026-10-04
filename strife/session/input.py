from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Literal

import discord

from strife.engine.errors import SessionError
from strife.engine.inputs import make_bot_request, resolve_sources
from strife.engine.requests import TimeoutConsequence
from strife.engine.log import LogEntryKind
from strife.engine.players import Move
from strife.presentation.components import LayoutView
from strife.routing.router import InteractionInput
from strife.session.types import PendingInput, QUERY_TIMEOUT_SECONDS, log

_UNTIL_ANY_BOT_DELAY_SECONDS = 8.0


class SessionInputMixin:
    async def submit(self, inp: InteractionInput) -> bool:
        """Record a click. Returns True when other humans still have to act."""
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
            # until="any" resolves on this click, so only "all" phases keep waiting.
            waiting_on_others = pending.until == "all" and any(
                not self.players[other].is_bot for other in self.pending
            )
        if waiting_on_others:
            # Simultaneous phase: show this seat as done instead of waiting for everyone.
            await self.refresh_header()
        return waiting_on_others


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


    async def force_forfeit(self, seat: int, reason: str) -> None:
        """Hand a removed seat's pending input a ``forfeit`` and log the removal.

        The system row is written exactly once: by the request path when the seat
        was waiting on a recorded input, otherwise here. ``removed`` marks it as a
        mid-match removal so replays keep going past it.
        """
        args = {"reason": reason, "removed": True}
        async with self.lock:
            pending = self.pending.get(seat)
            delivered = pending is not None and not pending.future.done()
            if delivered:
                pending.future.set_result(
                    Move(
                        actor_seat=seat,
                        source="forfeit",
                        args=dict(args),
                        kind=LogEntryKind.SYSTEM,
                        created_at=datetime.now(timezone.utc),
                    )
                )
                self.pending.pop(seat, None)
            if not delivered:
                self.log.system("forfeit", args, actor_seat=seat)
        await self.refresh_header()


    async def expire_phase(self, seat: int) -> bool:
        """Close an ``until="any"`` window whose shared deadline passed.

        Nobody is blamed: ``request_inputs`` returns ``{}`` and logs one system
        ``timeout`` row with no actor. Returns False if the window already closed.
        """
        async with self.lock:
            pending = self.pending.get(seat)
            if (
                pending is None
                or pending.phase_timeout is None
                or pending.phase_timeout.done()
            ):
                return False
            pending.phase_timeout.set_result(None)
        return True


    async def _request_input(
        self,
        view: LayoutView,
        *,
        actor: int,
        sources: set[str] | None,
        description: str | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: TimeoutConsequence | None = None,
    ) -> Move:
        if self.players[actor].is_bot:
            request = make_bot_request(
                self.players,
                view,
                actor,
                sources=sources,
                description=description,
            )
            move = await self.game.bot_move(request)
            await self._update_surface(view)
            async with self.lock:
                self.log.record(move)
            return move

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Move] = loop.create_future()
        now = time.monotonic()
        generation = self._timeout_generation.get(actor, 0) + 1
        self._timeout_generation[actor] = generation
        seconds = timeout_seconds if timeout_seconds is not None else self.turn_timeout_seconds
        deadline = now + seconds
        allowed = resolve_sources(view, sources)
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
        async with self.lock:
            self.log.record(move)
        return move


    async def _request_inputs(
        self,
        view: LayoutView,
        *,
        actors: set[int],
        sources: set[str] | None,
        until: Literal["all", "any"],
        per_seat_sources: dict[int, set[str]] | None = None,
        description: str | None = None,
        descriptions: dict[int, str] | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: TimeoutConsequence | None = None,
    ) -> dict[int, Move]:
        results: dict[int, Move] = {}
        humans = {seat for seat in actors if not self.players[seat].is_bot}
        bots = actors - humans

        if until == "any" and not humans:
            # All actors are bots: pick one at random and return only that move.
            bot_seat = self._host_rng.choice(sorted(bots))
            request = make_bot_request(
                self.players,
                view,
                bot_seat,
                sources=sources,
                description=description,
            )
            move = await self.game.bot_move(request)
            self.log.record(move)
            return {bot_seat: move}

        loop = asyncio.get_running_loop()
        futures: dict[int, asyncio.Future[Move]] = {}
        now = time.monotonic()
        seconds = timeout_seconds if timeout_seconds is not None else self.turn_timeout_seconds
        deadline = now + seconds
        # One shared "window closed" signal for first-to-act phases.
        phase_timeout: asyncio.Future[None] | None = (
            loop.create_future() if until == "any" else None
        )
        async with self.lock:
            for seat in humans:
                future: asyncio.Future[Move] = loop.create_future()
                futures[seat] = future
                seat_sources = resolve_sources(
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
                    until=until,
                    phase_timeout=phase_timeout,
                )
                self._timeout_inflight.discard(seat)
            self.last_move_at = now
            for seat in humans:
                self._timeout_warned.pop(seat, None)
        await self._update_surface(view)
        await self.refresh_header()

        if until == "any":
            bot_waiter: asyncio.Task | None = None
            if bots:
                delay = min(_UNTIL_ANY_BOT_DELAY_SECONDS, max(2.0, seconds * 0.2))

                async def _bot_contender() -> tuple[int, Move]:
                    await asyncio.sleep(delay)
                    bot_seat = self._host_rng.choice(sorted(bots))
                    request = make_bot_request(
                        self.players,
                        view,
                        bot_seat,
                        sources=sources,
                        per_seat_sources=per_seat_sources,
                        description=description,
                    )
                    move = await self.game.bot_move(request)
                    return bot_seat, move

                bot_waiter = asyncio.create_task(_bot_contender())

            waiters: list[asyncio.Future] = list(futures.values())
            if bot_waiter is not None:
                waiters.append(bot_waiter)
            if not waiters:
                return results
            if phase_timeout is not None:
                waiters.append(phase_timeout)
            done, _pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            try:
                async with self.lock:
                    for seat, future in futures.items():
                        # Not just `done`: a submit can land between the deadline
                        # firing and this lock, and was already accepted.
                        if future.done() and not future.cancelled():
                            move = future.result()
                            results[seat] = move
                            self.log.record(move)
                        elif not future.done():
                            future.cancel()
                        self.pending.pop(seat, None)
                    if (
                        not results
                        and bot_waiter is not None
                        and bot_waiter in done
                        and not bot_waiter.cancelled()
                    ):
                        exc = bot_waiter.exception()
                        if exc is None:
                            bot_seat, move = bot_waiter.result()
                            results[bot_seat] = move
                            self.log.record(move)
                        else:
                            log.exception(
                                "until=any bot move failed",
                                exc_info=exc,
                            )
                    if not results and phase_timeout is not None and phase_timeout in done:
                        # The window closed with nobody acting; no seat is to blame.
                        self.log.system(
                            "timeout",
                            {"reason": "timeout", "until": "any", "seats": sorted(humans)},
                        )
            finally:
                if phase_timeout is not None and not phase_timeout.done():
                    phase_timeout.cancel()
                if bot_waiter is not None and not bot_waiter.done():
                    bot_waiter.cancel()
                    try:
                        await bot_waiter
                    except asyncio.CancelledError:
                        pass
            await self.refresh_header()
            return results

        # until == "all": collect bot moves alongside human futures.
        for seat in bots:
            request = make_bot_request(
                self.players,
                view,
                seat,
                sources=sources,
                per_seat_sources=per_seat_sources,
                description=(
                    descriptions.get(seat) if descriptions is not None else description
                ),
            )
            move = await self.game.bot_move(request)
            results[seat] = move
            async with self.lock:
                self.log.record(move)

        for seat, future in futures.items():
            move = await future
            async with self.lock:
                results[seat] = move
                self.log.record(move)
                self.pending.pop(seat, None)
        await self.refresh_header()
        return results
