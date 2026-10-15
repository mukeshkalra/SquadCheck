"""
Tests for GET /api/players: transfer_adapter.build_players_response / handle_players and the two
transports (api/players.py, fpl/server.py). FPL is mocked with the transfer-adapter fixtures: no
network, deterministic data. The world has 95 usable players and 20 clubs ("Club n" / "Cn").
"""

import copy
import json
import logging
import unittest
import urllib.error
import urllib.request
from unittest import mock

from fpl.engine import transfer_adapter as ta
from fpl.engine.transfer_adapter import FplDataSource, handle, handle_players, build_players_response
from fpl.engine.tests.test_transfer_adapter import (
    API as TRANSFER_API, SERVER, Clock, FakeFpl, _Server, body, load,
)

logging.getLogger("squadcheck.transfer").setLevel(logging.CRITICAL)
PLAYERS_API = load("players_api_under_test", "api/players.py")
FIELDS = {"id", "name", "team", "team_name", "team_short", "position", "price", "photo_code"}


def fake_with_codes():
    fake = FakeFpl()
    for e in fake.bootstrap["elements"]:
        e["code"] = 9000 + e["id"]
    return fake


def source_for(fake, clock=None):
    return FplDataSource(fetch=fake, clock=clock or Clock())


def bootstrap_calls(fake):
    return [c for c in fake.calls if c == ta._BS_URL]


