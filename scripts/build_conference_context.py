#!/usr/bin/env python3
"""
build_conference_context.py
---------------------------
Aggregates team_context/<slug>.json files for a conference into a single
conf-level context dict, then layers in DB queries against the games table
(4-year conference standings history + marquee P4 OOC matchups for the
upcoming season). Output is the deterministic data layer consumed by
conference_research_agent.py.

This script does no Claude work and runs no synthesis. It is purely the
aggregator step. If a team is missing its team_context file, that team is
skipped with a warning rather than aborting the run -- the agent will see
which teams were missing in the meta block.

Usage:
    python3 scripts/build_conference_context.py --conf sec
    python3 scripts/build_conference_context.py --conference big10
    python3 scripts/build_conference_context.py --all
    python3 scripts/build_conference_context.py --conf sec --debug

Output: /cfb-research/conference_context/<slug>.json
"""

import json, os, sys, argparse
from datetime import datetime
from pathlib import Path

import pymysql
from dotenv import load_dotenv

BASE_DIR        = Path("/cfb-research")
TEAM_CONTEXT    = BASE_DIR / "team_context"
CONF_CONTEXT    = BASE_DIR / "conference_context"
WRITER_NOTES    = BASE_DIR / "writer_notes"   # editor steering notes (per season/conf)
SEASON          = 2026   # current season
PAST_YEARS      = 4      # standings-history window (per spec: 4 years)

# Load .env from project root
_here = os.path.dirname(os.path.abspath(__file__))
_env  = os.path.join(_here, '..', '.env') if os.path.basename(_here) == 'scripts' else os.path.join(_here, '.env')
load_dotenv(_env)

DB_CONFIG = {
    'host':            os.environ.get('DB_HOST', ''),
    'user':            os.environ.get('DB_USER', ''),
    'password':        os.environ.get('DB_PASSWORD', ''),
    'database':        os.environ.get('DB_NAME', ''),
    'connect_timeout': 10,
}

# Import the canonical conference -> team mapping. Single source of truth.
sys.path.insert(0, str(BASE_DIR / "scripts"))
from build_team_context import CONFERENCE_TEAMS  # noqa: E402

# Display name shown in the magazine hero. Edit here if marketing strings change.
CONF_DISPLAY = {
    "sec":    "SEC",
    "big10":  "Big Ten",
    "acc":    "ACC",
    "big12":  "Big 12",
    "pac12":  "Pac-12",
    "fbsind": "FBS Independents",
    "aac":    "American Athletic",
    "sbc":    "Sun Belt",
    "mwc":    "Mountain West",
    "mac":    "Mid-American",
    "cusa":   "Conference USA",
}

# Conference name as it appears in games.home_conference / away_conference.
# These are the CFBD-style names. Verify with:
#   SELECT DISTINCT home_conference FROM games WHERE season=2025;
# and adjust if the live data uses different strings (e.g., "Big 10" vs "Big Ten").
CONF_GAMES_NAME = {
    "sec":    "SEC",
    "big10":  "Big Ten",
    "acc":    "ACC",
    "big12":  "Big 12",
    "pac12":  "Pac-12",
    "fbsind": "FBS Independents",
    "aac":    "American Athletic",
    "sbc":    "Sun Belt",
    "mwc":    "Mountain West",
    "mac":    "Mid-American",
    "cusa":   "Conference USA",
}

# Power Four set, per project terminology (P4 = SEC, B1G, ACC, B12, ND).
# UConn games will leak in via "FBS Independents" but they're rare and
# rarely marquee, so we accept the false positive for v1.
P4_GAMES = {"SEC", "Big Ten", "ACC", "Big 12", "FBS Independents"}


