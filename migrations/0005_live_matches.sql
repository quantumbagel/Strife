-- Live matches are rows in matches (status = 'live') rather than a side table.
-- Moves are appended while the match runs; finish() updates the same row.

ALTER TABLE matches ADD COLUMN IF NOT EXISTS game_version TEXT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS board_message_id BIGINT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS header_message_id BIGINT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS lobby_channel_id BIGINT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS lobby_message_id BIGINT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS turn_timeout_seconds INT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS turn_timeout_max_strikes INT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS turn_timeout_consequence TEXT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS lobby_private BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS lobby_creator_id BIGINT;

CREATE INDEX IF NOT EXISTS matches_live_idx ON matches (started_at) WHERE status = 'live';

DROP TABLE IF EXISTS active_games;
