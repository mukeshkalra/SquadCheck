#!/usr/bin/env python3
"""Downloads top scorers from football-data.org for 10 leagues.
Saves to pipeline/data/cache/scorers/{code}.json
"""
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache" / "scorers"

# football-data.org competition codes
COMPETITIONS: List[Dict[str, str]] = [
    {"code": "PL",   "name": "Premier League"},
    {"code": "PD",   "name": "La Liga"},
    {"code": "BL1",  "name": "Bundesliga"},
    {"code": "SA",   "name": "Serie A"},
    {"code": "FL1",  "name": "Ligue 1"},
    {"code": "PPL",  "name": "Primeira Liga"},
    {"code": "DED",  "name": "Eredivisie"},
    {"code": "BSA",  "name": "Brasileirao"},
    {"code": "CL",   "name": "Champions League"},
    {"code": "ELC",  "name": "Championship"},
]

API_TOKEN = os.environ.get("FOOTBALL_DATA_API_TOKEN", "2cf108208e574657b42500b4ead1e9de")
BASE_URL = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": API_TOKEN}
REQUEST_DELAY = 6.5  # seconds between requests (free tier: 10 req/min)


def fetch_scorers(competition_code: str) -> Optional[List[Dict[str, Any]]]:
    """Fetch top scorers for a competition. Returns list of scorer dicts or None on error."""
    url = f"{BASE_URL}/competitions/{competition_code}/scorers?limit=50"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 429:
            print(f"  [fetch_scorers] Rate limited on {competition_code}, waiting 60s...")
            time.sleep(60)
            resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 404:
            print(f"  [fetch_scorers] Competition {competition_code} not found (404), skipping.")
            return None
        resp.raise_for_status()
        data = resp.json()
        scorers = data.get("scorers", [])
        # Flatten to useful fields
        result = []
        for entry in scorers:
            player = entry.get("player", {})
            team = entry.get("team", {})
            result.append({
                "player_id": player.get("id"),
                "name": player.get("name", ""),
                "nationality": player.get("nationality", ""),
                "goals": entry.get("goals", 0),
                "assists": entry.get("assists", 0) or 0,
                "penalties": entry.get("penalties", 0) or 0,
                "played_matches": entry.get("playedMatches", 0) or 0,
                "team_id": team.get("id"),
                "team_name": team.get("name", ""),
                "team_short": team.get("shortName", ""),
                "competition": competition_code,
            })
        return result
    except requests.RequestException as exc:
        print(f"  [fetch_scorers] Error fetching {competition_code}: {exc}")
        return None


def main(force: bool = False) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[fetch_scorers] Fetching scorers for {len(COMPETITIONS)} competitions...")

    for i, comp in enumerate(COMPETITIONS):
        code = comp["code"]
        cache_file = CACHE_DIR / f"{code}.json"

        if cache_file.exists() and not force:
            print(f"  [{i+1}/{len(COMPETITIONS)}] {code}: already cached, skipping.")
            continue

        print(f"  [{i+1}/{len(COMPETITIONS)}] {code} ({comp['name']})...")
        scorers = fetch_scorers(code)

        if scorers is not None:
            cache_file.write_text(
                json.dumps(scorers, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            print(f"    Saved {len(scorers)} scorers → {cache_file}")
        else:
            # Write empty list so we don't retry
            cache_file.write_text("[]", encoding="utf-8")
            print(f"    Saved empty result → {cache_file}")

        # Rate limiting: don't sleep after last item
        if i < len(COMPETITIONS) - 1:
            time.sleep(REQUEST_DELAY)

    print(f"[fetch_scorers] Done. Cache: {CACHE_DIR}")


if __name__ == "__main__":
    main(force=True)
