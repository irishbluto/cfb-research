#!/usr/bin/env python3
"""
conference_research_agent.py
----------------------------
Spawns a Claude Code session per conference to write the magazine-style
preview that powers conference_previews.php on puntandrally.com. Reads the
deterministic conference_context/<slug>.json built by build_conference_context.py,
plus prior-run memory and the national landscape, and produces:

  /cfb-research/conference_previews/<slug>.json

Every guardrail from research_agent.py carries forward unchanged
(single portal window, 12-game season, Football-Players-Only verification
paths, no missing-player framing, P4/G6 terminology, no conference
divisions, schedule fidelity, etc). The tone block is the only intentional
change: Phil Steele meets Banner Society — wry, specific, affectionate,
willing to be funny but never at the expense of accuracy.

Usage:
    python3 scripts/conference_research_agent.py --conf sec
    python3 scripts/conference_research_agent.py --conf sec --dry-run
    python3 scripts/conference_research_agent.py --all
    python3 scripts/conference_research_agent.py --conf sec --debug

Importable:
    from conference_research_agent import build_prompt, run_agent
"""

import json, os, sys, time, argparse, subprocess, logging
from datetime import datetime
from pathlib import Path

BASE_DIR        = Path("/cfb-research")
CONF_CONTEXT    = BASE_DIR / "conference_context"
CONF_MEMORY     = BASE_DIR / "conference_memory"
CONF_PREVIEWS   = BASE_DIR / "conference_previews"
NATIONAL_DIR    = BASE_DIR / "national"
LOG_DIR         = BASE_DIR / "logs"
CLAUDE_BIN      = "/home/joleary/.local/bin/claude"

AGENT_TIMEOUT_SECS = 1800  # 30 min — prompt grew to ~40KB after rule additions;
                           # first run (smaller prompt) completed in 458s, so 30 min
                           # gives generous headroom without inviting runaway runs.

# Import canonical conference list from build_team_context.py
sys.path.insert(0, str(BASE_DIR / "scripts"))
from build_team_context import CONFERENCE_TEAMS  # noqa: E402


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging():
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"conference_preview_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ],
    )
    return log_file


# ---------------------------------------------------------------------------
# Mode (calendar-day boundaries, mirrors research_agent.py)
# ---------------------------------------------------------------------------
def get_mode():
    """
    Calendar-day mode boundaries. Conference previews only run May 1 – Aug 31
    in production, so the agent will see spring_offseason or preseason
    in nearly all cases. Other modes returned for completeness in case a
    manual run hits them.
    """
    now   = datetime.now()
    month = now.month
    day   = now.day
    if (month == 12 and day >= 6) or (month == 1 and day <= 25):
        return "postseason", "playoffs, bowls, portal window, coaching changes"
    if (month == 1 and day >= 26) or month in (2, 3):
        return "early_offseason", "portal activity, coaching changes, spring practice previews"
    if month in (4, 5, 6):
        return "spring_offseason", "spring practice results, portal analysis, recruiting class shape, expectations"
    if month == 7 or (month == 8 and day <= 28):
        return "preseason", "fall camp, depth charts, preseason polls, predictions"
    return "in_season", "weekly results, rankings, playoff picture"


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def load_conf_context(conf_slug):
    path = CONF_CONTEXT / f"{conf_slug}.json"
    if not path.exists():
        raise RuntimeError(f"No conference_context for {conf_slug} at {path}. "
                           f"Run build_conference_context.py first.")
    return json.loads(path.read_text())


def load_conf_memory(conf_slug):
    """Return dict or {} if no prior memory yet."""
    path = CONF_MEMORY / f"{conf_slug}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def load_national_landscape():
    """Return dict or {} if national pipeline hasn't run yet."""
    path = NATIONAL_DIR / "landscape_latest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Block formatters — turn structured JSON into prompt-friendly text blocks
# ---------------------------------------------------------------------------

def _fmt_standings(standings):
    """One row per team in projected standings order, dense and scannable."""
    lines = []
    for i, s in enumerate(standings, start=1):
        bits = [
            f"{i}. {s.get('team', s.get('url_param', ''))}",
            f"proj {s.get('projected_record', '?')} ({s.get('expected_conf_record', '?')} conf)",
            f"Power #{s.get('power_rank', '?')}",
            f"Talent #{s.get('talent_rank', '?')}",
            f"SOS #{s.get('sos_rank', '?')}",
            f"Returns {s.get('returning_production', '?')}%",
            f"HC {s.get('head_coach', '')} ({s.get('coach_years', '?')})",
        ]
        if s.get('starting_qb_name'):
            qb_back = s.get('qb_back', '')
            qb_tag = "returning starter" if qb_back == 'Y' else "new starter" if qb_back == 'N' else ""
            qb_str = f"QB {s['starting_qb_name']}" + (f" [{qb_tag}]" if qb_tag else "")
            bits.append(qb_str)
        lines.append(" | ".join(bits))
    return "\n".join(lines)


def _fmt_top_players(top_players):
    """Top 15 players by Production Numbers (P&R proprietary, NOT 247).

    Provenance marker is appended so the agent never frames a portal addition
    as a returning anchor (the Jackson Harris bug):
      [NEW – portal]   -> player is in this team's portal_in this offseason
      [NEW – freshman] -> player is in this team's recruiting_class_2026
      (no marker)      -> returning player from the prior season's roster
    """
    lines = []
    for i, p in enumerate(top_players, start=1):
        statsline = p.get('statsline', '')
        stat_str  = f" — {statsline}" if statsline else ""
        if p.get('is_portal_in'):
            tag = "  [NEW – portal]"
        elif p.get('is_recruit'):
            tag = "  [NEW – freshman]"
        else:
            tag = ""
        lines.append(
            f"{i}. {p.get('player_name', '?')} "
            f"({p.get('position', '?')}, {p.get('team', '?')})"
            f" · prod {p.get('points', 0)}{stat_str}{tag}"
        )
    return "\n".join(lines)


def _fmt_top_recruits(top_recruits):
    """Top 10 recruits — 247 ratings (display .XX)."""
    lines = []
    for i, r in enumerate(top_recruits, start=1):
        rating = r.get('rating')
        rating_str = f"{rating:.2f}".lstrip('0') if isinstance(rating, (int, float)) else "?"
        stars = r.get('stars')
        stars_str = "★" * stars if isinstance(stars, int) else ""
        loc = r.get('location', '')
        loc_str = f" · {loc}" if loc else ""
        lines.append(
            f"{i}. {r.get('name', '?')} "
            f"({r.get('position', '?')}, {r.get('team', '?')})"
            f" · {rating_str} {stars_str}{loc_str}"
        )
    return "\n".join(lines)


