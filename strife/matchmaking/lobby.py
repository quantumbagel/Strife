from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from strife.engine.game import Game
from strife.engine.metadata import GameMetadata
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
    thread_id: int
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
    whitelist: set[int] = field(default_factory=set)
    blacklist: set[int] = field(default_factory=set)
    message_id: int | None = None
    surface: ViewSurface | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def total_players(self) -> int:
        return len(self.members) + len(self.bots)

    def can_ready(self, meta: GameMetadata, game: Game | None = None) -> tuple[bool, str | None]:
        if not meta.player_count.is_valid(self.total_players):
            return False, f"Need {meta.player_count.describe()} players"
        if meta.role_flow.value in {"selectable", "selectable_random"}:
            for member in self.members:
                if member.user_id not in self.role_selection:
                    return False, "Role selection incomplete"
        if game is not None:
            assignment = {m.user_id: self.role_selection[m.user_id] for m in self.members if m.user_id in self.role_selection}
            ok, reason = game.validate_roles(assignment)
            if not ok:
                return False, reason or "Invalid roles"
        return True, None

    def can_start(self, meta: GameMetadata, game: Game | None = None) -> tuple[bool, str | None]:
        ok, reason = self.can_ready(meta, game)
        if not ok:
            return False, reason
        if len(self.ready) < len(self.members):
            return False, "Not all players are ready"
        return True, None

