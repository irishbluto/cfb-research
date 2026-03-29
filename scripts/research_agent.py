#!/usr/bin/env python3
"""
research_agent.py
-----------------
Orchestrates CFB research by spawning one Claude Code agent session per team.
Each session reads the team context file, fetches YouTube and written source
content, and writes a structured research JSON.

Each team gets a fresh Claude session — clean context window, resumable if
one team fails, easy to debug.

Usage:
    python3 scripts/research_agent.py                        # all SEC teams (default)
    python3 scripts/research_agent.py --team alabama         # single team
    python3 scripts/research_agent.py --conference sec       # all teams in a conference
    python3 scripts/research_agent.py --conference big10     # Big Ten
    python3 scripts/research_agent.py --all                  # all active conferences
    python3 scripts/research_agent.py --resume               # skip teams with fresh output
    python3 scripts/research_agent.py --dry-run              # print prompts without running

    # Normal run (YouTube enabled)
    python3 scripts/research_agent.py --conference big10 --resume

    # Quota exhausted — skip YouTube entirely
    python3 scripts/research_agent.py --conference big10 --resume --no-youtube

    # Check quota before deciding which mode to use
    python3 scripts/youtube_fetcher.py --quota

Active conferences: sec, big10
Inactive (uncomment in CONFERENCE_TEAMS to enable):
    acc, big12, pac12, aac, sbc, mwc, mac, cusa, fbsind

Output: /cfb-research/research/{slug}_latest.json
Logs:   /cfb-research/logs/research_{date}.log
"""

import json, os, sys, time, argparse, subprocess, logging
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths - Here are the paths
# ---------------------------------------------------------------------------
BASE_DIR      = Path("/cfb-research")
CONTEXT_DIR   = BASE_DIR / "team_context"
CHANNELS_FILE = BASE_DIR / "config" / "youtube_channels.json"
OUTPUT_DIR    = BASE_DIR / "research"
LOG_DIR       = BASE_DIR / "logs"
CLAUDE_BIN    = "/home/joleary/.local/bin/claude"  # adjust if different

SEC_TEAMS = [
    "alabama", "arkansas", "auburn", "florida", "georgia", "kentucky",
    "lsu", "mississippi-state", "missouri", "oklahoma", "ole-miss",
    "south-carolina", "tennessee", "texas", "texas-am", "vanderbilt",
]

BIG10_TEAMS = [
    "illinois", "indiana", "iowa", "maryland", "michigan", "michigan-state",
    "minnesota", "nebraska", "northwestern", "ohio-state", "oregon",
    "penn-state", "purdue", "rutgers", "ucla", "usc", "washington", "wisconsin",
]

ACC_TEAMS = [
    "boston-college", "california", "clemson", "duke", "florida-state",
    "georgia-tech", "louisville", "miami", "nc-state", "north-carolina",
    "pittsburgh", "smu", "stanford", "syracuse", "virginia", "virginia-tech",
    "wake-forest",
]

BIG12_TEAMS = [
    "arizona", "arizona-state", "baylor", "byu", "cincinnati", "colorado",
    "houston", "iowa-state", "kansas", "kansas-state", "oklahoma-state",
    "tcu", "texas-tech", "ucf", "utah", "west-virginia",
]

PAC12_TEAMS = [
    "boise-state", "colorado-state", "fresno-state", "oregon-state",
    "san-diego-state", "texas-state", "utah-state", "washington-state",
]

AAC_TEAMS = [
    "army", "charlotte", "east-carolina", "florida-atlantic", "memphis",
    "navy", "north-texas", "rice", "south-florida", "temple", "tulane",
    "tulsa", "uab", "utsa",
]

SBC_TEAMS = [
    "app-state", "arkansas-state", "coastal-carolina", "georgia-southern",
    "georgia-state", "james-madison", "louisiana", "marshall", "old-dominion",
    "south-alabama", "southern-miss", "troy", "ul-monroe",
]

MWC_TEAMS = [
    "air-force", "hawaii", "nevada", "new-mexico", "north-dakota-state",
    "northern-illinois", "san-jose-state", "unlv", "utep", "wyoming",
]