def _fmt_top_portal(top_portal):
    """Top 10 portal additions — 247 ratings (display .XX)."""
    lines = []
    for i, p in enumerate(top_portal, start=1):
        rating = p.get('rating')
        rating_str = f"{rating:.2f}".lstrip('0') if isinstance(rating, (int, float)) else "?"
        stars = p.get('stars')
        stars_str = "★" * stars if isinstance(stars, int) else ""
        origin = p.get('origin', '')
        origin_str = f" from {origin}" if origin else ""
        lines.append(
            f"{i}. {p.get('name', '?')} "
            f"({p.get('position', '?')}, {p.get('team', '?')})"
            f" · {rating_str} {stars_str}{origin_str}"
        )
    return "\n".join(lines)


def _fmt_history(history, conf_schedule_length=None):
    """4-yr conference records with per-year schedule length and totals column.

    The schedule-length row above the table is critical: it bounds the maximum
    possible conference wins per year so the agent can't claim "9 conf wins in
    2024" for an SEC team that only played 8. Totals column is pre-computed so
    the agent doesn't have to do arithmetic (and get it wrong).
    """
    years = history.get('years', [])
    if not years:
        return "(no history available)"

    sched = conf_schedule_length or {}
    sched_line = ""
    if sched:
        bits = []
        for y in years:
            g = sched.get(y, sched.get(str(y), '?'))
            bits.append(f"{y}={g}")
        sched_line = "Conference games per team by year: " + ", ".join(bits) + "\n"

    # Build a fixed-width table: Team | each year | Sum
    name_w = 28
    cell_w = 6
    header = "Team".ljust(name_w) + "".join(str(y).rjust(cell_w) for y in years) + "Sum".rjust(cell_w + 2)
    rule   = "-" * len(header)
    rows = [sched_line + header, rule]

    for r in history.get('records', []):
        team = (r.get('url_param', '') or '?').ljust(name_w)
        cells = "".join((r.get('seasons', {}).get(str(y), '—') or '—').rjust(cell_w) for y in years)
        total = f"{r.get('total_conf_wins', 0)}-{r.get('total_conf_losses', 0)}"
        rows.append(team + cells + total.rjust(cell_w + 2))
    return "\n".join(rows)


def _fmt_marquee_ooc(marquee_ooc):
    if not marquee_ooc:
        return "(no marquee OOC matchups identified)"
    lines = []
    for g in marquee_ooc:
        venue = g.get('venue', '') or ''
        outlet = g.get('outlet', '') or ''
        loc_bits = []
        if g.get('neutral'):
            loc_bits.append(f"neutral · {venue}" if venue else "neutral site")
        elif venue:
            loc_bits.append(venue)
        if outlet:
            loc_bits.append(outlet)
        loc_str = " · ".join(loc_bits)
        lines.append(
            f"  Week {g.get('week', '?')} {g.get('start_date', '')[:10]} | "
            f"{g.get('home_team', '?')} ({g.get('home_conf', '?')}) "
            f"vs {g.get('away_team', '?')} ({g.get('away_conf', '?')})"
            + (f" — {loc_str}" if loc_str else "")
        )
    return "\n".join(lines)


def _fmt_memory_block(memory):
    """Conference memory injected as PRIOR RUN NOTES."""
    if not memory:
        return ""
    last_run = memory.get('last_run', 'unknown')
    run_count = memory.get('run_count', 0)
    prior_summary = memory.get('prior_summary', '(none)')
    prior_sentiment = memory.get('prior_sentiment', 'unknown')

    threads = memory.get('storyline_threads', []) or []
    thread_lines = []
    for t in threads:
        updates = t.get("updates", []) or []
        latest = updates[-1].get("note") if updates else t.get("theme", "")
        age_note = ""
        if t.get("first_seen") and t.get("last_updated") and t["first_seen"] != t["last_updated"]:
            age_note = f" (tracking since {t['first_seen']})"
        status_tag = " [STALE — verify if still relevant]" if t.get("status") == "stale" else ""
        thread_lines.append(f"  - {latest}{age_note}{status_tag}")

    storylines_section = ("\n".join(thread_lines)
                          if thread_lines
                          else "  (none yet — this is the first run)")

    flags = memory.get('agent_flags', {}) or {}
    high_conf = flags.get('high_confidence', []) or []
    recheck = (flags.get('low_confidence', []) or []) + (flags.get('watch_for_next_run', []) or [])

    return f"""=== PRIOR RUN NOTES ({last_run} — run #{run_count}) ===
Use these as your starting point. Confirm, update, or contradict based on the current data.
Storylines marked [STALE] may have resolved — check whether the current data sustains them.

Prior overall sentiment: {prior_sentiment}

Prior summary:
  {prior_summary}

Tracked storyline threads:
{storylines_section}

High-confidence from prior run:
  {', '.join(high_conf) if high_conf else '(none recorded)'}

Watch / recheck this run:
{chr(10).join(f"  - {w}" for w in recheck) if recheck else "  (none flagged)"}
=== END PRIOR RUN NOTES ===
"""


def _fmt_national_block(national):
    """National landscape: writeup + storylines + teams_discussed.
    Informational only — never cite directly in essay.
    """
    if not national:
        return "(no national landscape available — running cold)"
    writeup = national.get('writeup', '') or ''
    storylines = national.get('storylines', []) or []
    teams_discussed = national.get('teams_discussed', []) or []
    generated_date = national.get('generated_date', 'unknown')

    lines = [
        f"National landscape generated: {generated_date}",
        "",
        "Recent national writeup (informational — do not cite directly):",
        writeup if writeup else "(none)",
    ]
    if storylines:
        lines.append("")
        lines.append("National storylines (use to calibrate which conf storylines are nationally salient):")
        for s in storylines[:8]:
            head = s.get('headline', '')
            summary = s.get('summary', '')
            if head:
                lines.append(f"  - {head}: {summary}")
    if teams_discussed:
        lines.append("")
        lines.append("Teams nationally discussed: " + ", ".join(teams_discussed))
    return "\n".join(lines)


