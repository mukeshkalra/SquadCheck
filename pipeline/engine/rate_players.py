#!/usr/bin/env python3
"""
Position-relative player rating system.

Steps:
  A: Calculate market value percentiles BY POSITION from all merged players
  B: Map percentile → base rating using anchor points + linear interpolation
  C: Form adjustment (25% weight) - goals/assists for FWs/MFs, club position for DFs/GKs
  D: Age factor (5% weight)
  E: Club strength (10% weight)
  FINAL = Percentile×0.60 + Form×0.25 + Age×0.05 + ClubStrength×0.10

Saves to: pipeline/data/cache/rated_players.json
"""
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
INPUT_FILE = ROOT / "data" / "cache" / "merged_players.json"
OUTPUT_FILE = ROOT / "data" / "cache" / "rated_players.json"

# Top leagues by competitive strength (higher = better)
LEAGUE_STRENGTH: Dict[str, float] = {
    "PL": 100.0,   # Premier League
    "PD": 95.0,    # La Liga
    "BL1": 90.0,   # Bundesliga
    "SA": 88.0,    # Serie A
    "FL1": 82.0,   # Ligue 1
    "PPL": 72.0,   # Primeira Liga
    "DED": 70.0,   # Eredivisie
    "CL": 98.0,    # Champions League
    "ELC": 55.0,   # Championship
    "BSA": 60.0,   # Brasileirao
}

# Percentile → rating anchors (pct, rating)
PERCENTILE_ANCHORS: List[Tuple[float, float]] = [
    (0.0,   10.0),
    (10.0,  20.0),
    (25.0,  35.0),
    (50.0,  50.0),
    (70.0,  62.0),
    (85.0,  72.0),
    (92.0,  80.0),
    (97.0,  88.0),
    (99.0,  94.0),
    (100.0, 99.0),
]

# Club league position → form score for defenders/GKs
POSITION_FORM_MAP: List[Tuple[int, float]] = [
    (1,  100.0),
    (2,  92.0),
    (3,  85.0),
    (4,  78.0),
    (5,  72.0),
    (8,  60.0),
    (10, 50.0),
    (15, 35.0),
    (20, 20.0),
]

REFERENCE_DATE = date(2026, 6, 11)  # World Cup kickoff


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def interpolate(x: float, anchors: List[Tuple[float, float]]) -> float:
    """Linear interpolation between anchor points."""
    if not anchors:
        return 50.0
    if x <= anchors[0][0]:
        return anchors[0][1]
    if x >= anchors[-1][0]:
        return anchors[-1][1]
    for i in range(len(anchors) - 1):
        x0, y0 = anchors[i]
        x1, y1 = anchors[i + 1]
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return anchors[-1][1]


def percentile_rank(value: float, sorted_values: List[float]) -> float:
    """Return percentile (0-100) of value in sorted list."""
    if not sorted_values:
        return 50.0
    n = len(sorted_values)
    count_below = sum(1 for v in sorted_values if v < value)
    count_equal = sum(1 for v in sorted_values if v == value)
    return 100.0 * (count_below + 0.5 * count_equal) / n


def parse_dob(dob_str: Optional[str]) -> Optional[date]:
    """Parse date string YYYY-MM-DD to date object."""
    if not dob_str:
        return None
    try:
        parts = dob_str[:10].split("-")
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        pass
    return None


def age_at_wc(dob: Optional[date]) -> Optional[float]:
    """Return player age at World Cup kickoff."""
    if not dob:
        return None
    delta = REFERENCE_DATE - dob
    return delta.days / 365.25


def age_factor(age: Optional[float]) -> float:
    """
    Convert age to 0-100 factor.
    Peak age ~26-28 = 100, declining on either side.
    """
    if age is None:
        return 50.0
    if age < 17:
        return 20.0
    if age <= 21:
        return interpolate(age, [(17, 20), (21, 70)])
    if age <= 24:
        return interpolate(age, [(21, 70), (24, 90)])
    if age <= 28:
        return interpolate(age, [(24, 90), (28, 100)])
    if age <= 31:
        return interpolate(age, [(28, 100), (31, 90)])
    if age <= 34:
        return interpolate(age, [(31, 90), (34, 72)])
    if age <= 37:
        return interpolate(age, [(34, 72), (37, 50)])
    return max(10.0, interpolate(age, [(37, 50), (42, 10)]))


