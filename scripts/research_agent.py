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

Output: /cfb-research/research/{slug}_latest.json
Logs:   /cfb-research/logs/research_{date}.log
"""

import json, os, sys, time, argparse, subprocess, logging
from datetime import datetime, timedelta
from pathlib import Path

# Local import — sibling module in scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cost_logger import log_run

# ---------------------------------------------------------------------------
# Paths - Here are the paths
# ---------------------------------------------------------------------------
BASE_DIR      = Path("/cfb-research")
CONTEXT_DIR   = BASE_DIR / "team_context"
CHANNELS_FILE = BASE_DIR / "config" / "youtube_channels.json"
OUTPUT_DIR    = BASE_DIR / "research"
MEMORY_DIR    = BASE_DIR / "team_memory"
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
    "georgia-state", "james-madison", "louisiana", "louisiana-tech", "marshall",
    "old-dominion", "south-alabama", "southern-miss", "troy", "ul-monroe",
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
    "middle-tennessee", "missouri-state", "new-mexico-state",
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
    "acc":    ACC_TEAMS,
    "big12":  BIG12_TEAMS,
    "sbc":    SBC_TEAMS,
    "aac":    AAC_TEAMS,
    "pac12":  PAC12_TEAMS,
    "mwc":    MWC_TEAMS,
    "mac":    MAC_TEAMS,
    "cusa":   CUSA_TEAMS,
    
}

# How many days before a research file is considered stale and needs refresh
STALE_DAYS = 6

# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------
# Research agent failures are often transient (Anthropic API hiccup, tool
# timeout, JSON parse flake). One automatic retry catches most of these
# without requiring manual intervention. Timeouts and unexpected exceptions
# skip the retry — those are rarely transient.
MAX_ATTEMPTS = 2    # initial attempt + (MAX_ATTEMPTS - 1) retries
RETRY_DELAY  = 30   # seconds between attempts

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
    prev_coach  = context.get('previous_head_coach', '')
    prev_oc     = context.get('previous_oc', '')
    prev_dc     = context.get('previous_dc', '')
    conference  = context.get('conference')
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
    bc_ratio_raw = context.get('blue_chip_pct')
    bc_ratio = f"{bc_ratio_raw}%" if isinstance(bc_ratio_raw, (int, float)) and 0 <= bc_ratio_raw <= 100 else "N/A"
    is_g6 = str(conference).lower() in {'aac', 'sbc', 'mwc', 'mac', 'cusa', 'pac12'}
    portal_net  = context.get('portal_net', 0)
    top_portals = context.get('top_portal_additions', [])
    top_recruits = context.get('top_recruits', [])
    portal_class_rank = context.get('portal_class_rank')
    recruit_class_rank = context.get('recruiting_class_rank')
    four_yr     = context.get('four_yr_record', '')
    close_game_record = context.get('one_score_games', '')
    close_game_record_overall = context.get('one_score_games_under_coach', '')
    # Note explains the None case (first-year coach, or FCS→FBS transition where
    # no under-coach games exist in the table). Surfaced in the prompt so the
    # agent doesn't silently gloss over the missing record.
    close_game_note = context.get('one_score_games_under_coach_note', '')
    if close_game_record_overall:
        close_game_overall_display = close_game_record_overall
    elif close_game_note:
        close_game_overall_display = f"n/a ({close_game_note})"
    else:
        close_game_overall_display = "n/a"

    # Turnover margin + regression flags
    to_margin = context.get('turnover_margin')
    to_forced = context.get('turnovers_forced')
    to_committed = context.get('turnovers_committed')
    to_rank   = context.get('turnover_margin_rank')
    if to_margin is not None:
        sign = '+' if to_margin > 0 else ''
        turnover_display = (f"{sign}{to_margin} (#{to_rank}) — "
                           f"forced {to_forced}, committed {to_committed}")
    else:
        turnover_display = "n/a"
    turnover_luck_flag = context.get('turnover_luck_flag', '')
    one_score_regression_flag = context.get('one_score_regression_flag', '')

    # Build combined regression note for the prompt
    regression_notes = []
    if one_score_regression_flag:
        regression_notes.append(one_score_regression_flag)
    if turnover_luck_flag:
        regression_notes.append(turnover_luck_flag)
    regression_display = ' | '.join(regression_notes) if regression_notes else 'None identified'

    off_rank    = context.get('offense_power_rank')
    def_rank    = context.get('defense_power_rank')
    adv_season  = context.get('db_enriched_at', '')
    ppa_off     = context.get('offense_ppa_rank')
    ppa_def     = context.get('defense_ppa_rank')
    off_profile = context.get('offense_profile_db', context.get('offense_profile', ''))

    # ---------------------------------------------------------------------------
    # Load prior team memory if available (written by team_memory_writer.py)
    # ---------------------------------------------------------------------------
    memory_file = MEMORY_DIR / f"{slug}.json"
    prior_memory = {}
    if memory_file.exists():
        try:
            with open(memory_file) as f:
                prior_memory = json.load(f)
        except Exception:
            pass  # Corrupt memory file — proceed without it

    # ---------------------------------------------------------------------------
    # Research mode — determined early so roster caps can reference it.
    # Calendar-day boundaries (matches scripts/cron_team_research.sh dispatcher):
    #   early_offseason:  Jan 26 – Mar 31
    #   spring_offseason: Apr 1  – Jun 30
    #   preseason:        Jul 1  – Aug 28
    #   in_season:        Aug 29 – Dec 5
    #   postseason:       Dec 6  – Jan 25  (includes CFP, bowl season, portal window)
    # 2027 future build: replace these constants with first/last game lookups
    # against the games table so season windows track real schedule, not calendar.
    # ---------------------------------------------------------------------------
    _now   = datetime.now()
    _month = _now.month
    _day   = _now.day
    if (_month == 12 and _day >= 6) or (_month == 1 and _day <= 25):
        mode = "postseason"
        mode_focus = "college football playoffs, bowl games, injury updates, weekly game prep, portal window activity, recruiting, coaching changes"
    elif (_month == 1 and _day >= 26) or _month in (2, 3):
        mode = "early_offseason"
        mode_focus = "portal activity, recruiting, coaching changes, spring practice previews"
    elif _month in (4, 5, 6):
        mode = "spring_offseason"
        mode_focus = "spring practice results, depth chart battles, injury news, expectations and predictions"
    elif _month == 7 or (_month == 8 and _day <= 28):
        mode = "preseason"
        mode_focus = "fall camp, depth chart, injury news, expectations and predictions"
    else:
        mode = "in_season"
        mode_focus = "injury updates, weekly game prep, performance analysis, fanbase pulse"

    # ---------------------------------------------------------------------------
    # Source recency floor — keeps the agent from treating prior-cycle articles
    # as current reporting. The floor is wider in deep offseason (less news
    # volume, longer-tail coverage is OK) and tighter in-season.
    # Anything older than min_source_date is either dropped (if we can detect
    # the date in the data layer) or flagged in the prompt for the agent to
    # exclude. April 2025 Yahoo "spring takeaways" article that polluted
    # Buffalo's 2026-04-19 run is the canonical regression case this guards.
    # ---------------------------------------------------------------------------
    MODE_RECENCY_DAYS = {
        'early_offseason':  90,
        'spring_offseason': 75,
        'preseason':        60,
        'in_season':        21,
        'postseason':       21,
    }
    _recency_days   = MODE_RECENCY_DAYS.get(mode, 60)
    min_source_date = (_now - timedelta(days=_recency_days)).strftime('%Y-%m-%d')
    cycle_year      = _now.year if _month >= 2 else _now.year - 1  # Jan rolls into prior cycle

    # ---------------------------------------------------------------------------
    # Roster caps by mode — limits roster_block size without losing key players
    # Preseason/offseason: wider caps to cover position battles
    # In-season/playoffs: tighter caps, starters matter most
    # Keys match position_group values in team_context full_roster.
    # Fallback cap of 5 applies to any group not listed here.
    # ---------------------------------------------------------------------------
    _IN_SEASON_MODES = {'in_season', 'postseason'}
    ROSTER_CAPS = {
        'Quarterbacks':    3 if mode in _IN_SEASON_MODES else 5,
        'Running Backs':   5,
        'Wide Receivers':  6 if mode in _IN_SEASON_MODES else 8,
        'Tight Ends':      2 if mode in _IN_SEASON_MODES else 3,
        'Offensive Line':  8 if mode in _IN_SEASON_MODES else 10,
        'Defensive Line':  8 if mode in _IN_SEASON_MODES else 10,
        'Linebackers':     6,
        'Defensive Backs': 8,
        'Kickers':         1,
        'Punters':         1,
    }

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

    # Build prior memory block for prompt injection (v2 — storyline threads)
    memory_block = ""
    if prior_memory:
        run_label   = f"run #{prior_memory.get('run_count', '?')}"
        last_run    = prior_memory.get('last_run', 'unknown date')
        prior_mode  = prior_memory.get('mode', '')
        storylines  = prior_memory.get('prior_storylines', [])
        inj_flags   = prior_memory.get('prior_injury_flags', [])
        flags       = prior_memory.get('agent_flags', {})
        recheck     = flags.get('low_confidence', []) + flags.get('watch_for_next_run', [])
        threads     = prior_memory.get('storyline_threads', [])

        # Build storyline thread summaries (most recent update per thread).
        # Threads are pre-sorted by lifecycle_stage in the cache (developing →
        # continuing → settled), so the agent reads them top-down with the
        # threads that deserve the most prose first. The composition rule in
        # the synthesis section tells the agent how much real estate each
        # stage gets in agent_summary.
        thread_lines_by_stage = {"developing": [], "continuing": [], "settled": []}
        for t in threads:
            updates = t.get("updates", [])
            latest = updates[-1]["note"] if updates else t.get("theme", "")
            age_note = ""
            if t.get("first_seen") and t.get("last_updated") and t["first_seen"] != t["last_updated"]:
                age_note = f" (tracking since {t['first_seen']})"
            status_tag = ""
            if t.get("status") == "stale":
                status_tag = " [STALE — verify if still relevant]"
            source_tag = ""
            if t.get("source_type") == "coaching_diff":
                source_tag = " [COACHING CHANGE]"
            stage = (t.get("lifecycle_stage") or "continuing").lower()
            if stage not in thread_lines_by_stage:
                stage = "continuing"
            thread_lines_by_stage[stage].append(f"  - {latest}{age_note}{status_tag}{source_tag}")

        stage_headers = {
            "developing": ("DEVELOPING — lead the writeup with these; full paragraph treatment with specifics. "
                           "New this cycle or materially advanced."),
            "continuing": ("CONTINUING — paragraph-length context; do not restate every angle. "
                           "Active and load-bearing, no new dimensions this cycle."),
            "settled":    ("SETTLED — compress to ONE clause or short sentence. True and still important, "
                           "but converged across recent runs. A first-time reader should still encounter the "
                           "fact; do not expand it into a paragraph."),
        }
        stage_blocks = []
        for stage in ("developing", "continuing", "settled"):
            lines = thread_lines_by_stage[stage]
            if lines:
                stage_blocks.append(f"[{stage.upper()}] {stage_headers[stage]}\n" + "\n".join(lines))

        # Use thread summaries if available, fall back to flat prior_storylines for backward compat
        if stage_blocks:
            storylines_section = ("Tracked storyline threads (grouped by lifecycle stage — see composition rule in synthesis section):\n\n"
                                  + "\n\n".join(stage_blocks))
        elif storylines:
            storylines_section = f"""Prior key storylines:
{chr(10).join(f"  - {s}" for s in storylines)}"""
        else:
            storylines_section = "Tracked storyline threads:\n  (none yet — this is the first run)"

        memory_block = f"""=== PRIOR RUN NOTES ({last_run} — {run_label}, mode: {prior_mode}) ===
