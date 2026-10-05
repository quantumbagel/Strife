from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

import discord

from strife.engine.inputs import (
    check_timeout_consequence,
    make_bot_request,
    resolve_sources,
)
from strife.engine.log import LogEntryKind
from strife.engine.players import Move
from strife.engine.requests import SeatPrompt, TimeoutConsequence
from strife.presentation.components import LayoutView, default_form_values, form_fields
from strife.routing.router import InteractionInput
from strife.session.errors import SessionError
from strife.session.types import QUERY_TIMEOUT_SECONDS, PendingInput, log

_UNTIL_ANY_BOT_DELAY_SECONDS = 8.0


def _system_timeout_move(seat: int) -> Move:
    """AFK skip / failed-bot fallback. Same shape the watchdog injects for SKIP."""
    return Move(
        actor_seat=seat,
        source="timeout",
        args={},
        kind=LogEntryKind.SYSTEM,
    )


class SessionInputMixin:
    def _select_values(self, inp: InteractionInput) -> list[str]:
        if inp.values:
            return [str(v) for v in inp.values]
        if inp.args.get("values"):
            return [str(v) for v in inp.args["values"]]
        if inp.args.get("value") is not None:
            return [str(inp.args["value"])]
        return []

    def _close_until_any_window(
        self,
        phase_timeout: asyncio.Future[None] | None,
        *,
        as_timeout: bool = False,
        seats: list[int] | None = None,
    ) -> None:
        """Drop every pending seat sharing ``phase_timeout``.

        Caller holds ``self.lock``.
        """
        if phase_timeout is None:
            return
        if as_timeout and phase_timeout.done():
            return
        if as_timeout:
            if seats is None:
                seats = sorted(
                    seat
                    for seat, pending in self.pending.items()
                    if pending.phase_timeout is phase_timeout
                )
            logged = self.log.system(
                "timeout",
                {"reason": "timeout", "until": "any", "seats": seats},
            )
            self._phase_timeout_index = logged.turn_index
        for seat, pending in list(self.pending.items()):
            if pending.phase_timeout is not phase_timeout:
                continue
            if not pending.future.done():
                pending.future.cancel()
            self.pending.pop(seat, None)
        if not phase_timeout.done():
            if as_timeout:
                phase_timeout.set_result(None)
            else:
                phase_timeout.cancel()

    def _accept_move(self, seat: int, move: Move) -> Move | None:
        """Record ``move`` as this seat's answer. Caller holds ``self.lock``."""
        if self._finalized or self._ending or self._pausing:
            return None
        pending = self.pending.get(seat)
        if pending is None or pending.future.done():
            return None
        recorded = self.log.record(move)
        pending.future.set_result(recorded)
        self.pending.pop(seat, None)
        if pending.until == "any":
            self._close_until_any_window(pending.phase_timeout)
        return recorded

    async def submit(self, inp: InteractionInput) -> bool:
        """Record a click. Returns True when other humans still have to act."""
        if self.ctx._catching_up:
            raise SessionError("match_resuming")
        async with self.lock:
            seat = self._seat_for_user(inp.actor.id)
            if seat is None:
                raise SessionError("not_a_player")
            pending = self.pending.get(seat)
            if pending is None or seat not in pending.allowed_actors:
                raise SessionError("cannot_act")
            field = pending.form.get(inp.source)
            if field is not None:
                values = self._select_values(inp)
                if not values or any(value not in field.choices for value in values):
                    raise SessionError("invalid_action")
                pending.form_values[inp.source] = values if field.multi else values[0]
                return False
            if (
                pending.allowed_sources is not None
                and inp.source not in pending.allowed_sources
            ):
                raise SessionError("invalid_action")
            move = Move(
                actor_seat=seat,
                source=inp.source,
                args={**pending.form_values, **inp.args},
                created_at=datetime.now(UTC),
            )
            until = pending.until
            if self._accept_move(seat, move) is None:
                raise SessionError("cannot_act")
            # until="any" resolves on this click, so only "all" phases keep waiting.
            waiting_on_others = until == "all" and any(
                not self.players[other].is_bot for other in self.pending
            )
        if waiting_on_others:
            # Simultaneous phase: show this seat as done instead of waiting for everyone.
            await self.refresh_header()
        return waiting_on_others

    async def handle_slash_command(
        self, user_id: int, command_name: str, args: dict[str, Any]
    ) -> None:
        if self.ctx._catching_up:
            raise SessionError("match_resuming")
        async with self.lock:
            seat = self._seat_for_user(user_id)
            if seat is None:
                raise SessionError("not_a_player")
            pending = self.pending.get(seat)
            if pending is None or seat not in pending.allowed_actors:
                raise SessionError("cannot_act")
            if command_name in pending.form:
                raise SessionError("invalid_action")

            source = command_name
            move_args = {**pending.form_values, **args}

            if (
                pending.allowed_sources is not None
                and source not in pending.allowed_sources
            ):
                raise SessionError("invalid_action")

            move = Move(
                actor_seat=seat,
                source=source,
                args=move_args,
                created_at=datetime.now(UTC),
            )
            if self._accept_move(seat, move) is None:
                raise SessionError("cannot_act")

    async def handle_query(self, source: str, interaction: discord.Interaction) -> bool:
        seat = self._seat_for_user(interaction.user.id)
        if seat is None or seat in self._removed_seats:
            raise SessionError("not_a_player")
        if self.ctx._catching_up:
            # play() is still rebuilding the game; its state isn't safe to show yet.
            raise SessionError("match_resuming")

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=False)

        self.ctx._begin_query(interaction)
        try:
            handled = await asyncio.wait_for(
                self.game.handle_query(seat, source, self.ctx),
                timeout=QUERY_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001
            log.exception(
                "Error or timeout in handle_query for game %s (match_id: %s)",
                self.game_key,
                self.id,
            )
            raise SessionError("query_failed") from None
        finally:
            self.ctx._end_query()

        return handled

    async def force_move(self, seat: int, move: Move) -> None:
        async with self.lock:
            self._accept_move(seat, move)
        await self.refresh_header()

    async def force_forfeit(self, seat: int, reason: str) -> bool:
        """Hand a removed seat's pending input a ``forfeit`` and log the removal.

        If the seat was still waiting, the system row is that seat's answer.
        If the seat already answered, the removal is a standalone metadata row
        after that answer (applied at its log position, not as a second answer).
        """
        args = {"reason": reason, "removed": True}
        async with self.lock:
            if self._finalized or self._ending or self._pausing:
                return False
            pending = self.pending.get(seat)
            if pending is not None and not pending.future.done():
                recorded = self.log.system("forfeit", dict(args), actor_seat=seat)
                pending.future.set_result(recorded)
                self.pending.pop(seat, None)
                if pending.until == "any":
                    self._close_until_any_window(pending.phase_timeout)
            else:
                self.log.system("forfeit", dict(args), actor_seat=seat)
        await self.refresh_header()
        return True

    async def expire_phase(
        self, seat: int, phase_timeout: asyncio.Future[None] | None
    ) -> bool:
        """Close an ``until="any"`` window whose shared deadline passed.

        Nobody is blamed: ``request_inputs`` returns ``{}`` and logs one system
        ``timeout`` row with no actor. Returns False if the window already closed.
        """
        async with self.lock:
            pending = self.pending.get(seat)
            if (
                pending is None
                or pending.phase_timeout is None
                or pending.phase_timeout is not phase_timeout
                or pending.phase_timeout.done()
            ):
                return False
            self._close_until_any_window(pending.phase_timeout, as_timeout=True)
        return True

    def _arm_pending_deadline(self, seconds: float | None, now: float) -> float | None:
        first_live = self.ctx._first_live_prompt
        if first_live:
            self.ctx._first_live_prompt = False
        if seconds is None:
            return None
        if seconds <= 0 and first_live:
            seconds = 5.0
        return now + seconds

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
        allowed = resolve_sources(view, sources)
        check_timeout_consequence(timeout_consequence, allowed)
        if self.players[actor].is_bot:
            request = make_bot_request(
                self.players,
                view,
                actor,
                sources=sources,
                description=description,
            )
            try:
                move = await self.game.bot_move(request)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Bot move failed for seat %s", actor)
                move = _system_timeout_move(actor)
            await self._update_surface(view)
            async with self.lock:
                return self.log.record(move)

        fields = form_fields(view)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Move] = loop.create_future()
        now = time.monotonic()
        generation = self._timeout_generation.get(actor, 0) + 1
        self._timeout_generation[actor] = generation
        seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else self.turn_timeout_seconds
        )
        deadline = self._arm_pending_deadline(seconds, now)
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
                form=fields,
                form_values=default_form_values(fields),
            )
            self.last_move_at = now
            self._timeout_warned.pop(actor, None)
            self._timeout_inflight.discard(actor)
        await self._update_surface(view)
        await self.refresh_header()
        return await future

    async def _request_inputs(
        self,
        view: LayoutView,
        *,
        actors: set[int],
        sources: set[str] | None,
        until: Literal["all", "any"],
        per_seat: Mapping[int, SeatPrompt] | None = None,
        description: str | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: TimeoutConsequence | None = None,
    ) -> dict[int, Move]:
        results: dict[int, Move] = {}
        self._phase_timeout_index = None
        humans = {seat for seat in actors if not self.players[seat].is_bot}
        bots = actors - humans
        fields = form_fields(view)

        allowed_by_seat = [
            resolve_sources(view, sources, per_seat=per_seat, seat=seat)
            for seat in actors
        ]
        if allowed_by_seat:
            check_timeout_consequence(timeout_consequence, *allowed_by_seat)
        else:
            check_timeout_consequence(timeout_consequence, None)

        if until == "any" and not humans:
            # All actors are bots: pick one at random and return only that move.
            bot_seat = self._host_rng.choice(sorted(bots))
            request = make_bot_request(
                self.players,
                view,
                bot_seat,
                sources=sources,
                per_seat=per_seat,
                description=description,
            )
            try:
                move = await self.game.bot_move(request)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("until=any bot move failed")
                async with self.lock:
                    logged = self.log.system(
                        "timeout",
                        {
                            "reason": "timeout",
                            "until": "any",
                            "seats": sorted(actors),
                        },
                    )
                    self._phase_timeout_index = logged.turn_index
                return {}
            async with self.lock:
                return {bot_seat: self.log.record(move)}

        loop = asyncio.get_running_loop()
        futures: dict[int, asyncio.Future[Move]] = {}
        now = time.monotonic()
        seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else self.turn_timeout_seconds
        )
        if humans:
            deadline = self._arm_pending_deadline(seconds, now)
        else:
            deadline = now + seconds if seconds is not None else None
        # One shared "window closed" signal for first-to-act phases.
        phase_timeout: asyncio.Future[None] | None = (
            loop.create_future() if until == "any" else None
        )
        async with self.lock:
            for seat in humans:
                future: asyncio.Future[Move] = loop.create_future()
                futures[seat] = future
                seat_sources = resolve_sources(
                    view, sources, per_seat=per_seat, seat=seat
                )
                generation = self._timeout_generation.get(seat, 0) + 1
                self._timeout_generation[seat] = generation
                prompt = per_seat.get(seat) if per_seat is not None else None
                self.pending[seat] = PendingInput(
                    {seat},
                    seat_sources,
                    future,
                    description=description,
                    line_description=prompt.description if prompt is not None else None,
                    timeout_seconds=timeout_seconds,
                    timeout_consequence=timeout_consequence,
                    deadline_at=deadline,
                    timeout_generation=generation,
                    until=until,
                    phase_timeout=phase_timeout,
                    form=fields,
                    form_values=default_form_values(fields),
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
                budget = seconds if seconds is not None and seconds > 0 else 0.0
                delay = min(
                    _UNTIL_ANY_BOT_DELAY_SECONDS,
                    max(2.0, budget * 0.2),
                )

                async def _bot_contender() -> tuple[int, Move]:
                    await asyncio.sleep(delay)
                    bot_seat = self._host_rng.choice(sorted(bots))
                    request = make_bot_request(
                        self.players,
                        view,
                        bot_seat,
                        sources=sources,
                        per_seat=per_seat,
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
            done, _pending = await asyncio.wait(
                waiters, return_when=asyncio.FIRST_COMPLETED
            )
            try:
                async with self.lock:
                    for seat, future in futures.items():
                        # Not just `done`: a submit can land between the deadline
                        # firing and this lock, and was already accepted.
                        if future.done() and not future.cancelled():
                            results[seat] = future.result()
                        elif not future.done():
                            future.cancel()
                        self.pending.pop(seat, None)
                    window_open = (
                        phase_timeout is None or not phase_timeout.done()
                    ) and not results
                    if (
                        window_open
                        and bot_waiter is not None
                        and bot_waiter in done
                        and not bot_waiter.cancelled()
                    ):
                        exc = bot_waiter.exception()
                        if exc is None:
                            bot_seat, move = bot_waiter.result()
                            results[bot_seat] = self.log.record(move)
                            self._close_until_any_window(phase_timeout)
                        else:
                            log.exception(
                                "until=any bot move failed",
                                exc_info=exc,
                            )
                            self._close_until_any_window(
                                phase_timeout,
                                as_timeout=True,
                                seats=sorted(humans),
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
                per_seat=per_seat,
                description=description,
            )
            try:
                move = await self.game.bot_move(request)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Bot move failed for seat %s", seat)
                move = _system_timeout_move(seat)
            async with self.lock:
                results[seat] = self.log.record(move)

        for seat, future in futures.items():
            results[seat] = await future
        await self.refresh_header()
        return results
