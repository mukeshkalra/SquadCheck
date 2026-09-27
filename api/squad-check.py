"""
SquadCheck V1 — Vercel Serverless Function
POST /api/squad-check

Replaces fpl/server.py for production (Vercel Linux environment).
OCR handled by Google Cloud Vision (GOOGLE_VISION_API_KEY env var).
Local dev continues to use fpl/server.py with the Swift binary.

Security
--------
- Upload capped at 10 MB
- CORS restricted to squadcheck.club in production
- No credentials in code; API key from environment variable
"""

import sys
import io
import cgi
import os
import json
import time
import urllib.request
import concurrent.futures
from pathlib import Path
from http.server import BaseHTTPRequestHandler

# Add project root so fpl.engine.* imports resolve
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fpl.engine.scanner    import scan_squad, VALID
from fpl.engine.projection import build_params
from fpl.engine.pipeline   import run_pipeline, resolve_players, bootstrap_filter

# ── Config ────────────────────────────────────────────────────────────────────
_FPL_BS   = "https://fantasy.premierleague.com/api/bootstrap-static/"
_FPL_SUM  = "https://fantasy.premierleague.com/api/element-summary/{}/"
_HEADERS  = {"User-Agent": "Mozilla/5.0 (compatible; SquadCheck/1.0)"}
_MAX_BODY = 10 * 1024 * 1024   # 10 MB upload limit (security M1)
_THRESHOLD = 0.5

# Module-level cache — persists across warm Vercel invocations
_cache: dict = {"bootstrap": None, "bs_ts": 0, "params": None, "p_ts": 0}
_CACHE_TTL = 300   # 5 minutes


# ── FPL API helpers ───────────────────────────────────────────────────────────

def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _bootstrap() -> dict:
    now = time.time()
    if not _cache["bootstrap"] or now - _cache["bs_ts"] > _CACHE_TTL:
        _cache["bootstrap"] = _fetch(_FPL_BS)
        _cache["bs_ts"]     = now
        _cache["params"]    = None   # invalidate derived params
    return _cache["bootstrap"]


def _params(bootstrap: dict) -> dict:
    now = time.time()
    if not _cache["params"] or now - _cache["p_ts"] > _CACHE_TTL:
        _cache["params"] = build_params(bootstrap)
        _cache["p_ts"]   = now
    return _cache["params"]


def _summaries(player_ids: list) -> dict:
    """Fetch all 15 element-summaries in parallel to fit within Vercel's 10s limit."""
    def _one(pid):
        return pid, _fetch(_FPL_SUM.format(pid))
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
        return dict(ex.map(_one, player_ids))


# ── Request handler ───────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            payload = self._process()
            body    = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type",   "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            err = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
            self._cors()
            self.send_header("Content-Type",   "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    # ── CORS ─────────────────────────────────────────────────────────────────
    def _cors(self):
        # TODO (M2): restrict origin to "https://squadcheck.club" before scaling
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    # ── Multipart image extraction ────────────────────────────────────────────
    def _image_bytes(self) -> bytes:
        ct = self.headers.get("Content-Type", "")
        cl = int(self.headers.get("Content-Length", 0))

        if cl > _MAX_BODY:
            raise ValueError(f"Upload too large ({cl:,} bytes; max {_MAX_BODY:,})")

        body = self.rfile.read(cl)

        if "multipart/form-data" not in ct:
            return body   # raw POST

        form = cgi.FieldStorage(
            fp      = io.BytesIO(body),
            headers = self.headers,
            environ = {
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE":   ct,
                "CONTENT_LENGTH": str(cl),
            },
        )
        return form["screenshot"].file.read() if "screenshot" in form else body

    # ── Pipeline ──────────────────────────────────────────────────────────────
    def _process(self) -> dict:
        image_bytes = self._image_bytes()

        # Capture scanner stderr ([diag] lines) into the response for debugging.
        # Never captures GOOGLE_VISION_API_KEY or image contents.
        import io as _io
        _diag_buf    = _io.StringIO()
        _real_stderr = sys.stderr
        sys.stderr   = _diag_buf
        try:
            scan_result = scan_squad(image_bytes)
        finally:
            sys.stderr = _real_stderr
        _diag_log = _diag_buf.getvalue().strip()

        def _fail_response(scanner_status, message):
            return {
                "status":          "SCAN_FAIL",
                "scanner_status":  scanner_status,
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
                "message":         message,
                "_diag_log":       _diag_log,
                "_scanned": [
                    {"name": p.get("name",""), "is_starting": p.get("is_starting"),
                     "pos": p.get("position")}
                    for p in scan_result.get("players", [])
                ],
            }

        # Gate 1 — scanner must produce a structurally valid squad
        if scan_result.get("status") != VALID:
            return _fail_response(
                scan_result.get("status"),
                scan_result.get("message", ""),
            )

        # Gate 2 — bootstrap validation: strip any OCR noise that passed the
        # scanner's heuristics but isn't a real FPL player.
        bootstrap = _bootstrap()
        params    = _params(bootstrap)
        elements  = bootstrap.get("elements", [])

        raw_players             = scan_result.get("players", [])
        clean_players, rejected = bootstrap_filter(raw_players, elements)

        if len(clean_players) != 15:
            rej_names = ", ".join(p["name"] for p in rejected) if rejected else "—"
            got       = len(clean_players)
            return _fail_response(
                "PARTIAL",
                "Found %d of 15 players. Unrecognised: %s" % (got, rej_names),
            )

        scan_result["players"]         = clean_players
        scan_result["captain_id"]      = None
        scan_result["vice_captain_id"] = None

        resolved, _ = resolve_players(clean_players, elements)
        all_ids     = [r["player_id"] for r in resolved if r["player_id"]]

        element_summaries = _summaries(all_ids)

        result = run_pipeline(
            scan_result       = scan_result,
            bootstrap         = bootstrap,
            element_summaries = element_summaries,
            params            = params,
            threshold         = _THRESHOLD,
        )
        result["_diag_log"] = _diag_log
        return result

    def log_message(self, *_):
        pass   # suppress Vercel access logs
