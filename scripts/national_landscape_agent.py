#!/usr/bin/env python3
"""
national_landscape_agent.py
---------------------------
Spawns a Claude Code session to synthesize national CFB sources into a
condensed writeup + key storylines for the Punt & Rally index page.

Reads:  /cfb-research/national/fetched_sources.json (from national_fetcher.py)
        /cfb-research/national/landscape_memory.json (prior run context)
Writes: /cfb-research/national/landscape_latest.json
        /cfb-research/national/landscape_memory.json (updated)

Usage:
    python3 scripts/national_landscape_agent.py
    python3 scripts/national_landscape_agent.py --dry-run
    python3 scripts/national_landscape_agent.py --no-youtube

Also importable:
    from national_landscape_agent import build_prompt, run_agent
"""

import json, os, sys, time, argparse, subprocess, logging
from datetime import datetime
from pathlib import Path

BASE_DIR    = Path("/cfb-research")
NATIONAL_DIR = BASE_DIR / "national"
LOG_DIR     = BASE_DIR / "logs"
CLAUDE_BIN  = "/home/joleary/.local/bin/claude"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging():
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"national_landscape_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ]
    )
    return log_file

# ---------------------------------------------------------------------------
# Research mode (mirrors research_agent.py)
# ---------------------------------------------------------------------------

def get_mode():
    month = datetime.now().month
    if month == 1:
        return "cfb_playoffs", "college football playoffs, postseason, portal window, recruiting, coaching changes"
    elif month in (2, 3):
        return "early_offseason", "portal activity, coaching changes, recruiting, spring practice previews"
    elif month in (4, 5, 6):
        return "spring_offseason", "spring practice results, portal analysis, recruiting, expectations and predictions"
    elif month in (7, 8):
        return "preseason", "fall camp, depth charts, preseason polls, predictions, conference previews"
    else:
        return "in_season", "weekly results, rankings, playoff picture, Heisman race, injury impacts"

# ---------------------------------------------------------------------------
# Load prior memory
# ---------------------------------------------------------------------------

def load_memory():
    memory_file = NATIONAL_DIR / "landscape_memory.json"
    if memory_file.exists():
        try:
            return json.loads(memory_file.read_text())
        except Exception:
            pass
    return {}

# ---------------------------------------------------------------------------
# Build prompt
# ---------------------------------------------------------------------------

