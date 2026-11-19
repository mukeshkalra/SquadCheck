"""Tests for fpl/engine/fpl_team.py (POST /api/fpl-team). FPL is mocked: no network."""

import json
import unittest
import urllib.error

from fpl.engine import fpl_team as ft
from fpl.engine.transfer_adapter import ApiError, FplDataSource
from fpl.engine.tests.test_transfer_adapter import Clock, FakeFpl, SQUAD, POS


def picks(ids=SQUAD, cap=9, vice=10):
    return {"picks": [{"element": e, "position": i + 1, "multiplier": 1, "is_captain": e == cap,
                       "is_vice_captain": e == vice} for i, e in enumerate(ids)],
            "entry_history": {"bank": 9, "event": 6}}


class Fetch:
    def __init__(self, entry=None, pk=None, entry_err=None, picks_err=None):
        self.entry = {"name": "Test FC", "current_event": 6} if entry is None else entry
        self.pk = picks() if pk is None else pk
        self.entry_err, self.picks_err, self.calls = entry_err, picks_err, []

    def __call__(self, url):
        self.calls.append(url)
        if "/picks/" in url:
            if self.picks_err:
                raise self.picks_err
            return self.pk
        if self.entry_err:
            raise self.entry_err
        return self.entry


def http(code):
    return urllib.error.HTTPError("u", code, "x", {}, None)


def run(fetch, body=None, fake=None):
    src = FplDataSource(fetch=fake or FakeFpl(), clock=Clock())
    return ft.handle(json.dumps(body if body is not None else {"fpl_id": 123}).encode(), src, fetch)


class ParseTests(unittest.TestCase):
    def test_valid_and_numeric_string(self):
        self.assertEqual(ft.parse_request({"fpl_id": 5}), 5)
        self.assertEqual(ft.parse_request({"fpl_id": " 5 "}), 5)

    def test_rejects_bad_ids(self):
        for v in (0, -1, 1.5, "abc", "", None, True, [1], 10**9, "1e5", "12 3"):
            with self.assertRaises(ApiError):
                ft.parse_request({"fpl_id": v})

    def test_rejects_extra_or_missing_fields(self):
        for body in ({}, {"fpl_id": 1, "x": 1}, [], "1"):
            with self.assertRaises(ApiError):
                ft.parse_request(body)


class BuildTests(unittest.TestCase):
    def test_ok_shape(self):
        st, p = run(Fetch())
        self.assertEqual(st, 200)
        m = p["manual_squad"]
        self.assertEqual(len(m["names"]), 15)
        self.assertEqual(m["bench"], m["names"][11:])
        self.assertEqual((p["team_name"], p["picks_gameweek"], p["bank_m"]), ("Test FC", 6, 0.9))
        self.assertIn("GW6 deadline", p["note"])

    def test_captain_and_vice_must_be_starters(self):
        _, p = run(Fetch(pk=picks(cap=9, vice=10)))
        self.assertEqual(p["manual_squad"]["captain"], "S9")
        self.assertEqual(p["manual_squad"]["vice"], "S10")
        _, p2 = run(Fetch(pk=picks(cap=15, vice=14)))        # bench players are never returned as captains
        self.assertIsNone(p2["manual_squad"]["captain"])
        self.assertIsNone(p2["manual_squad"]["vice"])

    def test_picks_are_ordered_by_position_not_arrival(self):
        pk = picks()
        pk["picks"].reverse()
        _, p = run(Fetch(pk=pk))
        self.assertEqual(p["manual_squad"]["names"][0], "S1")

    def test_unknown_team_is_404(self):
        st, p = run(Fetch(entry_err=http(404)))
        self.assertEqual((st, p["status"]), (404, "NOT_FOUND"))

    def test_upstream_errors_are_502_and_leak_nothing(self):
        for f in (Fetch(entry_err=http(500)), Fetch(entry_err=OSError("boom")), Fetch(picks_err=http(500)),
                  Fetch(picks_err=OSError("boom"))):
            st, p = run(f)
            self.assertEqual(st, 502)
            self.assertNotIn("boom", json.dumps(p))

    def test_no_picks_yet(self):
        self.assertEqual(run(Fetch(entry={"name": "x", "current_event": None}))[0], 409)
        self.assertEqual(run(Fetch(picks_err=http(404)))[0], 409)

    def test_not_15_players_or_unknown_player(self):
        self.assertEqual(run(Fetch(pk=picks(SQUAD[:14])))[0], 422)
        self.assertEqual(run(Fetch(pk=picks(SQUAD[:14] + [9999])))[0], 422)

    def test_duplicate_names_are_refused_not_guessed(self):
        fake = FakeFpl()
        for e in fake.bootstrap["elements"]:
            if e["id"] in (1, 2):
                e["web_name"] = "Silva"
        st, p = run(Fetch(), fake=fake)
        self.assertEqual((st, p["status"]), (422, "BAD_TEAM"))

    def test_bank_missing_or_negative_is_none(self):
        pk = picks(); pk["entry_history"] = {}
        self.assertIsNone(run(Fetch(pk=pk))[1]["bank_m"])

    def test_bad_json_and_big_body(self):
        src = FplDataSource(fetch=FakeFpl(), clock=Clock())
        self.assertEqual(ft.handle(b"{x", src, Fetch())[0], 400)
        self.assertEqual(ft.handle(b"x" * (ft.MAX_BODY_BYTES + 1), src, Fetch())[0], 413)

    def test_bootstrap_failure(self):
        from fpl.engine import transfer_adapter as ta
        fake = FakeFpl(); fake.fail.add(ta._BS_URL)
        self.assertEqual(run(Fetch(), fake=fake)[0], 502)

    def test_result_goes_through_the_existing_manual_squad_check(self):
        from fpl.engine.manual_squad import manual_scan_result
        fake = FakeFpl()
        letters = "ABCDEFGHIJKLMNO"
        for e in fake.bootstrap["elements"]:
            if e["id"] in SQUAD:
                e["web_name"] = "Zq" + letters[e["id"] - 1] * 3       # alphabetic, as real names are
        order = [1, 3, 4, 5, 6, 8, 9, 10, 11, 13, 14, 2, 7, 12, 15]       # XI first, bench has the spare keeper
        _, p = run(Fetch(pk=picks(order)), fake=fake)
        r = manual_scan_result(p["manual_squad"], fake.bootstrap)
        self.assertEqual(r["status"], "VALID")


if __name__ == "__main__":
    unittest.main()
