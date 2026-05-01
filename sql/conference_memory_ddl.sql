-- =====================================================================
-- conference_memory_ddl.sql
-- ---------------------------------------------------------------------
-- DDL for the conference-preview memory layer. Mirrors the team_memory /
-- team_memory_storylines pattern but keyed on conference slug instead of
-- team slug. Coaching-change tracking is omitted (handled at team level).
--
-- Apply on the puntandrally MariaDB (same DB as team_memory). Idempotent
-- via IF NOT EXISTS so re-running is safe.
--
-- Companion script: conference_memory_writer.py (clones team_memory_writer
-- against these tables; generates conference_memory/<slug>.json caches for
-- the next conference_research_agent.py run).
-- =====================================================================

-- ---------------------------------------------------------------------
-- conference_memory
-- One row per conference. Holds the latest distilled state for the next
-- agent run's "PRIOR RUN NOTES" prompt block.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conference_memory (
    conf_slug           VARCHAR(20)  NOT NULL,
    conf_display        VARCHAR(50)  NOT NULL DEFAULT '',
    last_run            DATE         DEFAULT NULL,
    run_count           INT          NOT NULL DEFAULT 0,
    mode                VARCHAR(30)  DEFAULT '',
    prior_summary       TEXT,
    prior_sentiment     VARCHAR(40)  DEFAULT '',
    sentiment_score     DECIMAL(4,2) DEFAULT NULL,

    -- Agent-flagged JSON arrays
    high_confidence     LONGTEXT,
    low_confidence      LONGTEXT,
    watch_for_next_run  LONGTEXT,

    PRIMARY KEY (conf_slug)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- conference_memory_storylines
-- Storyline threads per conference, with token-overlap matching across
-- runs (status = active | stale | resolved). Mirrors team_memory_storylines.
-- Aging logic and dedupe handled in conference_memory_writer.py.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conference_memory_storylines (
    id            BIGINT       NOT NULL AUTO_INCREMENT,
    conf_slug     VARCHAR(20)  NOT NULL,
    theme         VARCHAR(255) NOT NULL,
    status        ENUM('active','stale','resolved') NOT NULL DEFAULT 'active',
    first_seen    DATE         NOT NULL,
    last_updated  DATE         NOT NULL,

    -- JSON array of {date, note} objects, capped at the most recent 8
    -- by the writer to keep prompt token cost predictable.
    updates       LONGTEXT,

    -- Provenance: 'agent' = surfaced by the research agent's storylines list.
    -- (No 'coaching_diff' source at conf level; coaching changes are
    -- handled per-team by team_memory_writer.)
    source_type   VARCHAR(30)  NOT NULL DEFAULT 'agent',

    season        INT          NOT NULL,

    PRIMARY KEY (id),
    KEY idx_conf_status_season (conf_slug, status, season),
    KEY idx_last_updated       (last_updated)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- Sanity-check queries (run after applying)
-- ---------------------------------------------------------------------
-- SHOW CREATE TABLE conference_memory;
-- SHOW CREATE TABLE conference_memory_storylines;
-- DESC conference_memory;
-- DESC conference_memory_storylines;

-- ---------------------------------------------------------------------
-- Rollback (only if needed; this drops all stored memory)
-- ---------------------------------------------------------------------
-- DROP TABLE IF EXISTS conference_memory_storylines;
-- DROP TABLE IF EXISTS conference_memory;