MAC_TEAMS = [
    "akron", "ball-state", "bowling-green", "buffalo", "central-michigan",
    "eastern-michigan", "kent-state", "massachusetts", "miami-oh", "ohio",
    "sacramento-state", "toledo", "western-michigan",
]

CUSA_TEAMS = [
    "delaware", "fiu", "jacksonville-state", "kennesaw-state", "liberty",
    "louisiana-tech", "middle-tennessee", "missouri-state", "new-mexico-state",
    "sam-houston", "western-kentucky",
]

FBSIND_TEAMS = [
    "notre-dame", "uconn",
]

# Conference → team slug mappings
# To activate a conference: uncomment its line below AND ensure context/YouTube
# files exist for those teams before running
CONFERENCE_TEAMS = {
    "sec":    SEC_TEAMS,
    "big10":  BIG10_TEAMS,
    "fbsind": FBSIND_TEAMS,
    # "acc":    ACC_TEAMS,
    # "big12":  BIG12_TEAMS,
    # "pac12":  PAC12_TEAMS,
    # "aac":    AAC_TEAMS,
    # "sbc":    SBC_TEAMS,
    # "mwc":    MWC_TEAMS,
    # "mac":    MAC_TEAMS,
    # "cusa":   CUSA_TEAMS,
    
}

# How many days before a research file is considered stale and needs refresh
STALE_DAYS = 7

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
def setup_logging():
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"research_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s  %(levelname)s  %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ]
    )
    return log_file

