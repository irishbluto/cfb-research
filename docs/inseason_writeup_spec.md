# In-Season Weekly Writeup — Design Spec

**Created:** 2026-07-17
**Status:** DESIGN — approved decisions locked, implementation not started
**Owner files:** `scripts/research_agent.py`, `scripts/build_team_context.py`, `scripts/cron_team_research.sh`, `scripts/run_pipeline.py`, site-side `includes/functions.php` / `includes/classTeams.php` / `teamprofile.php`

---

## 1. Purpose

When `mode = in_season` (Aug 29 – Dec 5), each team's research run produces a new
**`weekly_writeup`** field: a two-paragraph piece that lets a reader follow any of the
138 FBS teams the way a local fan does — what happened in the game they just played,
who they play this week, a peek at who's next, who's hurt, and how the beat feels
about it. This is the differentiator: nobody can follow 138 local beats; the writeup
does it for them.

Everything else in the pipeline is unchanged. Same sources (YouTube, written/beat,
Reddit, web fallback), same fetchers, same memory system, same JSON output flow.
`agent_summary` keeps its current in-season spec (up to 7 sentences) and keeps feeding
`social_log.php` and `team_memory_writer.py` exactly as today.

### Decisions locked 2026-07-17

| Decision | Choice |
|---|---|
| Output shape | New `weekly_writeup` JSON field, additive; `agent_summary` untouched |
| Cadence | Twice weekly per team: morning after their game + Thursday (most teams: Sun + Thu); manual single-team runs any day |
| Opponent data | Rankings snapshot only (record, power rating, SP+, AP/CFP) for this week's and next week's opponents |
| Betting | Spread for the upcoming game + team ATS record, woven in **when notable** |
| Length | 2 paragraphs, ~12 sentences average, **hard min 200 words, hard max 350 words** |

---

## 2. Output contract

New top-level key in the research JSON, in_season (and postseason) modes only:

```json
"weekly_writeup": {
  "text": "Paragraph one...\n\nParagraph two...",
  "word_count": 287,
  "run_type": "postgame | preview | manual",
  "season_week": 7,
  "last_game": "W 31-24 vs Missouri (2026-10-10)",
  "this_week": "at Georgia, Sat 2026-10-17, 3:30 PM",
  "next_week": "vs Vanderbilt, Sat 2026-10-24"
}
```

- `text` — the writeup. Exactly two paragraphs separated by `\n\n`. Hard limits:
  **200 ≤ words ≤ 350** (target ~270–300, ~12 sentences). Enforce in the pipeline,
  not just the prompt (see §8 validator).
- `run_type` — which of the twice-weekly runs produced it (postgame = morning after
  the game; preview = Thursday; manual = ad-hoc). The agent gets this as a prompt
  input and it shifts emphasis (see §4).
- The metadata fields (`last_game` / `this_week` / `next_week`) are echoed from the
  injected context so the site can render a header line without parsing prose.

**Site-side (small tweak, one-time):** add `weekly_writeup` to the research map in
`includes/functions.php` (~line 791) and `classTeams.php` (~line 157). Team page
renders `weekly_writeup.text` with `nl2br()`/paragraph split when present, falls back
to `agent_summary` when absent (offseason, or early runs before first game). Keep
PHP 7.4 compatible. `social_log.php` and `team_memory_writer.py` need **no changes**.

---

## 3. The two paragraphs — editorial structure

**Paragraph 1 — the game they just played (the anchor).**
Result and one-line context (record, ranking movement if any), then *why* it went the
way it did, grounded in the stat categories (§5): e.g. "won the explosiveness battle,"
"stalled in the red zone again," "the OL gave up pressure all night." Who played well
and who didn't, by name — names must obey the existing roster-verification rules.
Beat and fan reaction: what the locals took away from it, especially where vibes and
numbers disagree. If the game confirmed or broke a running storyline (lifecycle
threads), say so here.

**Paragraph 2 — looking ahead (the payoff).**
This week's opponent with the rankings snapshot framing the stakes (their record,
power rating, SP+, AP/CFP rank), spread and ATS note when notable. Injuries and
availability for the upcoming game: who is OUT and for how long, who is questionable,
sourced from the beat (see §6). How the beat feels the team will play — **include
beat-writer game predictions** when the sources make them (attribute the outlet, not
necessarily the writer). Close with a 1–2 sentence peek at the following week's
opponent — this is where lookahead/trap-game and short-week context lives naturally.

