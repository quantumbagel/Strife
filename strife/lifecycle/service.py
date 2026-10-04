from __future__ import annotations

import asyncio
import time

import discord

from strife.config.text import TextConfig
from strife.session.errors import SessionError
from strife.engine.log import LogEntryKind
from strife.engine.players import Move
from strife.engine.registry import GameRegistry
from strife.lifecycle.rematch import RematchManager
from strife.engine.requests import BotRequest
from strife.lifecycle.timeout import ResolvedTimeoutConsequence, determine_consequence
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

    def register_session_end(self, thread_id: int, match_id: int, outcome, players) -> None:
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

    async def _tick(self) -> None:
        await self.rematch.expire_stale()
        now = time.monotonic()
        for session in list(self.registries.active_games.values()):
            async with session.lock:
                finalized = session._finalized
                pending_snapshot = list(session.pending.items())
                last_move_at = session.last_move_at
                last_progress_at = session.last_progress_at
            if finalized:
                continue
            game_cfg = self.config.games.for_game(session.game_key)
            if not pending_snapshot:
                hang = game_cfg.play_hang_seconds
                last = max(last_progress_at, last_move_at)
                if hang and hang > 0 and now - last >= hang:
                    log.error(
                        "Play hung for session %s (no GameContext progress for %ss)",
                        session.id,
                        hang,
                    )
                    try:
                        await session._notify_thread(self.text.get("match.session_crashed"))
                        await session.cancel("error")
                    except Exception:
                        log.exception("Failed to cancel hung session %s", session.id)
                continue
            for seat, pending in pending_snapshot:
                if session.players[seat].is_bot:
                    continue
                timeout = pending.timeout_seconds if pending.timeout_seconds is not None else game_cfg.turn_timeout_seconds
                deadline = pending.deadline_at if pending.deadline_at is not None else last_move_at + timeout
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
                        if current is None or current.timeout_generation != pending.timeout_generation:
                            continue
                        session._timeout_inflight.add(seat)
                    try:
                        await self._resolve_timeout(session, seat)
                    except Exception as e:
                        log.exception(
                            "Error resolving timeout for session %s (seat %s)",
                            session.id,
                            seat,
                            exc_info=e,
                        )
                        try:
                            await session.cancel("error")
                        except Exception:
                            log.exception("Failed to cancel session %s after timeout crash", session.id)
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

    async def _resolve_timeout(self, session, seat: int) -> None:
        consequence = determine_consequence(session, seat, reason="timeout")
        await self._execute_consequence(session, seat, consequence, reason="timeout")

    async def forfeit(self, thread_id: int, user_id: int) -> None:
        """Forfeit ``user_id``'s seat.

        Raises ``SessionError("game_ending")`` when the match was already
        ending (or the forfeit otherwise changed nothing), so callers don't
        report a forfeit that didn't happen.
        """
        session = self.registries.get_game(thread_id)
        if session is None:
            raise SessionError("no_session")
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
        self, session, seat: int, consequence: ResolvedTimeoutConsequence, reason: str
    ) -> bool:
        """Apply ``consequence``. Returns False when the match was already ending."""
        async with session.lock:
            if session._finalized or session._ending:
                return False
            if reason == "timeout":
                current = session.pending.get(seat)
                if current is None or session.players[seat].is_bot:
                    return False
            player = session.players[seat]
            pending = session.pending.get(seat)

        if consequence == ResolvedTimeoutConsequence.PHASE_ENDS:
            return await session.expire_phase(seat)

        # Occupancy stays "game" until persist finishes on GAME_ENDS. Release
        # immediately when the seat leaves a still-running match (removed here,
        # or handed to a bot below) so the player can start something else.
        if consequence == ResolvedTimeoutConsequence.REMOVED and player.user_id:
            await self.registries.release_user(
                player.user_id, thread_id=session.thread_id
            )

        if consequence == ResolvedTimeoutConsequence.SKIP:
            await session.force_move(
                seat,
                Move(actor_seat=seat, source="timeout", args={}, kind=LogEntryKind.SYSTEM),
            )

        elif consequence == ResolvedTimeoutConsequence.AUTO_PASS:
            allowed = pending.allowed_sources if pending else None
            if allowed is not None and "pass" not in allowed:
                await session.force_move(
                    seat,
                    Move(actor_seat=seat, source="timeout", args={}, kind=LogEntryKind.SYSTEM),
                )
            else:
                await session.force_move(seat, Move(actor_seat=seat, source="pass", args={}))

        elif consequence == ResolvedTimeoutConsequence.STRIKE:
            session.timeout_strikes[seat] = session.timeout_strikes.get(seat, 0) + 1
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
                        title=self.text.get("match.strike_title", player=player.mention),
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

            if pending and pending.allowed_sources is not None and "pass" not in pending.allowed_sources:
                await session.force_move(
                    seat,
                    Move(actor_seat=seat, source="timeout", args={}, kind=LogEntryKind.SYSTEM),
                )
            else:
                await session.force_move(seat, Move(actor_seat=seat, source="pass", args={}))

        elif consequence == ResolvedTimeoutConsequence.BOT_TAKEOVER:
            difficulty = getattr(session.game.metadata, "bot_takeover_difficulty", "hard")
            async with session.lock:
                if session._finalized or session._ending:
                    return False
                if reason == "timeout" and session.pending.get(seat) is None:
                    return False
                session.taken_over.add(seat)
                player.is_bot = True
                player.bot_difficulty = difficulty
                active = session.game.active_seats() - session._removed_seats
                humans_left = any(
                    not p.is_bot and p.seat in active for p in session.players
                )
            if player.user_id:
                await self.registries.release_user(
                    player.user_id, thread_id=session.thread_id
                )
            if not humans_left:
                # Nobody is left to play against; don't let bots finish it alone.
                await session.cancel(reason, forfeiter_seat=seat)
                return True
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
            try:
                allowed = pending.allowed_sources if pending else None
                sources = frozenset(allowed) if allowed is not None else None
                form = {}
                description = None
                if pending is not None:
                    description = pending.description or pending.line_description
                    form = {name: field.choices for name, field in pending.form.items()}
                request = BotRequest(
                    seat=seat,
                    difficulty=difficulty,
                    sources=sources,
                    description=description,
                    form=form,
                )
                move = await session.game.bot_move(request)
                await session.force_move(seat, move)
            except Exception as e:
                log.exception(
                    "Error executing bot takeover for session %s (seat %s). Ending game.",
                    session.id,
                    seat,
                    exc_info=e,
                )
                await session.cancel("error", forfeiter_seat=seat)

        elif consequence == ResolvedTimeoutConsequence.REMOVED:
            try:
                session._removed_seats.add(seat)
                # Logs a system "forfeit" row even when the seat wasn't waiting
                # on a pending input (request path logs the forfeit row).
                await session.force_forfeit(seat, reason)
            except Exception as e:
                log.exception(
                    "Error removing player for session %s (seat %s). Ending game.",
                    session.id,
                    seat,
                    exc_info=e,
                )
                await session.cancel("error", forfeiter_seat=seat)

        elif consequence == ResolvedTimeoutConsequence.GAME_ENDS:
            return await session.cancel(reason, forfeiter_seat=seat)

        return True

    async def register_rematch_vote(self, thread_id: int, user: discord.User) -> None:
        await self.rematch.vote(thread_id, user.id)
