"""
Tests for fpl/engine/transfer_adapter.py and its two transports (api/transfer-plan.py,
fpl/server.py). Everything FPL-facing is mocked: no network, deterministic fixtures.

World: 20 clubs. Squad ids 1..15 (2 GKP / 5 DEF / 5 MID / 3 FWD, one club each, teams 1..15).
Candidates: DEF 101..120, MID 121..140, FWD 141..160 (team = (id-1) % 20 + 1), GKP 201..220 (one per club).
Prices are tenths of £m. Next gameweek is 7.
"""

import copy
import importlib.util
import json
import logging
import threading
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from unittest import mock

from fpl.engine import transfer_adapter as ta
from fpl.engine.transfer_adapter import ApiError, FplDataSource, handle, parse_request
from fpl.engine.transfer_planner import shortlist_candidates

ROOT = Path(__file__).resolve().parents[3]
logging.getLogger("squadcheck.transfer").setLevel(logging.CRITICAL)      # keep expected-failure logs out of test output


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


API = load("transfer_plan_api_under_test", "api/transfer-plan.py")
SERVER = load("fpl_server_for_transfer_tests", "fpl/server.py")

SQUAD = list(range(1, 16))
POS = {**{1: 1, 2: 1}, **{i: 2 for i in range(3, 8)}, **{i: 3 for i in range(8, 13)}, **{i: 4 for i in range(13, 16)}}


def element(pid, pos, team, price=55, quality=0.0, minutes=900, **kw):
    return {
        "id": pid, "web_name": kw.get("name", "P%d" % pid), "element_type": pos, "team": team,
        "now_cost": price, "status": "a", "chance_of_playing_next_round": None, "news": "",
        "minutes": minutes, "starts": minutes // 90, "bonus": 3,
        "expected_goals_per_90": 0.0 if pos == 1 else 0.10 + quality,
        "expected_assists_per_90": 0.0 if pos == 1 else 0.05 + quality / 2,
        "expected_goals_conceded_per_90": 1.0, "saves_per_90": 2.0 if pos == 1 else 0.0,
        "ep_next": str(round(2 + quality * 10, 1)), "form": str(round(2 + quality * 10, 1)),
        "points_per_game": str(round(2 + quality * 10, 1)),
    }


def make_bootstrap():
    els = []
    for pid in SQUAD:
        els.append(element(pid, POS[pid], pid, price=55, quality=0.02, name="S%d" % pid))
    for i in range(20):
        els.append(element(101 + i, 2, i + 1, price=50 + i % 5, quality=0.01 * (i % 10)))
        els.append(element(121 + i, 3, i + 1, price=50 + i % 5, quality=0.01 * (i % 10) + 0.05))
        els.append(element(141 + i, 4, i + 1, price=50 + i % 5, quality=0.01 * (i % 10) + 0.04))
        els.append(element(201 + i, 1, i + 1, price=45, quality=0.0))
    teams = [{"id": t, "name": "Club %d" % t, "short_name": "C%d" % t} for t in range(1, 21)]
    events = [{"id": 6, "finished": True}, {"id": 7, "is_next": True}, {"id": 8}, {"id": 9}, {"id": 10}]
    return {"elements": els, "teams": teams, "events": events}


def make_summary(team, events=(7, 8, 9)):
    return {"history": [{"minutes": 90, "starts": 1}] * 5,
            "fixtures": [{"event": e, "is_home": True, "team_h": team, "team_a": team % 20 + 1, "difficulty": 3}
                         for e in events]}


class FakeFpl:
    """url -> payload; records every call; can fail or block individual urls."""

    def __init__(self):
        self.bootstrap = make_bootstrap()
        self.fixtures = [{"id": 1, "team_h": 1, "team_a": 2, "finished": True, "finished_provisional": True}]
        self.summary = {e["id"]: make_summary(e["team"]) for e in self.bootstrap["elements"]}
        self.fail = set()
        self.block = {}
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, url):
        with self.lock:
            self.calls.append(url)
        if url in self.fail:
            raise OSError("simulated upstream failure")
        if url in self.block:
            self.block[url].wait(10)
        if url == ta._BS_URL:
            return self.bootstrap
        if url == ta._FX_URL:
            return self.fixtures
        pid = int(url.rstrip("/").split("/")[-1])
        return self.summary[pid]

    def summary_calls(self, pid=None):
        suffix = ta._SUM_URL.format(pid) if pid is not None else None
        return [c for c in self.calls if (c == suffix if suffix else "element-summary" in c)]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def sum_url(pid):
    return ta._SUM_URL.format(pid)


def body(**kw):
    req = {"squad_ids": SQUAD, "bank_m": 5.0, "free_transfers": 1}
    req.update(kw)
    return json.dumps(req).encode()


def run(fake=None, source=None, **kw):
    fake = fake or FakeFpl()
    source = source or FplDataSource(fetch=fake, clock=Clock())
    status, payload = handle(body(**kw), source)
    return status, payload, fake


def pairs(plan):
    return [(t["out"]["id"], t["in"]["id"]) for t in plan["transfers"]]


# ─────────────────────────────────────────────────────────────────────────────
# REQUEST VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

