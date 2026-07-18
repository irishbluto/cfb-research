#!/usr/bin/env python3
"""
check_inseason_data.py — one-shot READ-ONLY verification sweep for the
in-season weekly_writeup build (docs/inseason_writeup_spec.md §5/§11 step 1).

Answers every VERIFY item in the spec:
  1. Column existence for each metric the writeup will use
     (team_rankings, advancedstats, stats_misc, pff_team_grades, polls,
      gamelines, powerrating, SandPratings, games, team_composite_season,
      player_ratings)
  2. Season/year row coverage per table (does 2026 exist yet? did 2025?)
  3. Weekly-cadence evidence (polls weeks, gamelines weeks, powerrating_history
     weeks, any timestamp columns' max values)
  4. games-table readiness for game-aware dispatch (2026 rows, dates, weeks,
     completed-score columns)
  5. Team-name join sanity across tables (accent/apostrophe mismatches)

Usage (on the VPS, same env as build_team_context.py):
    python3 /cfb-research/scripts/check_inseason_data.py
    python3 /cfb-research/scripts/check_inseason_data.py > /tmp/inseason_check.txt

Purely SELECT/DESCRIBE — makes no writes.
"""

import os, sys
import pymysql
from dotenv import load_dotenv

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
DB_NAME = DB_CONFIG['database']

SEASON     = 2026
ADV_SEASON = 2025

# ---------------------------------------------------------------------------
# Columns the writeup build needs, per table. Grouped by spec metric.
# Sourced from teamprofile.php / classTeams.php usage (2026-07-17 audit).
# ---------------------------------------------------------------------------
REQUIRED_COLUMNS = {
    'team_rankings': [
        # overall efficiency / explosiveness (O & D)
        'offense_success_rate_ranking', 'defense_success_rate_ranking',
        'offense_explosiveness_ranking', 'defense_explosiveness_ranking',
        'offense_ppa_ranking', 'defense_ppa_ranking',
        # rush / pass splits (O & D)
        'offense_rushing_plays_success_rate_ranking', 'offense_rushing_plays_explosiveness_ranking',
        'offense_passing_plays_success_rate_ranking', 'offense_passing_plays_explosiveness_ranking',
        'defense_rushing_plays_success_rate_ranking', 'defense_rushing_plays_explosiveness_ranking',
        'defense_passing_plays_success_rate_ranking', 'defense_passing_plays_explosiveness_ranking',
        # red zone proxy (points per opportunity)
        'offense_points_per_opportunity_ranking', 'defense_points_per_opportunity_ranking',
        # OL
        'offense_line_yards_ranking', 'offense_stuff_rate_ranking',
        # havoc
        'defense_havoc_total_ranking', 'defense_havoc_front_seven_ranking', 'defense_havoc_db_ranking',
        'offense_havoc_total_ranking',
    ],
    'advancedstats': [
        'offense_ppa', 'defense_ppa',
        'offense_success_rate', 'defense_success_rate',
        'offense_explosiveness', 'defense_explosiveness',
        'offense_rushing_plays_success_rate', 'offense_passing_plays_success_rate',
        'offense_rushing_plays_explosiveness', 'offense_passing_plays_explosiveness',
        'defense_rushing_plays_success_rate', 'defense_passing_plays_success_rate',
        'defense_rushing_plays_explosiveness', 'defense_passing_plays_explosiveness',
        'offense_points_per_opportunity', 'defense_points_per_opportunity',
        'offense_line_yards', 'offense_stuff_rate',
        'defense_havoc_total',
    ],
    'stats_misc': [
        'points_per_drive_off', 'points_per_drive_def',
        'to_lost_off', 'to_forced_def',
        'scoring_per_opp_off', 'scoring_per_opp_def',
        'scoring_opps_off', 'scoring_opps_def',
        'stop_rate_off', 'stop_rate_def',
        'three_out_off', 'three_out_def',
        'drives_off', 'drives_def',
    ],
    'pff_team_grades': [
        'grades_run_block', 'grades_pass_block', 'grades_overall',
        'wins', 'losses', 'points_scored', 'points_allowed',
    ],
    'polls': ['school', 'week', 'season', 'poll', 'rank'],
    'gamelines': ['spread', 'formattedSpread'],
    'powerrating': ['rating'],
    'SandPratings': ['rating'],
    'games': ['season', 'week', 'home_team', 'away_team', 'home_points', 'away_points', 'start_date', 'season_type'],
}

