from __future__ import annotations

import asyncio
import time

import discord

from strife.config.text import TextConfig
from strife.engine.players import Move
from strife.engine.registry import GameRegistry
from strife.lifecycle.rematch import RematchManager
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
        self.rematch.start_offer(thread_id, set(humans), match_id)

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
        now = time.monotonic()
        for session in list(self.registries.active_games.values()):
            if not session.pending:
                continue
            game_cfg = self.config.games.for_game(session.game_key)
            timeout = game_cfg.turn_timeout_seconds
            warning = game_cfg.turn_warning_seconds
            idle = now - session.last_move_at
            if idle >= timeout - warning and not session._warned:
                session._warned = True
                thread = self.bot.get_channel(session.thread_id)
                if isinstance(thread, discord.Thread):
                    stalled = [session.players[s].display_name for s in session.pending if not session.players[s].is_bot]
                    if stalled:
                        await thread.send(
                            self.text.get("timeout.warning", player=stalled[0], seconds=int(timeout - idle))
                        )
            if idle >= timeout:
                for seat in list(session.pending.keys()):
                    if session.players[seat].is_bot:
                        continue
                    await self._resolve_timeout(session, seat)

    async def _resolve_timeout(self, session, seat: int) -> None:
        meta = session.game.metadata
        if meta.supports_bots:
            session.players[seat].is_bot = True
            session.players[seat].bot_difficulty = "hard"
            move = await session.game.bot_move("hard", seat)
            await session.force_move(seat, move)
            player = session.players[seat]
            if player.user_id:
                await self.registries.release_user(player.user_id)
            return
        if meta.supports_player_removal:
            session.game.remove_player(seat)
            move = Move(actor_seat=seat, source="forfeit", args={"reason": "timeout"})
            await session.force_move(seat, move)
            if session.players[seat].user_id:
                await self.registries.release_user(session.players[seat].user_id)
            return
        await session.cancel("timeout")

    async def forfeit(self, thread_id: int, user_id: int) -> None:
        session = self.registries.get_game(thread_id)
        if session is None:
            raise RuntimeError("no_session")
        seat = session._seat_for_user(user_id)
        if seat is None:
            raise PermissionError
        humans = [p for p in session.players if not p.is_bot]
        if len(humans) == 2:
            opponent = next(p.seat for p in humans if p.seat != seat)
            await session.cancel("forfeit")
            await self.registries.release_user(user_id)
            return
        if session.game.metadata.supports_player_removal:
            session.game.remove_player(seat)
            await session.force_move(seat, Move(actor_seat=seat, source="forfeit", args={}))
            await self.registries.release_user(user_id)
            return
        await session.cancel("forfeit")
        await self.registries.release_user(user_id)

    async def register_rematch_vote(self, thread_id: int, user: discord.User) -> None:
        await self.rematch.vote(thread_id, user.id)
