from __future__ import annotations

from strife.session import GameSession
from strife.matchmaking.registries import SessionRegistries
from strife.persistence.repositories import (
    FinishedMatch,
    GuildRepository,
    MatchRepository,
    UserRepository,
)


class SessionFinalizer:
    def __init__(
        self,
        *,
        registries: SessionRegistries,
        matches: MatchRepository,
        users: UserRepository,
        guilds: GuildRepository,
        lifecycle=None,
    ) -> None:
        self.registries = registries
        self.matches = matches
        self.users = users
        self.guilds = guilds
        self.lifecycle = lifecycle

    async def persist(self, finished: FinishedMatch, outcome) -> tuple[int, str]:
        return await self.matches.create_finished(finished)

    # Compatibility name used by older call sites.
    persist_and_release = persist

    def notify_match_end(self, thread_id: int, match_id: int, outcome, players) -> None:
        if self.lifecycle is not None:
            self.lifecycle.register_session_end(thread_id, match_id, outcome, players)

    async def session_complete(self, session: GameSession) -> None:
        await self.registries.drop_game(session.thread_id)
        for player in session.players:
            if player.user_id:
                await self.registries.release_user(player.user_id)