class TestRequestValidation(unittest.TestCase):
    def bad(self, raw, needle):
        with self.assertRaises(ApiError) as cm:
            parse_request(raw)
        self.assertEqual(cm.exception.http_status, 400)
        self.assertIn(needle, " | ".join(cm.exception.details + [cm.exception.message]))

    def base(self, **kw):
        d = {"squad_ids": SQUAD, "bank_m": 0.5, "free_transfers": 1}
        d.update(kw)
        return d

    def test_valid_minimal_request(self):
        r = parse_request(self.base())
        self.assertEqual((r["bank"], r["free_transfers"], r["intent"], r["max_hits"]), (5, 1, None, 0))

    def test_squad_problems(self):
        self.bad(self.base(squad_ids=SQUAD[:14]), "exactly 15")
        self.bad(self.base(squad_ids=SQUAD[:14] + [1]), "duplicate")
        self.bad(self.base(squad_ids=["a"] * 15), "list of integer")
        self.bad(self.base(squad_ids=SQUAD[:14] + [True]), "list of integer")
        self.bad(self.base(squad_ids="1,2,3"), "list of integer")

    def test_required_fields(self):
        for key in ("squad_ids", "bank_m", "free_transfers"):
            raw = self.base()
            del raw[key]
            self.bad(raw, "%s is required" % key)

    def test_bank_values(self):
        for v in (-0.1, 100.5, 0.55, "1.0", True, None, float("nan"), float("inf")):
            with self.subTest(v=v):
                self.bad(self.base(bank_m=v), "bank_m")
        self.assertEqual(parse_request(self.base(bank_m=0))["bank"], 0)
        self.assertEqual(parse_request(self.base(bank_m=2.3))["bank"], 23)
        self.assertEqual(parse_request(self.base(bank_m=0.1 + 0.2))["bank"], 3)

    def test_free_transfer_values(self):
        for v in (-1, 6, 1.5, "1", True, None):
            with self.subTest(v=v):
                self.bad(self.base(free_transfers=v), "free_transfers")
        for v in range(0, 6):
            self.assertEqual(parse_request(self.base(free_transfers=v))["free_transfers"], v)

    def test_unknown_field_and_non_object(self):
        self.bad(self.base(chip="wildcard"), "unknown field")
        with self.assertRaises(ApiError):
            parse_request([1, 2])

    def test_intent_rules(self):
        self.bad(self.base(moves=[[8, 121]], out_ids=[9]), "not both")
        self.bad(self.base(out_ids=[8], in_ids=[121]), "do not say who replaces whom")
        self.bad(self.base(moves=[[8]]), "[out_id, in_id]")
        self.bad(self.base(moves=[[99, 121]]), "not in squad_ids")
        self.bad(self.base(moves=[[8, 9]]), "already in squad_ids")
        self.bad(self.base(moves=[[8, 121], [9, 121]]), "distinct")
        self.bad(self.base(max_hits=-1), "max_hits")
        self.assertEqual(parse_request(self.base(moves=[[8, 121]]))["intent"], "moves")
        self.assertEqual(parse_request(self.base(out_ids=[8]))["intent"], "out_only")
        self.assertEqual(parse_request(self.base(in_ids=[121]))["intent"], "in_only")

    def test_all_problems_reported_together(self):
        with self.assertRaises(ApiError) as cm:
            parse_request({"squad_ids": [1], "bank_m": -1, "free_transfers": 9, "extra": 1})
        self.assertGreaterEqual(len(cm.exception.details), 4)

    def test_http_level_errors(self):
        src = FplDataSource(fetch=FakeFpl(), clock=Clock())
        self.assertEqual(handle(b"not json", src)[0], 400)
        self.assertEqual(handle(b"[1]", src)[0], 400)
        self.assertEqual(handle(b"\xff\xfe", src)[0], 400)
        self.assertEqual(handle(b"x" * (ta.MAX_BODY_BYTES + 1), src)[0], 413)
        status, payload = handle(body(bank_m=-1), src)
        self.assertEqual((status, payload["status"]), (400, "BAD_REQUEST"))
        self.assertTrue(payload["details"])


# ─────────────────────────────────────────────────────────────────────────────
# SQUAD IDS AGAINST FPL DATA
# ─────────────────────────────────────────────────────────────────────────────

class TestSquadResolution(unittest.TestCase):
    def test_valid_squad(self):
        status, p, _ = run()
        self.assertEqual(status, 200)
        self.assertEqual(p["status"], "OK")
        self.assertEqual(p["gameweek"], 7)
        self.assertEqual([s["id"] for s in p["squad"]], SQUAD)
        self.assertEqual(p["squad"][0]["position"], 1)
        self.assertEqual(p["request"]["bank"], 50)

    def test_unknown_player_id(self):
        status, p, _ = run(squad_ids=SQUAD[:14] + [99999])
        self.assertEqual((status, p["status"]), (400, "BAD_REQUEST"))
        self.assertIn("99999", p["error"])

    def test_unknown_named_player(self):
        status, p, _ = run(in_ids=[88888])
        self.assertEqual(status, 400)
        self.assertIn("88888", p["error"])

    def test_wrong_composition_is_a_client_error_not_a_crash(self):
        squad = SQUAD[:2] + [201] + SQUAD[3:]          # three GKP, four DEF... composition broken
        status, p, _ = run(squad_ids=squad)
        self.assertEqual((status, p["status"]), (422, "INVALID_PLAN_INPUT"))
        self.assertIn("composition", p["error"])

    def test_club_limit_breach_is_422(self):
        fake = FakeFpl()
        for e in fake.bootstrap["elements"]:
            if e["id"] in (3, 4, 5, 6):
                e["team"] = 20
        status, p, _ = run(fake)
        self.assertEqual((status, p["status"]), (422, "INVALID_PLAN_INPUT"))

    def test_squad_player_missing_required_fields_is_upstream_error(self):
        fake = FakeFpl()
        next(e for e in fake.bootstrap["elements"] if e["id"] == 8).pop("now_cost")
        status, p, _ = run(fake)
        self.assertEqual((status, p["status"]), (502, "UPSTREAM_ERROR"))
        self.assertIn("8", p["error"])

    def test_unusable_non_squad_candidate_is_skipped_not_fatal(self):
        fake = FakeFpl()
        next(e for e in fake.bootstrap["elements"] if e["id"] == 125).pop("now_cost")
        status, p, _ = run(fake)
        self.assertEqual(status, 200)


# ─────────────────────────────────────────────────────────────────────────────
# UPSTREAM FAILURES
# ─────────────────────────────────────────────────────────────────────────────

class TestUpstreamFailures(unittest.TestCase):
    def test_bootstrap_fetch_failure(self):
        fake = FakeFpl()
        fake.fail.add(ta._BS_URL)
        status, p, _ = run(fake)
        self.assertEqual((status, p["status"]), (502, "UPSTREAM_ERROR"))

    def test_malformed_bootstrap(self):
        for bad in ({}, {"elements": [], "events": []}, {"elements": [{"id": 1}], "events": []}, []):
            with self.subTest(bad=bad):
                fake = FakeFpl()
                fake.bootstrap = bad
                status, p, _ = run(fake)
                self.assertEqual((status, p["status"]), (502, "UPSTREAM_ERROR"))

    def test_no_upcoming_gameweek(self):
        fake = FakeFpl()
        fake.bootstrap["events"] = [{"id": 38, "finished": True}]
        status, p, _ = run(fake)
        self.assertEqual((status, p["status"]), (409, "NO_UPCOMING_GAMEWEEK"))

    def test_every_summary_failing_is_502_not_zero_projections(self):
        fake = FakeFpl()
        fake.fail = {sum_url(e["id"]) for e in fake.bootstrap["elements"]}
        status, p, _ = run(fake)
        self.assertEqual((status, p["status"]), (502, "UPSTREAM_ERROR"))
        self.assertIn("projection data", p["error"])

    def test_fixtures_failure_degrades_gracefully_and_is_reported(self):
        fake = FakeFpl()
        fake.fail.add(ta._FX_URL)
        status, p, _ = run(fake)
        self.assertEqual((status, p["status"]), (200, "DEGRADED"))
        self.assertFalse(p["data"]["fixtures_available"])
        status, p, _ = run()
        self.assertEqual(p["status"], "OK")
        self.assertTrue(p["data"]["fixtures_available"])

    def test_upstream_failures_are_logged_server_side_with_detail(self):
        fake = FakeFpl()
        fake.fail.add(ta._BS_URL)
        with self.assertLogs("squadcheck.transfer", level="WARNING") as cm:
            status, p, _ = run(fake)
        self.assertIn("simulated upstream failure", " ".join(cm.output))         # detail is in the log...
        self.assertNotIn("simulated upstream failure", json.dumps(p))          # ...and not in the response

    def test_unexpected_exception_is_a_500_with_status(self):
        with mock.patch.object(ta, "plan_transfers", side_effect=RuntimeError("boom")):
            status, p, _ = run()
        self.assertEqual((status, p["status"]), (500, "ERROR"))


