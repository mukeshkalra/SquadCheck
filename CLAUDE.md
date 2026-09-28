# CLAUDE.md

## What This Is

SquadCheck is a live FPL decision-support app at [squadcheck.club](https://squadcheck.club).
A user uploads a screenshot of their FPL squad; the app returns xPts projections, bench-swap suggestions, and a captain recommendation for the upcoming gameweek.
The old World Cup companion app is on the backburner — ignore it entirely.

## Folder Layout (FPL only)

```
fpl/
  index.html            # SPA frontend (the live product)
  server.py             # Local dev server — POST /api/squad-check
  engine/
    scanner.py          # Parse screenshot → ScanResult
    pipeline.py         # Orchestrate full flow (scan → filter → match → project → bench)
    projection.py       # Compute xPts per player per GW
    bench.py            # Bench/start swap optimiser
    tests/              # unittest suite
      test_pipeline.py
      test_bench.py
      test_projection.py
      test_scanner_screenshots.py
  V2_IDEA_LOG.md        # V2 ideas (reference only, don't modify)

api/
  squad-check.py        # Vercel serverless mirror of server.py

vercel.json             # Routing config
```

## Pipeline Steps

1. **scanner.py** — `scan_squad(source)`: accepts image bytes or dict; runs Google Vision OCR (`_run_ocr_google_vision`), detects view type (`_detect_view_type`: pitch / list / partial), parses blocks, extracts player names + captain/vc badges + starter vs bench split. `_validate` enforces 11 starters.
2. **pipeline.py** — `bootstrap_filter(players, elements)`: cleans noisy tokens against FPL bootstrap data. `resolve_squad_smart` / `resolve_players` / `_find_id`: match cleaned names to FPL player IDs. `run_pipeline` / `_run`: top-level orchestrator.
3. **projection.py** — `build_params(bootstrap)`: pre-computes league-wide xG/xGC baselines. `compute_xpts(player, ...)`: projects FPL points using minutes probability, clean-sheet odds, goal/assist rates.
4. **bench.py** — `optimise_bench(...)`: enumerates all valid XIs from the 15-player squad and returns the highest-xPts legal swap.

## Running Tests

```bash
# from repo root
python3 -m unittest fpl.engine.tests.test_pipeline -v 2>&1 | tail -40
python3 -m unittest fpl.engine.tests.test_scanner_screenshots -v 2>&1 | tail -40
# run all at once
python3 -m unittest discover -s fpl/engine/tests -v 2>&1 | tail -50
```

## Local Dev

```bash
python3 fpl/server.py          # http://localhost:8080/fpl/
python3 fpl/server.py 3000     # custom port
```

## Deploys

Push to `main` → Vercel auto-deploys via GitHub integration.
`vercel.json` rewrites `/` → `/fpl/`; `api/*.py` files are serverless functions.
No build step — static files served as-is.

## Rules

- Short replies; don't summarise what you just did.
- Don't re-read files already read in this session.
- Pipe all command/test output through `tail`/`head`/`grep` — never dump raw.
- Read files with `offset`/`limit` — never load a whole large file unnecessarily.
- Ask before touching more than 2 files at once.
- No refactors beyond exactly what is asked.