Use these as your starting point. Confirm, update, or contradict them based on new sources.
Storylines marked [STALE] may have resolved — check and either confirm or drop them.

Prior overall sentiment: {prior_memory.get('prior_sentiment', 'unknown')}

Prior agent summary:
  {prior_memory.get('prior_summary', '(none)')}

{storylines_section}
{f"{chr(10)}Prior injury flags:{chr(10)}{chr(10).join(f'  - {i}' for i in inj_flags)}" if inj_flags else ""}
High-confidence from prior run (likely still valid — verify if new sources contradict):
  {', '.join(flags.get('high_confidence', [])) or '(none recorded)'}

Watch / recheck this run:
{chr(10).join(f"  - {w}" for w in recheck) if recheck else "  (none flagged)"}
=== END PRIOR RUN NOTES ===

"""

    # Build position-grouped roster lookup from full_roster
    roster_block = ""
    full_roster = context.get('full_roster', [])
    if full_roster:
        # Group by position_group — roster is pre-sorted by impact rating desc,
        # so slicing to the cap keeps the highest-rated players at each position.
        from collections import defaultdict
        groups = defaultdict(list)
        for p in full_roster:
            pg = p.get('position_group', 'Unknown')
            name = p.get('name', '')
            if name:
                groups[pg].append(name)

        roster_block = "Roster by position group (capped by role importance — verify any player's position here before placing them in a positional context):\n"
        for group, names in sorted(groups.items()):
            cap = ROSTER_CAPS.get(group, 5)
            roster_block += f"  {group}: {', '.join(names[:cap])}\n"
            
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
        schedule_block = "Opening 2026 schedule (CONF = conference game, NON-CONF = non-conference):\n"
        for g in schedule[:5]:
            conf_tag = "[CONF]" if g.get('conference_game') else "[NON-CONF]"
            # Format spread from THIS team's perspective — agent must never reinterpret the sign
            raw_line = g.get('line')
            raw_win_pct = g.get('win_pct')
            if raw_line is not None and raw_win_pct is not None:
                try:
                    abs_line = abs(float(raw_line))
                    line_str = f"{abs_line:.1f}".rstrip('0').rstrip('.')
                    line_display = f"+{line_str}" if float(raw_win_pct) < 50 else f"-{line_str}"
                except (ValueError, TypeError):
                    line_display = str(raw_line)
            else:
                line_display = str(raw_line) if raw_line is not None else "N/A"
            schedule_block += (f"  Wk{g['week']} {g['date']} {g['location']} "
                             f"{g['opponent']} {conf_tag} (line: {line_display}, "
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
            import concurrent.futures
            sys.path.insert(0, str(BASE_DIR / "scripts"))
            from youtube_fetcher import fetch_team_videos
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(fetch_team_videos, slug, days=14, max_results=5)
                yt_result = future.result(timeout=60)   # 60s max — never blocks the pipeline
            if 'error' not in yt_result:
                prefetched_videos = yt_result['videos']
                if yt_result['count'] > 0:
                    youtube_block = f"YouTube videos found ({yt_result['count']} football-relevant in last 14 days):\n"
                    youtube_block += yt_result['summary_text']
                    youtube_block += "\n\nUse the video titles and descriptions above to identify 2-4 key points per video and assess sentiment. Do NOT fetch these YouTube URLs — use only the pre-fetched metadata provided above."
                else:
                    youtube_block = "YouTube: No football-relevant videos found in last 14 days. Skip YouTube section."
            else:
                youtube_block = f"YouTube: {yt_result['error']}. Skip YouTube section."
        except concurrent.futures.TimeoutError:
            youtube_block = "YouTube: Fetch timed out after 60s. Skip YouTube section."
            logging.warning(f"  [{slug}] YouTube fetch timed out — skipping")
        except Exception as e:
            youtube_block = f"YouTube: Fetcher unavailable ({e}). Skip YouTube section."

    # Pre-fetch written sources via RSS (fast, structured — reduces Claude fetching)
    written_block = ""
    try:
        sys.path.insert(0, str(BASE_DIR / "scripts"))
        from written_sources_fetcher import fetch_team_articles
        # Pass the mode-aware recency floor so the fetcher can drop pre-cycle
        # articles whose publish date is recoverable from the page HTML, and
        # bump the RSS `days` window to match (was hard-coded at 14, which is
        # too tight in deep offseason for low-volume teams).
        ws_result = fetch_team_articles(
            slug,
            days=max(14, _recency_days),
            max_per_source=3,
            prefetch=True,
            min_date=min_source_date,
        )
        if ws_result['count'] > 0 or ws_result['direct_count'] > 0:
            prefetched = ws_result.get('prefetched_count', 0)
            unfetched  = ws_result.get('unfetched_direct', [])
            written_block = (
                f"Written sources ({ws_result['count']} RSS articles, "
                f"{ws_result['direct_count']} direct URLs — "
                f"{prefetched} with pre-fetched body text):\n"
            )
            written_block += ws_result['summary_text']
            written_block += (
                "\n\nFor each article that has 'Content (pre-fetched)' above: "
                "read the provided text and extract 2-4 specific key points, assess sentiment. "
                "Do NOT fetch those URLs — the content is already provided inline. "
                "Skip any article not about the 2026 football season. "
            )
            if unfetched:
                written_block += (
                    f"For these {len(unfetched)} paywalled/direct URL(s) without pre-fetched content, "
                    "fetch the page and skim for recent football news: "
                    + ", ".join(unfetched)
                )
        else:
            written_block = "Written sources: No pre-configured sources available — rely on web search in Task 4."
    except Exception as e:
        written_block = f"Written sources: Fetcher unavailable ({e}). Rely on web search in Task 4."

    # Pre-fetch Reddit posts - fan community sentiment and program outlook
    reddit_block = ""
    try:
        sys.path.insert(0, str(BASE_DIR / "scripts"))
        from reddit_fetcher import fetch_team_reddit
        reddit_result = fetch_team_reddit(slug, days=30, verbose=False)
        if reddit_result['count'] > 0:
            reddit_block = (
                f"Reddit sources ({reddit_result['sub_count']} fan subreddit posts, "
                f"{reddit_result['cfb_count']} r/CFB posts - unfiltered community perspective):\n"
            )
            reddit_block += reddit_result['summary_text']
            reddit_block += (
                "\n\nFor each Reddit post above: use the title and post text to assess "
                "fan/community sentiment and surface recurring concerns, optimism, or storylines. "
                "Reddit reflects unfiltered fan perspective - weight it for sentiment and program "
                "outlook signals, not as authoritative factual reporting. Do NOT fetch Reddit URLs."
            )
        else:
            reddit_block = reddit_result['summary_text']  # graceful 'skip' message
    except Exception as e:
        reddit_block = f"Reddit: Fetcher unavailable ({e}). Skip Reddit section."

    output_path = str(OUTPUT_DIR / f"{slug}_latest.json")

    prompt = f"""You are a college football research agent for Punt & Rally (puntandrally.com), a CFB analytics site.