# ─────────────────────────────────────────────────────────────────────────────
# MISSING SUMMARY vs GENUINE BLANK GAMEWEEK
# ─────────────────────────────────────────────────────────────────────────────

class TestMissingVersusBlank(unittest.TestCase):
    def test_failed_summary_is_unavailable_and_never_evaluated(self):
        fake = FakeFpl()
        fake.fail.add(sum_url(125))
        status, p, _ = run(fake, moves=[[8, 125]])
        self.assertEqual(status, 200)
        q = p["plan"]["requested"]
        self.assertEqual(q["status"], "INCOMPLETE_DATA")
        self.assertIsNone(q["plan"])
        self.assertIn("not a blank gameweek", q["reason"])
        self.assertIn(125, p["data"]["summaries"]["failed"])
        self.assertIn(125, p["data"]["summaries"]["unavailable_ids"])
        self.assertEqual(p["status"], "DEGRADED")
        self.assertEqual(p["plan"]["data_quality"]["candidates_projection_missing"], [125])

    def test_genuine_blank_gameweek_is_zero_and_not_flagged_as_missing(self):
        fake = FakeFpl()
        fake.summary[125] = make_summary(5, events=(7, 9))          # no fixture in GW8
        status, p, _ = run(fake, moves=[[8, 125]])
        inn = p["plan"]["requested"]["plan"]["transfers"][0]["in"]
        self.assertEqual(inn["xpts"][1], 0.0)
        self.assertNotIn("projection_unavailable_gws", inn)
        self.assertGreater(inn["xpts"][0], 0.0)
        self.assertEqual(p["status"], "OK")
        self.assertFalse(p["data"]["degraded"])

    def test_malformed_summary_is_unavailable(self):
        for bad in (None, {}, {"history": "x", "fixtures": []}, {"history": [], "fixtures": None}, [1]):
            with self.subTest(bad=bad):
                fake = FakeFpl()
                fake.summary[125] = bad
                status, p, _ = run(fake, moves=[[8, 125]])
                self.assertEqual(status, 200)
                self.assertIn(125, p["data"]["summaries"]["malformed"])
                self.assertEqual(p["plan"]["requested"]["status"], "INCOMPLETE_DATA")
                self.assertEqual(p["status"], "DEGRADED")

    def test_squad_player_summary_failure_is_flagged_and_that_player_is_not_sold(self):
        fake = FakeFpl()
        fake.fail.add(sum_url(9))
        status, p, _ = run(fake, free_transfers=3)
        plan = p["plan"]
        self.assertEqual((status, p["status"]), (200, "DEGRADED"))
        self.assertTrue(any("S9" in w and "not a blank gameweek" in w for w in plan["warnings"]))
        self.assertEqual(plan["data_quality"]["squad_projection_missing"], [9])
        self.assertFalse(plan["hold"]["data_complete"])
        self.assertEqual(plan["hold"]["confidence"], "LOW")
        for plan_ in [plan["recommendation"]["plan"]] + plan["alternatives"]:
            self.assertNotIn(9, [t["out"]["id"] for t in plan_["transfers"]])
            self.assertFalse(plan_["data_complete"])
        self.assertTrue(p["data"]["retry_suggested"])

    def test_squad_player_failure_blocks_requested_sale_of_that_player(self):
        fake = FakeFpl()
        fake.fail.add(sum_url(9))
        status, p, _ = run(fake, out_ids=[9])
        self.assertEqual(p["plan"]["requested"]["status"], "INCOMPLETE_DATA")

    def test_summary_timeout_is_reported_not_zeroed(self):
        fake = FakeFpl()
        gate = threading.Event()
        fake.block[sum_url(125)] = gate
        try:
            source = FplDataSource(fetch=fake, clock=Clock())
            req = parse_request({"squad_ids": SQUAD, "bank_m": 5.0, "free_transfers": 1, "moves": [[8, 125]]})
            p = ta.build_response(req, source, summary_budget_s=0.3)
        finally:
            gate.set()
        self.assertIn(125, p["data"]["summaries"]["timed_out"])
        self.assertEqual(p["plan"]["requested"]["status"], "INCOMPLETE_DATA")
        self.assertEqual(p["status"], "DEGRADED")
        self.assertTrue(p["data"]["retry_suggested"])

    def test_late_result_is_cached_for_the_next_request(self):
        fake = FakeFpl()
        gate = threading.Event()
        fake.block[sum_url(125)] = gate
        source = FplDataSource(fetch=fake, clock=Clock())
        req = parse_request({"squad_ids": SQUAD, "bank_m": 5.0, "free_transfers": 1, "moves": [[8, 125]]})
        try:
            p1 = ta.build_response(req, source, summary_budget_s=0.3)
            self.assertEqual(p1["status"], "DEGRADED")
        finally:
            gate.set()
        for _ in range(100):                                     # let the straggler finish
            if 125 in source._summaries:
                break
            threading.Event().wait(0.02)
        p2 = ta.build_response(req, source, summary_budget_s=5)
        self.assertEqual(p2["status"], "OK")
        self.assertNotEqual(p2["plan"]["requested"]["status"], "INCOMPLETE_DATA")
        self.assertEqual(len(fake.summary_calls(125)), 1)


