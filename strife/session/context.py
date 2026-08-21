from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import random
from typing import Any, Literal, TYPE_CHECKING

from strife.engine.log import reject_system_source
from strife.engine.players import Move, Player
from strife.presentation.components import LayoutView
from strife.presentation.emoji import EmojiResolver

if TYPE_CHECKING:
    from strife.session.game_session import GameSession


class LiveContext:
    """``GameContext`` implementation backed by a live ``GameSession``."""

    def __init__(self, session: GameSession) -> None:
        self._session = session
        self._query_interaction: object | None = None

    def _touch(self) -> None:
        self._session.mark_progress()

    def _begin_query(self, interaction: object) -> None:
        self._query_interaction = interaction

    def _end_query(self) -> None:
        self._query_interaction = None

    @property
    def started_at(self) -> datetime | None:
        return self._session.started_at

    @property
    def is_replay(self) -> bool:
        return False

    @property
    def rng(self) -> random.Random:
        return self._session.game.rng

    @property
    def players(self) -> Sequence[Player]:
        return self._session.players

    @property
    def settings(self) -> Mapping[str, object]:
        return self._session.settings

    @property
    def emoji(self) -> EmojiResolver:
        return self._session.surface.compiler.emoji

    def is_bot(self, seat: int) -> bool:
        return self._session.players[seat].is_bot

    async def update(self, view: LayoutView) -> None:
        self._touch()
        await self._session._update_surface(view)

    async def request_input(
        self,
        view: LayoutView,
        *,
        actor: int,
        sources: set[str] | None = None,
        record: bool = True,
        description: str | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: str | None = None,
    ) -> Move:
        self._touch()
        move = await self._session._request_input(
            view,
            actor=actor,
            sources=sources,
            record=record,
            description=description,
            timeout_seconds=timeout_seconds,
            timeout_consequence=timeout_consequence,
        )
        self._touch()
        return move

    async def request_inputs(
        self,
        view: LayoutView,
        *,
        actors: set[int],
        sources: set[str] | None = None,
        until: Literal["all", "any"] = "all",
        per_seat_sources: dict[int, set[str]] | None = None,
        record: bool = True,
        description: str | None = None,
        descriptions: dict[int, str] | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: str | None = None,
    ) -> dict[int, Move]:
        self._touch()
        moves = await self._session._request_inputs(
            view,
            actors=actors,
            sources=sources,
            until=until,
            per_seat_sources=per_seat_sources,
            record=record,
            description=description,
            descriptions=descriptions,
            timeout_seconds=timeout_seconds,
            timeout_consequence=timeout_consequence,
        )
        self._touch()
        return moves

    async def send_private(self, seat: int, view: LayoutView) -> None:
        self._touch()
        await self._session._send_private(seat, view)

    async def record_event(self, source: str, arguments: dict[str, Any]) -> None:
        self._touch()
        reject_system_source(source)
        self._session._append_log_entry(source, arguments)

    async def respond_query(self, view: LayoutView) -> None:
        self._touch()
        interaction = self._query_interaction
        if interaction is None:
            raise RuntimeError("respond_query() is only valid inside handle_query")
        await self._session._respond_query(interaction, view)
