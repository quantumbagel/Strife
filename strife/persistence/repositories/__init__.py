from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import asyncpg

from strife.engine.log import LOG_FORMAT, LogEntryKind
from strife.engine.players import Move as MoveRecord
from strife.logging import get_logger

log = get_logger("persistence")

_VALID_PLAYER_RESULTS = frozenset({"win", "loss", "draw"})
_MATCH_CODE_UNIQUE = "matches_code_key"
_LIVE_THREAD_UNIQUE = "matches_live_thread_uidx"


class MatchNotLive(Exception):
    """Moves can only be appended while the match row is ``live``."""

    def __init__(self, match_id: int, status: str | None = None) -> None:
        self.match_id = match_id
        self.status = status
        if status is None:
            message = f"Match {match_id} does not exist"
        else:
            message = f"Match {match_id} is {status!r}, not live"
        super().__init__(message)


class MoveConflict(Exception):
    """A row already exists at this turn_index with different content."""

    def __init__(self, match_id: int, turn_index: int) -> None:
        self.match_id = match_id
        self.turn_index = turn_index
        super().__init__(
            f"Move at turn {turn_index} for match {match_id} "
            "already exists with different content"
        )


class LiveThreadConflict(Exception):
    """Another live match already occupies this Discord thread."""

    def __init__(self, thread_id: int) -> None:
        self.thread_id = thread_id
        super().__init__(f"A live match already exists for thread {thread_id}")


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


@dataclass(kw_only=True)
class MatchDetail(MatchSummary):
    guild_id: int = 0
    thread_id: int | None = None
    seed: int = 0
    settings: dict[str, Any] = field(default_factory=dict)
    log_format: int
    players: list[MatchPlayer] = field(default_factory=list)
    game_version: str | None = None
    game_build: str | None = None
    board_message_id: int | None = None
    header_message_id: int | None = None
    lobby_channel_id: int | None = None
    lobby_message_id: int | None = None
    turn_timeout_seconds: int | None = None
    turn_timeout_max_strikes: int | None = None
    turn_timeout_consequence: str | None = None
    lobby_private: bool = False
    lobby_creator_id: int | None = None


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
    match_id: int | None = None
    game_version: str | None = None
    board_message_id: int | None = None
    header_message_id: int | None = None
    lobby_channel_id: int | None = None
    lobby_message_id: int | None = None
    turn_timeout_seconds: int | None = None
    turn_timeout_max_strikes: int | None = None
    turn_timeout_consequence: str | None = None
    lobby_private: bool = False
    lobby_creator_id: int | None = None


@dataclass
class LiveMatchStart:
    code: str | None
    game_key: str
    guild_id: int
    thread_id: int
    seed: int
    settings: dict[str, Any]
    players: list[MatchPlayer]
    started_at: datetime | None = None
    game_version: str | None = None
    board_message_id: int | None = None
    header_message_id: int | None = None
    lobby_channel_id: int | None = None
    lobby_message_id: int | None = None
    turn_timeout_seconds: int | None = None
    turn_timeout_max_strikes: int | None = None
    turn_timeout_consequence: str | None = None
    lobby_private: bool = False
    lobby_creator_id: int | None = None
    game_build: str | None = None


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
    except IndexError, ValueError:
        return 0


def generate_match_code(rng: Any) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(6))


def normalize_match_code(raw: str) -> str | None:
    """Return the canonical 6-character code for user input, or None if malformed."""
    token = str(raw).strip().upper().removeprefix("#")
    if len(token) == 6 and all(ch in _ALPHABET for ch in token):
        return token
    return None


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
        async with self._pool.acquire() as conn, conn.transaction():
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


_NOT_LIVE = "status <> 'live'"


class MatchRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    def _match_insert_params(self, record: LiveMatchStart) -> tuple:
        return (
            record.code,
            record.game_key,
            record.guild_id,
            record.thread_id,
            record.seed,
            record.settings,
            "live",
            {},
            0,
            record.started_at,
            None,
            LOG_FORMAT,
            record.game_version,
            record.game_build,
            record.board_message_id,
            record.header_message_id,
            record.lobby_channel_id,
            record.lobby_message_id,
            record.turn_timeout_seconds,
            record.turn_timeout_max_strikes,
            record.turn_timeout_consequence,
            record.lobby_private,
            record.lobby_creator_id,
        )

    async def _insert_players(
        self, conn: asyncpg.Connection, match_id: int, players: list[MatchPlayer]
    ) -> None:
        if not players:
            return
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
                for player in players
            ],
        )

    async def _insert_moves(
        self, conn: asyncpg.Connection, match_id: int, moves: list[MoveRecord]
    ) -> None:
        if not moves:
            return
        await conn.executemany(
            """
            INSERT INTO moves(match_id, turn_index, actor_seat, source, arguments, kind, created_at)
            VALUES($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (match_id, turn_index) DO NOTHING
            """,
            [
                (
                    match_id,
                    move.turn_index,
                    move.actor_seat,
                    move.source,
                    move.args,
                    move.kind.value,
                    move.created_at or datetime.now(UTC),
                )
                for move in moves
            ],
        )

    async def _apply_player_stats(
        self,
        conn: asyncpg.Connection,
        game_key: str,
        players: list[MatchPlayer],
    ) -> None:
        stats_rows = [
            player
            for player in players
            if player.user_id
            and not player.is_bot
            and player.result in _VALID_PLAYER_RESULTS
        ]
        if not stats_rows:
            return
        await UserRepository(self._pool).apply_results(
            [
                PlayerResult(
                    user_id=p.user_id,  # type: ignore[arg-type]
                    display_name=p.display_name,
                    result=p.result or "loss",
                )
                for p in stats_rows
            ],
            game_key,
            conn=conn,
        )

    def _stored_player_result(self, player: MatchPlayer) -> str | None:
        if player.result in _VALID_PLAYER_RESULTS:
            return player.result
        if player.result:
            log.warning(
                "Invalid match result %r for user_id=%s seat=%s; storing NULL",
                player.result,
                player.user_id,
                player.seat_index,
            )
        return None

    async def _reject_move_conflicts(
        self,
        conn: asyncpg.Connection,
        match_id: int,
        moves: list[MoveRecord],
    ) -> None:
        indices = [move.turn_index for move in moves]
        stored = await conn.fetch(
            """
            SELECT turn_index, actor_seat, source, arguments, kind
            FROM moves
            WHERE match_id = $1 AND turn_index = ANY($2)
            """,
            match_id,
            indices,
        )
        by_index = {row["turn_index"]: row for row in stored}
        for move in moves:
            row = by_index.get(move.turn_index)
            if row is None:
                continue
            stored_args = row["arguments"] or {}
            # Compare in stored form: JSON turns tuples into lists and int keys into strings.
            submitted_args = json.loads(json.dumps(move.args or {}))
            if (
                row["source"] != move.source
                or row["actor_seat"] != move.actor_seat
                or row["kind"] != move.kind.value
                or stored_args != submitted_args
            ):
                raise MoveConflict(match_id, move.turn_index)

    async def start_live(self, record: LiveMatchStart) -> tuple[int, str]:
        import random

        async with self._pool.acquire() as conn:
            match_id: int | None = None
            final_code: str | None = None
            code = record.code
            for _ in range(10):
                if code is None:
                    code = generate_match_code(random.Random())
                    record.code = code
                try:
                    async with conn.transaction():
                        row = await conn.fetchrow(
                            """
                            INSERT INTO matches(
                                code, game_key, guild_id, thread_id, seed, settings,
                                status, outcome, total_turns, started_at, ended_at, log_format,
                                game_version, game_build, board_message_id, header_message_id,
                                lobby_channel_id, lobby_message_id,
                                turn_timeout_seconds, turn_timeout_max_strikes,
                                turn_timeout_consequence, lobby_private, lobby_creator_id
                            ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23)
                            RETURNING id, code
                            """,
                            *self._match_insert_params(record),
                        )
                        match_id = row["id"]
                        final_code = row["code"]
                        await self._insert_players(conn, match_id, record.players)
                    break
                except asyncpg.UniqueViolationError as exc:
                    if exc.constraint_name == _LIVE_THREAD_UNIQUE:
                        raise LiveThreadConflict(record.thread_id) from exc
                    if exc.constraint_name != _MATCH_CODE_UNIQUE:
                        raise
                    code = None
            if match_id is None or final_code is None:
                raise RuntimeError("Failed to generate unique match code")
            return match_id, final_code

    async def append_moves(self, match_id: int, moves: list[MoveRecord]) -> None:
        if not moves:
            return
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT status FROM matches WHERE id = $1 FOR UPDATE",
                match_id,
            )
            if row is None or row["status"] != "live":
                raise MatchNotLive(match_id, None if row is None else row["status"])
            await self._insert_moves(conn, match_id, moves)
            await self._reject_move_conflicts(conn, match_id, moves)

    async def set_board_message(self, match_id: int, board_message_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE matches SET board_message_id = $2
                WHERE id = $1 AND status = 'live'
                """,
                match_id,
                board_message_id,
            )

    async def set_header_message(self, match_id: int, header_message_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE matches SET header_message_id = $2
                WHERE id = $1 AND status = 'live'
                """,
                match_id,
                header_message_id,
            )

    async def finish(self, record: FinishedMatch) -> tuple[int, str]:
        match_id = record.match_id
        if match_id is None:
            raise ValueError("finish() requires FinishedMatch.match_id")
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                locked = await conn.fetchrow(
                    "SELECT status FROM matches WHERE id = $1 FOR UPDATE",
                    match_id,
                )
                if locked is None or locked["status"] != "live":
                    raise RuntimeError(
                        f"Live match {match_id} is not available to finish"
                    )
                row = await conn.fetchrow(
                    """
                    UPDATE matches SET
                        status = $2,
                        outcome = $3,
                        total_turns = $4,
                        ended_at = $5
                    WHERE id = $1 AND status = 'live'
                    RETURNING id, code
                    """,
                    match_id,
                    record.status,
                    record.outcome,
                    record.total_turns,
                    record.ended_at or datetime.now(UTC),
                )
                if row is None:
                    raise RuntimeError(
                        f"Live match {match_id} is not available to finish"
                    )
                # Backfill any rows the live writer failed to persist.
                await self._insert_moves(conn, match_id, record.moves)
                if record.players:
                    await conn.executemany(
                        """
                        UPDATE match_players SET
                            user_id = $3,
                            is_bot = $4,
                            bot_difficulty = $5,
                            display_name = $6,
                            role_key = $7,
                            result = $8
                        WHERE match_id = $1 AND seat_index = $2
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
                                self._stored_player_result(player),
                            )
                            for player in record.players
                        ],
                    )
                await self._apply_player_stats(conn, record.game_key, record.players)
            return row["id"], row["code"]

    async def get(
        self, ref: str | int, *, guild_id: int | None = None, include_live: bool = False
    ) -> MatchDetail | None:
        """Look up a match by internal id (int) or 6-character code (str).

        User input is always a code; ids only come from signed custom_ids.
        ``guild_id`` limits the lookup to matches played in that server.
        Live matches are skipped unless ``include_live``.
        """
        status_filter = "TRUE" if include_live else _NOT_LIVE
        if isinstance(ref, int):
            column, key = "id", ref
        else:
            code = normalize_match_code(ref)
            if code is None:
                return None
            column, key = "code", code
        async with self._pool.acquire() as conn:
            if guild_id is None:
                row = await conn.fetchrow(
                    f"SELECT * FROM matches WHERE {column} = $1 AND {status_filter}",
                    key,
                )
            else:
                row = await conn.fetchrow(
                    f"SELECT * FROM matches WHERE {column} = $1 AND guild_id = $2 AND {status_filter}",
                    key,
                    guild_id,
                )
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
                FROM matches WHERE guild_id = $1 AND status <> 'live'
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
        guild_id: int,
        limit: int = 10,
        offset: int = 0,
    ) -> list[MatchSummary]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT m.id, m.code, m.game_key, m.status, m.outcome, m.created_at, m.started_at, m.ended_at, m.total_turns,
                       (SELECT count(*) FROM match_players mp2 WHERE mp2.match_id = m.id) AS player_count,
                       mp.seat_index, mp.role_key, mp.result
                FROM matches m
                JOIN match_players mp ON mp.match_id = m.id
                WHERE mp.user_id = $1 AND m.guild_id = $2 AND m.status <> 'live'
                  AND ($3::text IS NULL OR m.game_key = $3)
                ORDER BY m.created_at DESC LIMIT $4 OFFSET $5
                """,
                user_id,
                guild_id,
                game_key or None,
                limit,
                offset,
            )
            return [self._to_summary(row) for row in rows]

    async def count_for_user(
        self, user_id: int, game_key: str | None, *, guild_id: int
    ) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT count(*) AS total
                FROM matches m
                JOIN match_players mp ON mp.match_id = m.id
                WHERE mp.user_id = $1 AND m.guild_id = $2 AND m.status <> 'live'
                  AND ($3::text IS NULL OR m.game_key = $3)
                """,
                user_id,
                guild_id,
                game_key or None,
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

    def _to_detail(
        self, row: asyncpg.Record, players: list[asyncpg.Record]
    ) -> MatchDetail:
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
            log_format=int(row["log_format"]),
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            game_version=row.get("game_version"),
            game_build=row.get("game_build"),
            board_message_id=row.get("board_message_id"),
            header_message_id=row.get("header_message_id"),
            lobby_channel_id=row.get("lobby_channel_id"),
            lobby_message_id=row.get("lobby_message_id"),
            turn_timeout_seconds=row.get("turn_timeout_seconds"),
            turn_timeout_max_strikes=row.get("turn_timeout_max_strikes"),
            turn_timeout_consequence=row.get("turn_timeout_consequence"),
            lobby_private=bool(row.get("lobby_private")),
            lobby_creator_id=row.get("lobby_creator_id"),
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

    async def list_live(self) -> list[MatchDetail]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM matches
                WHERE status = 'live'
                ORDER BY started_at NULLS LAST, id
                """
            )
            if not rows:
                return []
            match_ids = [row["id"] for row in rows]
            player_rows = await conn.fetch(
                """
                SELECT * FROM match_players
                WHERE match_id = ANY($1)
                ORDER BY match_id, seat_index
                """,
                match_ids,
            )
            by_match: dict[int, list[asyncpg.Record]] = {row["id"]: [] for row in rows}
            for player in player_rows:
                by_match[player["match_id"]].append(player)
            return [self._to_detail(row, by_match[row["id"]]) for row in rows]

    async def count_live(self, game_key: str) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT count(*) AS total
                FROM matches
                WHERE status = 'live' AND game_key = $1
                """,
                game_key,
            )
            return int(row["total"]) if row else 0

    async def delete_for_game(self, game_key: str) -> int:
        async with self._pool.acquire() as conn:
            status = await conn.execute(
                "DELETE FROM matches WHERE game_key = $1", game_key
            )
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
                records.append(
                    MoveRecord(
                        turn_index=row["turn_index"],
                        actor_seat=row["actor_seat"],
                        source=row["source"],
                        args=arguments,
                        kind=LogEntryKind(row["kind"]),
                        created_at=row["created_at"],
                    )
                )
            return records


class UserRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def touch(
        self, user_id: int, display_name: str, *, conn: asyncpg.Connection | None = None
    ) -> None:
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
                if result.result not in _VALID_PLAYER_RESULTS:
                    log.warning(
                        "Skipping stats for invalid result %r (user_id=%s game=%s)",
                        result.result,
                        result.user_id,
                        game_key,
                    )
                    continue
                wins = int(result.result == "win")
                losses = int(result.result == "loss")
                draws = int(result.result == "draw")
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
        async with self._pool.acquire() as owned, owned.transaction():
            await _write(owned)

    async def get_stats(
        self, user_id: int, game_key: str | None, *, guild_id: int
    ) -> UserStats:
        """W/L/D for matches played in one server.

        ``user_game_stats`` is global, so count the stored seats instead, with
        the same rows ``apply_results`` counts: human seats whose result is
        ``win``, ``loss``, or ``draw``.
        AFK seats a bot finished are stored as a human loss.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT count(*) FILTER (WHERE mp.result = 'win') AS wins,
                       count(*) FILTER (WHERE mp.result = 'loss') AS losses,
                       count(*) FILTER (WHERE mp.result = 'draw') AS draws,
                       count(*) AS played
                FROM match_players mp
                JOIN matches m ON m.id = mp.match_id
                WHERE mp.user_id = $1
                  AND NOT mp.is_bot
                  AND mp.result IN ('win', 'loss', 'draw')
                  AND m.status <> 'live'
                  AND m.guild_id = $2
                  AND ($3::text IS NULL OR m.game_key = $3)
                """,
                user_id,
                guild_id,
                game_key or None,
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