# Tables whose season coverage matters; (table, season_column)
SEASON_TABLES = [
    ('team_rankings',         'season'),
    ('advancedstats',         'season'),
    ('stats_misc',            'year'),
    ('pff_team_grades',       'year'),
    ('polls',                 'season'),
    ('gamelines',             'season'),
    ('powerrating',           'season'),
    ('powerrating_history',   'season'),
    ('SandPratings',          'season'),
    ('team_composite_season', 'season'),
    ('player_ratings',        'season'),
    ('games',                 'season'),
]

DIV = "=" * 78


def q_all(conn, sql, params=None):
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def q_one(conn, sql, params=None):
    rows = q_all(conn, sql, params)
    return rows[0] if rows else None


def table_exists(conn, table):
    r = q_one(conn, """
        SELECT COUNT(*) AS n FROM information_schema.tables
        WHERE table_schema = %s AND table_name = %s
    """, (DB_NAME, table))
    return bool(r and r['n'])


def get_columns(conn, table):
    rows = q_all(conn, """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
    """, (DB_NAME, table))
    # column_name key case differs across MySQL/MariaDB versions
    out = {}
    for r in rows:
        name = r.get('column_name', r.get('COLUMN_NAME'))
        dtype = r.get('data_type', r.get('DATA_TYPE'))
        out[name] = dtype
    return out


def section(title):
    print(f"\n{DIV}\n== {title}\n{DIV}")


