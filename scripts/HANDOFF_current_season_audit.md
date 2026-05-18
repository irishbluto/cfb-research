# Handoff: Current-season audit for remaining builders

**Created:** 2026-05-17
**Predecessor work:** TO margin + one-score record current-season builders shipped this session. See git log for `build_team_context.py` and `research_agent.py` commits around 2026-05-17.

## Context — read this first

The canonical rule lives in `Custom_Instructions_Updated.md` → Data Strategy → "Seasonal data cycle — current-season pivot at in_season boundary" and is mirrored in memory at `feedback_season_data_cycle.md`. Read both before starting. Short version:

Once the agent enters `in_season` mode (late August), every DB-backed metric must surface **current-season** data, not prior-season. Prior-year data stays visible during Week 1-2 (sample too small for signal) but only as **PRIOR-YEAR CONTEXT ONLY — do NOT apply to current team** framing, never as a current-team read. The TO margin / one-score builders shipped this cycle are the reference implementation pattern — mirror their shape.

## Reference implementation (already shipped)

In `build_team_context.py`:
- `build_turnover_margin(conn, team, season)` (offseason, queries season - 1) at line ~1102
- `build_current_season_turnover_margin(conn, team, season)` (in-season, queries season) at line ~1175 — note the `current_season_*` prefixed field names
- `build_one_score_games(conn, team, season)` at line ~990
- `build_current_season_one_score(conn, team, season)` at line ~1241 — note it also emits `current_season_games_played` so the prompt layer can gate on sample size

In `research_agent.py`:
- `close_games_turnover_block` at lines ~322-415 — the mode-gated injection pattern. Note the `_MIN_IN_SEASON_GAMES = 3` threshold and the early-season fallback that surfaces prior-year wrapped in "PRIOR-YEAR CONTEXT ONLY" labels.

## Builder inventory — every DB-backed builder in build_team_context.py

