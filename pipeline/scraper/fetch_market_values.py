#!/usr/bin/env python3
"""Downloads Transfermarkt players CSV and caches it."""
import csv
import gzip
import io
import requests
from pathlib import Path

TM_URL = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/players.csv.gz"
ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = ROOT / "data" / "cache" / "transfermarkt.csv"
KEEP_COLS = {"name", "position", "date_of_birth", "country_of_citizenship",
             "current_club_domestic_competition_id", "market_value_in_eur"}


def main(force: bool = False) -> None:
    if CACHE_FILE.exists() and not force:
        print(f"[fetch_market_values] Already cached: {CACHE_FILE}")
        return
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    print(f"[fetch_market_values] Downloading from {TM_URL}...")
    resp = requests.get(TM_URL, timeout=120)
    resp.raise_for_status()
    raw_csv = gzip.decompress(resp.content).decode("utf-8")

    reader = csv.DictReader(io.StringIO(raw_csv))
    fieldnames_original = reader.fieldnames or []
    # Find columns that match (case-insensitive)
    col_map = {}
    for orig in fieldnames_original:
        lower = orig.lower().strip()
        if lower in KEEP_COLS:
            col_map[lower] = orig

    out_cols = [col_map[c] for c in KEEP_COLS if c in col_map]
    if not out_cols:
        # Fallback: save all columns
        CACHE_FILE.write_text(raw_csv, encoding="utf-8")
        print(f"[fetch_market_values] (all columns) Saved → {CACHE_FILE}")
        return

    out_buf = io.StringIO()
    writer = csv.DictWriter(out_buf, fieldnames=out_cols, extrasaction="ignore")
    writer.writeheader()
    count = 0
    for row in reader:
        writer.writerow({k: row.get(k, "") for k in out_cols})
        count += 1

    CACHE_FILE.write_text(out_buf.getvalue(), encoding="utf-8")
    print(f"[fetch_market_values] Saved {count:,} rows ({len(out_cols)} cols) → {CACHE_FILE}")


if __name__ == "__main__":
    main(force=True)