def _fmt_editor_notes(editor_notes, standings):
    """
    Editor steering notes — the highest-authority block in the prompt.

    Notes come from writer_notes/<season>/<conf>.json (loaded by
    build_conference_context.py and folded into conf_context['editor_notes']).
    They reflect 2026 reality the agent's prior knowledge can't be trusted on:
    coaching changes, key transfers, off-field stories, intentional editorial
    angles. The block instructs the agent to treat them as ground truth and
    to use them as direction (not text — never quoted verbatim).

    Returns "" when there are no notes so we don't render an empty block.
    """
    if not editor_notes:
        return ""
    conf_note  = (editor_notes.get('conference_note') or '').strip()
    team_notes = editor_notes.get('team_notes', {}) or {}
    if not conf_note and not team_notes:
        return ""

    # Build display-name lookup from standings so we render "Alabama" rather
    # than "alabama". Falls back to the slug if standings doesn't have it.
    display_by_slug = {}
    for s in standings:
        slug = s.get('url_param') or s.get('team_slug') or s.get('team', '')
        team = s.get('team') or s.get('display_name') or slug
        if slug:
            display_by_slug[slug] = team

    lines = [
        "## EDITOR NOTES — AUTHORITATIVE STEERING (read first, weight highest)",
        "",
        "These notes come from the editor and reflect current 2026 reality. Treat them as ground",
        "truth. They OVERRIDE any conflicting prior knowledge or context-data inference. They are",
        "DIRECTION, not text — weave them naturally into the narrative; do NOT quote them verbatim.",
        "If a note states a fact (a hire, a transfer, an injury, a story), trust it. If a note",
        "names a tone or angle, let it shape your framing of that team or the conference.",
        "",
    ]
    if conf_note:
        lines.append("Conference-wide steer:")
        lines.append(f"  {conf_note}")
        lines.append("")
    if team_notes:
        lines.append("Per-team steer:")
        # Render in standings order so the agent reads them top-down.
        rendered = set()
        for s in standings:
            slug = s.get('url_param') or s.get('team_slug') or s.get('team', '')
            note = team_notes.get(slug)
            if note:
                disp = display_by_slug.get(slug, slug)
                lines.append(f"  - {disp}: {note}")
                rendered.add(slug)
        # Catch any slugs not represented in standings (defensive).
        for slug, note in team_notes.items():
            if slug not in rendered:
                lines.append(f"  - {slug}: {note}")
    lines.append("== END EDITOR NOTES ==")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build prompt