**Composition rules carried over:** the lifecycle weighting (DEVELOPING leads,
CONTINUING one tight pass, SETTLED one clause), forbidden national-policy topics,
name-verification rules, and "titles are not sources" all apply to `weekly_writeup`
exactly as they do to `agent_summary`. In-season, DEVELOPING is almost always the
game just played; season-long threads (QB situation, coach seat, award campaigns)
compress as the calendar advances.

**Anti-recap rule (new):** the writeup must never read like a box-score restatement.
The score gets one clause; the real estate goes to *why* (stat-grounded) and *what it
means for the next two games*. A reader who watched the game should still learn
something.

---

## 4. Run-type emphasis

Same format both runs; the split is where the fresh reporting is.

| | `postgame` (morning after) | `preview` (Thursday) |
|---|---|---|
| P1 weight | ~60% — beat is all reaction, use it | ~40% — compress the game to its takeaways |
| P2 weight | ~40% — early look, spread may not be final | ~60% — beat is in preview mode: final injury reports, predictions, matchup analysis |
| Injuries | What came out of the game (new injuries, severity unknown is fine — say so) | Availability for Saturday: definitive OUT/questionable list |
| Predictions | Usually not out yet — omit rather than invent | Prime capture window — most beats publish picks Wed–Fri |

`run_pipeline.py` passes `--run-type postgame|preview|manual` through to
`research_agent.py`; default derives from day-of-week vs the team's last game date.

---

## 5. Guiding data — the stat block

