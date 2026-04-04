#!/usr/bin/env python3
"""
SquadCheck Data Pipeline v2
48-team WC 2026 SC Power Index.
Roster source: pipeline/data/squads.json
Market values: fuzzy-matched from Transfermarkt CSV
"""

import csv
import gzip
import json
import time
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_TOKEN  = "2cf108208e574657b42500b4ead1e9de"
API_BASE   = "https://api.football-data.org/v4"
API_HEADERS = {"X-Auth-Token": API_TOKEN}
API_DELAY  = 7

TM_URL = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/players.csv.gz"

ROOT        = Path(__file__).parent.parent
RAW_DIR     = ROOT / "data" / "raw"
SCORERS_DIR = RAW_DIR / "scorers"
OUTPUT_DIR  = ROOT / "data" / "output"
TEAM_DIR    = OUTPUT_DIR / "team_details"
SQUADS_FILE = ROOT / "data" / "squads.json"

LEAGUES = ["PL", "BL1", "SA", "FL1", "PD", "PPL", "DED", "BSA", "CL", "ELC"]

# Per-team default market value (EUR) when no TM match found
TEAM_DEFAULTS: dict[str, float] = {
    "France": 8e6,   "England": 8e6,    "Spain": 8e6,   "Germany": 8e6,
    "Portugal": 7e6, "Netherlands": 7e6,"Belgium": 6e6,  "Brazil": 7e6,
    "Argentina": 7e6,"Croatia": 5e6,    "Switzerland": 5e6, "Norway": 5e6,
    "Sweden": 4e6,   "Austria": 4e6,    "Scotland": 4e6, "Czechia": 3e6,
    "Türkiye": 4e6,  "Bosnia and Herzegovina": 2e6,
    "Colombia": 4e6, "Uruguay": 4e6,    "Ecuador": 3e6,  "Paraguay": 2e6,
    "USA": 4e6,      "Mexico": 3e6,     "Canada": 4e6,   "Panama": 1.5e6,
    "Haiti": 1e6,    "Curaçao": 1e6,
    "Japan": 3e6,    "South Korea": 3e6,"Australia": 2e6,"Saudi Arabia": 1.5e6,
    "Iran": 1.5e6,   "Iraq": 0.5e6,     "Uzbekistan": 1e6, "Jordan": 0.5e6,
    "Qatar": 0.5e6,
    "Morocco": 3e6,  "Senegal": 3e6,    "Algeria": 2e6,  "Ghana": 2e6,
    "Ivory Coast": 2e6, "Egypt": 1.5e6, "Tunisia": 1.5e6,"Cape Verde": 1e6,
    "South Africa": 1e6, "DR Congo": 1e6,"New Zealand": 0.5e6,
}

# GK market value multiplier (GKs undervalued vs outfield in transfer market)
GK_MV_MULT = 2.5

# Market value breakpoints (value_eur, rating) — highest to lowest
MV_BREAKPOINTS = [
    (150_000_000, 95), (100_000_000, 90), ( 80_000_000, 87),
    ( 60_000_000, 83), ( 40_000_000, 78), ( 30_000_000, 74),
    ( 20_000_000, 68), ( 15_000_000, 64), ( 10_000_000, 58),
    (  7_000_000, 53), (  5_000_000, 48), (  3_000_000, 42),
    (  2_000_000, 37), (  1_000_000, 30), (    500_000, 23),
    (          0, 15),
]

PEAK_AGES = {"GK": (27, 33), "DF": (25, 31), "MF": (24, 30), "FW": (23, 29)}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def market_value_to_rating(mv: float) -> float:
    if mv >= 150_000_000:
        return 95.0
    for i, (threshold, rating) in enumerate(MV_BREAKPOINTS):
        if mv >= threshold:
            if i == 0:
                return float(rating)
            upper_val, upper_rating = MV_BREAKPOINTS[i - 1]
            frac = (mv - threshold) / (upper_val - threshold)
            return rating + frac * (upper_rating - rating)
    return 15.0


def calc_age(dob_str: str) -> float:
    if not dob_str:
        return 0.0
    try:
        dob = datetime.strptime(dob_str[:10], "%Y-%m-%d").date()
        return (date.today() - dob).days / 365.25
    except ValueError:
        return 0.0


def age_adjustment(dob_str: str, pos: str) -> float:
    age = calc_age(dob_str)
    if age <= 0:
        return 0.0
    lo, hi = PEAK_AGES.get(pos, (24, 30))
    if lo <= age <= hi:
        return 1.0
    years_off = (lo - age) if age < lo else (age - hi)
    if years_off <= 2:
        return 0.0
    return max(-3.0, -(years_off - 2))


