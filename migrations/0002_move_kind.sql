ALTER TABLE moves
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'game';

UPDATE moves
SET kind = 'system'
WHERE source IN ('forfeit', 'game_end', 'bot_takeover');
