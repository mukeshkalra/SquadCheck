"""_http_fetch retries FPL's transient 403/429/5xx (seen on cold serverless instances) and nothing else."""
import io
import json
import unittest
import urllib.error
from unittest import mock

from fpl.engine import transfer_adapter as ta


class _Resp:
    def __init__(self, body):
        self._b, self.headers = json.dumps(body).encode(), {}
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _err(code):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b""))


class TestRetry(unittest.TestCase):
    def run_with(self, outcomes):
        seen = []
        def fake(req, timeout=0):
            seen.append(req.get_header("User-agent"))
            o = outcomes[len(seen) - 1]
            if isinstance(o, Exception):
                raise o
            return _Resp(o)
        with mock.patch("urllib.request.urlopen", fake):
            return seen, ta._http_fetch("https://x", sleep=lambda s: None)

    def test_recovers_after_403s(self):
        seen, data = self.run_with([_err(403), _err(403), {"ok": 1}])
        self.assertEqual(data, {"ok": 1})
        self.assertEqual(len(seen), 3)
        self.assertIn("Chrome", seen[2])          # last attempt uses the browser-style agent

    def test_gives_up_after_three_attempts(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.run_with([_err(403)] * 3)

    def test_404_is_not_retried(self):
        seen = []
        def fake(req, timeout=0):
            seen.append(1); raise _err(404)
        with mock.patch("urllib.request.urlopen", fake), self.assertRaises(urllib.error.HTTPError):
            ta._http_fetch("https://x", sleep=lambda s: None)
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
