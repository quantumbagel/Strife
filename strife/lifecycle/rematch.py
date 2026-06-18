from __future__ import annotations

import time

from strife.config.text import TextConfig
from strife.matchmaking.lobby import Lobby, LobbyMember, QueuedBot
from strife.matchmaking.lobby_view import build_lobby_view
from strife.matchmaking.registries import SessionRegistries, UserLocation


class RematchManager:
    def __init__(self, registries: SessionRegistries, lobby_service, text: TextConfig) -> None:
        self.registries = registries
        self.lobby = lobby_service
        self.text = text
        self._votes: dict[int, set[int]] = {}
        self._eligible: dict[int, set[int]] = {}
        self._expires: dict[int, float] = {}
        self._match_ids: dict[int, int] = {}

    def start_offer(self, thread_id: int, eligible: set[int], match_id: int) -> None:
        self._eligible[thread_id] = set(eligible)
        self._votes[thread_id] = set()
        self._expires[thread_id] = time.monotonic() + 120
        self._match_ids[thread_id] = match_id

    async def vote(self, thread_id: int, user_id: int) -> None:
        eligible = self._eligible.get(thread_id, set())
        if user_id not in eligible:
            raise PermissionError
        if time.monotonic() > self._expires.get(thread_id, 0):
            return
        votes = self._votes.setdefault(thread_id, set())
        votes.add(user_id)
        if votes >= eligible:
            await self._reset_to_lobby(thread_id)

    async def _reset_to_lobby(self, thread_id: int) -> None:
        session = self.registries.get_game(thread_id)
        if session is None:
            return
        game_key = session.game_key
        guild_id = session.guild_id
        members = [
            LobbyMember(user_id=p.user_id, display_name=p.display_name)
            for p in session.players
            if p.user_id and not p.is_bot
        ]
        bots = [
            QueuedBot(name=p.display_name, difficulty=p.bot_difficulty or "medium")
            for p in session.players
            if p.is_bot
        ]
        lobby = Lobby(
            thread_id=thread_id,
            guild_id=guild_id,
            channel_id=0,
            game_key=game_key,
            creator_id=members[0].user_id if members else 0,
            private=False,
            members=members,
            bots=bots,
            settings=dict(session.settings),
            surface=session.surface,
        )
        self.registries.active_games.pop(thread_id, None)
        self.registries.add_lobby(lobby)
        for member in members:
            if not await self.registries.reserve_user(
                member.user_id, UserLocation("lobby", thread_id, guild_id)
            ):
                lobby.members = [m for m in lobby.members if m.user_id != member.user_id]
        meta = self.lobby.registry.metadata(game_key)
        view = build_lobby_view(
            lobby,
            meta,
            self.lobby.emoji,
            self.text,
            accent=self.lobby._accent(game_key),
        )
        if lobby.surface:
            lobby.surface.set_prefix("lobby_join:")
            await lobby.surface.replace(view)
        self._votes.pop(thread_id, None)
        self._eligible.pop(thread_id, None)
        self._expires.pop(thread_id, None)
