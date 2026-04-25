#!/usr/bin/env python3
"""
team_memory_writer.py  (v2 — storyline threading + DB persistence)
------------------------------------------------------------------
Reads completed research/{slug}_latest.json files, maintains storyline
threads across runs, detects coaching changes, and persists everything
to MariaDB. Also writes team_memory/{slug}.json as a fast-read cache
for research_agent.py prompt injection.

Usage:
    python3 scripts/team_memory_writer.py                   # all teams with research output
    python3 scripts/team_memory_writer.py --team alabama    # single team
    python3 scripts/team_memory_writer.py --conf sec        # all teams in a conference
    python3 scripts/team_memory_writer.py --conference sec  # alias
    python3 scripts/team_memory_writer.py --all             # all configured teams
"""

import json, sys, logging, argparse, os, re
from datetime import datetime
from pathlib import Path

import pymysql

BASE_DIR    = Path("/cfb-research")
OUTPUT_DIR  = BASE_DIR / "research"
MEMORY_DIR  = BASE_DIR / "team_memory"

CURRENT_SEASON = 2026
SLUG_TO_CONF = {}  # Built after CONFERENCE_TEAMS is imported in main()
MAX_ACTIVE_STORYLINES = 10
STALE_AFTER_RUNS = 3       # mark stale if not updated in N runs
RESOLVE_AFTER_RUNS = 5     # resolve if stale and still not updated after N total

STOPWORDS = frozenset(
    "the a an is are was were in on at to for of and but or with from as by "
    "after new team season has been will could should their this that".split()
)


# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------
def get_db():
    """Connect to MariaDB using .env credentials."""
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


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s  %(levelname)s  %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )


# ---------------------------------------------------------------------------
# Storyline matching
# ---------------------------------------------------------------------------
def tokenize(text):
    """Extract meaningful tokens from a storyline string."""
    import re
    words = re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def match_storyline(new_text, matchable_threads, threshold=0.35, min_overlap=2):
    """
    Find the best matching thread (active OR stale) for a new storyline.
    Returns the thread dict if matched, else None.

    Asymmetric token overlap: scores how much of the existing theme's vocabulary
    survives into the new text. Match requires:
      - score >= threshold (default 0.35), AND
      - at least min_overlap shared tokens (default 2; guards against single-
        word false positives on short themes).

    Caller reactivates stale matches by setting status='active' on the row.
    """
    new_tokens = tokenize(new_text)
    best_match = None
    best_score = 0.0

    for thread in matchable_threads:
        theme_tokens = tokenize(thread["theme"])
        if not theme_tokens:
            continue
        shared = theme_tokens & new_tokens
        if len(shared) < min_overlap:
            continue
        score = len(shared) / len(theme_tokens)
        if score >= threshold and score > best_score:
            best_score = score
            best_match = thread

    return best_match


# ---------------------------------------------------------------------------
# Coaching diff
# ---------------------------------------------------------------------------
def _normalize_coach(name):
    """Strip coordinator ranking annotations like (#67) for comparison."""
    return re.sub(r'\s*\(#\d+\)', '', name).strip()


def detect_coaching_changes(old_hc, old_oc, old_dc, new_snapshot):
    """Compare old coaching staff against new snapshot, return list of change descriptions."""
    changes = []
    new_hc = new_snapshot.get("head_coach", "")
    new_oc = new_snapshot.get("oc", "")
    new_dc = new_snapshot.get("dc", "")

    if old_hc and new_hc and _normalize_coach(old_hc) != _normalize_coach(new_hc):
        changes.append(("HC change: {} → {}".format(old_hc, new_hc), "head_coach"))
    if old_oc and new_oc and _normalize_coach(old_oc) != _normalize_coach(new_oc):
        changes.append(("OC change: {} → {}".format(old_oc, new_oc), "oc"))
    if old_dc and new_dc and _normalize_coach(old_dc) != _normalize_coach(new_dc):
        changes.append(("DC change: {} → {}".format(old_dc, new_dc), "dc"))

    return changes


