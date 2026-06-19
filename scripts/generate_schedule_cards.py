#!/usr/bin/env python3
"""
generate_schedule_cards.py
==========================

Render the 1080×1350 Schedule Card PNG for one FBS team, one conference,
or all 136 by screenshotting ``schedulecard.php`` with headless Chromium
(Playwright).

Why a screenshot and not a Pillow re-render
-------------------------------------------
``schedulecard.php`` is the single source of truth for the card layout,
and it rasterizes client-side via html2canvas — which n8n can't trigger.
Re-implementing the layout in Pillow (like generate_team_cards.py does
for the coach card) would mean maintaining the design in two places that
drift. Instead we load the real page at native size and screenshot the
``.sc-card`` element, so the output is pixel-identical to the on-page
"Download PNG" button. Playwright + headless chromium is already
installed and proven on the VPS (see scrape_team_context.py and
check_schedule_outlook.py), so this adds no new infrastructure.

The team list (school, slug, conference_abbr) comes from the same shim
generate_team_cards.py uses (``/api/coach_card_stats.php``), so the slugs
match the established convention used by the teamcards subdomain.

Usage
-----
    # All FBS teams
    python3 scripts/generate_schedule_cards.py --year 2026 --all

    # One team
    python3 scripts/generate_schedule_cards.py --year 2026 --team "Texas A&M"

    # One conference (abbr or full name; same matching as generate_team_cards.py)
    python3 scripts/generate_schedule_cards.py --year 2026 --conf SEC

    # Write straight into the teamcards subdomain docroot on the VPS so the
    # n8n workflow can download them. (DNS-only subdomain — Cloudflare's Bot
    # Fight Mode breaks remote image fetchers, so we never host these on www.)
    python3 scripts/generate_schedule_cards.py --year 2026 --all \\
        --out /var/www/teamcards.puntandrally.com/schedule

Output files: ``{out}/{team_slug}.png`` — flat layout; the slug is globally
unique, so no per-conference subfolders are needed. The n8n workflow builds
its image_url the same way: ``…/schedule/{team_slug}.png``.

API key resolution (first non-empty wins): --api-key → X_API_KEY env →
.env at repo root → DEFAULT_API_KEY constant below. Same order as
generate_team_cards.py.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests
from PIL import Image
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

API_BASE  = "https://www.puntandrally.com/api/coach_card_stats.php"
SITE_BASE = "https://www.puntandrally.com"

CARD_W, CARD_H = 1080, 1350          # native schedule-card dimensions
SUPERSAMPLE    = 2                    # render at 2x then downscale → crisp text

# Paste your shim key between the quotes to skip --api-key on every run.
# Same convenience/secret tradeoff documented in generate_team_cards.py:
# fine for a private repo on a personal VPS; scrub before going public.
DEFAULT_API_KEY = ""

# ---------------------------------------------------------------------------
# API key resolution (copied from generate_team_cards.py for a standalone script)
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
# Team list — reuse the coach_card_stats shim (slugs match the convention)
# ---------------------------------------------------------------------------

@dataclass
class TeamRef:
    school: str          # ?team= param value (canonical school name)
    slug: str            # output filename + n8n source_id / url
    conf_abbr: str       # informational; kept for logging / future grouping
    conference: str      # full conference name (for --conf matching)

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())

def fetch_team_list(api_key: str, year: int,
                    team: Optional[str], conf: Optional[str]) -> list[TeamRef]:
    headers = {"X-API-Key": api_key}

    if team:
        r = requests.get(API_BASE,
                         params={"action": "team", "school": team, "year": year},
                         headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"API error for {team}: {data.get('error')}")
        t = data["team"]
        return [TeamRef(school=t["school"], slug=t["team_slug"],
                        conf_abbr=t.get("conference_abbr") or "",
                        conference=t.get("conference") or "")]

    r = requests.get(API_BASE,
                     params={"action": "all", "year": year},
                     headers=headers, timeout=120)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"API error on action=all: {data.get('error')}")
    teams = [TeamRef(school=t["school"], slug=t["team_slug"],
                     conf_abbr=t.get("conference_abbr") or "",
                     conference=t.get("conference") or "")
             for t in data["teams"]]

    if conf:
        want = _norm(conf)
        def matches(tr: TeamRef) -> bool:
            for c in (_norm(tr.conf_abbr), _norm(tr.conference)):
                if not c:
                    continue
                if want == c:
                    return True
                if want.startswith(c) and want[len(c):].isdigit():
                    return True
                if c.startswith(want) and c[len(want):].isdigit():
                    return True
            return False
        teams = [t for t in teams if matches(t)]
        if not teams:
            raise RuntimeError(
                f"--conf '{conf}' matched zero teams. Try an abbreviation "
                f"(SEC, B1G, ACC, B12, PAC, AAC, MWC, MAC, CUSA, SBC, Ind) "
                f"or the full conference name.")
    return teams

# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# JS that resolves true once every <img> inside .sc-card has finished loading.
# The card pulls team + opponent logos from remote CDNs; networkidle alone
# isn't always enough, so we poll image completeness explicitly.
_IMAGES_READY_JS = """() => {
  const c = document.querySelector('.sc-card');
  if (!c) return false;
  const imgs = Array.from(c.querySelectorAll('img'));
  return imgs.every(i => i.complete && i.naturalHeight > 0);
}"""

def card_url(school: str, year: int) -> str:
    # fullsize=1 sets the card transform to scale(1.0) so it renders at the
    # native 1080×1350 (the on-page default is scaled to 0.52 for preview).
    return (f"{SITE_BASE}/schedulecard.php"
            f"?team={quote(school)}&theyear={year}&fullsize=1")

def render_one(page, tr: TeamRef, year: int, out_dir: Path) -> Path:
    page.goto(card_url(tr.school, year), wait_until="networkidle", timeout=45000)
    page.wait_for_selector(".sc-card", timeout=15000)
    # Fonts (Bebas Neue / Barlow) must be ready or text reflows after capture.
    try:
        page.evaluate("document.fonts ? document.fonts.ready : null")
    except Exception:
        pass
    page.wait_for_function(_IMAGES_READY_JS, timeout=20000)
    time.sleep(0.4)  # final settle for layout/paint

    el = page.query_selector(".sc-card")
    if el is None:
        raise RuntimeError(".sc-card not found on page")
    png_bytes = el.screenshot(type="png")

    # Downscale the supersampled capture to the standard 1080×1350.
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    if img.size != (CARD_W, CARD_H):
        img = img.resize((CARD_W, CARD_H), Image.LANCZOS)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{tr.slug}.png"
    img.save(path, format="PNG", optimize=True)
    return path

def render_all(teams: list[TeamRef], year: int, out_dir: Path) -> int:
    failures = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1700},   # ≥ card width; card is fixed 1080
            device_scale_factor=SUPERSAMPLE,
        )
        page = context.new_page()
        page.set_default_timeout(45000)

        total = len(teams)
        for i, tr in enumerate(teams, 1):
            # One retry — transient goto/network timeouts are the common
            # failure here, and a fresh attempt almost always clears them.
            for attempt in (1, 2):
                try:
                    path = render_one(page, tr, year, out_dir)
                    print(f"  [{i:>3}/{total}] {path.name}")
                    break
                except (PlaywrightTimeout, Exception) as e:  # noqa: BLE001
                    if attempt == 2:
                        failures += 1
                        print(f"  [{i:>3}/{total}] FAIL {tr.school}: {e}",
                              file=sys.stderr)
                    else:
                        time.sleep(1.5)

        browser.close()
    return failures

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Render schedule cards (1080×1350 PNGs) via headless Chromium.")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--team", help="Single school name (e.g. 'Texas A&M').")
    group.add_argument("--all",  action="store_true", help="Render all FBS teams.")
    group.add_argument("--conf", help="Render all teams in one conference "
                                       "(abbr like SEC/B1G or full name).")
    p.add_argument("--year",    type=int, required=True, help="Season year.")
    p.add_argument("--api-key", default=None,
                   help="X-API-Key for the shim. Optional — see resolution order "
                        "in the module docstring.")
    p.add_argument("--out",     type=Path, default=Path("schedule_cards"),
                   help="Output directory (default: schedule_cards/). On the VPS, "
                        "point this at the teamcards subdomain's schedule/ docroot.")
    return p.parse_args()

def main() -> int:
    args = parse_args()

    api_key = resolve_api_key(args.api_key)
    if not api_key:
        print("FATAL: no API key found. Pass --api-key, set X_API_KEY, add it to "
              ".env at the repo root, or set DEFAULT_API_KEY near the top of this "
              "script.", file=sys.stderr)
        return 1

    try:
        teams = fetch_team_list(api_key, args.year, args.team, args.conf)
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: {e}", file=sys.stderr)
        return 1

    if args.conf:
        print(f"Conference filter '{args.conf}' → {len(teams)} teams.")
    print(f"Rendering {len(teams)} schedule card(s) → {args.out}")

    failures = render_all(teams, args.year, args.out)
    print(f"Done. Success: {len(teams) - failures}/{len(teams)}, "
          f"Failures: {failures}.")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
