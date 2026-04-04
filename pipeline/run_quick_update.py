#!/usr/bin/env python3
"""
Quick update runner — uses only cached data, no network calls.

Steps:
  4. match_players   — Re-match using existing TM/scorers/standings cache
  5. rate_players    — Re-score players
  6. calculate_index — Rebuild SC Power Index
  7. generate_html   — Regenerate power-index.html

Use this when squad data changes (squads.json edited) or to regenerate
the HTML without re-downloading any external data.

Usage:
  python run_quick_update.py
"""
import os
import sys
import time
from pathlib import Path

# Load .env file if present
_env_file = Path(__file__).resolve().parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _val = _line.split("=", 1)
            os.environ.setdefault(_key.strip(), _val.strip())

# Add pipeline dir to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.match_players import run as match_players
from engine.rate_players import run as rate_players
from engine.calculate_index import run as calculate_index
from publish.generate_html import main as generate_html


def step_banner(num: int, name: str) -> None:
    print(f"\n{'='*60}")
    print(f"  STEP {num}: {name}")
    print(f"{'='*60}")


def run_quick() -> None:
    t0 = time.time()
    print("SquadCheck Quick Update (cache-only, no network)")
    print("=" * 60)

    steps = [
        (4, "match_players",   match_players),
        (5, "rate_players",    rate_players),
        (6, "calculate_index", calculate_index),
        (7, "generate_html",   generate_html),
    ]

    for num, name, fn in steps:
        step_banner(num, name)
        try:
            fn()
        except Exception as exc:
            print(f"\n[ERROR] Step {num} ({name}) failed: {exc}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"  Quick update complete in {elapsed:.1f}s")
    print(f"{'='*60}")


if __name__ == "__main__":
    run_quick()
