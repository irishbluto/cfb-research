#!/usr/bin/env python3
"""
team_memory_writer.py
---------------------
Reads completed research/{slug}_latest.json files and writes
team_memory/{slug}.json with a distilled knowledge snapshot
for use in subsequent research_agent.py runs.

Run this after each research_agent.py batch completes. It is fast
(pure JSON read/write, no API calls) — a full 138-team pass takes < 5s.

Usage:
    python3 scripts/team_memory_writer.py                   # all teams with research output
    python3 scripts/team_memory_writer.py --team alabama    # single team
    python3 scripts/team_memory_writer.py --conf sec        # all teams in a conference
    python3 scripts/team_memory_writer.py --conference sec  # alias
    python3 scripts/team_memory_writer.py --all             # all configured teams
"""

import json, sys, logging, argparse
from datetime import datetime
from pathlib import Path

BASE_DIR    = Path("/cfb-research")
OUTPUT_DIR  = BASE_DIR / "research"
MEMORY_DIR  = BASE_DIR / "team_memory"

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s  %(levelname)s  %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )

# ---------------------------------------------------------------------------
# Write memory for one team
# ---------------------------------------------------------------------------
def write_team_memory(slug):
    """Read research output and write/update team_memory file."""
    input_file  = OUTPUT_DIR / f"{slug}_latest.json"
    memory_file = MEMORY_DIR / f"{slug}.json"

    if not input_file.exists():
        logging.warning(f"  [{slug}] No research output found — skipping")
        return False

    try:
        with open(input_file) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logging.error(f"  [{slug}] Invalid JSON in research file: {e}")
        return False
    except Exception as e:
        logging.error(f"  [{slug}] Failed to read research file: {e}")
        return False

    # Increment run_count from existing memory if it exists
    run_count = 1
    if memory_file.exists():
        try:
            with open(memory_file) as f:
                old = json.load(f)
            run_count = old.get("run_count", 0) + 1
        except Exception:
            pass  # Corrupt old memory — start fresh at 1

    # Pull coaching snapshot from passthrough field (added to research output)
    coaching = data.get("coaching_snapshot", {})

    # Pull agent_flags — agent writes these if the field is in the output schema.
    # Fall back to empty structure if the field isn't present yet (first run after deploy).
    agent_flags = data.get("agent_flags", {
        "high_confidence":    [],
        "low_confidence":     [],
        "watch_for_next_run": []
    })

    # Defensive cleanup — ensure all three keys exist even if agent only wrote some
    for key in ("high_confidence", "low_confidence", "watch_for_next_run"):
        if key not in agent_flags or not isinstance(agent_flags[key], list):
            agent_flags[key] = []

    memory = {
        "team":             data.get("team", slug),
        "slug":             slug,
        "last_run":         data.get("research_date", datetime.now().strftime("%Y-%m-%d")),
        "run_count":        run_count,
        "mode":             data.get("mode", ""),
        "prior_summary":    data.get("agent_summary", ""),
        "prior_sentiment":  data.get("overall_sentiment", ""),
        "prior_storylines": data.get("key_storylines", [])[:5],
        "prior_injury_flags": data.get("injury_flags", [])[:3],
        "coaching_snapshot": coaching,
        "agent_flags":      agent_flags,
    }

    MEMORY_DIR.mkdir(exist_ok=True)
    try:
        with open(memory_file, 'w') as f:
            json.dump(memory, f, indent=2)
        logging.info(f"  ✓ {slug} — memory written (run #{run_count}, mode: {memory['mode']})")
        return True
    except Exception as e:
        logging.error(f"  [{slug}] Failed to write memory: {e}")
        return False

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    setup_logging()

    parser = argparse.ArgumentParser(
        description='Write team memory snapshots from completed research output.'
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument('--team',       default=None, help='Single team slug e.g. "alabama"')
    target.add_argument('--conf',       default=None, dest='conf', help='Conference slug e.g. "sec"')
    target.add_argument('--conference', default=None, dest='conf', help='Alias for --conf')
    target.add_argument('--all',        action='store_true', help='All configured teams')
    args = parser.parse_args()

    # Import conference map from research_agent to avoid duplicating the team lists
    sys.path.insert(0, str(BASE_DIR / "scripts"))
    try:
        from research_agent import CONFERENCE_TEAMS
    except ImportError as e:
        logging.error(f"Could not import CONFERENCE_TEAMS from research_agent: {e}")
        sys.exit(1)

    if args.team:
        teams = [args.team]
        logging.info(f"Writing memory for team: {args.team}")

    elif args.conf:
        conf = args.conf.lower()
        teams = CONFERENCE_TEAMS.get(conf, [])
        if not teams:
            logging.error(f"Unknown conference: '{conf}'")
            logging.error(f"Known: {sorted(CONFERENCE_TEAMS.keys())}")
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
        # Default: all teams that have a research output file
        teams = sorted(
            f.stem.replace("_latest", "")
            for f in OUTPUT_DIR.glob("*_latest.json")
        )
        logging.info(f"Writing memory for {len(teams)} teams with existing research output")

    if not teams:
        logging.warning("No teams to process.")
        return

    ok    = sum(write_team_memory(slug) for slug in teams)
    skipped = len(teams) - ok
    logging.info(f"Done — {ok}/{len(teams)} memory files written"
                 + (f", {skipped} skipped (no output file)" if skipped else ""))

if __name__ == "__main__":
    main()