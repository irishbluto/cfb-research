# SportSource Analytics "Team Success Ranking" (TSR) — Research Notes

*Compiled 2026-07-15. Context: ACC's new football tiebreaker policy (announced 2026-07-15) uses TSR as step 2 after head-to-head, before commissioner draw.*

## Bottom line

There is no published formula. TSR is proprietary and the weekly values are not released publicly (confirmed by fan-board discussion during the 2025 ACC race — nobody could find a link because none exists). Any model has to be a reconstruction from SportSource's own descriptions of the metric's ancestor, the **Team Rating Score**, which the ACC used as a late-step tiebreaker from 2016 through 2025. "Team Success Ranking" in the 2026 policy occupies the exact same slot ("team rating provided to the league at the conclusion of all regular-season games"), so it is almost certainly the same metric, possibly renamed/refreshed.

## What SportSource has said about the metric

From ESPN's 2016 story (when the ACC first hired SSA for the Atlantic Division tiebreaker), SportSource described the Team Rating Score as:

> "a metric that evaluates all facets of on-field team performance that are highly correlated to team success and combines them into a single comparable value." It combines statistics from offense, defense and special teams and uses "individual statistics that are a mix of raw, tempo-agnostic, opponent-adjusted and efficiency metrics." Conference and nonconference winning percentages, as well as strength of schedule, also factor into the statistic.

Notes: the CFP uses SportSource as its official data platform but does NOT use the team rating score. The metric is computable Saturday night after the regular season (no polls, no committee input) — that's the ACC's stated logistical reason for using it.

## Component evidence

**From SSA's "About the Ratings" page (coachesbythenumbers.com)** — their team/coach rating system:

- Winning percentage is the dominant factor ("first and foremost is winning")
- SRS (Simple Rating System) model used for Strength of Schedule, all FBS + FCS teams
- Offensive performance: scoring offense, third-down efficiency, turnovers lost, relative (opponent-adjusted) scoring offense (points scored as % of opponent's avg points allowed), points per possession, SoS adjustment
- Defensive performance: mirror of the above
- Special teams performance: included, unspecified components
- Bonus points for CFP appearances/wins, conference championships, national titles (career/season accolades — almost certainly excluded from the in-season TSR used for tiebreaking)

**From SSA's public Ranking Tool (sportsourceanalytics.com/rankingtool)** — the stat menu they consider the building blocks of a team ranking (sliders over rank-based components):

Winning %, SoS, Scoring O/D, Rushing O/D, Passing O/D, Total O/D, Turnover Margin, Opp-Adjusted Scoring O/D, Opp-Adjusted Total O/D, Points-Per-Possession O/D, Third Down Efficiency O/D, Yards Per Play O/D, Time of Possession.

**ACC's 2026 framing:** "a body-of-work measure that rewards actual team quality rather than the strength of your opponents' opponents" — explicit contrast with the old conference-opponents-win-% tiebreaker that put Duke in the 2025 title game. Phillips: 10,000 simulated seasons run to validate.

## Usage elsewhere (calibration hooks)

- ACC: tiebreaker step 5/6 (Team Rating Score) 2016–2025; step 2 (TSR) from 2026
- Mountain West: CCG participants use a composite avg of Connelly SP+, ESPN SOR, KPI, and SportSource rankings
- American: final tiebreaker uses a composite incl. SP+, SOR, KPI, and a SportSource ranking

The company: SportSource Analytics, Indianapolis, acquired by Tracking Football; runs cfbstats.com and the official #CFBPlayoff analytics platform.

## Proposed reconstruction (v1 spec)

Structure it the way SSA describes it — a rank-composite, not a point-margin power rating:

1. **Record block (heaviest):** overall win%, conference win%, nonconference win% — SoS-adjusted. Given ACC principle "no team over-rewarded/penalized by number of conference games," expect win% not win counts.
2. **SoS block:** SRS-based SoS rank (iterative SRS over FBS+FCS; we already have the machinery in powerratings_history-style iteration).
3. **Offense block:** ranks in scoring offense, opp-adjusted scoring offense (pts as % of opp avg allowed), points/possession, 3rd-down %, turnovers lost, yards/play.
4. **Defense block:** mirror ranks.
5. **Special teams block (light):** net punting, FG%, punt/KO return + coverage as proxy.
6. Composite = weighted avg of component ranks → final ordinal ranking of 136 teams.

**Calibration:** no ground-truth TSR values exist publicly. Best available proxy targets, per the writeup's own claim that TSR should rarely disagree with CFP rankings: fit weights to maximize rank agreement with (a) final CFP committee rankings 2014–2025 and (b) ESPN SOR / resume composites, with win% forced dominant. Validate on ACC 6-2-type logjams (e.g., the 2025 five-way tie: model should have put Miami ahead of Duke).

**Watch for:** SSA or the ACC publishing more detail once the season starts (weekly TSR release to schools may leak via beat writers), and whether the metric surfaces on cfbstats.com.

## Sources

- https://sports.yahoo.com/acc-may-turn-to-analytics-company-to-determine-atlantic-division-winner-214205749.html (quotes ESPN 2016, id 17595894)
- https://coachesbythenumbers.com/about/about-the-coaches-ratings/
- https://www.sportsourceanalytics.com/rankingtool/
- https://theacc.com/news/2026/7/15/acc-announces-new-football-championship-tiebreaker-policy.aspx
- https://theacc.com/documents/2026/7/15//ACC_Football_Tiebreaker_Policy_Jully_2026.pdf (policy PDF)
- https://www.espn.com/college-football/story/_/id/49366844/acc-implements-new-tiebreaker-policy-football-title-game
- https://www.cbssports.com/college-football/news/acc-new-tiebreaker-rules-disaster-scenario/
- https://www.profootballnetwork.com/cfb/acc-tiebreakers-everything-to-know/ (2023–25 tiebreaker order w/ Team Rating Score)
- https://virginia.sportswar.com/message_board/football/691a72759c3a0e00134652b6 (confirmation rankings not published)
