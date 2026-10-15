"""
SquadCheck — Vercel Serverless Function
GET /api/players

The player directory for name search in the transfer planner: id, name, club, position, price and
photo code for every usable FPL player. Read-only, no parameters, no secrets. It reads the same cached
FPL bootstrap data as /api/transfer-plan; all logic lives in fpl.engine.transfer_adapter so the local
server shares it.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fpl.engine.transfer_adapter import FplDataSource, PLAYERS_CACHE_CONTROL, handle_players

# Module-level source: the bootstrap cache persists across warm invocations of this function.
_source = FplDataSource()


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        status, payload = handle_players(_source)
        self._send(status, payload)

    def do_POST(self):
        self._send(405, {"status": "METHOD_NOT_ALLOWED", "error": "Use GET", "details": []}, allow="GET, OPTIONS")

    do_PUT = do_DELETE = do_PATCH = do_POST

    def _send(self, status, payload, allow=None):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self._cors()
        if allow:
            self.send_header("Allow", allow)
        self.send_header("Cache-Control", PLAYERS_CACHE_CONTROL if status == 200 else "no-store")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")   # same policy as the other endpoints
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, *_):
        pass
