"""
SquadCheck — Vercel Serverless Function
POST /api/fpl-team   {"fpl_id": 123456}  ->  the team's squad in /api/squad-check's manual_squad shape.
All logic lives in fpl.engine.fpl_team.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fpl.engine.fpl_team import handle as handle_team
from fpl.engine.transfer_adapter import FplDataSource, MAX_BODY_BYTES

_source = FplDataSource()


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > MAX_BODY_BYTES:
            status, payload = 413, {"status": "BAD_REQUEST", "error": "Request body too large", "details": []}
        else:
            status, payload = handle_team(self.rfile.read(length), _source)
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, *_):
        pass
