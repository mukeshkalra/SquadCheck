#!/usr/bin/env python3
"""
SquadCheck V1 — Local development server.

Serves the fpl/ directory as a static site and provides the squad-check API.

Usage
-----
    python3 fpl/server.py          # port 8080
    python3 fpl/server.py 3000     # custom port

Then open:  http://localhost:8080/fpl/

How it works
------------
POST /api/squad-check
    1. Fetches live FPL bootstrap-static (cached 5 min)
    2. Auto-builds a representative 15-player squad from top current-season players
       (until OCR is implemented; the scanner returns UNSUPPORTED for image bytes)
    3. Fetches element-summary for each player (cached indefinitely per session)
    4. Runs build_params + run_pipeline
    5. Returns the V1 JSON payload

All other requests are served as static files from the project root.
"""

import sys
import os
import io
import cgi
import json
import time
import socketserver
import http.server
import urllib.request
from pathlib import Path

# ── project path ─────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fpl.engine.scanner    import scan_squad, VALID, VIEW_PITCH
from fpl.engine.projection import build_params
from fpl.engine.pipeline   import run_pipeline

# ── FPL API ───────────────────────────────────────────────────────────────────
_BS_URL  = "https://fantasy.premierleague.com/api/bootstrap-static/"
_SUM_URL = "https://fantasy.premierleague.com/api/element-summary/{}/"
_HDRS    = {"User-Agent": "Mozilla/5.0 (compatible; SquadCheck/1.0; +https://squadcheck.club)"}

_cache = {
    "bootstrap":    None,
    "bootstrap_ts": 0,
    "summaries":    {},
    "params":       None,
    "params_ts":    0,
}
_BOOTSTRAP_TTL = 300   # 5 min
_PARAMS_TTL    = 300


def _fetch(url):
    req = urllib.request.Request(url, headers=_HDRS)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def get_bootstrap():
    now = time.time()
    if _cache["bootstrap"] is None or now - _cache["bootstrap_ts"] > _BOOTSTRAP_TTL:
        print("  → fetching bootstrap-static …")
        _cache["bootstrap"] = _fetch(_BS_URL)
        _cache["bootstrap_ts"] = now
    return _cache["bootstrap"]


def get_summary(pid):
    if pid not in _cache["summaries"]:
        _cache["summaries"][pid] = _fetch(_SUM_URL.format(pid))
    return _cache["summaries"][pid]


def get_params(bootstrap):
    now = time.time()
    if _cache["params"] is None or now - _cache["params_ts"] > _PARAMS_TTL:
        _cache["params"] = build_params(bootstrap)
        _cache["params_ts"] = now
    return _cache["params"]


# ── Screenshot squad definition ───────────────────────────────────────────────
# Exact 15 players from fpl/assets/example-squad.jpg, identified visually.
# Formation: 3-4-3 (Pickford; Calafiori, Diop, Kayode; Cherki, Gibbs-White,
#                    Rogers, Semenyo; Isak, Haaland, Barry)
# Club constraint: BHA×3, MCI×3, EVE×2 — all valid (max 3 per club).
#
# Captain:      Gibbs-White (NFO) — "C" badge visible on screenshot
# Vice-captain: Semenyo (MCI)     — "V" badge visible on screenshot

_SCREENSHOT_PLAYERS = [
    # (web_name_in_bootstrap, element_type, is_starting)
    # Starting XI
    ("Pickford",    1, True),    # GKP EVE
    ("Calafiori",   2, True),    # DEF ARS
    ("Diop",        2, True),    # DEF IPS
    ("Kayode",      2, True),    # DEF BRE
    ("Cherki",      3, True),    # MID MCI
    ("Gibbs-White", 3, True),    # MID NFO  ← Captain
    ("Rogers",      3, True),    # MID CHE
    ("Semenyo",     3, True),    # MID MCI  ← Vice
    ("Isak",        4, True),    # FWD LIV
    ("Haaland",     4, True),    # FWD MCI
    ("Barry",       4, True),    # FWD EVE
    # Bench (order matches FPL: GKP, 1.MID, 2.DEF, 3.DEF)
    ("Verbruggen",  1, False),   # GKP BHA
    ("Yalcouyé",   3, False),   # MID BHA  (1. MID)
    ("De Cuyper",   2, False),   # DEF BHA  (2. DEF)
    ("Mendy",       2, False),   # DEF HUL  (3. DEF)
]
_SCREENSHOT_CAPTAIN = "Gibbs-White"
_SCREENSHOT_VICE    = "Semenyo"