class TestUpstreamDeadlines(unittest.TestCase):
    """A slow upstream must not hold a request past its deadline, and late results must not be wasted."""

    def _wait_for(self, predicate):
        for _ in range(200):
            if predicate():
                return
            threading.Event().wait(0.02)
        self.fail("condition not reached")

    def test_slow_bootstrap_fails_fast_with_a_generic_error_then_late_result_is_reused(self):
        fake, gate = FakeFpl(), threading.Event()
        fake.block[ta._BS_URL] = gate
        source = FplDataSource(fetch=fake, clock=Clock(), bootstrap_deadline=0.2)
        import time as _t
        t0 = _t.perf_counter()
        status, p = handle(body(), source)
        self.assertLess(_t.perf_counter() - t0, 2.0)
        self.assertEqual((status, p["status"]), (502, "UPSTREAM_ERROR"))
        self.assertEqual(p["error"], "FPL bootstrap data did not arrive in time")
        gate.set()
        self._wait_for(lambda: source._bootstrap is not None)
        status, p = handle(body(), source)
        self.assertEqual((status, p["status"]), (200, "OK"))
        self.assertEqual(len([c for c in fake.calls if c == ta._BS_URL]), 1)

    def test_concurrent_requests_during_a_slow_bootstrap_share_one_fetch(self):
        fake, gate = FakeFpl(), threading.Event()
        fake.block[ta._BS_URL] = gate
        source = FplDataSource(fetch=fake, clock=Clock(), bootstrap_deadline=0.2)
        results = []
        threads = [threading.Thread(target=lambda: results.append(handle(body(), source))) for _ in range(5)]
        [t.start() for t in threads]
        [t.join(10) for t in threads]
        gate.set()
        self.assertEqual([s for s, _ in results], [502] * 5)
        self.assertEqual(len([c for c in fake.calls if c == ta._BS_URL]), 1)

    def test_slow_fixtures_degrade_instead_of_blocking_and_recover_when_they_arrive(self):
        fake, gate = FakeFpl(), threading.Event()
        fake.block[ta._FX_URL] = gate
        source = FplDataSource(fetch=fake, clock=Clock(), fixtures_deadline=0.2)
        status, p = handle(body(), source)
        self.assertEqual((status, p["status"]), (200, "DEGRADED"))
        self.assertFalse(p["data"]["fixtures_available"])
        gate.set()
        self._wait_for(lambda: source._fixtures is not None)
        status, p = handle(body(), source)
        self.assertEqual((status, p["status"]), (200, "OK"))
        self.assertTrue(p["data"]["fixtures_available"])
        self.assertEqual(len([c for c in fake.calls if c == ta._FX_URL]), 1)

    def test_worst_case_cold_request_is_bounded_below_the_function_limit(self):
        # bootstrap + fixtures + the minimum summary allowance + a generous planning allowance
        worst = ta.BOOTSTRAP_DEADLINE_S + ta.FIXTURES_DEADLINE_S + ta.MIN_SUMMARY_BUDGET_S + 5
        self.assertLess(worst, 60)                              # api/transfer-plan.py maxDuration in vercel.json
        cfg = json.loads((ROOT / "vercel.json").read_text())
        self.assertEqual(cfg["functions"]["api/transfer-plan.py"]["maxDuration"], 60)


class TestSummaryRetry(unittest.TestCase):
    def test_one_retry_recovers_from_a_transient_network_failure(self):
        fake = FakeFpl()
        n = {"c": 0}
        orig = fake.__call__

        def flaky(url):
            if "element-summary/5/" in url:
                n["c"] += 1
                if n["c"] == 1:
                    raise OSError("connect timed out")
            return orig(url)
        source = FplDataSource(fetch=flaky, clock=Clock())
        out, stats = source.summaries([5])
        self.assertEqual((list(out), stats["failed"], n["c"]), ([5], [], 2))

    def test_persistent_failure_stops_after_the_attempt_limit(self):
        fake = FakeFpl()
        fake.fail.add(sum_url(5))
        source = FplDataSource(fetch=fake, clock=Clock())
        out, stats = source.summaries([5])
        self.assertEqual((out, stats["failed"]), ({}, [5]))
        self.assertEqual(len(fake.summary_calls(5)), ta.SUMMARY_ATTEMPTS)

    def test_bad_json_is_not_retried(self):
        calls = []

        def bad(url):
            calls.append(url)
            raise ValueError("bad json")
        out, stats = FplDataSource(fetch=bad, clock=Clock()).summaries([5])
        self.assertEqual((stats["failed"], len(calls)), ([5], 1))

    def test_worst_case_upstream_calls_per_request_are_bounded(self):
        self.assertEqual(ta.MAX_SUMMARIES_PER_REQUEST * ta.SUMMARY_ATTEMPTS + 2, 186)

    def test_default_fetcher_uses_the_short_summary_timeout(self):
        seen = []
        with mock.patch.object(ta, "_http_fetch", lambda url, timeout=15: seen.append(timeout) or {"history": [], "fixtures": []}):
            src = FplDataSource()
            src.summaries([5])
        self.assertEqual(seen, [ta.SUMMARY_ATTEMPT_TIMEOUT_S])


class TestHttpFetch(unittest.TestCase):
    """_http_fetch asks for gzip and handles both encoded and plain responses (no network)."""

    class Resp:
        def __init__(self, raw, encoding=None):
            self._raw, self.headers = raw, ({"Content-Encoding": encoding} if encoding else {})

        def read(self):
            return self._raw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _call(self, resp):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["accept"] = req.get_header("Accept-encoding")
            seen["timeout"] = timeout
            return resp
        with mock.patch.object(ta.urllib.request, "urlopen", fake_urlopen):
            return ta._http_fetch("https://example.invalid/x"), seen

    def test_gzip_response_is_decoded_and_gzip_is_requested(self):
        import gzip as _gzip
        payload = {"elements": [1, 2, 3]}
        data, seen = self._call(self.Resp(_gzip.compress(json.dumps(payload).encode()), "gzip"))
        self.assertEqual(data, payload)
        self.assertEqual(seen["accept"], "gzip")
        self.assertEqual(seen["timeout"], 15)

    def test_plain_response_still_works(self):
        data, _ = self._call(self.Resp(b'{"a": 1}'))
        self.assertEqual(data, {"a": 1})

    def test_corrupt_gzip_raises_rather_than_returning_garbage(self):
        with self.assertRaises(Exception):
            self._call(self.Resp(b"not gzip", "gzip"))