# ---------------------------------------------------------------------------
# Build the research prompt for one team
# ---------------------------------------------------------------------------
def build_prompt(slug, context, channels, no_youtube=False):
    team_name   = context.get('team', slug)
    coach       = context.get('head_coach', 'Unknown Coach')
    power_rank  = context.get('power_rank')
    profile     = context.get('profile_2026', '')
    qb_note     = context.get('starting_qb_note', '')
    team_notes  = context.get('team_notes', [])
    inj_notes   = context.get('injury_notes', [])
    staff_notes = context.get('staff_schedule_notes', [])
    portal_in   = context.get('portal_in', [])
    portal_out  = context.get('portal_out', [])
    ret_starters = context.get('returning_starters', 'unknown')
    oc          = context.get('offensive_coordinator', '')
    dc          = context.get('defensive_coordinator', '')
    oc_rank     = context.get('offensive_coordinator_rank')
    dc_rank     = context.get('defensive_coordinator_rank')
    schedule    = context.get('schedule_2026', [])
    talent_rank = context.get('talent_rank')
    portal_net  = context.get('portal_net', 0)
    top_portals = context.get('top_portal_additions', [])
    top_recruits = context.get('top_recruits', [])
    four_yr     = context.get('four_yr_record', '')
    off_rank    = context.get('offense_power_rank')
    def_rank    = context.get('defense_power_rank')
    adv_season  = context.get('db_enriched_at', '')
    ppa_off     = context.get('offense_ppa_rank')
    ppa_def     = context.get('defense_ppa_rank')
    off_profile = context.get('offense_profile_db', context.get('offense_profile', ''))

    # Format team notes for prompt
    notes_block = ""
    if team_notes:
        notes_block += "Team notes (your own curated observations):\n"
        notes_block += "\n".join(f"  - {n}" for n in team_notes) + "\n"
    if inj_notes:
        notes_block += "Injury notes:\n"
        notes_block += "\n".join(f"  - {n}" for n in inj_notes) + "\n"
    if staff_notes:
        notes_block += "Staff/schedule notes:\n"
        notes_block += "\n".join(f"  - {n}" for n in staff_notes) + "\n"

    # Format top portal additions
    portal_block = ""
    if top_portals:
        portal_block = "Top portal additions: " + ", ".join(
            f"{p['name']} ({p['position']}, {p['stars']}★)"
            for p in top_portals[:5]
        )

    # Format top recruits
    recruit_block = ""
    if top_recruits:
        recruit_block = "Top 2026 recruits: " + ", ".join(
            f"{r['name']} ({r['position']}, {r['stars']}★)"
            for r in top_recruits[:5]
        )

    # Format opening schedule
    schedule_block = ""
    if schedule:
        schedule_block = "Opening 2026 schedule:\n"
        for g in schedule[:5]:
            fav = "favored" if g.get('favorite') == team_name else "underdog"
            schedule_block += (f"  Wk{g['week']} {g['date']} {g['location']} "
                             f"{g['opponent']} (line: {g['line']}, "
                             f"win%: {g['win_pct']}%)\n")

    # Format best players — used to constrain player identification in the summary
    best_players_block = ""
    best_players = context.get('best_players', [])
    if best_players:
        best_players_block = "Key players by impact rating (only use these names when identifying team leaders or standouts):\n"
        for p in best_players:
            line = f"  {p['player_name']} ({p['position']})"
            if p.get('statsline'):
                line += f" — {p['statsline']}"
            best_players_block += line + "\n"

    # Pre-fetch YouTube videos via API (much more reliable than asking Claude to scrape)
    youtube_block = ""
    prefetched_videos = []
    if no_youtube:
        youtube_block = "YouTube: Skipped (--no-youtube flag set or daily quota reached). Skip YouTube section."
    else:
        try:
            sys.path.insert(0, str(BASE_DIR / "scripts"))
            from youtube_fetcher import fetch_team_videos
            yt_result = fetch_team_videos(slug, days=14, max_results=5)
            if 'error' not in yt_result:
                prefetched_videos = yt_result['videos']
                if yt_result['count'] > 0:
                    youtube_block = f"YouTube videos found ({yt_result['count']} football-relevant in last 14 days):\n"
                    youtube_block += yt_result['summary_text']
                    youtube_block += "\n\nFor each video above: fetch the URL, watch/read enough to extract 2-4 key points, assess sentiment."
                else:
                    youtube_block = "YouTube: No football-relevant videos found in the last 14 days across configured channels."
            else:
                youtube_block = f"YouTube: Could not fetch videos ({yt_result['error']}). Skip YouTube section."
        except Exception as e:
            youtube_block = f"YouTube: Fetcher unavailable ({e}). Skip YouTube section."

    # Pre-fetch written sources via RSS (fast, structured — reduces Claude fetching)
    written_block = ""
    try:
        sys.path.insert(0, str(BASE_DIR / "scripts"))
        from written_sources_fetcher import fetch_team_articles
        ws_result = fetch_team_articles(slug, days=14, max_per_source=5)
        if ws_result['count'] > 0 or ws_result['direct_count'] > 0:
            written_block = (
                f"Written sources pre-fetched ({ws_result['count']} RSS articles"
                f", {ws_result['direct_count']} direct URLs to fetch):\n"
            )
            written_block += ws_result['summary_text']
            written_block += (
                "\n\nFor each RSS article above: fetch the URL and read enough to extract "
                "2-4 specific key points, assess sentiment. Skip any article that is clearly "
                "not about the 2026 football season (e.g. recruiting classes beyond 2026, "
                "other sports). For direct URLs: fetch the page and skim for recent football news."
            )
        else:
            written_block = "Written sources: No pre-configured sources available — rely on web search in Task 3."
    except Exception as e:
        written_block = f"Written sources: Fetcher unavailable ({e}). Rely on web search in Task 3."

    # Determine research mode based on time of year
    month = datetime.now().month
    if month in (1, 2, 3):
        mode = "early_offseason"
        mode_focus = "portal activity, recruiting, coaching changes, spring practice previews"
    elif month in (4, 5, 6):
        mode = "spring_offseason"
        mode_focus = "spring practice results, depth chart battles, transfer portal updates"
    elif month in (7, 8):
        mode = "preseason"
        mode_focus = "fall camp, depth chart, injury news, expectations and predictions"
    else:
        mode = "in_season"
        mode_focus = "injury updates, weekly game prep, performance analysis, fanbase pulse"

    output_path = str(OUTPUT_DIR / f"{slug}_latest.json")

    prompt = f"""You are a college football research agent for Punt & Rally (puntandrally.com), a CFB analytics site.

Your task: Research {team_name} football and write a structured JSON research report.

## Team Context (use this to guide what to look for)

Team: {team_name}
Head Coach: {coach} | Record: {context.get('coach_record', '')} | {context.get('coach_years', '')}
2025 Record: {context.get('last_season_record', '')} | ATS: {context.get('last_season_ats', '')}
Power Rating: #{power_rank} overall | Offense: #{off_rank} | Defense: #{def_rank}
PPA: Offense #{ppa_off} | Defense #{ppa_def}
Offense Profile: {off_profile}
Talent Rank: #{talent_rank} | 4-Year Record: {four_yr}
2026 Profile: {profile}
OC: {oc} (#{oc_rank}) | DC: {dc} (#{dc_rank})
Returning Starters: {ret_starters}
QB Situation: {qb_note}
Portal Net: {portal_net:+d} ({len(portal_in)} in, {len(portal_out)} out)
{portal_block}
{recruit_block}

{notes_block}
{schedule_block}
{best_players_block}
## Research Mode: {mode.upper()}
Current focus: {mode_focus}

## Your Research Tasks

1. **YouTube Research** — Videos have been pre-fetched for you below. For each football-relevant video:
   - Fetch the URL and read/watch enough to extract 2-4 specific key points
   - Assess sentiment (optimistic / cautious / concerned / mixed / neutral)
   - If a video is not football-relevant (basketball, baseball, etc.) skip it entirely

{youtube_block}

2. **Written Sources** — Articles have been pre-fetched via RSS below. For each:
   - Fetch the URL and read enough to extract 2-4 specific key points
   - Assess sentiment (optimistic / cautious / concerned / mixed / neutral)
   - Skip articles clearly not about {team_name} 2026 football (other sports, recruiting classes beyond 2026)
   - For direct URLs (no RSS): fetch the page and skim for the most recent football news items

{written_block}

3. **Web Search Fallback** — Only if Tasks 1 and 2 leave obvious gaps, do a targeted search:
   - Maximum 2 searches total — be specific, not broad
   - Good examples: "{team_name} injury update March 2026", "{team_name} depth chart spring 2026"
   - Do NOT search for things already covered by YouTube or written sources above

4. **Synthesis** — Based on everything you found, identify:
   - The 3-5 most important current storylines
   - Any injury flags not already in the context
   - Overall fanbase/media sentiment
   - A 2-3 sentence summary a reader could scan in 10 seconds

## Output Format

Write your findings to this exact file path: {output_path}

The file must be valid JSON matching this exact structure:
{{
  "team": "{team_name}",
  "slug": "{slug}",
  "research_date": "{datetime.now().strftime('%Y-%m-%d')}",
  "mode": "{mode}",
  "youtube_findings": [
    {{
      "channel": "channel name",
      "video_title": "title of video",
      "url": "https://youtube.com/watch?v=xxx",
      "published": "YYYY-MM-DD",
      "key_points": ["point 1", "point 2"],
      "sentiment": "optimistic|cautious|concerned|mixed|neutral"
    }}
  ],
  "beat_coverage": [
    {{
      "source": "outlet name",
      "headline": "article headline",
      "url": "https://...",
      "published": "YYYY-MM-DD",
      "key_points": ["point 1", "point 2"],
      "sentiment": "optimistic|cautious|concerned|mixed|neutral"
    }}
  ],
  "key_storylines": [
    "storyline 1",
    "storyline 2",
    "storyline 3"
  ],
  "injury_flags": [
    "any injuries not already in context file"
  ],
  "overall_sentiment": "one of: optimistic|cautiously_optimistic|mixed|cautious|concerned",
  "sentiment_score": 0.0,
  "agent_summary": "3-4 dense, specific sentences a CFB analyst would write. Must include: (1) the team's single most important current narrative with specific player names and/or numbers, (2) the biggest concern or question mark with concrete evidence, (3) one context-setter such as a key schedule game, ranking, or historical note. Avoid generic phrases like 'enters 2026 with questions' or 'looks to build on last year.'"
}}

## Important Instructions
- sentiment_score: 0.0 = extremely negative, 0.5 = neutral, 1.0 = extremely positive
- If a YouTube channel returns no recent videos, include it in youtube_findings with an empty key_points array and a note in the video_title field
- If you cannot access a URL, note it but don't leave the field empty — use "unavailable" as the URL
- key_storylines should be concrete and specific, not generic (not "team has questions at QB" but "Austin Mack vs Keelon Russell QB battle unresolved after spring")
- You have a strict time budget. Do not retry failed URL fetches — if a URL does not load in one attempt, mark it "unavailable" and move on immediately
- Pre-fetched RSS articles in Task 2 are your primary written source — do not do broad web searches if those articles cover the topic
- Maximum 2 web searches in Task 3 — only if there are clear gaps after Tasks 1 and 2
- Write the output file as soon as you have enough data — do not wait until everything is perfect
- Prefer beat writers and team-specific outlets over general aggregators like Heavy.com, Yardbarker, or Bleacher Report
- When identifying standout players, unit leaders, or key contributors in key_storylines or agent_summary, ONLY use players from the Key Players list above — do not name players not on that list as leaders or standouts, as rankings are based on actual performance data
- Write the JSON file before finishing — do not just print it
- The JSON must be valid — no trailing commas, no comments inside the JSON
"""
    return prompt, mode

