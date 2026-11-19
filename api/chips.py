"""
SquadCheck — Vercel Serverless Function
POST /api/chips   (JSON body; see fpl/engine/chips_adapter.py for the contract)

Chip advice for a squad the scanner flow already returned. All logic lives in fpl.engine.chips and
fpl.engine.chips_adapter so the local server shares it. It does not read screenshots and does not
change the scanner or transfer-plan endpoints.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fpl.engine.chips_adapter import handle as handle_chips
from fpl.engine.transfer_adapter import FplDataSource, MAX_BODY_BYTES

# Module-level source: caches persist across warm invocations.
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
            status, payload = handle_chips(self.rfile.read(length), _source)
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")   # same policy as /api/squad-check
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, *_):
        pass
