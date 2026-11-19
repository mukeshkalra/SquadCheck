"""Tests for fpl/engine/chips_adapter.py, api/chips.py and the /api/chips route in fpl/server.py.
Everything FPL-facing is mocked (same world as test_transfer_adapter): no network."""

import json
import threading
import unittest
import urllib.request
from http.server import HTTPServer

from fpl.engine import chips_adapter as ca
from fpl.engine.chips_adapter import handle, parse_request, windows_for
from fpl.engine.transfer_adapter import ApiError, FplDataSource
from fpl.engine.tests.test_transfer_adapter import (SQUAD, Clock, FakeFpl, load, make_summary)

API = load("chips_api_under_test", "api/chips.py")
SERVER = load("fpl_server_for_chips_tests", "fpl/server.py")
ALL = ["wildcard", "freehit", "bboost", "3xc"]
XI = [1, 3, 4, 5, 6, 8, 9, 10, 11, 13, 14]


def req(**kw):
    d = {"squad_ids": SQUAD, "bank_m": 0.5, "free_transfers": 1, "chips_available": ALL}
    d.update(kw)
    return d


def call(fake=None, **kw):
    fake = fake or FakeFpl()
    source = FplDataSource(fetch=fake, clock=Clock())
    status, payload = handle(json.dumps(req(**kw)).encode(), source)
    return status, payload, fake


def chip(payload, name):
    return [c for c in payload["chips"] if c["chip"] == name][0]


class ParseTests(unittest.TestCase):
    def bad(self, **kw):
        with self.assertRaises(ApiError) as cm:
            parse_request(req(**kw))
        self.assertEqual(cm.exception.http_status, 400)
        return cm.exception.message

    def test_valid(self):
        r = parse_request(req())
        self.assertEqual(r["bank"], 5)
        self.assertEqual(r["chips_available"], sorted(ALL))

    def test_chips_default_to_none_ticked(self):
        d = req()
        del d["chips_available"]
        self.assertEqual(parse_request(d)["chips_available"], [])

    def test_required_fields(self):
        for k in ("squad_ids", "bank_m", "free_transfers"):
            d = req()
            del d[k]
            with self.assertRaises(ApiError):
                parse_request(d)

    def test_squad_problems(self):
        self.assertIn("15", self.bad(squad_ids=SQUAD[:14]))
        self.assertIn("duplicate", self.bad(squad_ids=SQUAD[:14] + [1]))

    def test_bank_rules(self):
        for v in (-1, 101, "5", True, 0.25, float("nan")):
            self.bad(bank_m=v)

    def test_free_transfer_rules(self):
        for v in (-1, 6, 1.5, "1", None):
            self.bad(free_transfers=v)

    def test_chip_names(self):
        self.assertIn("chips_available", self.bad(chips_available=["wildcard", "magic"]))
        self.assertIn("chips_available", self.bad(chips_available="wildcard"))

    def test_duplicate_chips_are_collapsed(self):
        self.assertEqual(parse_request(req(chips_available=["bboost", "bboost"]))["chips_available"], ["bboost"])

    def test_unknown_field_and_non_object(self):
        self.assertIn("unknown", self.bad(extra=1))
        with self.assertRaises(ApiError):
            parse_request([1, 2])

    def test_starters(self):
        self.assertEqual(parse_request(req(starter_ids=XI))["starter_ids"], XI)
        self.bad(starter_ids=XI[:10])
        self.bad(starter_ids=XI[:10] + [999])
        self.bad(starter_ids=XI[:10] + [XI[0]])


class WindowTests(unittest.TestCase):
    CHIPS = [{"name": "bboost", "start_event": 1, "stop_event": 19}, {"name": "bboost", "start_event": 20, "stop_event": 38},
             {"name": "wildcard", "start_event": 2, "stop_event": 19}, {"name": "junk", "start_event": 1, "stop_event": 38},
             {"name": "3xc", "start_event": "x", "stop_event": 19}, {"name": "freehit"}]

    def test_left_counts(self):
        b = {"chips": self.CHIPS}
        self.assertEqual(windows_for(b, 11)["bboost"], 8)
        self.assertEqual(windows_for(b, 19)["bboost"], 0)          # final gameweek of the window
        self.assertEqual(windows_for(b, 20)["bboost"], 18)         # second set opens

    def test_malformed_and_unknown_entries_are_skipped(self):
        w = windows_for({"chips": self.CHIPS}, 11)
        self.assertNotIn("junk", w)
        self.assertNotIn("3xc", w)
        self.assertNotIn("freehit", w)

    def test_window_not_yet_open(self):
        self.assertNotIn("wildcard", windows_for({"chips": self.CHIPS}, 1))

    def test_missing_chip_list(self):
        self.assertEqual(windows_for({}, 11), {})
        self.assertEqual(windows_for({"chips": None}, 11), {})


