from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from typing import Protocol
import time

from strife.config.text import TextConfig
from strife.engine.game import Game
from strife.engine.requests import TimeoutConsequence
from strife.engine.match_log import MatchLog
from strife.engine.players import GameOutcome, Move, Player
from strife.persistence.repositories import FinishedMatch, LiveMatchStart, MoveConflict
from strife.presentation.message import ViewSurface
from strife.session.context import LiveContext
from strife.session.input import SessionInputMixin
from strife.session.io import SessionIOMixin
from strife.session.lifecycle import SessionLifecycleMixin
from strife.session.types import (
    BOT_MOVE_TIMEOUT_SECONDS,
    QUERY_TIMEOUT_SECONDS,
    PendingInput,
    guard_bot_move,
    log,
)
from strife.session.writer import MoveWriter

__all__ = [
    "BOT_MOVE_TIMEOUT_SECONDS",
    "QUERY_TIMEOUT_SECONDS",
    "GameSession",
    "MatchFinalizer",
    "PendingInput",
]


class MatchFinalizer(Protocol):
    async def finish(
        self, finished: FinishedMatch, outcome: GameOutcome
    ) -> tuple[int, str]: ...

    async def start_live(self, record: LiveMatchStart) -> tuple[int, str]: ...

    async def append_moves(self, match_id: int, moves: list[Move]) -> None: ...

    async def set_board_message(self, match_id: int, message_id: int) -> None: ...

    async def set_header_message(self, match_id: int, message_id: int) -> None: ...

    def notify_match_end(
        self,
        thread_id: int,
        match_id: int,
        outcome: GameOutcome,
        players: list[Player],
    ) -> None: ...

    async def session_complete(self, session: GameSession) -> None: ...