def main():
    if not DB_CONFIG['host']:
        print("ERROR: DB env vars not loaded — run from /cfb-research/scripts/ next to ../.env")
        sys.exit(1)
    conn = pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)
    print(f"Connected to `{DB_NAME}`. READ-ONLY sweep for in-season writeup build.")
    flags = []   # collected FLAG lines for the summary

    # ------------------------------------------------------------------
    # 1. Column existence
    # ------------------------------------------------------------------
    section("1. COLUMN EXISTENCE (spec metric list -> actual columns)")
    col_cache = {}
    for table, needed in REQUIRED_COLUMNS.items():
        if not table_exists(conn, table):
            print(f"\n[{table}]  TABLE MISSING")
            flags.append(f"TABLE MISSING: {table}")
            continue
        cols = get_columns(conn, table)
        col_cache[table] = cols
        missing = [c for c in needed if c not in cols]
        print(f"\n[{table}]  {len(cols)} columns total; required present: {len(needed)-len(missing)}/{len(needed)}")
        if missing:
            for c in missing:
                print(f"    MISSING: {c}")
            flags.append(f"{table}: missing columns {missing}")
        # timestamp-ish columns are cadence evidence — surface them
        ts_cols = [c for c, t in cols.items()
                   if t in ('timestamp', 'datetime', 'date')
                   or any(k in c.lower() for k in ('updated', 'created', 'modified', 'refreshed'))]
        if ts_cols:
            print(f"    timestamp-like columns: {ts_cols}")

    # ------------------------------------------------------------------
    # 2. Season / year coverage per table
    # ------------------------------------------------------------------
    section("2. SEASON COVERAGE (rows per season, last 4 + current)")
    for table, ycol in SEASON_TABLES:
        if not table_exists(conn, table):
            print(f"\n[{table}]  TABLE MISSING")
            flags.append(f"TABLE MISSING: {table}")
            continue
        cols = col_cache.get(table) or get_columns(conn, table)
        col_cache[table] = cols
        if ycol not in cols:
            alt = [c for c in cols if c.lower() in ('season', 'year', 'yr')]
            print(f"\n[{table}]  no `{ycol}` column (candidates: {alt})")
            flags.append(f"{table}: expected season column `{ycol}` not found")
            continue
        try:
            rows = q_all(conn, f"""
                SELECT `{ycol}` AS yr, COUNT(*) AS rows_n
                FROM `{table}`
                GROUP BY `{ycol}` ORDER BY `{ycol}` DESC LIMIT 5
            """)
            years = {int(r['yr']): r['rows_n'] for r in rows if r['yr'] is not None}
            print(f"\n[{table}] " + ", ".join(f"{y}: {n} rows" for y, n in years.items()))
            if SEASON not in years:
                print(f"    NOTE: no {SEASON} rows yet")
            if ADV_SEASON not in years:
                flags.append(f"{table}: no {ADV_SEASON} rows — reference season absent")
            # max of any timestamp-like column for the newest season present
            ts_cols = [c for c, t in cols.items()
                       if t in ('timestamp', 'datetime')
                       or any(k in c.lower() for k in ('updated', 'modified', 'refreshed'))]
            if ts_cols and years:
                newest = max(years)
                for tc in ts_cols[:2]:
                    r = q_one(conn, f"SELECT MAX(`{tc}`) AS mx FROM `{table}` WHERE `{ycol}` = %s", (newest,))
                    if r and r['mx']:
                        print(f"    last touch ({newest}, {tc}): {r['mx']}")
        except Exception as e:
            print(f"\n[{table}]  QUERY ERROR: {e}")
            flags.append(f"{table}: coverage query failed ({e})")

    # ------------------------------------------------------------------
    # 3. Weekly-cadence evidence
    # ------------------------------------------------------------------
    section("3. WEEKLY CADENCE EVIDENCE (2025 as the reference season)")

    # polls — weeks per poll name (also confirms exact poll strings)
    if table_exists(conn, 'polls'):
        try:
            rows = q_all(conn, """
                SELECT poll, COUNT(DISTINCT week) AS wks, MIN(week) AS wmin, MAX(week) AS wmax, COUNT(*) AS rows_n
                FROM polls WHERE season = %s GROUP BY poll
            """, (ADV_SEASON,))
            print(f"\n[polls] season {ADV_SEASON}:")
            for r in rows:
                print(f"    '{r['poll']}': weeks {r['wmin']}-{r['wmax']} ({r['wks']} distinct), {r['rows_n']} rows")
            if not rows:
                flags.append(f"polls: no {ADV_SEASON} rows")
            ap_ok  = any('AP' in (r['poll'] or '') and r['wks'] >= 10 for r in rows)
            cfp_ok = any('Playoff' in (r['poll'] or '') for r in rows)
            if not ap_ok:
                flags.append("polls: AP Top 25 weekly coverage looks thin")
            if not cfp_ok:
                flags.append("polls: no 'Playoff Committee Rankings' rows found")
        except Exception as e:
            print(f"[polls] QUERY ERROR: {e}")
            flags.append(f"polls cadence query failed ({e})")

    # gamelines — weeks covered; needs a week or game linkage
    if table_exists(conn, 'gamelines'):
        cols = col_cache.get('gamelines') or get_columns(conn, 'gamelines')
        wk = 'week' if 'week' in cols else None
        try:
            if wk:
                rows = q_all(conn, f"""
                    SELECT `{wk}` AS wk, COUNT(*) AS n FROM gamelines
                    WHERE season = %s GROUP BY `{wk}` ORDER BY wk
                """, (ADV_SEASON,))
                weeks = [int(r['wk']) for r in rows if r['wk'] is not None]
                print(f"\n[gamelines] season {ADV_SEASON}: weeks {min(weeks) if weeks else '-'}-{max(weeks) if weeks else '-'} covered ({len(weeks)} distinct)")
            else:
                print(f"\n[gamelines] no `week` column — columns: {sorted(cols)[:20]}")
                flags.append("gamelines: no week column — check how lines join to games")
            r = q_one(conn, "SELECT COUNT(*) AS n FROM gamelines WHERE season = %s", (SEASON,))
            print(f"    {SEASON} rows so far: {r['n'] if r else 0}")
        except Exception as e:
            print(f"[gamelines] QUERY ERROR: {e}")
            flags.append(f"gamelines cadence query failed ({e})")

    # powerrating_history — proves weekly power-rating snapshots
    if table_exists(conn, 'powerrating_history'):
        try:
            cols = get_columns(conn, 'powerrating_history')
            wkcol = next((c for c in cols if c.lower() in ('week', 'wk')), None)
            if wkcol:
                rows = q_all(conn, f"""
                    SELECT `{wkcol}` AS wk, COUNT(*) AS n FROM powerrating_history
                    WHERE season = %s GROUP BY `{wkcol}` ORDER BY wk
                """, (ADV_SEASON,))
                weeks = [int(r['wk']) for r in rows if r['wk'] is not None]
                print(f"\n[powerrating_history] season {ADV_SEASON}: {len(weeks)} weekly snapshots"
                      f" (weeks {min(weeks)}-{max(weeks)})" if weeks else f"\n[powerrating_history] no {ADV_SEASON} rows")
            else:
                print(f"\n[powerrating_history] no week column — columns: {sorted(cols)[:15]}")
        except Exception as e:
            print(f"[powerrating_history] QUERY ERROR: {e}")

    # stats_misc — the 'ONLY HAVE PPD FOR 2025' teamprofile comment
    if table_exists(conn, 'stats_misc'):
        try:
            rows = q_all(conn, "SELECT year, COUNT(*) AS n FROM stats_misc GROUP BY year ORDER BY year DESC")
            yrs = {int(r['year']): r['n'] for r in rows}
            print(f"\n[stats_misc] years present: " + ", ".join(f"{y} ({n})" for y, n in yrs.items()))
            print("    ^ teamprofile.php comment says PPD data exists ONLY for 2025 —")
            print("      QUESTION for Jonathan: what job builds stats_misc, and will it run weekly in-season 2026?")
            if len(yrs) <= 1:
                flags.append("stats_misc: single year of data — confirm weekly in-season build plan for 2026 (PPD source)")
        except Exception as e:
            print(f"[stats_misc] QUERY ERROR: {e}")

    # ------------------------------------------------------------------
    # 4. games table — dispatch readiness
    # ------------------------------------------------------------------
    section(f"4. GAMES TABLE — {SEASON} game-aware dispatch readiness")
    if table_exists(conn, 'games'):
        try:
            r = q_one(conn, """
                SELECT COUNT(*) AS n, MIN(start_date) AS dmin, MAX(start_date) AS dmax,
                       SUM(CASE WHEN week IS NULL THEN 1 ELSE 0 END) AS no_week
                FROM games WHERE season = %s AND season_type = 'regular'
            """, (SEASON,))
            print(f"{SEASON} regular-season rows: {r['n']} | dates {r['dmin']} -> {r['dmax']} | rows missing week: {r['no_week']}")
            if not r['n']:
                flags.append(f"games: no {SEASON} schedule rows — dispatcher has nothing to key on")
            rows = q_all(conn, """
                SELECT DAYNAME(start_date) AS dow, COUNT(*) AS n
                FROM games WHERE season = %s AND season_type = 'regular'
                GROUP BY DAYNAME(start_date) ORDER BY n DESC
            """, (SEASON,))
            print("games by day-of-week: " + ", ".join(f"{r2['dow']}: {r2['n']}" for r2 in rows))
            # 2025 completed-score sanity: dispatcher keys on finals appearing next morning
            r = q_one(conn, """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN home_points IS NOT NULL THEN 1 ELSE 0 END) AS with_scores
                FROM games WHERE season = %s AND season_type = 'regular'
            """, (ADV_SEASON,))
            print(f"{ADV_SEASON} reference: {r['with_scores']}/{r['total']} rows have final scores")
            print("QUESTION for Jonathan: how soon after a game do final scores land in `games`? (same night / next morning / weekly batch)")
        except Exception as e:
            print(f"[games] QUERY ERROR: {e}")
            flags.append(f"games dispatch query failed ({e})")

    # ------------------------------------------------------------------
    # 5. Team-name join sanity across tables
    # ------------------------------------------------------------------
    section(f"5. TEAM-NAME JOIN SANITY ({ADV_SEASON}: advancedstats vs each table)")
    try:
        base = {r['team'] for r in q_all(conn, "SELECT DISTINCT team FROM advancedstats WHERE season = %s", (ADV_SEASON,))}
        print(f"advancedstats {ADV_SEASON}: {len(base)} teams (baseline)")
        checks = [
            ('team_rankings',   'team',   'season', ADV_SEASON),
            ('stats_misc',      'team',   'year',   ADV_SEASON),
            ('pff_team_grades', 'name',   'year',   ADV_SEASON),
            ('polls',           'school', 'season', ADV_SEASON),
            ('powerrating',     'team',   'season', SEASON),
            ('SandPratings',    'team',   'season', ADV_SEASON),
        ]
        for table, namecol, ycol, yr in checks:
            if not table_exists(conn, table):
                continue
            cols = col_cache.get(table) or get_columns(conn, table)
            nc = namecol if namecol in cols else next((c for c in cols if c.lower() in ('team', 'school', 'name', 'team_name')), None)
            if not nc:
                print(f"[{table}] no name column found — columns: {sorted(cols)[:12]}")
                continue
            try:
                names = {r['nm'] for r in q_all(conn, f"SELECT DISTINCT `{nc}` AS nm FROM `{table}` WHERE `{ycol}` = %s", (yr,))}
            except Exception as e:
                print(f"[{table}] QUERY ERROR: {e}")
                continue
            if not names:
                print(f"[{table}] ({yr}, `{nc}`): NO ROWS")
                continue
            missing = sorted(base - names)
            print(f"[{table}] ({yr}, `{nc}`): {len(names)} names; FBS teams unmatched: {len(missing)}")
            if missing and len(missing) <= 12:
                for m in missing:
                    print(f"    unmatched: {m}")
            elif missing:
                print(f"    (first 12) {missing[:12]}")
            if missing:
                flags.append(f"{table}: {len(missing)} team-name mismatches vs advancedstats (accents/apostrophes/branding)")
    except Exception as e:
        print(f"join sanity failed: {e}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    section("SUMMARY — FLAGS")
    if flags:
        for f in flags:
            print(f"  FLAG: {f}")
    else:
        print("  No flags — every table, column, and coverage check passed.")
    print("\nPaste this full output back into the Punt & Rally build chat.")
    conn.close()


if __name__ == '__main__':
    main()