These guide the narrative; the writeup does **not** have to use all of them. The agent
should surface both the number and the national rank where available, and prefer ranks
in prose (ranks read; raw rates don't). All of it must be **current-season** data per
the season-data-cycle rule; weeks 1–2 fall back to prior-year wrapped in "PRIOR-YEAR
CONTEXT ONLY" framing with the existing `_MIN_IN_SEASON_GAMES = 3` gate.

### Inventory vs. what exists today

**Site audit 2026-07-17 (teamprofile.php + classTeams.php): every metric already
has a DB home.** No new external sources needed. Remaining verification is about
*in-season weekly cadence*, not existence — run `scripts/check_inseason_data.py`
on the VPS for the full sweep.

Statuses: **EXISTS** (table + columns confirmed via site code; confirm weekly
current-season rows land), **EXTEND** (builder must be widened to pull it),
**NEW** (builder must be written; table exists).

| Metric (O & D unless noted) | Source (confirmed from site code) | Status |
|---|---|---|
| Team record (current season) | `games` table (also `pff_team_grades.wins/losses`) | NEW builder — flagged in HANDOFF_current_season_audit |
| Power rating + rank | `powerrating` (+ `powerrating_history` weekly snapshots) | EXISTS |
| SP+ rating + rank | `SandPratings` | EXISTS — confirm weekly scraper cadence |
| AP Top 25 / CFP rank | `polls` table — poll names `AP Top 25` / `Playoff Committee Rankings`, keyed school+week+season (`getRanking`, classTeams ~L5056). CFP from ~Wk 10, Tue nights; site uses AP Sun–Tue even after Wk 10 | EXISTS — NEW builder |
| Off/Def efficiency (success rate, PPA) | `team_rankings` `*_success_rate_ranking` / `*_ppa_ranking`; raws in `advancedstats` | EXISTS — needs current-season variant (handoff PRIORITY) |
| Off/Def explosiveness | `team_rankings` `*_explosiveness_ranking` | EXISTS — same variant |
| Rush/Pass efficiency + explosiveness splits (O & D) | `team_rankings` `offense/defense_rushing/passing_plays_success_rate_ranking` + `_explosiveness_ranking` (all 8 confirmed rendered on teamprofile) | EXTEND — widen builder column list |
| Red zone performance | `team_rankings` `*_points_per_opportunity_ranking` + `stats_misc.scoring_per_opp_off/def` — the site itself uses PPO as its red-zone language (classTeams ~L6654) | EXTEND — PPO **is** the site's red-zone metric; no new source |
| Points per drive | `stats_misc.points_per_drive_off/def`, ranks via `getMiscStatsWithRanks` window functions | EXTEND — **CADENCE OPEN**: teamprofile comment says PPD data exists only for 2025; confirm what job builds `stats_misc` and that it runs weekly in-season |
| Scoring (PPG for/against) | `games` table (parallel of `build_last_season_scoring`); `pff_team_grades.points_scored/allowed` as cross-check | NEW builder — flagged in handoff |
| Turnover margin | `build_current_season_turnover_margin`; also `stats_misc.to_lost_off/to_forced_def` | **DONE** (builder shipped 2026-05-17) |
| OL run block | `pff_team_grades.grades_run_block` (+rank via `getTeamPFFGradesWithRanks`); `team_rankings` line-yards/stuff-rate as secondary | EXTEND — confirm PFF import runs weekly in-season |
| OL pass block | `pff_team_grades.grades_pass_block` | EXTEND — same |
| Defensive havoc | `team_rankings` `defense_havoc_total/front_seven/db_ranking` | EXISTS — current-season variant |

Name-matching watchout for the new builders: `pff_team_grades` matches on
`name`/`franchise_id` with an apostrophe normalization map (Hawai'i, Texas A&M) —
mirror `getTeamPFFGradesWithRanks`'s normalization **and add San José State**
(confirmed unmatched in the DB sweep), and note `stats_misc` uses `year` while
most tables use `season`.

### Verification results (check_inseason_data.py run on VPS, 2026-07-17)

**All green.** 62/63 required columns exist (the one "missing" was this script
assuming `SandPratings.rating` — actual column is `rating_overall` with `year`,
which `build_sp_plus` already handles). Deep history everywhere: advancedstats/
team_rankings/pff_team_grades 2021–2025 (~136 rows/season), stats_misc 2016–2025
(the "PPD only 2025" teamprofile comment was wrong/stale — 10 seasons present),
polls 2025 = AP wks 1–17 + Coaches + CFP wks 11–17. `games` 2026: 893 regular
rows, zero missing week numbers, Sat 756 / Fri 65 / Thu 35 / Tue 18 / Wed 15 /
Sun 3 / Mon 1. 2025 finals: 888/888 populated. Column-convention notes for the
builders: `powerrating`, `powerrating_history`, `SandPratings`, `player_ratings`,
`stats_misc` all use `year`; `gamelines` has no season/week — it joins on
`gamelines.id = games.id` (classTeams ~L9217); `powerrating_history` has no week
column (`built_at` timestamp is the snapshot key). Polls carry ranked teams only
(~49 schools/season) — absence = unranked, not a join failure.

### In-season cadence — resolved by cron.php (site scripts/cron.php)

The site already has a centralized cron dispatcher with the exact chains the
writeup depends on; "no 2026 rows yet" in stats tables is simply preseason.
Dependencies for the writeup dispatcher:

| cron.php group | Contents | Schedule | Writeup dependency |
|---|---|---|---|
| `gameday` | updategames + gamelines + weather | DEFINED: Sat 9am–Sun 3:45am ET every 15 min, hourly otherwise | Finals + spreads land same-night → Sunday-morning postgame batch is safe |
| `stats` | drives import → adv stats → season stats → ranks → stats_misc | **TBD — manual only** (intent: Sun after finals + Mon morning) | MUST run before the Sunday postgame batch (populates advancedstats, team_rankings, stats_misc weekly) |
| `builds` | game control, CSS, composite, matchup rolling, power ratings | **TBD — manual only** (chains after stats) | Same — powerrating freshness |
| `polls` | polls.php (AP/Coaches/CFP) | **TBD** (intent: Sun night + Tue night for CFP) | See poll-timing note below |
| PFF | pffteamgrades.php | **ON HOLD** (team-assignment data issue); Jonathan runs manually Sunday mornings | OL grades may be a week stale for Sunday batch; fresh by Thursday — acceptable |

**Poll timing is editorially self-solving:** AP releases Sunday afternoon, after
the Sunday-morning postgame batch — so the postgame writeup naturally uses the
rank the team carried INTO the game ("No. 14 Oklahoma beat..."), which is correct
usage; the Thursday preview run picks up the new poll (and Wednesday+ CFP).
No engineering needed — just an agent prompt note.

**Scheduling requirement to carry into session 4:** the research postgame batch
must be sequenced AFTER cron.php's `stats` + `builds` groups complete on Sunday
morning (e.g. stats/builds ~4–6 AM ET, research slots from 8 AM), and the
`stats`/`builds`/`polls` groups need their TBD schedules actually enabled in
hPanel before Aug 29. The PFF group's team-assignment data issue (Sorsby wrong
team) is a pre-season fix item on the site side.

The `build_current_season_advanced_stats` builder from the handoff doc is the
workhorse here — this spec widens its column list to cover the splits, red zone/PPO,
PPD, and OL categories in one pass. Follow the shipped TO-margin/one-score pattern
(`current_season_*` field prefixes, games-played gate).

### Opponent rankings snapshot (new builder)

`build_opponent_snapshots(conn, team, season)` — from the schedule, resolve **this
week's opponent** and **next week's opponent**, and emit for each:

```
opponent, date, site (home/away/neutral), record, power_rating + rank,
sp_plus + rank, ap_rank (if ranked), cfp_rank (if ranked)
```

Plus for this week's game only: **spread** — source is the existing `gamelines`
table; NOTE `gamelines.spread` is not reliably signed, use `formattedSpread` /
`getDisplaySpread` logic for the favorite (memory: gamelines-unsigned-spread) —
and the team's **ATS record** (`team_ats_record`, already scraped, auto-rolls to
current season). Injected as a compact `## Upcoming Opponents`
prompt block. No full stat lines for opponents — matchup color comes from the beat.