class GameSession(SessionInputMixin, SessionIOMixin, SessionLifecycleMixin):
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
        finalizer: MatchFinalizer,
        game_key: str,
        header_surface: ViewSurface | None = None,
        turn_timeout_seconds: int = 90,
        turn_timeout_max_strikes: int = 3,
        turn_timeout_consequence: TimeoutConsequence = TimeoutConsequence.ABANDON,
    ) -> None:
        self.id = thread_id
        self.thread_id = thread_id
        self.guild_id = guild_id
        self.game = game
        self.players = players
        self.settings = settings
        self.seed = seed
        self.surface = surface
        self.header_surface = header_surface
        self.text = text
        self.turn_timeout_seconds = turn_timeout_seconds
        self.turn_timeout_max_strikes = turn_timeout_max_strikes
        self.turn_timeout_consequence = turn_timeout_consequence
        self._host_rng = random.Random()
        self._finalizer = finalizer
        self.game_key = game_key
        guard_bot_move(game)
        self.ctx = LiveContext(self)
        self.lock = asyncio.Lock()
        self.pending: dict[int, PendingInput] = {}
        self.log = MatchLog(on_append=self._on_log_append)
        now = time.monotonic()
        self.last_move_at = now
        self.last_progress_at = now
        self.task: asyncio.Task | None = None
        self._started_at = datetime.now(timezone.utc)
        self._match_id: int | None = None
        self._match_code: str | None = None
        self._bot = None
        self._finalized = False
        self._ending = False
        self._pausing = False
        self._writer_failed = False
        self._resuming = False
        self._timeout_warned: dict[int, float] = {}
        self._timeout_inflight: set[int] = set()
        self._timeout_generation: dict[int, int] = {}
        self.lobby_surface = None
        self.lobby_private = False
        self.lobby_creator_id: int | None = None
        self.lobby_channel_id: int | None = None
        self.lobby_message_id: int | None = None
        self._dm_failure_notified: set[int] = set()
        # Seats that forfeited or timed out of a still-running match.
        self._removed_seats: set[int] = set()
        self.timeout_strikes: dict[int, int] = {}
        self.taken_over: set[int] = set()
        self._phase_timeout_index: int | None = None
        self._move_writer: MoveWriter | None = None
        self._board_message_saved = False
        self._header_refreshing = False
        self._header_dirty = False
        self.game_version: str | None = None

    @property
    def started_at(self) -> datetime:
        return self._started_at

    @property
    def recorded_moves(self) -> list[Move]:
        return self.log.entries

    def mark_progress(self) -> None:
        self.last_progress_at = time.monotonic()

    def _on_log_append(self, move: Move) -> None:
        self.last_move_at = time.monotonic()
        if self._move_writer is not None:
            self._move_writer.submit(move)

    def _owner_ids(self) -> frozenset[int]:
        settings = getattr(self._bot, "settings", None) if self._bot is not None else None
        if settings is None:
            return frozenset()
        return frozenset(getattr(settings, "owner_ids", ()) or ())

    def _start_writer(self) -> None:
        if self._match_id is None:
            return
        self._move_writer = MoveWriter(
            self._finalizer.append_moves,
            self._match_id,
            on_fatal=self._on_writer_fatal,
        )
        self._move_writer.start()

    async def _on_writer_fatal(self, exc: BaseException) -> None:
        """Another process finished or owns this row; stop play without finish()."""
        log.error(
            "Live match %s lost move persistence (%s); stopping without finish",
            self._match_id,
            exc,
        )
        if isinstance(exc, MoveConflict):
            log.error(
                "Stored log diverged for live match %s at turn %s",
                self._match_id,
                exc.turn_index,
            )
        self._move_writer = None
        async with self.lock:
            if self._finalized or self._ending or self._writer_failed:
                return
            self._writer_failed = True
            self._ending = True
        task = self.task
        current = asyncio.current_task()
        if task is not None and not task.done() and task is not current:
            task.cancel()
            await asyncio.wait({task}, timeout=10.0)
        await self._notify_thread(self.text.get("match.interrupted"))
        try:
            await self._finalizer.session_complete(self)
        except Exception:
            log.exception(
                "Failed to release occupancy after writer failure (thread %s)",
                self.thread_id,
            )

    async def _stop_writer(self) -> None:
        writer = self._move_writer
        self._move_writer = None
        if writer is None:
            return
        try:
            await writer.stop(timeout=10.0)
        except Exception:
            log.exception("Failed to flush move writer for thread %s", self.thread_id)

    async def start(self, *, resume: bool = False) -> None:
        self._start_writer()
        if resume:
            self._resuming = True
            self._board_message_saved = self.surface.message is not None
            self.ctx.begin_catchup(list(self.log.entries))
        self.task = asyncio.create_task(self._run())
        if resume:
            await self._notify_thread(self.text.get("match.session_resumed"))

    async def _run(self) -> None:
        from strife.presentation.emoji_context import bind_emoji, reset_emoji

        token = bind_emoji(self.surface.compiler.emoji)
        try:
            try:
                outcome = await self.game.play(self.ctx)
                await self._finalize(outcome, status="completed")
            except asyncio.CancelledError:
                if self._pausing:
                    return
                if not self._finalized and not self._ending:
                    self.log.system(
                        "game_end",
                        {"reason": "cancelled", "cancelled": True},
                    )
                    await self._finalize(
                        GameOutcome(
                            results={},
                            summary={"reason": "cancelled"},
                            description=self.text.get("match.session_cancelled_description"),
                            player_descriptions={
                                player.seat: "Abandoned" for player in self.players
                            },
                        ),
                        status="abandoned",
                    )
                return
            except Exception:
                catching_up = self._resuming and self.ctx._catching_up
                if catching_up:
                    log.exception(
                        "Catch-up failed for session %s",
                        self.id,
                        extra={"match_id": self.id},
                    )
                    await self._notify_thread(self.text.get("match.interrupted"))
                    if not self._finalized and not self._ending:
                        self.log.system(
                            "game_end",
                            {"reason": "interrupted", "cancelled": True},
                        )
                        await self._finalize(
                            GameOutcome(
                                results={},
                                summary={"reason": "interrupted"},
                                description=self.text.get("match.interrupted"),
                                player_descriptions={
                                    player.seat: "Abandoned" for player in self.players
                                },
                            ),
                            status="abandoned",
                        )
                    return
                log.exception("Game session crashed", extra={"match_id": self.id})
                await self._notify_thread(self.text.get("match.session_crashed"))
                if not self._finalized and not self._ending:
                    async with self.lock:
                        self.log.system(
                            "game_end",
                            {"reason": "error", "cancelled": True},
                        )
                    await self._finalize(
                        GameOutcome(
                            results={},
                            summary={"error": True},
                            description=self.text.get("match.session_crashed_description"),
                            player_descriptions={
                                player.seat: "Abandoned" for player in self.players
                            },
                        ),
                        status="abandoned",
                    )
        finally:
            reset_emoji(token)
