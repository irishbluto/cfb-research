#!/usr/bin/env python3
"""
enrich_from_db.py
-----------------
Enriches team context JSON files with stats pulled directly from the
puntandrally database. Fills in fields that are unreliable to scrape
(Overview tab) and adds DB-sourced stats that aren't on any page.

Run after scrape_team_context.py:
    python3 scripts/enrich_from_db.py                  # all teams in context dir
    python3 scripts/enrich_from_db.py --team alabama    # single team slug

DB fields added to each context file:
    offense_power_rank, defense_power_rank
    offense_rating, defense_rating, power_rating_value
    off_momentum_control_rank, def_momentum_control_rank
    off_game_control_rank, def_game_control_rank
    offense_ppa_rank, defense_ppa_rank
    offense_success_rank, defense_success_rank
    offense_ppd_rank, defense_ppd_rank   (from advancedstats)
    scoring_home_ppg, scoring_road_ppg   (from seasonstats / games)
    offense_profile                      (from advancedstats pass/rush ratio)
"""

import json, os, sys, argparse, re
import pymysql
from dotenv import load_dotenv

# Load .env from project root (works whether script is in /scripts/ or root)
_here = os.path.dirname(os.path.abspath(__file__))
_env  = os.path.join(_here, '..', '.env') if os.path.basename(_here) == 'scripts' else os.path.join(_here, '.env')
load_dotenv(_env)

# ---------------------------------------------------------------------------
# DB config — loaded from .env file, never hardcoded
# ---------------------------------------------------------------------------
DB_CONFIG = {
    'host':            os.environ.get('DB_HOST', ''),
    'user':            os.environ.get('DB_USER', ''),
    'password':        os.environ.get('DB_PASSWORD', ''),
    'database':        os.environ.get('DB_NAME', ''),
    'connect_timeout': 10,
}

CONTEXT_DIR = "/cfb-research/team_context"
SEASON      = 2026   # current season for power ratings
ADV_SEASON  = 2025   # most recent completed season for advancedstats

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_conn():
    return pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)

