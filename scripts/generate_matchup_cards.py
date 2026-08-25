#!/usr/bin/env python3
"""
generate_matchup_cards.py
=========================

Render the 1080×1350 Matchup Card PNG for every game sitting in the post queue,
by screenshotting ``matchupcard.php`` with headless Chromium (Playwright).

Forked from generate_schedule_cards.py. Three things changed: the URL builder
(``?game_id=`` instead of ``?team=``), the element selector (``.mc-card``
instead of ``.sc-card``), and the work list — which comes from the POST QUEUE
rather than a team list.

Why the queue and not "this week's games"
-----------------------------------------
matchup_queue_build.php has already decided which ~20 games are worth posting.
Asking the queue means the renderer and the poster can never disagree about
what the week is. It also means a game dropped from the queue stops being
rendered without anyone editing this script.

Why a screenshot and not a Pillow re-render
-------------------------------------------
Same reason as the schedule card: ``matchupcard.php`` is the single source of
truth for the layout and it rasterizes client-side via html2canvas, which
nothing headless can trigger. Screenshotting the real DOM keeps the output
pixel-identical to the page's own "Download PNG" button, and means the design
lives in exactly one place.

Note that the card was DESIGNED around html2canvas's limitations — no
object-fit, no CSS filters (see memory: html2canvas-object-fit-unsupported).
Do not "improve" the card with features Playwright supports but html2canvas
does not, or the two outputs diverge.

Usage
-----
    # Everything currently queued and not yet kicked (the normal run)
    python3 scripts/generate_matchup_cards.py --from-queue \\
        --out /var/www/teamcards.puntandrally.com/matchup

    # Morning refresh: only what posts in the next 14 hours, so the spread on
    # the card matches the market at post time. Lines move all week.
    python3 scripts/generate_matchup_cards.py --from-queue --hours 14 --force \\
        --out /var/www/teamcards.puntandrally.com/matchup

    # One game, for eyeballing
    python3 scripts/generate_matchup_cards.py --game 401858438 --out /tmp/cards

    # A whole queued week, regardless of timing
    python3 scripts/generate_matchup_cards.py --season 2026 --week 3 --out /tmp/cards

By default a card that already exists is SKIPPED. Pass --force to redraw, which
is what the refresh pass does.

Output: ``{out}/{game_id}.png``, served at
``https://teamcards.puntandrally.com/matchup/{game_id}.png``.
NEVER host these on www — Cloudflare Bot Fight Mode breaks Buffer's image fetch.

API key resolution (first non-empty wins): --api-key → X_API_KEY env →
.env at repo root → DEFAULT_API_KEY constant. Same order as the other two
card scripts.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from PIL import Image
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

API_BASE  = "https://www.puntandrally.com/api/matchup_slate.php"
SITE_BASE = "https://www.puntandrally.com"

CARD_W, CARD_H = 1080, 1350          # native matchup-card dimensions
SUPERSAMPLE    = 2                    # render at 2x then downscale → crisp text

DEFAULT_API_KEY = ""

# ---------------------------------------------------------------------------
# API key resolution (copied from generate_schedule_cards.py — standalone)
# ---------------------------------------------------------------------------

def _load_dotenv_value(key: str) -> str:
    """Minimal .env reader (KEY=value, # comments) at the repo root."""
    repo_root = Path(__file__).resolve().parent.parent
    env_path = repo_root / ".env"
    if not env_path.is_file():
        return ""
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""

def resolve_api_key(cli_arg: Optional[str]) -> str:
    for c in (cli_arg or "",
              os.environ.get("X_API_KEY", ""),
              _load_dotenv_value("X_API_KEY"),
              DEFAULT_API_KEY):
        if c:
            return c
    return ""

# ---------------------------------------------------------------------------
# Work list — from the post queue
# ---------------------------------------------------------------------------

@dataclass
class GameRef:
    game_id: int
    away: str
    home: str
    kickoff: str        # ET
    next_post_at: str   # ET

    @property
    def label(self) -> str:
        return f"{self.away} @ {self.home}"

def fetch_games(api_key: str, args: argparse.Namespace) -> list[GameRef]:
    headers = {"X-API-Key": api_key}

    if args.game:
        # Single game bypasses the queue entirely — useful for checking a card
        # that is not queued, or re-drawing one by hand.
        return [GameRef(game_id=args.game, away="", home="", kickoff="", next_post_at="")]

    if args.week:
        params = {"action": "week", "season": args.season, "week": args.week}
    else:
        params = {"action": "queue"}
        if args.hours:
            params["hours"] = args.hours

    r = requests.get(API_BASE, params=params, headers=headers, timeout=60)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"API error: {data.get('error')}")

    return [GameRef(game_id=int(g["game_id"]),
                    away=g.get("away_team", ""),
                    home=g.get("home_team", ""),
                    kickoff=g.get("kickoff", ""),
                    next_post_at=g.get("next_post_at", ""))
            for g in data.get("games", [])]

# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# Resolves true once every <img> inside .mc-card has finished loading. The card
# pulls two ESPN CDN team logos and possibly a TV network logo; networkidle
# alone is not always enough, so poll image completeness explicitly.
#
# Coach cutouts and the P&R logo are same-origin and load fast, but a missing
# cutout is DELIBERATE (see pr_coach_cutout(): a fired coach renders faceless
# rather than wrong). So this checks `complete`, and treats a zero-height image
# as done rather than hanging forever — an image that failed to load is still
# finished loading.
_IMAGES_READY_JS = """() => {
  const c = document.querySelector('.mc-card');
  if (!c) return false;
  const imgs = Array.from(c.querySelectorAll('img'));
  return imgs.every(i => i.complete);
}"""