def get(url, method="GET", data=None):
    req = urllib.request.Request(url, method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except ValueError:
            return e.code, None, dict(e.headers)


class TestDirectoryResponse(unittest.TestCase):
    def test_successful_response_shape_and_content(self):
        fake = fake_with_codes()
        status, p = handle_players(source_for(fake))
        self.assertEqual(status, 200)
        self.assertEqual(p["status"], "OK")
        self.assertEqual(p["gameweek"], 7)
        self.assertEqual(p["count"], 95)
        self.assertEqual(len(p["players"]), 95)
        self.assertEqual(p["skipped"], 0)
        self.assertIn("tenths", p["price_unit"])
        for d in p["players"]:
            self.assertEqual(set(d), FIELDS)
        by_id = {d["id"]: d for d in p["players"]}
        s12 = by_id[12]
        self.assertEqual((s12["name"], s12["team"], s12["position"], s12["price"], s12["photo_code"]),
                         ("S12", 12, 3, 55, 9012))
        self.assertEqual((s12["team_name"], s12["team_short"]), ("Club 12", "C12"))
        self.assertEqual(by_id[201]["position"], 1)
        self.assertEqual([d["id"] for d in p["players"]], sorted(by_id))

    def test_prices_are_integer_tenths_like_the_other_responses(self):
        status, p = handle_players(source_for(fake_with_codes()))
        self.assertTrue(all(isinstance(d["price"], int) and d["price"] > 0 for d in p["players"]))
        status2, plan = handle(body(moves=[[12, 126]]), source_for(fake_with_codes()))
        squad = {d["id"]: d["price"] for d in plan["squad"]}
        directory = {d["id"]: d["price"] for d in p["players"]}
        self.assertTrue(all(directory[i] == price for i, price in squad.items()))

    def test_response_is_json_serialisable_and_has_no_internals(self):
        status, p = handle_players(source_for(fake_with_codes()))
        text = json.dumps(p)
        for leak in ("http", "Traceback", "api/", "timing", "element-summary"):
            self.assertNotIn(leak, text)


class TestMalformedAndMissingData(unittest.TestCase):
    def test_unusable_entries_are_skipped_and_counted(self):
        fake = fake_with_codes()
        els = fake.bootstrap["elements"]
        els[0]["web_name"] = ""                      # empty name
        els[1]["web_name"] = "   "                   # blank name
        els[2]["web_name"] = 42                      # not a string
        del els[3]["web_name"]                       # missing name
        els[4]["element_type"] = 9                   # invalid position
        els[5]["team"] = "x"                         # club not an int
        els[6]["now_cost"] = 0                       # no price
        els[7]["now_cost"] = None
        els[8]["id"] = els[9]["id"]                  # duplicate id: second one skipped
        els.append("not a player")
        els.append(None)
        status, p = handle_players(source_for(fake))
        self.assertEqual(status, 200)
        self.assertEqual(p["skipped"], 11)
        self.assertEqual(p["count"], 95 - 9)
        ids = [d["id"] for d in p["players"]]
        self.assertEqual(len(ids), len(set(ids)))
        for d in p["players"]:
            self.assertTrue(d["name"].strip())
            self.assertIn(d["position"], (1, 2, 3, 4))

    def test_names_are_trimmed(self):
        fake = fake_with_codes()
        fake.bootstrap["elements"][0]["web_name"] = "  Padded  "
        status, p = handle_players(source_for(fake))
        self.assertEqual(next(d for d in p["players"] if d["id"] == 1)["name"], "Padded")

    def test_missing_or_bad_photo_codes_are_null_not_guessed(self):
        fake = fake_with_codes()
        by_id = {e["id"]: e for e in fake.bootstrap["elements"]}
        del by_id[1]["code"]
        by_id[2]["code"] = "9002"
        by_id[3]["code"] = 0
        by_id[4]["code"] = -5
        by_id[5]["code"] = None
        by_id[6]["code"] = True
        status, p = handle_players(source_for(fake))
        got = {d["id"]: d["photo_code"] for d in p["players"]}
        for pid in (1, 2, 3, 4, 5, 6):
            self.assertIsNone(got[pid], pid)
        self.assertEqual(got[7], 9007)

    def test_missing_or_malformed_teams_give_null_names_but_keep_the_players(self):
        for broken in (None, "nope", [{"id": "x"}, 7, {"id": 3, "name": 5, "short_name": ""}]):
            fake = fake_with_codes()
            if broken is None:
                del fake.bootstrap["teams"]
            else:
                fake.bootstrap["teams"] = broken
            status, p = handle_players(source_for(fake))
            self.assertEqual(status, 200, broken)
            self.assertEqual(p["count"], 95)
            self.assertTrue(all(d["team_name"] is None and d["team_short"] is None for d in p["players"]))

    def test_partly_missing_teams(self):
        fake = fake_with_codes()
        fake.bootstrap["teams"] = [t for t in fake.bootstrap["teams"] if t["id"] != 5]
        status, p = handle_players(source_for(fake))
        for d in p["players"]:
            if d["team"] == 5:
                self.assertIsNone(d["team_name"])
            else:
                self.assertEqual(d["team_short"], "C%d" % d["team"])

    def test_no_usable_players_at_all_is_a_safe_upstream_error(self):
        fake = fake_with_codes()
        fake.bootstrap["elements"] = [{"id": 1, "web_name": "A"}, "junk", {"id": 2, "now_cost": 50}]
        status, p = handle_players(source_for(fake))
        self.assertEqual(status, 502)
        self.assertEqual(p["status"], "UPSTREAM_ERROR")
        self.assertEqual(set(p), {"status", "error", "details"})
        self.assertNotIn("junk", json.dumps(p))


class TestUpstreamFailure(unittest.TestCase):
    def test_bootstrap_failure_is_a_generic_502(self):
        fake = fake_with_codes()
        fake.fail.add(ta._BS_URL)
        status, p = handle_players(source_for(fake))
        self.assertEqual(status, 502)
        self.assertEqual(p["status"], "UPSTREAM_ERROR")
        self.assertEqual(set(p), {"status", "error", "details"})
        text = json.dumps(p)
        self.assertNotIn("simulated", text)
        self.assertNotIn("http", text)

    def test_malformed_bootstrap_payload_is_a_generic_502(self):
        for bad in ({"elements": [], "events": []}, {"elements": "x"}, ["list"], None):
            fake = fake_with_codes()
            fake.bootstrap = bad
            status, p = handle_players(source_for(fake))
            self.assertEqual(status, 502, bad)
            self.assertEqual(p["status"], "UPSTREAM_ERROR")

    def test_unexpected_error_returns_500_without_detail(self):
        src = source_for(fake_with_codes())
        with mock.patch.object(src, "bootstrap", side_effect=RuntimeError("secret-token-123")):
            status, p = handle_players(src)
        self.assertEqual(status, 500)
        self.assertEqual(p, {"status": "ERROR", "error": "Internal error", "details": []})
        self.assertNotIn("secret", json.dumps(p))

    def test_never_raises(self):
        for exc in (RuntimeError("x"), KeyError("k"), ValueError("v"), ta.ApiError(409, "X", "msg")):
            src = source_for(fake_with_codes())
            with mock.patch.object(src, "bootstrap", side_effect=exc):
                status, p = handle_players(src)
            self.assertIn(status, (409, 500))
            self.assertIn("status", p)

    def test_recovers_after_a_failed_fetch(self):
        fake = fake_with_codes()
        clock = Clock()
        src = source_for(fake, clock)
        fake.fail.add(ta._BS_URL)
        self.assertEqual(handle_players(src)[0], 502)
        fake.fail.clear()
        status, p = handle_players(src)
        self.assertEqual((status, p["count"]), (200, 95))


class TestSharedCacheAndUpstreamUse(unittest.TestCase):
    def test_one_bootstrap_fetch_serves_many_requests_and_nothing_else_is_fetched(self):
        fake = fake_with_codes()
        src = source_for(fake)
        for _ in range(5):
            self.assertEqual(handle_players(src)[0], 200)
        self.assertEqual(len(bootstrap_calls(fake)), 1)
        self.assertEqual(fake.calls, [ta._BS_URL])          # no summaries, no fixtures

    def test_shares_the_cache_with_transfer_plan(self):
        fake = fake_with_codes()
        src = source_for(fake)
        self.assertEqual(handle(body(free_transfers=0), src)[0], 200)
        before = len(bootstrap_calls(fake))
        self.assertEqual(handle_players(src)[0], 200)
        self.assertEqual(len(bootstrap_calls(fake)), before)

    def test_cache_expires_with_the_source_ttl(self):
        fake = fake_with_codes()
        clock = Clock()
        src = source_for(fake, clock)
        handle_players(src)
        clock.t += 299
        handle_players(src)
        self.assertEqual(len(bootstrap_calls(fake)), 1)
        clock.t += 5
        handle_players(src)
        self.assertEqual(len(bootstrap_calls(fake)), 2)

    def test_does_not_modify_the_cached_bootstrap(self):
        fake = fake_with_codes()
        src = source_for(fake)
        before = copy.deepcopy(src.bootstrap())
        handle_players(src)
        self.assertEqual(src.bootstrap(), before)

    def test_build_players_response_does_not_touch_planner_behaviour(self):
        fake = fake_with_codes()
        a = handle(body(free_transfers=2), source_for(fake))[1]
        handle_players(source_for(fake))
        b = handle(body(free_transfers=2), source_for(fake))[1]
        for r in (a, b):
            r.pop("timing_ms")
        self.assertEqual(a, b)


class TestTransports(unittest.TestCase):
    def setUp(self):
        self.fakes = [fake_with_codes(), fake_with_codes()]
        self.sources = [source_for(f) for f in self.fakes]
        for p in (mock.patch.object(PLAYERS_API, "_source", self.sources[0]),
                  mock.patch.object(SERVER, "_TRANSFER_SOURCE", self.sources[1])):
            p.start()
            self.addCleanup(p.stop)

    def both(self, fn):
        with _Server(PLAYERS_API.handler) as api_url, _Server(SERVER.Handler) as srv_url:
            return fn(api_url), fn(srv_url)

    def test_get_returns_the_same_directory_from_both_transports(self):
        (s1, p1, h1), (s2, p2, h2) = self.both(lambda u: get(u + "/api/players"))
        self.assertEqual((s1, s2), (200, 200))
        self.assertEqual(p1, p2)
        self.assertEqual(p1["count"], 95)
        for h in (h1, h2):
            self.assertIn("application/json", h["Content-Type"])
            self.assertEqual(h["Access-Control-Allow-Origin"], "*")
            self.assertEqual(h["Cache-Control"], ta.PLAYERS_CACHE_CONTROL)

    def test_query_string_and_trailing_slash_are_tolerated_on_the_server(self):
        with _Server(SERVER.Handler) as url:
            self.assertEqual(get(url + "/api/players?q=abc")[1]["count"], 95)
            self.assertEqual(get(url + "/api/players/")[1]["count"], 95)
        with _Server(PLAYERS_API.handler) as url:
            self.assertEqual(get(url + "/api/players?q=abc")[1]["count"], 95)

    def test_options_preflight(self):
        (s1, _, h1), (s2, _, h2) = self.both(lambda u: get(u + "/api/players", method="OPTIONS"))
        self.assertEqual((s1, s2), (204, 204))
        for h in (h1, h2):
            self.assertIn("GET", h["Access-Control-Allow-Methods"])

    def test_other_methods_are_rejected_with_405_and_allow(self):
        (s1, p1, h1), (s2, p2, h2) = self.both(lambda u: get(u + "/api/players", method="POST", data=b"{}"))
        self.assertEqual((s1, s2), (405, 405))
        self.assertEqual(p1["status"], "METHOD_NOT_ALLOWED")
        self.assertEqual(p2["status"], "METHOD_NOT_ALLOWED")
        self.assertIn("GET", h1["Allow"])
        self.assertIn("GET", h2["Allow"])

    def test_upstream_failure_over_http_is_502_and_not_cached(self):
        for f in self.fakes:
            f.fail.add(ta._BS_URL)
        (s1, p1, h1), (s2, p2, h2) = self.both(lambda u: get(u + "/api/players"))
        self.assertEqual((s1, s2), (502, 502))
        for p, h in ((p1, h1), (p2, h2)):
            self.assertEqual(p["status"], "UPSTREAM_ERROR")
            self.assertEqual(h["Cache-Control"], "no-store")
            self.assertNotIn("simulated", json.dumps(p))

    def test_transfer_plan_and_the_static_site_still_work_on_the_local_server(self):
        with _Server(SERVER.Handler) as url:
            req = urllib.request.Request(url + "/api/transfer-plan", data=body(free_transfers=0),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=20) as r:
                self.assertEqual(r.status, 200)
            with urllib.request.urlopen(url + "/vercel.json", timeout=20) as r:
                self.assertEqual(r.status, 200)
            self.assertEqual(get(url + "/api/nothing-here")[0], 404)

    def test_transfer_plan_api_function_is_unchanged_in_routing(self):
        with _Server(TRANSFER_API.handler) as url:
            status, p, _ = get(url + "/api/transfer-plan", method="POST", data=b"{}")
            self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