# ---------------------------------------------------------------------------
# Check if output is fresh enough to skip
# ---------------------------------------------------------------------------
def is_fresh(slug):
    output_file = OUTPUT_DIR / f"{slug}_latest.json"
    if not output_file.exists():
        return False
    try:
        with open(output_file) as f:
            data = json.load(f)
        rd = data.get('research_date', '')
        if rd:
            age = (datetime.now() - datetime.strptime(rd, '%Y-%m-%d')).days
            return age < STALE_DAYS
    except Exception:
        pass
    return False

# ---------------------------------------------------------------------------
# Run Claude agent for one team
# ---------------------------------------------------------------------------
def run_agent(slug, prompt, dry_run=False, debug=False):
    """Spawn a Claude Code session with the research prompt."""
    if dry_run:
        print(f"\n{'='*60}")
        print(f"DRY RUN — prompt for {slug}:")
        print(prompt[:500] + "..." if len(prompt) > 500 else prompt)
        return True

    # Write prompt to a temp file so we don't hit shell escaping issues
    prompt_file = BASE_DIR / "logs" / f"prompt_{slug}.txt"
    prompt_file.write_text(prompt)

    cmd = [
        CLAUDE_BIN, "--dangerously-skip-permissions",
        "-p", prompt,
    ]

    if debug:
        logging.info(f"Running: {' '.join(cmd[:3])} [prompt length: {len(prompt)}]")

    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=900,   # 15 minute timeout per team
            cwd=str(BASE_DIR),
        )
        elapsed = round(time.time() - start, 1)

        if result.returncode == 0:
            # Check if output file was actually written
            output_file = OUTPUT_DIR / f"{slug}_latest.json"
            if output_file.exists():
                try:
                    with open(output_file) as f:
                        json.load(f)  # validate JSON
                    logging.info(f"  ✓ {slug} — valid JSON written ({elapsed}s)")
                    return True
                except json.JSONDecodeError as e:
                    logging.error(f"  ✗ {slug} — invalid JSON in output: {e}")
                    return False
            else:
                logging.warning(f"  ✗ {slug} — agent ran but no output file written ({elapsed}s)")
                if debug:
                    logging.debug(f"  Agent stdout: {result.stdout[:500]}")
                return False
        else:
            logging.error(f"  ✗ {slug} — agent exited with code {result.returncode} ({elapsed}s)")
            if result.stderr:
                logging.error(f"  stderr: {result.stderr[:300]}")
            return False

    except subprocess.TimeoutExpired:
        logging.error(f"  ✗ {slug} — timed out after 300s")
        return False
    except Exception as e:
        logging.error(f"  ✗ {slug} — unexpected error: {e}")
        return False

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--team',       default=None,
                        help='Single team slug e.g. "alabama"')
    parser.add_argument('--conference', default=None,
                        help='Conference slug e.g. "sec"')
    parser.add_argument('--all',        action='store_true',
                        help='Run all configured teams across all conferences')
    parser.add_argument('--resume',     action='store_true',
                        help='Skip teams with fresh output (< STALE_DAYS old)')
    parser.add_argument('--dry-run',    action='store_true',
                        help='Print prompts without running agents')
    parser.add_argument('--no-youtube',  action='store_true',
                        help='Skip YouTube API fetches (use when quota is exhausted)')
    parser.add_argument('--debug',      action='store_true')
    parser.add_argument('--delay',      type=int, default=10,
                        help='Seconds to wait between teams (default: 10)')
    args = parser.parse_args()

    log_file = setup_logging()
    logging.info(f"Research agent starting — log: {log_file}")
    if args.no_youtube:
        logging.info("YouTube fetching disabled (--no-youtube)")

    OUTPUT_DIR.mkdir(exist_ok=True)

    # Build the flat list of all known slugs for validation
    all_known_slugs = set(s for team_list in CONFERENCE_TEAMS.values() for s in team_list)

    # Determine which teams to run
    if args.team:
        if args.team not in all_known_slugs:
            logging.error(f"Unknown team slug: '{args.team}'")
            logging.error(f"Known slugs: {sorted(all_known_slugs)}")
            sys.exit(1)
        teams = [args.team]
        logging.info(f"Running single team: {args.team}")

    elif args.conference:
        conf = args.conference.lower()
        if conf not in CONFERENCE_TEAMS:
            logging.error(f"Unknown conference: '{conf}'")
            logging.error(f"Known conferences: {sorted(CONFERENCE_TEAMS.keys())}")
            sys.exit(1)
        teams = CONFERENCE_TEAMS[conf]
        logging.info(f"Running {len(teams)} teams in {conf.upper()}")

    elif args.all:
        # Flatten all conferences, preserve order, deduplicate
        seen = set()
        teams = []
        for team_list in CONFERENCE_TEAMS.values():
            for t in team_list:
                if t not in seen:
                    seen.add(t)
                    teams.append(t)
        logging.info(f"Running all {len(teams)} configured teams")

    else:
        # Default: SEC (original behaviour — nothing breaks)
        teams = SEC_TEAMS
        logging.info(f"No filter specified — running all {len(teams)} SEC teams")

    results = {'success': [], 'skipped': [], 'failed': []}

    for i, slug in enumerate(teams):
        # Load context file
        context_file = CONTEXT_DIR / f"{slug}.json"
        if not context_file.exists():
            logging.warning(f"[{slug}] No context file found — skipping")
            results['failed'].append(slug)
            continue

        with open(context_file) as f:
            context = json.load(f)

        # Skip if fresh and --resume flag set
        if args.resume and is_fresh(slug):
            logging.info(f"[{slug}] Output is fresh — skipping")
            results['skipped'].append(slug)
            continue

        logging.info(f"[{slug}] Starting research ({i+1}/{len(teams)})")

        prompt, mode = build_prompt(slug, context, {}, no_youtube=args.no_youtube)

        if args.debug:
            logging.info(f"[{slug}] Mode: {mode} | Prompt length: {len(prompt)} chars")

        success = run_agent(slug, prompt, dry_run=args.dry_run, debug=args.debug)

        if success:
            results['success'].append(slug)
        else:
            results['failed'].append(slug)

        # Delay between teams to avoid rate limiting
        if i < len(teams) - 1 and not args.dry_run:
            logging.info(f"  Waiting {args.delay}s before next team...")
            time.sleep(args.delay)

    # Summary
    logging.info(f"\n{'='*50}")
    logging.info(f"Research run complete")
    logging.info(f"  Success: {len(results['success'])} — {results['success']}")
    logging.info(f"  Skipped: {len(results['skipped'])} — {results['skipped']}")
    logging.info(f"  Failed:  {len(results['failed'])} — {results['failed']}")

    if results['failed']:
        logging.info(f"\nTo retry failed teams:")
        for slug in results['failed']:
            logging.info(f"  python3 scripts/research_agent.py --team {slug}")

if __name__ == '__main__':
    main()