Your task: Research {team_name} football and write a structured JSON research report.

## 2026 FBS Conference Structure (use to validate schedule and conference game flags)
SEC (16): Alabama, Arkansas, Auburn, Florida, Georgia, Kentucky, LSU, Mississippi State, Missouri, Oklahoma, Ole Miss, South Carolina, Tennessee, Texas, Texas A&M, Vanderbilt
Big Ten (18): Illinois, Indiana, Iowa, Maryland, Michigan, Michigan State, Minnesota, Nebraska, Northwestern, Ohio State, Oregon, Penn State, Purdue, Rutgers, UCLA, USC, Washington, Wisconsin
ACC (17): Boston College, Cal, Clemson, Duke, Florida State, Georgia Tech, Louisville, Miami, NC State, North Carolina, Pittsburgh, SMU, Stanford, Syracuse, Virginia, Virginia Tech, Wake Forest
Big 12 (16): Arizona, Arizona State, Baylor, BYU, Cincinnati, Colorado, Houston, Iowa State, Kansas, Kansas State, Oklahoma State, TCU, Texas Tech, UCF, Utah, West Virginia
PAC-12 (8): Boise State, Colorado State, Fresno State, Oregon State, San Diego State, Texas State, Utah State, Washington State
FBS Independents (2): Notre Dame, UConn
AAC (14): Army, Charlotte, East Carolina, Florida Atlantic, Memphis, Navy, North Texas, Rice, South Florida, Temple, Tulane, Tulsa, UAB, UTSA
Sun Belt (14): App State, Arkansas State, Coastal Carolina, Georgia Southern, Georgia State, James Madison, Louisiana, Louisiana Tech, Marshall, Old Dominion, South Alabama, Southern Miss, Troy, UL Monroe
MWC (10): Air Force, Hawaii, Nevada, New Mexico, North Dakota State, Northern Illinois, San Jose State, UNLV, UTEP, Wyoming
MAC (13): Akron, Ball State, Bowling Green, Buffalo, Central Michigan, Eastern Michigan, Kent State, Massachusetts, Miami OH, Ohio, Sacramento State, Toledo, Western Michigan
CUSA (10): Delaware, FIU, Jacksonville State, Kennesaw State, Liberty, Middle Tennessee, Missouri State, New Mexico State, Sam Houston, Western Kentucky

## Team Context (use this to guide what to look for)

