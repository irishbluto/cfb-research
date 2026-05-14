#!/usr/bin/env python3
"""
cost_logger.py
--------------
Shared helper for capturing per-run cost + token usage from `claude -p` calls.

Designed to be called by research_agent.py, conference_research_agent.py, and
national_landscape_agent.py after each subprocess.run() that invokes the
Claude CLI with `--output-format json`.

When --output-format json is set, the CLI emits a single JSON envelope to
stdout containing cost_usd / total_cost_usd, duration_ms, num_turns,
session_id, and a `usage` dict with input/output/cache token counts. The
agent's real deliverable (the team / conference / national JSON file) is
still written to disk by the agent itself — stdout becomes purely a
diagnostic + metering channel.

Each call appends one row to /cfb-research/logs/agent_cost_log.csv. The header
is written automatically on first run. The file is safe to tail, import into
a spreadsheet, or roll up with awk/pandas for monthly burn projections.

Usage:
    from cost_logger import log_run

    result = subprocess.run(cmd, capture_output=True, text=True, ...)
    log_run(
        pipeline   = "team_research",      # or conference_preview / national_landscape
        slug       = "alabama",
        mode       = None,                  # optional — e.g. preseason / week3
        elapsed    = elapsed_secs,
        returncode = result.returncode,
        stdout     = result.stdout,
    )

Failure handling: malformed / non-JSON stdout still logs a row with whatever
elapsed time + returncode we have, so timeouts and crashed runs are still
visible. Never raises — pipelines should not abort because metering failed.
"""

import csv
import json
import logging
from datetime import datetime
from pathlib import Path

# Single canonical log path. BASE_DIR is /cfb-research on the VPS; we resolve
# relative to this file so this works regardless of caller cwd.
_LOG_DIR     = Path("/cfb-research/logs")
_CSV_PATH    = _LOG_DIR / "agent_cost_log.csv"

_CSV_HEADER = [
    "timestamp",
    "pipeline",
    "slug",
    "mode",
    "returncode",
    "elapsed_secs",
    "cost_usd",
    "num_turns",
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "duration_ms",
    "duration_api_ms",
    "session_id",
    "is_error",
    "subtype",
]


def log_run(pipeline, slug, elapsed, returncode, stdout, mode=None):
    """Append one row to agent_cost_log.csv. Never raises.

    Args:
        pipeline:   e.g. "team_research", "conference_preview", "national_landscape"
        slug:       team / conference slug, or "national"
        elapsed:    wall-clock seconds from subprocess
        returncode: subprocess return code (None if timeout)
        stdout:     raw stdout string from `claude -p --output-format json`
                    Pass "" if not available (e.g. timeout path).
        mode:       optional descriptor — preseason / week3 / etc.
    """
    parsed = _safe_parse(stdout)

    usage  = parsed.get("usage", {}) if isinstance(parsed, dict) else {}
    # Prefer total_cost_usd (cumulative across resumes); fall back to cost_usd.
    cost   = parsed.get("total_cost_usd", parsed.get("cost_usd", ""))

    row = {
        "timestamp":                   datetime.now().isoformat(timespec="seconds"),
        "pipeline":                    pipeline,
        "slug":                        slug,
        "mode":                        mode or "",
        "returncode":                  returncode if returncode is not None else "",
        "elapsed_secs":                round(elapsed, 1) if elapsed is not None else "",
        "cost_usd":                    cost,
        "num_turns":                   parsed.get("num_turns", ""),
        "input_tokens":                usage.get("input_tokens", ""),
        "output_tokens":               usage.get("output_tokens", ""),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", ""),
        "cache_read_input_tokens":     usage.get("cache_read_input_tokens", ""),
        "duration_ms":                 parsed.get("duration_ms", ""),
        "duration_api_ms":             parsed.get("duration_api_ms", ""),
        "session_id":                  parsed.get("session_id", ""),
        "is_error":                    parsed.get("is_error", ""),
        "subtype":                     parsed.get("subtype", ""),
    }

    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        new_file = not _CSV_PATH.exists()
        with open(_CSV_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_HEADER)
            if new_file:
                writer.writeheader()
            writer.writerow(row)
    except Exception as e:
        # Metering must never break the pipeline.
        logging.warning(f"  [cost_logger] failed to write CSV row: {e}")

    # One-line human-readable summary in the main log
    if cost != "" and cost is not None:
        try:
            cost_str = f"${float(cost):.4f}"
        except (TypeError, ValueError):
            cost_str = f"${cost}"
        in_t  = usage.get("input_tokens", "?")
        out_t = usage.get("output_tokens", "?")
        cc_t  = usage.get("cache_creation_input_tokens", 0) or 0
        cr_t  = usage.get("cache_read_input_tokens", 0) or 0
        logging.info(
            f"  [cost] {pipeline}/{slug}: {cost_str} "
            f"(in={in_t} out={out_t} cache_w={cc_t} cache_r={cr_t} "
            f"turns={parsed.get('num_turns','?')})"
        )
    else:
        # No cost info parsed — probably a timeout or non-JSON stdout
        logging.warning(
            f"  [cost] {pipeline}/{slug}: no cost data (rc={returncode}, "
            f"elapsed={elapsed}s) — row logged with blanks"
        )


def _safe_parse(stdout):
    """Best-effort JSON parse. Returns {} on any failure.

    `claude -p --output-format json` writes a single JSON object to stdout.
    In rare cases the CLI may emit trailing logs after the JSON; tolerate
    that by trying the last `{...}` block if a straight load fails.
    """
    if not stdout:
        return {}

    s = stdout.strip()
    if not s:
        return {}

    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # Fallback: find the last well-formed JSON object in stdout.
    start = s.rfind("{")
    while start != -1:
        try:
            return json.loads(s[start:])
        except json.JSONDecodeError:
            start = s.rfind("{", 0, start)

    return {}


# ---------------------------------------------------------------------------
# Tiny CLI for manual rollups: `python3 cost_logger.py summary`
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "summary":
        if not _CSV_PATH.exists():
            print(f"No cost log yet at {_CSV_PATH}")
            sys.exit(0)
        totals = {}      # (pipeline) -> [count, cost_sum, input_sum, output_sum]
        with open(_CSV_PATH, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = row["pipeline"] or "?"
                t = totals.setdefault(key, [0, 0.0, 0, 0])
                t[0] += 1
                try: t[1] += float(row["cost_usd"] or 0)
                except ValueError: pass
                try: t[2] += int(row["input_tokens"] or 0)
                except ValueError: pass
                try: t[3] += int(row["output_tokens"] or 0)
                except ValueError: pass
        print(f"{'pipeline':<22} {'runs':>6} {'total $':>10} {'in tok':>12} {'out tok':>12}")
        for k, (n, c, it, ot) in sorted(totals.items()):
            print(f"{k:<22} {n:>6} {c:>10.4f} {it:>12,} {ot:>12,}")
    else:
        print("Usage: python3 cost_logger.py summary")