# ---------------------------------------------------------------------------
def build_prompt(conf_slug, conf_context, conf_memory, national):
    """Assemble the conference preview prompt. Returns (prompt_str, mode)."""
    mode, mode_focus = get_mode()

    conf_display  = conf_context.get('conference_display', conf_slug.upper())
    season        = conf_context.get('season', datetime.now().year)
    standings     = conf_context.get('standings', []) or []
    top_players   = conf_context.get('top_players', []) or []
    top_recruits  = conf_context.get('top_recruits', []) or []
    top_portal    = conf_context.get('top_portal', []) or []
    history       = conf_context.get('history', {}) or {}
    marquee_ooc   = conf_context.get('marquee_ooc', []) or []
    missing_teams = conf_context.get('missing_teams', []) or []
    conf_sched    = conf_context.get('conf_schedule_length', {}) or {}
    editor_notes  = conf_context.get('editor_notes')   # None when no notes file

    # Format blocks
    standings_block     = _fmt_standings(standings)
    players_block       = _fmt_top_players(top_players)
    recruits_block      = _fmt_top_recruits(top_recruits)
    portal_block        = _fmt_top_portal(top_portal)
    history_block       = _fmt_history(history, conf_sched)
    ooc_block           = _fmt_marquee_ooc(marquee_ooc)
    memory_block        = _fmt_memory_block(conf_memory)
    national_block      = _fmt_national_block(national)
    editor_notes_block  = _fmt_editor_notes(editor_notes, standings)

    output_path = str(CONF_PREVIEWS / f"{conf_slug}.json")
    teams_in_order = ", ".join(s.get('url_param', s.get('team', '?')) for s in standings)

    missing_note = ""
    if missing_teams:
        missing_note = (f"\nNOTE: {len(missing_teams)} member team_context file(s) "
                        f"were missing at build time and are not represented in the "
                        f"data above: {', '.join(missing_teams)}. Acknowledge this in "
                        f"meta.agent_notes if it materially affects your essay.\n")

    prompt = f"""You are the conference preview lead for Punt & Rally (puntandrally.com), a CFB analytics site covering all 138 FBS teams.

Your task: Write the {conf_display} {season} conference preview, magazine-style. Output a single JSON file containing a 4–5 paragraph conference essay, a paragraph-length blurb for every team in projected standings order, 3–5 conference-wide key storylines, and the metadata listed in the schema below.

## Research Mode: {mode.upper()}
Current focus: {mode_focus}

{editor_notes_block}

{memory_block}

## Conference Data (your factual base — use this, don't reinvent)

Conference: {conf_display} ({conf_slug})
Season: {season}
Member count: {len(standings)}
Teams in projected order: {teams_in_order}{missing_note}

### Projected Standings
Sorted by powerrating.expectedconfwins DESC, ties broken by overall projected wins.
Each line is one team: rank, team, projected record (conf record), Power rank, Talent rank, SOS rank, Returning Production %, Head Coach (year), QB.

{standings_block}

### Top 15 Players by Production Numbers
Production Numbers are Punt & Rally's own production rating (NOT 247Sports). Stats are 2025 season figures used until 2026 kicks off.

{players_block}

### Top 10 Recruits (2026 class)
Individual recruit ratings ARE 247 ratings (decimal scale).

{recruits_block}

### Top 10 Portal Additions (2026 cycle)
Individual portal ratings ARE 247 ratings (decimal scale).

{portal_block}

### Four-Year Conference Records
Each team's W-L in conference games where BOTH teams' conference at the time matched the current conf. Em-dash means the team wasn't in this conference that season.

{history_block}

### Marquee Non-Conference Matchups ({season})
OOC games (conference_game = N) where one team is in {conf_display} and the opponent is from a P4 conference. Sorted by date.

{ooc_block}

## National Perspective (informational only — do NOT cite directly)
The national CFB pipeline summarizes what national outlets are talking about. Use this to calibrate which of your storylines are nationally salient. NEVER quote, paraphrase, or attribute a claim to a "national source" — the conference preview is your independent synthesis.

{national_block}

"""
    prompt += """## Your Tasks

1. **Conference Essay (4–5 paragraphs, 600–900 words total).**
   - Lead with the dominant {season_word} narrative for this conference: who's in front, what makes the chase interesting, what the central question of the year is.
   - Cover the contender tier, the chase pack, the messy middle, and the rebuild floor — every team should be at least implied somewhere in the essay's geography.
   - Schedule color where it matters (a CFP-defining matchup, a brutal stretch, a soft schedule that flatters a record).
   - Identify a candidate pull-quote from the essay: ONE sentence that captures the central tension or the cleanest joke. Output that sentence verbatim in `pull_quote`.
   - Naming and tone rules below apply to every sentence.

2. **Per-Team Blurbs (one per team, every team in projected standings order).**
   - 60–110 words each, full prose paragraph. Not bullets. Not headlines.
   - Cover: realistic outlook, the player or unit that defines them in {season} (named correctly per the Football Players Only rules), the central concern or question, and what the year hinges on.
   - The blurb subheadline on the page already shows projected record / coach / Power / Talent / SOS — do NOT restate those numbers in the prose. Write around them.
   - Every team in `standings` (above) gets exactly one entry, in the order they appear there.

3. **Key Storylines (3–5 conference-wide items).**
   - Concrete and specific. Bad: "the league has a lot of new coaches." Good: "Lane Kiffin's first year at LSU sets the league's tone for coaching-scrutiny narratives."
   - Maintain continuity with the prior-run threads above where they're still active. Use similar keywords so the memory matcher can thread runs together.
   - **Apply the Football Players Only verification (Rule set below) to every prior thread you intend to update.** If a prior thread names a player you cannot verify by path (a), (b), or (c), do NOT 'resolve' it — leave it unmentioned and let it age out.

"""
    prompt += f"""4. **Output JSON.** Write valid JSON to: {output_path}

Schema (exact structure, no trailing commas, no comments):
{{
  "conference_slug": "{conf_slug}",
  "conference_display": "{conf_display}",
  "season": {season},
  "research_date": "{datetime.now().strftime('%Y-%m-%d')}",
  "mode": "{mode}",
  "tagline": "Short magazine subhead, ≤80 chars. Pithy. The kind of line a section editor would put under the conference name on a magazine cover.",
  "essay": "4–5 paragraphs of conference-wide analysis. Use \\n\\n between paragraphs. Follow ALL rules below.",
  "pull_quote": "One sentence pulled verbatim from `essay` that works as a callout.",
  "team_blurbs": [
    {{
      "slug": "team-slug",
      "team": "Team display name",
      "url_param": "Team URL param (matches /teamprofile?team=...)",
      "blurb": "60–110 word paragraph for this team. Full prose, not bullets."
    }}
  ],
  "key_storylines": [
    "3–5 specific, concrete conference-wide storylines"
  ],
  "agent_summary": "3–4 dense sentences capturing the conference's central question, the team to watch, the team most likely to surprise, and the structural reality that defines the {season} race.",
  "overall_sentiment": "one of: optimistic | cautiously_optimistic | mixed | cautious | concerned",
  "sentiment_score": 0.0,
  "agent_flags": {{
    "high_confidence": ["2-3 facts you confirmed from the structured data — e.g. 'Texas Tech's portal class is the highest-rated in conference history per the data'"],
    "low_confidence": ["1-2 things based on a single signal, hedge language, or gut feel — flag for next run"],
    "watch_for_next_run": ["1-2 active unknowns to recheck — e.g. 'Indiana's QB1 not officially named'"]
  }},
  "meta": {{
    "team_count": {len(standings)},
    "missing_teams": {json.dumps(missing_teams)},
    "agent_notes": "Optional: note if data was sparse, if a particular team's blurb relied on memory more than data, etc."
  }}
}}

"""
    prompt += """## Rules — All Lifted Verbatim From the Team Research Agent

Every rule below is the same as the team-research agent's rules. They were calibrated through extensive trial and error and are not negotiable. The ONLY thing that changes for the conference preview is the tone block at the bottom.

### Coaching staff (disqualifying error if wrong)
Use ONLY the head coach names provided in the standings data above. Never name a former coach under any circumstances — describe coaching changes generically ("after the previous staff's exit") unless the structured data carries the named predecessor. Do NOT label a head coach as "on the hot seat" or question their job security unless that exact framing appears in your prior-run memory threads or in a specific data point that justifies it (e.g., a 5-19 conference record over the prior two seasons).

### Football Players Only (disqualifying error if wrong)
Every player named anywhere in the essay, blurbs, or storylines must be a verified college football player connected to a conference member. Three verification paths exist; pick the one that matches your framing:

  (a) **Roster path** — the player appears in the standings data as that team's `starting_qb_name`, OR in `top_players` as a member of that team. Required for any active-roster framing (starter, returner, position group anchor, injury, depth-chart context).

  (b) **Team data path** — the player appears in `top_recruits` (incoming class) or `top_portal` (incoming transfer). Required for any "new arrival" / "incoming class" framing about a specific team.

  (c) **Source path with explicit football tag** — used only when prior-run memory threads reference a player. The thread must explicitly carry a football position label (WR, DT, LB, QB, edge, safety, etc.) or unambiguous football context. A name + destination + generic "transfer/portal/commit" wording is NOT sufficient — the NCAA portal exists for ~30 sports and "Player X to Duke" with no sport tag could be basketball, baseball, lacrosse, or any other sport. Generic "athlete" / "recruit" / "prospect" language is also insufficient.

**No positional fabrication:** never invent a position-group context (WR rotation, OL depth, secondary) around a player who has not cleared (a), (b), or (c). Even if the name later turns out to be a real football player, wrapping an unverified name in "joins the WR room" framing is itself a disqualifying error.

**Recruiting / portal loss framings — extra scrutiny:** "Portal miss on X," "[team] lost out on X," "X chose [other school] over [this team]" — these are HIGH-RISK because they name a player who by definition is NOT in the team's data. Use them ONLY when there's a concrete prior-thread context with explicit football tagging. Otherwise cut the entire sentence.

**Pre-flight verification (mandatory before returning JSON):** scan every player name in your output one final time. For each name, mentally tag the verification path you used: `[a top_players]`, `[a starting_qb]`, `[b recruits]`, `[b portal]`, or `[c memory thread]`. If you cannot tag a name with a specific path, REMOVE THE NAME from the output. There is zero penalty for omitting a player; there is a disqualifying penalty for naming a non-football player or fabricating positional context around an unverified name.

"""

    prompt += """### Player rules (strictly enforced)

  (1) Name players as leaders or standouts ONLY from `top_players` or the team's `starting_qb_name`. When discussing any team's strength at a position group in their blurb, if a `top_players` entry from that team plays that position, they MUST be named — do not construct a positional narrative that omits the most prominent established player.

  (2) Before placing any player in a positional context (QB battle, RB room, OL depth), verify their position in `top_players` or the recruit/portal blocks. If you can't verify, remove the name.

  (3) **Player provenance — strict (this fixes the Jackson Harris bug).** Each top_players entry is tagged with one of:
        `[NEW – portal]`   = player is in this team's portal_in this offseason. Frame as a portal addition, NEVER as "returning anchor" / "veteran" / "established." Prior-school context is fine; current-school production is not yet established.
        `[NEW – freshman]` = player is in this team's recruiting_class_2026. Frame as an incoming freshman, never as a returning player.
        (no marker)        = returning player from the prior season. Returning-player framing is appropriate.
      A name in top_players WITHOUT a marker is a returner. A name in top_players WITH a marker is a new arrival regardless of how high their Production Numbers rank — the production points were earned at a prior school. Do not write "returns as anchor" or similar for any name carrying a marker.

  (3b) **Portal-out symmetry (strict):** Never claim a player has "transferred out" or "is leaving the program" based on memory threads alone. The conference data shows incoming portal only — claims about outgoing transfers must be cut from the conference preview unless they appear in a prior-run thread WITH explicit football tagging.

  (3c) **Podcast/video TITLES are not sources.** This applies to your prior-run memory threads as well — if a thread originated from a podcast title or video title without corroboration, treat it as suspect and let it age out, rather than 'updating' it.

  (4) The structured data above (standings, top_players, top_recruits, top_portal) is ground truth. If a memory thread contradicts the current data, the data wins.

  (4b) Do not describe a P4-to-P4 transfer as "unproven at this level." That label is reserved for players arriving from G6 programs or below (FBS G6, FCS, D2, JUCO). A transfer from another Power Four school is a peer-level move.

  (5) `top_players` is capped at 15 across the conference. If you reference a specific player not in the structured data (e.g., from a memory thread), you may name them ONLY if they pass path (c). Do NOT name uncapped players as starters or "key contributors" in the conference essay or in a team's blurb.

  (6) **Departed players (graduated, NFL Draft, transferred out) must never be framed as mysteriously absent.** If a memory thread references a player no longer in this season's data, assume the mundane explanation — they graduated, declared for the NFL, or transferred — and either state the explanation plainly or omit them entirely. NEVER write "absent from the roster," "missing from the lineup," or any language implying something unusual about a player no longer present.

  (6b) **Transfer portal window (2026 rule change — critical):** The transfer portal now has a SINGLE window in early January. There is NO post-spring portal window. Between now and the end of the 2026 season, current rostered players CANNOT transfer. Never write that a current player is "expected to enter the portal," "drawing portal interest," "a portal risk," "could transfer," or any similar framing during spring/summer/in-season. Those framings described the old two-window system and are factually wrong for 2026. Portal discussion in spring/summer should cover only: (a) `top_portal` additions already in the structured data, or (b) the upcoming January 2027 window as a future event — NEVER current roster attrition risk.

  (7) **Source fidelity on counts:** when stating a numeric count (transfers, returning starters), never attach a composition or unit claim the data doesn't support. The structured data carries the totals; do not invent breakdowns.

"""

    prompt += """### Schedule rules (strictly enforced)

  - The `marquee_ooc` block above carries the actual upcoming OOC games for this conference's members. Reference them as written — never override location, opponent, or week with your own knowledge.
  - **Season length:** the FBS regular season is 12 games for every team. The only 2026 exception is the PAC-12, whose eight members have 11 scheduled games while they experiment with a flexible final-weekend matchup format (the 12th game is TBD, not missing). NEVER write "13-game schedule," "14-game schedule," or any other number — bowls, conference championship games, and the College Football Playoff are POSTSEASON and are not part of the regular-season game count.
  - "Gauntlet" is reserved for runs of opponents power-ranked roughly #30 or better. A run of even-handed games (spreads ±6, mid-tier opponents) is a "challenging stretch," not a gauntlet.
  - When citing the `history` block, do not extend a streak beyond what the data shows. If a team has no record in a season (em-dash), it means they were not in this conference that year — say so plainly, do not pretend the dash is zero or imply a mystery.

### History block fidelity (strictly enforced)

  - The history table above shows each member's conference record by year **and a pre-computed Sum column** with their total conference W-L over the four years. **Use the Sum column.** Never re-add the records yourself — Claude is bad at multi-row arithmetic and gets it wrong (e.g., reading "3-5, 1-7, 3-5, 0-8" and writing "three combined wins" when the actual sum is 7-25).
  - When you reference a team's recent W-L pattern, list the records explicitly — never paraphrase a pattern that doesn't match the rows. WRONG: "back-to-back 4-4 conference records" when the row shows 6-2 then 4-4. WRONG: "won three conference games combined over the past four seasons" when the Sum column shows 7. RIGHT: read the row and quote it ("Tennessee finished 6-2 in 2024 before sliding to 4-4 in 2025") or quote the Sum column ("Arkansas's 7-25 conference record over the last four seasons").

### Conference schedule length (strictly enforced)

  - The "Conference games per team by year" row above the history table is the ground truth for how many conference games each member played each season. A team CANNOT have more conference wins than the listed games for that year.
  - Pre-2026 SEC was 8 games — never claim "9 conf wins per year over four seasons" or any equivalent for an SEC team prior to 2026. The 2026 season is the SEC's FIRST 9-game conference schedule.
  - The PAC-12 had only 2 members in 2024 and 2025 (Oregon State, Washington State) — historical W-L for current PAC-12 members from prior conferences will mostly show em-dashes for those years; that is correct, do not invent records.
  - Big Ten, Big 12 have been 9-game conference schedules across this window. AAC, Sun Belt, MWC (except 2024 = 7), MAC, CUSA have been 8-game. ACC has been 8-game and remains asymmetric in 2026 (some teams 8, some 9).

### Language rules (strictly enforced)

  - Never use "G5" — always use "G6" to refer to non-Power-Four FBS programs.
  - Do not use conference divisions (SEC East, Big Ten West, etc.) — divisions were eliminated in 2024 and remain eliminated in 2026.
  - Do not use superlatives ("most significant," "largest," "most dominant," "highest-ever") without a concrete data anchor. "The biggest portal class in conference history" requires either a specific point of comparison from the data or a memory thread that established it.
  - **Conference tier (2026):** The Power Four (P4) is SEC, Big Ten, ACC, Big 12, plus Notre Dame (FBS Independent) — 69 teams. Everything else is Group of Six (G6): PAC-12, AAC, Sun Belt, MWC, MAC, CUSA, plus UConn. The PAC-12 is a G6 conference in 2026 — never call it or its members "Power Four" or place them "among the P4 field."
  - **P4 ranking context (P4 teams only):** there are 69 Power-Four teams. Top 17 = elite (top quarter); 18–35 = above average; 36–52 = below average; 53–69 = bottom quarter. A team ranked #38 nationally is "slightly below average" within P4 — calibrate language precisely. Do NOT apply these bands to a G6 team.
  - **G6 ranking context (G6 teams only):** the `power_rank` is an FBS-wide rank across 138 teams. For a G6 team, frame their standing against the full 138 (top third = #1–46, middle = #47–92, bottom = #93–138) AND/OR relative to the ~69-team G6 pool. A G6 team in the FBS top 50 is near the top of G6 — never "below average" against a P4 frame.
  - Never use "dead last," "last place," or "last" to describe a specific rank unless it equals the total number of teams in that pool. In FBS, #138 is last. In P4, #69 is last.
  - **Blue chip ratio is only meaningful for programs competing for the College Football Playoff and national titles.** Do not reference blue-chip percentage for G6 programs — it is near zero for nearly all of them and adds no analytical value.
  - Historical claims (a coach's record against a specific opponent, program milestones, conference standings history) must come from the structured data above (especially `history`) or from a prior-run memory thread — never from training knowledge alone.
  - **Word ban — strict.** Do not use the words "structure," "structural," or "infrastructure" anywhere in your output (essay, blurbs, storylines, summary). These words have been overused in prior writeups and add no analytical value. Replace with specific descriptions of what you mean: name the line ("offensive line," "secondary"), the depth chart, the returning production, the staff continuity, the system, the QB room — be concrete.
  - **Anti-redundancy — essay vs blurbs.** The conference essay establishes the conference-wide picture; each per-team blurb adds team-specific texture the essay didn't cover. Do NOT recycle the same framing for the same team across both — if the essay says "LSU is one Brian Kelly press conference from a CFP appearance or a coaching change," LSU's blurb must say something else (the specific portal pieces, the front-seven question, the schedule sequence, etc.). A reader scrolling through both should learn something new in the blurb.

### Production / Talent / Returning-Production attribution (strictly enforced)

  - "Production Numbers" (the `points` value in `top_players`) and "Talent rank" (in `standings`) are Punt & Rally's own analytical builds. **NEVER attribute them to 247Sports, On3, Rivals, or any external source.** Refer to them by name without provenance: "by Production Numbers, X is the conference's top player," or "the conference's #1 talent ranking sits with Y."
  - Recruit and portal individual ratings (the `rating` field in `top_recruits` and `top_portal`) ARE 247Sports ratings. Display them as `.XX` (two decimals).
  - **Returning production definition (Punt & Rally's measure):** "Returning production" as used on this site INCLUDES returning players AND portal additions — it is a measure of how much production will be on the field for the upcoming season, NOT a snap-back rate or pure-returner percentage. So a team with 84% returning production may have a heavy portal class and still be 84% — the metric counts incoming transfers as production-on-the-field. Frame the percentage accordingly: do not say "X% of last year's roster is back" — the figure does not mean that. "X% returning production" or "X% of expected production returns" is correct.
  - **Returning production source — strict.** The `returning_production` value in standings is Punt & Rally's calculation. NEVER cite Bill Connelly's returning production, ESPN's calculation, or any other source's number. NEVER reference Connelly by name in conjunction with a returning-production figure. The number you display or cite must be the value in the `standings` block, full stop.

### QB experience rule (strictly enforced)

  Each team's `starting_qb_name` plus `qb_back` flag (Y = returning starter, N = new) is the authoritative source on the quarterback's status.

  - If `qb_back: Y`, the player started for THIS team last season. Never describe them as "unproven," "untested," "hasn't proved it," "inherits the program," "takes over," or "takes the reins" — all of those framings imply a new arrival. Use returning-starter framing: "back for another year," "in his second year as starter," or specific stats from the prior season.
  - If `qb_back: N`, the player is a new starter (could be a portal addition, a freshman who beat out the returner, or a backup elevated). Frame accordingly using their actual provenance: portal addition (cross-check `top_portal`), recruit (cross-check `top_recruits`), or returning backup elevated.
  - Concrete example to AVOID: "Gunner Stockton inherits a program that has averaged eight conference wins per year for the past four seasons." Stockton's `qb_back` is Y — he started last fall. Correct framing: "Gunner Stockton, back for his second year as the Georgia starter, takes a roster that's averaged…"

"""

    prompt += """### Storyline continuity + sport audit

If prior-run notes (above) include tracked storyline threads, your `key_storylines` should update those threads where the current data still supports them. Use similar keywords so the memory matcher can thread them across runs. If a tracked storyline has resolved (a coaching battle settled, a portal saga ended), drop it — it'll age out naturally.

**Prior-storyline sport audit (mandatory before treating any thread as football):** prior runs may have written storyline threads that quietly contain a non-football player (a basketball-podcast contamination, a misclassified portal name). For every prior thread you intend to "update" or "resolve," apply Football-Players-Only verification: if the thread names a player, that player must pass path (a), (b), or (c). If they fail all three, DO NOT update or "resolve" the thread — leave it unmentioned so it ages out, and treat the prior thread as suspect rather than ground truth. A "resolution" of a contaminated prior thread propagates the original error.

### Data sanity

  - If a structured value looks implausible (a percentage above 100, a rank of `null`, an obviously stale figure), do not use it — omit it silently. Do not call attention to data oddities.
  - Always use the projected records from `standings` for win projections — never derive or estimate a different number.
  - When the `history` table shows a team's record evolving (improving, declining), reference that pattern by name only if the pattern is genuine — a 5-3, 5-3, 5-3, 5-3 line is not a "trend," it's stability.

"""

    prompt += """### Tone — Phil Steele meets Banner Society

Write with the voice of a smart, slightly weary CFB lifer who has seen every variation of "this is the year" and remains delighted by the sport anyway. Phil Steele's appetite for the specific data point crossed with Banner Society's wry affection for the genre. Be willing to be funny — dry-witty, observational, occasionally biting — but **every joke has to land on a real contradiction, a real tendency, or a specific data point.** No generic snark.

This voice does:
  - Uses specifics as comedic timing. "Texas A&M has now hired coordinators from three NFL franchises and one Wing Stop." (Specific = funny.)
  - Points out real patterns wryly. "Missouri has built an entire identity around being slightly disappointing in November."
  - Earns its critique. If you're calling a coach's seat warm, point to the actual W-L trajectory or the data point that justifies it.
  - Loves the weird: schedule quirks, conference geography comedy, fanbase reflexes — fair game when the joke is concrete.
  - Treats fans seriously even when teasing them. Affectionate, not contemptuous.
  - Uses dry one-liners when the data hands you one. ("Their O-line is held together with duct tape and prayer, but at least the tape is name-brand.")

This voice does NOT do:
  - Hot takes ("they're done," "fire him by October," "year of the [team]").
  - Empty snark or insults without a specific anchor.
  - Generic doom or generic hype.
  - Picking fights between fanbases to please a third one.
  - Punching down on G6 programs as a class. Punch up at the SEC if you must, never down at MAC fans.
  - Reaching for a joke when the data doesn't support it.
  - Personal shots at coaches, players, or AD's outside the scope of on-field results.

When in doubt, lean grounded. A funny line that feels forced is worse than a clean, specific observation. Accuracy beats wit. The goal is an entertaining writeup a smart, informed fan of any team in this conference would nod along with — annoyed at one or two lines but never feeling cheap-shotted.

**Mode-aware calibration:**
  - `spring_offseason` and `preseason`: lean toward earned snark with affection. Spring is the season of optimism for fans, but it's also the season of the most absurd offseason takes — the sweet spot for this voice is poking at hype while taking the underlying optimism seriously where it's warranted. Identify the real reasons for hope (returning production, portal hits, staff continuity) and present them. Then, where appropriate, deflate the hype with a specific reality check.
  - `early_offseason`: reflective with edge — what worked, what didn't, who's already lying about themselves.
  - `in_season` / `postseason`: pragmatic with the scoreboard. The jokes get tighter because the data does the work.

### Writing the per-team blurbs specifically

Each blurb is a paragraph (60–110 words). Don't just compress the conference essay's view of the team — the blurb is its own thing. Give the reader the team's central reality in three or four beats: what they have, what they're trying to do with it, what could go wrong, and the headline question. The voice should be the same as the essay (Phil Steele meets Banner Society), but blurbs lean slightly more analytical — fewer punchlines per word, more concrete texture.

"""

    prompt += """### Output instructions

  - Write the JSON file as soon as you have enough — do not wait for perfection. No trailing commas, no comments inside the JSON, no markdown fences.
  - **No URL fetching.** All source content for the conference preview is the structured data and the prior memory above. Do NOT use fetch_url, web_search, or any tool to retrieve outside content. The national landscape was already synthesized by a separate pipeline; quoting from it directly is forbidden by the National Perspective rule above.
  - **agent_flags:** Fill these in honestly after completing the rest of the JSON.
    - `high_confidence`: Facts you confirmed from the structured data plainly. Be specific.
    - `low_confidence`: Things based on a single signal (one team's QB situation, a memory thread you're unsure about). Flag for next run.
    - `watch_for_next_run`: Active unknowns — unsettled QB rooms, portal additions whose enrollment is unconfirmed, coaching-staff drama not yet resolved. Max 2 items.
  - **sentiment_score:** 0.0 = extremely negative · 0.5 = neutral · 1.0 = extremely positive. Conferences with a clear contender + chase pack and minimal scandal lean 0.6+; conferences with multiple coaching-seat dramas lean 0.4 or below.
  - The `team_blurbs` array MUST contain exactly one entry per team in `standings`, in the same order. Do not skip teams. If a team's data is thin (a `missing_teams` entry), say so plainly in their blurb and keep it short — don't fabricate.
"""

    return prompt, mode