# ---------------------------------------------------------------------------
# Step 1 — Transfermarkt CSV
# ---------------------------------------------------------------------------

def step1_download_tm(force: bool = False) -> Path:
    out = RAW_DIR / "players.csv"
    if out.exists() and not force:
        print("[Step 1] players.csv already cached — skipping download.")
        return out
    print("[Step 1] Downloading Transfermarkt players dataset...")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    resp = requests.get(TM_URL, timeout=120)
    resp.raise_for_status()
    content = gzip.decompress(resp.content)
    out.write_bytes(content)
    print(f"[Step 1] Saved {len(content)/1e6:.1f} MB → {out}")
    return out


# ---------------------------------------------------------------------------
# Step 2 — Scorers from football-data.org
# ---------------------------------------------------------------------------

def step2_fetch_scorers(force: bool = False) -> dict[str, dict]:
    SCORERS_DIR.mkdir(parents=True, exist_ok=True)
    all_scorers: dict[str, dict] = {}

    for i, league in enumerate(LEAGUES):
        cache = SCORERS_DIR / f"{league}.json"
        if cache.exists() and not force:
            print(f"[Step 2] {league}: using cache")
            data = json.loads(cache.read_text())
        else:
            if i > 0:
                print(f"[Step 2] Waiting {API_DELAY}s...")
                time.sleep(API_DELAY)
            url = f"{API_BASE}/competitions/{league}/scorers?limit=100"
            print(f"[Step 2] GET {url}")
            resp = requests.get(url, headers=API_HEADERS, timeout=30)
            if resp.status_code == 429:
                print("[Step 2] Rate-limited — waiting 60s...")
                time.sleep(60)
                resp = requests.get(url, headers=API_HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            cache.write_text(json.dumps(data, indent=2))
            print(f"[Step 2] {league}: {len(data.get('scorers', []))} scorers")

        for entry in data.get("scorers", []):
            p = entry.get("player", {})
            key = normalize(p.get("name", ""))
            if not key:
                continue
            goals   = entry.get("goals") or 0
            assists = entry.get("assists") or 0
            if key not in all_scorers:
                all_scorers[key] = {"goals": 0, "assists": 0}
            # take max across leagues (player may appear in CL + domestic)
            all_scorers[key]["goals"]   = max(all_scorers[key]["goals"],   goals)
            all_scorers[key]["assists"] = max(all_scorers[key]["assists"], assists)

    print(f"[Step 2] {len(all_scorers)} unique scorers indexed.")
    return all_scorers


# ---------------------------------------------------------------------------
# Step 3 — Build TM index for fuzzy market-value lookup
# ---------------------------------------------------------------------------

def step3_build_tm_index(csv_path: Path) -> tuple[dict, dict]:
    """
    Returns:
        name_index : {normalized_name → {mv, dob, raw_pos}}
        token_index: {word → set(normalized_names)}   for fast candidate search
    """
    print("[Step 3] Building Transfermarkt name index...")
    name_index:  dict[str, dict]      = {}
    token_index: dict[str, set[str]]  = defaultdict(set)

    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []

        mv_col  = next((h for h in headers if "market_value_in_eur" in h.lower() and "highest" not in h.lower()), "market_value_in_eur")
        dob_col = next((h for h in headers if "date_of_birth" in h.lower()), "date_of_birth")
        pos_col = next((h for h in headers if h.lower() == "sub_position"), None) or next((h for h in headers if "position" in h.lower()), "position")
        name_col = next((h for h in headers if h.lower() == "name"), "name")

        for row in reader:
            raw_name = (row.get(name_col) or "").strip()
            if not raw_name:
                continue
            norm = normalize(raw_name)
            try:
                mv = float(row.get(mv_col) or 0)
            except ValueError:
                mv = 0.0

            # Keep highest MV if same normalized name appears twice
            if norm not in name_index or mv > name_index[norm]["mv"]:
                name_index[norm] = {
                    "mv":      mv,
                    "dob":     (row.get(dob_col) or "").strip(),
                    "raw_pos": (row.get(pos_col) or "").strip(),
                }

            for word in norm.split():
                if len(word) >= 3:
                    token_index[word].add(norm)

    print(f"[Step 3] TM index: {len(name_index):,} players, {len(token_index):,} tokens.")
    return name_index, token_index


def lookup_mv(player_name: str, name_index: dict, token_index: dict):
    """Fuzzy-match a squad player name against TM index. Returns {mv, dob} or None."""
    norm = normalize(player_name)

    # 1. Exact match
    if norm in name_index:
        return name_index[norm]

    # 2. Collect candidates via shared tokens
    words = [w for w in norm.split() if len(w) >= 3]
    candidates: set[str] = set()
    for w in words:
        candidates.update(token_index.get(w, set()))

    # 3. Score candidates with SequenceMatcher
    best_score = 0.72   # minimum threshold
    best_match = None
    for cand in candidates:
        score = SequenceMatcher(None, norm, cand).ratio()
        if score > best_score:
            best_score = score
            best_match = name_index[cand]

    return best_match


# ---------------------------------------------------------------------------
# Step 4 — Build rosters from squads.json + MV lookup
# ---------------------------------------------------------------------------

def step4_build_rosters(squads_data: dict, name_index: dict, token_index: dict) -> dict[str, list]:
    print("[Step 4] Building rosters from squads.json + TM market-value lookup...")
    rosters: dict[str, list] = {}
    matched = unmatched = 0

    for team, players_raw in squads_data["squads"].items():
        default_mv = TEAM_DEFAULTS.get(team, 1_000_000)
        roster = []

        for p in players_raw:
            name = p["name"]
            pos  = p["pos"]   # GK / DF / MF / FW

            tm = lookup_mv(name, name_index, token_index)
            if tm and tm["mv"] > 0:
                mv  = tm["mv"]
                dob = tm["dob"]
                matched += 1
            else:
                mv  = default_mv
                dob = ""
                unmatched += 1

            # GK multiplier: GKs are undervalued vs outfield in transfer market
            effective_mv = mv * GK_MV_MULT if pos == "GK" else mv

            roster.append({
                "name": name,
                "pos":  pos,
                "team": team,
                "market_value_in_eur": mv,
                "effective_mv": effective_mv,
                "date_of_birth": dob,
                "tm_matched": tm is not None and tm["mv"] > 0,
            })

        rosters[team] = roster

    print(f"[Step 4] TM matched: {matched}, defaults used: {unmatched}")
    return rosters


# ---------------------------------------------------------------------------
# Step 5 — Rate each player
# ---------------------------------------------------------------------------

def step5_rate_players(rosters: dict[str, list], scorers: dict[str, dict]) -> dict[str, list]:
    print("[Step 5] Rating players...")
    scorer_hits = 0

    for team, players in rosters.items():
        for p in players:
            pos = p["pos"]
            base = market_value_to_rating(p["effective_mv"])
            adj  = age_adjustment(p["date_of_birth"], pos)

            key = normalize(p["name"])
            sc  = scorers.get(key)
            if sc:
                goals, assists = sc["goals"], sc["assists"]
                bonus = min(5.0, goals * 0.2 + assists * 0.15)
                scorer_hits += 1
            else:
                goals = assists = 0
                bonus = 0.0

            p["age"]               = round(calc_age(p["date_of_birth"]), 1)
            p["base_rating"]       = round(base, 2)
            p["goal_assist_bonus"] = round(bonus, 2)
            p["age_adjustment"]    = round(adj, 2)
            p["goals"]             = goals
            p["assists"]           = assists
            p["rating"]            = round(min(99.0, base + bonus + adj), 2)

    print(f"[Step 5] Scorer cross-references: {scorer_hits}")
    return rosters


# ---------------------------------------------------------------------------
# Step 6 — SC Power Index
# ---------------------------------------------------------------------------

def select_best_xi(players: list[dict]) -> list[dict]:
    slots = {"GK": 1, "DF": 4, "MF": 3, "FW": 3}
    used: set[str] = set()
    xi: list[dict] = []
    for pos, n in slots.items():
        pool = sorted(
            [p for p in players if p["pos"] == pos and p["name"] not in used],
            key=lambda x: x["rating"], reverse=True
        )
        for p in pool[:n]:
            xi.append(p)
            used.add(p["name"])
    return xi


def calc_xfactor(players: list[dict]) -> float:
    u23 = [p for p in players if 0 < p["age"] < 23 and p["rating"] > 50]
    if not u23:
        return 20.0
    count     = len(u23)
    avg_rtg   = sum(p["rating"] for p in u23) / count
    pos_div   = len({p["pos"] for p in u23})
    return min(100.0, (min(count, 5) / 5 * 40) + (avg_rtg / 80 * 40) + (pos_div / 3 * 20))


def calc_depth(players: list[dict], xi: list[dict]) -> float:
    xi_names = {p["name"] for p in xi}
    bench = sorted(
        [p for p in players if p["name"] not in xi_names],
        key=lambda x: x["rating"], reverse=True
    )
    bench12 = bench[:12]
    if not bench12:
        return 20.0
    bench_avg   = sum(p["rating"] for p in bench12) / len(bench12)
    depth_ratio = len(bench12) / 12
    pos_counts: dict[str, int] = {}
    for p in bench:
        if p["rating"] > 40:
            pos_counts[p["pos"]] = pos_counts.get(p["pos"], 0) + 1
    covered = sum(1 for v in pos_counts.values() if v >= 2)
    return min(100.0, (bench_avg * 0.5) + (depth_ratio * 30) + (covered / 4 * 20))


def step6_calc_index(rosters: dict[str, list], squads_data: dict) -> list[dict]:
    print("[Step 6] Calculating SC Power Index...")
    team_to_group = squads_data["team_to_group"]
    confed_map    = squads_data["confederations"]
    results: list[dict] = []

    for team, players in rosters.items():
        players.sort(key=lambda x: x["rating"], reverse=True)
        xi = select_best_xi(players)
        sq = (sum(p["rating"] for p in xi) / len(xi)) if xi else 0.0
        xf = calc_xfactor(players)
        sd = calc_depth(players, xi)
        rf = 50.0  # placeholder

        sci = round(sq * 0.50 + xf * 0.15 + sd * 0.20 + rf * 0.15, 2)

        results.append({
            "team":           team,
            "group":          team_to_group.get(team, "?"),
            "confederation":  confed_map.get(team, "Other"),
            "sc_power_index": sci,
            "squad_quality":  round(sq, 2),
            "x_factor":       round(xf, 2),
            "squad_depth":    round(sd, 2),
            "recent_form":    rf,
            "squad_size":     len(players),
            "best_xi":        [p["name"] for p in xi],
        })
        print(f"[Step 6]   Gr.{team_to_group.get(team,'?')} {team:<28s}  SCI={sci:5.1f}  SQ={sq:5.1f}  XF={xf:5.1f}  SD={sd:5.1f}")

    results.sort(key=lambda x: x["sc_power_index"], reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i
    return results


# ---------------------------------------------------------------------------
# Step 7 — Save outputs
# ---------------------------------------------------------------------------

def step7_save(results: list[dict], rosters: dict[str, list]):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEAM_DIR.mkdir(parents=True, exist_ok=True)

    # Rankings JSON
    out_json = OUTPUT_DIR / "sc_power_index.json"
    out_json.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[Step 7] {out_json}")

    # Rankings CSV
    fieldnames = ["rank", "group", "team", "confederation", "sc_power_index",
                  "squad_quality", "x_factor", "squad_depth", "recent_form", "squad_size"]
    out_csv = OUTPUT_DIR / "sc_power_index.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    print(f"[Step 7] {out_csv}")

    # Per-team detail JSONs
    for team, players in rosters.items():
        slug = team.lower().replace(" ", "_").replace(",", "").replace("ü", "u").replace("ç", "c")
        out  = TEAM_DIR / f"{slug}.json"
        out.write_text(json.dumps(
            sorted(players, key=lambda x: x["rating"], reverse=True),
            indent=2, ensure_ascii=False
        ))
    print(f"[Step 7] {len(rosters)} team detail files → {TEAM_DIR}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 68)
    print("SquadCheck Pipeline v2 — 48-team WC 2026 SC Power Index")
    print("=" * 68)

    squads_data = json.loads(SQUADS_FILE.read_text())

    csv_path    = step1_download_tm()
    scorers     = step2_fetch_scorers()
    name_index, token_index = step3_build_tm_index(csv_path)
    rosters     = step4_build_rosters(squads_data, name_index, token_index)
    rosters     = step5_rate_players(rosters, scorers)
    results     = step6_calc_index(rosters, squads_data)
    step7_save(results, rosters)

    print()
    print("=" * 68)
    print("FINAL RANKINGS — SC Power Index (Top 20)")
    print("=" * 68)
    for r in results[:20]:
        bar = "█" * int(r["sc_power_index"] / 4)
        print(f"  #{r['rank']:2d}  Gr.{r['group']}  {r['team']:<28s}  {r['sc_power_index']:5.1f}  {bar}")


if __name__ == "__main__":
    main()