def form_factor_attacker(goals: int, assists: int, played: int = 0) -> float:
    """
    Form score for FWs and MFs based on goals + assists.
    G+A per season (rough scale: 20+ = elite).
    """
    ga = (goals or 0) + (assists or 0)
    if ga <= 0:
        return 30.0
    if ga <= 2:
        return 40.0
    if ga <= 5:
        return 50.0
    if ga <= 8:
        return 60.0
    if ga <= 12:
        return 70.0
    if ga <= 16:
        return 80.0
    if ga <= 20:
        return 88.0
    if ga <= 25:
        return 94.0
    return 99.0


def form_factor_defender(club_position: Optional[int]) -> float:
    """Form score for DFs and GKs based on club league position."""
    if club_position is None:
        return 45.0
    return interpolate(float(club_position), [(x, y) for x, y in POSITION_FORM_MAP])


def club_strength_factor(club_competition: Optional[str]) -> float:
    """Return 0-100 factor based on competition strength."""
    if not club_competition:
        return 40.0
    strength = LEAGUE_STRENGTH.get(club_competition.upper(), None)
    if strength is not None:
        return strength
    # Unknown league
    return 45.0


def clamp(val: float, lo: float = 1.0, hi: float = 99.0) -> float:
    return max(lo, min(hi, val))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> None:
    print("[rate_players] Loading merged players...")

    if not INPUT_FILE.exists():
        print(f"[rate_players] Input not found: {INPUT_FILE}. Run match_players first.")
        return

    players: List[Dict[str, Any]] = json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    print(f"[rate_players] Loaded {len(players)} players")

    # Step A: Build per-position market value distributions
    pos_values: Dict[str, List[float]] = {}
    for p in players:
        pos = p.get("position", "Unknown")
        mv = float(p.get("market_value_eur", 0) or 0)
        pos_values.setdefault(pos, []).append(mv)

    for pos in pos_values:
        pos_values[pos].sort()

    print(f"[rate_players] Positions found: {list(pos_values.keys())}")

    rated: List[Dict[str, Any]] = []

    for p in players:
        pos = p.get("position", "Unknown")
        mv = float(p.get("market_value_eur", 0) or 0)
        goals = int(p.get("goals", 0) or 0)
        assists = int(p.get("assists", 0) or 0)
        club_pos = p.get("club_position")
        club_comp = p.get("club_competition")
        dob_str = p.get("date_of_birth")

        # Step A: percentile within position
        pct = percentile_rank(mv, pos_values.get(pos, [mv]))

        # Step B: base rating from percentile
        base_rating = interpolate(pct, PERCENTILE_ANCHORS)

        # Step C: form factor
        if pos in ("Forward", "Midfielder"):
            form = form_factor_attacker(goals, assists)
        else:
            form = form_factor_defender(club_pos)

        # Step D: age factor
        dob = parse_dob(dob_str)
        age = age_at_wc(dob)
        age_score = age_factor(age)

        # Step E: club strength
        club_str = club_strength_factor(club_comp)

        # Final rating
        final = (
            base_rating * 0.60
            + form * 0.25
            + age_score * 0.05
            + club_str * 0.10
        )
        final = clamp(final)

        entry = dict(p)
        entry["pct_rank"] = round(pct, 2)
        entry["base_rating"] = round(base_rating, 2)
        entry["form_score"] = round(form, 2)
        entry["age_score"] = round(age_score, 2)
        entry["age"] = round(age, 1) if age is not None else None
        entry["club_strength"] = round(club_str, 2)
        entry["rating"] = round(final, 2)

        rated.append(entry)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(rated, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"[rate_players] Saved {len(rated)} rated players → {OUTPUT_FILE}")

    # Summary stats
    ratings = [p["rating"] for p in rated]
    if ratings:
        avg = sum(ratings) / len(ratings)
        top = sorted(rated, key=lambda x: x["rating"], reverse=True)[:5]
        print(f"[rate_players] Avg rating: {avg:.1f}")
        print("[rate_players] Top 5 players:")
        for p in top:
            print(f"  {p['name']:30s} ({p['team']:20s}) {p['rating']:.1f}")


if __name__ == "__main__":
    run()