**Notable threshold for betting:** include the spread when it frames the game (big
favorite/dog, short line in a rivalry); include ATS only when it's a story (e.g.
6-0 ATS, 0-5 ATS at home). Silence is the default — this is a football writeup, not
a betting card.

---

## 6. Sources, injuries, and the beat

Sources are unchanged. What changes is the extraction focus in in_season mode:

- **Beat-first, harder than ever.** The beat carries the game story, the injury
  report, the vibes, and the predictions. Aggregators stay deprioritized.
- **Injuries are a first-class deliverable.** Keep the existing `injury_flags`
  comprehensive-list rules, and extend each entry with availability for the upcoming
  game: `Player (Pos): injury — OUT for season / OUT ~3 weeks / questionable Sat /
  probable; source`. The writeup's P2 lists everyone expected to miss the upcoming
  game with a timeline; day-to-day starters get named when a beat writer has flagged
  them. (This structured status also future-proofs a site injury widget.)
- **Beat predictions.** New extraction task in in_season prompt: when a pre-fetched
  article or podcast makes a game prediction (pick and/or score), capture it. New
  JSON field so the prose and the site can both use it:

```json
"beat_predictions": [
  {"source": "outlet", "prediction": "Georgia 31-24", "url": "https://...", "published": "YYYY-MM-DD"}
]
```