# ---------------------------------------------------------------------------
# Conference schedule length — games per team per season.
# This is the canonical reference. Use it to bound conference W-L claims
# (a team CANNOT have more conf wins than games played that year). FBSIND
# has no conference games. Updated 2026-05-02 from Jonathan's confirmation.
# ---------------------------------------------------------------------------
CONF_SCHEDULE_LENGTH = {
    "sec":    {2022: 8, 2023: 8, 2024: 8, 2025: 8, 2026: 9},
    "big10":  {2022: 9, 2023: 9, 2024: 9, 2025: 9, 2026: 9},
    "acc":    {2022: 8, 2023: 8, 2024: 8, 2025: 8, 2026: "8 or 9"},  # 17 teams, asymmetric
    "big12":  {2022: 9, 2023: 9, 2024: 9, 2025: 9, 2026: 9},
    "pac12":  {2022: 9, 2023: 9, 2024: 1, 2025: 2, 2026: 8},  # realignment crater
    "fbsind": {2022: 0, 2023: 0, 2024: 0, 2025: 0, 2026: 0},
    "aac":    {2022: 8, 2023: 8, 2024: 8, 2025: 8, 2026: 8},
    "sbc":    {2022: 8, 2023: 8, 2024: 8, 2025: 8, 2026: 8},
    "mwc":    {2022: 8, 2023: 8, 2024: 7, 2025: 8, 2026: 8},  # 2024 anomaly: 7 games
    "mac":    {2022: 8, 2023: 8, 2024: 8, 2025: 8, 2026: 8},
    "cusa":   {2022: 8, 2023: 8, 2024: 8, 2025: 8, 2026: 8},
}

# Boolean-ish values seen historically in games.conference_game / neutral_site.
TRUEISH  = ('Y', 'y', '1', 'true', 'TRUE', 'True', 't')
FALSEISH = ('N', 'n', '0', 'false', 'FALSE', 'False', 'f')


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_conn():
    return pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)