# ---------------------------------------------------------------------------
# Merge the deterministic data layer into the agent's prose output so the
# public URL serves a single self-contained JSON. Called after a successful
# Claude run; never overwrites Claude's contributions, only adds data fields.
# ---------------------------------------------------------------------------
DATA_FIELDS_TO_MERGE = (
    'standings', 'top_players', 'top_recruits', 'top_portal',
    'history', 'marquee_ooc',
)


def merge_context_into_preview(conf_slug):
    """Copy deterministic data from conference_context/<slug>.json into
    conference_previews/<slug>.json. Returns True on success."""
    preview_path = CONF_PREVIEWS / f"{conf_slug}.json"
    context_path = CONF_CONTEXT / f"{conf_slug}.json"

    if not preview_path.exists() or not context_path.exists():
        logging.error(f"  [{conf_slug}] merge: missing input "
                      f"(preview={preview_path.exists()}, context={context_path.exists()})")
        return False

    try:
        preview = json.loads(preview_path.read_text())
        context = json.loads(context_path.read_text())
    except Exception as e:
        logging.error(f"  [{conf_slug}] merge: parse error: {e}")
        return False

    for field in DATA_FIELDS_TO_MERGE:
        if field in context:
            preview[field] = context[field]

    # Surface the conference_context build timestamp so the page can show
    # data freshness independently of when the agent ran.
    if 'built_at' in context:
        preview.setdefault('meta', {})['context_built_at'] = context['built_at']

    # Carry forward missing_teams (the agent already saw it via the prompt
    # and may have echoed it into meta, but the canonical list is here).
    if 'missing_teams' in context:
        preview.setdefault('meta', {})['missing_teams'] = context['missing_teams']

    preview_path.write_text(json.dumps(preview, indent=2, ensure_ascii=False, default=str))
    logging.info(f"  [{conf_slug}] merge: ✔ merged "
                 f"{len([f for f in DATA_FIELDS_TO_MERGE if f in context])} data fields")
    return True


