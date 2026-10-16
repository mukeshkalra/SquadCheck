"""Player-history fetch in api/squad-check.py: one retry, a small cache, tolerates a few gaps, fails clearly past that."""
import importlib.util
import json
import socket
import time
import types
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load_api():
    spec = importlib.util.spec_from_file_location("squad_check_api_resilience", ROOT / "api" / "squad-check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.time = types.SimpleNamespace(time=time.time, sleep=lambda s: None)   # no real waiting; the shared time module is untouched
    return module


class Fetcher:
    """Stands in for api._fetch: scripted failures per player id, and a call log."""
    def __init__(self, fail=None):
        self.fail = fail or {}          # pid -> list of exceptions to raise on successive calls ("ok" after the list)
        self.calls = []

    def __call__(self, url, timeout=15):
        pid = int(url.rstrip("/").split("/")[-1])
        self.calls.append(pid)
        queue = self.fail.get(pid)
        if queue:
            raise queue.pop(0)
        return {"history": [], "fixtures": [], "pid": pid}


TIMEOUT = lambda: urllib.error.URLError(socket.timeout("timed out"))


class TestSummaries(unittest.TestCase):
    def api(self, fail=None):
        api = load_api()
        api._fetch = Fetcher(fail)
        return api

    def test_all_players_fetched_with_at_most_six_at_a_time(self):
        api = self.api()
        got, missing = api._summaries_with_gaps(list(range(1, 16)))
        self.assertEqual(sorted(got), list(range(1, 16)))
        self.assertEqual(missing, [])
        self.assertEqual(api._SUM_WORKERS, 6)

    def test_a_single_timeout_is_retried_and_the_player_is_still_returned(self):
        api = self.api({3: [TIMEOUT()]})
        got, missing = api._summaries_with_gaps([1, 2, 3])
        self.assertEqual(missing, [])
        self.assertEqual(api._fetch.calls.count(3), 2)

    def test_a_player_failing_twice_is_a_gap_not_a_crash(self):
        api = self.api({3: [TIMEOUT(), TIMEOUT()]})
        got, missing = api._summaries_with_gaps([1, 2, 3])
        self.assertEqual(missing, [3])
        self.assertEqual(sorted(got), [1, 2])

    def test_a_non_json_reply_is_a_gap_and_is_not_retried(self):
        api = self.api({2: [json.JSONDecodeError("x", "<html>", 0)]})
        got, missing = api._summaries_with_gaps([1, 2])
        self.assertEqual(missing, [2])
        self.assertEqual(api._fetch.calls.count(2), 1)

    def test_up_to_three_gaps_are_tolerated(self):
        fail = {i: [TIMEOUT(), TIMEOUT()] for i in (1, 2, 3)}
        api = self.api(fail)
        self.assertEqual(sorted(api._summaries(list(range(1, 16)))), list(range(4, 16)))

    def test_more_than_three_gaps_fail_with_a_message_the_user_can_act_on(self):
        fail = {i: [TIMEOUT(), TIMEOUT()] for i in (1, 2, 3, 4)}
        api = self.api(fail)
        with self.assertRaises(RuntimeError) as cm:
            api._summaries(list(range(1, 16)))
        self.assertIn("try again in a minute", str(cm.exception))

    def test_a_second_request_for_the_same_players_uses_the_cache(self):
        api = self.api()
        api._summaries_with_gaps([1, 2, 3])
        n = len(api._fetch.calls)
        api._summaries_with_gaps([1, 2, 3])
        self.assertEqual(len(api._fetch.calls), n)

    def test_an_expired_cache_entry_is_fetched_again(self):
        api = self.api()
        api._summaries_with_gaps([1])
        pid_ts, data = api._cache["summaries"][1]
        api._cache["summaries"][1] = (pid_ts - api._SUM_TTL - 1, data)
        api._summaries_with_gaps([1])
        self.assertEqual(api._fetch.calls.count(1), 2)


if __name__ == "__main__":
    unittest.main()