def build_prompt(fetched_data, no_youtube=False):
    """Build the Claude agent prompt from fetched sources + memory."""
    mode, mode_focus = get_mode()
    memory = load_memory()

    # Load pre-built prompt blocks if fetched via national_fetcher
    # Or rebuild from raw fetched_sources.json
    written_block = fetched_data.get('written_block', '')
    youtube_block = fetched_data.get('youtube_block', '')
    podcast_block = fetched_data.get('podcast_block', '')
    stats         = fetched_data.get('stats', {})

    # If blocks aren't pre-built (loaded from JSON), rebuild them
    if not written_block:
        written_block, youtube_block, podcast_block = _rebuild_blocks_from_json(fetched_data, no_youtube)

    # Build memory block
    memory_block = ""
    if memory:
        run_label = f"run #{memory.get('run_count', '?')}"
        last_run  = memory.get('last_run', 'unknown')
        prior_storylines = memory.get('prior_storylines', [])
        prior_summary    = memory.get('prior_writeup_summary', '(none)')

        storyline_lines = '\n'.join(f"  - {s}" for s in prior_storylines) if prior_storylines else "  (first run)"

        memory_block = f"""=== PRIOR RUN NOTES ({last_run} — {run_label}) ===
Use these to maintain storyline continuity. Confirm, update, or drop each based on new sources.

Prior writeup summary:
  {prior_summary}

Prior key storylines:
{storyline_lines}
=== END PRIOR RUN NOTES ===

"""

    output_path = str(NATIONAL_DIR / "landscape_latest.json")

    prompt = f"""You are a national college football analyst for Punt & Rally (puntandrally.com), a CFB analytics site covering all 138 FBS teams.

Your task: Synthesize the national CFB sources provided below into a condensed writeup and key storylines — the "what matters in college football right now" view.

## Research Mode: {mode.upper()}
Current focus: {mode_focus}

{memory_block}## Source Material

You have been given content from {stats.get('total', 0)} sources across three categories.
Your job is to READ all of them, identify the stories that matter most, and synthesize.

**CRITICAL — Cross-source frequency is your primary ranking signal.**
When multiple independent sources cover the same topic, that story is more important.
A story covered by Thamel (ESPN), Josh Pate (Late Kick), AND the CFB Enquirer podcast
is a top story. A story mentioned by only one outlet is worth noting but should not lead.

**Source roles — strictly enforced:**
- Sources marked [INPUT ONLY] are paywalled (The Athletic, Bruce Feldman). Use their
  content to inform your analysis, but NEVER include their URLs in the storylines output.
  When a storyline is informed by an INPUT ONLY source, cite a linkable source that covers
  the same topic. If no linkable source covers it, you may still include the storyline
  but with an empty sources array.
- All other sources are both input AND linkable — include their URLs in storylines.

### Written Sources ({stats.get('written_count', 0)} articles)

{written_block}

### YouTube ({stats.get('youtube_count', 0)} videos)

{youtube_block}

### Podcasts ({stats.get('podcast_count', 0)} episodes)

{podcast_block}

## Your Tasks

1. **Read all sources above** — extract the key topics, stories, and themes from each.
   Do NOT fetch any URLs — all content is pre-provided or summarized above.

2. **Identify the 3-5 biggest national stories** by cross-source frequency.
   For each story, note which sources covered it.

3. **Write a national CFB writeup** — 2-4 paragraphs of analyst-quality prose.
   - Lead with the single biggest story (highest cross-source frequency).
   - Cover both P4 and G6 stories — do not default to an all-SEC or all-P4 lens.
   - On quiet weeks (few sources, little new), write 2 paragraphs and don't pad.
   - On heavy weeks, use 3-4 paragraphs to cover the landscape.
   - Write as a knowledgeable, even-handed national CFB analyst.
   - Mode-aware tone: spring/preseason lean toward earned optimism about the sport;
     in-season lean pragmatic with the scoreboard in hand.
   - No generic filler ("the offseason continues to unfold," "as we look ahead").
     Every sentence should carry specific information.

4. **Build the storylines list** — 3-5 items, each with:
   - A concise headline (under 100 chars)
   - A 1-2 sentence summary
   - The list of sources that covered this story (name + URL, linkable only)
   - A source_count (total sources that informed it, including non-linkable)

5. **Write output JSON** to: {output_path}

## Output Format

Write valid JSON matching this exact structure:
{{
  "generated_date": "{datetime.now().strftime('%Y-%m-%d')}",
  "mode": "{mode}",
  "writeup": "2-4 paragraphs of national CFB analysis. Use \\n\\n between paragraphs.",
  "storylines": [
    {{
      "headline": "Short storyline title (under 100 chars)",
      "summary": "1-2 sentence expansion of the headline",
      "source_count": 3,
      "sources": [
        {{"name": "ESPN — Pete Thamel", "url": "https://...", "type": "written"}},
        {{"name": "Josh Pate — Late Kick", "url": "https://youtube.com/...", "type": "youtube"}},
        {{"name": "CFB Enquirer", "url": "https://...", "type": "podcast"}}
      ]
    }}
  ],
  "meta": {{
    "sources_processed": {stats.get('total', 0)},
    "written_count": {stats.get('written_count', 0)},
    "youtube_count": {stats.get('youtube_count', 0)},
    "podcast_count": {stats.get('podcast_count', 0)},
    "agent_notes": "Optional: note if sources were sparse, or if a major story dominated everything"
  }}
}}

## Important Instructions

**No URL fetching.** All source content is provided above. Do NOT use fetch_url, web_search,
or any tool to retrieve content. Read what's given and synthesize.

**Storyline continuity:** If prior run notes list storylines, check whether your current
sources update, confirm, or contradict them. Use similar language/keywords for ongoing
stories so the memory system can match them across runs. Drop resolved stories naturally.

**Conference terminology (2026):**
- Power Four (P4): SEC, Big Ten, ACC, Big 12, plus Notre Dame (FBS Ind) — 69 teams
- Group of Six (G6): PAC-12, AAC, Sun Belt, MWC, MAC, CUSA, UConn — 69 teams
- PAC-12 is G6 in 2026 — never call it Power Four
- Never use "G5" — always "G6"
- No conference divisions (SEC East, Big Ten West, etc.) — eliminated

**Transfer portal (2026 rule change):** Single window in early January. No post-spring
portal window. Do not write that players are "expected to enter the portal" during
spring/summer/in-season — that framing is factually wrong under 2026 rules.

**No superlatives without a source:** "Most significant," "largest," "most dominant" —
these require a source making that exact claim. Cut them if you don't have one.

**FBS season length:** Regular season is 12 games. PAC-12 has 11 scheduled with a
flexible 12th TBD in 2026. Never write "13-game schedule."

**Source attribution:** In the writeup, you may reference sources naturally
("as Pete Thamel reported for ESPN" or "Josh Pate noted on Late Kick") but only
for linkable sources. Never name-drop Athletic or Feldman writers in reader-facing text.

**Output quality:** Write the JSON file immediately — no trailing commas, no comments.
If sources are sparse, say so in meta.agent_notes and write a shorter 2-paragraph piece.
"""
    return prompt, mode

# ---------------------------------------------------------------------------
# Rebuild prompt blocks from cached fetched_sources.json
# ---------------------------------------------------------------------------

def _rebuild_blocks_from_json(data, no_youtube=False):
    """
    When running standalone (not piped from national_fetcher), rebuild
    the prompt blocks from the raw fetched_sources.json structure.
    """
    # Import the block builders from national_fetcher
    sys.path.insert(0, str(BASE_DIR / "scripts"))
    from national_fetcher import _build_written_block, _build_youtube_block, _build_podcast_block

    raw = data.get('output', data)  # handle both wrapper and raw
    days = raw.get('days', 7)

    written_block = _build_written_block(raw.get('written', []), days)
    youtube_block = _build_youtube_block(raw.get('youtube', []), no_youtube)
    podcast_block = _build_podcast_block(raw.get('podcasts', []), days)

    return written_block, youtube_block, podcast_block