class TestCompletenessContract(unittest.TestCase):
    def test_complete_result(self):
        status, p, _ = run()
        self.assertEqual((status, p["status"], p["data"]["completeness"]), (200, "OK", "complete"))
        self.assertEqual(p["data"]["degraded_reasons"], [])
        self.assertFalse(p["data"]["retry_suggested"])
        self.assertTrue(p["plan"]["data_quality"]["complete"])

    def test_degraded_result_via_missing_fixtures_names_the_reason(self):
        fake = FakeFpl()
        fake.fail.add(ta._FX_URL)
        status, p, _ = run(fake)
        self.assertEqual((status, p["status"], p["data"]["completeness"]), (200, "DEGRADED", "degraded"))
        self.assertTrue(any("fixture list unavailable" in r for r in p["data"]["degraded_reasons"]))
        self.assertFalse(p["data"]["retry_suggested"])

    def test_failed_results_are_non_200_with_a_distinct_status(self):
        fake = FakeFpl()
        fake.fail.add(ta._BS_URL)
        status, p, _ = run(fake)
        self.assertNotEqual(status, 200)
        self.assertNotIn(p["status"], ("OK", "DEGRADED"))

    def test_error_bodies_have_one_shape_and_expose_no_internals(self):
        cases = []
        fake = FakeFpl(); fake.fail.add(ta._BS_URL); cases.append(run(fake)[:2])
        fake = FakeFpl(); fake.fail = {sum_url(e["id"]) for e in fake.bootstrap["elements"]}; cases.append(run(fake)[:2])
        with mock.patch.object(ta, "plan_transfers", side_effect=RuntimeError("secret /etc/path boom")):
            cases.append(run()[:2])
        cases.append(run(bank_m=-1)[:2])
        cases.append(run(squad_ids=SQUAD[:14] + [99999])[:2])
        fake = FakeFpl(); fake.bootstrap["events"] = [{"id": 38, "finished": True}]; cases.append(run(fake)[:2])
        for status, p in cases:
            self.assertEqual(set(p), {"status", "error", "details"})
            blob = json.dumps(p)
            for leak in ("Traceback", "boom", "/etc", "simulated upstream failure", "OSError", "urllib",
                         "fantasy.premierleague.com"):
                self.assertNotIn(leak, blob)
        self.assertEqual(sorted({s for s, _ in cases}), [400, 409, 500, 502])

    def test_literal_move_infeasibility_and_coverage_survive_json(self):
        status, p, _ = run(free_transfers=2, moves=[[12, 101]])         # MID -> DEF
        wire = json.loads(json.dumps(p))
        q = wire["plan"]["requested"]
        self.assertEqual((q["status"], q["plan"], q["pairing_preserved"]), ("INFEASIBLE", None, True))
        self.assertIn("positions differ", q["reason"])
        self.assertEqual(wire["plan"]["inputs"]["moves"], [[12, 101]])
        s = wire["plan"]["search"]
        self.assertEqual(s["optimality"], "not_guaranteed")
        self.assertIn("coverage", s)
        self.assertFalse(wire["plan"]["basis"]["guaranteed"])
        status, p, _ = run(free_transfers=2, moves=[[12, 130]])
        wire = json.loads(json.dumps(p))
        self.assertEqual([[t["out"]["id"], t["in"]["id"]] for t in wire["plan"]["requested"]["plan"]["transfers"]],
                         [[12, 130]])


# ─────────────────────────────────────────────────────────────────────────────
# REQUEST AMPLIFICATION AND CONCURRENCY
# ─────────────────────────────────────────────────────────────────────────────