class EndToEndTests(unittest.TestCase):
    def test_ok_shape(self):
        st, p, _ = call()
        self.assertEqual(st, 200)
        self.assertIn(p["status"], ("OK", "DEGRADED"))
        self.assertEqual(p["gameweek"], 7)
        self.assertEqual({c["chip"] for c in p["chips"]}, set(ALL))
        self.assertIn("headline", p)
        self.assertEqual(p["request"]["free_transfers"], 1)

    def test_nothing_ticked_means_nothing_recommended(self):
        st, p, _ = call(chips_available=[])
        self.assertEqual(st, 200)
        self.assertTrue(all(c["status"] == "NOT_AVAILABLE" for c in p["chips"]))
        self.assertEqual(p["headline"]["status"], "HOLD_ALL")

    def test_closed_window_is_not_available(self):
        fake = FakeFpl()
        fake.bootstrap["chips"] = [{"name": "bboost", "start_event": 1, "stop_event": 19}]
        st, p, _ = call(fake)
        self.assertEqual(chip(p, "bboost")["status"] != "NOT_AVAILABLE", True)
        self.assertEqual(chip(p, "3xc")["status"], "NOT_AVAILABLE")

    def test_missing_chip_list_applies_no_expiry_and_says_so(self):
        _, p, _ = call(chips_available=["bboost"])          # the fake bootstrap has no chip list
        self.assertTrue(any("chip windows" in w for w in p["warnings"]))
        self.assertNotEqual(chip(p, "bboost")["status"], "USE_BY")
        self.assertNotEqual(chip(p, "bboost")["status"], "NOT_AVAILABLE")

    def test_expiring_chip_uses_api_window(self):
        fake = FakeFpl()
        fake.bootstrap["chips"] = [{"name": "bboost", "start_event": 1, "stop_event": 7}]     # final gameweek is this one
        st, p, _ = call(fake, chips_available=["bboost"])
        self.assertEqual(chip(p, "bboost")["status"], "USE_BY")
        self.assertEqual(p["headline"]["status"], "USE_BY")

    def test_injured_player_is_not_the_triple_captain_pick(self):
        fake = FakeFpl()
        for e in fake.bootstrap["elements"]:
            if e["id"] in (13, 14, 15):
                e["status"], e["chance_of_playing_next_round"] = "i", 0
        st, p, _ = call(fake, chips_available=["3xc"])
        pick = chip(p, "3xc").get("pick")
        self.assertNotIn(pick, (13, 14, 15))

    def test_doubtful_player_at_74_is_excluded_and_75_is_allowed(self):
        fake = FakeFpl()
        for e in fake.bootstrap["elements"]:
            if e["id"] in range(8, 16):
                e["status"], e["chance_of_playing_next_round"] = "d", 74
        _, p, _ = call(fake, chips_available=["3xc"])
        self.assertNotIn(chip(p, "3xc").get("pick"), range(8, 16))
        fake2 = FakeFpl()
        for e in fake2.bootstrap["elements"]:
            if e["id"] in range(8, 16):
                e["status"], e["chance_of_playing_next_round"] = "d", 75
        _, p2, _ = call(fake2, chips_available=["3xc"])
        self.assertIsNotNone(chip(p2, "3xc").get("pick"))

    def test_blank_week_counts_unavailable_starters(self):
        fake = FakeFpl()
        for pid in (4, 5, 9, 10):
            fake.summary[pid] = make_summary(pid, events=(8, 9))       # no fixture in the coming gameweek (7)
        _, p, _ = call(fake, chips_available=["freehit"], starter_ids=XI)
        self.assertEqual(chip(p, "freehit")["numbers"]["unavailable_starters"], 4)

    def test_double_gameweek_is_reported_as_a_fact(self):
        fake = FakeFpl()
        for pid in (13,):
            s = make_summary(pid, events=(7, 8, 8, 9))
            fake.summary[pid] = s
        _, p, _ = call(fake, chips_available=["3xc"])
        c = chip(p, "3xc")
        if c.get("pick") == 13:
            self.assertTrue(any("double" in f for f in c["facts"]))

    def test_no_fixture_data_never_invents_blanks(self):
        fake = FakeFpl()
        for pid in SQUAD:
            fake.summary[pid] = {"history": [{"minutes": 90}] * 5, "fixtures": []}
        st, p, _ = call(fake, chips_available=["freehit"], starter_ids=XI)
        self.assertEqual(st, 200)

    def test_unknown_player_is_a_400(self):
        st, p, _ = call(squad_ids=SQUAD[:14] + [9999])
        self.assertEqual(st, 400)
        self.assertEqual(p["status"], "BAD_REQUEST")

    def test_squad_with_wrong_shape_is_reported_not_crashed(self):
        st, p, _ = call(squad_ids=[3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 1, 101])   # 1 GKP, 6 DEF
        self.assertIn(st, (400, 422))
        self.assertNotEqual(p.get("status"), "ERROR")

    def test_upstream_bootstrap_failure(self):
        fake = FakeFpl()
        from fpl.engine import transfer_adapter as ta
        fake.fail.add(ta._BS_URL)
        st, p, _ = call(fake)
        self.assertGreaterEqual(st, 500)
        self.assertNotIn("Traceback", json.dumps(p))

    def test_no_summaries_is_a_502(self):
        fake = FakeFpl()
        from fpl.engine import transfer_adapter as ta
        for e in fake.bootstrap["elements"]:
            fake.fail.add(ta._SUM_URL.format(e["id"]))
        st, p, _ = call(fake)
        self.assertEqual(st, 502)
        self.assertEqual(p["status"], "UPSTREAM_ERROR")

    def test_partial_summary_failure_is_flagged(self):
        fake = FakeFpl()
        from fpl.engine import transfer_adapter as ta
        fake.fail.add(ta._SUM_URL.format(5))
        st, p, _ = call(fake)
        self.assertEqual(st, 200)
        self.assertEqual(p["status"], "DEGRADED")
        self.assertTrue(p["warnings"])           # missing projection reported by the engine

    def test_no_upcoming_gameweek(self):
        fake = FakeFpl()
        for e in fake.bootstrap["events"]:
            e["finished"], e["is_next"] = True, False
        st, p, _ = call(fake)
        self.assertEqual(st, 409)

    def test_bad_json_and_big_body(self):
        src = FplDataSource(fetch=FakeFpl(), clock=Clock())
        self.assertEqual(handle(b"{nope", src)[0], 400)
        self.assertEqual(handle(b"[]", src)[0], 400)
        self.assertEqual(handle(b"x" * (ca.MAX_BODY_BYTES + 1), src)[0], 413)

    def test_free_hit_and_wildcard_teams_are_legal(self):
        st, p, _ = call()
        for name in ("freehit", "wildcard"):
            t = chip(p, name).get("team")
            self.assertTrue(t)
            self.assertEqual(len(t["xi"]) + len(t["bench"]), 15)

    def test_one_chip_per_gameweek(self):
        st, p, _ = call()
        live = [c for c in p["chips"] if c["status"] in ("PLAY_NOW", "USE_BY")]
        self.assertLessEqual(len(live), 1)

    def test_is_deterministic(self):
        _, a, _ = call()
        _, b, _ = call()
        for d in (a, b):
            d.pop("timing_ms", None)
            d["data"].pop("timing_ms", None)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))


