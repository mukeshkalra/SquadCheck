"""
Tests for the optional "strategy" field of /api/transfer-plan (transfer_adapter.parse_request /
build_response). Mocked FPL data (the transfer-adapter fixtures): no network.

"sensible" = no points hits. "everything" = no narrowing (same as omitting the field). "upside" is
rejected on purpose: the planner has no ceiling metric, so there is nothing real to rank by.
Ranking is never changed by a strategy: it stays net gain after hits.
"""

import json
import logging
import unittest

from fpl.engine import transfer_adapter as ta
from fpl.engine.transfer_adapter import ApiError, FplDataSource, handle, parse_request
from fpl.engine.tests.test_transfer_adapter import Clock, FakeFpl, body

logging.getLogger("squadcheck.transfer").setLevel(logging.CRITICAL)


def run(fake=None, **kw):
    fake = fake or FakeFpl()
    status, payload = handle(body(**kw), FplDataSource(fetch=fake, clock=Clock()))
    return status, payload


def comparable(payload):
    p = json.loads(json.dumps(payload))
    p.pop("timing_ms", None)
    p["request"].pop("strategy", None)
    return p


def all_plans(plan):
    plans = list(plan["alternatives"]) + [plan["hold"], plan["recommendation"]["plan"]]
    req = plan.get("requested")
    if req:
        plans += [x for x in (req.get("plan"), req.get("with_additional_transfers")) if x]
    return plans


class TestRequestParsing(unittest.TestCase):
    def errors(self, **kw):
        with self.assertRaises(ApiError) as cm:
            parse_request(json.loads(body(**kw)))
        return " | ".join(cm.exception.details)

    def parsed(self, **kw):
        return parse_request(json.loads(body(**kw)))

    def test_strategy_is_optional_and_defaults_to_none(self):
        self.assertIsNone(self.parsed()["strategy"])

    def test_accepted_values(self):
        self.assertEqual(self.parsed(strategy="sensible")["strategy"], "sensible")
        self.assertEqual(self.parsed(strategy="everything")["strategy"], "everything")
        self.assertEqual(self.parsed(strategy="sensible", max_hits=0)["max_hits"], 0)
        self.assertEqual(self.parsed(strategy="everything", max_hits=2)["max_hits"], 2)

    def test_upside_is_refused_with_the_reason(self):
        msg = self.errors(strategy="upside")
        self.assertIn('"upside" is not available', msg)
        self.assertIn("no ceiling or upside metric", msg)

    def test_unknown_and_malformed_values_are_refused(self):
        for bad in ("Sensible", "safe", "", None, 3, ["sensible"], {"a": 1}, True):
            self.assertIn("strategy must be one of: sensible, everything", self.errors(strategy=bad), bad)

    def test_sensible_conflicts_with_allowing_hits(self):
        self.assertIn('"sensible" means no points hits', self.errors(strategy="sensible", max_hits=1))
        self.assertIn('"sensible" means no points hits', self.errors(strategy="sensible", max_hits=3))

    def test_sensible_does_not_hide_other_validation_errors(self):
        msg = self.errors(strategy="sensible", max_hits=1, keep_ids=[121])
        self.assertIn("sensible", msg)
        self.assertIn("kept player 121", msg)


