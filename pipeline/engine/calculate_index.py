#!/usr/bin/env python3
"""
Calculates SquadCheck Power Index for all 48 World Cup teams.

For each team:
  - Select Best XI: 1 GK + 4 DEF + 3 MID + 3 FWD (highest rated players)
  - Squad Quality (50%): avg rating of Best XI
  - X-Factor (15%): U23 players (born after 2003-06-11) with rating > 50
  - Squad Depth (20%): avg rating of bench (players 12-23)
  - Recent Form (15%): placeholder = 50.0

SC Power Index = SQ×0.50 + XF×0.15 + SD×0.20 + RF×0.15

Saves to:
  - pipeline/data/output/sc_power_index.json
  - pipeline/data/output/sc_power_index.csv
  - pipeline/data/output/team_details/{team}.json
"""
import csv
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
INPUT_FILE = ROOT / "data" / "cache" / "rated_players.json"
OUTPUT_DIR = ROOT / "data" / "output"
TEAM_DETAILS_DIR = OUTPUT_DIR / "team_details"
JSON_OUTPUT = OUTPUT_DIR / "sc_power_index.json"
CSV_OUTPUT = OUTPUT_DIR / "sc_power_index.csv"

WC_DATE = date(2026, 6, 11)
U23_CUTOFF = date(2003, 6, 12)  # born on/after this date = U23 at WC

# Best XI formation: position → count
BEST_XI_FORMATION: Dict[str, int] = {
    "Goalkeeper": 1,
    "Defender": 4,
    "Midfielder": 3,
    "Forward": 3,
}

# Position short codes for display
POS_SHORT: Dict[str, str] = {
    "Goalkeeper": "GK",
    "Defender": "DF",
    "Midfielder": "MF",
    "Forward": "FW",
}

# Confederation flags (emoji)
CONF_FLAGS: Dict[str, str] = {
    "UEFA": "🇪🇺",
    "CONMEBOL": "🌎",
    "CONCACAF": "🌎",
    "AFC": "🌏",
    "CAF": "🌍",
    "OFC": "🌊",
}