def build_screenshot_squad(bootstrap):
    """
    Build the scan_dict and player-id list from the hardcoded screenshot squad.
    Resolves web_names against bootstrap to get player IDs for summaries.
    Returns (scan_dict, all_player_ids, captain_id, vice_captain_id) or raises.
    """
    elements = {e["id"]: e for e in bootstrap.get("elements", [])}
    by_name  = {}
    for e in bootstrap.get("elements", []):
        wn = (e.get("web_name", "") or "").lower().strip()
        by_name.setdefault(wn, []).append(e)

    scan_players = []
    all_ids      = []
    captain_id   = None
    vice_id      = None

    for name, pos, is_start in _SCREENSHOT_PLAYERS:
        q       = name.lower().strip()
        matches = [e for e in by_name.get(q, []) if e.get("element_type") == pos]
        if len(matches) != 1:
            raise RuntimeError(
                "Screenshot squad: could not uniquely resolve %r (pos=%d) — "
                "found %d matches" % (name, pos, len(matches))
            )
        pid = matches[0]["id"]
        all_ids.append(pid)
        scan_players.append({"name": name, "position": pos, "is_starting": is_start})
        if name == _SCREENSHOT_CAPTAIN:
            captain_id = pid
        if name == _SCREENSHOT_VICE:
            vice_id = pid

    scan_dict = {"view_type": VIEW_PITCH, "players": scan_players}
    return scan_dict, all_ids, captain_id, vice_id


def build_demo_squad(bootstrap):
    """
    DEV / TEST MODE ONLY — auto-select 15 players from live bootstrap.
    Enable with env var SQUADCHECK_DEMO=1 or CLI flag --demo.
    Formation: 1 GKP + (4 DEF, 4 MID, 2 FWD) + bench.
    Returns (scan_dict, all_player_ids) or (None, None).
    """
    elements = bootstrap.get("elements", [])
    avail = [e for e in elements
             if e.get("status") == "a"
             and e.get("starts", 0) >= 3
             and not e.get("removed", False)]

    by_pos = {1: [], 2: [], 3: [], 4: []}
    for e in avail:
        p = e.get("element_type")
        if p in by_pos:
            by_pos[p].append(e)
    for p in by_pos:
        by_pos[p].sort(key=lambda x: float(x.get("points_per_game") or 0), reverse=True)

    def pick(pool, n, tally):
        chosen = []
        for e in pool:
            if len(chosen) == n: break
            t = e["team"]
            if tally.get(t, 0) >= 3: continue
            tally[t] = tally.get(t, 0) + 1
            chosen.append(e)
        return chosen

    tally = {}
    gkps = pick(by_pos[1], 2, tally)
    defs = pick(by_pos[2], 5, tally)
    mids = pick(by_pos[3], 5, tally)
    fwds = pick(by_pos[4], 3, tally)

    if any(len(g) < n for g, n in [(gkps,2),(defs,5),(mids,5),(fwds,3)]):
        return None, None

    xi_ids = ({gkps[0]["id"]}
              | {e["id"] for e in defs[:4]}
              | {e["id"] for e in mids[:4]}
              | {fwds[0]["id"], fwds[1]["id"]})
    all_players = gkps + defs + mids + fwds
    scan_players = [{"name": e.get("web_name",""), "position": e.get("element_type"),
                     "is_starting": e["id"] in xi_ids} for e in all_players]
    return {"view_type": VIEW_PITCH, "players": scan_players}, [e["id"] for e in all_players]


# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        # Only log API calls; args[0] may be HTTPStatus (not a string) on error paths
        try:
            first = str(args[0]) if args else ""
            if "/api/" in first:
                print("  %s %s" % (self.command, first))
        except Exception:
            pass

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        if self.path.rstrip("/") != "/api/squad-check":
            self.send_error(404)
            return
        try:
            payload = self._squad_check()
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(200)         # status line FIRST
            self._cors()                    # then CORS headers
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            status = payload.get("status", "?")
            action = payload.get("action", "?")
            gw     = payload.get("gameweek", "?")
            print("  ✓ GW%s  status=%s  action=%s" % (gw, status, action))
        except Exception as exc:
            import traceback; traceback.print_exc()
            err  = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _read_uploaded_bytes(self):
        """
        Extract the raw image bytes from a multipart/form-data upload.

        Uses cgi.FieldStorage (stdlib) for reliable boundary parsing —
        handles all browser-specific boundary formats without manual splitting.
        Falls back to the raw body if parsing fails or the field is missing.
        """
        content_type   = self.headers.get("Content-Type", "")
        content_length = int(self.headers.get("Content-Length", 0))

        if not content_length:
            return b""

        body = self.rfile.read(content_length)

        if "multipart/form-data" not in content_type:
            return body   # raw POST — pass straight through

        try:
            environ = {
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE":   content_type,
                "CONTENT_LENGTH": str(content_length),
            }
            form = cgi.FieldStorage(
                fp=io.BytesIO(body),
                headers=self.headers,
                environ=environ,
            )
            if "screenshot" in form:
                return form["screenshot"].file.read()
        except Exception as exc:
            print("  [multipart] parse error: %s" % exc)
            import traceback; traceback.print_exc()

        return body   # fallback: return full body

    def _squad_check(self):
        """
        Production path  — browser upload:
          1. Read actual image bytes from the multipart form.
          2. Pass bytes directly to scan_squad().
          3. If the scanner returns anything other than VALID (including
             UNSUPPORTED, which is the current V1 result for image bytes
             because OCR is not yet implemented), return an error payload.
             The frontend maps this to the appropriate error screen.
          4. On VALID only: fetch element-summaries and run the pipeline.

        DEV / TEST mode (--demo flag or SQUADCHECK_DEMO=1 env var):
          Uses a pre-structured squad dict so the full pipeline can be
          exercised without a working OCR implementation.
          This path is completely separate from the production upload path.
        """
        demo_mode = os.environ.get("SQUADCHECK_DEMO") == "1" or _DEMO_FLAG

        if demo_mode:
            # ── DEV / TEST — structured dict, not image bytes ────────────────
            print("  [demo mode] building squad from structured dict …")
            bootstrap  = get_bootstrap()
            params     = get_params(bootstrap)
            scan_dict, all_ids, captain_id, vice_captain_id = \
                build_screenshot_squad(bootstrap)
            scan_result = scan_squad(scan_dict)           # dict → VALID
            scan_result["captain_id"]      = captain_id
            scan_result["vice_captain_id"] = vice_captain_id
        else:
            # ── PRODUCTION — actual uploaded image bytes ──────────────────────
            image_bytes = self._read_uploaded_bytes()
            n = len(image_bytes)
            print("  [upload] %d bytes received → passing to scanner …" % n)

            scan_result = scan_squad(image_bytes)         # bytes → currently UNSUPPORTED
            status      = scan_result.get("status")
            print("  [scanner] status=%s" % status)

            if status != VALID:
                # Scanner could not produce a valid 15-player squad.
                # Return the error payload; the UI handles UNSUPPORTED / PARTIAL /
                # AMBIGUOUS with specific messages.  Do NOT proceed to projection.
                return {
                    "status":          "SCAN_FAIL",
                    "scanner_status":  status,
                    "view_type":       scan_result.get("view_type", "UNKNOWN"),
                    "players":         [],
                    "submitted_xi":    None,
                    "recommended_xi":  None,
                    "delta":           0.0,
                    "action":          None,
                    "substitutions":   [],
                    "threshold":       None,
                    "gameweek":        None,
                    "captain_id":      None,
                    "vice_captain_id": None,
                    "captain_note":    "",
                    "message":         scan_result.get("message", ""),
                }

            # VALID scan — fetch bootstrap and proceed
            bootstrap  = get_bootstrap()
            params     = get_params(bootstrap)
            # Captain / vice not yet extractable from image bytes (no OCR)
            scan_result["captain_id"]      = None
            scan_result["vice_captain_id"] = None

        # ── Common: fetch element-summaries and run full pipeline ─────────────
        # Resolve player IDs from the scan_result so we know which summaries to fetch
        from fpl.engine.pipeline import resolve_players
        elements = bootstrap.get("elements", [])
        resolved, _ = resolve_players(scan_result.get("players", []), elements)
        all_ids = [r["player_id"] for r in resolved if r["player_id"] is not None]

        print("  → fetching %d element-summaries …" % len(all_ids))
        summaries = {pid: get_summary(pid) for pid in all_ids}

        return run_pipeline(
            scan_result       = scan_result,
            bootstrap         = bootstrap,
            element_summaries = summaries,
            params            = params,
            threshold         = 0.5,
        )


# ── Main ──────────────────────────────────────────────────────────────────────

class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


_DEMO_FLAG = False   # set True by --demo CLI flag


def main():
    global _DEMO_FLAG
    args = sys.argv[1:]
    _DEMO_FLAG = "--demo" in args
    args = [a for a in args if a != "--demo"]
    port = int(args[0]) if args else 8080

    server = ThreadingServer(("", port), Handler)
    mode   = "DEMO (auto-squad)" if _DEMO_FLAG else "PRODUCTION (screenshot squad)"
    print()
    print("  SquadCheck V1  —  http://localhost:%d/fpl/" % port)
    print("  Mode: %s" % mode)
    print("  Press Ctrl-C to stop.")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()