# ---------------------------------------------------------------------------
# Run Claude agent
# ---------------------------------------------------------------------------

def run_agent(prompt, dry_run=False):
    """Spawn a Claude Code session with the national landscape prompt."""
    if dry_run:
        print(f"\n{'='*60}")
        print(f"DRY RUN — prompt length: {len(prompt)} chars")
        print(prompt[:800] + "..." if len(prompt) > 800 else prompt)
        return True

    # Strip null bytes (safety — same as research_agent.py)
    prompt = prompt.replace('\x00', '')

    # Write prompt to log file for debugging
    prompt_file = LOG_DIR / "prompt_national_landscape.txt"
    prompt_file.write_text(prompt)

    cmd = [
        CLAUDE_BIN, "--dangerously-skip-permissions",
        "-p", prompt,
    ]

    logging.info(f"Running Claude agent (prompt: {len(prompt)} chars)")
    start = time.time()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,   # 10 minute timeout (single synthesis, not per-team)
            cwd=str(BASE_DIR),
        )
        elapsed = round(time.time() - start, 1)

        if result.returncode == 0:
            output_file = NATIONAL_DIR / "landscape_latest.json"
            if output_file.exists():
                try:
                    with open(output_file) as f:
                        data = json.load(f)
                    logging.info(f"  ✓ Valid JSON written ({elapsed}s)")
                    # Update memory after successful run
                    _update_memory(data)
                    return True
                except json.JSONDecodeError as e:
                    logging.error(f"  ✗ Invalid JSON in output: {e}")
                    return False
            else:
                logging.warning(f"  ✗ Agent ran but no output file ({elapsed}s)")
                if result.stdout:
                    logging.debug(f"  stdout: {result.stdout[:500]}")
                return False
        else:
            logging.error(f"  ✗ Agent exited with code {result.returncode} ({elapsed}s)")
            if result.stderr:
                logging.error(f"  stderr: {result.stderr[:300]}")
            return False

    except subprocess.TimeoutExpired:
        logging.error(f"  ✗ Timed out after 600s")
        return False
    except Exception as e:
        logging.error(f"  ✗ Unexpected error: {e}")
        return False

# ---------------------------------------------------------------------------
# Memory update — lightweight, just enough for continuity
# ---------------------------------------------------------------------------

def _update_memory(landscape_data):
    """Update landscape_memory.json from successful agent output."""
    memory_file = NATIONAL_DIR / "landscape_memory.json"

    # Load existing memory for run count
    existing = {}
    if memory_file.exists():
        try:
            existing = json.loads(memory_file.read_text())
        except Exception:
            pass

    run_count = existing.get('run_count', 0) + 1

    # Extract storyline headlines for continuity tracking
    storylines = [s.get('headline', '') for s in landscape_data.get('storylines', [])]

    # Build a 1-2 sentence digest of the writeup
    writeup = landscape_data.get('writeup', '')
    # Take first sentence (up to 200 chars) as summary
    first_sentence = writeup.split('.')[0][:200] + '.' if writeup else '(none)'

    memory = {
        'last_run':               datetime.now().strftime('%Y-%m-%d'),
        'run_count':              run_count,
        'mode':                   landscape_data.get('mode', ''),
        'prior_storylines':       storylines,
        'prior_writeup_summary':  first_sentence,
    }

    memory_file.write_text(json.dumps(memory, indent=2))
    logging.info(f"  Memory updated (run #{run_count}, {len(storylines)} storylines)")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='National landscape Claude agent')
    parser.add_argument('--dry-run',    action='store_true', help='Print prompt without running')
    parser.add_argument('--no-youtube', action='store_true', help='Mark YouTube as skipped in prompt')
    args = parser.parse_args()

    log_file = setup_logging()
    logging.info(f"National landscape agent — log: {log_file}")

    NATIONAL_DIR.mkdir(exist_ok=True)

    # Load fetched sources
    fetched_path = NATIONAL_DIR / "fetched_sources.json"
    if not fetched_path.exists():
        logging.error(f"No fetched sources found at {fetched_path}")
        logging.error("Run national_fetcher.py first.")
        sys.exit(1)

    try:
        raw_data = json.loads(fetched_path.read_text())
    except Exception as e:
        logging.error(f"Could not parse fetched_sources.json: {e}")
        sys.exit(1)

    # Wrap raw data with stats for prompt builder
    fetched_data = {
        'output': raw_data,
        'stats':  raw_data.get('stats', {}),
    }

    prompt, mode = build_prompt(fetched_data, no_youtube=args.no_youtube)
    logging.info(f"Mode: {mode} | Prompt: {len(prompt)} chars")

    success = run_agent(prompt, dry_run=args.dry_run)

    if success:
        logging.info("National landscape complete ✓")
    else:
        logging.error("National landscape failed ✗")
        sys.exit(1)

if __name__ == '__main__':
    main()
