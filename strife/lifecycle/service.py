from __future__ import annotations

import asyncio
import time

import discord

from strife.config.text import TextConfig
from strife.session.errors import SessionError
from strife.engine.inputs import timeout_auto_passes
from strife.engine.log import LogEntryKind
from strife.engine.players import Move
from strife.engine.registry import GameRegistry
from strife.engine.requests import BotRequest, FormSpec, TimeoutConsequence
from strife.lifecycle.rematch import RematchManager
from strife.lifecycle.timeout import (
    ResolvedTimeoutConsequence,
    determine_consequence,
    will_removal_end_game,
)
from strife.logging import get_logger
from strife.matchmaking.registries import SessionRegistries
from strife.matchmaking.service import LobbyService

log = get_logger("lifecycle.service")

# The scheduler ticks every 5s; a shorter warning would land with the timeout.
_MIN_WARNING_SECONDS = 10.0


def _warning_window(timeout: float, configured: float | None) -> float | None:
    """Seconds before the deadline to warn, or None when there's no room to.

    The configured warning is clamped to half the timeout so 60s turns with the
    default 30s warning still get one.
    """
    if not configured or configured <= 0 or timeout <= 0:
        return None
    warning = min(float(configured), timeout / 2)
    return warning if warning >= _MIN_WARNING_SECONDS else None


def _system_timeout_move(seat: int) -> Move:
    return Move(
        actor_seat=seat,
        source="timeout",
        args={},
        kind=LogEntryKind.SYSTEM,
    )


