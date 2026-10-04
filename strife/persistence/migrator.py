from __future__ import annotations

from pathlib import Path

import asyncpg

from strife.logging import get_logger

log = get_logger("persistence.migrator")

# Session-level lock so concurrent bot processes cannot migrate together.
_MIGRATION_LOCK_KEY = 714_882_391_046_221
_SCHEMA_MIGRATIONS_PKEY = "schema_migrations_pkey"


class Migrator:
    def __init__(self, pool: asyncpg.Pool, migrations_dir: Path) -> None:
        self._pool = pool
        self._dir = migrations_dir

    async def run(self) -> list[str]:
        async with self._pool.acquire() as conn:
            await conn.execute("SELECT pg_advisory_lock($1)", _MIGRATION_LOCK_KEY)
            try:
                return await self._apply(conn)
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", _MIGRATION_LOCK_KEY)

    async def reset(self) -> None:
        reset_path = self._dir / "reset.sql"
        if not reset_path.exists():
            raise FileNotFoundError(reset_path)
        sql = reset_path.read_text(encoding="utf-8")
        async with self._pool.acquire() as conn:
            await conn.execute("SELECT pg_advisory_lock($1)", _MIGRATION_LOCK_KEY)
            try:
                async with conn.transaction():
                    await conn.execute(sql)
                await self._apply(conn)
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", _MIGRATION_LOCK_KEY)

    async def _apply(self, conn: asyncpg.Connection) -> list[str]:
        applied: list[str] = []
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        rows = await conn.fetch("SELECT version FROM schema_migrations")
        done = {row["version"] for row in rows}
        files = sorted(self._dir.glob("*.sql"))
        for path in files:
            if path.name == "reset.sql":
                continue
            version = path.stem
            if version in done:
                continue
            sql = path.read_text(encoding="utf-8")
            try:
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations(version) VALUES($1)",
                        version,
                    )
            except asyncpg.UniqueViolationError as exc:
                if exc.constraint_name != _SCHEMA_MIGRATIONS_PKEY:
                    raise
                log.info("Migration %s already applied", version)
                done.add(version)
                continue
            done.add(version)
            applied.append(version)
            log.info("Applied migration %s", version)
        return applied
