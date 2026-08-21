from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from strife.config.text import TextConfig
from strife.engine.game import Game
from strife.engine.metadata import GameMetadata, supports_role_selection
from strife.presentation.message import ViewSurface


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
    role_selection: dict[int, str] = field(default_factory=dict)
    approved: set[int] = field(default_factory=set)
    pending_requests: dict[int, str] = field(default_factory=dict)
    denied: set[int] = field(default_factory=set)
    blacklist: set[int] = field(default_factory=set)
    message_id: int | None = None
    surface: ViewSurface | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    starting: bool = False

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
        game_cls: type[Game] | None = None,
    ) -> tuple[bool, str | None, dict | None]:
        from strife.matchmaking.role_validation import invalid_role_reason, is_role_selection_complete

        if not meta.player_count.is_valid(self.total_players):
            return False, "errors.need_players", {
                "describe": meta.player_count.describe(),
            }
        if supports_role_selection(meta):
            if not is_role_selection_complete(self, meta):
                return False, "errors.role_selection_incomplete", None
            if game_cls is not None:
                reason = invalid_role_reason(self, meta, game_cls)
                if reason is not None:
                    return False, "errors.invalid_roles", {"detail": reason}
        return True, None, None

    def can_start(
        self,
        meta: GameMetadata,
        text: TextConfig,
        game_cls: type[Game] | None = None,
    ) -> tuple[bool, str | None, dict | None]:
        ok, reason_key, reason_kwargs = self.can_ready(meta, text, game_cls)
        if not ok:
            return False, reason_key, reason_kwargs
        if not all(member.user_id in self.ready for member in self.members):
            return False, "errors.not_all_ready", None
        return True, None, None


