-- Live game threads, so a crash/SIGKILL can be cleaned up on the next boot.
-- A row exists only while the match runs; normal ends delete it.
CREATE TABLE IF NOT EXISTS active_games (
    thread_id  BIGINT PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    game_key   TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