class TestSensibleMeansNoHits(unittest.TestCase):
    def test_every_plan_has_zero_hits_and_equal_gains(self):
        for free in (1, 2, 3):
            status, p = run(free_transfers=free, strategy="sensible")
            self.assertEqual(status, 200)
            plan = p["plan"]
            self.assertEqual(p["request"]["strategy"], "sensible")
            self.assertEqual(p["request"]["max_hits"], 0)
            self.assertTrue(plan["alternatives"], free)
            for x in all_plans(plan):
                self.assertEqual((x["hits"], x["hit_points"]), (0, 0))
                self.assertLessEqual(x["n_transfers"], free)
                self.assertAlmostEqual(x["projected_gain_before_hits"], x["projected_gain_vs_hold"], delta=0.011)

    def test_zero_free_transfers_leaves_only_hold(self):
        status, p = run(free_transfers=0, strategy="sensible")
        self.assertEqual(status, 200)
        self.assertEqual(p["plan"]["recommendation"]["action"], "HOLD")
        self.assertEqual(p["plan"]["alternatives"], [])

    def test_identical_to_the_default_no_hit_request(self):
        for free in (1, 2):
            _, a = run(free_transfers=free, strategy="sensible")
            _, b = run(free_transfers=free)
            self.assertEqual(comparable(a), comparable(b))

    def test_sensible_with_named_players_never_charges_a_hit(self):
        status, p = run(free_transfers=1, strategy="sensible", moves=[[12, 126], [11, 127]])
        q = p["plan"]["requested"]
        self.assertEqual(status, 200)
        self.assertEqual(q["status"], "INFEASIBLE")          # two moves, one free transfer, hits not allowed
        self.assertIn("hits are disabled", q["reason"])

    def test_sensible_respects_keep_players(self):
        _, base = run(free_transfers=2, strategy="sensible")
        sold = {t["out"]["id"] for a in base["plan"]["alternatives"] for t in a["transfers"]}
        self.assertTrue(sold)
        _, p = run(free_transfers=2, strategy="sensible", keep_ids=sorted(sold))
        now = {t["out"]["id"] for a in p["plan"]["alternatives"] for t in a["transfers"]}
        self.assertFalse(now & sold)


class TestEverythingIsUnchanged(unittest.TestCase):
    def test_everything_equals_omitting_the_field_with_and_without_hits(self):
        for kw in ({"free_transfers": 1}, {"free_transfers": 1, "max_hits": 2}, {"free_transfers": 2, "max_hits": 1}):
            _, a = run(strategy="everything", **kw)
            _, b = run(**kw)
            self.assertEqual(a["request"]["strategy"], "everything")
            self.assertEqual(comparable(a), comparable(b))

    def test_hit_permitting_plans_are_still_available_and_priced(self):
        _, p = run(free_transfers=0, max_hits=2, strategy="everything")
        alts = p["plan"]["alternatives"]
        self.assertTrue(any(a["hit_points"] > 0 for a in alts))
        for a in alts:
            self.assertAlmostEqual(a["projected_gain_before_hits"] - a["hit_points"],
                                   a["projected_gain_vs_hold"], delta=0.011)


class TestRankingAndMeasuresAreNotChanged(unittest.TestCase):
    def test_ranking_is_still_net_gain_after_hits_for_every_strategy(self):
        for kw in ({"strategy": "everything", "max_hits": 2}, {"max_hits": 2}, {"strategy": "sensible"}):
            _, p = run(free_transfers=1, **kw)
            nets = [a["projected_gain_vs_hold"] for a in p["plan"]["alternatives"]]
            self.assertEqual(nets, sorted(nets, reverse=True), kw)

    def test_three_measures_stay_separate(self):
        _, p = run(free_transfers=0, max_hits=2, strategy="everything")
        a = next(x for x in p["plan"]["alternatives"] if x["hit_points"] > 0)
        self.assertIn("projected_gain_before_hits", a)
        self.assertIn("projected_gain_vs_hold", a)
        self.assertEqual(len(a["outlook"]["vs_hold"]), 3)
        self.assertEqual(a["outlook"]["status"], "provisional_unvalidated")
        self.assertNotEqual(a["projected_gain_before_hits"], a["projected_gain_vs_hold"])
        self.assertNotIn("ceiling", json.dumps(p).lower())
        self.assertNotIn("upside", json.dumps(p["plan"]).lower())

    def test_no_strategy_introduces_a_new_ranking_objective_in_the_module(self):
        import inspect
        from fpl.engine import transfer_planner
        src = inspect.getsource(transfer_planner)
        for word in ("ceiling", "upside", "strategy"):
            self.assertNotIn(word, src.lower())


if __name__ == "__main__":
    unittest.main()
