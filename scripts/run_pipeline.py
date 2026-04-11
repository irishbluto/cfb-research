#!/usr/bin/env python3
"""
run_pipeline.py
---------------
Master pipeline runner for CFB research. Runs all six steps in order,
waiting for each to complete before proceeding to the next.

Pipeline steps:
  1. scrape_team_context.py     — scrapes puntandrally.com team pages
                                  (currently still scrapes teamroster + schedule;
                                  the teamprofile/portals/croots pages are
                                  redundant now that step 2 is DB-first and
                                  will be removed once build_team_context.py
                                  is validated across a full conference run)
  2. build_team_context.py      — DB-first context builder (REPLACES the old
                                  enrich_from_db.py). Pulls power ratings,
                                  SP+ (SandPratings), team_preview returning
                                  production, Bill Connelly returning prod,
                                  coaching staff + ranks, schedule tiers,
                                  team notes, portal, recruiting, best players,
                                  advanced stats, composite, last-season
                                  scoring/record — all directly from the
                                  puntandrally MariaDB. Merges into the JSON
                                  produced by step 1 so full_roster,
                                  schedule_2026, and any manually-curated
                                  fields (agent_notes, beat_writers, etc.)
                                  are preserved.
  3. youtube_fetcher.py         — fetches YouTube videos (daily cache)
  4. written_sources_fetcher.py — fetches RSS/articles with body prefetch
  5. research_agent.py          — runs Claude agent per team → JSON output
  6. team_memory_writer.py      — distills research output into team_memory/{slug}.json
                                  for use as prior-run context on subsequent runs
                                  (fast, no API calls; skipped if step 5 was skipped)

Note: enrich_from_db.py is deprecated as of 2026-04-11 and no longer part of
the pipeline. It remains on disk for reference / rollback until
build_team_context.py has been validated across all 138 FBS teams.

Step control flags:
  --no-research   Run steps 1–4 only; skip research agent and memory writer
  --skip-fetch    Skip steps 3–4 (YouTube + written sources); use existing cache
  --fetch-only    Run steps 3–4 only; skip scrape, build context, research, and memory
  --skip-scrape   Skip step 1 (scrape) but still run step 2 (build context).
                  Use when roster + schedule are already current and you only
                  want to refresh the DB-derived context fields.

Pass-through flags:
  --days N        Lookback window in days for YouTube + written sources (default: 14)
  --no-ytdlp      Disable yt-dlp fallback in youtube_fetcher
  --no-prefetch   Disable article body prefetch in written_sources_fetcher

-------------------------------------------------------------------------------
USAGE EXAMPLES
-------------------------------------------------------------------------------

# Full pipeline — all 6 steps (standard conference run)
    python3 scripts/run_pipeline.py --conf sec
    python3 scripts/run_pipeline.py --conference big10

# Single team, full pipeline
    python3 scripts/run_pipeline.py --team alabama

# Steps 1–4 only — scrape/build/fetch without running the agent
# Useful to review context quality before committing to a full agent run
    python3 scripts/run_pipeline.py --conf acc --no-research

# Steps 5–6 only — re-run agent on fresh data (scrape/build/fetch already done today)
# Most common re-run pattern: use when you've already fetched today
    python3 scripts/run_pipeline.py --conf sec --skip-fetch

# Steps 3–4 only — refresh YouTube + articles without scraping or running agent
# Use when context is current but caches are stale
    python3 scripts/run_pipeline.py --conf big10 --fetch-only

# Refresh ONLY DB-derived context (step 2) — skip scrape, keep rosters/schedules as-is
# Use after a stats push (power ratings, composite, advanced stats updated)
    python3 scripts/run_pipeline.py --conf sec --skip-scrape --no-research

# Extend lookback window (e.g. first run of the season, or after a long gap)
    python3 scripts/run_pipeline.py --conf mwc --days 30

# YouTube quota exhausted — skip YouTube fetcher entirely
# Add --no-youtube directly to research_agent.py if running it standalone;
# run_pipeline.py passes through --skip-fetch to avoid re-fetching YouTube
    python3 scripts/run_pipeline.py --conf sec --skip-fetch

-------------------------------------------------------------------------------
TEAM MEMORY NOTES
-------------------------------------------------------------------------------

team_memory/{slug}.json is written after each successful research run and
injected into the next run's prompt as "PRIOR RUN NOTES." It captures:
  - Prior agent summary, sentiment, and key storylines
  - Coaching staff snapshot at time of last run
  - agent_flags: what the agent was confident about, what to recheck,
    and any unresolved storylines to watch (e.g. open QB battles)

To seed memory from existing research output without re-running the agent:
    python3 scripts/team_memory_writer.py --conf sec
    python3 scripts/team_memory_writer.py --all

Memory is only as valuable as the prior run's output quality. On a team's
first-ever run, no memory file exists and the agent starts cold — this is
expected and has zero cost/overhead.
"""

