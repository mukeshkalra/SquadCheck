"""
SquadCheck — Vercel Serverless Function
POST /api/transfer-plan   (JSON body; see fpl/engine/transfer_adapter.py for the contract)

Input is the 15 player ids the scanner flow (/api/squad-check) already returned, plus bank,
free transfers and optional named transfers. It does not read screenshots and does not change the
scanner endpoint. All logic lives in fpl.engine.transfer_adapter so the local server shares it.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fpl.engine.transfer_adapter import FplDataSource, MAX_BODY_BYTES, handle as handle_transfer_plan

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
            status, payload = handle_transfer_plan(self.rfile.read(length), _source)
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
