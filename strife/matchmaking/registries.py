from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from strife.engine.session import GameSession
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

    async def reserve_user(self, user_id: int, loc: UserLocation) -> bool:
        async with self._lock:
            if user_id in self.user_location:
                return False
            self.user_location[user_id] = loc
            return True

    async def release_user(self, user_id: int) -> None:
        async with self._lock:
            self.user_location.pop(user_id, None)

    def location_of(self, user_id: int) -> UserLocation | None:
        return self.user_location.get(user_id)

    def add_lobby(self, lobby: Lobby) -> None:
        self.lobbies[lobby.thread_id] = lobby
        self.guild_lobbies.setdefault(lobby.guild_id, set()).add(lobby.thread_id)

    def remove_lobby(self, thread_id: int) -> None:
        lobby = self.lobbies.pop(thread_id, None)
        if lobby:
            guild_set = self.guild_lobbies.get(lobby.guild_id)
            if guild_set:
                guild_set.discard(thread_id)

    def get_lobby(self, thread_id: int) -> Lobby | None:
        return self.lobbies.get(thread_id)

    def get_game(self, thread_id: int) -> GameSession | None:
        return self.active_games.get(thread_id)

    def promote(self, lobby_id: int, session: GameSession) -> None:
        lobby = self.lobbies.pop(lobby_id, None)
        if lobby:
            guild_set = self.guild_lobbies.get(lobby.guild_id)
            if guild_set:
                guild_set.discard(lobby_id)
        self.active_games[session.thread_id] = session
        for member in session.players:
            if member.user_id and not member.is_bot:
                self.user_location[member.user_id] = UserLocation("game", session.thread_id, session.guild_id)
