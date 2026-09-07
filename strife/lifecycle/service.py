from __future__ import annotations

import asyncio
import time

import discord

from strife.config.text import TextConfig
from strife.engine.errors import SessionError
from strife.engine.log import LogEntryKind
from strife.engine.players import Move
from strife.engine.registry import GameRegistry
from strife.lifecycle.rematch import RematchManager
from strife.lifecycle.timeout import determine_consequence, TimeoutConsequence
from strife.logging import get_logger
from strife.matchmaking.registries import SessionRegistries
from strife.matchmaking.service import LobbyService

log = get_logger("lifecycle.service")


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
        humans = [
            p.user_id
            for p in players
            if p.user_id and not p.is_bot and not p.taken_over
        ]
        self.rematch.start_offer(thread_id, set(humans), match_id, outcome)

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
                idle = timeout - remaining
                warning = game_cfg.turn_warning_seconds
                if (
                    warning
                    and warning > 0
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
        session = self.registries.get_game(thread_id)
        if session is None:
            raise SessionError("no_session")
        seat = session._seat_for_user(user_id)
        if seat is None:
            raise PermissionError

        consequence = determine_consequence(session, seat, reason="forfeit")
        await self._execute_consequence(session, seat, consequence, reason="forfeit")

    async def _execute_consequence(
        self, session, seat: int, consequence: TimeoutConsequence, reason: str
    ) -> None:
        async with session.lock:
            if session._finalized or session._ending:
                return
            if reason == "timeout":
                current = session.pending.get(seat)
                if current is None or session.players[seat].is_bot:
                    return
            player = session.players[seat]
            pending = session.pending.get(seat)

        # Occupancy stays "game" through bot takeover and until persist
        # finishes on GAME_ENDS. Release immediately only when the seat
        # actually leaves a still-running match.
        if consequence == TimeoutConsequence.REMOVED and player.user_id:
            await self.registries.release_user(player.user_id)

        if consequence == TimeoutConsequence.SKIP:
            await session.force_move(
                seat,
                Move(actor_seat=seat, source="timeout", args={}, kind=LogEntryKind.SYSTEM),
            )

        elif consequence == TimeoutConsequence.AUTO_PASS:
            await session.force_move(seat, Move(actor_seat=seat, source="pass", args={}))

        elif consequence == TimeoutConsequence.STRIKE:
            player.timeout_strikes += 1
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
                            current=player.timeout_strikes,
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

        elif consequence == TimeoutConsequence.BOT_TAKEOVER:
            difficulty = getattr(session.game.metadata, "bot_takeover_difficulty", "hard")
            async with session.lock:
                if session._finalized or session._ending:
                    return
                if reason == "timeout" and session.pending.get(seat) is None:
                    return
                player.taken_over = True
                player.is_bot = True
                player.bot_difficulty = difficulty
            session._record_system(
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
                move = await session.game.bot_move(difficulty, seat)
                await session.force_move(seat, move)
            except Exception as e:
                log.exception(
                    "Error executing bot takeover for session %s (seat %s). Ending game.",
                    session.id,
                    seat,
                    exc_info=e,
                )
                await session.cancel("error", forfeiter_seat=seat)

        elif consequence == TimeoutConsequence.REMOVED:
            try:
                session.game.remove_player(seat)
                args = {"reason": "timeout"} if reason == "timeout" else {}
                await session.force_move(
                    seat,
                    Move(
                        actor_seat=seat,
                        source="forfeit",
                        args=args,
                        kind=LogEntryKind.SYSTEM,
                    ),
                )
            except Exception as e:
                log.exception(
                    "Error removing player for session %s (seat %s). Ending game.",
                    session.id,
                    seat,
                    exc_info=e,
                )
                await session.cancel("error", forfeiter_seat=seat)

        elif consequence == TimeoutConsequence.GAME_ENDS:
            await session.cancel(reason, forfeiter_seat=seat)

    async def register_rematch_vote(self, thread_id: int, user: discord.User) -> None:
        await self.rematch.vote(thread_id, user.id)
