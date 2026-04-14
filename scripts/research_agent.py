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

Active conferences: sec, big10, acc, big12, fbsind
Inactive (uncomment in CONFERENCE_TEAMS to enable):
    pac12, aac, sbc, mwc, mac, cusa

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
    # Research mode — determined early so roster caps can reference it
    # ---------------------------------------------------------------------------
    month = datetime.now().month
    if month == 1:
        mode = "cfb_playoffs"
        mode_focus = "college football playoffs, injury updates, weekly game prep, postseason news, portal activity, recruiting, coaching changes"
    elif month in (2, 3):
        mode = "early_offseason"
        mode_focus = "portal activity, recruiting, coaching changes, spring practice previews"
    elif month in (4, 5, 6):
        mode = "spring_offseason"
        mode_focus = "spring practice results, depth chart battles, injury news, expectations and predictions"
    elif month in (7, 8):
        mode = "preseason"
        mode_focus = "fall camp, depth chart, injury news, expectations and predictions"
    else:
        mode = "in_season"
        mode_focus = "injury updates, weekly game prep, performance analysis, fanbase pulse"

    # ---------------------------------------------------------------------------
    # Roster caps by mode — limits roster_block size without losing key players
    # Preseason/offseason: wider caps to cover position battles
    # In-season/playoffs: tighter caps, starters matter most
    # Keys match position_group values in team_context full_roster.
    # Fallback cap of 5 applies to any group not listed here.
    # ---------------------------------------------------------------------------
    _IN_SEASON_MODES = {'in_season', 'cfb_playoffs'}
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

        # Build storyline thread summaries (most recent update per thread)
        thread_lines = []
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
            thread_lines.append(f"  - {latest}{age_note}{status_tag}{source_tag}")

        # Use thread summaries if available, fall back to flat prior_storylines for backward compat
        if thread_lines:
            storylines_section = f"""Tracked storyline threads:
{chr(10).join(thread_lines)}"""
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
        ws_result = fetch_team_articles(slug, days=14, max_per_source=3, prefetch=True)
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
Sun Belt (13): App State, Arkansas State, Coastal Carolina, Georgia Southern, Georgia State, James Madison, Louisiana, Marshall, Old Dominion, South Alabama, Southern Miss, Troy, UL Monroe
MWC (10): Air Force, Hawaii, Nevada, New Mexico, North Dakota State, Northern Illinois, San Jose State, UNLV, UTEP, Wyoming
MAC (13): Akron, Ball State, Bowling Green, Buffalo, Central Michigan, Eastern Michigan, Kent State, Massachusetts, Miami OH, Ohio, Sacramento State, Toledo, Western Michigan
CUSA (11): Delaware, FIU, Jacksonville State, Kennesaw State, Liberty, Louisiana Tech, Middle Tennessee, Missouri State, New Mexico State, Sam Houston, Western Kentucky

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

## Your Research Tasks

1. **YouTube Research** — Videos have been pre-fetched for you below. For each football-relevant video:
   - Fetch the URL and read/watch enough to extract 2-4 specific key points
   - Assess sentiment (optimistic / cautious / concerned / mixed / neutral)
   - If a video is not football-relevant (basketball, baseball, etc.) skip it entirely

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
   - Good examples: "{team_name} football injury update April 2026", "{team_name} football depth chart spring 2026", "{team_name} 2026 football outlook"
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
    "any injuries not already in context file"
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

**Schedule accuracy (strictly enforced):**
  - The `conference_game` field in the schedule data is authoritative — it is computed from actual 2026 conference rosters. Never override it with your own knowledge of conference membership. If `conference_game: true`, it is a conference game, full stop.
  - Game location codes: `"location": "vs"` = home game; `"location": "at"` = away game; `"location": "vs*"` = NEUTRAL SITE. Never describe a neutral site game as a "home game" or "home opener." Use "neutral site" or the specific venue/city if known from sources.
  - Spreads must always be shown from THIS team's perspective. If this team is an underdog, the spread is positive (e.g., +7). If this team is favored, the spread is negative (e.g., -7). Never show an underdog with a negative spread.

**Data sanity:** If a context value is implausible (a percentage above 100, a rank of "None", an obviously wrong stat), do not use it — omit it silently. Offense-type labeling must match the numeric split: never call an offense "run-first" if pass_pct > run_pct, or "pass-first" if run_pct > pass_pct — the context numbers override any article label. Always use the `projected_record` value from Team Context for win projections — never derive or estimate a different number.

**Coaching staff (disqualifying error if wrong):** Use ONLY the Head Coach, OC, and DC named in Team Context. Never name a former coach under any circumstances — describe changes generically (e.g. "following the previous staff departure"). Do not label a head coach as "on the hot seat" or question his job security unless that exact characterization appears in a cited source — this applies especially to coaches in their first or second year.

