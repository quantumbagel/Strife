-- Live-row integrity: status domain, one live match per thread, plugin build id.
-- Idempotent; safe to run against a database that already has match rows.

-- Block concurrent inserts so the dedup below holds until the unique index exists.
LOCK TABLE matches IN SHARE ROW EXCLUSIVE MODE;

ALTER TABLE matches ALTER COLUMN log_format SET DEFAULT 3;

UPDATE matches
SET status = 'abandoned'
WHERE status NOT IN ('live', 'completed', 'abandoned');

UPDATE matches AS m
SET status = 'abandoned', ended_at = now()
FROM (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY thread_id
               ORDER BY started_at DESC NULLS LAST, id DESC
           ) AS rn
    FROM matches
    WHERE status = 'live' AND thread_id IS NOT NULL
) AS dups
WHERE m.id = dups.id AND dups.rn > 1;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'matches_status_check'
          AND conrelid = 'matches'::regclass
    ) THEN
        ALTER TABLE matches
            ADD CONSTRAINT matches_status_check
            CHECK (status IN ('live', 'completed', 'abandoned'));
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS matches_live_thread_uidx
    ON matches (thread_id)
    WHERE status = 'live' AND thread_id IS NOT NULL;

ALTER TABLE matches ADD COLUMN IF NOT EXISTS game_build TEXT;