def query_all(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Team-context loader
# ---------------------------------------------------------------------------

def load_team_context(slug):
    """Read team_context/<slug>.json. Returns None if missing / unparseable."""
    path = TEAM_CONTEXT / f"{slug}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:
        print(f"  [WARN] could not parse {path}: {e}", flush=True)
        return None


# ---------------------------------------------------------------------------
# Editor steering notes (writer_notes/<season>/<conf>.json)
# ---------------------------------------------------------------------------

def load_writer_notes(conf_slug, season=SEASON):
    """
    Load editor steering notes for {season}/{conf_slug}.json.

    Schema (see writer_notes/2026/sec.json for the canonical example):
      {
        "season": 2026,
        "conference": "sec",
        "conference_note": "1-3 sentences about the conference as a whole",
        "team_notes": {
          "<team-slug>": "1-3 sentences about that team",
          ...
        }
      }

    These notes are AUTHORITATIVE: the prompt block built around them is
    instructed to override any conflicting prior knowledge or context-data
    inference. The loader strips empty/whitespace-only strings so the
    downstream formatter can short-circuit on an entirely-empty file.

    Returns the cleaned dict, or None when:
      - the file doesn't exist
      - the file is malformed
      - all notes are empty after trimming
    """
    path = WRITER_NOTES / str(season) / f"{conf_slug}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        print(f"  [WARN] writer_notes parse failed for {path}: {e}", flush=True)
        return None
    conf_note = (data.get('conference_note') or '').strip()
    raw_team  = data.get('team_notes', {}) or {}
    team_notes = {
        slug: note.strip()
        for slug, note in raw_team.items()
        if isinstance(note, str) and note.strip()
    }
    if not conf_note and not team_notes:
        return None
    return {
        'season':          data.get('season', season),
        'conference':      data.get('conference', conf_slug),
        'conference_note': conf_note,
        'team_notes':      team_notes,
        'source_path':     str(path),
    }


def parse_record(s):
    """'8-1' -> (8, 1). Returns (0, 0) on garbage."""
    if not s or '-' not in str(s):
        return (0, 0)
    try:
        w, l = str(s).split('-', 1)
        return (int(w), int(l))
    except Exception:
        return (0, 0)


# ---------------------------------------------------------------------------
# Standings — sort all member contexts into projected order
# ---------------------------------------------------------------------------

def build_standings(team_contexts):
    """
    Order:
        1. expected_conf_record wins DESC
        2. expected_conf_record losses ASC
        3. projected_record wins DESC (overall) as tiebreak
        4. projected_record losses ASC
    Returns list of headline dicts in that order.
    """
    def sort_key(ctx):
        cw, cl = parse_record(ctx.get('expected_conf_record', ''))
        ow, ol = parse_record(ctx.get('projected_record', ''))
        return (-cw, cl, -ow, ol)

    return [
        {
            'team':                  c.get('display_name') or c.get('team') or c.get('url_param', ''),
            'url_param':             c.get('url_param', ''),
            'slug':                  c.get('slug', ''),
            'expected_conf_record':  c.get('expected_conf_record', ''),
            'projected_record':      c.get('projected_record', ''),
            'returning_production':  c.get('returning_production_pct'),
            'power_rank':            c.get('power_rank'),
            'talent_rank':           c.get('talent_rank'),
            'sos_rank':              c.get('sos_rank'),
            'blue_chip_pct':         c.get('blue_chip_pct'),
            'head_coach':            c.get('head_coach', ''),
            'coach_years':           c.get('coach_years', ''),
            'vegas_win_total':       c.get('vegas_win_total', ''),
            'starting_qb_name':      c.get('starting_qb_name', ''),
            'qb_back':               c.get('qb_back', ''),
        }
        for c in sorted(team_contexts, key=sort_key)
    ]


# ---------------------------------------------------------------------------
# Leaderboards — aggregate across all member team_context arrays
# ---------------------------------------------------------------------------

def _team_id_fields(ctx):
    """Pull the (display, url_param, slug) trio off a context, with fallbacks."""
    return (
        ctx.get('display_name') or ctx.get('team') or ctx.get('url_param', ''),
        ctx.get('url_param', ''),
        ctx.get('slug', ''),
    )


def build_top_players(team_contexts, limit=15):
    """
    Top-N players by Production Numbers (P&R's own production rating, stored
    in roster_best_players.points and surfaced via team_context.best_players[]).
    NEVER attribute to 247 — these are P&R-built numbers.

    Each entry is also cross-referenced against the same team's portal_in[] and
    recruiting_class_2026[] to set is_portal_in / is_recruit flags. The
    research-agent prompt uses these to avoid framing portal additions as
    "returning anchors" (the Jackson Harris bug).
    """
    def _norm(s):
        return ''.join(ch for ch in (s or '').lower() if ch.isalnum())

    pool = []
    for c in team_contexts:
        team, url_param, slug = _team_id_fields(c)
        portal_names  = {_norm(p.get('name', '')) for p in (c.get('portal_in') or [])}
        recruit_names = {_norm(r.get('name', '')) for r in (c.get('recruiting_class_2026') or [])}

        for p in c.get('best_players', []) or []:
            name = p.get('player_name', '')
            if not name:
                continue
            n = _norm(name)
            is_portal_in = n in portal_names if n else False
            is_recruit   = (n in recruit_names) if (n and not is_portal_in) else False
            pool.append({
                'player_name':    name,
                'position':       p.get('position', ''),
                'points':         p.get('points', 0),
                'statsline':      p.get('statsline', ''),
                'team':           team,
                'team_url_param': url_param,
                'team_slug':      slug,
                'is_portal_in':   is_portal_in,
                'is_recruit':     is_recruit,
            })
    pool.sort(key=lambda x: x.get('points', 0) or 0, reverse=True)
    return pool[:limit]


# ---------------------------------------------------------------------------
# Recruit / portal rating → natural-language label
# ---------------------------------------------------------------------------

def _rating_to_label(rating, stars=None):
    """
    Translate a 247-style rating into natural-language framing the agent
    can drop straight into prose. Stars take precedence when present;
    rating bands are the fallback. Returns "" when neither is informative.

    Why this lives in the data layer: the agent should never write '.98' in
    prose — readers don't decode 247 decimals. Doing the translation once,
    deterministically, eliminates a class of tone bugs without another rule.
    """
    try:
        s = int(stars) if stars is not None else None
    except (TypeError, ValueError):
        s = None
    try:
        r = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        r = None

    # Prefer stars when present
    if s == 5:
        return "five-star"
    if s == 4 and r is not None and r >= 0.94:
        return "high-end four-star"
    if s == 4:
        return "four-star"
    if s == 3:
        return "three-star"
    if s == 2:
        return "two-star"
    # Fallback to rating bands when stars are missing/zero
    if r is not None:
        if r >= 0.98:
            return "five-star"
        if r >= 0.94:
            return "high-end four-star"
        if r >= 0.89:
            return "four-star"
        if r >= 0.79:
            return "three-star"
    return ""


def build_top_recruits(team_contexts, limit=10):
    """
    Top-N recruits in the conference, sorted by 247 rating DESC then stars DESC.
    Source: team_context.recruiting_class_2026[] (built from players_recruiting).
    Individual recruit ratings ARE 247 numbers — display as .XX (2 decimals).
    """
    pool = []
    for c in team_contexts:
        team, url_param, slug = _team_id_fields(c)
        for r in c.get('recruiting_class_2026', []) or []:
            if not r.get('name'):
                continue
            pool.append({
                'name':           r.get('name', ''),
                'position':       r.get('position', ''),
                'stars':          r.get('stars'),
                'rating':         r.get('rating'),
                'rating_label':   _rating_to_label(r.get('rating'), r.get('stars')),
                'height_weight':  r.get('height_weight', ''),
                'location':       r.get('location', ''),
                'high_school':    r.get('high_school', ''),
                'team':           team,
                'team_url_param': url_param,
                'team_slug':      slug,
            })
    pool.sort(
        key=lambda x: (x.get('rating') or 0, x.get('stars') or 0),
        reverse=True,
    )
    return pool[:limit]


def build_top_portal(team_contexts, limit=10):
    """
    Top-N portal additions in the conference, sorted by 247 rating DESC.
    Source: team_context.portal_in[] (built from players_portal).
    Individual portal ratings ARE 247 numbers — display as .XX (2 decimals).
    """
    pool = []
    for c in team_contexts:
        team, url_param, slug = _team_id_fields(c)
        for p in c.get('portal_in', []) or []:
            if not p.get('name'):
                continue
            pool.append({
                'name':           p.get('name', ''),
                'position':       p.get('position', ''),
                'stars':          p.get('stars'),
                'rating':         p.get('rating'),
                'rating_label':   _rating_to_label(p.get('rating'), p.get('stars')),
                'origin':         p.get('school', ''),    # 'school' = origin in portal_in dedupe
                'eligibility':    p.get('eligibility', ''),
                'transfer_date':  p.get('transferDate', ''),
                'team':           team,
                'team_url_param': url_param,
                'team_slug':      slug,
            })
    pool.sort(key=lambda x: x.get('rating') or 0, reverse=True)
    return pool[:limit]


# ---------------------------------------------------------------------------
# Games-table queries
# ---------------------------------------------------------------------------

def _bool_in(col, truthy):
    """SQL fragment: `col IN (%s, %s, ...)`. Returns (clause, params)."""
    placeholders = ",".join(["%s"] * len(truthy))
    return f"{col} IN ({placeholders})", list(truthy)


def build_history(conn, conf_games_name, member_url_params,
                  current_season=SEASON, years=PAST_YEARS):
    """
    For each *current* member of the conference, return their conference
    W-L for each of the past `years` seasons. Only counts games where
    BOTH teams' conferences at the time were the conference of interest —
    so Texas pre-2024 shows '—' under SEC, not their Big 12 record.
    """
    end_season   = current_season - 1
    start_season = end_season - years + 1
    cg_clause, cg_params = _bool_in("conference_game", TRUEISH)

    sql = f"""
        SELECT home_team AS team, season,
               CASE WHEN home_points > away_points THEN 1 ELSE 0 END AS won
        FROM games
        WHERE home_conference = %s AND away_conference = %s
          AND {cg_clause}
          AND season BETWEEN %s AND %s
          AND home_points IS NOT NULL AND away_points IS NOT NULL
        UNION ALL
        SELECT away_team AS team, season,
               CASE WHEN away_points > home_points THEN 1 ELSE 0 END AS won
        FROM games
        WHERE home_conference = %s AND away_conference = %s
          AND {cg_clause}
          AND season BETWEEN %s AND %s
          AND home_points IS NOT NULL AND away_points IS NOT NULL
    """
    params = (
        conf_games_name, conf_games_name, *cg_params, start_season, end_season,
        conf_games_name, conf_games_name, *cg_params, start_season, end_season,
    )
    rows = query_all(conn, sql, params)

    # Aggregate (team, season) -> [wins, losses]
    agg = {}
    for r in rows:
        key = (r['team'], r['season'])
        if key not in agg:
            agg[key] = [0, 0]
        agg[key][0 if r['won'] else 1] += 1

    history_rows = []
    for url_param in member_url_params:
        seasons = {}
        total_w = 0
        total_l = 0
        for yr in range(start_season, end_season + 1):
            wl = agg.get((url_param, yr))
            if wl:
                seasons[str(yr)] = f"{wl[0]}-{wl[1]}"
                total_w += wl[0]
                total_l += wl[1]
            else:
                seasons[str(yr)] = "—"
        history_rows.append({
            'url_param':       url_param,
            'seasons':         seasons,
            'total_conf_wins': total_w,    # sum across listed seasons (em-dashes excluded)
            'total_conf_losses': total_l,
        })

    # Explicit window label so the agent never compares this multi-year stat
    # against a different-window stat without flagging the difference.
    window_label = (f"{start_season}–{end_season}"
                    if end_season > start_season else f"{start_season}")

    return {
        'years':         list(range(start_season, end_season + 1)),
        'window_label':  window_label,
        'window_years':  end_season - start_season + 1,
        'records':       history_rows,
    }


def build_marquee_ooc(conn, conf_games_name, season=SEASON, max_games=10):
    """
    Marquee non-conference matchups for the upcoming season:
    OOC games (conference_game = N) where one team is in our conf and the
    opponent is from a P4 conference. Sorted by start_date ASC.

    P4 = SEC, Big Ten, ACC, Big 12, FBS Independents (Notre Dame).
    UConn games will leak in via FBS Independents — accepted v1 noise.
    """
    cg_clause, cg_params = _bool_in("conference_game", FALSEISH)
    p4_placeholders = ",".join(["%s"] * len(P4_GAMES))

    sql = f"""
        SELECT id, season, week, start_date, neutral_site, venue, outlet,
               mediaType, home_team, home_conference, home_points,
               away_team, away_conference, away_points
        FROM games
        WHERE season = %s
          AND {cg_clause}
          AND (
                (home_conference = %s AND away_conference IN ({p4_placeholders}))
             OR (away_conference = %s AND home_conference IN ({p4_placeholders}))
          )
        ORDER BY start_date ASC
    """
    params = (
        season, *cg_params,
        conf_games_name, *P4_GAMES,
        conf_games_name, *P4_GAMES,
    )
    rows = query_all(conn, sql, params)

    return [{
        'game_id':    r.get('id'),
        'season':     r.get('season'),
        'week':       r.get('week'),
        'start_date': r.get('start_date', ''),
        'neutral':    str(r.get('neutral_site', '')).lower() in ('true', 't', '1', 'y', 'yes'),
        'venue':      r.get('venue', ''),
        'outlet':     r.get('outlet', ''),
        'mediaType':  r.get('mediaType', ''),
        'home_team':  r.get('home_team', ''),
        'home_conf':  r.get('home_conference', ''),
        'away_team':  r.get('away_team', ''),
        'away_conf':  r.get('away_conference', ''),
    } for r in rows[:max_games]]


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build(conf_slug, debug=False):
    if conf_slug not in CONFERENCE_TEAMS:
        raise ValueError(f"Unknown conference slug: '{conf_slug}'. "
                         f"Known: {sorted(CONFERENCE_TEAMS.keys())}")

    members = CONFERENCE_TEAMS[conf_slug]
    if isinstance(members[0], (list, tuple)):
        member_tuples = list(members)             # (display, url_param, slug)
    else:
        # Defensive: in case CONFERENCE_TEAMS is ever flattened to slug-only
        member_tuples = [(s, s, s) for s in members]

    member_url_params = [m[1] for m in member_tuples]

    if debug:
        print(f"  [{conf_slug}] {len(member_tuples)} members", flush=True)

    # Load all member team_context JSONs (skip-with-warn on missing/bad)
    contexts = []
    missing  = []
    for display, url_param, slug in member_tuples:
        ctx = load_team_context(slug)
        if ctx is None:
            missing.append(slug)
            continue
        # Inject canonical id fields so aggregators don't have to guess
        ctx.setdefault('display_name', display)
        ctx.setdefault('url_param',    url_param)
        ctx.setdefault('slug',         slug)
        contexts.append(ctx)

    if missing:
        print(f"  [WARN] {len(missing)} team_context files missing: "
              f"{', '.join(missing)}", flush=True)

    if not contexts:
        raise RuntimeError(
            f"No team_context files found for {conf_slug}. "
            f"Run `build_team_context.py --conf {conf_slug}` first."
        )

    # JSON-side aggregations (no DB)
    standings    = build_standings(contexts)
    top_players  = build_top_players(contexts, limit=15)
    top_recruits = build_top_recruits(contexts, limit=10)
    top_portal   = build_top_portal(contexts, limit=10)

    # DB-side aggregations
    conn = get_conn()
    try:
        conf_games_name = CONF_GAMES_NAME.get(conf_slug, conf_slug.upper())
        history     = build_history(conn, conf_games_name, member_url_params)
        marquee_ooc = build_marquee_ooc(conn, conf_games_name)
    finally:
        conn.close()

    # Editor steering notes — authoritative; overrides agent prior knowledge.
    # None when no writer_notes file or all notes are empty.
    editor_notes = load_writer_notes(conf_slug)
    if debug:
        if editor_notes:
            note_count = (1 if editor_notes['conference_note'] else 0) + len(editor_notes['team_notes'])
            print(f"    editor_notes: loaded {note_count} note(s) "
                  f"from {editor_notes['source_path']}", flush=True)
        else:
            print(f"    editor_notes: (none — file missing or all empty)", flush=True)

    out = {
        'conference_slug':       conf_slug,
        'conference_display':    CONF_DISPLAY.get(conf_slug, conf_slug.upper()),
        'season':                SEASON,
        'built_at':              datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'team_count':            len(contexts),
        'missing_teams':         missing,
        'conf_schedule_length':  CONF_SCHEDULE_LENGTH.get(conf_slug, {}),
        'standings':             standings,
        'top_players':           top_players,
        'top_recruits':          top_recruits,
        'top_portal':            top_portal,
        'history':               history,
        'marquee_ooc':           marquee_ooc,
        'editor_notes':          editor_notes,
    }

    CONF_CONTEXT.mkdir(exist_ok=True)
    out_path = CONF_CONTEXT / f"{conf_slug}.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str))

    if debug:
        print(f"    standings top 3: "
              f"{', '.join(s['team'] for s in standings[:3])}", flush=True)
        print(f"    top_players: {len(top_players)} | "
              f"top_recruits: {len(top_recruits)} | "
              f"top_portal: {len(top_portal)}", flush=True)
        print(f"    history years: {history['years']} | "
              f"marquee_ooc: {len(marquee_ooc)} games", flush=True)
    print(f"  ✔ wrote {out_path}", flush=True)
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Aggregate team_context JSONs into conf-level context.'
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--conf',       default=None, dest='conf',
                        help='Conference slug (sec, big10, acc, ...)')
    target.add_argument('--conference', default=None, dest='conf',
                        help='Alias for --conf')
    target.add_argument('--all',        action='store_true',
                        help='Build all 11 conferences')
    parser.add_argument('--debug',      action='store_true',
                        help='Verbose per-section diagnostics')
    args = parser.parse_args()

    confs = list(CONFERENCE_TEAMS.keys()) if args.all else [args.conf.lower()]

    success = failed = 0
    for conf in confs:
        print(f"\n[{conf}]", flush=True)
        try:
            build(conf, debug=args.debug)
            success += 1
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)
            if args.debug:
                import traceback; traceback.print_exc()
            failed += 1

    print(f"\nDone — {success} succeeded, {failed} failed", flush=True)


if __name__ == '__main__':
    main()
