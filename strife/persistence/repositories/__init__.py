from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import asyncpg


@dataclass
class MoveRecord:
    turn_index: int
    actor_seat: int | None
    source: str
    arguments: dict[str, Any]
    created_at: datetime | None = None


@dataclass
class MatchPlayer:
    seat_index: int
    user_id: int | None
    is_bot: bool
    bot_difficulty: str | None
    display_name: str
    role_key: str | None
    result: str | None


@dataclass
class MatchSummary:
    id: int
    code: str
    game_key: str
    status: str
    outcome: dict[str, Any]
    created_at: datetime
    total_turns: int


@dataclass
class UserMatchSummary(MatchSummary):
    seat_index: int | None
    role_key: str | None
    result: str | None


@dataclass
class MatchDetail(MatchSummary):
    guild_id: int
    thread_id: int | None
    seed: int
    settings: dict[str, Any]
    players: list[MatchPlayer]
    started_at: datetime | None
    ended_at: datetime | None


@dataclass
class FinishedMatch:
    code: str | None
    game_key: str
    guild_id: int
    thread_id: int | None
    seed: int
    settings: dict[str, Any]
    status: str
    outcome: dict[str, Any]
    total_turns: int
    players: list[MatchPlayer]
    moves: list[MoveRecord]
    started_at: datetime | None = None
    ended_at: datetime | None = None


@dataclass
class PlayerResult:
    user_id: int
    display_name: str
    result: str


@dataclass
class UserStats:
    wins: int = 0
    losses: int = 0
    draws: int = 0
    played: int = 0


_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def generate_match_code(rng: Any) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(6))


class GuildRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert(self, guild_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO guilds(guild_id) VALUES($1)
                ON CONFLICT (guild_id) DO UPDATE SET updated_at = now()
                """,
                guild_id,
            )

    async def set_default_channel(self, guild_id: int, channel_id: int) -> None:
        async with self._pool.acquire() as conn:
            await self.upsert(guild_id)
            await conn.execute(
                "UPDATE guilds SET default_channel_id = $2, updated_at = now() WHERE guild_id = $1",
                guild_id,
                channel_id,
            )

    async def get_default_channel(self, guild_id: int) -> int | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT default_channel_id FROM guilds WHERE guild_id = $1",
                guild_id,
            )
            return row["default_channel_id"] if row else None


class MatchRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_finished(self, record: FinishedMatch) -> int:
        import random

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                match_id: int | None = None
                code = record.code
                for _ in range(10):
                    if code is None:
                        code = generate_match_code(random.Random())
                    try:
                        row = await conn.fetchrow(
                            """
                            INSERT INTO matches(
                                code, game_key, guild_id, thread_id, seed, settings,
                                status, outcome, total_turns, started_at, ended_at
                            ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                            RETURNING id
                            """,
                            code,
                            record.game_key,
                            record.guild_id,
                            record.thread_id,
                            record.seed,
                            record.settings,
                            record.status,
                            record.outcome,
                            record.total_turns,
                            record.started_at,
                            record.ended_at,
                        )
                        match_id = row["id"]
                        break
                    except asyncpg.UniqueViolationError:
                        code = None
                if match_id is None:
                    raise RuntimeError("Failed to generate unique match code")

                for player in record.players:
                    await conn.execute(
                        """
                        INSERT INTO match_players(
                            match_id, seat_index, user_id, is_bot, bot_difficulty,
                            display_name, role_key, result
                        ) VALUES($1,$2,$3,$4,$5,$6,$7,$8)
                        """,
                        match_id,
                        player.seat_index,
                        player.user_id,
                        player.is_bot,
                        player.bot_difficulty,
                        player.display_name,
                        player.role_key,
                        player.result,
                    )

                for move in record.moves:
                    await conn.execute(
                        """
                        INSERT INTO moves(match_id, turn_index, actor_seat, source, arguments, created_at)
                        VALUES($1,$2,$3,$4,$5,$6)
                        """,
                        match_id,
                        move.turn_index,
                        move.actor_seat,
                        move.source,
                        move.arguments,
                        move.created_at or datetime.now(timezone.utc),
                    )
                return match_id

    async def get(self, ref: str | int) -> MatchDetail | None:
        async with self._pool.acquire() as conn:
            if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
                row = await conn.fetchrow("SELECT * FROM matches WHERE id = $1", int(ref))
            else:
                row = await conn.fetchrow("SELECT * FROM matches WHERE code = $1", ref.upper())
            if row is None:
                return None
            players = await conn.fetch(
                "SELECT * FROM match_players WHERE match_id = $1 ORDER BY seat_index",
                row["id"],
            )
            return self._to_detail(row, players)

    async def list_recent(
        self, guild_id: int, *, limit: int = 10, offset: int = 0
    ) -> list[MatchSummary]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, code, game_key, status, outcome, created_at, total_turns
                FROM matches WHERE guild_id = $1
                ORDER BY created_at DESC LIMIT $2 OFFSET $3
                """,
                guild_id,
                limit,
                offset,
            )
            return [self._to_summary(row) for row in rows]

    async def list_for_user(
        self,
        user_id: int,
        game_key: str | None,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[MatchSummary]:
        async with self._pool.acquire() as conn:
            if game_key:
                rows = await conn.fetch(
                    """
                    SELECT m.id, m.code, m.game_key, m.status, m.outcome, m.created_at, m.total_turns,
                           mp.seat_index, mp.role_key, mp.result
                    FROM matches m
                    JOIN match_players mp ON mp.match_id = m.id
                    WHERE mp.user_id = $1 AND m.game_key = $2
                    ORDER BY m.created_at DESC LIMIT $3 OFFSET $4
                    """,
                    user_id,
                    game_key,
                    limit,
                    offset,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT m.id, m.code, m.game_key, m.status, m.outcome, m.created_at, m.total_turns,
                           mp.seat_index, mp.role_key, mp.result
                    FROM matches m
                    JOIN match_players mp ON mp.match_id = m.id
                    WHERE mp.user_id = $1
                    ORDER BY m.created_at DESC LIMIT $2 OFFSET $3
                    """,
                    user_id,
                    limit,
                    offset,
                )
            return [self._to_summary(row) for row in rows]

    def _to_summary(self, row: asyncpg.Record) -> MatchSummary:
        if "seat_index" in row:
            return UserMatchSummary(
                id=row["id"],
                code=row["code"],
                game_key=row["game_key"],
                status=row["status"],
                outcome=row["outcome"] or {},
                created_at=row["created_at"],
                total_turns=row["total_turns"],
                seat_index=row["seat_index"],
                role_key=row["role_key"],
                result=row["result"],
            )
        return MatchSummary(
            id=row["id"],
            code=row["code"],
            game_key=row["game_key"],
            status=row["status"],
            outcome=row["outcome"] or {},
            created_at=row["created_at"],
            total_turns=row["total_turns"],
        )

    def _to_detail(self, row: asyncpg.Record, players: list[asyncpg.Record]) -> MatchDetail:
        return MatchDetail(
            id=row["id"],
            code=row["code"],
            game_key=row["game_key"],
            status=row["status"],
            outcome=row["outcome"] or {},
            created_at=row["created_at"],
            total_turns=row["total_turns"],
            guild_id=row["guild_id"],
            thread_id=row["thread_id"],
            seed=row["seed"],
            settings=row["settings"] or {},
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            players=[
                MatchPlayer(
                    seat_index=p["seat_index"],
                    user_id=p["user_id"],
                    is_bot=p["is_bot"],
                    bot_difficulty=p["bot_difficulty"],
                    display_name=p["display_name"],
                    role_key=p["role_key"],
                    result=p["result"],
                )
                for p in players
            ],
        )


class MoveRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_for_match(self, match_id: int) -> list[MoveRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM moves WHERE match_id = $1 ORDER BY turn_index",
                match_id,
            )
            return [
                MoveRecord(
                    turn_index=row["turn_index"],
                    actor_seat=row["actor_seat"],
                    source=row["source"],
                    arguments=row["arguments"] or {},
                    created_at=row["created_at"],
                )
                for row in rows
            ]


class UserRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def touch(self, user_id: int, display_name: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users(user_id, display_name)
                VALUES($1, $2)
                ON CONFLICT (user_id) DO UPDATE
                SET display_name = EXCLUDED.display_name, last_seen = now()
                """,
                user_id,
                display_name,
            )

    async def apply_results(self, results: list[PlayerResult], game_key: str) -> None:
        async with self._pool.acquire() as conn:
            for result in results:
                await self.touch(result.user_id, result.display_name)
                wins = losses = draws = 0
                if result.result == "win":
                    wins = 1
                elif result.result == "loss":
                    losses = 1
                elif result.result == "draw":
                    draws = 1
                await conn.execute(
                    """
                    INSERT INTO user_game_stats(user_id, game_key, wins, losses, draws, played)
                    VALUES($1, $2, $3, $4, $5, 1)
                    ON CONFLICT (user_id, game_key) DO UPDATE SET
                        wins = user_game_stats.wins + EXCLUDED.wins,
                        losses = user_game_stats.losses + EXCLUDED.losses,
                        draws = user_game_stats.draws + EXCLUDED.draws,
                        played = user_game_stats.played + 1
                    """,
                    result.user_id,
                    game_key,
                    wins,
                    losses,
                    draws,
                )

    async def get_stats(self, user_id: int, game_key: str | None) -> UserStats:
        async with self._pool.acquire() as conn:
            if game_key:
                row = await conn.fetchrow(
                    """
                    SELECT wins, losses, draws, played
                    FROM user_game_stats WHERE user_id = $1 AND game_key = $2
                    """,
                    user_id,
                    game_key,
                )
            else:
                row = await conn.fetchrow(
                    """
                    SELECT COALESCE(SUM(wins),0) AS wins,
                           COALESCE(SUM(losses),0) AS losses,
                           COALESCE(SUM(draws),0) AS draws,
                           COALESCE(SUM(played),0) AS played
                    FROM user_game_stats WHERE user_id = $1
                    """,
                    user_id,
                )
            if row is None:
                return UserStats()
            return UserStats(
                wins=row["wins"],
                losses=row["losses"],
                draws=row["draws"],
                played=row["played"],
            )
