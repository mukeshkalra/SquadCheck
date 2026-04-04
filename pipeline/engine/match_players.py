#!/usr/bin/env python3
"""
Matches squad players to Transfermarkt data (market value, DOB) and
cross-references scorers/standings data.

Saves merged player data to: pipeline/data/cache/merged_players.json

Structure per player entry:
{
  "team": "France",
  "confederation": "UEFA",
  "group": "I",
  "name": "Kylian Mbappé",
  "position": "Forward",
  "market_value_eur": 180000000,
  "date_of_birth": "1998-12-20",
  "goals": 10,
  "assists": 5,
  "club_name": "Real Madrid",
  "club_competition": "PD",
  "club_position": 1,
  "tm_matched": true
}
"""
import csv
import json
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
SQUADS_FILE = ROOT / "data" / "input" / "squads.json"
TM_CACHE = ROOT / "data" / "cache" / "transfermarkt.csv"
SCORERS_DIR = ROOT / "data" / "cache" / "scorers"
STANDINGS_DIR = ROOT / "data" / "cache" / "standings"
OUTPUT_FILE = ROOT / "data" / "cache" / "merged_players.json"

# Fallback market values (EUR) by FIFA ranking tier
# Groups A-L, 48 teams: rough assignment
FIFA_RANK_DEFAULTS: Dict[str, int] = {
    "top10": 2_000_000,
    "top25": 1_000_000,
    "rest":    500_000,
}

# Teams considered FIFA top 10 (rough approximation)
FIFA_TOP_10 = {
    "France", "Brazil", "England", "Argentina", "Belgium",
    "Spain", "Portugal", "Netherlands", "Germany", "Italy"
}
# Teams considered FIFA top 25
FIFA_TOP_25 = {
    "Croatia", "Uruguay", "Denmark", "Switzerland", "United States",
    "Morocco", "Japan", "Senegal", "Colombia", "Mexico",
    "South Korea", "Ecuador", "Canada", "Australia", "Türkiye"
}


# ---------------------------------------------------------------------------
# Levenshtein distance (pure Python, no external deps)
# ---------------------------------------------------------------------------