class TestAmplification(unittest.TestCase):
    def test_documented_bound_matches_the_constant(self):
        self.assertEqual(ta.MAX_SUMMARIES_PER_REQUEST, 92)

    def test_worst_case_shortlist_stays_within_the_bound(self):
        # three rankings that share no players, plus 5 named incoming from the same pool
        els = []
        n = 0
        for pos in (1, 2, 3, 4):
            for i in range(60):
                n += 1
                e = element(1000 + n, pos, 1 + i % 20, quality=0.0)
                e["ep_next"], e["form"], e["points_per_game"] = str(i), str(59 - i), str((i * 7) % 60)
                els.append(e)
        bs = {"elements": make_bootstrap()["elements"][:15] + els, "events": make_bootstrap()["events"]}
        named = [e["id"] for e in els[:5]]
        ids = shortlist_candidates(bs, SQUAD, forced_ids=named, per_position=ta.CANDIDATES_PER_POSITION)
        self.assertLessEqual(len(ids), ta.MAX_SUMMARIES_PER_REQUEST)
        self.assertGreater(len(ids), 70)                         # the bound is nearly reached by this input

    def test_request_triggers_at_most_the_bound_in_upstream_calls(self):
        status, p, fake = run(in_ids=[130])
        self.assertLessEqual(len(fake.summary_calls()), ta.MAX_SUMMARIES_PER_REQUEST)
        self.assertEqual(len(fake.calls), len(fake.summary_calls()) + 2)       # + bootstrap + fixtures

    def test_concurrent_cold_requests_share_one_fetch_per_url(self):
        fake = FakeFpl()
        orig = fake.__call__
        calls_lock = threading.Lock()
        counts = {}

        def counting_fetch(url):
            with calls_lock:
                counts[url] = counts.get(url, 0) + 1
            threading.Event().wait(0.05)                 # make overlap likely
            return orig(url)
        source = FplDataSource(fetch=counting_fetch, clock=Clock())
        results = []

        def one():
            results.append(handle(body(free_transfers=1), source))
        threads = [threading.Thread(target=one) for _ in range(6)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        self.assertEqual(len(results), 6)
        self.assertTrue(all(s == 200 for s, _ in results))
        self.assertEqual(counts[ta._BS_URL], 1)
        self.assertEqual(counts[ta._FX_URL], 1)
        self.assertTrue(all(c == 1 for u, c in counts.items() if "element-summary" in u), counts)
        self.assertLessEqual(sum(counts.values()), ta.MAX_SUMMARIES_PER_REQUEST + 2)

    def test_upstream_concurrency_is_bounded_per_instance(self):
        fake = FakeFpl()
        active, peak, lock = [0], [0], threading.Lock()
        orig = fake.__call__

        def fetch(url):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            threading.Event().wait(0.02)
            try:
                return orig(url)
            finally:
                with lock:
                    active[0] -= 1
        source = FplDataSource(fetch=fetch, clock=Clock(), max_workers=4)
        source.summaries(list(range(101, 141)), budget_s=30)
        self.assertLessEqual(peak[0], 4)
        self.assertGreater(peak[0], 1)

    def test_request_budget_leaves_the_summaries_what_remains(self):
        fake = FakeFpl()
        seen = {}
        source = FplDataSource(fetch=fake, clock=Clock())
        real = source.summaries
        source.summaries = lambda ids, budget_s=None: (seen.setdefault("b", budget_s), real(ids, budget_s))[1]
        req = parse_request({"squad_ids": SQUAD, "bank_m": 5.0, "free_transfers": 1})
        ta.build_response(req, source)
        self.assertLessEqual(seen["b"], ta.SUMMARY_BUDGET_S)
        self.assertGreaterEqual(seen["b"], ta.MIN_SUMMARY_BUDGET_S)
        self.assertGreater(ta.REQUEST_BUDGET_S, ta.SUMMARY_BUDGET_S)


# ─────────────────────────────────────────────────────────────────────────────
# INTENT SCENARIOS THROUGH THE API
# ─────────────────────────────────────────────────────────────────────────────

class TestIntentScenarios(unittest.TestCase):
    def test_hold_only_request(self):
        status, p, _ = run(free_transfers=0)
        plan = p["plan"]
        self.assertEqual(plan["recommendation"]["action"], "HOLD")
        self.assertEqual(plan["alternatives"], [])
        self.assertEqual(plan["hold"]["free_transfers_remaining"], 0)
        self.assertIsNone(plan["requested"])
        self.assertEqual(p["request"]["intent"], None)

    def test_no_named_players_discovers_plans(self):
        status, p, _ = run(free_transfers=2)
        plan = p["plan"]
        self.assertIn(plan["recommendation"]["action"], ("TRANSFER", "HOLD"))
        self.assertIsNone(plan["requested"])
        for key in ("hold", "recommendation", "alternatives", "search", "basis", "inputs"):
            self.assertIn(key, plan)

    def test_known_out_unknown_in(self):
        status, p, _ = run(free_transfers=2, out_ids=[12])
        q = p["plan"]["requested"]
        self.assertEqual(q["intent"], "out_only")
        if q["plan"]:
            self.assertEqual(q["plan"]["n_transfers"], 1)
            self.assertEqual(q["plan"]["transfers"][0]["out"]["id"], 12)

    def test_unknown_out_known_in(self):
        status, p, _ = run(free_transfers=2, in_ids=[130])
        q = p["plan"]["requested"]
        self.assertEqual(q["intent"], "in_only")
        self.assertEqual(q["plan"]["n_transfers"], 1)
        self.assertEqual(q["plan"]["transfers"][0]["in"]["id"], 130)

    def test_known_out_and_in_stay_literal(self):
        status, p, _ = run(free_transfers=2, moves=[[12, 130]])
        q = p["plan"]["requested"]
        self.assertEqual(q["intent"], "moves")
        self.assertEqual(pairs(q["plan"]), [(12, 130)])
        self.assertEqual(q["plan"]["n_transfers"], 1)
        self.assertTrue(q["pairing_preserved"])
        self.assertEqual(p["plan"]["inputs"]["moves"], [[12, 130]] if isinstance(p["plan"]["inputs"]["moves"][0], list)
                         else [(12, 130)])

    def test_literal_move_gets_no_silently_added_transfers(self):
        status, p, _ = run(free_transfers=3, moves=[[12, 130]])
        q = p["plan"]["requested"]
        self.assertEqual(q["plan"]["n_transfers"], 1)
        ext = q["with_additional_transfers"]
        if ext:                                   # additions only ever appear as a separate, labelled plan
            self.assertGreater(ext["n_transfers"], 1)
            self.assertIn("did not request", ext["label"])

    def test_infeasible_move_is_a_structured_200(self):
        status, p, _ = run(moves=[[12, 101]])                   # MID -> DEF
        q = p["plan"]["requested"]
        self.assertEqual(status, 200)
        self.assertEqual(q["status"], "INFEASIBLE")
        self.assertIsNone(q["plan"])
        self.assertIn("positions differ", q["reason"])

    def test_unaffordable_move_reports_the_shortfall(self):
        fake = FakeFpl()
        next(e for e in fake.bootstrap["elements"] if e["id"] == 130)["now_cost"] = 150
        status, p, _ = run(fake, bank_m=0.0, moves=[[12, 130]])
        q = p["plan"]["requested"]
        self.assertEqual(q["status"], "INFEASIBLE")
        self.assertIn("short by", q["reason"])

    def test_moves_exceeding_free_transfers_are_infeasible_without_hits(self):
        status, p, _ = run(free_transfers=1, moves=[[12, 130], [11, 131]])
        q = p["plan"]["requested"]
        self.assertEqual(q["status"], "INFEASIBLE")
        self.assertIn("hits are disabled", q["reason"])
        status, p, _ = run(free_transfers=1, max_hits=1, moves=[[12, 130], [11, 131]])
        self.assertEqual(p["plan"]["requested"]["plan"]["hits"], 1)


# ─────────────────────────────────────────────────────────────────────────────
# NAMED PLAYERS, COVERAGE, LABELLING
# ─────────────────────────────────────────────────────────────────────────────

class TestCandidatesAndCoverage(unittest.TestCase):
    def test_named_target_outside_the_ordinary_shortlist_is_retained(self):
        fake = FakeFpl()
        bs = fake.bootstrap
        ordinary = set(shortlist_candidates(bs, SQUAD, per_position=ta.CANDIDATES_PER_POSITION))
        outsider = next(e["id"] for e in bs["elements"]
                        if e["element_type"] == 3 and e["id"] not in ordinary and e["id"] > 100)
        status, p, _ = run(fake, in_ids=[outsider], bank_m=9.0)
        self.assertEqual(status, 200)
        self.assertIn(outsider, p["data"]["named_players_retained"])
        self.assertEqual(p["plan"]["requested"]["plan"]["transfers"][0]["in"]["id"], outsider)
        self.assertIn(sum_url(outsider), fake.calls)

    def test_named_target_with_no_minutes_is_still_retained(self):
        fake = FakeFpl()
        el = next(e for e in fake.bootstrap["elements"] if e["id"] == 126)
        el["minutes"] = 0
        status, p, _ = run(fake, moves=[[12, 126]], bank_m=9.0)
        self.assertEqual(pairs(p["plan"]["requested"]["plan"]), [(12, 126)])

    def test_candidate_count_is_bounded(self):
        status, p, fake = run()
        self.assertLessEqual(p["data"]["candidates_considered"], 15 + 4 * ta.CANDIDATES_PER_POSITION + 1)
        self.assertEqual(len(fake.summary_calls()), p["data"]["summaries"]["fetched"])

    def test_search_coverage_and_non_guaranteed_optimality_survive(self):
        status, p, _ = run(free_transfers=2)
        s = p["plan"]["search"]
        self.assertNotIn("exact", s)
        self.assertEqual(s["scope"], "supplied_players")
        self.assertIn(s["optimality"], ("not_guaranteed", "optimal_over_supplied_players"))
        self.assertIn("coverage", s)
        self.assertIn("candidates_supplied", s["coverage"])
        self.assertIn("shortlist", s["note"])
        if s["pruned"]:
            self.assertEqual(s["optimality"], "not_guaranteed")

    def test_pruned_search_never_claims_optimality(self):
        status, p, _ = run(free_transfers=2)
        s = p["plan"]["search"]
        self.assertTrue(s["pruned"])                           # 12 per position of ~20 supplied, plus out-pool
        self.assertEqual(s["optimality"], "not_guaranteed")

    def test_projection_and_outlook_labelling_survive(self):
        status, p, _ = run(free_transfers=2)
        plan = p["plan"]
        self.assertFalse(plan["basis"]["guaranteed"])
        self.assertEqual(plan["hold"]["outlook"]["status"], "provisional_unvalidated")
        rec = plan["recommendation"]
        self.assertIn("projected_gain", rec)
        if rec["action"] == "TRANSFER":
            self.assertIn("not a guarantee", rec["reason"])
            self.assertEqual(rec["plan"]["outlook"]["status"], "provisional_unvalidated")
        for a in plan["alternatives"]:
            self.assertTrue(a["projected"])
            self.assertIn("free_transfers_remaining", a)
            self.assertIn("bank_after", a)
            self.assertIn("risks", a)
            self.assertIn("confidence", a)

    def test_response_is_json_serialisable_and_has_timing(self):
        status, p, _ = run(free_transfers=2, moves=[[12, 130]])
        json.dumps(p)
        t = p["timing_ms"]
        for key in ("bootstrap_and_params", "shortlist", "summary_fetch", "projection", "planning", "total"):
            self.assertIn(key, t)
        self.assertGreaterEqual(t["total"], t["planning"])


# ─────────────────────────────────────────────────────────────────────────────
# CACHE AND DEDUPLICATION
# ─────────────────────────────────────────────────────────────────────────────

class TestSummaryCache(unittest.TestCase):
    def test_each_summary_fetched_once_per_request_even_with_duplicate_ids(self):
        fake = FakeFpl()
        source = FplDataSource(fetch=fake, clock=Clock())
        out, stats = source.summaries([5, 5, 5, 130, 130])
        self.assertEqual(sorted(out), [5, 130])
        self.assertEqual(len(fake.summary_calls(5)), 1)
        self.assertEqual(len(fake.summary_calls(130)), 1)
        self.assertEqual(stats["requested"], 2)

    def test_second_request_served_from_cache(self):
        fake = FakeFpl()
        source = FplDataSource(fetch=fake, clock=Clock())
        _, p1, _ = run(fake, source=source, moves=[[12, 130]])
        n1 = len(fake.summary_calls())
        status, p2, _ = run(fake, source=source, moves=[[12, 130]])
        self.assertEqual(len(fake.summary_calls()), n1)
        self.assertEqual(p2["data"]["summaries"]["fetched"], 0)
        self.assertEqual(p2["data"]["summaries"]["cache_hits"], p2["data"]["summaries"]["requested"])
        self.assertEqual(len([c for c in fake.calls if c == ta._BS_URL]), 1)
        self.assertEqual(p1["plan"], p2["plan"])

    def test_cache_expires(self):
        fake, clock = FakeFpl(), Clock()
        source = FplDataSource(fetch=fake, clock=clock, summary_ttl=300, ttl=300)
        source.summaries([5])
        clock.t += 301
        source.summaries([5])
        self.assertEqual(len(fake.summary_calls(5)), 2)

    def test_failures_are_not_cached(self):
        fake = FakeFpl()
        fake.fail.add(sum_url(5))
        source = FplDataSource(fetch=fake, clock=Clock())
        out, stats = source.summaries([5])
        self.assertEqual((out, stats["failed"]), ({}, [5]))
        fake.fail.clear()
        out, stats = source.summaries([5])
        self.assertEqual(list(out), [5])

    def test_failed_fixtures_not_cached_and_retried_after_interval(self):
        fake, clock = FakeFpl(), Clock()
        fake.fail.add(ta._FX_URL)
        source = FplDataSource(fetch=fake, clock=clock, fixtures_retry=60)
        bs = source.bootstrap()
        source.params(bs)
        source.params(bs)
        self.assertEqual(len([c for c in fake.calls if c == ta._FX_URL]), 1)     # within retry window
        clock.t += 61
        fake.fail.clear()
        source.params(bs)
        self.assertTrue(source.fixtures_ok)
        self.assertEqual(len([c for c in fake.calls if c == ta._FX_URL]), 2)


# ─────────────────────────────────────────────────────────────────────────────
# TRANSPORTS: deployment handler and local server
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# PLAYER METADATA, GAIN BEFORE HITS, KEEP PLAYERS
# ─────────────────────────────────────────────────────────────────────────────

def collect_players(obj, out=None):
    """Every player dict (id, web_name, position, team) anywhere in a payload."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        if {"id", "web_name", "position", "team"} <= obj.keys():
            out.append(obj)
        for v in obj.values():
            collect_players(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_players(v, out)
    return out


def fake_with_codes():
    fake = FakeFpl()
    for e in fake.bootstrap["elements"]:
        e["code"] = 9000 + e["id"]
    return fake


class TestPlayerMetadata(unittest.TestCase):
    MOVE = [[12, 126]]

    def test_every_player_in_the_response_carries_photo_code_and_team_names(self):
        status, p, _ = run(fake_with_codes(), moves=self.MOVE)
        self.assertEqual(status, 200)
        self.assertIsNotNone(p["plan"]["requested"]["plan"])
        players = collect_players(p["plan"]) + p["squad"]
        self.assertGreater(len(collect_players(p["plan"]["requested"]["plan"])), 1)
        for d in players:
            self.assertEqual(d["photo_code"], 9000 + d["id"], d)
            self.assertEqual(d["team_name"], "Club %d" % d["team"])
            self.assertEqual(d["team_short"], "C%d" % d["team"])

    def test_missing_or_malformed_codes_become_none_without_failing_the_request(self):
        fake = fake_with_codes()
        by_id = {e["id"]: e for e in fake.bootstrap["elements"]}
        del by_id[12]["code"]
        by_id[126]["code"] = "9126"
        by_id[8]["code"] = 0
        by_id[9]["code"] = None
        status, p, _ = run(fake, moves=self.MOVE)
        self.assertEqual(status, 200)
        got = {d["id"]: d["photo_code"] for d in collect_players(p["plan"]) + p["squad"]}
        for pid in (12, 126, 8, 9):
            if pid in got:
                self.assertIsNone(got[pid], pid)
        self.assertEqual(got[1], 9001)
        self.assertIsNone(next(d for d in p["squad"] if d["id"] == 12)["photo_code"])

    def test_unknown_team_gives_none_names_for_that_club_only(self):
        fake = fake_with_codes()
        fake.bootstrap["teams"] = [t for t in fake.bootstrap["teams"] if t["id"] != 6]
        status, p, _ = run(fake, moves=self.MOVE)
        self.assertEqual(status, 200)
        for d in collect_players(p["plan"]) + p["squad"]:
            if d["team"] == 6:
                self.assertIsNone(d["team_name"])
                self.assertIsNone(d["team_short"])
            else:
                self.assertEqual(d["team_short"], "C%d" % d["team"])

    def test_missing_or_malformed_teams_list_never_breaks_the_response(self):
        for broken in (None, "nope", [{"id": "x"}, {"name": "no id"}, 7, {"id": 3, "name": 5, "short_name": ""}]):
            fake = fake_with_codes()
            if broken is None:
                del fake.bootstrap["teams"]
            else:
                fake.bootstrap["teams"] = broken
            status, p, _ = run(fake, moves=self.MOVE)
            self.assertEqual(status, 200, broken)
            for d in collect_players(p["plan"]) + p["squad"]:
                self.assertIsNone(d["team_name"], broken)
                self.assertIsNone(d["team_short"], broken)
                self.assertEqual(d["photo_code"], 9000 + d["id"])

    def test_bootstrap_without_any_codes_still_works(self):
        status, p, _ = run(FakeFpl(), moves=self.MOVE)
        self.assertEqual(status, 200)
        for d in collect_players(p["plan"]) + p["squad"]:
            self.assertIsNone(d["photo_code"])
            self.assertIn("team_name", d)


class TestGainBeforeHitsInResponse(unittest.TestCase):
    def test_before_hits_minus_hit_points_is_the_net_gain_for_every_plan(self):
        status, p, _ = run(free_transfers=0, max_hits=2)
        alts = p["plan"]["alternatives"]
        self.assertTrue(alts)
        for alt in alts:
            self.assertAlmostEqual(alt["projected_gain_before_hits"] - alt["hit_points"],
                                   alt["projected_gain_vs_hold"], delta=0.011)
        self.assertTrue(any(a["hit_points"] > 0 for a in alts))

    def test_no_hit_plans_have_identical_gains(self):
        status, p, _ = run(free_transfers=2)
        for alt in p["plan"]["alternatives"]:
            self.assertEqual(alt["hit_points"], 0)
            self.assertAlmostEqual(alt["projected_gain_before_hits"], alt["projected_gain_vs_hold"], delta=0.011)
        self.assertEqual(p["plan"]["hold"]["projected_gain_before_hits"], 0.0)


class TestKeepPlayersRequest(unittest.TestCase):
    def errors(self, **kw):
        with self.assertRaises(ApiError) as cm:
            parse_request(json.loads(body(**kw)))
        return " | ".join(cm.exception.details)

    def test_keep_ids_parsed_and_defaulted(self):
        self.assertEqual(parse_request(json.loads(body()))["keep_ids"], [])
        self.assertEqual(parse_request(json.loads(body(keep_ids=[3, 4])))["keep_ids"], [3, 4])

    def test_invalid_keep_ids_report_every_problem(self):
        self.assertIn("keep_ids must be a list of integer player ids", self.errors(keep_ids="3"))
        self.assertIn("keep_ids must be a list of integer player ids", self.errors(keep_ids=[1.5]))
        self.assertIn("kept player 121 is not in squad_ids", self.errors(keep_ids=[121]))
        self.assertIn("keep_ids must be distinct", self.errors(keep_ids=[3, 3]))
        self.assertIn("cannot be both sold and kept", self.errors(keep_ids=[12], out_ids=[12]))
        self.assertIn("cannot be both sold and kept", self.errors(keep_ids=[12], moves=[[12, 126]]))

    def test_keep_is_echoed_in_request_and_plan(self):
        status, p, _ = run(free_transfers=2, keep_ids=[12, 13])
        self.assertEqual(status, 200)
        self.assertEqual(p["request"]["keep_ids"], [12, 13])
        self.assertEqual(p["plan"]["inputs"]["keep_ids"], [12, 13])
        self.assertEqual(p["plan"]["search"]["coverage"]["squad_players_kept"], [12, 13])

    def test_kept_players_are_never_sold_end_to_end(self):
        status, base, _ = run(free_transfers=2, max_hits=1)
        sold = {t["out"]["id"] for a in base["plan"]["alternatives"] for t in a["transfers"]}
        self.assertTrue(sold, "fixture should produce at least one sale")
        status, p, _ = run(free_transfers=2, max_hits=1, keep_ids=sorted(sold))
        self.assertEqual(status, 200)
        sold_now = {t["out"]["id"] for a in p["plan"]["alternatives"] for t in a["transfers"]}
        self.assertFalse(sold_now & sold)

    def test_unknown_field_is_still_rejected(self):
        self.assertIn("unknown field", self.errors(keep=[1]))


class _Server:
    def __init__(self, handler_cls):
        self.httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


def post(url, data, ctype="application/json"):
    req = urllib.request.Request(url, data=data, headers={"Content-Type": ctype}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except ValueError:
            return e.code, None, dict(e.headers)


def strip_timing(p):
    p = copy.deepcopy(p)
    p.pop("timing_ms", None)
    return p


class TestTransports(unittest.TestCase):
    def setUp(self):
        # one independent source per transport, as in production (separate processes, separate caches)
        self.fakes = [FakeFpl(), FakeFpl()]
        self.sources = [FplDataSource(fetch=f, clock=Clock()) for f in self.fakes]
        patches = [mock.patch.object(API, "_source", self.sources[0]),
                   mock.patch.object(SERVER, "_TRANSFER_SOURCE", self.sources[1])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def both(self, data):
        with _Server(API.handler) as api_url, _Server(SERVER.Handler) as srv_url:
            a = post(api_url + "/api/transfer-plan", data)
            s = post(srv_url + "/api/transfer-plan", data)
        return a, s

    def test_deployment_handler_and_local_server_return_identical_results(self):
        (sa, pa, ha), (ss, ps, hs) = self.both(body(free_transfers=2, moves=[[12, 130]]))
        self.assertEqual((sa, ss), (200, 200))
        self.assertEqual(strip_timing(pa), strip_timing(ps))
        self.assertEqual(pa["plan"]["requested"]["intent"], "moves")
        for h in (ha, hs):
            self.assertEqual(h["Access-Control-Allow-Origin"], "*")
            self.assertIn("application/json", h["Content-Type"])

    def test_error_statuses_match_across_transports(self):
        for data, expected in ((body(bank_m=-1), 400), (b"nope", 400), (body(squad_ids=SQUAD[:14] + [99999]), 400)):
            (sa, pa, _), (ss, ps, _) = self.both(data)
            self.assertEqual((sa, ss), (expected, expected))
            self.assertEqual(pa, ps)
        for fake, source in zip(self.fakes, self.sources):
            fake.fail.add(ta._BS_URL)
            source._bootstrap = None                      # force a refetch of bootstrap
        (sa, pa, _), (ss, ps, _) = self.both(body())
        self.assertEqual((sa, ss), (502, 502))
        self.assertEqual(pa["status"], "UPSTREAM_ERROR")

    def test_oversized_body_rejected_by_both(self):
        (sa, _, _), (ss, _, _) = self.both(b"x" * (ta.MAX_BODY_BYTES + 10))
        self.assertEqual((sa, ss), (413, 413))

    def test_options_preflight_on_deployment_handler(self):
        with _Server(API.handler) as url:
            req = urllib.request.Request(url + "/api/transfer-plan", method="OPTIONS")
            with urllib.request.urlopen(req, timeout=10) as r:
                self.assertEqual(r.status, 204)
                self.assertIn("POST", r.headers["Access-Control-Allow-Methods"])

    def test_scanner_route_and_404_unchanged_on_local_server(self):
        with mock.patch.object(SERVER.Handler, "_squad_check", return_value={"status": "SENTINEL"}):
            with _Server(SERVER.Handler) as url:
                s, p, _ = post(url + "/api/squad-check", b"img")
                self.assertEqual((s, p["status"]), (200, "SENTINEL"))
                s, _, _ = post(url + "/api/does-not-exist", b"{}")
                self.assertEqual(s, 404)

    def test_scanner_modules_are_untouched_by_the_new_endpoint(self):
        squad_check = load("squad_check_unchanged", "api/squad-check.py")
        self.assertTrue(callable(squad_check.handler))
        self.assertTrue(hasattr(squad_check, "_process") or hasattr(squad_check.handler, "_process"))
        import inspect
        self.assertNotIn("transfer", inspect.getsource(squad_check).lower())


if __name__ == "__main__":
    unittest.main()
