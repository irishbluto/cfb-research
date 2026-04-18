#!/usr/bin/env python3
"""
run_national_landscape.py
-------------------------
Master pipeline runner for the national CFB landscape process.
Two-step pipeline: fetch sources → run Claude synthesis agent.

Pipeline steps:
  1. national_fetcher.py       — fetches RSS (written + podcast) + YouTube API
  2. national_landscape_agent.py — Claude synthesizes into writeup + storylines

Output: /cfb-research/national/landscape_latest.json

Step control flags:
  --fetch-only    Run step 1 only (fetch sources, skip agent)
  --skip-fetch    Skip step 1 (use cached fetched_sources.json, re-run agent)

Pass-through flags:
  --days N        Lookback window in days (default: 7)
  --no-youtube    Skip YouTube API calls (use when quota is low)
  --no-prefetch   Skip article body prefetch
  --dry-run       Print agent prompt without running Claude

-------------------------------------------------------------------------------
USAGE EXAMPLES
-------------------------------------------------------------------------------

# Full pipeline — fetch + synthesize
    python3 scripts/run_national_landscape.py

# Fetch only — review sources before running agent
    python3 scripts/run_national_landscape.py --fetch-only

# Re-run agent on existing fetched data (already fetched today)
    python3 scripts/run_national_landscape.py --skip-fetch

# Extended lookback (e.g. first run or after a gap)
    python3 scripts/run_national_landscape.py --days 14

# YouTube quota low — skip YouTube entirely
    python3 scripts/run_national_landscape.py --no-youtube

# Dry run — see the prompt without running Claude
    python3 scripts/run_national_landscape.py --dry-run
"""

import sys, time, argparse, logging, subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR     = Path("/cfb-research")
SCRIPTS_DIR  = BASE_DIR / "scripts"
NATIONAL_DIR = BASE_DIR / "national"
LOG_DIR      = BASE_DIR / "logs"

def setup_logging():
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"national_pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ]
    )
    return log_file

def run_step(label, cmd, fatal=True):
    """Run a pipeline step as a subprocess. Returns True on success."""
    logging.info(f"{'='*50}")
    logging.info(f"Step: {label}")
    logging.info(f"  Command: {' '.join(cmd)}")

    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=900,
            cwd=str(BASE_DIR),
        )
        elapsed = round(time.time() - start, 1)

        if result.returncode == 0:
            logging.info(f"  ✓ {label} complete ({elapsed}s)")
            if result.stdout:
                # Log last few lines of output
                lines = result.stdout.strip().split('\n')
                for line in lines[-5:]:
                    logging.info(f"    {line}")
            return True
        else:
            logging.error(f"  ✗ {label} failed (exit {result.returncode}, {elapsed}s)")
            if result.stderr:
                logging.error(f"    stderr: {result.stderr[:300]}")
            if result.stdout:
                logging.error(f"    stdout: {result.stdout[-300:]}")
            if fatal:
                logging.error(f"  FATAL — aborting pipeline")
                return False
            else:
                logging.warning(f"  Non-fatal — continuing")
                return False

    except subprocess.TimeoutExpired:
        logging.error(f"  ✗ {label} timed out after 900s")
        if fatal:
            return False
        return False

def main():
    parser = argparse.ArgumentParser(description='National landscape pipeline runner')
    parser.add_argument('--fetch-only',  action='store_true',
                        help='Run fetcher only, skip agent')
    parser.add_argument('--skip-fetch',  action='store_true',
                        help='Skip fetcher, use cached data, re-run agent')
    parser.add_argument('--days',        type=int, default=7,
                        help='Lookback window in days (default: 7)')
    parser.add_argument('--no-youtube',  action='store_true',
                        help='Skip YouTube API calls')
    parser.add_argument('--no-prefetch', action='store_true',
                        help='Skip article body prefetch')
    parser.add_argument('--dry-run',     action='store_true',
                        help='Print agent prompt without running Claude')
    args = parser.parse_args()

    log_file = setup_logging()
    logging.info(f"National landscape pipeline starting")
    logging.info(f"  Log: {log_file}")
    logging.info(f"  Days: {args.days}")
    if args.no_youtube:
        logging.info(f"  YouTube: SKIPPED")
    if args.no_prefetch:
        logging.info(f"  Prefetch: SKIPPED")

    NATIONAL_DIR.mkdir(exist_ok=True)
    python = sys.executable or "python3"

    # ---------------------------------------------------------------
    # Step 1: Fetch sources
    # ---------------------------------------------------------------
    if not args.skip_fetch:
        fetch_cmd = [python, str(SCRIPTS_DIR / "national_fetcher.py"),
                     "--days", str(args.days)]
        if args.no_youtube:
            fetch_cmd.append("--no-youtube")
        if args.no_prefetch:
            fetch_cmd.append("--no-prefetch")

        if not run_step("Fetch national sources", fetch_cmd, fatal=True):
            sys.exit(1)

        if args.fetch_only:
            logging.info("Fetch-only mode — stopping before agent")
            logging.info(f"Output: {NATIONAL_DIR / 'fetched_sources.json'}")
            return
    else:
        # Verify cached data exists
        cached = NATIONAL_DIR / "fetched_sources.json"
        if not cached.exists():
            logging.error(f"--skip-fetch but no cached data at {cached}")
            logging.error("Run without --skip-fetch first.")
            sys.exit(1)
        logging.info("Skipping fetch — using cached fetched_sources.json")

    # ---------------------------------------------------------------
    # Step 2: Run Claude synthesis agent
    # ---------------------------------------------------------------
    agent_cmd = [python, str(SCRIPTS_DIR / "national_landscape_agent.py")]
    if args.dry_run:
        agent_cmd.append("--dry-run")
    if args.no_youtube:
        agent_cmd.append("--no-youtube")

    if not run_step("National landscape agent", agent_cmd, fatal=True):
        sys.exit(1)

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------
    logging.info(f"\n{'='*50}")
    logging.info("National landscape pipeline complete ✓")
    logging.info(f"  Output: {NATIONAL_DIR / 'landscape_latest.json'}")
    logging.info(f"  Memory: {NATIONAL_DIR / 'landscape_memory.json'}")
    logging.info(f"  Log:    {log_file}")

if __name__ == '__main__':
    main()