class LifecycleService:
    def __init__(
        self,
        bot: discord.Client,
        registries: SessionRegistries,
        registry: GameRegistry,
        lobby: LobbyService,
        text: TextConfig,
        config,
        emoji,
    ) -> None:
        self.bot = bot
        self.registries = registries
        self.registry = registry
        self.lobby = lobby
        self.text = text
        self.config = config
        self.emoji = emoji
        self.rematch = RematchManager(registries, lobby, text)
        self._task: asyncio.Task | None = None

    def register_session_end(
        self, thread_id: int, match_id: int, outcome, players
    ) -> None:
        self.rematch.start_offer(thread_id, match_id, outcome)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:
                log.exception("AFK scheduler tick failed")
            await asyncio.sleep(5)

    @staticmethod
    def _session_blocked(session) -> bool:
        return bool(session._finalized or session._ending or session._pausing)

    @staticmethod
    def _can_cancel(session) -> bool:
        if session._finalized or session._ending or session._pausing:
            return False
        task = session.task
        if task is not None and task.done():
            return False
        return True

    @staticmethod
    def _timeout_pending_matches(session, seat: int, generation: int) -> bool:
        current = session.pending.get(seat)
        return (
            current is not None
            and current.timeout_generation == generation
            and not current.future.done()
            and not session.players[seat].is_bot
        )

    async def _cancel_session(
        self, session, reason: str, forfeiter_seat: int | None = None
    ) -> bool:
        async with session.lock:
            if not self._can_cancel(session):
                return False
        return await session.cancel(reason, forfeiter_seat=forfeiter_seat)

    async def _tick(self) -> None:
        await self.rematch.expire_stale()
        now = time.monotonic()
        for session in list(self.registries.active_games.values()):
            async with session.lock:
                finalized = session._finalized
                pausing = session._pausing
                pending_snapshot = list(session.pending.items())
                last_move_at = session.last_move_at
                last_progress_at = session.last_progress_at
                task_done = session.task is not None and session.task.done()
            if finalized or pausing:
                continue
            game_cfg = self.config.games.for_game(session.game_key)
            if not pending_snapshot:
                hang = game_cfg.play_hang_seconds
                last = max(last_progress_at, last_move_at)
                if hang and hang > 0 and now - last >= hang:
                    if task_done or session._finalized:
                        continue
                    log.error(
                        "Play hung for session %s (no GameContext progress for %ss)",
                        session.id,
                        hang,
                    )
                    try:
                        await session._notify_thread(
                            self.text.get("match.session_crashed")
                        )
                        await self._cancel_session(session, "error")
                    except Exception:
                        log.exception("Failed to cancel hung session %s", session.id)
                continue
            for seat, pending in pending_snapshot:
                if session.players[seat].is_bot:
                    continue
                timeout = (
                    pending.timeout_seconds
                    if pending.timeout_seconds is not None
                    else game_cfg.turn_timeout_seconds
                )
                deadline = (
                    pending.deadline_at
                    if pending.deadline_at is not None
                    else last_move_at + timeout
                )
                remaining = deadline - now
                # First-to-act windows just close on timeout; nobody needs a ping.
                warning = (
                    None
                    if pending.until == "any"
                    else _warning_window(timeout, game_cfg.turn_warning_seconds)
                )
                if (
                    warning is not None
                    and remaining <= warning
                    and remaining > 0
                    and session._timeout_warned.get(seat) != pending.timeout_generation
                ):
                    async with session.lock:
                        session._timeout_warned[seat] = pending.timeout_generation
                    await self._send_turn_warning(session, seat, remaining)
                if remaining <= 0:
                    async with session.lock:
                        if seat in session._timeout_inflight:
                            continue
                        current = session.pending.get(seat)
                        if (
                            current is None
                            or current.timeout_generation != pending.timeout_generation
                            or current.future.done()
                        ):
                            continue
                        session._timeout_inflight.add(seat)
                    try:
                        await self._resolve_timeout(
                            session,
                            seat,
                            pending.timeout_generation,
                            pending.phase_timeout,
                        )
                    except Exception as e:
                        log.exception(
                            "Error resolving timeout for session %s (seat %s)",
                            session.id,
                            seat,
                            exc_info=e,
                        )
                        try:
                            await self._cancel_session(session, "error")
                        except Exception:
                            log.exception(
                                "Failed to cancel session %s after timeout crash",
                                session.id,
                            )
                    finally:
                        session._timeout_inflight.discard(seat)

    async def _send_turn_warning(self, session, seat: int, remaining: float) -> None:
        player = session.players[seat]
        thread = self.bot.get_channel(session.thread_id)
        if not thread:
            try:
                thread = await self.bot.fetch_channel(session.thread_id)
            except Exception:
                thread = None
        if thread is None:
            return
        seconds = max(1, int(remaining))
        try:
            await thread.send(
                self.text.get(
                    "match.turn_warning",
                    player=player.mention,
                    seconds=seconds,
                )
            )
        except Exception:
            log.exception("Failed to send turn warning for session %s", session.id)

    async def _resolve_timeout(
        self,
        session,
        seat: int,
        timeout_generation: int,
        phase_timeout: asyncio.Future[None] | None = None,
    ) -> None:
        consequence = determine_consequence(session, seat, reason="timeout")
        await self._execute_consequence(
            session,
            seat,
            consequence,
            reason="timeout",
            timeout_generation=timeout_generation,
            phase_timeout=phase_timeout,
        )

    async def forfeit(self, thread_id: int, user_id: int) -> None:
        """Forfeit ``user_id``'s seat.

        Raises ``SessionError("game_ending")`` when the match was already
        ending (or the forfeit otherwise changed nothing), so callers don't
        report a forfeit that didn't happen.
        """
        session = self.registries.get_game(thread_id)
        if session is None:
            raise SessionError("no_session")
        async with session.lock:
            if self._session_blocked(session):
                raise SessionError("game_ending")
            seat = session._seat_for_user(user_id)
            if seat is None or seat in session._removed_seats:
                raise PermissionError
            consequence = determine_consequence(session, seat, reason="forfeit")
        applied = await self._execute_consequence(
            session, seat, consequence, reason="forfeit"
        )
        if not applied:
            raise SessionError("game_ending")

    async def _execute_consequence(
        self,
        session,
        seat: int,
        consequence: ResolvedTimeoutConsequence,
        reason: str,
        timeout_generation: int | None = None,
        phase_timeout: asyncio.Future[None] | None = None,
    ) -> bool:
        """Apply ``consequence``. Returns False when the match was already ending."""
        async with session.lock:
            if self._session_blocked(session):
                return False
            if reason == "timeout":
                current = session.pending.get(seat)
                if (
                    current is None
                    or current.future.done()
                    or session.players[seat].is_bot
                    or current.timeout_generation != timeout_generation
                ):
                    return False
                generation = timeout_generation
                # Recompute under the lock so until=any vs skip is current.
                consequence = determine_consequence(session, seat, reason=reason)
            else:
                current = session.pending.get(seat)
                generation = current.timeout_generation if current is not None else -1
                if seat in session._removed_seats:
                    return False
                consequence = determine_consequence(session, seat, reason=reason)
                if consequence == ResolvedTimeoutConsequence.REMOVED:
                    session._removed_seats.add(seat)
            player = session.players[seat]
            pending = session.pending.get(seat)
            allowed_sources = pending.allowed_sources if pending else None

        if consequence == ResolvedTimeoutConsequence.PHASE_ENDS:
            return await session.expire_phase(seat, phase_timeout)

        if consequence == ResolvedTimeoutConsequence.SKIP:
            async with session.lock:
                if self._session_blocked(session):
                    return False
                if reason == "timeout" and not self._timeout_pending_matches(
                    session, seat, generation
                ):
                    return False
                session._accept_move(seat, _system_timeout_move(seat))
            await session.refresh_header()

        elif consequence == ResolvedTimeoutConsequence.AUTO_PASS:
            async with session.lock:
                if self._session_blocked(session):
                    return False
                if reason == "timeout" and not self._timeout_pending_matches(
                    session, seat, generation
                ):
                    return False
                current = session.pending.get(seat)
                sources = (
                    current.allowed_sources if current is not None else allowed_sources
                )
                if timeout_auto_passes(TimeoutConsequence.AUTO_PASS, sources):
                    session._accept_move(
                        seat, Move(actor_seat=seat, source="pass", args={})
                    )
                else:
                    session._accept_move(seat, _system_timeout_move(seat))
            await session.refresh_header()

        elif consequence == ResolvedTimeoutConsequence.STRIKE:
            async with session.lock:
                if self._session_blocked(session):
                    return False
                if reason == "timeout" and not self._timeout_pending_matches(
                    session, seat, generation
                ):
                    return False
                session.timeout_strikes[seat] = session.timeout_strikes.get(seat, 0) + 1
                session.log.system(
                    "timeout_strike",
                    {"seat": seat, "count": session.timeout_strikes[seat]},
                )
                current = session.pending.get(seat)
                sources = (
                    current.allowed_sources if current is not None else allowed_sources
                )
                if sources is not None and "pass" not in sources:
                    session._accept_move(seat, _system_timeout_move(seat))
                else:
                    session._accept_move(
                        seat, Move(actor_seat=seat, source="pass", args={})
                    )
            max_strikes = getattr(session, "turn_timeout_max_strikes", 3)

            thread = self.bot.get_channel(session.thread_id)
            if not thread:
                try:
                    thread = await self.bot.fetch_channel(session.thread_id)
                except Exception:
                    thread = None

            if thread is not None:
                try:
                    from strife.presentation.feedback import build_feedback_view

                    view = build_feedback_view(
                        icon=self.emoji.get("timer"),
                        title=self.text.get(
                            "match.strike_title", player=player.mention
                        ),
                        body=self.text.get(
                            "match.strike_body",
                            current=session.timeout_strikes[seat],
                            max=max_strikes,
                        ),
                        text=self.text,
                    )
                    compiled = session.surface.compiler.compile(
                        view,
                        resource_id=session.thread_id,
                        prefix=session.surface.prefix,
                    )
                    await thread.send(view=compiled)
                except Exception:
                    log.exception("Failed to send strike warning message")

            await session.refresh_header()

        elif consequence == ResolvedTimeoutConsequence.BOT_TAKEOVER:
            difficulty = getattr(
                session.game.metadata, "bot_takeover_difficulty", "hard"
            )
            async with session.lock:
                if self._session_blocked(session):
                    return False
                if reason == "timeout" and not self._timeout_pending_matches(
                    session, seat, generation
                ):
                    return False
                session.taken_over.add(seat)
                player.is_bot = True
                player.bot_difficulty = difficulty
                active = session.game.active_seats() - session._removed_seats
                humans_left = any(
                    not p.is_bot and p.seat in active for p in session.players
                )
                takeover_pending = session.pending.get(seat)
                if humans_left:
                    session.log.system(
                        "bot_takeover",
                        {
                            "seat": seat,
                            "reason": reason,
                            "user_id": player.user_id,
                            "display_name": player.display_name,
                            "bot_difficulty": difficulty,
                        },
                    )
            # Occupancy stays "game" until persist finishes on GAME_ENDS.
            if player.user_id:
                await self.registries.release_user(
                    player.user_id, thread_id=session.thread_id
                )
            if not humans_left:
                # Nobody is left to play against; don't let bots finish it alone.
                return await self._cancel_session(session, reason, forfeiter_seat=seat)
            try:
                allowed = takeover_pending.allowed_sources if takeover_pending else None
                sources = frozenset(allowed) if allowed is not None else None
                form = {}
                form_specs = {}
                description = None
                if takeover_pending is not None:
                    description = (
                        takeover_pending.description or takeover_pending.line_description
                    )
                    form = {
                        name: field.choices
                        for name, field in takeover_pending.form.items()
                    }
                    form_specs = {
                        name: FormSpec(
                            choices=field.choices,
                            multi=field.multi,
                            min_values=field.min_values,
                            max_values=field.max_values,
                            default=field.default,
                        )
                        for name, field in takeover_pending.form.items()
                    }
                request = BotRequest(
                    seat=seat,
                    difficulty=difficulty,
                    sources=sources,
                    description=description,
                    form=form,
                    form_fields=form_specs,
                )
                move = await session.game.bot_move(request)
                await session.force_move(seat, move)
            except Exception as e:
                log.exception(
                    "Bot takeover move failed for session %s (seat %s); skipping the turn",
                    session.id,
                    seat,
                    exc_info=e,
                )
                # Same as any failed bot move: a system timeout for that turn, play goes on.
                try:
                    await session.force_move(seat, _system_timeout_move(seat))
                except Exception:
                    log.exception("Couldn't skip the turn after a failed takeover; ending game")
                    await self._cancel_session(session, "timeout", forfeiter_seat=seat)

        elif consequence == ResolvedTimeoutConsequence.REMOVED:
            try:
                async with session.lock:
                    if self._session_blocked(session):
                        if reason == "forfeit":
                            session._removed_seats.discard(seat)
                        return False
                    if reason == "timeout":
                        if not self._timeout_pending_matches(
                            session, seat, generation
                        ):
                            return False
                        if seat in session._removed_seats:
                            return False
                        if will_removal_end_game(session, seat):
                            end_instead = True
                        else:
                            session._removed_seats.add(seat)
                            end_instead = False
                    else:
                        end_instead = False
                if end_instead:
                    return await self._cancel_session(
                        session, reason, forfeiter_seat=seat
                    )
                applied = await session.force_forfeit(seat, reason)
                if not applied:
                    return False
                if player.user_id:
                    await self.registries.release_user(
                        player.user_id, thread_id=session.thread_id
                    )
            except Exception as e:
                log.exception(
                    "Error removing player for session %s (seat %s). Ending game.",
                    session.id,
                    seat,
                    exc_info=e,
                )
                await self._cancel_session(session, "error", forfeiter_seat=seat)
            else:
                async with session.lock:
                    active = session.game.active_seats() - session._removed_seats
                    humans_left = any(
                        not p.is_bot and p.seat in active for p in session.players
                    )
                if not humans_left:
                    await self._cancel_session(session, reason, forfeiter_seat=seat)

        elif consequence == ResolvedTimeoutConsequence.GAME_ENDS:
            async with session.lock:
                if not self._can_cancel(session):
                    return False
                if reason == "timeout" and not self._timeout_pending_matches(
                    session, seat, generation
                ):
                    return False
            return await session.cancel(reason, forfeiter_seat=seat)

        return True

    async def register_rematch_vote(self, thread_id: int, user: discord.User) -> None:
        await self.rematch.vote(thread_id, user.id)
