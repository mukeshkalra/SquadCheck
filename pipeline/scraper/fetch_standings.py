#!/usr/bin/env python3
"""Downloads standings from football-data.org for 9 leagues (no CL).
Saves to pipeline/data/cache/standings/{code}.json
"""
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache" / "standings"

# football-data.org competition codes (no Champions League for standings)
COMPETITIONS: List[Dict[str, str]] = [
    {"code": "PL",   "name": "Premier League"},
    {"code": "PD",   "name": "La Liga"},
    {"code": "BL1",  "name": "Bundesliga"},
    {"code": "SA",   "name": "Serie A"},
    {"code": "FL1",  "name": "Ligue 1"},
    {"code": "PPL",  "name": "Primeira Liga"},
    {"code": "DED",  "name": "Eredivisie"},
    {"code": "BSA",  "name": "Brasileirao"},
    {"code": "ELC",  "name": "Championship"},
]

API_TOKEN = os.environ.get("FOOTBALL_DATA_API_TOKEN", "2cf108208e574657b42500b4ead1e9de")
BASE_URL = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": API_TOKEN}
REQUEST_DELAY = 6.5  # seconds between requests (free tier: 10 req/min)


def fetch_standings(competition_code: str) -> Optional[List[Dict[str, Any]]]:
    """Fetch standings for a competition. Returns flat list of team standing rows or None."""
    url = f"{BASE_URL}/competitions/{competition_code}/standings"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 429:
            print(f"  [fetch_standings] Rate limited on {competition_code}, waiting 60s...")
            time.sleep(60)
            resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 404:
            print(f"  [fetch_standings] Competition {competition_code} not found (404), skipping.")
            return None
        resp.raise_for_status()
        data = resp.json()

        standings_groups = data.get("standings", [])
        result = []
        for group in standings_groups:
            # type: TOTAL, HOME, AWAY — we want TOTAL
            if group.get("type", "TOTAL") != "TOTAL":
                continue
            table = group.get("table", [])
            for row in table:
                team = row.get("team", {})
                result.append({
                    "team_id": team.get("id"),
                    "team_name": team.get("name", ""),
                    "team_short": team.get("shortName", ""),
                    "position": row.get("position", 0),
                    "played_games": row.get("playedGames", 0),
                    "won": row.get("won", 0),
                    "draw": row.get("draw", 0),
                    "lost": row.get("lost", 0),
                    "points": row.get("points", 0),
                    "goals_for": row.get("goalsFor", 0),
                    "goals_against": row.get("goalsAgainst", 0),
                    "goal_difference": row.get("goalDifference", 0),
                    "competition": competition_code,
                })
        return result

    except requests.RequestException as exc:
        print(f"  [fetch_standings] Error fetching {competition_code}: {exc}")
        return None


def main(force: bool = False) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[fetch_standings] Fetching standings for {len(COMPETITIONS)} competitions...")

    for i, comp in enumerate(COMPETITIONS):
        code = comp["code"]
        cache_file = CACHE_DIR / f"{code}.json"

        if cache_file.exists() and not force:
            print(f"  [{i+1}/{len(COMPETITIONS)}] {code}: already cached, skipping.")
            continue

        print(f"  [{i+1}/{len(COMPETITIONS)}] {code} ({comp['name']})...")
        standings = fetch_standings(code)

        if standings is not None:
            cache_file.write_text(
                json.dumps(standings, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            print(f"    Saved {len(standings)} team rows → {cache_file}")
        else:
            cache_file.write_text("[]", encoding="utf-8")
            print(f"    Saved empty result → {cache_file}")

        if i < len(COMPETITIONS) - 1:
            time.sleep(REQUEST_DELAY)

    print(f"[fetch_standings] Done. Cache: {CACHE_DIR}")


if __name__ == "__main__":
    main(force=True)
