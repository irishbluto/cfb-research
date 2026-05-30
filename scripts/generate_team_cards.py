#!/usr/bin/env python3
"""
generate_team_cards.py
======================

Render the 1080×1080 Coach Card PNG for a single FBS team or for all 138.
Direction B v2 design — cream parchment background, head-coach cutout
over a primary-color side blob, six diagnostic stat rows, gold tag bar,
editorial copy in italic, team-color quote band at the bottom.

The script is data-thin by design: it hits the PHP shim at
``/api/coach_card_stats.php`` for everything it needs (stats, brand
colors, head coach, slug pair, cutout URL, light-color flag) and
focuses purely on composition.

Editorial copy (subhead, watch_for, takeaway, quote) doesn't have a
live source yet — the script accepts overrides via CLI or pulls from
a ``team_card_copy.json`` sidecar if you drop one next to the script.
Otherwise placeholder strings render so the layout can be reviewed.

Usage
-----
    # Single team
    python scripts/generate_team_cards.py \\
        --team "Notre Dame" \\
        --year 2026 \\
        --api-key "$X_API_KEY" \\
        --out team_cards/

    # All 138 (writes one PNG per team into --out)
    python scripts/generate_team_cards.py \\
        --all \\
        --year 2026 \\
        --api-key "$X_API_KEY" \\
        --out team_cards/

    # Use a sidecar JSON for editorial overrides
    python scripts/generate_team_cards.py \\
        --team "LSU" --year 2026 --api-key "$X_API_KEY" \\
        --out team_cards/ \\
        --copy scripts/team_card_copy.json

The sidecar shape is:
    {
      "Notre Dame": {
        "subhead":  "The Title Window Is Open",
        "watch_for": "WATCH THE QB ROOM",
        "takeaway": "Defense returns nine starters from #1 unit",
        "quote":    "Best team in South Bend in a generation."
      },
      ...
    }

Output files: ``{out}/{team_slug}_{year}.png``
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

API_BASE = "https://www.puntandrally.com/api/coach_card_stats.php"

CANVAS_W = 1080
CANVAS_H = 1080

# Palette (locked design)
CREAM      = (242, 234, 211)   # #F2EAD3 background
GOLD       = (201, 151,   0)   # #C99700 tag bar / accents
INK        = ( 24,  24,  28)   # near-black for body copy
INK_MUTED  = ( 95,  95, 105)   # muted gray for labels
HAIRLINE   = (180, 170, 145)   # subtle row divider on cream
WHITE      = (255, 255, 255)

# Default editorial copy when no override is provided
PLACEHOLDER_COPY = {
    "subhead":   "[SUBHEAD PLACEHOLDER]",
    "watch_for": "WATCH FOR …",
    "takeaway":  "[TAKEAWAY PLACEHOLDER — single line italic]",
    "quote":     "[QUOTE PLACEHOLDER — bottom band]",
}

# Stat-row labels in their fixed display order. Each tuple is
# (label-text, key-in-payload-stats, formatter-fn).
def _record_display(rec):
    return rec.get("display") if isinstance(rec, dict) else None
def _rank_display(r):
    return f"#{r}" if isinstance(r, int) and r > 0 else "—"
def _ret_display(rp):
    return rp.get("display") if isinstance(rp, dict) else None

STAT_ROWS = [
    ("PROJ RECORD",   "proj_record",  _record_display),
    ("OFFENSE",       "offense_rank", _rank_display),
    ("DEFENSE",       "defense_rank", _rank_display),
    ("TALENT",        "talent_rank",  _rank_display),
    ("RET PROD",      "ret_prod",     _ret_display),
    ("SCH STRENGTH",  "sch_str_rank", _rank_display),
]

# ---------------------------------------------------------------------------
# Font lookup — try a chain of likely paths, first match wins
# ---------------------------------------------------------------------------

# We want: serif (regular, bold, italic, bold-italic) + sans (regular, bold).
# On Ubuntu (VPS) install with:
#   sudo apt install fonts-liberation fonts-dejavu fonts-dejavu-extra
# On Windows the C:\Windows\Fonts paths cover Georgia + Arial out of the box.

FONT_CANDIDATES = {
    "serif_regular": [
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        r"C:\Windows\Fonts\georgia.ttf",
    ],
    "serif_bold": [
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        r"C:\Windows\Fonts\georgiab.ttf",
    ],
    "serif_italic": [
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
        r"C:\Windows\Fonts\georgiai.ttf",
    ],
    "serif_bold_italic": [
        "/usr/share/fonts/truetype/liberation/LiberationSerif-BoldItalic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf",
        r"C:\Windows\Fonts\georgiaz.ttf",
    ],
    "sans_regular": [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ],
    "sans_bold": [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
    ],
}

_font_path_cache: dict[str, str] = {}

def _resolve_font_path(role: str) -> str:
    if role in _font_path_cache:
        return _font_path_cache[role]
    for candidate in FONT_CANDIDATES[role]:
        if Path(candidate).is_file():
            _font_path_cache[role] = candidate
            return candidate
    raise FileNotFoundError(
        f"No font found for role '{role}'. Install fonts or extend "
        f"FONT_CANDIDATES. Tried: {FONT_CANDIDATES[role]}"
    )

def font(role: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_resolve_font_path(role), size=size)

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def hex_to_rgb(h: Optional[str], fallback=(60, 60, 70)) -> tuple[int, int, int]:
    if not h:
        return fallback
    s = h.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return fallback
    try:
        return tuple(int(s[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return fallback

def luminance(rgb: tuple[int, int, int]) -> float:
    def lin(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else math.pow((c + 0.055) / 1.055, 2.4)
    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

def readable_on_cream(rgb: tuple[int, int, int]) -> bool:
    # Mirrors PHP is_readable_on_light(). Cutoff tuned for #F2EAD3 bg.
    return luminance(rgb) <= 0.72

# ---------------------------------------------------------------------------
# Network — stats shim + cutout download (with on-disk cache)
# ---------------------------------------------------------------------------

@dataclass
class FetchedPayload:
    school: str
    payload: dict

def fetch_team(api_key: str, school: str, year: int) -> FetchedPayload:
    r = requests.get(
        API_BASE,
        params={"action": "team", "school": school, "year": year},
        headers={"X-API-Key": api_key},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"API error for {school}: {data.get('error')}")
    return FetchedPayload(school=school, payload=data["team"])

def fetch_all(api_key: str, year: int) -> list[FetchedPayload]:
    r = requests.get(
        API_BASE,
        params={"action": "all", "year": year},
        headers={"X-API-Key": api_key},
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"API error on action=all: {data.get('error')}")
    return [FetchedPayload(school=t["school"], payload=t) for t in data["teams"]]

def fetch_cutout(url: Optional[str], cache_dir: Path) -> Optional[Image.Image]:
    """Download cutout PNG, cache on disk, return as RGBA. None on 404."""
    if not url:
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / Path(url).name
    if not cached.exists():
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            cached.write_bytes(r.content)
        except requests.RequestException as e:
            print(f"  ! cutout fetch failed for {url}: {e}", file=sys.stderr)
            return None
    try:
        return Image.open(cached).convert("RGBA")
    except Exception as e:
        print(f"  ! cutout open failed for {cached}: {e}", file=sys.stderr)
        return None

# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def render_background(canvas: Image.Image) -> None:
    canvas.paste(CREAM, (0, 0, CANVAS_W, CANVAS_H))

def render_side_blob(canvas: Image.Image, primary_rgb: tuple[int, int, int]) -> None:
    """
    Right-side blob filled with primary brand color. Approximated as a
    soft-edged rounded rectangle sweeping from the right edge inward,
    with the top/bottom angled slightly for a banner feel.
    """
    blob_w = 500
    blob_h = 820
    blob_x0 = CANVAS_W - blob_w + 20
    blob_y0 = 130
    # Build the blob on its own layer so we can soft-feather the inner edge.
    layer = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(
        (blob_x0, blob_y0, blob_x0 + blob_w, blob_y0 + blob_h),
        radius=80,
        fill=(*primary_rgb, 255),
    )
    canvas.alpha_composite(layer)

def render_coach_cutout(
    canvas: Image.Image,
    cutout: Optional[Image.Image],
    helmet_logo_url: Optional[str],
    cache_dir: Path,
) -> None:
    """
    Drop the rembg-cut coach over the side blob. If no cutout exists,
    fall back to a centered helmet logo. If neither, leave blob plain.
    """
    target_h = 760
    if cutout is not None:
        ratio = target_h / cutout.height
        new_w = int(cutout.width * ratio)
        scaled = cutout.resize((new_w, target_h), Image.LANCZOS)
        # Anchor: bottom edge sits ~95px from canvas bottom; centered in blob
        blob_center_x = CANVAS_W - 250
        x = blob_center_x - new_w // 2
        y = CANVAS_H - 95 - target_h
        canvas.alpha_composite(scaled, (x, y))
        return

    # Fallback to helmet logo
    if helmet_logo_url:
        helmet = fetch_cutout(helmet_logo_url, cache_dir)
        if helmet is not None:
            helmet_h = 380
            ratio = helmet_h / helmet.height
            new_w = int(helmet.width * ratio)
            scaled = helmet.resize((new_w, helmet_h), Image.LANCZOS)
            x = CANVAS_W - 250 - new_w // 2
            y = 280
            canvas.alpha_composite(scaled, (x, y))

def render_team_name(
    canvas: Image.Image,
    payload: dict,
    primary_rgb: tuple[int, int, int],
    alt_rgb: tuple[int, int, int],
) -> None:
    """Big serif team name in primary brand (or alt if primary too light)."""
    name = (payload.get("school") or "").upper()
    color = primary_rgb if readable_on_cream(primary_rgb) else alt_rgb

    d = ImageDraw.Draw(canvas)
    # Auto-fit: shrink font until it fits in 560px width
    max_w = 560
    size = 96
    f = font("serif_bold", size)
    while d.textlength(name, font=f) > max_w and size > 56:
        size -= 4
        f = font("serif_bold", size)
    d.text((60, 60), name, font=f, fill=(*color, 255))

def render_tag_bar(canvas: Image.Image, payload: dict, year: int) -> None:
    """Gold pill bar reading '{YEAR} {CONF} DIAGNOSTIC'."""
    conf = (payload.get("conference_abbr") or "FBS").upper()
    text = f"{year}  {conf}  DIAGNOSTIC"

    d = ImageDraw.Draw(canvas)
    f = font("sans_bold", 22)
    pad_x = 16
    pad_y = 8
    text_w = d.textlength(text, font=f)
    bar_w = int(text_w + pad_x * 2)
    bar_h = 40
    x0, y0 = 60, 200
    d.rounded_rectangle(
        (x0, y0, x0 + bar_w, y0 + bar_h),
        radius=6,
        fill=(*GOLD, 255),
    )
    d.text((x0 + pad_x, y0 + pad_y - 1), text, font=f, fill=(*WHITE, 255))

def render_subhead(canvas: Image.Image, copy_block: dict) -> None:
    subhead = copy_block.get("subhead") or PLACEHOLDER_COPY["subhead"]
    d = ImageDraw.Draw(canvas)
    f = font("serif_italic", 32)
    d.text((60, 260), subhead, font=f, fill=(*INK, 255))

def render_stat_rows(canvas: Image.Image, payload: dict) -> None:
    stats = payload.get("stats") or {}
    d = ImageDraw.Draw(canvas)
    label_f = font("sans_bold", 22)
    value_f = font("serif_bold", 40)

    x_label = 60
    x_value_right = 600  # right-align the value
    y_top = 330
    row_h = 60

    for i, (label, key, formatter) in enumerate(STAT_ROWS):
        y = y_top + i * row_h
        # Row divider
        if i > 0:
            d.line(
                [(x_label, y - 6), (x_value_right + 20, y - 6)],
                fill=(*HAIRLINE, 255),
                width=1,
            )
        d.text((x_label, y + 8), label, font=label_f, fill=(*INK_MUTED, 255))
        value_str = formatter(stats.get(key)) or "—"
        value_w = d.textlength(value_str, font=value_f)
        d.text(
            (x_value_right - value_w, y - 4),
            value_str,
            font=value_f,
            fill=(*INK, 255),
        )

def render_takeaway(canvas: Image.Image, copy_block: dict) -> None:
    takeaway = copy_block.get("takeaway") or PLACEHOLDER_COPY["takeaway"]
    d = ImageDraw.Draw(canvas)
    # Gold accent stripe
    d.rectangle((60, 720, 68, 760), fill=(*GOLD, 255))
    f = font("serif_italic", 26)
    d.text((84, 720), takeaway, font=f, fill=(*INK, 255))

def render_watch_for(canvas: Image.Image, copy_block: dict) -> None:
    watch = copy_block.get("watch_for") or PLACEHOLDER_COPY["watch_for"]
    d = ImageDraw.Draw(canvas)
    # Bullseye glyph (concentric circles)
    cx, cy = 80, 800
    d.ellipse((cx - 14, cy - 14, cx + 14, cy + 14), outline=(*GOLD, 255), width=3)
    d.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), fill=(*GOLD, 255))
    f = font("sans_bold", 22)
    d.text((cx + 24, cy - 14), watch, font=f, fill=(*INK, 255))

def render_nameplate(canvas: Image.Image, payload: dict) -> None:
    """
    Brushstroke-style nameplate over the coach cutout bottom.
    v1: a slightly-tilted gold band with a subtle drop shadow, white
    serif coach name centered inside. Easy to swap for a real
    brushstroke texture PNG later.
    """
    head_coach = payload.get("head_coach") or "—"

    plate_w = 540
    plate_h = 80
    # Position it bottom-right area, just above the quote band
    cx = CANVAS_W - 250
    cy = 870

    # Layer for rotation/shadow
    layer = Image.new("RGBA", (plate_w + 60, plate_h + 60), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    # Drop shadow
    ld.rounded_rectangle((24, 24, 24 + plate_w, 24 + plate_h), radius=6, fill=(0, 0, 0, 90))
    layer = layer.filter(ImageFilter.GaussianBlur(radius=6))
    ld = ImageDraw.Draw(layer)
    # Gold band
    ld.rounded_rectangle((20, 20, 20 + plate_w, 20 + plate_h), radius=6, fill=(*GOLD, 255))
    # Coach name
    f = font("serif_bold_italic", 36)
    text_w = ld.textlength(head_coach, font=f)
    ld.text(
        (20 + (plate_w - text_w) / 2, 20 + (plate_h - 36) / 2 - 6),
        head_coach,
        font=f,
        fill=(*WHITE, 255),
    )
    # Slight tilt
    layer = layer.rotate(-3, resample=Image.BICUBIC, expand=True)
    px = cx - layer.width // 2
    py = cy - layer.height // 2
    canvas.alpha_composite(layer, (px, py))

def render_quote_band(
    canvas: Image.Image,
    payload: dict,
    copy_block: dict,
    primary_rgb: tuple[int, int, int],
    alt_rgb: tuple[int, int, int],
) -> None:
    """Bottom band with italic takeaway-style quote + @PuntandRally."""
    band_h = 140
    band_color = primary_rgb if readable_on_cream(primary_rgb) else alt_rgb
    d = ImageDraw.Draw(canvas)
    d.rectangle((0, CANVAS_H - band_h, CANVAS_W, CANVAS_H), fill=(*band_color, 255))

    quote = copy_block.get("quote") or PLACEHOLDER_COPY["quote"]
    f_quote = font("serif_italic", 28)
    f_handle = font("sans_bold", 20)
    # Truncate quote if too long
    max_w = 800
    text_w = d.textlength(quote, font=f_quote)
    while text_w > max_w and len(quote) > 10:
        quote = quote[:-4] + "…"
        text_w = d.textlength(quote, font=f_quote)
    d.text((60, CANVAS_H - band_h + 30), f"“{quote}”", font=f_quote, fill=(*WHITE, 255))
    d.text(
        (CANVAS_W - 60 - d.textlength("@PuntandRally", font=f_handle),
         CANVAS_H - band_h + 90),
        "@PuntandRally",
        font=f_handle,
        fill=(*WHITE, 230),
    )

# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def render_card(
    payload: dict,
    copy_block: dict,
    year: int,
    cache_dir: Path,
) -> Image.Image:
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), CREAM + (255,))
    render_background(canvas)

    colors    = payload.get("colors") or {}
    primary   = hex_to_rgb(colors.get("primary"))
    alt       = hex_to_rgb(colors.get("alt"), fallback=(180, 140, 40))

    render_side_blob(canvas, primary if readable_on_cream(primary) else alt)
    cutout = fetch_cutout(payload.get("cutout_url"), cache_dir)
    render_coach_cutout(canvas, cutout, payload.get("logo"), cache_dir)

    render_team_name(canvas, payload, primary, alt)
    render_tag_bar(canvas, payload, year)
    render_subhead(canvas, copy_block)
    render_stat_rows(canvas, payload)
    render_takeaway(canvas, copy_block)
    render_watch_for(canvas, copy_block)
    render_nameplate(canvas, payload)
    render_quote_band(canvas, payload, copy_block, primary, alt)

    return canvas.convert("RGB")

def load_copy_overrides(path: Optional[Path]) -> dict:
    if not path:
        return {}
    if not path.is_file():
        print(f"  ! copy sidecar not found: {path}", file=sys.stderr)
        return {}
    return json.loads(path.read_text(encoding="utf-8"))

def write_card(img: Image.Image, slug: str, year: int, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{slug}_{year}.png"
    img.save(path, format="PNG", optimize=True)
    return path

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render coach cards (1080×1080 PNGs).")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--team", help="Single school name (e.g. 'Notre Dame').")
    group.add_argument("--all",  action="store_true", help="Render all 138 FBS teams.")
    p.add_argument("--year",    type=int, required=True, help="Season year.")
    p.add_argument("--api-key", required=True, help="X-API-Key for the shim.")
    p.add_argument("--out",     type=Path, default=Path("team_cards"),
                   help="Output directory (default: team_cards/).")
    p.add_argument("--cache",   type=Path, default=Path(".cache/cutouts"),
                   help="Local cutout cache (default: .cache/cutouts/).")
    p.add_argument("--copy",    type=Path, default=None,
                   help="Optional JSON sidecar with per-team editorial overrides.")
    return p.parse_args()

def main() -> int:
    args = parse_args()
    copy_map = load_copy_overrides(args.copy)

    if args.team:
        try:
            fetched = fetch_team(args.api_key, args.team, args.year)
        except Exception as e:
            print(f"FATAL: {e}", file=sys.stderr)
            return 1
        copy_block = copy_map.get(fetched.school, {})
        img = render_card(fetched.payload, copy_block, args.year, args.cache)
        path = write_card(img, fetched.payload["team_slug"], args.year, args.out)
        print(f"Wrote {path}")
        return 0

    # --all
    try:
        all_payloads = fetch_all(args.api_key, args.year)
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 1
    print(f"Rendering {len(all_payloads)} cards…")
    failures = 0
    for i, fetched in enumerate(all_payloads, 1):
        try:
            copy_block = copy_map.get(fetched.school, {})
            img = render_card(fetched.payload, copy_block, args.year, args.cache)
            path = write_card(img, fetched.payload["team_slug"], args.year, args.out)
            print(f"  [{i:>3}/{len(all_payloads)}] {path.name}")
        except Exception as e:
            failures += 1
            print(f"  [{i:>3}/{len(all_payloads)}] FAIL {fetched.school}: {e}",
                  file=sys.stderr)
    print(f"Done. Success: {len(all_payloads) - failures}/{len(all_payloads)}, "
          f"Failures: {failures}.")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