# ---------------------------------------------------------------------------
# Core: write memory for one team
# ---------------------------------------------------------------------------
def write_team_memory(slug, db):
    """Read research output, update storyline threads, persist to DB + JSON cache."""
    input_file = OUTPUT_DIR / f"{slug}_latest.json"
    today = datetime.now().strftime("%Y-%m-%d")

    if not input_file.exists():
        logging.warning(f"  [{slug}] No research output found — skipping")
        return False

    try:
        with open(input_file) as f:
            data = json.load(f)
    except (json.JSONDecodeError, Exception) as e:
        logging.error(f"  [{slug}] Failed to read research file: {e}")
        return False

    # Skip stale research files (not from today)
    research_date = data.get("research_date", "")
    if research_date and research_date != today:
        logging.info(f"  [{slug}] Research file from {research_date} (not today) — skipping")
        return False

    cur = db.cursor()

    # ------------------------------------------------------------------
    # 1. Load existing memory row (if any)
    # ------------------------------------------------------------------
    cur.execute("SELECT * FROM team_memory WHERE slug = %s", (slug,))
    existing = cur.fetchone()
    run_count = (existing["run_count"] + 1) if existing else 1

    # ------------------------------------------------------------------
    # 2. Coaching change detection
    # ------------------------------------------------------------------
    coaching = data.get("coaching_snapshot", {})
    coaching_changes = []
    if existing:
        coaching_changes = detect_coaching_changes(
            existing.get("coaching_hc", ""),
            existing.get("coaching_oc", ""),
            existing.get("coaching_dc", ""),
            coaching
        )

    # ------------------------------------------------------------------
    # 3. Load matchable storyline threads from DB (active + stale)
    # Stale threads are included so a continuing storyline whose phrasing
    # drifted between runs can match and reactivate the existing thread,
    # rather than spawning a duplicate. Reactivation happens in the UPDATE
    # path inside step 4.
    # ------------------------------------------------------------------
    cur.execute(
        "SELECT * FROM team_memory_storylines WHERE slug = %s AND status IN ('active','stale') AND season = %s",
        (slug, CURRENT_SEASON)
    )
    matchable_threads = cur.fetchall()
    # Parse the JSON updates column
    for t in matchable_threads:
        try:
            t["_updates"] = json.loads(t["updates"]) if isinstance(t["updates"], str) else t["updates"]
        except Exception:
            t["_updates"] = []

    # ------------------------------------------------------------------
    # 4. Match new storylines against active threads
    # ------------------------------------------------------------------
    new_storylines = data.get("key_storylines", [])[:5]
    matched_ids = set()

    for storyline_text in new_storylines:
        match = match_storyline(storyline_text, matchable_threads)
        if match and match["id"] not in matched_ids:
            # Update existing thread
            matched_ids.add(match["id"])
            updates = match["_updates"]
            updates.append({"date": today, "note": storyline_text})
            # Keep last 8 updates max to control token cost
            updates = updates[-8:]
            cur.execute(
                "UPDATE team_memory_storylines SET updates = %s, last_updated = %s, status = 'active' WHERE id = %s",
                (json.dumps(updates), today, match["id"])
            )
        else:
            # Create new thread
            updates_json = json.dumps([{"date": today, "note": storyline_text}])
            # Build a short theme from the first ~80 chars
            theme = storyline_text[:80].rstrip(". ")
            cur.execute(
                """INSERT INTO team_memory_storylines
                   (slug, theme, status, first_seen, last_updated, updates, source_type, season)
                   VALUES (%s, %s, 'active', %s, %s, %s, 'agent', %s)""",
                (slug, theme, today, today, updates_json, CURRENT_SEASON)
            )

    # ------------------------------------------------------------------
    # 5. Auto-create threads for coaching changes
    # ------------------------------------------------------------------
    for change_desc, _role in coaching_changes:
        logging.info(f"  [{slug}] Coaching change detected: {change_desc}")
        updates_json = json.dumps([{"date": today, "note": change_desc}])
        cur.execute(
            """INSERT INTO team_memory_storylines
               (slug, theme, status, first_seen, last_updated, updates, source_type, season)
               VALUES (%s, %s, 'active', %s, %s, %s, 'coaching_diff', %s)""",
            (slug, change_desc, today, today, updates_json, CURRENT_SEASON)
        )

    # ------------------------------------------------------------------
    # 6. Age out stale / resolved threads
    # ------------------------------------------------------------------
    # Count how many runs have occurred since each thread was last updated.
    # We use (current run_count - thread's last run_count) but since we don't
    # track per-thread run counts, we use a date-gap heuristic:
    # - Active threads not updated today → increment a "missed runs" counter
    #   (we track this via the gap between last_updated and today)
    # - Mark stale if not updated in this run AND not in the N most recent threads
    #   (keeps the freshest threads active even if not mentioned every single run)

    # Any active thread NOT updated today and NOT in the most recent N by last_updated → stale
    cur.execute(
        """UPDATE team_memory_storylines
           SET status = 'stale'
           WHERE slug = %s AND status = 'active' AND season = %s
             AND last_updated < %s
             AND id NOT IN (
                 SELECT id FROM (
                     SELECT id FROM team_memory_storylines
                     WHERE slug = %s AND status = 'active' AND season = %s
                     ORDER BY last_updated DESC
                     LIMIT %s
                 ) AS recent
             )""",
        (slug, CURRENT_SEASON, today, slug, CURRENT_SEASON, STALE_AFTER_RUNS)
    )
    # Stale threads not updated in 30+ days → resolved (generous window — seasons are long)
    cur.execute(
        """UPDATE team_memory_storylines
           SET status = 'resolved'
           WHERE slug = %s AND status = 'stale' AND season = %s
             AND last_updated < DATE_SUB(%s, INTERVAL 30 DAY)""",
        (slug, CURRENT_SEASON, today)
    )

    # ------------------------------------------------------------------
    # 7. Enforce storyline cap
    # ------------------------------------------------------------------
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM team_memory_storylines WHERE slug = %s AND status = 'active' AND season = %s",
        (slug, CURRENT_SEASON)
    )
    active_count = cur.fetchone()["cnt"]
    if active_count > MAX_ACTIVE_STORYLINES:
        excess = active_count - MAX_ACTIVE_STORYLINES
        cur.execute(
            """UPDATE team_memory_storylines
               SET status = 'resolved'
               WHERE slug = %s AND status = 'active' AND season = %s
               ORDER BY last_updated ASC, JSON_LENGTH(updates) ASC
               LIMIT %s""",
            (slug, CURRENT_SEASON, excess)
        )

    # ------------------------------------------------------------------
    # 8. Agent flags (defensive cleanup)
    # ------------------------------------------------------------------
    agent_flags = data.get("agent_flags", {})
    for key in ("high_confidence", "low_confidence", "watch_for_next_run"):
        if key not in agent_flags or not isinstance(agent_flags[key], list):
            agent_flags[key] = []

    # ------------------------------------------------------------------
    # 9. Upsert team_memory row
    # ------------------------------------------------------------------
    team_name = data.get("team", slug)
    conference = SLUG_TO_CONF.get(slug, "")
    mode = data.get("mode", "")

    cur.execute(
        """INSERT INTO team_memory
           (slug, team_name, conference, last_run, run_count, mode,
            prior_summary, prior_sentiment, sentiment_score,
            coaching_hc, coaching_oc, coaching_dc,
            high_confidence, low_confidence, watch_for_next_run)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE
            team_name       = VALUES(team_name),
            conference      = VALUES(conference),
            last_run        = VALUES(last_run),
            run_count       = VALUES(run_count),
            mode            = VALUES(mode),
            prior_summary   = VALUES(prior_summary),
            prior_sentiment = VALUES(prior_sentiment),
            sentiment_score = VALUES(sentiment_score),
            coaching_hc     = VALUES(coaching_hc),
            coaching_oc     = VALUES(coaching_oc),
            coaching_dc     = VALUES(coaching_dc),
            high_confidence = VALUES(high_confidence),
            low_confidence  = VALUES(low_confidence),
            watch_for_next_run  = VALUES(watch_for_next_run)""",
        (
            slug, team_name, conference, today, run_count, mode,
            data.get("agent_summary", ""),
            data.get("overall_sentiment", ""),
            data.get("sentiment_score"),
            coaching.get("head_coach", ""),
            coaching.get("oc", ""),
            coaching.get("dc", ""),
            json.dumps(agent_flags.get("high_confidence", [])),
            json.dumps(agent_flags.get("low_confidence", [])),
            json.dumps(agent_flags.get("watch_for_next_run", [])),
        )
    )

    # ------------------------------------------------------------------
    # 10. Write JSON cache file (for research_agent.py prompt injection)
    # ------------------------------------------------------------------
    # Re-fetch active storylines after all updates
    cur.execute(
        """SELECT theme, status, first_seen, last_updated, updates, source_type
           FROM team_memory_storylines
           WHERE slug = %s AND status IN ('active','stale') AND season = %s
           ORDER BY last_updated DESC""",
        (slug, CURRENT_SEASON)
    )
    storylines_rows = cur.fetchall()
    storyline_threads = []
    for row in storylines_rows:
        try:
            updates = json.loads(row["updates"]) if isinstance(row["updates"], str) else row["updates"]
        except Exception:
            updates = []
        storyline_threads.append({
            "theme":        row["theme"],
            "status":       row["status"],
            "first_seen":   str(row["first_seen"]),
            "last_updated": str(row["last_updated"]),
            "updates":      updates,
            "source_type":  row["source_type"],
        })

    cache = {
        "team":              team_name,
        "slug":              slug,
        "last_run":          today,
        "run_count":         run_count,
        "mode":              mode,
        "prior_summary":     data.get("agent_summary", ""),
        "prior_sentiment":   data.get("overall_sentiment", ""),
        "prior_storylines":  data.get("key_storylines", [])[:5],
        "prior_injury_flags": data.get("injury_flags", [])[:10],
        "coaching_snapshot": coaching,
        "agent_flags":       agent_flags,
        "storyline_threads": storyline_threads,
    }

    MEMORY_DIR.mkdir(exist_ok=True)
    try:
        with open(MEMORY_DIR / f"{slug}.json", 'w') as f:
            json.dump(cache, f, indent=2)
        active_count = sum(1 for t in storyline_threads if t.get("status") == "active")
        stale_count = len(storyline_threads) - active_count
        logging.info(f"  ✓ {slug} — memory written (run #{run_count}, mode: {mode}, "
                     f"{active_count} active / {stale_count} stale)")
        return True
    except Exception as e:
        logging.error(f"  [{slug}] Failed to write cache file: {e}")
        return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    setup_logging()

    parser = argparse.ArgumentParser(
        description='Write team memory with storyline threading (v2).'
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument('--team',       default=None, help='Single team slug')
    target.add_argument('--conf',       default=None, dest='conf', help='Conference slug')
    target.add_argument('--conference', default=None, dest='conf', help='Alias for --conf')
    target.add_argument('--all',        action='store_true', help='All configured teams')
    args = parser.parse_args()

    sys.path.insert(0, str(BASE_DIR / "scripts"))
    try:
        from research_agent import CONFERENCE_TEAMS
    except ImportError as e:
        logging.error(f"Could not import CONFERENCE_TEAMS: {e}")
        sys.exit(1)

    # Build reverse lookup: slug → conference abbreviation
    global SLUG_TO_CONF
    for conf_key, slugs in CONFERENCE_TEAMS.items():
        for s in slugs:
            SLUG_TO_CONF[s] = conf_key.upper()

    if args.team:
        teams = [args.team]
        logging.info(f"Writing memory for team: {args.team}")
    elif args.conf:
        conf = args.conf.lower()
        teams = CONFERENCE_TEAMS.get(conf, [])
        if not teams:
            logging.error(f"Unknown conference: '{conf}'")
            sys.exit(1)
        logging.info(f"Writing memory for {len(teams)} teams in {conf.upper()}")
    elif args.all:
        seen, teams = set(), []
        for tlist in CONFERENCE_TEAMS.values():
            for t in tlist:
                if t not in seen:
                    seen.add(t)
                    teams.append(t)
        logging.info(f"Writing memory for all {len(teams)} configured teams")
    else:
        teams = sorted(
            f.stem.replace("_latest", "")
            for f in OUTPUT_DIR.glob("*_latest.json")
        )
        logging.info(f"Writing memory for {len(teams)} teams with existing research output")

    if not teams:
        logging.warning("No teams to process.")
        return

    db = get_db()
    try:
        ok = sum(write_team_memory(slug, db) for slug in teams)
        skipped = len(teams) - ok
        logging.info(f"Done — {ok}/{len(teams)} memory files written"
                     + (f", {skipped} skipped" if skipped else ""))
    finally:
        db.close()


if __name__ == "__main__":
    main()