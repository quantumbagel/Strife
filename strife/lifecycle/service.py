from __future__ import annotations

import asyncio
import time

import discord

from strife.config.text import TextConfig
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
        self._session_meta: dict[int, dict] = {}

    def register_session_end(self, thread_id: int, match_id: int, outcome, players) -> None:
        humans = [p.user_id for p in players if p.user_id and not p.is_bot]
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
            if not session.pending:
                continue
            game_cfg = self.config.games.for_game(session.game_key)
            idle = now - session.last_move_at
            for seat, pending in list(session.pending.items()):
                if session.players[seat].is_bot:
                    continue
                timeout = pending.timeout_seconds if pending.timeout_seconds is not None else game_cfg.turn_timeout_seconds
                if idle >= timeout:
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
    async def _resolve_timeout(self, session, seat: int) -> None:
        consequence = determine_consequence(session, seat, reason="timeout")
        await self._execute_consequence(session, seat, consequence, reason="timeout")

    async def forfeit(self, thread_id: int, user_id: int) -> None:
        session = self.registries.get_game(thread_id)
        if session is None:
            raise RuntimeError("no_session")
        seat = session._seat_for_user(user_id)
        if seat is None:
            raise PermissionError

        consequence = determine_consequence(session, seat, reason="forfeit")
        await self._execute_consequence(session, seat, consequence, reason="forfeit")

    async def _execute_consequence(
        self, session, seat: int, consequence: TimeoutConsequence, reason: str
    ) -> None:
        player = session.players[seat]
        pending = session.pending.get(seat)

        # 1. Release the user if they are leaving the game session
        if consequence in (
            TimeoutConsequence.BOT_TAKEOVER,
            TimeoutConsequence.REMOVED,
            TimeoutConsequence.GAME_ENDS,
        ):
            if player.user_id:
                await self.registries.release_user(player.user_id)

        # 2. Execute the consequence action
        if consequence == TimeoutConsequence.SKIP:
            await session.force_move(seat, Move(actor_seat=seat, source="timeout", args={}))

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
                    await thread.send(
                        f"⚠️ {player.mention} timed out! Strike {player.timeout_strikes}/{max_strikes}."
                    )
                except Exception:
                    log.exception("Failed to send strike warning message")

            source = "pass"
            if pending and pending.allowed_sources is not None and "pass" not in pending.allowed_sources:
                source = "timeout"

            await session.force_move(seat, Move(actor_seat=seat, source=source, args={}))

        elif consequence == TimeoutConsequence.BOT_TAKEOVER:
            difficulty = getattr(session.game.metadata, "bot_takeover_difficulty", "hard")
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
                move = await asyncio.wait_for(session.game.bot_move(difficulty, seat), timeout=10.0)
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
                    seat, Move(actor_seat=seat, source="forfeit", args=args)
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
