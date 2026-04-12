#!/usr/bin/env python3
"""
seed_team_memory_db.py  (one-time migration)
---------------------------------------------
Reads existing team_memory/{slug}.json files (v1 format) and seeds
the new team_memory + team_memory_storylines DB tables.

Run this ONCE after creating the DB tables and before the first v2
team_memory_writer.py run.

Usage:
    python3 scripts/seed_team_memory_db.py              # all existing JSON files
    python3 scripts/seed_team_memory_db.py --dry-run    # preview without writing to DB

What it does:
    1. Reads each team_memory/{slug}.json
    2. INSERTs a row into team_memory (or skips if already exists)
    3. Creates an initial team_memory_storylines row for each prior_storyline
    4. Logs everything for review
"""

import json, sys, logging, argparse, os
from pathlib import Path

import pymysql

BASE_DIR    = Path("/cfb-research")
MEMORY_DIR  = BASE_DIR / "team_memory"
CURRENT_SEASON = 2026


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s  %(levelname)s  %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )


def get_db():
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
    return pymysql.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def seed_team(slug, data, cur, dry_run=False):
    """Seed one team's memory + storylines into the DB."""

    team_name   = data.get("team", slug)
    last_run    = data.get("last_run", "2026-04-12")
    run_count   = data.get("run_count", 1)
    mode        = data.get("mode", "spring_offseason")
    summary     = data.get("prior_summary", "")
    sentiment   = data.get("prior_sentiment", "")
    coaching    = data.get("coaching_snapshot", {})
    agent_flags = data.get("agent_flags", {})
    storylines  = data.get("prior_storylines", [])

    # Defensive cleanup on agent_flags
    for key in ("high_confidence", "low_confidence", "watch_for_next_run"):
        if key not in agent_flags or not isinstance(agent_flags[key], list):
            agent_flags[key] = []

    # ----- team_memory row -----
    if dry_run:
        logging.info(f"  [DRY RUN] Would insert team_memory: {slug} "
                     f"(run #{run_count}, {len(storylines)} storylines)")
    else:
        # Check if row already exists (don't overwrite v2 data if script is re-run)
        cur.execute("SELECT slug FROM team_memory WHERE slug = %s", (slug,))
        if cur.fetchone():
            logging.info(f"  [{slug}] Already in team_memory — skipping (won't overwrite)")
            return False

        cur.execute(
            """INSERT INTO team_memory
               (slug, team_name, conference, last_run, run_count, mode,
                prior_summary, prior_sentiment, sentiment_score,
                coaching_hc, coaching_oc, coaching_dc,
                high_confidence, low_confidence, watch_for_next_run)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                slug,
                team_name,
                "",  # conference not stored in v1 JSON — will be filled on next v2 run
                last_run,
                run_count,
                mode,
                summary,
                sentiment,
                None,  # sentiment_score not in v1 JSON
                coaching.get("head_coach", ""),
                coaching.get("oc", ""),
                coaching.get("dc", ""),
                json.dumps(agent_flags.get("high_confidence", [])),
                json.dumps(agent_flags.get("low_confidence", [])),
                json.dumps(agent_flags.get("watch_for_next_run", [])),
            )
        )

    # ----- team_memory_storylines rows -----
    for storyline_text in storylines:
        if not storyline_text or not storyline_text.strip():
            continue

        theme = storyline_text[:80].rstrip(". ")
        updates_json = json.dumps([{"date": last_run, "note": storyline_text}])

        if dry_run:
            logging.info(f"    [DRY RUN] Would insert storyline: {theme}")
        else:
            cur.execute(
                """INSERT INTO team_memory_storylines
                   (slug, theme, status, first_seen, last_updated, updates, source_type, season)
                   VALUES (%s, %s, 'active', %s, %s, %s, 'agent', %s)""",
                (slug, theme, last_run, last_run, updates_json, CURRENT_SEASON)
            )

    storyline_count = len([s for s in storylines if s and s.strip()])
    logging.info(f"  {'[DRY RUN] ' if dry_run else ''}✓ {slug} — "
                 f"seeded (run #{run_count}, {storyline_count} storylines)")
    return True


def main():
    setup_logging()

    parser = argparse.ArgumentParser(description='Seed team_memory DB tables from existing JSON files.')
    parser.add_argument('--dry-run', action='store_true', help='Preview without writing to DB')
    args = parser.parse_args()

    if not MEMORY_DIR.exists():
        logging.error(f"Memory directory not found: {MEMORY_DIR}")
        sys.exit(1)

    json_files = sorted(MEMORY_DIR.glob("*.json"))
    if not json_files:
        logging.warning("No JSON memory files found.")
        return

    logging.info(f"Found {len(json_files)} memory files to seed")
    if args.dry_run:
        logging.info("=== DRY RUN MODE — no DB writes ===")

    db = None
    cur = None
    if not args.dry_run:
        db = get_db()
        cur = db.cursor()
    else:
        # Dummy cursor for dry run
        cur = None

    ok = 0
    skipped = 0
    errors = 0

    for f in json_files:
        slug = f.stem
        try:
            with open(f) as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, Exception) as e:
            logging.error(f"  [{slug}] Failed to read JSON: {e}")
            errors += 1
            continue

        try:
            result = seed_team(slug, data, cur, dry_run=args.dry_run)
            if result:
                ok += 1
            else:
                skipped += 1
        except Exception as e:
            logging.error(f"  [{slug}] DB error: {e}")
            errors += 1

    if db:
        db.close()

    logging.info(f"\nDone — {ok} seeded, {skipped} skipped (already exist), {errors} errors")
    if args.dry_run:
        logging.info("Re-run without --dry-run to write to DB.")


if __name__ == "__main__":
    main()