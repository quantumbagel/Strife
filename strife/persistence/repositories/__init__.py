from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import asyncpg

from strife.engine.log import LogEntryKind, infer_log_kind
from strife.engine.players import Move as MoveRecord


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
    started_at: datetime | None = None
    ended_at: datetime | None = None
    player_count: int | None = None


@dataclass
class UserMatchSummary(MatchSummary):
    seat_index: int | None = None
    role_key: str | None = None
    result: str | None = None


@dataclass
class MatchDetail(MatchSummary):
    guild_id: int = 0
    thread_id: int | None = None
    seed: int = 0
    settings: dict[str, Any] = field(default_factory=dict)
    players: list[MatchPlayer] = field(default_factory=list)


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


def _status_rowcount(status: str) -> int:
    parts = status.split()
    try:
        return int(parts[-1])
    except (IndexError, ValueError):
        return 0


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
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO guilds(guild_id) VALUES($1)
                    ON CONFLICT (guild_id) DO UPDATE SET updated_at = now()
                    """,
                    guild_id,
                )
                await conn.execute(
                    "UPDATE guilds SET default_channel_id = $2, updated_at = now() WHERE guild_id = $1",
                    guild_id,
                    channel_id,
                )

    async def clear_default_channel(self, guild_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE guilds SET default_channel_id = NULL, updated_at = now() WHERE guild_id = $1",
                guild_id,
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

    async def create_finished(self, record: FinishedMatch) -> tuple[int, str]:
        import random

        async with self._pool.acquire() as conn:
            match_id: int | None = None
            final_code: str | None = None
            code = record.code
            for _ in range(10):
                if code is None:
                    code = generate_match_code(random.Random())
                try:
                    async with conn.transaction():
                        row = await conn.fetchrow(
                            """
                            INSERT INTO matches(
                                code, game_key, guild_id, thread_id, seed, settings,
                                status, outcome, total_turns, started_at, ended_at
                            ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                            RETURNING id, code
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
                        final_code = row["code"]

                        if record.players:
                            await conn.executemany(
                                """
                                INSERT INTO match_players(
                                    match_id, seat_index, user_id, is_bot, bot_difficulty,
                                    display_name, role_key, result
                                ) VALUES($1,$2,$3,$4,$5,$6,$7,$8)
                                """,
                                [
                                    (
                                        match_id,
                                        player.seat_index,
                                        player.user_id,
                                        player.is_bot,
                                        player.bot_difficulty,
                                        player.display_name,
                                        player.role_key,
                                        player.result,
                                    )
                                    for player in record.players
                                ],
                            )

                        if record.moves:
                            await conn.executemany(
                                """
                                INSERT INTO moves(match_id, turn_index, actor_seat, source, arguments, kind, created_at)
                                VALUES($1,$2,$3,$4,$5,$6,$7)
                                """,
                                [
                                    (
                                        match_id,
                                        move.turn_index,
                                        move.actor_seat,
                                        move.source,
                                        move.args,
                                        move.kind.value,
                                        move.created_at or datetime.now(timezone.utc),
                                    )
                                    for move in record.moves
                                ],
                            )

                        stats_rows = [
                            player
                            for player in record.players
                            if player.user_id and not player.is_bot and player.result
                        ]
                        if stats_rows:
                            await UserRepository(self._pool).apply_results(
                                [
                                    PlayerResult(
                                        user_id=p.user_id,  # type: ignore[arg-type]
                                        display_name=p.display_name,
                                        result=p.result or "loss",
                                    )
                                    for p in stats_rows
                                ],
                                record.game_key,
                                conn=conn,
                            )
                    break
                except asyncpg.UniqueViolationError:
                    code = None
            if match_id is None or final_code is None:
                raise RuntimeError("Failed to generate unique match code")
            return match_id, final_code

    async def get(self, ref: str | int) -> MatchDetail | None:
        async with self._pool.acquire() as conn:
            if isinstance(ref, int):
                row = await conn.fetchrow("SELECT * FROM matches WHERE id = $1", ref)
            else:
                token = str(ref).strip().upper()
                if len(token) == 6 and all(ch in _ALPHABET for ch in token):
                    row = await conn.fetchrow("SELECT * FROM matches WHERE code = $1", token)
                elif token.isdigit():
                    row = await conn.fetchrow("SELECT * FROM matches WHERE id = $1", int(token))
                else:
                    row = await conn.fetchrow("SELECT * FROM matches WHERE code = $1", token)
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
                SELECT id, code, game_key, status, outcome, created_at, started_at, ended_at, total_turns,
                       (SELECT count(*) FROM match_players mp2 WHERE mp2.match_id = id) AS player_count
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
                    SELECT m.id, m.code, m.game_key, m.status, m.outcome, m.created_at, m.started_at, m.ended_at, m.total_turns,
                           (SELECT count(*) FROM match_players mp2 WHERE mp2.match_id = m.id) AS player_count,
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
                    SELECT m.id, m.code, m.game_key, m.status, m.outcome, m.created_at, m.started_at, m.ended_at, m.total_turns,
                           (SELECT count(*) FROM match_players mp2 WHERE mp2.match_id = m.id) AS player_count,
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

    async def count_for_user(self, user_id: int, game_key: str | None) -> int:
        async with self._pool.acquire() as conn:
            if game_key:
                row = await conn.fetchrow(
                    """
                    SELECT count(*) AS total
                    FROM matches m
                    JOIN match_players mp ON mp.match_id = m.id
                    WHERE mp.user_id = $1 AND m.game_key = $2
                    """,
                    user_id,
                    game_key,
                )
            else:
                row = await conn.fetchrow(
                    """
                    SELECT count(*) AS total
                    FROM matches m
                    JOIN match_players mp ON mp.match_id = m.id
                    WHERE mp.user_id = $1
                    """,
                    user_id,
                )
            return int(row["total"]) if row else 0

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
                started_at=row.get("started_at"),
                ended_at=row.get("ended_at"),
                player_count=row.get("player_count"),
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
            started_at=row.get("started_at"),
            ended_at=row.get("ended_at"),
            player_count=row.get("player_count"),
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

    async def delete_for_game(self, game_key: str) -> int:
        async with self._pool.acquire() as conn:
            status = await conn.execute("DELETE FROM matches WHERE game_key = $1", game_key)
        return _status_rowcount(status)


class MoveRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_for_match(self, match_id: int) -> list[MoveRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM moves WHERE match_id = $1 ORDER BY turn_index",
                match_id,
            )
            records: list[MoveRecord] = []
            for row in rows:
                arguments = row["arguments"] or {}
                if "kind" in row.keys():
                    kind = LogEntryKind(row["kind"])
                else:
                    kind = infer_log_kind(row["source"], arguments)
                records.append(
                    MoveRecord(
                        turn_index=row["turn_index"],
                        actor_seat=row["actor_seat"],
                        source=row["source"],
                        args=arguments,
                        kind=kind,
                        created_at=row["created_at"],
                    )
                )
            return records


class UserRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def touch(self, user_id: int, display_name: str, *, conn: asyncpg.Connection | None = None) -> None:
        query = """
            INSERT INTO users(user_id, display_name)
            VALUES($1, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET display_name = EXCLUDED.display_name, last_seen = now()
        """
        if conn is not None:
            await conn.execute(query, user_id, display_name)
            return
        async with self._pool.acquire() as owned:
            await owned.execute(query, user_id, display_name)

    async def apply_results(
        self,
        results: list[PlayerResult],
        game_key: str,
        *,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        async def _write(connection: asyncpg.Connection) -> None:
            for result in results:
                await self.touch(result.user_id, result.display_name, conn=connection)
                wins = losses = draws = 0
                if result.result == "win":
                    wins = 1
                elif result.result == "loss":
                    losses = 1
                elif result.result == "draw":
                    draws = 1
                await connection.execute(
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

        if conn is not None:
            await _write(conn)
            return
        async with self._pool.acquire() as owned:
            async with owned.transaction():
                await _write(owned)

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

    async def delete_stats_for_game(self, game_key: str) -> int:
        async with self._pool.acquire() as conn:
            status = await conn.execute(
                "DELETE FROM user_game_stats WHERE game_key = $1",
                game_key,
            )
        return _status_rowcount(status)