def query_one(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone()

def query_all(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()

# ---------------------------------------------------------------------------
# Pull all power rating ranks for a team
# Returns dict with rank fields
# ---------------------------------------------------------------------------

def get_power_ranks(conn, team, season):
    """Get power rating values and ranks vs all FBS teams."""
    # Get team's own ratings
    row = query_one(conn, """
        SELECT rating, orating, drating, schedulerating
        FROM powerrating
        WHERE team = %s AND year = %s
    """, (team, season))

    if not row:
        return {}

    result = {
        'power_rating_value': round(float(row['rating']), 2) if row['rating'] else None,
        'offense_rating':     round(float(row['orating']), 2) if row['orating'] else None,
        'defense_rating':     round(float(row['drating']), 2) if row['drating'] else None,
    }

    # Compute ranks by counting teams rated higher
    rating_rank = query_one(conn, """
        SELECT COUNT(*) + 1 AS rnk
        FROM powerrating
        WHERE year = %s AND rating > %s
    """, (season, row['rating']))
    result['power_rank_db'] = int(rating_rank['rnk']) if rating_rank else None

    orating_rank = query_one(conn, """
        SELECT COUNT(*) + 1 AS rnk
        FROM powerrating
        WHERE year = %s AND orating > %s
    """, (season, row['orating']))
    result['offense_power_rank'] = int(orating_rank['rnk']) if orating_rank else None

    drating_rank = query_one(conn, """
        SELECT COUNT(*) + 1 AS rnk
        FROM powerrating
        WHERE year = %s AND drating < %s
    """, (season, row['drating']))
    result['defense_power_rank'] = int(drating_rank['rnk']) if drating_rank else None

    return result

# ---------------------------------------------------------------------------
# Pull GC / MC offense + defense ranks
# ---------------------------------------------------------------------------

def get_gc_mc_ranks(conn, team, season):
    """Get game control and momentum control offense/defense ranks."""
    result = {}

    # GC offense rank (higher gc_score_off = better)
    row = query_one(conn, """
        SELECT COUNT(*) + 1 AS rnk
        FROM team_game_control tgc
        JOIN (
            SELECT team, AVG(gc_score_off) AS avg_gc
            FROM team_game_control
            WHERE season = %s
            GROUP BY team
        ) sub ON sub.team != %s
        JOIN (
            SELECT AVG(gc_score_off) AS my_avg
            FROM team_game_control
            WHERE team = %s AND season = %s
        ) me
        WHERE sub.avg_gc > me.my_avg
    """, (season, team, team, season))
    # Simpler approach — use team_composite_season if available, else skip
    comp = query_one(conn, """
        SELECT gc_net_raw, mc_net_raw, composite_rank,
               gc_normalized, mc_normalized, composite_score
        FROM team_composite_season
        WHERE team = %s AND season = %s
    """, (team, ADV_SEASON))

    if comp:
        result['composite_rank']  = comp['composite_rank']
        result['gc_net_raw']      = round(float(comp['gc_net_raw']), 2) if comp['gc_net_raw'] else None
        result['mc_net_raw']      = round(float(comp['mc_net_raw']), 2) if comp['mc_net_raw'] else None
        result['composite_score'] = round(float(comp['composite_score']), 2) if comp['composite_score'] else None

    return result

# ---------------------------------------------------------------------------
# Pull advanced stats ranks
# ---------------------------------------------------------------------------

def get_adv_stat_ranks(conn, team, season):
    """Get key advanced stat ranks from team_rankings table."""
    result = {}

    row = query_one(conn, """
        SELECT
            offense_ppa_ranking,
            defense_ppa_ranking,
            offense_success_rate_ranking,
            defense_success_rate_ranking,
            offense_explosiveness_ranking,
            defense_explosiveness_ranking,
            offense_havoc_total_ranking,
            defense_havoc_total_ranking,
            offense_points_per_opportunity_ranking,
            defense_points_per_opportunity_ranking,
            offense_field_position_average_start_ranking,
            defense_field_position_average_start_ranking
        FROM team_rankings
        WHERE team = %s AND season = %s
    """, (team, season))

    if row:
        result['offense_ppa_rank']          = row['offense_ppa_ranking']
        result['defense_ppa_rank']          = row['defense_ppa_ranking']
        result['offense_success_rank']      = row['offense_success_rate_ranking']
        result['defense_success_rank']      = row['defense_success_rate_ranking']
        result['offense_explosiveness_rank']= row['offense_explosiveness_ranking']
        result['defense_explosiveness_rank']= row['defense_explosiveness_ranking']
        result['offense_havoc_rank']        = row['offense_havoc_total_ranking']
        result['defense_havoc_rank']        = row['defense_havoc_total_ranking']
        result['offense_ppo_rank']          = row['offense_points_per_opportunity_ranking']
        result['defense_ppo_rank']          = row['defense_points_per_opportunity_ranking']

    # Raw advancedstats for pass/run profile
    adv = query_one(conn, """
        SELECT
            offense_ppa, defense_ppa,
            offense_success_rate, defense_success_rate,
            offense_passing_plays_ppa, offense_rushing_plays_ppa,
            offense_passing_plays_success_rate, offense_rushing_plays_success_rate
        FROM advancedstats
        WHERE team = %s AND season = %s
    """, (team, season))

    if adv and adv['offense_passing_plays_success_rate'] and adv['offense_rushing_plays_success_rate']:
        pass_sr  = float(adv['offense_passing_plays_success_rate'])
        rush_sr  = float(adv['offense_rushing_plays_success_rate'])
        total_sr = pass_sr + rush_sr
        if total_sr > 0:
            pass_pct = round(pass_sr / total_sr * 100)
            rush_pct = 100 - pass_pct
            if pass_pct >= 55:
                profile = f"Pass Heavy ({pass_pct}% pass, {rush_pct}% run)"
            elif rush_pct >= 55:
                profile = f"Run Heavy ({rush_pct}% run, {pass_pct}% pass)"
            else:
                profile = f"Balanced ({pass_pct}% pass, {rush_pct}% run)"
            result['offense_profile_db'] = profile

    return result

# ---------------------------------------------------------------------------
# Pull scoring (home/road PPG) from games table
# ---------------------------------------------------------------------------

def get_scoring(conn, team, season):
    """Calculate home and road PPG from games table."""
    result = {}

    home = query_one(conn, """
        SELECT AVG(CAST(home_points AS SIGNED)) AS ppg,
               COUNT(*) AS games
        FROM games
        WHERE home_team = %s AND season = %s
          AND home_points IS NOT NULL
          AND season_type = 'regular'
    """, (team, season - 1))  # use prior completed season

    away = query_one(conn, """
        SELECT AVG(CAST(away_points AS SIGNED)) AS ppg,
               COUNT(*) AS games
        FROM games
        WHERE away_team = %s AND season = %s
          AND away_points IS NOT NULL
          AND season_type = 'regular'
    """, (team, season - 1))

    if home and home['ppg']:
        result['scoring_home_ppg']   = round(float(home['ppg']), 1)
        result['scoring_home_games'] = int(home['games'])
    if away and away['ppg']:
        result['scoring_road_ppg']   = round(float(away['ppg']), 1)
        result['scoring_road_games'] = int(away['games'])

    return result

# ---------------------------------------------------------------------------
# Pull best players from roster_best_players table
# ---------------------------------------------------------------------------

def get_best_players(conn, team, season):
    """
    Pull top-rated players from roster_best_players table.
    Returns a list of dicts ordered by points descending.
    Used by the research agent to constrain player identification —
    the agent should only name players from this list as leaders/standouts.
    """
    rows = query_all(conn, """
        SELECT player_name, position, points, statsline
        FROM roster_best_players
        WHERE year = %s AND team = %s
        ORDER BY points DESC
        LIMIT 12
    """, (season, team))

    if not rows:
        return {}

    players = []
    for row in rows:
        players.append({
            'player_name': row['player_name'],
            'position':    row['position'] or '',
            'points':      int(row['points']) if row['points'] else 0,
            'statsline':   row['statsline'] or '',
        })

    return {'best_players': players}

# ---------------------------------------------------------------------------
# Pull previous coaching staff from coachingstaff table
# ---------------------------------------------------------------------------

def get_previous_coach(conn, team, current_season):
    """
    Pull the prior season's head coach, OC, and DC from coachingstaff.
    Used to ground the research agent on coaching changes — prevents the
    agent from inferring or hallucinating former staff names from sources.
    Returns dict with previous staff fields, or empty dict if not found.
    """
    row = query_one(conn, """
        SELECT headcoach, oc, dc
        FROM coachingstaff
        WHERE school = %s AND year = %s
        LIMIT 1
    """, (team, current_season - 1))

    if not row:
        return {}

    result = {}
    if row.get('headcoach'):
        result['previous_head_coach'] = row['headcoach']
    if row.get('oc'):
        result['previous_oc'] = row['oc']
    if row.get('dc'):
        result['previous_dc'] = row['dc']
    return result

# ---------------------------------------------------------------------------
# Main enrichment function
# ---------------------------------------------------------------------------

def enrich_team(conn, context_path, debug=False):
    with open(context_path) as f:
        context = json.load(f)

    team = context.get('url_param', context.get('team', ''))
    slug = context.get('slug', '')

    if debug:
        print(f"  Enriching: {team}")

    enriched = {}
    enriched.update(get_power_ranks(conn, team, SEASON))
    enriched.update(get_gc_mc_ranks(conn, team, ADV_SEASON))
    enriched.update(get_adv_stat_ranks(conn, team, ADV_SEASON))
    enriched.update(get_scoring(conn, team, SEASON))
    enriched.update(get_best_players(conn, team, SEASON))
    enriched.update(get_previous_coach(conn, team, SEASON))

    # Merge into context — DB values take precedence for their fields
    # Merge into context — DB values take precedence for their fields
    context.update(enriched)

    # Rebuild search_keywords to include top 3 best players
    existing_keywords = context.get('search_keywords', [])
    for p in context.get('best_players', [])[:8]:
        if p.get('player_name') and p['player_name'] not in existing_keywords:
            existing_keywords.append(p['player_name'])
    context['search_keywords'] = existing_keywords

    context['db_enriched_at'] = __import__('datetime').datetime.now().strftime('%Y-%m-%d')
    context['db_enriched_at'] = __import__('datetime').datetime.now().strftime('%Y-%m-%d')

    with open(context_path, 'w') as f:
        json.dump(context, f, indent=2, ensure_ascii=False)

    if debug:
        print(f"  power={context.get('power_rating_value')} "
              f"off_rank=#{context.get('offense_power_rank')} "
              f"def_rank=#{context.get('defense_power_rank')}")
        print(f"  ppa off=#{context.get('offense_ppa_rank')} "
              f"def=#{context.get('defense_ppa_rank')} "
              f"profile={context.get('offense_profile_db','')}")
        print(f"  ppg home={context.get('scoring_home_ppg')} "
              f"road={context.get('scoring_road_ppg')}")
        best = context.get('best_players', [])
        if best:
            print(f"  best_players ({len(best)}): " +
                  ", ".join(f"{p['player_name']} ({p['position']})" for p in best[:8]))
        prev_coach = context.get('previous_head_coach', '')
        if prev_coach:
            print(f"  previous_head_coach: {prev_coach}")
    return True

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--team',        default=None, help='Slug e.g. "alabama"')
    parser.add_argument('--context-dir', default=CONTEXT_DIR)
    parser.add_argument('--debug',       action='store_true')
    args = parser.parse_args()

    conn = get_conn()
    print(f"DB connected. Enriching context files in {args.context_dir}\n")
    

    if args.team:
        path = os.path.join(args.context_dir, f"{args.team}.json")
        if not os.path.exists(path):
            print(f"ERROR: {path} not found"); sys.exit(1)
        files = [path]
    else:
        files = sorted(f for f in
                      [os.path.join(args.context_dir, x)
                       for x in os.listdir(args.context_dir) if x.endswith('.json')]
                      if os.path.isfile(f))

    success = failed = 0
    for path in files:
        slug = os.path.basename(path).replace('.json','')
        print(f"[{slug}]")
        try:
            enrich_team(conn, path, args.debug)
            print(f"  ✓")
            success += 1
        except Exception as e:
            print(f"  ✗ {e}")
            if args.debug:
                import traceback; traceback.print_exc()
            failed += 1

    conn.close()
    print(f"\nDone — success: {success}  failed: {failed}")

if __name__ == '__main__':
    main()