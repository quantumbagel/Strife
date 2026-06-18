CREATE TABLE IF NOT EXISTS guilds (
    guild_id           BIGINT PRIMARY KEY,
    default_channel_id BIGINT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS matches (
    id          BIGSERIAL PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    game_key    TEXT NOT NULL,
    guild_id    BIGINT NOT NULL REFERENCES guilds(guild_id),
    thread_id   BIGINT,
    seed        BIGINT NOT NULL,
    settings    JSONB NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL,
    outcome     JSONB NOT NULL DEFAULT '{}',
    total_turns INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at  TIMESTAMPTZ,
    ended_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS matches_guild_created_idx ON matches (guild_id, created_at DESC);
CREATE INDEX IF NOT EXISTS matches_game_idx ON matches (game_key);

CREATE TABLE IF NOT EXISTS match_players (
    match_id       BIGINT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    seat_index     INT NOT NULL,
    user_id        BIGINT,
    is_bot         BOOLEAN NOT NULL DEFAULT FALSE,
    bot_difficulty TEXT,
    display_name   TEXT NOT NULL,
    role_key       TEXT,
    result         TEXT,
    PRIMARY KEY (match_id, seat_index)
);
CREATE INDEX IF NOT EXISTS match_players_user_idx ON match_players (user_id);

CREATE TABLE IF NOT EXISTS moves (
    id         BIGSERIAL PRIMARY KEY,
    match_id   BIGINT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    turn_index INT NOT NULL,
    actor_seat INT,
    source     TEXT NOT NULL,
    arguments  JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (match_id, turn_index)
);

CREATE TABLE IF NOT EXISTS users (
    user_id      BIGINT PRIMARY KEY,
    display_name TEXT,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_game_stats (
    user_id  BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    game_key TEXT   NOT NULL,
    wins     INT NOT NULL DEFAULT 0,
    losses   INT NOT NULL DEFAULT 0,
    draws    INT NOT NULL DEFAULT 0,
    played   INT NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, game_key)
);
