# Strength of Record (SOR) — Research + Formula Spec

**Status:** BUILT 2026-07-27 — code in repo, **not yet deployed to Hostinger**, `sql/sor_table.sql` not yet run
**Date:** 2026-07-27
**Decisions locked:** benchmark = **both Top 25 and Top 10**; storage = **new `sor` table, weekly write**

See §7 for what shipped and the deploy checklist. §1–§6 are the original research and design, kept as written.

---

## 1. What is actually public about the formula

### 1.1 The CFP's version (2025 onward) — "Record Strength"

The CFP convened a data analytics panel in the 2025 offseason at the behest of the Management Committee. Two changes came out of it:

1. **Strength of Schedule was re-weighted** — "adjusted to apply greater weight to games against strong opponents."
2. **A new metric, "Record Strength," was added** — it "rewards teams defeating high-quality opponents while minimizing the penalty for losing to such a team. Conversely, these changes will provide minimal reward for defeating a lower-quality opponent while imposing a greater penalty for losing to such a team."

Executive director Rich Clark described it as giving "further credit to how a team performs against its schedule."

**No formula, weights, coefficients, or scale have ever been published.** Every write-up (CFP.com, CBS, PFN, ESPN, Field Level Media) repeats the same four qualitative bullets. There is no public reference implementation to match numerically.

### 1.2 ESPN's version — "Strength of Record" (SOR)

ESPN has published SOR since 2014 and it *is* one of the metrics on the committee's data sheet. Its definition:

> The chance that an **average Top 25 team** would have the given team's record **or better**, against that team's schedule.

Mechanically, per ESPN and the best public reverse-engineering attempt (The Data Jocks):

- Per-game win probabilities come from **FPI**, computed for the *benchmark* team (not the real team) against each opponent, at each site.
- ESPN references **20,000 simulations** of that benchmark team playing the schedule.
- SOR = the share of simulations in which the benchmark team wins at least as many games.
- Lower probability = harder record to achieve = better SOR rank.

FPI itself is proprietary, so an exact numerical match to ESPN is impossible. The *structure* is fully replicable.

### 1.3 The key insight — the two are the same metric

The CFP's four Record Strength bullets are not a separate formula family. They are **exactly what falls out of a win-probability-based record-difficulty metric**, for free:

| CFP language | Falls out of SOR because… |
|---|---|
| Extra credit for beating quality teams | benchmark win prob `p` is low, so a win moves the distribution a lot |
| Less penalty for losing to quality teams | `p` is low, so the benchmark loses that game too in most sims |
| Minimal credit for beating weak teams | `p` ≈ 0.97, benchmark wins it too — no separation |
| Greater penalty for losing to weak teams | `p` ≈ 0.97, so almost no benchmark sim loses it; you fall behind the whole distribution |

**Conclusion: build one engine.** A benchmark-team record-difficulty model satisfies the ESPN definition and the CFP's stated behavior simultaneously. We do not need two metrics — but we get a useful second *display* number cheaply (see §2.4, Wins Above Benchmark).

---

## 2. The formula we will build

### 2.1 Notation

For team **T** in season **Y** through week **w**:

- Completed games `g = 1 … N`, actual wins `W`
- Opponent of game `g` is `O_g`, played at site `s_g ∈ {home, away, neutral}`
- `P(x)` = **combined power** of team `x` = `(SandPratings.rating_overall + powerrating.rating) / 2`
  — this is already the canonical blend used by `Game::projectGameSpread()` and `Team::getCombinedPowerRankBucket()`. Reuse it; do not invent a third power number.
- `B` = benchmark power (see §2.5)
- `HFA = 2.5` (matches `projectGameSpread`)

### 2.2 Per-game benchmark win probability

For each completed game, compute the spread **as if the benchmark team had played that opponent at that site**:

```
margin_g = B − P(O_g) + hfa_g
    where hfa_g = +2.5 if T played home
                  −2.5 if T played away
                   0.0 if neutral
p_g = winprob(margin_g)
p_g = clamp(p_g, 0.005, 0.995)
```