| Builder | Line | Currently uses | In-season behavior | Audit verdict |
|---|---|---|---|---|
| `build_header` | 271 | SEASON | Head coach + Vegas + projected record. Vegas/projected are forward-looking and don't need flipping; head coach is per-year. **Probably fine.** | VERIFY — confirm vegas_win_total etc. tables get updated mid-season. |
| `build_power_ranks` | 345 | SEASON | Reads `powerrating` table. Per memory `project_security_hardening.md` and powerratings rebuild section, this table IS updated weekly in-season. **Probably auto-flips.** | VERIFY — confirm 2026 in-season rows appear in powerrating week-by-week, no current_season variant needed. |
| `build_talent_ranks` | 396 | SEASON | `team_talent` is set at start of year (247 composite). Doesn't change mid-season. **Fine.** | NO CHANGE. |
| `build_sp_plus` | 452 | SEASON | `SandPratings` (Bill Connelly's SP+) — Bill updates these weekly during the season. Should auto-flip if scraper runs. | VERIFY scraper writes current-year rows in-season. |
| `build_preview` | 517 | SEASON | `team_preview` — preseason-only data (QB situation, blue chip, returning production). **Stays prior-year in-season** because it's a preseason snapshot. | NO CHANGE — but research_agent should weight this less in-season. |
| `build_coaching` | 625 | SEASON | Coaching staff is per-year. **Fine.** | NO CHANGE. |
| `build_schedule_summary` | 682 | SEASON | Schedule + tiers. **Fine, auto-flips.** | NO CHANGE. |
| `build_notes` | 720 | SEASON | Manually-curated notes per season. **Fine.** | NO CHANGE. |
| `build_portal` | 748 | SEASON | Portal in/out for the year. Forward-looking for current cycle. **Fine.** | NO CHANGE. |
| `build_recruiting` | 794 | SEASON | Recruiting class for the year. **Fine.** | NO CHANGE. |
| `build_recruiting_summary` | 830 | SEASON | Same as above. **Fine.** | NO CHANGE. |
| `build_best_players` | 867 | SEASON | `player_ratings` Production Numbers. **CRITICAL — this is the Production Numbers / `best_players` field the agent leans on heavily.** Production Numbers are P&R's own build (per memory `feedback_metric_attribution.md`); confirm the build job populates 2026 rows in-season as games play out. | **AUDIT REQUIRED** — likely needs current-season variant if player_ratings only has preseason projections in_season. |
| `build_advanced_stats` | 887 | **ADV_SEASON (2025)** | Reads `team_rankings` + `advancedstats` for PPA, success rate, explosiveness, etc. **THIS IS PRIOR-YEAR DATA.** | **PRIORITY — needs `build_current_season_advanced_stats` variant pulling from 2026 rows.** Confirm CFBD-sourced advanced stats land in those tables weekly during the season. |
| `build_composite` | 935 | **ADV_SEASON (2025)** | `team_composite_season` — GC/MC net raws + composite rank. **PRIOR-YEAR DATA.** | **PRIORITY — needs current-season variant.** Confirm composite is built weekly in-season. |
| `build_last_season_scoring` | 953 | SEASON-1 | PPG and margin for season-1 (games table). Name says "last_season" — explicitly prior-year by design. | **In-season: build current-season parallel** (running 2026 PPG/margin from games table where home_points IS NOT NULL). |
| `build_one_score_games` | 990 | SEASON-1 | ✅ Current-season variant shipped this cycle. | DONE. |
| `build_turnover_margin` | 1102 | SEASON-1 | ✅ Current-season variant shipped this cycle. | DONE. |
| `build_last_season_record` | 1328 | SEASON-1 | W-L for prior season + four-year aggregate. Name says "last_season" — explicitly prior-year by design. | **In-season: build current-season W-L parallel** (running 2026 record). Multi-year aggregate stays prior-year-anchored. |

## Out-of-table builders that may also need attention

The roster/schedule scrape (`scrape_team_context.py`) writes `full_roster`, `schedule_2026`, `profile_2026`, `last_season_ats`. These are scraper-driven and auto-flip with the underlying pages — **probably no audit needed**, but worth a quick check that the scraper handles the calendar transition cleanly. Memory `feedback_scraper_page_redesign_followthrough.md` flags scrape_team_context.py as needing same-session audit when teamprofile/teamroster/scheduleoutlook get touched.

## Injection layer audit (research_agent.py + conference_research_agent.py)

For every new `build_current_season_*` builder, the injection layer needs parallel updates:

1. **`research_agent.py`** — wherever an offseason field is currently surfaced in a prompt block, add a mode-gated branch that uses the current-season variant in `in_season`/`postseason`, with early-season fallback (`< 3 games` shows prior-year wrapped as CONTEXT ONLY). The `close_games_turnover_block` pattern (lines ~322-415) is the model.

2. **`conference_research_agent.py`** — likely needs parallel changes for any per-team data it consumes. **Check this file carefully** — per memory `project_conference_previews.md`, this is offseason-only deliverable today, so it may not need in_season handling at all. Confirm before duplicating work.

3. **Agent rules** — the regression-analysis rule at lines ~839-857 of research_agent.py is the model for how to instruct the agent about current-vs-prior framing. Each new metric category may want its own paragraph in that rule (e.g., "PPA in-season is descriptive, not predictive — current-year PPA is how the team has played; do not extrapolate prior-year PPA to current play").

## Suggested execution order

1. **Inventory verification** — for each "VERIFY" / "AUDIT REQUIRED" row above, confirm whether the underlying table (powerrating, SandPratings, player_ratings, team_rankings, advancedstats, team_composite_season) actually gets populated weekly during the season. Query a known-good 2025 season's rows to see the cadence. If the table auto-populates, no builder change needed; if not, build a current-season variant.

2. **Top priority builders** (highest agent prompt weight): `build_advanced_stats` (PPA), `build_best_players` (Production Numbers), `build_composite` (GC/MC). These are the in-season weight-bearing reads.

3. **Secondary** (running totals from games table): current-season parallels of `build_last_season_scoring` and `build_last_season_record`.

4. **Injection layer** — wire each new builder into research_agent.py with the same mode-gated pattern + early-season fallback. Each one needs its own prompt-rendering block (mirroring `close_games_turnover_block` lines 322-415).

5. **Agent rule updates** — for each metric category that now has current-season behavior, add a paragraph to the prompt explaining the in-season vs offseason framing (mirroring the regression-analysis rule at lines ~839-857).

6. **Conference agent** — confirm scope (offseason-only?) before duplicating.

7. **Test** — force `mode = "in_season"` temporarily, run on a team with 2025 data populated, verify each field renders the way the rule expects.

## Verification commands

```bash
# Confirm both files still compile after changes
python3 -m py_compile scripts/build_team_context.py
python3 -m py_compile scripts/research_agent.py
python3 -m py_compile scripts/conference_research_agent.py

# Test build_team_context on a team
python3 scripts/build_team_context.py --team georgia --debug

# Inspect the JSON to confirm current_season_* fields populate
python3 -c "import json; d = json.load(open('/cfb-research/team_context/georgia.json')); print({k:v for k,v in d.items() if 'current_season' in k})"

# Manually force mode = "in_season" in research_agent.py and dry-run on one team
```

## Important constraints to preserve

- Per memory `feedback_bash_git_mounts.md` — do NOT use bash sandbox for git operations on the Windows `C:\GitHub\cfb-research` mount; it orphans index.lock. Use VSCode source control for Windows-side commits (per `feedback_git_vscode_windows.md`).
- Per memory `feedback_code_style.md` — surgical changes, paste-ready blocks, PHP 7.4 constraints for any site-side work.
- **Edit tool truncation watchout:** during this session, the Edit tool truncated research_agent.py mid-write at byte ~32KB. If that happens again, restore via `git show HEAD:scripts/research_agent.py > /tmp/restore.py` then re-apply via bash + Python file-write. The bash-based atomic write pattern in this session's commits is the fallback.

## Files this handoff touches

- `C:\GitHub\cfb-research\scripts\build_team_context.py` — add `build_current_season_*` builders, wire into main aggregator
- `C:\GitHub\cfb-research\scripts\research_agent.py` — add mode-gated injection blocks for each new builder, add prompt rules
- `C:\GitHub\cfb-research\scripts\conference_research_agent.py` — verify scope, parallel updates if applicable
- `C:\Users\irish\Projects\PuntandRally\Custom_Instructions_Updated.md` — keep Data Strategy → Seasonal data cycle status section in sync (this handoff already updated it as of 2026-05-17)
- Memory `feedback_season_data_cycle.md` — keep status section in sync

## Out of scope for the next chat

- Sources / scrapers handling — the rule explicitly notes "Sources that auto-flip" (scrapers) typically need no agent-side change; only re-audit if a scraper is touched.
- National landscape agent — uses contextual sources, not team-stat queries.
- Conference previews — currently offseason-only per memory; confirm before treating in scope.
