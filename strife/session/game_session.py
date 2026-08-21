from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Protocol
import time

from strife.config.text import TextConfig
from strife.engine.game import Game
from strife.engine.log import LogEntryKind
from strife.engine.players import GameOutcome, Move, Player
from strife.persistence.repositories import FinishedMatch
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

__all__ = [
    "BOT_MOVE_TIMEOUT_SECONDS",
    "QUERY_TIMEOUT_SECONDS",
    "GameSession",
    "MatchFinalizer",
    "PendingInput",
]


class MatchFinalizer(Protocol):
    async def persist(
        self, finished: FinishedMatch, outcome: GameOutcome
    ) -> tuple[int, str]: ...

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
        turn_timeout_consequence: str = "abandon",
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
        self._finalizer = finalizer
        self.game_key = game_key
        guard_bot_move(game)
        self.ctx = LiveContext(self)
        self.lock = asyncio.Lock()
        self.pending: dict[int, PendingInput] = {}
        self.recorded_moves: list[Move] = []
        now = time.monotonic()
        self.last_move_at = now
        self.last_progress_at = now
        self.task: asyncio.Task | None = None
        self._turn_index = 0
        self._started_at = datetime.now(timezone.utc)
        self._match_id: int | None = None
        self._match_code: str | None = None
        self._bot = None
        self._finalized = False
        self._timeout_warned: dict[int, float] = {}

    @property
    def started_at(self) -> datetime:
        return self._started_at

    def mark_progress(self) -> None:
        self.last_progress_at = time.monotonic()

    async def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        from strife.presentation.emoji_context import bind_emoji, reset_emoji

        token = bind_emoji(self.surface.compiler.emoji)
        try:
            try:
                outcome = await self.game.play(self.ctx)
                await self._finalize(outcome, status="completed")
            except asyncio.CancelledError:
                if not self._finalized:
                    self._append_log_entry(
                        "game_end",
                        {"reason": "cancelled", "cancelled": True},
                        kind=LogEntryKind.SYSTEM,
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
                log.exception("Game session crashed", extra={"match_id": self.id})
                await self._notify_thread(self.text.get("match.session_crashed"))
                if not self._finalized:
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