# Team flag emojis (best effort)
TEAM_FLAGS: Dict[str, str] = {
    "Mexico": "🇲🇽", "South Korea": "🇰🇷", "South Africa": "🇿🇦", "Czechia": "🇨🇿",
    "Canada": "🇨🇦", "Bosnia and Herzegovina": "🇧🇦", "Qatar": "🇶🇦", "Switzerland": "🇨🇭",
    "Brazil": "🇧🇷", "Morocco": "🇲🇦", "Haiti": "🇭🇹", "Scotland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿",
    "United States": "🇺🇸", "Paraguay": "🇵🇾", "Australia": "🇦🇺", "Türkiye": "🇹🇷",
    "Germany": "🇩🇪", "Curaçao": "🇨🇼", "Ivory Coast": "🇨🇮", "Ecuador": "🇪🇨",
    "Netherlands": "🇳🇱", "Japan": "🇯🇵", "Sweden": "🇸🇪", "Tunisia": "🇹🇳",
    "Belgium": "🇧🇪", "Egypt": "🇪🇬", "Cape Verde": "🇨🇻", "Saudi Arabia": "🇸🇦",
    "Spain": "🇪🇸", "New Zealand": "🇳🇿", "Senegal": "🇸🇳", "Jordan": "🇯🇴",
    "France": "🇫🇷", "Norway": "🇳🇴", "Algeria": "🇩🇿", "Austria": "🇦🇹",
    "Portugal": "🇵🇹", "DR Congo": "🇨🇩", "Uzbekistan": "🇺🇿", "Colombia": "🇨🇴",
    "England": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "Croatia": "🇭🇷", "Iran": "🇮🇷", "Ghana": "🇬🇭",
    "Argentina": "🇦🇷", "Uruguay": "🇺🇾", "Panama": "🇵🇦", "Iraq": "🇮🇶",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_dob(dob_str: Optional[str]) -> Optional[date]:
    if not dob_str:
        return None
    try:
        parts = dob_str[:10].split("-")
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        pass
    return None


def is_u23(dob: Optional[date]) -> bool:
    """True if player is U23 at World Cup kickoff."""
    if not dob:
        return False
    return dob >= U23_CUTOFF


def select_best_xi(
    players: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Select best XI using formation 1-4-3-3.
    Returns (best_xi, remaining_bench).
    Each position slot is filled by highest-rated available player.
    """
    # Group by position, sorted by rating desc
    by_pos: Dict[str, List[Dict[str, Any]]] = {}
    for p in players:
        pos = p.get("position", "Unknown")
        by_pos.setdefault(pos, []).append(p)

    for pos in by_pos:
        by_pos[pos].sort(key=lambda x: x.get("rating", 0), reverse=True)

    selected_ids: List[int] = []
    best_xi: List[Dict[str, Any]] = []

    # Pick slots per formation
    for pos, count in BEST_XI_FORMATION.items():
        available = by_pos.get(pos, [])
        picked = 0
        for p in available:
            if picked >= count:
                break
            best_xi.append(p)
            selected_ids.append(id(p))
            picked += 1

        # If not enough players in position, pad with best remaining
        # (shouldn't happen with proper squads, but defensive coding)
        if picked < count:
            remaining_all = [
                q for pos2, lst in by_pos.items()
                for q in lst
                if id(q) not in selected_ids and pos2 != pos
            ]
            remaining_all.sort(key=lambda x: x.get("rating", 0), reverse=True)
            for q in remaining_all[:count - picked]:
                best_xi.append(q)
                selected_ids.append(id(q))

    # Bench = everyone not in best_xi
    bench = [p for p in players if id(p) not in selected_ids]
    bench.sort(key=lambda x: x.get("rating", 0), reverse=True)

    return best_xi, bench


def xfactor_score(players: List[Dict[str, Any]], threshold: float = 50.0) -> float:
    """
    X-Factor: score based on U23 players with rating > threshold.
    Returns 0-100 scale.
    Max meaningful: ~5-6 elite U23s = 100.
    """
    u23_quality = []
    for p in players:
        dob = parse_dob(p.get("date_of_birth"))
        rtg = p.get("rating", 0) or 0
        if is_u23(dob) and rtg > threshold:
            u23_quality.append(rtg)

    if not u23_quality:
        return 20.0  # baseline
    # Each U23 above threshold contributes; cap at sensible ceiling
    score = min(100.0, 20.0 + sum(u23_quality) / max(1, len(u23_quality)) * 0.6 + len(u23_quality) * 4.0)
    return score


def squad_quality(best_xi: List[Dict[str, Any]]) -> float:
    """Average rating of best XI."""
    if not best_xi:
        return 0.0
    return sum(p.get("rating", 0) for p in best_xi) / len(best_xi)


def squad_depth(bench: List[Dict[str, Any]], bench_size: int = 12) -> float:
    """Average rating of bench players 1-12."""
    bench_slice = bench[:bench_size]
    if not bench_slice:
        return 0.0
    return sum(p.get("rating", 0) for p in bench_slice) / len(bench_slice)


def safe_slug(name: str) -> str:
    """Convert team name to a filesystem-safe slug."""
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> None:
    print("[calculate_index] Loading rated players...")

    if not INPUT_FILE.exists():
        print(f"[calculate_index] Input not found: {INPUT_FILE}. Run rate_players first.")
        return

    all_players: List[Dict[str, Any]] = json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    print(f"[calculate_index] Loaded {len(all_players)} players")

    # Group by team
    teams_map: Dict[str, List[Dict[str, Any]]] = {}
    for p in all_players:
        team = p.get("team", "Unknown")
        teams_map.setdefault(team, []).append(p)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEAM_DETAILS_DIR.mkdir(parents=True, exist_ok=True)

    index_entries: List[Dict[str, Any]] = []

    for team_name, players in sorted(teams_map.items()):
        # Get metadata from first player
        confederation = players[0].get("confederation", "") if players else ""
        group = players[0].get("group", "") if players else ""

        best_xi, bench = select_best_xi(players)

        sq = squad_quality(best_xi)
        xf = xfactor_score(players)
        sd = squad_depth(bench)
        rf = 50.0  # placeholder Recent Form

        sci = (
            sq * 0.50
            + xf * 0.15
            + sd * 0.20
            + rf * 0.15
        )

        # U23 players for display
        u23_players = [
            p for p in players
            if is_u23(parse_dob(p.get("date_of_birth"))) and (p.get("rating", 0) or 0) > 40
        ]
        u23_players.sort(key=lambda x: x.get("rating", 0), reverse=True)

        entry: Dict[str, Any] = {
            "team": team_name,
            "confederation": confederation,
            "group": group,
            "flag": TEAM_FLAGS.get(team_name, "🏳️"),
            "sci": round(sci, 2),
            "squad_quality": round(sq, 2),
            "xfactor": round(xf, 2),
            "squad_depth": round(sd, 2),
            "recent_form": round(rf, 2),
            "squad_size": len(players),
            "best_xi": [
                {
                    "name": p.get("name", ""),
                    "position": p.get("position", ""),
                    "pos_short": POS_SHORT.get(p.get("position", ""), "??"),
                    "rating": p.get("rating", 0),
                    "market_value_eur": p.get("market_value_eur", 0),
                }
                for p in best_xi
            ],
            "u23_stars": [
                {
                    "name": p.get("name", ""),
                    "position": p.get("position", ""),
                    "age": p.get("age"),
                    "rating": p.get("rating", 0),
                }
                for p in u23_players[:6]
            ],
        }
        index_entries.append(entry)

        # Save individual team detail
        team_slug = safe_slug(team_name)
        detail_file = TEAM_DETAILS_DIR / f"{team_slug}.json"
        detail = dict(entry)
        detail["all_players"] = [
            {
                "name": p.get("name", ""),
                "position": p.get("position", ""),
                "rating": p.get("rating", 0),
                "market_value_eur": p.get("market_value_eur", 0),
                "goals": p.get("goals", 0),
                "assists": p.get("assists", 0),
                "age": p.get("age"),
                "club_name": p.get("club_name"),
                "club_position": p.get("club_position"),
            }
            for p in sorted(players, key=lambda x: x.get("rating", 0), reverse=True)
        ]
        detail_file.write_text(
            json.dumps(detail, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    # Sort by SCI descending, assign ranks
    index_entries.sort(key=lambda x: x["sci"], reverse=True)
    for i, entry in enumerate(index_entries):
        entry["rank"] = i + 1

    # Save main JSON
    JSON_OUTPUT.write_text(
        json.dumps(index_entries, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"[calculate_index] Saved power index JSON → {JSON_OUTPUT}")

    # Save CSV
    csv_cols = ["rank", "team", "confederation", "group", "sci",
                "squad_quality", "xfactor", "squad_depth", "recent_form", "squad_size"]
    with open(CSV_OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(index_entries)
    print(f"[calculate_index] Saved power index CSV  → {CSV_OUTPUT}")

    # Print top 10
    print("\n[calculate_index] Top 10 Teams:")
    print(f"  {'Rank':>4}  {'Team':<28} {'Group':>5}  {'SCI':>6}  {'SQ':>6}  {'XF':>6}  {'SD':>6}")
    print("  " + "-" * 65)
    for entry in index_entries[:10]:
        print(
            f"  {entry['rank']:>4}  {entry['team']:<28} "
            f"{entry['group']:>5}  {entry['sci']:>6.2f}  "
            f"{entry['squad_quality']:>6.2f}  {entry['xfactor']:>6.2f}  "
            f"{entry['squad_depth']:>6.2f}"
        )


if __name__ == "__main__":
    run()
