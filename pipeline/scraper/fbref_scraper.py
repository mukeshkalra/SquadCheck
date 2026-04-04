import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Comment

BASE_URL = "https://fbref.com/en/squads/{squad_id}/{team_name}-Men-Stats"
RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
HEADERS = {
    "User-Agent": "SquadCheck/1.0 (squadcheck.club)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://fbref.com/",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}
DELAY = 4  # seconds between requests

TABLES = [
    "stats_standard",
    "stats_shooting",
    "stats_passing",
    "stats_defense",
    "stats_gca",
    "stats_possession",
]

TEAMS = [
    {"squad_id": "2f764e8c", "team_name": "United-States"},
]


def fetch_page(url: str) -> BeautifulSoup:
    print(f"  GET {url}")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    # FBref hides some tables inside HTML comments — uncomment them before parsing
    html = resp.text
    html = re.sub(r"<!--(.*?)-->", r"\1", html, flags=re.DOTALL)
    return BeautifulSoup(html, "lxml")


def parse_table(soup: BeautifulSoup, table_id: str) -> list[dict]:
    table = soup.find("table", {"id": table_id})
    if table is None:
        print(f"    [warn] table #{table_id} not found")
        return []

    # Build column names from <thead>; FBref uses two header rows —
    # the second row has the actual stat labels.
    thead = table.find("thead")
    header_rows = thead.find_all("tr")
    header_row = header_rows[-1]  # last row = actual column names
    cols = []
    for th in header_row.find_all("th"):
        stat = th.get("data-stat", th.get_text(strip=True))
        cols.append(stat)

    rows = []
    tbody = table.find("tbody")
    if tbody is None:
        return []
    for tr in tbody.find_all("tr"):
        if "thead" in tr.get("class", []) or "spacer" in tr.get("class", []):
            continue
        cells = tr.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        row = {}
        for col, cell in zip(cols, cells):
            # Prefer the data-stat attribute as the key for consistency
            key = cell.get("data-stat") or col
            # Prefer link text for player name cells
            a = cell.find("a")
            row[key] = a.get_text(strip=True) if a else cell.get_text(strip=True)
        rows.append(row)

    print(f"    [{table_id}] {len(rows)} rows, {len(cols)} cols")
    return rows


def merge_player_tables(tables_data: dict[str, list[dict]]) -> list[dict]:
    """Merge all table rows by player name, using 'player' as the join key."""
    merged: dict[str, dict] = {}

    for table_id, rows in tables_data.items():
        for row in rows:
            name = row.get("player", "").strip()
            if not name or name.lower() in ("player", ""):
                continue
            if name not in merged:
                merged[name] = {"player": name}
            # Prefix non-identity columns with table name to avoid collisions
            for k, v in row.items():
                if k in ("player", "nationality", "pos", "age", "born", "team"):
                    merged[name][k] = v  # shared identity fields, no prefix
                else:
                    merged[name][f"{table_id}__{k}"] = v

    return list(merged.values())


def scrape_team(squad_id: str, team_name: str) -> list[dict]:
    url = BASE_URL.format(squad_id=squad_id, team_name=team_name)
    print(f"\nScraping {team_name} ({squad_id})")
    soup = fetch_page(url)

    tables_data = {}
    for i, table_id in enumerate(TABLES):
        tables_data[table_id] = parse_table(soup, table_id)
        if i < len(TABLES) - 1:
            print(f"    waiting {DELAY}s...")
            time.sleep(DELAY)

    return merge_player_tables(tables_data)


def save(team_name: str, players: list[dict]):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    slug = team_name.lower().replace("-", "_")
    out = RAW_DIR / f"{slug}.json"
    out.write_text(json.dumps(players, indent=2, ensure_ascii=False))
    print(f"\nSaved {len(players)} players -> {out}")


def main():
    for team in TEAMS:
        players = scrape_team(team["squad_id"], team["team_name"])
        save(team["team_name"], players)
        time.sleep(DELAY)


if __name__ == "__main__":
    main()
