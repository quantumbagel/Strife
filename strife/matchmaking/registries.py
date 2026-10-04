from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from strife.session import GameSession
from strife.matchmaking.lobby import Lobby


@dataclass
class UserLocation:
    kind: Literal["lobby", "game"]
    thread_id: int
    guild_id: int


class SessionRegistries:
    def __init__(self) -> None:
        self.active_games: dict[int, GameSession] = {}
        self.lobbies: dict[int, Lobby] = {}
        self.guild_lobbies: dict[int, set[int]] = {}
        self.user_location: dict[int, UserLocation] = {}
        self._lock = asyncio.Lock()
        # Called with the other lobbies whose join requests a reservation dropped.
        self.on_requests_pruned: Callable[[list[Lobby]], None] | None = None

    async def reserve_user(self, user_id: int, loc: UserLocation) -> bool:
        async with self._lock:
            if user_id in self.user_location:
                return False
            self.user_location[user_id] = loc
            pruned = self._prune_requests(user_id, keep_lobby_id=loc.thread_id)
        if pruned and self.on_requests_pruned is not None:
            self.on_requests_pruned(pruned)
        return True

    def _prune_requests(self, user_id: int, *, keep_lobby_id: int) -> list[Lobby]:
        """Drop the user's join requests: they are seated now, so the requests are stale."""
        pruned: list[Lobby] = []
        for lobby in self.lobbies.values():
            if lobby.pending_requests.pop(user_id, None) is not None:
                if lobby.lobby_id != keep_lobby_id:
                    pruned.append(lobby)
        return pruned

    async def release_user(self, user_id: int, *, thread_id: int | None = None) -> None:
        async with self._lock:
            loc = self.user_location.get(user_id)
            if loc is None:
                return
            if thread_id is not None and loc.thread_id != thread_id:
                return
            self.user_location.pop(user_id, None)

    def location_of(self, user_id: int) -> UserLocation | None:
        return self.user_location.get(user_id)

    def add_lobby(self, lobby: Lobby) -> None:
        self.lobbies[lobby.lobby_id] = lobby
        self.guild_lobbies.setdefault(lobby.guild_id, set()).add(lobby.lobby_id)

    def remove_lobby(self, lobby_id: int) -> None:
        lobby = self.lobbies.pop(lobby_id, None)
        if lobby:
            guild_set = self.guild_lobbies.get(lobby.guild_id)
            if guild_set:
                guild_set.discard(lobby_id)

    def get_lobby(self, lobby_id: int) -> Lobby | None:
        return self.lobbies.get(lobby_id)

    def get_game(self, thread_id: int) -> GameSession | None:
        return self.active_games.get(thread_id)

    async def drop_game(self, thread_id: int) -> None:
        async with self._lock:
            self.active_games.pop(thread_id, None)

    async def promote(self, lobby_id: int, session: GameSession) -> None:
        async with self._lock:
            lobby = self.lobbies.pop(lobby_id, None)
            if lobby:
                guild_set = self.guild_lobbies.get(lobby.guild_id)
                if guild_set:
                    guild_set.discard(lobby_id)
            self.active_games[session.thread_id] = session
            for member in session.players:
                if member.user_id and not member.is_bot:
                    self.user_location[member.user_id] = UserLocation(
                        "game", session.thread_id, session.guild_id
                    )

    async def rollback_promote(self, lobby: Lobby, session: GameSession) -> None:
        """Undo ``promote``: drop the session and restore the lobby occupancy."""
        async with self._lock:
            self.active_games.pop(session.thread_id, None)
            self.lobbies[lobby.lobby_id] = lobby
            self.guild_lobbies.setdefault(lobby.guild_id, set()).add(lobby.lobby_id)
            for member in lobby.members:
                self.user_location[member.user_id] = UserLocation(
                    "lobby", lobby.thread_id, lobby.guild_id
                )