import os, sys, subprocess, argparse, json
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
LOG_DIR     = Path("/cfb-research/logs")

# Use the project venv on VPS; fall back to current interpreter elsewhere
_venv_python = Path("/cfb-research/venv/bin/python3")
PYTHON = str(_venv_python) if _venv_python.exists() else sys.executable

# ---------------------------------------------------------------------------
# Step runner — streams output to terminal in real time
# ---------------------------------------------------------------------------

def run_step(name, cmd, warn_on_fail=False):
    """
    Run a pipeline step. Streams stdout/stderr to the terminal in real time.
    Returns (success: bool, elapsed_seconds: float).
    If warn_on_fail=True, a non-zero exit logs a warning but doesn't stop the pipeline.
    """
    t0 = datetime.now()
    print(f"\n{'='*60}", flush=True)
    print(f"  STEP: {name}", flush=True)
    print(f"  CMD:  {' '.join(str(c) for c in cmd)}", flush=True)
    print(f"  TIME: {t0.strftime('%H:%M:%S')}", flush=True)
    print(f"{'='*60}\n", flush=True)

    result = subprocess.run(cmd)   # inherits stdin/stdout/stderr — real-time output
    elapsed = (datetime.now() - t0).total_seconds()

    if result.returncode != 0:
        tag = "[WARN]" if warn_on_fail else "[FAILED]"
        print(f"\n{tag} {name} — exit code {result.returncode} ({elapsed:.0f}s)", flush=True)
        return False, elapsed

    print(f"\n[OK] {name} — {elapsed:.0f}s", flush=True)
    return True, elapsed


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='CFB research pipeline runner — runs all steps for a team or conference.'
    )

    # Target: team or conference (one required)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--team',       default=None, help='Team slug e.g. "alabama"')
    target.add_argument('--conf',       default=None, dest='conf', help='Conference slug e.g. "sec"')
    target.add_argument('--conference', default=None, dest='conf', help='Alias for --conf')

    # Step control
    parser.add_argument('--no-research', action='store_true',
                        help='Run steps 1–4 only; skip research_agent.py')
    parser.add_argument('--skip-fetch',  action='store_true',
                        help='Skip steps 3–4 (use existing YouTube/article cache)')
    parser.add_argument('--fetch-only',  action='store_true',
                        help='Run steps 3–4 only; skip scrape, build context, and research')
    parser.add_argument('--skip-scrape', action='store_true',
                        help='Skip step 1 (scrape) but still run step 2 (build team context)')

    # Pass-through options for individual scripts
    parser.add_argument('--days',       type=int, default=14,
                        help='Lookback window in days for YouTube + written sources (default: 14)')
    parser.add_argument('--no-ytdlp',   action='store_true',
                        help='Disable yt-dlp fallback in youtube_fetcher')
    parser.add_argument('--no-prefetch', action='store_true',
                        help='Disable article body prefetch in written_sources_fetcher')

    args = parser.parse_args()

    # Validate flag combinations
    if args.fetch_only and args.skip_fetch:
        parser.error("--fetch-only and --skip-fetch cannot be used together")
    if args.fetch_only and args.no_research:
        parser.error("--fetch-only already skips research; --no-research is redundant")
    if args.fetch_only and args.skip_scrape:
        parser.error("--fetch-only already skips scrape; --skip-scrape is redundant")


    # ---------------------------------------------------------------------------
    # Resolve target args and which steps to run
    # ---------------------------------------------------------------------------

    if args.team:
        target_args = ['--team', args.team]
        run_label   = f"team: {args.team}"
        log_tag     = args.team.replace('-', '_')
    else:
        target_args = ['--conference', args.conf]
        run_label   = f"conference: {args.conf.upper()}"
        log_tag     = args.conf.replace('-', '_')

    run_scrape   = not args.fetch_only and not args.skip_scrape
    run_build    = not args.fetch_only
    run_youtube  = not args.skip_fetch
    run_written  = not args.skip_fetch
    run_research = not args.no_research and not args.fetch_only

    started_at = datetime.now()
    log_path   = LOG_DIR / f"pipeline_{log_tag}_{started_at.strftime('%Y%m%d_%H%M%S')}.json"

    steps_desc = []
    run_memory = run_research  # only write memory when research ran

    if run_scrape:   steps_desc.append('1.scrape')
    if run_build:    steps_desc.append('2.build')
    if run_youtube:  steps_desc.append('3.youtube')
    if run_written:  steps_desc.append('4.written')
    if run_research: steps_desc.append('5.research')
    if run_memory:   steps_desc.append('6.memory')

    print(f"\n{'='*60}", flush=True)
    print(f"  CFB Research Pipeline", flush=True)
    print(f"  Target:  {run_label}", flush=True)
    print(f"  Steps:   {' → '.join(steps_desc)}", flush=True)
    print(f"  Started: {started_at.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(f"{'='*60}", flush=True)


    # ---------------------------------------------------------------------------
    # Execute steps
    # ---------------------------------------------------------------------------

    step_results = []

    def step(name, script, extra_args=None, warn_on_fail=False):
        cmd = [PYTHON, str(SCRIPTS_DIR / script)] + target_args + (extra_args or [])
        ok, elapsed = run_step(name, cmd, warn_on_fail=warn_on_fail)
        step_results.append({'step': name, 'ok': ok, 'elapsed_secs': round(elapsed)})
        return ok

    # Step 1 — Scrape (roster + schedule; will be trimmed once build_team_context
    # is validated across all teams and the teamprofile/portals/croots scraping
    # can be retired)
    if run_scrape:
        if not step("1. Scrape team context", "scrape_team_context.py"):
            print("\n[ABORT] Scrape failed — stopping pipeline.", flush=True)
            sys.exit(1)

    # Step 2 — Build team context (DB-first; replaces enrich_from_db.py)
    if run_build:
        if not step("2. Build team context (DB-first)", "build_team_context.py"):
            print("\n[ABORT] Build team context failed — stopping pipeline.", flush=True)
            sys.exit(1)

    # Step 3 — YouTube (non-fatal: missing videos shouldn't block the agent)
    if run_youtube:
        yt_extra = ['--days', str(args.days)]
        if args.no_ytdlp:
            yt_extra.append('--no-ytdlp')
        ok = step("3. YouTube fetcher", "youtube_fetcher.py", yt_extra, warn_on_fail=True)
        if not ok:
            print("  Continuing pipeline — agent will run without YouTube data.", flush=True)

    # Step 4 — Written sources (non-fatal: missing articles shouldn't block the agent)
    if run_written:
        ws_extra = ['--days', str(args.days)]
        if args.no_prefetch:
            ws_extra.append('--no-prefetch')
        ok = step("4. Written sources", "written_sources_fetcher.py", ws_extra, warn_on_fail=True)
        if not ok:
            print("  Continuing pipeline — agent will run without pre-fetched articles.", flush=True)

    # Step 5 — Research agent
    if run_research:
        if not step("5. Research agent", "research_agent.py"):
            print("\n[ABORT] Research agent failed.", flush=True)
            sys.exit(1)

    # Step 6 — Team memory writer (non-fatal: memory is a nice-to-have, not a blocker)
    if run_memory:
        ok = step("6. Team memory writer", "team_memory_writer.py", warn_on_fail=True)
        if not ok:
            print("  Memory write failed — research output is still valid.", flush=True)


    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------

    total_secs = (datetime.now() - started_at).total_seconds()
    total_min  = total_secs / 60

    print(f"\n{'='*60}", flush=True)
    print(f"  PIPELINE COMPLETE — {run_label}", flush=True)
    print(f"  Total time: {total_min:.1f} min", flush=True)
    print(f"  Steps:", flush=True)
    for r in step_results:
        status = '✓' if r['ok'] else '✗'
        mins   = r['elapsed_secs'] // 60
        secs   = r['elapsed_secs'] % 60
        time_s = f"{mins}m {secs}s" if mins else f"{secs}s"
        print(f"    {status} {r['step']} — {time_s}", flush=True)
    print(f"{'='*60}\n", flush=True)

    # Write JSON summary log
    try:
        LOG_DIR.mkdir(exist_ok=True)
        summary = {
            'target':     run_label,
            'started_at': started_at.isoformat(),
            'total_secs': round(total_secs),
            'steps':      step_results,
        }
        log_path.write_text(json.dumps(summary, indent=2))
        print(f"Run log: {log_path}", flush=True)
    except Exception as e:
        print(f"[WARN] Could not write run log: {e}", flush=True)


if __name__ == '__main__':
    main()
