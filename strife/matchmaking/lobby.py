from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import discord

from strife.config.text import TextConfig
from strife.engine.metadata import GameMetadata
from strife.presentation.message import ViewSurface


class LobbyGone(Exception):
    """The lobby message was deleted, so the lobby has been discarded."""


def lobby_action(fn: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
    """Report ``LobbyGone`` from a lobby entry point instead of failing the interaction."""

    @functools.wraps(fn)
    async def wrapper(self, *args, **kwargs) -> None:
        try:
            await fn(self, *args, **kwargs)
        except LobbyGone:
            interaction = next(
                (a for a in (*args, *kwargs.values()) if isinstance(a, discord.Interaction)),
                None,
            )
            if interaction is not None:
                await self._error(interaction, "lobby.already_dead")

    return wrapper


@dataclass
class QueuedBot:
    name: str
    difficulty: str


@dataclass
class LobbyMember:
    user_id: int
    display_name: str


@dataclass
class Lobby:
    thread_id: int  # lobby id used as component resource_id (not a Discord thread)
    guild_id: int
    channel_id: int
    game_key: str
    creator_id: int
    private: bool
    members: list[LobbyMember] = field(default_factory=list)
    bots: list[QueuedBot] = field(default_factory=list)
    ready: set[int] = field(default_factory=set)
    settings: dict = field(default_factory=dict)
    approved: set[int] = field(default_factory=set)
    pending_requests: dict[int, str] = field(default_factory=dict)
    denied: set[int] = field(default_factory=set)
    blacklist: set[int] = field(default_factory=set)
    message_id: int | None = None
    surface: ViewSurface | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    starting: bool = False
    launching: bool = False

    @property
    def lobby_id(self) -> int:
        return self.thread_id

    @property
    def total_players(self) -> int:
        return len(self.members) + len(self.bots)

    def is_full(self, meta: GameMetadata) -> bool:
        max_players = meta.player_count.max_players
        if max_players is None:
            return False
        return self.total_players >= max_players

    def can_ready(
        self,
        meta: GameMetadata,
        text: TextConfig,
    ) -> tuple[bool, str | None, dict | None]:
        if not meta.player_count.is_valid(self.total_players):
            return False, "errors.need_players", {
                "describe": meta.player_count.describe(),
            }
        return True, None, None

    def can_start(
        self,
        meta: GameMetadata,
        text: TextConfig,
    ) -> tuple[bool, str | None, dict | None]:
        ok, reason_key, reason_kwargs = self.can_ready(meta, text)
        if not ok:
            return False, reason_key, reason_kwargs
        if not all(member.user_id in self.ready for member in self.members):
            return False, "errors.not_all_ready", None
        return True, None, None


def allocate_bot_name(bots: list[QueuedBot], difficulty: str) -> str:
    prefix = f"Bot-{difficulty}-"
    used: set[int] = set()
    for bot in bots:
        if not bot.name.startswith(prefix):
            continue
        suffix = bot.name[len(prefix) :]
        if suffix.isdigit():
            used.add(int(suffix))
    n = 1
    while n in used:
        n += 1
    return f"{prefix}{n}"

