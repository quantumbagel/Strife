from __future__ import annotations

from strife.session import GameSession
from strife.matchmaking.registries import SessionRegistries
from strife.persistence.repositories import (
    FinishedMatch,
    GuildRepository,
    LiveMatchStart,
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

    async def finish(self, finished: FinishedMatch, outcome) -> tuple[int, str]:
        return await self.matches.finish(finished)

    async def start_live(self, record: LiveMatchStart) -> tuple[int, str]:
        return await self.matches.start_live(record)

    async def append_moves(self, match_id: int, moves) -> None:
        await self.matches.append_moves(match_id, moves)

    async def set_board_message(self, match_id: int, message_id: int) -> None:
        await self.matches.set_board_message(match_id, message_id)

    def notify_match_end(self, thread_id: int, match_id: int, outcome, players) -> None:
        if self.lifecycle is not None:
            self.lifecycle.register_session_end(thread_id, match_id, outcome, players)

    async def session_complete(self, session: GameSession) -> None:
        await self.registries.drop_game(session.thread_id)
        for player in session.players:
            if player.user_id:
                await self.registries.release_user(
                    player.user_id, thread_id=session.thread_id
                )
