#!/usr/bin/env python3
"""Quick diagnostic — dump scheduleoutlook.php line output for Alabama"""
import time
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    p = b.new_context().new_page()
    p.goto("https://www.puntandrally.com/scheduleoutlook.php?getteam=Alabama",
           wait_until="domcontentloaded")
    time.sleep(2.0)
    lines = [l.strip() for l in p.inner_text("body").split('\n') if l.strip()]
    print(f"Total lines: {len(lines)}")
    print()
    # Find where the schedule content starts (after nav)
    for i, l in enumerate(lines):
        if 'Alabama' in l and ('2026' in l or 'Crimson' in l):
            print(f"=== Starting at line {i} ===")
            for j, ll in enumerate(lines[i:i+80]):
                print(f"{i+j:4}: {ll}")
            break
    b.close()