**Football players only (disqualifying error if wrong):** Every player named in key_storylines or agent_summary must be a verified football player. Before naming any player, confirm they appear in the full_roster block OR that the source explicitly describes them in a football context (spring practice, depth chart, football transfer portal, etc.) AND does not reference basketball, baseball, or another sport in the same context. If both conditions are not met, skip the player entirely. This applies even when a source uses football-sounding language (e.g., "DT," "chasing a recruit") — always verify the sport is football. When in doubt, omit.

**Player rules (strictly enforced):**
  (1a) Name–summary consistency: if you named a specific player in key_storylines (following the rules below), you SHOULD also name them in agent_summary when referencing that same storyline. Do not strip a verified name down to a generic descriptor ("a true freshman receiver," "a spring transfer," "a portal addition") in the summary after you've already identified them by name in storylines — that makes the summary less useful than the storylines feeding it. Specificity travels up.
  (1) Name players as leaders or standouts ONLY from the Key Players list — not from sources. When discussing any position group (whether as a strength, concern, or storyline), if a player from that group appears in the Key Players list, they MUST be mentioned — do not construct a positional narrative that omits the most prominent established player in favor of newcomers, depth questions, or concerns. The top returning player at a position is the anchor; depth issues are secondary context.
  (2) Before placing any player in a positional context (QB battle, RB room, OL depth), verify their position_group in the Roster block. If it doesn't match, remove the name entirely.
  (3) `portal_in` contains ONLY this offseason's new transfers — prior-cycle transfers are already integrated as returning players and do NOT appear in portal_in. If a player is on the roster but not in portal_in, treat them as a returner regardless of what any article says about their transfer history. Never label a player a "transfer" or "newcomer" based on an article alone — portal_in is the authoritative list of new arrivals. A player not on portal_out is still on the team.
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

**QB experience rule (strictly enforced):** The "QB Situation" field in Team Context is the authoritative source on the quarterback's status and experience. If context identifies a QB as a returning starter, never describe them as "unproven," "untested," or someone who "hasn't proved it" — not from sources, and not as your own editorial synthesis. This prohibition is absolute: do not generate this framing yourself even if no source says it. You may report a spring injury, a competition, or a concern about depth accurately — but those facts stand alone. Do not attach editorial conclusions about a returning starter's track record that the context data contradicts.

**Regression analysis:** If the Regression Flags field above is not "None identified," incorporate those flags into your analysis as a key storyline or within agent_summary. One-score game records are one of the strongest regression indicators in college football — most one-score games even out over time, so a team that went 6-1 is a strong candidate to regress in close games the following season, while a team that went 1-6 is a strong candidate to improve. Frame one-score regression with confidence. Turnover margin is a less reliable indicator — some defenses genuinely create turnovers through scheme and talent, and some offenses have persistent ball-security problems, so extreme turnover margins don't always revert. Frame turnover regression as "worth monitoring" rather than an expectation. If BOTH a one-score flag and a turnover flag point in the same direction (e.g., team won lots of close games AND had an unsustainably high turnover margin), that strengthens the regression case and should be treated as a major storyline.

**Storylines:** key_storylines must be concrete and specific, not generic. Bad: "team has questions at QB." Good: "Austin Mack vs Keelon Russell QB battle unresolved after spring."

**Storyline continuity:** If prior run notes include tracked storyline threads, your key_storylines should update those threads where possible — use similar language and keywords so the memory system can match them across runs. If a tracked storyline has resolved (e.g. QB battle decided, coaching hire confirmed), you may drop it from key_storylines and it will age out naturally. If a storyline marked [STALE] is still relevant based on your sources, include it again to keep it active. Do NOT invent storyline updates — only update a thread if your current sources have new information.

**Tone:** Write as a knowledgeable, even-handed CFB analyst who respects the work programs put in during the offseason and stays grounded in specifics. Mode-aware calibration:
  - `spring_offseason` and `preseason`: lean toward earned optimism. Spring and summer are the seasons of reasonable hope — most programs genuinely are trying to get better, and fans deserve a writeup that takes their team's offseason investments seriously. Identify the real reasons for optimism (returning production, portal hits, staff continuity, schedule breaks) and present them plainly. Acknowledge concerns honestly, but don't lead with skepticism and don't pile on.
  - `in_season` and `cfb_playoffs`: lean pragmatic. Results are on the field now, so analysis should track what's actually happened. Temper both hype and doom with the scoreboard.
  - `early_offseason`: reflective and fair — what worked, what didn't, what's next.
Always earn observations with concrete specifics from the context and sources — never with generic hype ("program-changing class," "elite coaching hire") and never with generic skepticism ("we've seen this movie before," "every program says that"). Dry wit is fine when it lands naturally on a real contradiction in the data; never force it, and never default to snark as a substitute for analysis. The goal is a writeup a smart, informed fan of this specific team would nod along with — not one that performs cynicism for its own sake.

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
        logging.error(f"  ✗ {slug} — timed out after 900s")
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