The clamp matters — without it, FCS games and 60-point mismatches produce `p = 1.0`, which zeroes the whole product and makes SOR degenerate.

### 2.3 Record difficulty — exact Poisson-binomial, no Monte Carlo

ESPN simulates 20,000 times. **We do not need to.** The benchmark's win total is a sum of independent Bernoulli trials with unequal `p` — a Poisson-binomial. The exact distribution comes from a two-line DP:

```
dist = [1.0]                       // dist[k] = P(benchmark wins exactly k)
foreach p_g:
    new = array_fill(0, len(dist)+1, 0.0)
    for k in 0..len(dist)-1:
        new[k]   += dist[k] * (1 - p_g)
        new[k+1] += dist[k] * p_g
    dist = new
```

O(N²) with N ≤ ~13. Exact, deterministic, no RNG, ~microseconds per team. This is strictly better than ESPN's method — no simulation noise, so week-over-week SOR movement is real movement.

Then:

```
P_ge = Σ_{k ≥ W} dist[k]           // benchmark matches or beats the record
P_eq = dist[W]
midp = P_ge − 0.5 · P_eq           // mid-p correction, kills the integer-jump artifact
SOR  = 1 − midp                    // 0..1, higher = more impressive record
```

**Display:** `SOR_score = round(100 × SOR, 1)` and a national `sor_rank`. Higher score = better, which reads correctly on a card. (ESPN publishes the raw probability and ranks ascending; ours is the complement so the card sorts the same direction as every other P&R metric.)

The **mid-p correction** is the one non-obvious piece. Without it, two teams at 10-2 against wildly different schedules can land on the same `P(X ≥ 10)` step. Subtracting half the point mass at exactly `W` smooths the tie and is the standard fix.

### 2.4 Secondary display metric — Wins Above Benchmark (WAB)

Free, from the same `p_g` array:

```
E_bench = Σ p_g                    // benchmark's expected wins on this schedule
WAB     = W − E_bench
```

This is the linear cousin of SOR, and it is the number that will actually get read on a card ("Texas is +1.8 wins above what a Top-25 team would manage here"). Sort order agrees with SOR ~95% of the time; where they disagree is genuinely interesting (very short or very lopsided schedules). Store both.

### 2.5 The benchmark teams

Per your decision, compute **two** benchmarks each week:

```
B_top25 = mean( combined power of the top 25 teams by combined power, season Y )
B_top10 = mean( combined power of the top 10 teams by combined power, season Y )
```

Notes:

- Take the mean **of the combined power blend**, not of `powerrating.rating` alone — otherwise the benchmark is on a different scale than the opponents it is being compared to and every SOR is silently biased.
- Recompute the benchmark **every week** from that week's ratings. It drifts up through the season as ratings separate; that is correct and matches ESPN.
- `B_top25` is the headline (ESPN-faithful). `B_top10` goes in a secondary column — it compresses the top of the board and better separates actual playoff contenders.
- Expect `B_top25` ≈ +16 to +18 and `B_top10` ≈ +21 to +24 on your rating scale. Worth sanity-checking on live data before shipping.

### 2.6 Worked sanity check (illustrative, not live data)

A 11-1 SEC team whose loss came at a Top-5 opponent:
- 12 games, benchmark `p_g` mostly 0.55–0.90, a couple at 0.35
- `E_bench` ≈ 8.9 → `WAB` ≈ +2.1
- `P(X ≥ 11)` ≈ 0.06 → `SOR ≈ 94`

A 12-0 G5 team with an all-cupcake slate:
- `p_g` mostly 0.93–0.99, `E_bench` ≈ 11.4 → `WAB` ≈ +0.6
- `P(X ≥ 12)` ≈ 0.48 → `SOR ≈ 52`

That gap — undefeated G5 landing well below one-loss P4 — is precisely the behavior the committee's metric is designed to produce, and it is the reason SOR is the right thing to build.

---

## 3. Data audit — what we have, what we need