def levenshtein(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if s1 == s2:
        return 0
    len1, len2 = len(s1), len(s2)
    if len1 == 0:
        return len2
    if len2 == 0:
        return len1
    # Use two rows to save memory
    prev = list(range(len2 + 1))
    for i in range(1, len1 + 1):
        curr = [i] + [0] * len2
        for j in range(1, len2 + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,       # deletion
                curr[j - 1] + 1,   # insertion
                prev[j - 1] + cost # substitution
            )
        prev = curr
    return prev[len2]


def normalize_name(name: str) -> str:
    """Lowercase, remove accents, strip extra whitespace."""
    name = name.lower().strip()
    # Unicode normalization: decompose accented chars then drop combining marks
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_name


def name_parts(name: str) -> List[str]:
    """Return normalized first and last name components."""
    return normalize_name(name).split()


def best_match(
    player_name: str,
    candidates: List[Dict[str, Any]],
    max_dist: int = 3
) -> Optional[Dict[str, Any]]:
    """
    Find best matching TM record for a player name.
    Tries exact match first, then Levenshtein on full name,
    then on last name only.
    """
    norm_query = normalize_name(player_name)
    query_parts = norm_query.split()

    best: Optional[Dict[str, Any]] = None
    best_dist = max_dist + 1

    for cand in candidates:
        cand_norm = normalize_name(cand.get("name", ""))

        # Exact match
        if cand_norm == norm_query:
            return cand

        # Full name Levenshtein
        dist_full = levenshtein(norm_query, cand_norm)
        if dist_full <= max_dist and dist_full < best_dist:
            best_dist = dist_full
            best = cand
            continue

        # Last-name only match (useful for single-name players)
        if query_parts and cand_norm.split():
            cand_last = cand_norm.split()[-1]
            query_last = query_parts[-1]
            dist_last = levenshtein(query_last, cand_last)
            if dist_last == 0 and len(query_last) >= 4:
                # Exact last-name match is worth checking
                if dist_full < best_dist:
                    best_dist = dist_full
                    best = cand

    return best


# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------

def load_tm_data() -> List[Dict[str, Any]]:
    """Load transfermarkt CSV into list of dicts. Returns [] if file missing."""
    if not TM_CACHE.exists():
        print(f"[match_players] TM cache not found: {TM_CACHE}. Run fetch_market_values first.")
        return []

    records = []
    with open(TM_CACHE, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Normalize field names to lowercase
            norm_row = {k.lower().strip(): v for k, v in row.items()}
            records.append(norm_row)
    print(f"[match_players] Loaded {len(records):,} TM records")
    return records


def load_all_scorers() -> Dict[str, List[Dict[str, Any]]]:
    """Load all scorers JSON files. Returns dict keyed by normalized player name."""
    scorers_by_name: Dict[str, List[Dict[str, Any]]] = {}
    if not SCORERS_DIR.exists():
        return scorers_by_name
    for f in SCORERS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for entry in data:
                norm = normalize_name(entry.get("name", ""))
                if norm:
                    if norm not in scorers_by_name:
                        scorers_by_name[norm] = []
                    scorers_by_name[norm].append(entry)
        except (json.JSONDecodeError, OSError):
            pass
    print(f"[match_players] Loaded scorer data for {len(scorers_by_name)} players")
    return scorers_by_name


def load_all_standings() -> Dict[str, Dict[str, Any]]:
    """Load all standings JSON files. Returns dict keyed by normalized team name → best record."""
    standings_by_team: Dict[str, Dict[str, Any]] = {}
    if not STANDINGS_DIR.exists():
        return standings_by_team
    for f in STANDINGS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for row in data:
                norm = normalize_name(row.get("team_name", ""))
                short_norm = normalize_name(row.get("team_short", ""))
                for key in [norm, short_norm]:
                    if key and key not in standings_by_team:
                        standings_by_team[key] = row
        except (json.JSONDecodeError, OSError):
            pass
    print(f"[match_players] Loaded standings for {len(standings_by_team)} teams")
    return standings_by_team


# ---------------------------------------------------------------------------
# Market value parsing
# ---------------------------------------------------------------------------

def parse_market_value(raw: str) -> Optional[int]:
    """Parse market value string to integer EUR amount."""
    if not raw:
        return None
    raw = raw.strip().replace(",", "").replace(" ", "")
    # Remove currency symbol
    raw = raw.replace("€", "").replace("$", "").replace("£", "")
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return None


def get_default_value(team: str) -> int:
    """Return default market value based on team's FIFA ranking tier."""
    if team in FIFA_TOP_10:
        return FIFA_RANK_DEFAULTS["top10"]
    if team in FIFA_TOP_25:
        return FIFA_RANK_DEFAULTS["top25"]
    return FIFA_RANK_DEFAULTS["rest"]


# ---------------------------------------------------------------------------
# Scorer matching
# ---------------------------------------------------------------------------

def find_scorer_stats(
    player_name: str,
    scorers_by_name: Dict[str, List[Dict[str, Any]]]
) -> Tuple[int, int]:
    """
    Return (goals, assists) for a player from scorers data.
    Sums across all competitions. Falls back to 0,0 if not found.
    """
    norm = normalize_name(player_name)
    # Exact match
    entries = scorers_by_name.get(norm, [])
    if not entries:
        # Try fuzzy match among scorer names
        for cand_name, cand_entries in scorers_by_name.items():
            if levenshtein(norm, cand_name) <= 2:
                entries = cand_entries
                break

    total_goals = sum(e.get("goals", 0) or 0 for e in entries)
    total_assists = sum(e.get("assists", 0) or 0 for e in entries)
    # Use best single-competition performance to avoid double counting
    if entries:
        best = max(entries, key=lambda e: (e.get("goals", 0) or 0))
        return (best.get("goals", 0) or 0), (best.get("assists", 0) or 0)
    return 0, 0


def find_club_standing(
    club_name: str,
    standings_by_team: Dict[str, Dict[str, Any]]
) -> Tuple[Optional[int], Optional[str]]:
    """Return (league_position, competition_code) for a club name."""
    if not club_name:
        return None, None
    norm = normalize_name(club_name)
    row = standings_by_team.get(norm)
    if row:
        return row.get("position"), row.get("competition")
    # Fuzzy search
    for cand_name, cand_row in standings_by_team.items():
        if levenshtein(norm, cand_name) <= 3:
            return cand_row.get("position"), cand_row.get("competition")
    return None, None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> None:
    print("[match_players] Starting player matching...")

    squads = json.loads(SQUADS_FILE.read_text(encoding="utf-8"))
    tm_records = load_tm_data()
    scorers_by_name = load_all_scorers()
    standings_by_team = load_all_standings()

    # Index TM records by first letter of name for faster lookup
    tm_index: Dict[str, List[Dict[str, Any]]] = {}
    for rec in tm_records:
        key = normalize_name(rec.get("name", ""))[:1]
        if key:
            tm_index.setdefault(key, []).append(rec)

    merged_players: List[Dict[str, Any]] = []
    total_matched = 0
    total_players = 0

    for team_name, team_data in squads.items():
        confederation = team_data.get("confederation", "")
        group = team_data.get("group", "")
        players = team_data.get("players", [])

        for player in players:
            total_players += 1
            p_name = player.get("name", "")
            position = player.get("position", "")

            # --- TM matching ---
            first_char = normalize_name(p_name)[:1]
            candidates = tm_index.get(first_char, [])
            # Also include candidates from adjacent letters for robustness
            if not candidates:
                candidates = tm_records  # fallback: search all

            tm_match = best_match(p_name, candidates, max_dist=3)

            market_value: int
            date_of_birth: Optional[str]
            club_name: Optional[str]
            club_competition_id: Optional[str]
            tm_matched: bool

            if tm_match:
                raw_mv = tm_match.get("market_value_in_eur", "")
                parsed_mv = parse_market_value(str(raw_mv))
                market_value = parsed_mv if parsed_mv and parsed_mv > 0 else get_default_value(team_name)
                date_of_birth = tm_match.get("date_of_birth") or None
                club_name = tm_match.get("current_club_name") or None
                club_competition_id = tm_match.get("current_club_domestic_competition_id") or None
                tm_matched = True
                total_matched += 1
            else:
                market_value = get_default_value(team_name)
                date_of_birth = None
                club_name = None
                club_competition_id = None
                tm_matched = False

            # --- Scorer stats ---
            goals, assists = find_scorer_stats(p_name, scorers_by_name)

            # --- Club standings ---
            club_position: Optional[int] = None
            club_league: Optional[str] = None
            if club_name:
                club_position, club_league = find_club_standing(club_name, standings_by_team)

            merged_players.append({
                "team": team_name,
                "confederation": confederation,
                "group": group,
                "name": p_name,
                "position": position,
                "market_value_eur": market_value,
                "date_of_birth": date_of_birth,
                "goals": goals,
                "assists": assists,
                "club_name": club_name,
                "club_competition": club_competition_id or club_league,
                "club_position": club_position,
                "tm_matched": tm_matched,
            })

    print(f"[match_players] Matched {total_matched}/{total_players} players to TM data")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(merged_players, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"[match_players] Saved {len(merged_players)} players → {OUTPUT_FILE}")


if __name__ == "__main__":
    run()
