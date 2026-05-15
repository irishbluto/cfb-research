-- =====================================================================
-- team_memory_lifecycle.sql
-- ---------------------------------------------------------------------
-- Adds a `lifecycle_stage` column to team_memory_storylines so writeups
-- can allocate prose real estate by editorial weight, independently of
-- freshness:
--
--   status          answers "did we touch this lately?"
--     active / stale / resolved (existing column — unchanged)
--
--   lifecycle_stage answers "how much space should this get in the writeup?"
--     developing  — new this run or materially advanced (lead the writeup)
--     continuing  — active and load-bearing, no new dimensions (paragraph)
--     settled     — true and important but converged (one clause)
--     retired     — no longer load-bearing (dropped from memory cache)
--
-- The agent (research_agent.py) reads lifecycle_stage from the JSON cache
-- and allocates agent_summary real estate accordingly. The writer
-- (team_memory_writer.py) computes the stage deterministically each run
-- from update history and calendar mode transitions.
--
-- Apply on the puntandrally MariaDB (same DB as team_memory). Idempotent —
-- safe to re-run.
--
-- Companion changes (must be deployed together):
--   scripts/team_memory_writer.py  — computes & stores lifecycle_stage,
--                                    plus calendar-aware retirement pass
--   scripts/research_agent.py      — surfaces stage in prior-memory block,
--                                    plus composition rules in synthesis prompt
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1) Add the column (idempotent via IF NOT EXISTS — MariaDB 10.5+).
--    Placed AFTER status so the two-axis design is obvious in DESC output.
-- ---------------------------------------------------------------------
ALTER TABLE team_memory_storylines
    ADD COLUMN IF NOT EXISTS lifecycle_stage
        ENUM('developing','continuing','settled','retired')
        NOT NULL DEFAULT 'developing'
        AFTER status;

-- ---------------------------------------------------------------------
-- 2) Helpful secondary index for the writer's retirement pass and the
--    cache-rebuild filter (status + lifecycle_stage filtering).
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_slug_status_lifecycle
    ON team_memory_storylines (slug, status, lifecycle_stage, season);

-- ---------------------------------------------------------------------
-- 3) One-shot backfill so the first post-deploy run isn't a blank slate.
--    Token-overlap analysis (the real stage logic) can't run in SQL, so we
--    use update count + status as a coarse proxy. Threads will re-compute
--    their stage on the next team_memory_writer.py run that touches them.
--
--    Heuristic:
--      status=resolved        → retired   (already off the page)
--      JSON_LENGTH(updates) >= 5 → settled    (likely converged on rephrase)
--      JSON_LENGTH(updates) 2-4  → continuing
--      JSON_LENGTH(updates) = 1  → developing
-- ---------------------------------------------------------------------
UPDATE team_memory_storylines
SET lifecycle_stage = CASE
    WHEN status = 'resolved'                   THEN 'retired'
    WHEN JSON_LENGTH(updates) >= 5             THEN 'settled'
    WHEN JSON_LENGTH(updates) BETWEEN 2 AND 4  THEN 'continuing'
    ELSE 'developing'
END
WHERE lifecycle_stage = 'developing';   -- only seed rows still at the default

-- ---------------------------------------------------------------------
-- Sanity checks (run after applying)
-- ---------------------------------------------------------------------
-- DESC team_memory_storylines;
-- SELECT lifecycle_stage, status, COUNT(*) AS n
--   FROM team_memory_storylines
--  WHERE season = 2026
--  GROUP BY lifecycle_stage, status
--  ORDER BY lifecycle_stage, status;
-- SELECT slug, theme, status, lifecycle_stage, JSON_LENGTH(updates) AS updates_n
--   FROM team_memory_storylines
--  WHERE slug = 'notre-dame' AND season = 2026
--  ORDER BY last_updated DESC;

-- ---------------------------------------------------------------------
-- Rollback (only if needed)
-- ---------------------------------------------------------------------
-- ALTER TABLE team_memory_storylines DROP INDEX idx_slug_status_lifecycle;
-- ALTER TABLE team_memory_storylines DROP COLUMN lifecycle_stage;

-- ---------------------------------------------------------------------
-- Future: conference_memory_storylines mirror
-- ---------------------------------------------------------------------
-- The conference memory writer doesn't exist yet (per project memory:
-- "Memory layer DEFERRED until writer_notes stabilize across all 11 confs").
-- When it's built, mirror this ALTER on conference_memory_storylines using
-- the same column definition and backfill heuristic.