{memory_block}Team: {team_name}
Conference: {conference}
Head Coach: {coach} | Record: {context.get('coach_record', '')} | {context.get('coach_years', '')}
{f"Previous Staff (2025) — HC: {prev_coach} | OC: {prev_oc} | DC: {prev_dc}" if prev_coach else "Previous coaching staff: Not in DB — do NOT name or guess any former coaches or coordinators"}
2025 Record: {context.get('last_season_record', '')} | ATS: {context.get('last_season_ats', '')}
2025 One Score Game Record: {close_game_record} | Under {coach}: {close_game_overall_display}
2025 Turnover Margin: {turnover_display}
Regression Flags: {regression_display}
4-Year Record: {four_yr}
Power Rating: #{power_rank} overall | Offense: #{off_rank} | Defense: #{def_rank}
PPA: Offense #{ppa_off} | Defense #{ppa_def}
Offense Profile: {off_profile}
Talent Rank: #{talent_rank}{f" | Blue Chip %: {bc_ratio}" if not is_g6 else ""}
2026 Profile: {profile}
OC: {oc} (#{oc_rank}) | DC: {dc} (#{dc_rank})
Returning Starters: {ret_starters}
QB Situation: {qb_note}
Recruiting Class Rank: {recruit_class_rank} | Portal Class Rank: #{portal_class_rank}
Portal Net: {portal_net:+d} ({len(portal_in)} in, {len(portal_out)} out)
{portal_block}
{recruit_block}

{notes_block}
{schedule_block}
{roster_block}
{best_players_block}
## Research Mode: {mode.upper()}
Current focus: {mode_focus}
Current cycle year: {cycle_year}
Source recency floor: {min_source_date} — IGNORE any article, video, or web-search hit dated before this as current reporting. If a URL contains a year segment (e.g. `/2025/`, `/2024/`) that does not match the current cycle year ({cycle_year}), exclude it. If a source's publish date cannot be determined, treat it as background only — never place it in `agent_flags.high_confidence`.

## Your Research Tasks

1. **YouTube Research** — Videos have been pre-fetched for you below. For each football-relevant video:
   - Fetch the URL and read/watch enough to extract 2-4 specific key points
   - Assess sentiment (optimistic / cautious / concerned / mixed / neutral)
   - If a video is not football-relevant (basketball, baseball, etc.) skip it entirely
   - **Sport-discrimination on multi-sport channels (strict):** Many fan podcasts (especially "Locked On [Team]" / generic "[Team] Podcast" channels) cover multiple sports. If a video title contains a basketball, baseball, or other-sport signal — `NCAA Tournament`, `March Madness`, `Final Four`, `Sweet 16`, `Elite 8`, `NIT`, `hoops`, `basketball`, `MLB`, `College World Series`, `Omaha`, `lacrosse`, `hockey`, named non-football coach, or a named non-football player — SKIP THE VIDEO ENTIRELY even if the title also mentions football (e.g., "NCAA Ruling on [basketball player] | NFL Draft Preview"). Mixed-sport videos contaminate the football analysis because non-football names get pulled into key_points and then promoted into storylines.
   - **Per-key-point sport check:** every key_point you extract must be unambiguously about football. If a key_point names a player you cannot tie to this team's `full_roster`, `portal_in`, `portal_out`, `recruiting_class_2026`, or `top_portal_additions`, AND the source video does not explicitly identify them by football position (WR, DT, LB, etc.) or as a "football transfer/commit/decommit," DROP that key_point. Do not record speculative "[name] going to/from [school]" lines without sport tagging — they will poison synthesis. Better to keep 1 verified key_point than 3 with an unverified name.

{youtube_block}

2. **Written Sources** — Article content has been pre-fetched and is provided inline below. For each:
   - Read the 'Content (pre-fetched)' text and extract 2-4 specific key points — do NOT fetch RSS article URLs
   - Assess sentiment (optimistic / cautious / concerned / mixed / neutral)
   - Skip articles clearly not about {team_name} 2026 football (other sports, recruiting classes beyond 2026)
   - Only fetch a URL if it is explicitly marked as a paywalled/direct source without pre-fetched content

{written_block}

3. **Reddit Sources** — Fan and community perspective on the program:
   - Read post titles and any provided post text for sentiment signals and recurring themes
   - Note when multiple posts echo the same concern or optimism — that's a signal worth capturing
   - Weight these for overall_sentiment and key_storylines, not as factual reporting
   - If a Reddit post references a player or storyline also found in written sources, treat it as corroboration

{reddit_block}

4. **Web Search Fallback** — Only if Tasks 1, 2, and 3 leave obvious gaps, do a targeted search:
   - Maximum 2 searches total — be specific, not broad
   - Every search query MUST include the word "football" — no exceptions. This prevents cross-sport contamination from basketball, baseball, or other programs at the same school.
   - Every search query MUST include the cycle year ({cycle_year}) AND the recency operator `after:{min_source_date}` to keep results inside the current cycle.
   - Good examples: "{team_name} football injury update {cycle_year} after:{min_source_date}", "{team_name} football depth chart spring {cycle_year} after:{min_source_date}", "{team_name} {cycle_year} football outlook after:{min_source_date}"
   - For every result you consider, inspect the URL and visible date. Discard any result whose URL contains a year segment that is not {cycle_year}, or whose visible date predates {min_source_date}. The April 2025 Yahoo "spring takeaways" article that polluted a prior Buffalo run is the canonical case to avoid.
   - Do NOT search for things already covered by YouTube, written sources, or Reddit above
   - For any player found ONLY via web search (not in roster or pre-fetched sources): the source must explicitly mention "football" AND must not reference basketball or another sport in the same context. If you cannot confirm this, omit the player entirely.

5. **Synthesis** — Based on everything you found, identify:
   - The 3-5 most important current storylines
   - Any injury flags not already in the context
   - Overall fanbase/media sentiment
   - A 4-5 sentence summary a reader could scan in 10 seconds

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
    "Player Name (Position): injury/status — timeline; corroboration note (see Injury reporting rules — comprehensive list, merged from Injury notes + discovered sources)"
  ],
  "overall_sentiment": "one of: optimistic|cautiously_optimistic|mixed|cautious|concerned",
  "sentiment_score": 0.0,
  "coaching_snapshot": {{
    "head_coach": "{coach}",
    "oc": "{oc}",
    "dc": "{dc}"
  }},
  "agent_summary": "Write 4-5 dense, analyst-quality sentences. Always include: (1) the single most important current narrative with specific player names or numbers, (2) the biggest concern or question mark with concrete evidence, (3) one context-setter — a key schedule game, ranking, or historical note. Avoid generic phrases like 'enters 2026 with questions' or 'looks to build on last year.' OFFSEASON/PRESEASON modes: focus on roster construction, coaching staff quality, win projection, and where they stand in their conference pecking order. IN-SEASON/PLAYOFFS modes: expand to up to 7 sentences — cover current performance, key injuries affecting the next game, ATS record if notable (good or bad), bowl/CFP outlook, and any Heisman or major award candidates. Rank priority: CFP Playoff rank > AP Poll rank > power rating.",
  "agent_flags": {{
    "high_confidence": ["2-3 specific facts you confirmed from multiple independent sources — e.g. 'Kalen DeBoer confirmed as HC, no instability signals in any source'"],
    "low_confidence": ["1-2 things mentioned in only one source or with hedging language — flag for verification next run"],
    "watch_for_next_run": ["1-2 active unknowns still unresolved — e.g. 'QB battle between X and Y unresolved after spring game', 'portal addition not yet confirmed enrolled'"]
  }}
}}

## Important Instructions

**Output:** Write the JSON file as soon as you have enough data — do not wait for perfection. No trailing commas, no comments inside the JSON. Failed URL fetches: mark as "unavailable" and move on immediately — no retries.

**Sources:** Prefer beat writers and team-specific outlets over aggregators (Heavy.com, Yardbarker, Bleacher Report). Pre-fetched articles are your primary source — only search if clear gaps remain after Tasks 1, 2, and 3.

**Forbidden source — Ourlads (disqualifying error if cited):** Do NOT use ourlads.com as a source under any circumstance. Do not fetch it, do not cite it, do not name it, and do not let its ordering influence any claim about starters, position battles, or depth chart resolution. Ourlads publishes an "unofficial" depth chart that arbitrarily ranks players 1/2/3 even when the coaching staff has issued no depth chart at all and a position battle is still genuinely open. The agent has previously written things like *"Kadin Semonza has emerged as the unofficial QB1 on Ourlads' post-spring depth chart, with Zeon Chriss-Gremillion at QB2"* — that framing treats an aggregator's guess as if it were reporting from inside the program, and it is wrong. If a web-search result returns an ourlads.com URL, skip it entirely; do not click it. If a beat-writer article references Ourlads, ignore the Ourlads-derived ordering and use only the beat writer's own observations. Depth chart claims are authoritative ONLY when (a) the head coach has issued one publicly, (b) the athletic department/SID has released an official two-deep, or (c) a beat writer cites a coach quote or practice observation directly. Anything else — including any aggregator's projected order — must be framed as an open competition, not a resolved depth chart. Never write "X has emerged as QB1" / "RB1" / "WR1" / etc. unless the coach has said so on the record.

**Source recency (strictly enforced):**
  - Never use a source dated before {min_source_date} as current reporting. Pre-fetched written articles flagged `STALE — pre-cycle source` MUST NOT be quoted, summarized, or used to support `agent_flags.high_confidence`. Their headlines may be acknowledged only as "from a prior cycle" if absolutely needed for context.
  - For web-search results: inspect the URL path and any visible date. If the URL contains a year segment that is not {cycle_year} (e.g. `/2025/04/`, `/articles/2024-...`), or the snippet's date predates {min_source_date}, discard the result. Do NOT click through and rationalize it as still relevant.
  - For YouTube items: the `published` timestamp on each video is authoritative. If it predates {min_source_date}, drop the entry — even if the title sounds current. Re-uploaded or re-promoted old footage is a known failure mode.
  - When a source has no recoverable date at all, you may use it for background framing only. Never place an undated source in `agent_flags.high_confidence`, never let it be the sole support for a `key_storylines` claim.
  - Canonical regression case: an April 2025 Yahoo "4 takeaways from UB spring football" article was previously ingested as current 2026 reporting and produced a fabricated 2026 spring-game narrative. The recency floor + URL year-segment check exist specifically to prevent that pattern.

