#!/usr/bin/env python3
"""
Full pipeline runner for SquadCheck.

Steps:
  1. fetch_market_values  — Download Transfermarkt CSV
  2. fetch_scorers        — Download top scorers from football-data.org
  3. fetch_standings      — Download league standings from football-data.org
  4. match_players        — Match squad players to TM data + scorers + standings
  5. rate_players         — Score each player
  6. calculate_index      — Build SC Power Index for 48 teams
  7. generate_html        — Write power-index.html

Usage:
  python run_pipeline.py            # Full run (uses cache where available)
  python run_pipeline.py --force    # Force re-fetch all data
  python run_pipeline.py --step 4   # Run from step 4 onward
"""
import argparse
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

from scraper.fetch_market_values import main as fetch_market_values
from scraper.fetch_scorers import main as fetch_scorers
from scraper.fetch_standings import main as fetch_standings
from engine.match_players import run as match_players
from engine.rate_players import run as rate_players
from engine.calculate_index import run as calculate_index
from publish.generate_html import main as generate_html


def step_banner(num: int, name: str) -> None:
    print(f"\n{'='*60}")
    print(f"  STEP {num}: {name}")
    print(f"{'='*60}")


def run_pipeline(force: bool = False, start_step: int = 1) -> None:
    t0 = time.time()
    print("SquadCheck Pipeline — Full Run")
    print(f"Force re-fetch: {force}")
    print(f"Starting from step: {start_step}")

    steps = [
        (1, "fetch_market_values", lambda: fetch_market_values(force=force)),
        (2, "fetch_scorers",       lambda: fetch_scorers(force=force)),
        (3, "fetch_standings",     lambda: fetch_standings(force=force)),
        (4, "match_players",       match_players),
        (5, "rate_players",        rate_players),
        (6, "calculate_index",     calculate_index),
        (7, "generate_html",       generate_html),
    ]

    for num, name, fn in steps:
        if num < start_step:
            print(f"  [skip] Step {num}: {name}")
            continue
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
    print(f"  Pipeline complete in {elapsed:.1f}s")
    print(f"{'='*60}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SquadCheck full data pipeline")
    parser.add_argument(
        "--force", action="store_true",
        help="Force re-download of all cached data"
    )
    parser.add_argument(
        "--step", type=int, default=1, metavar="N",
        help="Start from step N (1=fetch_tm, 2=fetch_scorers, 3=fetch_standings, "
             "4=match, 5=rate, 6=index, 7=html)"
    )
    args = parser.parse_args()
    run_pipeline(force=args.force, start_step=args.step)


if __name__ == "__main__":
    main()