def card_url(game_id: int) -> str:
    # fullsize=1 sets the card transform to scale(1.0) so it renders at native
    # 1080×1350; the on-page default is scaled to 0.52 for preview.
    # design=news is the default, so it is not passed explicitly.
    return f"{SITE_BASE}/matchupcard.php?game_id={game_id}&fullsize=1"

def render_one(page, g: GameRef, out_dir: Path) -> Path:
    page.goto(card_url(g.game_id), wait_until="networkidle", timeout=45000)
    page.wait_for_selector(".mc-card", timeout=15000)
    # Alfa Slab One / Yellowtail / Old Standard TT / Oswald must be ready or the
    # text reflows after capture.
    try:
        page.evaluate("document.fonts ? document.fonts.ready : null")
    except Exception:
        pass
    page.wait_for_function(_IMAGES_READY_JS, timeout=20000)
    time.sleep(0.4)  # final settle for layout/paint

    el = page.query_selector(".mc-card")
    if el is None:
        raise RuntimeError(".mc-card not found — is matchupcard.php uploaded, "
                           "and did includes/slugs.php go up with it?")
    png_bytes = el.screenshot(type="png")

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    if img.size != (CARD_W, CARD_H):
        img = img.resize((CARD_W, CARD_H), Image.LANCZOS)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{g.game_id}.png"
    img.save(path, format="PNG", optimize=True)
    return path

def render_all(games: list[GameRef], out_dir: Path, force: bool) -> tuple[int, int]:
    failures = 0
    skipped  = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1700},   # ≥ card width; card is fixed 1080
            device_scale_factor=SUPERSAMPLE,
        )
        page = context.new_page()
        page.set_default_timeout(45000)

        total = len(games)
        for i, g in enumerate(games, 1):
            dest = out_dir / f"{g.game_id}.png"
            if dest.exists() and not force:
                skipped += 1
                print(f"  [{i:>3}/{total}] skip {g.game_id}.png (exists; --force to redraw)")
                continue

            # One retry — transient goto/network timeouts are the common failure
            # and a second attempt almost always clears them.
            for attempt in (1, 2):
                try:
                    path = render_one(page, g, out_dir)
                    size = path.stat().st_size
                    print(f"  [{i:>3}/{total}] {path.name}  {size//1024}KB  {g.label}")
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == 2:
                        failures += 1
                        print(f"  [{i:>3}/{total}] FAIL {g.game_id} {g.label}: {e}",
                              file=sys.stderr)
                    else:
                        time.sleep(1.5)

        browser.close()
    return failures, skipped

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Render matchup cards (1080×1350 PNGs) via headless Chromium.")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--from-queue", action="store_true",
                       help="Render every game currently queued and not yet kicked.")
    group.add_argument("--game", type=int,
                       help="Single game_id, queued or not.")
    group.add_argument("--week", type=int,
                       help="Every queued game for one week (needs --season).")

    p.add_argument("--season", type=int, default=0,
                   help="Season year; required with --week.")
    p.add_argument("--hours", type=int, default=0,
                   help="With --from-queue: only games posting within N hours. "
                        "Use 14 for the morning refresh pass so the spread on "
                        "the card matches the market at post time.")
    p.add_argument("--force", action="store_true",
                   help="Redraw cards that already exist. The refresh pass needs this.")
    p.add_argument("--api-key", default=None,
                   help="X-API-Key for the shim. Optional — see resolution order "
                        "in the module docstring.")
    p.add_argument("--out", type=Path, default=Path("matchup_cards"),
                   help="Output directory. On the VPS point this at "
                        "/var/www/teamcards.puntandrally.com/matchup")
    return p.parse_args()

def main() -> int:
    args = parse_args()

    if args.week and not args.season:
        print("FATAL: --week needs --season.", file=sys.stderr)
        return 1

    api_key = resolve_api_key(args.api_key)
    if not api_key and not args.game:
        print("FATAL: no API key found. Pass --api-key, set X_API_KEY, add it to "
              ".env at the repo root, or set DEFAULT_API_KEY near the top of this "
              "script.", file=sys.stderr)
        return 1

    try:
        games = fetch_games(api_key, args)
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: {e}", file=sys.stderr)
        return 1

    if not games:
        print("Nothing queued to render. (Has matchup_queue_build.php run?)")
        return 0

    label = (f"queue, next {args.hours}h" if args.hours
             else f"season {args.season} week {args.week}" if args.week
             else f"game {args.game}" if args.game
             else "queue")
    print(f"Rendering {len(games)} matchup card(s) [{label}] → {args.out}")

    failures, skipped = render_all(games, args.out, args.force)
    drawn = len(games) - failures - skipped
    print(f"Done. Drawn: {drawn}, skipped: {skipped}, failures: {failures}.")

    # PERMISSIONS: if this created the output directory, make sure it is
    # traversable. A 2026-07-14 incident had a recreated card directory come
    # back mode 744 — LiteSpeed 404'd every card while PHP's file_exists()
    # still returned true, and Cloudflare cached those 404s for ~5 minutes.
    try:
        if args.out.is_dir() and (args.out.stat().st_mode & 0o111) != 0o111:
            print(f"WARNING: {args.out} is not traversable by others. "
                  f"Run: chmod 755 {args.out}", file=sys.stderr)
    except OSError:
        pass

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