**Schedule accuracy (strictly enforced):**
  - The `conference_game` field in the schedule data is authoritative — it is computed from actual 2026 conference rosters. Never override it with your own knowledge of conference membership. If `conference_game: true`, it is a conference game, full stop.
  - Game location codes: `"location": "vs"` = home game; `"location": "at"` = away game; `"location": "vs*"` = NEUTRAL SITE. Never describe a neutral site game as a "home game" or "home opener." Use "neutral site" or the specific venue/city if known from sources.
  - Spreads must always be shown from THIS team's perspective. If this team is an underdog, the spread is positive (e.g., +7). If this team is favored, the spread is negative (e.g., -7). Never show an underdog with a negative spread.
  - **Chronological fidelity — never skip games.** When describing the schedule, walk it in order. Do NOT call Game N "the opener" when Game N-1 exists. A team that plays a tune-up in Week 1 then a marquee opponent in Week 2 OPENS in Week 1 — frame the Week 2 game as "the first major test," "the first Power-Four opponent," or "the first résumé moment" instead, with explicit acknowledgement of Week 1 (e.g., "after a Week 1 tune-up vs UTEP, Oklahoma travels to Michigan…"). Specifically forbidden: writing "the season opens at [marquee opponent]" or "[team] opens against [marquee opponent]" when the actual Week 1 game is a different team. Same principle applies to mid-season stretches and finales: never elide a game from the chronology because it's narratively less interesting. Recent failure: agent wrote "The season opens at Michigan — a road trip that serves as the first real résumé moment" for Oklahoma when Oklahoma's actual Week 1 was UTEP.
  - **Name the opponent, not the tier.** When `schedule_2026` lists a specific opponent for an upcoming game, NAME that opponent — never substitute a vague tier descriptor like "a Power Four opponent," "a P4 road trip," "an SEC opponent," "a marquee nonconference test," or "a tough nonconference foe" when the school is in the data. Write "a Week 3 trip to Tennessee" or "a road date at Florida State" — not "a trip to face a Power Four opponent early." The "first Power-Four opponent" phrasing in the chronological-fidelity rule above is shorthand reserved for cases where you are deliberately NOT naming the opponent yet (because you'll name them in the next sentence) — it is NOT a license to leave the opponent unnamed in the only sentence about that game. Tier-only framing ("a P4 opponent") is acceptable ONLY when no opponent is listed in the data (TBD slots, PAC-12 flexible Week 13). Concrete failure to AVOID: "A challenging nonconference slate, including a trip to face a Power Four opponent early, will clarify quickly whether the improvement is real." — The schedule names the opponent; the sentence must too.
  - Season length: the FBS regular season is 12 games for every team. The only 2026 exception is the PAC-12, whose eight members have 11 scheduled games while they experiment with a flexible final-weekend matchup format (the 12th game is TBD, not missing). Never write "13-game schedule," "14-game schedule," or any other number — bowls, conference championship games, and the College Football Playoff are POSTSEASON and are not part of the regular-season game count. Do not speculate about a team playing more or fewer than 12 regular-season games; rescheduling or cancellations only happen due to unforeseen events (weather, emergencies) and should not be anticipated in a writeup.

**Data sanity:** If a context value is implausible (a percentage above 100, a rank of "None", an obviously wrong stat), do not use it — omit it silently. Offense-type labeling must match the numeric split: never call an offense "run-first" if pass_pct > run_pct, or "pass-first" if run_pct > pass_pct — the context numbers override any article label. Always use the `projected_record` value from Team Context for win projections — never derive or estimate a different number.

**Coaching staff (disqualifying error if wrong):** Use ONLY the Head Coach, OC, and DC named in Team Context. Never name a former coach under any circumstances — describe changes generically (e.g. "following the previous staff departure"). Do not label a head coach as "on the hot seat" or question his job security unless that exact characterization appears in a cited source — this applies especially to coaches in their first or second year.

**Football players only (disqualifying error if wrong):** Every player named anywhere in your output must be a verified COLLEGE FOOTBALL player connected to THIS team. Three verification paths exist, each with a hard gate — pick the one that matches the framing you are using, and if no path applies, OMIT the name entirely:

  (a) **Roster path** — the player appears in the Key Players / full_roster block for THIS team. Required for any active-roster framing: starter, returner, position battle, depth chart, injury, snap count, anchor of a position group.

  (b) **Team data path** — the player appears in this team's `portal_in`, `portal_out`, `top_portal_additions`, or `recruiting_class_2026` block. Required for any "new arrival," "departed transfer," "decommit," or "incoming class" framing about THIS team. If the player is not in any of these structured fields, this path fails — do not promote a source mention into a "transfer in/out" claim.

  (c) **Source path with explicit football tag** — a cited source identifies the player by an EXPLICIT football position label ("WR," "DT," "LB," "QB," "edge," "safety," etc.), an EXPLICIT sport tag ("football transfer," "2026 football signee," "spring football," "fall camp," "two-deep"), or unambiguous football beat-writer context (named football beat outlet, football coordinator quote about them, football depth-chart discussion). A name + destination school + generic "transfer/portal/commit/decommit/chose" wording is NOT sufficient — the NCAA transfer portal exists for roughly 30 sports, and "Player X to Duke" with no sport tag could be basketball, baseball, lacrosse, or any other sport. Generic "athlete" / "recruit" / "prospect" language is also insufficient without a position label.

**No positional fabrication:** never invent a position-group context (WR rotation, RB room, OL depth, secondary, front seven, etc.) around a player who has not cleared (a), (b), or (c). Even if the name later turns out to be a real football player, wrapping an unverified name in "leaves [team] relying on its WR rotation" or "joins the RB room" framing is itself a disqualifying error. The fabrication is the failure, independent of the name.

**Recruiting / portal loss framings — extra scrutiny:** "Portal miss on X," "[team] lost out on X," "X chose [other school] over [this team]," "X decommitted to [other school]" — these are HIGH-RISK framings because they name a player who by definition is NOT on the roster. Use them ONLY when X appears in this team's `recruiting_class_2026` as a decommit, OR when a cited source explicitly tags X as a football player by position. Otherwise cut the entire sentence — do not soften it, do not generalize it, just delete it.

**Recruits vs current roster — separation rule (strictly enforced, disqualifying if blended):** A recruit who is not yet on the active 2026 roster is NOT depth, NOT a returner, NOT a piece of the current-year position group. The two categories must be presented separately. Concrete rules:

  (R1) **Eligible recruit names — `recruiting_class_2026` only.** The only recruits you may name anywhere in the writeup are those in this team's `Top 2026 recruits` block above (the structured field). Any other commit, signee, or named-recruit mention picked up from beat-writer coverage, YouTube content, web search, or any other source — whether described as a "2027 commit," "2027 pledge," "future class," or just "Player X just committed to [team]" — must be OMITTED entirely. Beat writers cover 2027 (and even 2028) commits constantly during the spring/summer; that is recruiting beat content, not 2026 preview content.

  (R2) **Recruits never appear inside current-position-group sentences.** Do NOT name a recruit (even a verified 2026 signee) inside a sentence that discusses the current position group's depth, returners, production, or state. The failure mode to avoid: *"The WR room remains a structural liability — Lewis Bond's NFL departure stripped the unit's most experienced FBS producer, no 2025 returner cleared 13 receptions, and while Armani Hill's commitment adds a name, Jaedn Skeete and Reed Swanson still headline a group..."* That sentence reads to a fan as if Armani Hill is a freshman WR competing for snaps, when he's actually a 2027 commit who won't be on the roster for 18 months. The recruit name doesn't help the 2026 team and confuses the reader about who the current depth actually is. Cut the recruit reference; keep the returner/portal/departure analysis intact.

  (R3) **Where recruiting CAN appear.** Recruiting momentum is a legitimate preview topic — fans want to know whether the program is trending up or down. If you discuss the recruiting class, do it as a SEPARATE beat: a class-rank summary, a portal-vs-recruiting balance comment, or one sentence about the top 2026 signee framed explicitly as "incoming freshman" or "2026 signee." Examples that are FINE: *"Boston College's 2026 class ranks 48th nationally, headlined by 4★ WR signee Jordan Hicks who'll have a chance to crack a thin receiver rotation as a true freshman."* / *"The portal haul (#12 nationally) and #48 recruiting class together signal a program leaning into year-three roster construction."* What this rule forbids is BLENDING — slipping a recruit name into a sentence whose subject is the current depth chart or position group state.

  (R4) **Asymmetric volume is expected.** Many teams will have no recruiting-class beat worth mentioning at all in a given run (no movement, no top-end commits, average class). Many others will have a full paragraph's worth. That's fine — give recruiting the space it earns from the data, no more and no less. Do not invent a "recruiting bright spot" sentence just to round out the writeup, and do not crowbar a single 2027 name into a roster paragraph because it's the only recruiting note you found.

**Concrete example to avoid (2026-04 Big Ten Illinois run):** the agent wrote *"The portal miss on John Blackwell (confirmed to Duke) leaves Illinois relying on its existing WR rotation of Jayshon Platt, Alex Perry, Collin Dixon, and Hudson Clement."* John Blackwell is a Wisconsin → Duke BASKETBALL transfer with no connection to Illinois football, and the "WR rotation" framing was fabricated around him. This is a textbook compound failure: an unverified non-football name AND an invented positional context. Both halves are disqualifying.

**Pre-flight verification (mandatory before returning JSON):** scan every player name in your output one final time. For each name, mentally tag the verification path you used: `[a roster]`, `[b portal_in]`, `[b portal_out]`, `[b recruiting_2026]`, or `[c source: <position tag>]`. If you cannot tag a name with a specific path, REMOVE THE NAME from the output. There is zero penalty for omitting a player; there is a disqualifying penalty for naming a non-football player or fabricating positional context around an unverified name.

**Player rules (strictly enforced):**
  (1a) Name–summary consistency: if you named a specific player in key_storylines (following the rules below), you SHOULD also name them in agent_summary when referencing that same storyline. Do not strip a verified name down to a generic descriptor ("a true freshman receiver," "a spring transfer," "a portal addition") in the summary after you've already identified them by name in storylines — that makes the summary less useful than the storylines feeding it. Specificity travels up.
  (1) Name players as leaders or standouts ONLY from the Key Players list — not from sources. When discussing any position group (whether as a strength, concern, or storyline), if a player from that group appears in the Key Players list, they MUST be mentioned — do not construct a positional narrative that omits the most prominent established player in favor of newcomers, depth questions, or concerns. The top returning player at a position is the anchor; depth issues are secondary context.
  (2) Before placing any player in a positional context (QB battle, RB room, OL depth), verify their position_group in the Roster block. If it doesn't match, remove the name entirely.
  (3) `portal_in` contains ONLY this offseason's new transfers — prior-cycle transfers are already integrated as returning players and do NOT appear in portal_in. If a player is on the roster but not in portal_in, treat them as a returner regardless of what any article says about their transfer history. Never label a player a "transfer" or "newcomer" based on an article alone — portal_in is the authoritative list of new arrivals. A player not on portal_out is still on the team.

  (3b) **Portal-out symmetry (strict):** `portal_out` is the authoritative list of departed transfers — same rule as (3) in reverse. Never claim a player has "transferred out," "is transferring out," "is leaving the program," or any equivalent based on source mentions alone if they are NOT in portal_out. This applies even when a YouTube video title, podcast description, or fan-forum post says they are gone. If you cannot find the player in portal_out, treat them as still on the roster — the structured data is fresher and more reliable than fan-podcast titles. Combined with the 2026 single-window rule (rule 6b): if portal_out doesn't list them and the January window has already closed, the claim is almost certainly wrong and must be dropped, NOT softened with "reportedly" or "per [podcast]" or "not yet corroborated."

  (3c) **Podcast/video TITLES are not sources (strict):** A YouTube video title or podcast episode title is a clickbait teaser, not reporting. Titles like "Brandon Lee Transferring," "[Star Player] Out for Season," "[Coach] On The Hot Seat" routinely overstate or misrepresent the actual content. A claim of fact (player movement, injury, coaching change) sourced ONLY from a video/podcast title — with no corroboration in the show notes, no beat-writer confirmation, no structured-data appearance, and no second independent source — must be DROPPED entirely from key_storylines and agent_summary. You may keep it in `agent_flags.low_confidence` as a flag for the next run, but a low_confidence flag is NOT a license to also publish the claim. Anything you tag as low_confidence cannot also appear in key_storylines or agent_summary in the same run — pick one: either you have enough corroboration to publish, or you flag it for next run and stay silent now.
  (4) If a source contradicts the roster on position for a player WHO IS listed in the roster, ignore the source — the roster is ground truth for listed players.
  (4b) Do not describe a P4-to-P4 transfer as "unproven at this level." That label is reserved for players arriving from G6 programs or below (FBS G6, FCS, D2, JUCO). A transfer from another Power Four school is a peer-level move.
  (5) The roster is capped and does not include every player on the team. If a source names a specific player not found anywhere in the roster, you MAY include them in key_storylines only — use their name directly as given in the source. Do NOT add phrases like "not yet in depth rankings" or "emerging depth." Do NOT name uncapped players as starters, leaders, or key contributors in agent_summary.
  (6) Departed players (graduated, NFL Draft, transferred out, medical retirement) must never be framed as mysteriously absent from the current roster. If the context or a source explains where they went (e.g., NFL Draft note, portal_out, graduation, prior-run memory), state it plainly — "after Horvath's graduation" or "following X's NFL departure." If no explanation is available, simply omit the player entirely. NEVER write phrases like "absent from the capped roster," "missing from the official roster," "unaccounted for on the roster," or any language that implies something unusual about a player no longer being on the team. Normal eligibility turnover is expected and requires no narrative. A player mentioned in prior-run memory who is not on this year's roster has almost certainly graduated or moved on — assume the mundane explanation, not an anomaly.
  (6b) Transfer portal window (2026 rule change): The transfer portal now has a SINGLE window in early January. There is NO post-spring portal window anymore — the old April/May window has been eliminated. Between now and the end of the 2026 season, players on a roster CANNOT transfer. Never write that a current player is "expected to enter the portal," "drawing portal interest," "a portal risk," "could transfer," or any similar framing during the spring, summer, or in-season — those framings described the old two-window system and are factually wrong for 2026. If a source (even 247Sports, On3, Rivals) uses that language about a currently-rostered player, treat it as outdated boilerplate and omit it. Portal discussion in spring/summer/in-season should cover only: (a) portal_in additions already enrolled, (b) portal_out players who already departed in the January window, or (c) the upcoming January 2027 window as a future event — never current roster attrition risk.
  (7) Source fidelity on counts: when citing a numeric count from a source article (transfers, commits, returning starters, injuries, etc.), never attach a composition or unit claim the source did not make. If the source says "19 transfers enrolled — 12 on defense, 6 on offense, 1 specialist," you may write "19 transfers (12 on defense)" or simply "19 transfers." You may NOT write "19 transfers remake the front seven," "19 new defenders," or any phrasing that reassigns the total to a single unit or position group. Either preserve the source's own breakdown or stay at the total — do not invent a composition. This rule applies equally when the DB count differs from the source count: prefer the source number (local beats often have fresher info than CFBD), but carry the source's qualifiers with it.

**Schedule fidelity (disqualifying error if wrong):** The season opener is whatever game is listed FIRST in the `schedule_2026` block — do NOT skip over FCS opponents or tune-up games when identifying the opener, describing early-season matchups, or framing the opening stretch. If the Week 1 game is against an FCS opponent, that IS the opener; a marquee Week 2 P4 matchup is the "Week 2 opener" or "second game," not "the opener." Similarly, when describing an "early gauntlet" or "opening stretch," include every game in order — an FCS Week 1 followed by a tough Week 2 is a legitimate "soft-then-spike" framing, not grounds to pretend Week 1 didn't exist. Phrases like "opens at home against [P4 team]" or "travels immediately to [P4 team]" must match the actual first/second entries in schedule_2026. Use each game's `location` field ("vs"/"at"/"vs*") to describe it correctly — and never omit a game from the chronological narrative because it seems low-stakes.

**Language rules:**
  - Never use "G5" — always use "G6" to refer to non-Power Four FBS programs.
  - Do not use conference divisions, like SEC East or Big Ten West.  Conferences no longer split into divisions in 2026.
  - Do not use superlatives ("most significant," "largest," "most dominant," "highest-ever") without a cited source making that exact claim. "The most significant roster overhaul in the country" requires a source — if you don't have one, cut it.
  - Conference tier (2026): The Power Four (P4) is SEC, Big Ten, ACC, Big 12, plus Notre Dame (FBS Independent) — 69 teams. Everything else is Group of Six (G6): PAC-12, AAC, Sun Belt, MWC, MAC, CUSA, and UConn. The PAC-12 is a G6 conference in 2026 — never call it or its members "Power Four" or place them "among the P4 field."
  - P4 ranking context (P4 teams only): there are 69 Power Four teams. Top 17 = elite (top quarter); 18-35 = above average; 36-52 = below average; 53-69 = bottom quarter. A team ranked #38 is "slightly below average" — calibrate language precisely. Do NOT apply these bands to a G6 team.
  - G6 ranking context (G6 teams only): the `power_rank` field is an FBS-wide rank across 138 teams. For a G6 team, frame their standing against the full 138-team FBS field (top third ≈ #1-46, middle third ≈ #47-92, bottom third ≈ #93-138) AND/OR relative to the ~69-team G6 pool (a G6 team in the FBS top 50 is near the top of G6). A G6 team ranked #49 FBS-overall is in the upper third of FBS and an upper-tier G6 program — never "below average" against a P4 frame.
  - Never use "dead last," "last place," or "last" to describe a specific rank unless it equals the total number of teams in that pool. In FBS, #138 is last. In P4, #69 is last. A team ranked #100 has 38 teams below them — do not call it last.
  - Blue chip ratio is only meaningful for programs competing for the College Football Playoff and national titles. Do not reference blue chip % for G6 programs — it is near zero for nearly all of them and adds no analytical value.
  - Historical claims (a coach's record against specific opponents, program milestones, conference standings history) must come from the provided context or a cited source — never from training knowledge alone.
  - Avoid using the term gauntlet to describe matchups unless playing high power ranked teams (#30 and up).  A run of even handed games (spreads +/- 6) is a challenging stretch, a guantlet is playing the #2 and #12 power ranked teams within a few weeks.

**QB experience rule (strictly enforced):** The "QB Situation" field in Team Context is the authoritative source on the quarterback's status and experience. If context identifies a QB as a returning starter, never describe them as "unproven," "untested," or someone who "hasn't proved it" — not from sources, and not as your own editorial synthesis. This prohibition is absolute: do not generate this framing yourself even if no source says it. You may report a spring injury, a competition, or a concern about depth accurately — but those facts stand alone. Do not attach editorial conclusions about a returning starter's track record that the context data contradicts.

**Regression analysis:** If the Regression Flags field above is not "None identified," incorporate those flags into your analysis as a key storyline or within agent_summary. One-score game records are one of the strongest regression indicators in college football — most one-score games even out over time, so a team that went 6-1 is a strong candidate to regress in close games the following season, while a team that went 1-6 is a strong candidate to improve. Frame one-score regression with confidence. Turnover margin is a less reliable indicator — some defenses genuinely create turnovers through scheme and talent, and some offenses have persistent ball-security problems, so extreme turnover margins don't always revert. Frame turnover regression as "worth monitoring" rather than an expectation. If BOTH a one-score flag and a turnover flag point in the same direction (e.g., team won lots of close games AND had an unsustainably high turnover margin), that strengthens the regression case and should be treated as a major storyline.

**Injury reporting (comprehensive list — strictly enforced):** The `injury_flags` array must be a COMPREHENSIVE roster-health snapshot for the 2026 season, not a net-new-findings list. Build it by merging two sources and deduplicating:
  1. Every entry in the "Injury notes" block above (context-sourced — already verified in the data layer; include them all).
  2. Any additional injuries surfaced in YouTube, written sources, Reddit, or web search.
Each player appears exactly ONCE. If a player is covered by both sources, merge into the single most complete entry.

Notable threshold (mode-aware — use the current Research Mode above):
  - Any mode: season-ending injuries, surgeries with multi-month recovery, any reported "out indefinitely" or "expected to miss significant time."
  - `spring_offseason` / `preseason`: injuries that may affect fall camp availability or Week 1 status — include even if the timeline sounds hopeful.
  - `in_season` / `postseason`: any player who may miss the upcoming game (week-to-week or worse). Include day-to-day status for listed starters when a beat writer has flagged it by name.
  - `early_offseason`: lingering surgeries from the prior season; this list is usually short.
Exclude routine bumps and bruises, and exclude players who have fully recovered.

Resolution rule: DO NOT carry a player forward from the prior_injury_flags shown in the prior run notes unless current sources OR the Injury notes above confirm the injury is still active. Cleared players drop off the list naturally. If a timeline has shifted or the injury has worsened, update the entry to reflect the newest reporting — beat writers will usually catch those changes.

Format convention: Each entry should follow the pattern `"Player Name (Position): injury/status — timeline or expected impact; corroboration note if applicable"`. Accuracy beats rigid formatting — if a source's phrasing is more precise, keep it. Examples:
  - "Kyngstonn Viliamu-Asa (LB): knee — out through September 6 Wisconsin opener; corroborated across multiple beat outlets"
  - "Drayk Bowen (LB): hip labrum surgery — targeting early-June full clearance; on track for fall camp"
  - "Quincy Porter (WR): undisclosed — missed entire spring; no return timeline reported"
  - "Jagusah (OL): undisclosed — possibly out for 2026 season; flagged in team notes, not yet corroborated by beat coverage"

If there are genuinely no notable injuries on the roster (rare — most common in early offseason for healthy programs), return an empty array `[]`. Do not include placeholder entries like "no specific injury flags."

**Storylines:** key_storylines must be concrete and specific, not generic. Bad: "team has questions at QB." Good: "Austin Mack vs Keelon Russell QB battle unresolved after spring."

**Storyline continuity:** If prior run notes include tracked storyline threads, your key_storylines should update those threads where possible — use similar language and keywords so the memory system can match them across runs. If a tracked storyline has resolved (e.g. QB battle decided, coaching hire confirmed), you may drop it from key_storylines and it will age out naturally. If a storyline marked [STALE] is still relevant based on your sources, include it again to keep it active. Do NOT invent storyline updates — only update a thread if your current sources have new information.

**Writeup composition (lifecycle-weighted real estate, constant total length):** Tracked storyline threads in the PRIOR RUN NOTES are grouped by lifecycle stage — DEVELOPING, CONTINUING, SETTLED. These tags govern how much room each thread gets in `agent_summary` (the prose writeup readers actually see):

  - **DEVELOPING** (new this cycle or materially advanced): lead the writeup. Full paragraph treatment with specific names, numbers, and stakes. This is what makes the writeup feel current — readers came back BECAUSE something is moving here.

  - **CONTINUING** (active, load-bearing, no new dimensions this cycle): paragraph-length context, but do NOT restate every prior angle. One tight pass on the current state, then move on.

  - **SETTLED** (true and important, but converged across recent runs — same point being made repeatedly with minor rephrasing): compress to ONE clause or short sentence. The reader still needs to encounter the fact (the site replaces a preview magazine; a first-time visitor in July must still learn CJ Carr is the QB and a Heisman contender, that the regression indicators favor the team, etc.). What they do NOT need is two paragraphs of the same point reworded.

  This is a composition rule, NOT a length rule — the writeup total stays the SAME length spec'd in the agent_summary field (4-5 sentences offseason, up to 7 in-season). What shifts is the allocation: as the calendar advances, more threads move into SETTLED and the compressed clauses make room for fresh DEVELOPING material at paragraph length. By August most spring storylines should be settled one-liners and fall-camp news should dominate. The total length never shrinks — a short writeup signals "nothing is happening" to a first-time reader, and that's the wrong message for a live preview site that's better than a magazine.

  Concrete bad pattern to avoid: when nothing is genuinely new this run, the temptation is to expand SETTLED threads back into paragraphs by rephrasing them. Resist this. If the slate is light, write a thorough, compact summary of the settled landscape (one clause per settled thread) and lead with whichever continuing or developing storyline has the most life. The writeup should always READ like a beat writer giving you the current snapshot, never like a recap of what's been said before.

  Stage tags on threads are advisory inputs to YOU — do not echo the tags themselves in the prose. The reader sees only the writeup, not the staging.

**Prior-storyline sport audit (mandatory before treating any thread as football):** prior runs may have written storyline threads that quietly contain a non-football player (a basketball-podcast contamination, a misclassified portal name, a recruit later confirmed to a different sport). For every prior-run storyline thread you intend to "update" or "resolve," apply the same Football-Players-Only verification: if the thread names a player, that player must pass path (a) roster, (b) team data (portal_in/out, recruiting_class_2026, top_portal_additions), or (c) explicit football tag in a current source. If they fail all three, DO NOT update or "resolve" the thread — instead, leave it unmentioned so it ages out naturally, and treat the prior thread as suspect rather than as ground truth. A "resolution" of a contaminated prior thread (e.g., "the prior portal competition for [non-football player] is now resolved") propagates the original error and makes it look corroborated — that is the worst possible outcome.

**Tone:** Write as a knowledgeable, even-handed CFB analyst who's covered fall Saturdays for twenty years out of genuine love for the sport. Quick with a dry one-liner when the moment calls for it (e.g., "Their O-line situation is held together with duct tape and prayer, but the tape is at least name-brand."), but grounded in specifics and film. Respects the offseason grind, holds defensible opinions, and has a soft spot for the beautiful quirks of college football and weird special-teams stories. Humor is a seasoning, not the main course — no hot takes, no shouting, more press-box veteran than studio yeller.
Mode-aware calibration:
  - `spring_offseason` and `preseason`: lean toward earned optimism. Spring and summer are the seasons of reasonable hope — most programs genuinely are trying to get better, and fans deserve a writeup that takes their team's offseason investments seriously. Identify the real reasons for optimism (returning production, portal hits, staff continuity, schedule breaks) and present them plainly. Acknowledge concerns honestly, but don't lead with skepticism and don't pile on.
  - `in_season` and `postseason`: lean pragmatic. Results are on the field now, so analysis should track what's actually happened. Temper both hype and doom with the scoreboard.
  - `early_offseason`: reflective and fair — what worked, what didn't, what's next.
Always earn observations with concrete specifics from the context and sources — never with generic hype ("program-changing class," "elite coaching hire") and avoid generic skepticism ("we've seen this movie before," "every program says that"). Dry wit is good touch when it lands naturally on a real contradiction in the data; never force it, and never default to snark as a substitute for analysis. The goal is an entertaining writeup a smart, informed fan of this specific team would nod along with — not one that performs cynicism for its own sake.

**coaching_snapshot:** Copy the head_coach, oc, and dc values exactly as given in Team Context — do not modify them.

**agent_flags:** Fill these in honestly after completing the rest of the JSON:
  - high_confidence: Facts you confirmed from 2+ independent sources. Be specific: "OL starter list confirmed across beat coverage and spring depth chart" beats "coaching staff is stable."
  - low_confidence: Things mentioned in only one source or with qualifier language ("reportedly," "expected to," "could"). Flag these for the next run to verify or drop.
  - watch_for_next_run: Active unknowns — unresolved depth chart battles, portal additions not yet confirmed enrolled, injuries with unclear return timelines. Max 2 items. Be concrete: "QB battle between Mack and Russell still open" not "depth chart uncertainty."
  Keep each list to 3 items max. If nothing qualifies for a list, use an empty array.

**sentiment_score:** 0.0 = extremely negative · 0.5 = neutral · 1.0 = extremely positive.
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
    # Strip null bytes — can sneak in from Playwright scrape encoding issues
    # and cause subprocess.run to fail with "embedded null byte"
    prompt = prompt.replace('\x00', '')

    prompt_file = BASE_DIR / "logs" / f"prompt_{slug}.txt"
    prompt_file.write_text(prompt)

    cmd = [
        CLAUDE_BIN, "--dangerously-skip-permissions",
        "--output-format", "json",
        "-p", prompt,
    ]

    if debug:
        logging.info(f"Running: {' '.join(cmd[:3])} [prompt length: {len(prompt)}]")

    # Retry loop — initial attempt + (MAX_ATTEMPTS - 1) retries on transient failure.
    # Timeouts and unexpected exceptions break out early (rarely transient).
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if attempt > 1:
            logging.info(f"  [{slug}] Retry {attempt - 1}/{MAX_ATTEMPTS - 1} — waiting {RETRY_DELAY}s")
            time.sleep(RETRY_DELAY)

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

            # Capture cost + token usage from the JSON envelope on stdout.
            # Logs one row to logs/agent_cost_log.csv per attempt; never raises.
            log_run(
                pipeline   = "team_research",
                slug       = slug,
                elapsed    = elapsed,
                returncode = result.returncode,
                stdout     = result.stdout,
            )

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
                        # fall through to retry
                else:
                    logging.warning(f"  ✗ {slug} — agent ran but no output file written ({elapsed}s)")
                    if debug:
                        logging.debug(f"  Agent stdout: {result.stdout[:500]}")
                    # fall through to retry
            else:
                logging.error(f"  ✗ {slug} — agent exited with code {result.returncode} ({elapsed}s)")
                if result.stderr:
                    logging.error(f"  stderr: {result.stderr[:500]}")
                if result.stdout:
                    # Tail of stdout often carries the real error from the Claude CLI
                    tail = result.stdout[-500:] if len(result.stdout) > 500 else result.stdout
                    logging.error(f"  stdout tail: {tail}")
                # fall through to retry

        except subprocess.TimeoutExpired:
            logging.error(f"  ✗ {slug} — timed out after 900s (no retry)")
            # Timeout — cost was incurred but stdout was not captured. Log a
            # row with blanks so we still see the run in the cost CSV.
            log_run(
                pipeline   = "team_research",
                slug       = slug,
                elapsed    = round(time.time() - start, 1),
                returncode = None,
                stdout     = "",
            )
            return False
        except Exception as e:
            logging.error(f"  ✗ {slug} — unexpected error: {e} (no retry)")
            return False

    logging.error(f"  ✗ {slug} — all {MAX_ATTEMPTS} attempts failed")
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