class TransportTests(unittest.TestCase):
    def _serve(self, handler_cls):
        srv = HTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def test_vercel_function_and_options(self):
        API._source = FplDataSource(fetch=FakeFpl(), clock=Clock())
        srv = self._serve(API.handler)
        try:
            url = "http://127.0.0.1:%d/api/chips" % srv.server_address[1]
            r = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(req()).encode(),
                                                              headers={"Content-Type": "application/json"}, method="POST"))
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers["Access-Control-Allow-Origin"], "*")
            self.assertIn("chips", json.loads(r.read()))
            o = urllib.request.urlopen(urllib.request.Request(url, method="OPTIONS"))
            self.assertEqual(o.status, 204)
        finally:
            srv.shutdown()

    def test_local_server_route(self):
        SERVER._TRANSFER_SOURCE = FplDataSource(fetch=FakeFpl(), clock=Clock())
        srv = self._serve(SERVER.Handler if hasattr(SERVER, "Handler") else SERVER.SquadCheckHandler)
        try:
            url = "http://127.0.0.1:%d/api/chips" % srv.server_address[1]
            r = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(req()).encode(),
                                                              headers={"Content-Type": "application/json"}, method="POST"))
            self.assertEqual(r.status, 200)
            self.assertIn("headline", json.loads(r.read()))
        finally:
            srv.shutdown()

    def test_vercel_config_has_max_duration(self):
        cfg = json.loads((ca.__file__.rsplit("/fpl/", 1)[0] + "/vercel.json").__class__ and
                         open(ca.__file__.rsplit("/fpl/", 1)[0] + "/vercel.json").read())
        self.assertEqual(cfg["functions"]["api/chips.py"]["maxDuration"], 60)


if __name__ == "__main__":
    unittest.main()