# ---------------------------------------------------------------------------
# Run Claude agent (mirrors national_landscape_agent.py pattern)
# ---------------------------------------------------------------------------
def run_agent(conf_slug, prompt, dry_run=False):
    """Spawn a Claude Code session with the conference prompt. Validate JSON output.
    Writes the rendered prompt to logs/prompt_conference_<slug>.txt in both
    dry-run and live modes — that file is the canonical review artifact."""
    # Strip null bytes (safety — same as national_landscape_agent.py)
    prompt = prompt.replace('\x00', '')

    # Persist the rendered prompt for review/debugging — always, not just live runs
    LOG_DIR.mkdir(exist_ok=True)
    prompt_file = LOG_DIR / f"prompt_conference_{conf_slug}.txt"
    prompt_file.write_text(prompt)

    if dry_run:
        print(f"\n{'='*60}", flush=True)
        print(f"DRY RUN — conference: {conf_slug}", flush=True)
        print(f"Prompt length: {len(prompt)} chars", flush=True)
        print(f"Full prompt written to: {prompt_file}", flush=True)
        print(f"{'='*60}\n", flush=True)
        # Print first 1200 + last 600 chars so the structure is visible inline too
        if len(prompt) > 1800:
            print(prompt[:1200])
            print("\n...[middle elided — see file above for full prompt]...\n")
            print(prompt[-600:])
        else:
            print(prompt)
        return True

    cmd = [CLAUDE_BIN, "--dangerously-skip-permissions", "-p", prompt]

    logging.info(f"  [{conf_slug}] Running Claude (prompt: {len(prompt)} chars)")
    start = time.time()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=AGENT_TIMEOUT_SECS,
            cwd=str(BASE_DIR),
        )
        elapsed = round(time.time() - start, 1)

        if result.returncode == 0:
            output_file = CONF_PREVIEWS / f"{conf_slug}.json"
            if output_file.exists():
                try:
                    data = json.loads(output_file.read_text())
                    blurb_count = len(data.get('team_blurbs', []) or [])
                    storyline_count = len(data.get('key_storylines', []) or [])
                    logging.info(
                        f"  [{conf_slug}] ✔ valid JSON ({elapsed}s) — "
                        f"{blurb_count} blurbs, {storyline_count} storylines"
                    )
                    # Merge in the deterministic data layer so the public URL
                    # is a single source of truth (PHP only fetches one file).
                    if not merge_context_into_preview(conf_slug):
                        logging.warning(f"  [{conf_slug}] ⚠ merge step failed — "
                                        f"preview JSON has prose only, no data layer")
                    return True
                except json.JSONDecodeError as e:
                    logging.error(f"  [{conf_slug}] ✗ invalid JSON: {e}")
                    return False
            else:
                logging.warning(f"  [{conf_slug}] ✗ agent ran but no output file ({elapsed}s)")
                if result.stdout:
                    logging.debug(f"  stdout tail: {result.stdout[-500:]}")
                return False
        else:
            logging.error(f"  [{conf_slug}] ✗ exit {result.returncode} ({elapsed}s)")
            if result.stderr:
                logging.error(f"  stderr: {result.stderr[:400]}")
            if result.stdout:
                logging.error(f"  stdout tail: {result.stdout[-400:]}")
            return False

    except subprocess.TimeoutExpired:
        logging.error(f"  [{conf_slug}] ✗ timed out after {AGENT_TIMEOUT_SECS}s")
        return False
    except Exception as e:
        logging.error(f"  [{conf_slug}] ✗ unexpected error: {e}")
        return False