### 3.1 Already in place (no work required)

| Need | Where it lives |
|---|---|
| Opponent power ratings | `powerrating.rating` (current season) + `powerrating_YYYY` archives; `SandPratings.rating_overall` |
| The canonical power blend | `Team::getCombinedPowerRankBucket()` (SQL already computes `combined_overall`) and `Game::projectGameSpread()` |
| Weekly rating history | `powerrating_history` table (see memory: powerratings-history-pipeline) — enables backfilling SOR for past weeks/seasons |
| Game results + sites | `games` table: `home_team`, `away_team`, `home_points`, `away_points`, `neutral_site`, `season`, `week`, `season_type` |
| Home-field constant | `2.5`, hard-coded in `projectGameSpread` |
| A spread→win-prob mapping | `bettrends.suwinper` keyed on `spreadabs`, via `Team::getwinprob()` |
| Weekly per-team write path | `scheduleoutlook.php` already loops every team and writes `schedulebreakdown` — the SOR write hooks into the same loop |
| Page + view-mode scaffolding | `schedulebreakdown.php` mode pills, conference filter, card grid |

**Headline answer to your second question: we need no new external data.** Everything SOR requires is already in the database. That is unusual and it is why this metric is worth building — it is pure derivation off inputs you already maintain weekly.

### 3.2 Gaps that need real work

**A. `getwinprob()` is not fit for this purpose — needs a continuous replacement.**

Three problems:
- It's an **exact-match table lookup** on `bettrends.spreadabs`. Benchmark margins are continuous (`B − P(O) ± 2.5` produces things like 13.87). Every call would need rounding to a half point, and any missing row returns `null` → `p = 0` silently.
- It **caps the spread at 30** and the comment says "SPREAD OVER 20 = 100% Win Rate." A hard 1.0 is fatal to a product-of-probabilities model.
- It's a **DB round trip per game per team per benchmark** — 135 teams × 12 games × 2 benchmarks ≈ 3,200 queries on the weekly run.

**Fix:** add a closed-form `sorWinProb(float $margin): float` to the new include. Standard CFB form:

```
p = Φ(margin / 13.5)          // normal CDF, σ ≈ 13.5 pts for CFB game-to-game margin
```

Calibrate the `13.5` by least-squares against the existing `bettrends.suwinper` curve so it agrees with the numbers already used site-wide, then clamp to `[0.005, 0.995]`. One-time fit, then pure arithmetic — no queries. **This should be its own small task with a before/after table vs `bettrends`.**

**B. FCS opponents need an explicit power constant.**

`projectGameSpread` currently assigns `-21` to any team with no `powerrating` row. On the SOR scale that makes an FCS body-bag game roughly a 92% benchmark win — too generous, and it means an FBS team gets meaningful credit for beating an FCS opponent. The CFP explicitly wants "minimal reward for defeating a lower-quality opponent."

