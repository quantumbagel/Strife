from __future__ import annotations

from dataclasses import dataclass

import asyncpg


@dataclass(frozen=True)
class ActiveGame:
    thread_id: int
    guild_id: int
    game_key: str


class ActiveGameRepository:
    """Game threads with a live match, so a hard crash can be cleaned up on boot.

    Matches are only written at finalize, so this is the only trace a killed
    process leaves of the threads it was running.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def add(self, thread_id: int, guild_id: int, game_key: str) -> None:
        await self._pool.execute(
            """
            INSERT INTO active_games(thread_id, guild_id, game_key)
            VALUES($1, $2, $3)
            ON CONFLICT (thread_id) DO UPDATE
                SET guild_id = EXCLUDED.guild_id,
                    game_key = EXCLUDED.game_key,
                    started_at = now()
            """,
            thread_id,
            guild_id,
            game_key,
        )

    async def remove(self, thread_id: int) -> None:
        await self._pool.execute("DELETE FROM active_games WHERE thread_id = $1", thread_id)

    async def list_all(self) -> list[ActiveGame]:
        rows = await self._pool.fetch(
            "SELECT thread_id, guild_id, game_key FROM active_games ORDER BY started_at"
        )
        return [
            ActiveGame(
                thread_id=row["thread_id"],
                guild_id=row["guild_id"],
                game_key=row["game_key"],
            )
            for row in rows
        ]