# ---------------------------------------------------------------------------
# Per-conference orchestration
# ---------------------------------------------------------------------------
def run_conference(conf_slug, dry_run=False, debug=False):
    """Load all inputs, build prompt, run agent. Returns True on success."""
    logging.info(f"\n[{conf_slug}] starting")

    try:
        conf_context = load_conf_context(conf_slug)
    except RuntimeError as e:
        logging.error(f"  [{conf_slug}] ✗ {e}")
        return False

    conf_memory = load_conf_memory(conf_slug)
    national    = load_national_landscape()

    if debug:
        logging.info(f"  conf_context: {len(conf_context.get('standings', []))} teams, "
                     f"{len(conf_context.get('top_players', []))} top_players, "
                     f"{len(conf_context.get('marquee_ooc', []))} marquee_ooc")
        logging.info(f"  conf_memory: {'loaded' if conf_memory else 'first run / empty'}")
        logging.info(f"  national_landscape: {'loaded' if national else 'unavailable'}")

    prompt, mode = build_prompt(conf_slug, conf_context, conf_memory, national)
    logging.info(f"  [{conf_slug}] mode={mode} | prompt={len(prompt)} chars")

    CONF_PREVIEWS.mkdir(exist_ok=True)
    return run_agent(conf_slug, prompt, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description='Spawn Claude to write a conference preview from conference_context JSON.'
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--conf',       default=None, dest='conf',
                        help='Conference slug (sec, big10, acc, ...)')
    target.add_argument('--conference', default=None, dest='conf',
                        help='Alias for --conf')
    target.add_argument('--all',        action='store_true',
                        help='Run all 11 conferences')
    parser.add_argument('--dry-run',    action='store_true',
                        help='Print the assembled prompt without running Claude')
    parser.add_argument('--debug',      action='store_true',
                        help='Verbose logging')
    args = parser.parse_args()

    log_file = setup_logging()
    logging.info(f"Conference preview agent — log: {log_file}")

    confs = list(CONFERENCE_TEAMS.keys()) if args.all else [args.conf.lower()]

    success = failed = 0
    for conf in confs:
        try:
            if run_conference(conf, dry_run=args.dry_run, debug=args.debug):
                success += 1
            else:
                failed += 1
        except Exception as e:
            logging.error(f"  [{conf}] ✗ unhandled: {e}")
            if args.debug:
                import traceback; traceback.print_exc()
            failed += 1

    logging.info(f"\nDone — {success} succeeded, {failed} failed")
    if failed and not args.dry_run:
        sys.exit(1)


if __name__ == '__main__':
    main()