**Recommendation:** define `SOR_FCS_POWER = -30` (independent of the `-21` used for spread projection — different purpose, don't couple them). Sanity-check against actual FBS-vs-FCS results. Also decide explicitly whether FCS games count in `N` at all. My recommendation: **count them**, so scheduling one is mildly punitive rather than free.

**C. Neutral-site and completed-game flags must be trustworthy.**

Per memory (`games-flags-format`): the ESPN importer wrote `Y`/`N` where the code expects `'1'`/`'0'`, which made neutral sites invisible. A backfill ran 2026-07-25 but the **old importer re-ran afterward**, so the fix is not confirmed live. SOR is sensitive to this — a neutral game misread as a home game shifts that game's `p_g` by 5 points of margin.

**Blocking prerequisite:** upload the fixed importer to `public_html/scripts` and re-run the normalize SQL once before the first SOR write.

**D. "Completed game" definition.**

`getTeamWinAveragesRecent()` uses `home_points IS NOT NULL AND away_points IS NOT NULL AND (home_points > 0 OR away_points > 0)`. Reuse that exact predicate so SOR's `N` never disagrees with the record shown elsewhere on the site. Do not roll a new one.

**E. Which week's ratings rate the opponents?**

Two defensible choices:
- **Current ratings** (what ESPN does): rate every past opponent by how good they look *today*. SOR moves when your opponents win or lose. This is the correct, intended behavior.
- Frozen-at-game-time ratings: more "fair," but nobody does it and it kills the week-to-week storyline.

**Go with current ratings.** But this means SOR for week `w` must be recomputed from scratch each week, not incrementally updated — the weekly write is a full recompute of all teams. That is fine (it's microseconds) but it needs saying, because the `schedulebreakdown` write next to it is incremental.

### 3.3 Optional / later

- **Backfill history:** with `powerrating_history` you can compute SOR for every week of past seasons and get "SOR movers" charts and a validation set. Recommend doing this *after* the live version works.
- **Future-schedule SOR:** a projected end-of-season SOR using `expectedwins`. Interesting but a different metric — defer.

---

## 4. Proposed implementation

### 4.1 New file: `includes/sor_model.php`

Pure functions, no page state, testable in isolation:

```php
sorWinProb(float $margin): float                  // calibrated normal CDF + clamp
sorBenchmarkPower(int $year, int $topN): float    // mean combined power of top N
sorPoissonBinomial(array $p): array               // exact win-total distribution
sorForTeam(string $team, int $year, int $week,
           float $benchPower, array $powerMap): array
    // returns: n_games, wins, losses, e_bench, wab, p_ge, sor, per-game detail
```

Load the whole `combined power` map for the season **once** into an array and pass it in — one query, not one per opponent.

### 4.2 New table: `sor`

```sql
CREATE TABLE `sor` (
  `id`            INT AUTO_INCREMENT PRIMARY KEY,
  `team`          VARCHAR(64)  NOT NULL,
  `year`          SMALLINT     NOT NULL,
  `week`          TINYINT      NOT NULL,
  `games`         TINYINT      NOT NULL,
  `wins`          TINYINT      NOT NULL,
  `losses`        TINYINT      NOT NULL,
  `bench_power25` DECIMAL(6,3) NOT NULL,
  `bench_power10` DECIMAL(6,3) NOT NULL,
  `exp_wins25`    DECIMAL(5,3) NOT NULL,
  `exp_wins10`    DECIMAL(5,3) NOT NULL,
  `wab25`         DECIMAL(5,3) NOT NULL,
  `wab10`         DECIMAL(5,3) NOT NULL,
  `sor25`         DECIMAL(6,3) NOT NULL,   -- 0..100, higher = better
  `sor10`         DECIMAL(6,3) NOT NULL,
  `sor25_rank`    SMALLINT     NULL,
  `sor10_rank`    SMALLINT     NULL,
  `updated`       TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY `team_year_week` (`team`,`year`,`week`),
  KEY `year_week_sor` (`year`,`week`,`sor25`)
) COLLATE utf8mb4_general_ci;
```

`COLLATE utf8mb4_general_ci` is required — per memory (`nil-budgets-table`), new tables without it throw error #1267 when joined to `teams`.

Ranks are written in a **second pass** after all teams are computed, since ranking needs the full field.

### 4.3 Write path

Hook into the existing `scheduleoutlook.php` per-team block, guarded by the same `$arewehome || $validWriteKey` condition as the `schedulebreakdown` write. Ranks get assigned by a small follow-up pass (either at the end of the batch run, or a `sor_rank` recompute in `batch_admin.php`).

### 4.4 Page: `schedulebreakdown.php?whatstat=sor`

- New mode pill **"Strength of Record"**, placed first or second (it is the headline metric now).
- Card body: `SOR 94.2` big, then `12-1 · +2.1 WAB · Top-10 SOR 88.6`, and the existing opponent-quality bar kept underneath for context.
- Sort descending by `sor25`.
- Legend swaps to a one-line explainer: *"Chance an average Top-25 team would match this record on this schedule — inverted, so higher is better."*
- Keep the conference filter and P4/G6 pills untouched (pure CSS/JS, mode-agnostic).

### 4.5 Later — per-team panel

On `scheduleoutlook.php` / `teamprofile.php`, a per-game table: opponent, site, benchmark win prob, result, credit contribution. This is the view that makes the metric explainable and is the one worth putting in front of readers.

---

## 5. Open questions to settle before/during build

1. **σ for the win-prob curve** — fit against `bettrends`, don't guess. Needs DB access (localhost-only) or a dump of the `bettrends` table.
2. **FCS constant** — `-30` proposed; validate against actual FBS-vs-FCS margins.
3. **Do FCS games count toward `N`?** Recommend yes.
4. **Conference championship / bowl games** — include, but confirm `season_type` filtering matches what the rest of the site treats as "the record."
5. **Preseason display** — with `N = 0` SOR is undefined. Hide the mode pill when `$preseason` is true (the page already does this for other modes).

---

## 7. What shipped (2026-07-27)

### New files

| File | What it is |
|---|---|
| `includes/sor_model.php` | The whole engine. Pure functions, reads only, everything cached in statics for the request. |
| `sql/sor_table.sql` | The `sor` table. **Must be run once before the first weekly write.** |

Public API:

```php
sor_win_prob($margin, $sigma)          // calibrated normal CDF, clamped
sor_sigma($database)                   // fits sigma against bettrends, cached
sor_power_map($database, $year)        // key => combined power
sor_benchmark($powerMap, $topN)        // mean power of the top N
sor_poisson_binomial(array $p)         // exact win-total distribution
sor_from_probs(array $p, $wins)        // -> sor, p_ge, exp_wins, wab
sor_compute_all($db, $year, $week, $detailFor = null)   // whole field + ranks
sor_load($db, $year, $week)            // stored table, else live compute
sor_get($db, $team, $year, $week)      // one team, name-folded
sor_write($db, $rows, $year, $week)    // upsert into `sor`
sor_fmt() / sor_fmt_wab() / sor_blurb() // display helpers
```

**The fallback matters:** `sor_load()` reads the `sor` table and silently falls back to a live compute when the table is missing or empty for the season. Every consumer page therefore works *before* the table is created and before the first weekly write — a missing table degrades to correct-but-slower, never fatal (the DB layer already swallows ER_NO_SUCH_TABLE).

### Page changes

| Page | Change |
|---|---|
| `schedulebreakdown.php` | New **Strength of Record** mode pill (`?whatstat=sor`), first in the nav. Cards show the SOR score, record, WAB pill, Top-10 pill. Explainer paragraph replaces the tier legend in this mode. Builds its team list from the SOR engine, not the schedulebreakdown rows, so it works in a week where that write hasn't run. |
| `scheduleoutlook.php` | Schedule Strength card gains a **Strength of Record** line directly under Total Opp Power (score + national rank + WAB in muted text). Also carries the weekly `sor` write, under the same `$arewehome \|\| $validWriteKey` guard as the schedulebreakdown write. |
| `teamprofile.php` | `{year} Schedule — Information` card gains an **SOR** line as the first body row, directly beneath the SOS chip. Hidden in preseason. |
| `viewconferencesos.php` | SOR rank badge next to the SOS badge on every team row; SOR loaded once for the page and matched by folded key. |
| `cfp_projections.php` | New **SOR** column in both the field table and the bubble table, after Sched Rank. SOR also enters the in-season seed formula (below). |

### The cfp_projections formula change

In-season `seed_score` was `0.8 · md_rank + 0.2 · rating_rank`. It is now:

```
seed_score = 0.55 · md_rank + 0.20 · rating_rank + 0.25 · sor_rank
```

Preseason and `cfp_rankings` modes are unchanged — preseason has no games for SOR to read, and when the committee's own poll exists it stays authoritative. Teams with no SOR rank fall back to their rating rank so a missing value never pushes a team to the bottom. **The 0.25 weight is a judgement call, not a fitted number** — worth revisiting against real in-season data.

### Verification done

- `php -l` clean on all six files; line endings preserved exactly (4 CRLF files stayed CRLF, `cfp_projections.php` stayed LF, zero bare-LF contamination), tails byte-identical — the [[edit-tool-truncates-crlf-files]] failure mode was checked for explicitly.
- Unit suite: normal CDF accuracy, win-prob symmetry and clamping, Poisson-binomial correctness, **DP cross-checked against a 400,000-run Monte Carlo (max abs difference < 0.003)** — that is the evidence for skipping ESPN's simulation approach.
- Behavioural suite: harder schedule outscores easier at equal record; monotone in wins; adjacent win totals stay distinct (mid-p working); all four CFP Record Strength behaviours confirmed to emerge rather than being coded.
- E2E against a stub DB: sigma fit recovers a known generating value, FBS filter, both sides of every game counted, legacy `'Y'` neutral flag honoured, FCS fallback, rank uniqueness, per-game credit column summing to WAB, upsert SQL shape.
- Rendered every `schedulebreakdown.php` mode under `E_ALL` — zero notices, and the six pre-existing modes are unregressed.

**One real bug caught and fixed during rendering:** PHP 8 dropped the sign from `number_format(-0.04, 1)`, so a small negative WAB rendered as an unsigned `0.0`. Sign is now taken from the rounded value and applied by hand; values that round to zero print unsigned. Regression tests pinned.

### Deploy checklist

1. **Run `sql/sor_table.sql`** on the live DB. Until then every page live-computes (correct, just slower).
2. **Confirm the neutral-site flag fix is actually live** ([[games-flags-format]]) — the old importer re-ran after the 2026-07-25 backfill. A neutral game misread as home shifts that game's margin by 5 points.
3. Upload the six changed/new files to Hostinger.
4. **Check the fitted sigma on real data.** `sor_sigma()` grid-searches 8.0–20.0 against `bettrends`; if it rails at an edge it falls back to 13.5 and reports `ok = false`. Log or eyeball it once — the fitted value is not verifiable from here because the DB is localhost-only.
5. **Sanity-check the benchmarks.** Expect `bench_power25` ≈ +16 to +18 and `bench_power10` ≈ +21 to +24 on your rating scale. Anything far off means the combined-power blend isn't lining up.
6. Validate `SOR_FCS_POWER = -30` against real FBS-vs-FCS margins; it's a defined constant at the top of the model, independent of the `-21` `projectGameSpread` uses.

### Still open

- Per-game SOR explainer panel (opponent, site, benchmark win prob, result, credit). The data is already there — `sor_compute_all($db, $y, $w, 'Team Name')` returns it under `gamelist`, and the credit column is tested to sum to WAB. Just needs a view.
- Backfilling past weeks/seasons from `powerrating_history` for SOR-movers charts.
- The 0.25 cfp_projections weight (see above).

---

## 6. Sources

- [CFP — Selection Committee Prepares for 2025-26 Season](https://collegefootballplayoff.com/news/2025/8/20/selection-committee-prepares-for-2025-26.aspx)
- [CBS Sports — Explaining the CFP's new strength of schedule metric](https://www.cbssports.com/college-football/news/explaining-the-college-football-playoffs-new-strength-of-schedule-metric-and-the-secs-campaign-behind-it/)
- [ESPN — CFP selection committee to use enhanced metrics](https://www.espn.com/college-football/story/_/id/46027603/cfp-selection-committee-use-enhanced-metrics)
- [ESPN — How Strength of Record helps determine the CFP field](https://www.espn.com/college-football/story/_/page/weeklyscenario110521/how-strength-record-determine-college-football-playoff-field)
- [Pro Football Network — How SOS and Record Strength will impact CFP rankings](https://www.profootballnetwork.com/cfb/strength-schedule-record-strength-impact-cfp-rankings/)
- [The Data Jocks — Reverse Engineering Strength of Record](https://thedatajocks.com/strength-of-record-college-football/)