- **Vibes are signal.** Fan/beat sentiment ("this staff has lost the locker room,"
  "quiet confidence after the bye") is exactly what a national reader can't get
  elsewhere. `overall_sentiment` / Reddit weighting rules unchanged; the writeup
  should *voice* the mood, attributed loosely ("the beat's read is...", "the fanbase
  has moved from X to Y").
- **Team notes.** `notes_block` (manually curated, ~biweekly per team, often more)
  stays injected and gets explicit prompt weight in-season: treat recent notes as
  editor guidance — storylines the editor wants tracked — and prefer them when
  choosing which angle leads. Notes newer than the last run should be checked
  against what the sources say this cycle.
- **Recency:** in_season recency floor is already 21 days; for the `postgame` run,
  instruct the agent to weight sources published after kickoff of the last game most
  heavily.

---

## 7. Cadence & scheduling

Target: **run 1 the morning after each team's game; run 2 on Thursday.** Most teams,
most weeks: Sunday + Thursday. Manual single-team runs any day (already supported:
`run_pipeline.py --team <slug>` — slug, not display name).

This breaks the current conference-unit dispatch (`cron_team_research.sh` in_season:
fixed conference groups Sun–Fri). Replace in_season dispatch with **game-aware
dispatch** — which the script header already earmarks as the 2027 direction:

- **Postgame batch (daily):** teams whose game completed the previous day (query
  `games` for final scores dated yesterday). Sundays this is ~100+ teams; Wed/Thu
  mornings it's the MACtion handful; many days it's zero.
- **Preview batch (Thursday):** all FBS teams with a game in the next 7 days (skips
  teams on bye whose next game is beyond the window — see §9... actually see below).
- Slot mechanics stay: the batch splits across the existing 5 slots. **Load flag:**
  Sunday (~110 teams) and Thursday (~130 teams) are well above today's 69-team max
  day at ~4 min/team (~7–9 h of runtime). Options, pick at implementation time:
  add slots (e.g. hourly), run 2 pipeline processes per slot, or shave per-team time
  (in_season roster caps already help). YouTube quota (10k/day, shared with national)
  needs a check at 110-team scale.
- **Mid-week-game teams (suggestion):** a Tuesday-game team gets its preview run
  Thursday — 5 days before its next game, before the beat has flipped to preview
  mode. Consider keying the preview run to *game day minus 2* instead of a fixed
  Thursday. Fixed Thursday everywhere is fine for v1; note the tradeoff.

---

## 8. Enforcement — hard limits that actually hold

Prompt instructions alone won't hold a 200/350 hard limit. Add to the pipeline
(pattern: `check_research.py`):

1. After the agent writes the JSON: validate `weekly_writeup.text` — exactly 2
   paragraphs (`\n\n` split), word count in [200, 350], `word_count` field matches.
2. On failure: one retry with a corrective suffix ("your writeup was N words;
   rewrite to 200–350 words, two paragraphs, cutting X"). On second failure: accept
   but log loudly (a slightly-long writeup beats a missing one) — site can render
   regardless.
3. Validator also rejects: paragraph 1 not mentioning the last game (postgame runs),
   markdown headers/bullets inside `text` (prose only).

---

## 9. Edge cases

- **Bye week.** No postgame trigger fires (no game yesterday). Thursday preview run
  still executes: P1 becomes a season-to-date stock-taking (record, trajectory, what
  the stats say the team is), P2 previews the next game with extra runway plus the
  next-week peek. Beat coverage in byes (rest, self-scouting, "getting healthy")
  is the P2 texture.
- **Week 1 / opener.** No game played yet: first in_season run is preview-only —
  P1 = camp finale + season expectations (preseason threads compress), P2 = opener
  preview. Weeks 1–2 stats use the prior-year CONTEXT-ONLY gate.
- **AP/CFP:** CFP ranks don't exist before ~Week 10 — the rank-priority rule
  (CFP > AP > power rating) already handles absence; snapshot builder emits null.
- **Postseason mode:** `weekly_writeup` continues through postseason for teams still
  playing (bowl/CFP opponent becomes "this week"); the postseason manual team list
  already gates who runs Mon+Thu.
- **Canceled/postponed games:** postgame dispatcher keys on final scores, so a
  no-contest simply doesn't trigger; Thursday run carries the story.

---

## 10. Suggestions (beyond the ask — take or leave)

1. **Beat-prediction accountability.** Storing `beat_predictions` structured (§6)
   enables a fun site feature later: "the beat is 8-2 picking Iowa games this year."
   Zero extra agent cost now; big differentiator potential.
2. **Structured availability list** alongside prose (extend `injury_flags` with a
   `game_status` enum) → future injury-report widget on teamprofile, and it keeps
   the prose honest.
3. **Ranking movement as narrative fuel.** Inject last week's power-rating/SP+ rank
   next to this week's (`#38 → #29`) — movement is inherently narrative and the
   powerrating table has the history.
4. **`weekly_writeup` archive.** Keep each week's writeup (keyed by season_week)
   instead of overwriting — a team's writeup trail IS the season story, and it's
   free content for a "season in review" page. Cheap: the memory DB or a dated JSON
   sidecar.
5. **Sunday teaser → Thursday full.** If Sunday load forces cuts, an alternative
   shape: postgame run updates only P1 + injuries (shorter), Thursday run writes the
   full two-paragraph piece. Keeps Sunday cost down. (Not the locked decision —
   listed only as the fallback if load bites.)

---

## 11. Implementation order (when build starts)

1. Data layer: `build_current_season_advanced_stats` (widened per §5) + current-season
   record/scoring builders + `build_opponent_snapshots` — plus the VERIFY sweep
   (weekly cadence of powerrating/SandPratings/team_rankings/advancedstats 2026 rows;
   AP/CFP source; spread source; PPD/red-zone source decision).
2. `research_agent.py`: in_season prompt section — weekly_writeup field spec + two-
   paragraph structure + run_type emphasis + beat_predictions task + injury
   availability extension. Mode-gated injection blocks per the close_games pattern.
3. Validator (§8) wired into `run_pipeline.py`.
4. `cron_team_research.sh`: game-aware in_season dispatch + `--run-type` plumbing.
5. Site-side: `functions.php`/`classTeams.php` map + teamprofile render + fallback.
6. Force `mode="in_season"` dry-runs on 2-3 teams (P4 Saturday team, MACtion team,
   bye-week team) before the Aug 29 boundary.

**Constraints to preserve:** no bash-sandbox git on the Windows mounts; Edit-tool
tail-check on CRLF files (verify via Read on C:\ paths, not the laggy bash mirror);
PHP 7.4 on site-side; surgical paste-ready changes; keep mode boundaries in sync
across cron script, research_agent.py, national_landscape_agent.py, and
classTeams.php `_researchModeAt`.
