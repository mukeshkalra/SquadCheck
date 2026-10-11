"""
Tests for fpl/engine/transfer_planner.py — synthetic data only (no network, no scanner).

Squad ids 1..15 (2 GKP / 5 DEF / 5 MID / 3 FWD), each on its own club unless overridden.
Candidates are ids 101+. Prices are tenths of £m. xpts = [GW+1, GW+2, GW+3].
"""

import itertools
import math
import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from fpl.engine import transfer_planner as tp
from fpl.engine import bench
from fpl.engine.bench import _enumerate_valid_xis, _is_valid_xi, optimise_bench
from fpl.engine.transfer_planner import (
    best_xi, plan_transfers, project_players, shortlist_candidates,
)

SQUAD = list(range(1, 16))
POS = {**{1: 1, 2: 1}, **{i: 2 for i in range(3, 8)}, **{i: 3 for i in range(8, 13)},
       **{i: 4 for i in range(13, 16)}}
BASE = {1: 5, 2: 3, 3: 5, 4: 4.5, 5: 4, 6: 3.5, 7: 2, 8: 8, 9: 7, 10: 6, 11: 5, 12: 1, 13: 7, 14: 6.5, 15: 3}
WIDE = dict(in_pool_per_position=None, out_pool_size=15, max_evals=10 ** 7)


def P(pid, pos, x, price=50, team=None, conf="HIGH", status="a", **kw):
    x = list(x) if isinstance(x, (list, tuple)) else [x, x, x]
    d = {"id": pid, "web_name": kw.get("name", "P%d" % pid), "position": pos,
         "team": team if team is not None else pid, "price": price, "xpts": x,
         "confidence": conf, "status": status, "risks": kw.get("risks", [])}
    if "caveat" in kw:
        d["outlook_caveat"] = kw["caveat"]
    return d


def world(extra=(), overrides=None):
    pl = {i: P(i, POS[i], BASE[i]) for i in SQUAD}
    for i, o in (overrides or {}).items():
        pl[i] = {**pl[i], **o}
    for e in extra:
        pl[e["id"]] = e
    return pl


def plan(pl, **kw):
    kw.setdefault("bank", 0)
    kw.setdefault("free_transfers", 1)
    return plan_transfers(SQUAD, pl, **kw)


def pairs(p):
    return [(t["out"]["id"], t["in"]["id"]) for t in p["transfers"]]


# ─────────────────────────────────────────────────────────────────────────────
# LEGAL-XI VALUATION AND DRIFT GUARDS
# ─────────────────────────────────────────────────────────────────────────────

class TestBestXi(unittest.TestCase):
    def test_matches_bench_enumeration_including_ties_zeros_negatives(self):
        rnd = random.Random(5)
        formations = set()
        for trial in range(1500):
            mode = trial % 3
            if mode == 0:
                sc = {i: rnd.choice([0, 0, 1, 1, 2]) for i in SQUAD}          # many ties/zeros
            elif mode == 1:
                sc = {i: round(rnd.uniform(-2, 10), 1) for i in SQUAD}          # negatives
            else:
                sc = {i: round(rnd.uniform(0, 10), 1) for i in SQUAD}
            by_id = {i: {"position": POS[i], "xPts": sc[i]} for i in SQUAD}
            brute = max(sum(sc[p] for p in xi) for xi in _enumerate_valid_xis(SQUAD, by_id))
            total, xi = best_xi(SQUAD, sc, POS)
            self.assertAlmostEqual(total, brute, places=9)
            self.assertTrue(_is_valid_xi(xi, by_id))
            self.assertAlmostEqual(sum(sc[p] for p in xi), total, places=9)
            formations.add(tuple(sum(POS[p] == q for p in xi) for q in (2, 3, 4)))
        self.assertEqual(formations, set(bench.LEGAL_FORMATIONS))                 # every legal formation optimal at least once

    def test_formations_equal_those_bench_can_produce(self):
        by_id = {i: {"position": POS[i], "xPts": 1.0} for i in SQUAD}
        bench_forms = {tuple(sum(POS[p] == q for p in xi) for q in (2, 3, 4))
                       for xi in _enumerate_valid_xis(SQUAD, by_id)}
        self.assertEqual(bench_forms, set(bench.LEGAL_FORMATIONS))

    def test_planner_reuses_the_public_bench_valuation_not_a_copy(self):
        import inspect
        self.assertIs(tp.best_xi, bench.best_legal_xi)
        src = inspect.getsource(tp)
        for private in ("_POS_MIN", "_POS_MAX", "_XI_SIZE", "_enumerate_valid_xis", "from fpl.engine.pipeline"):
            self.assertNotIn(private, src)

    def test_squad_composition_pinned_to_pipeline(self):
        from fpl.engine.pipeline import _SQUAD_POS
        self.assertEqual(tp.SQUAD_COMPOSITION, _SQUAD_POS)

    def test_ignores_input_order(self):
        self.assertEqual(best_xi(SQUAD, dict(BASE), POS), best_xi(list(reversed(SQUAD)), dict(BASE), POS))

    def test_hold_value_equals_optimise_bench_on_same_projections(self):
        rnd = random.Random(9)
        for _ in range(20):
            xp = {i: round(rnd.uniform(0, 10), 1) for i in SQUAD}
            pl = {i: P(i, POS[i], xp[i]) for i in SQUAD}
            projections = [{"player_id": i, "position": POS[i], "xPts": xp[i],
                            "confidence": "HIGH", "web_name": "P%d" % i} for i in SQUAD]
            xi442 = [1, 3, 4, 5, 6, 8, 9, 10, 11, 13, 14]
            bench = optimise_bench(projections, xi442)
            r = plan(pl, free_transfers=0)
            self.assertAlmostEqual(r["hold"]["score"], bench["recommended_xi"]["xPts"], places=2)


# ─────────────────────────────────────────────────────────────────────────────
# HOLD AND TRANSFER COUNTS
# ─────────────────────────────────────────────────────────────────────────────

class TestHold(unittest.TestCase):
    def test_zero_free_transfers_is_hold(self):
        r = plan(world([P(101, 3, 12)]), free_transfers=0, bank=100)
        self.assertEqual(r["recommendation"]["action"], "HOLD")
        self.assertEqual(r["alternatives"], [])

    def test_hold_when_every_feasible_move_is_negative(self):
        # every squad player projects 6; every candidate projects 1
        pl = world([P(100 + i, 1 + i % 4, 1.0, price=40) for i in range(8)],
                   overrides={i: {"xpts": [6, 6, 6]} for i in SQUAD})
        r = plan(pl, free_transfers=3, bank=100)
        self.assertEqual(r["recommendation"]["action"], "HOLD")
        self.assertTrue(all(a["projected_gain_vs_hold"] <= 0 for a in r["alternatives"]))
        everything = plan(pl, free_transfers=3, bank=100, top_n=100000)["alternatives"]
        self.assertTrue(any(a["projected_gain_vs_hold"] < 0 for a in everything))

    def test_no_upgrade_holds_and_does_not_manufacture_transfers(self):
        r = plan(world([P(101, 3, 1.0)]), free_transfers=5, bank=100)
        self.assertEqual(r["recommendation"]["action"], "HOLD")
        self.assertEqual(r["hold"]["n_transfers"], 0)

    def test_gain_below_threshold_holds(self):
        r = plan(world([P(101, 1, 5.3)]), bank=0, min_gain=0.5)      # GKP1 5.0 -> +0.3
        self.assertEqual(r["recommendation"]["action"], "HOLD")
        self.assertIn("below", r["recommendation"]["reason"])


class TestFreeTransferCounts(unittest.TestCase):
    def setUp(self):
        self.pl = world([P(101, 2, 7), P(102, 3, 9)])

    def test_one_free(self):
        self.assertEqual(plan(self.pl, free_transfers=1)["recommendation"]["plan"]["n_transfers"], 1)

    def test_two_free(self):
        self.assertEqual(plan(self.pl, free_transfers=2)["recommendation"]["plan"]["n_transfers"], 2)

    def test_never_exceeds_free(self):
        self.assertTrue(all(a["n_transfers"] <= 1 for a in plan(self.pl, free_transfers=1)["alternatives"]))

    def test_extra_transfer_only_if_it_helps(self):
        r = plan(world([P(101, 2, 7)]), free_transfers=3)
        self.assertEqual(r["recommendation"]["plan"]["n_transfers"], 1)

    def test_remaining_free_transfers_exposed(self):
        r = plan(world([P(101, 2, 7)]), free_transfers=3)
        p = r["recommendation"]["plan"]
        self.assertEqual((p["free_transfers_used"], p["free_transfers_remaining"]), (1, 2))
        self.assertEqual(r["hold"]["free_transfers_remaining"], 3)

    def test_remaining_zero_and_hits(self):
        r = plan(world([P(101, 1, 14)]), free_transfers=0, max_hits=1)
        p = r["recommendation"]["plan"]
        self.assertEqual((p["free_transfers_remaining"], p["hits"]), (0, 1))


# ─────────────────────────────────────────────────────────────────────────────
# NAMED MOVES: LITERAL REQUEST vs OVERALL RECOMMENDATION
# ─────────────────────────────────────────────────────────────────────────────

class TestLiteralMoves(unittest.TestCase):
    def setUp(self):
        self.pl = world([P(101, 3, 10), P(102, 3, 6), P(103, 2, 11)])

    def test_literal_move_has_no_unrequested_transfers_with_spare_free_transfers(self):
        r = plan(self.pl, moves=[(11, 102)], free_transfers=2)
        q = r["requested"]
        self.assertEqual(pairs(q["plan"]), [(11, 102)])
        self.assertEqual(q["plan"]["n_transfers"], 1)
        self.assertEqual(q["status"], "RECOMMENDED")

    def test_additional_transfers_variant_is_separate_and_labelled(self):
        r = plan(self.pl, moves=[(11, 102)], free_transfers=2)
        ext = r["requested"]["with_additional_transfers"]
        self.assertIsNotNone(ext)
        self.assertEqual(ext["n_transfers"], 2)
        self.assertIn((11, 102), pairs(ext))
        self.assertIn("did not request", ext["label"])
        self.assertGreater(ext["projected_gain_vs_hold"], r["requested"]["plan"]["projected_gain_vs_hold"])

    def test_overall_recommendation_independent_of_the_request(self):
        r = plan(self.pl, moves=[(11, 102)], free_transfers=2)
        rec = r["recommendation"]["plan"]
        self.assertNotEqual(pairs(rec), [(11, 102)])
        self.assertFalse(r["requested"]["matches_overall_recommendation"])
        self.assertGreaterEqual(rec["projected_gain_vs_hold"], r["requested"]["plan"]["projected_gain_vs_hold"])

    def test_matches_overall_when_it_is_the_best_plan(self):
        r = plan(world([P(101, 1, 9)]), moves=[(1, 101)])
        self.assertTrue(r["requested"]["matches_overall_recommendation"])

    def test_pairing_is_preserved_not_substituted(self):
        # 101 is a better MID than 102, but the user asked for 102: never swapped silently.
        pl = world([P(101, 3, 10), P(102, 3, 6)])
        r = plan(pl, moves=[(11, 102)], free_transfers=1)
        self.assertEqual(pairs(r["requested"]["plan"]), [(11, 102)])
        self.assertEqual(pairs(r["recommendation"]["plan"]), [(12, 101)])

    def test_two_moves_keep_both_pairings(self):
        pl = world([P(101, 3, 10), P(102, 2, 11)])
        r = plan(pl, moves=[(11, 101), (6, 102)], free_transfers=2)
        self.assertEqual(sorted(pairs(r["requested"]["plan"])), [(6, 102), (11, 101)])

    def test_different_positions_infeasible_with_specific_reason_and_no_repair(self):
        pl = world([P(103, 4, 12), P(102, 3, 9)])
        r = plan(pl, moves=[(8, 103)], free_transfers=2)
        q = r["requested"]
        self.assertEqual(q["status"], "INFEASIBLE")
        self.assertIsNone(q["plan"])
        self.assertIn("positions differ", q["reason"])
        self.assertIsNotNone(q["with_additional_transfers"])               # offered separately, labelled
        self.assertIn("did not request", q["with_additional_transfers"]["label"])

    def test_unaffordable_move_reports_numbers_and_funding_alternative(self):
        pl = world([P(101, 3, 12, price=80), P(102, 3, 1, price=10)])
        r = plan(pl, moves=[(8, 101)], free_transfers=2, bank=0)
        q = r["requested"]
        self.assertEqual(q["status"], "INFEASIBLE")
        self.assertIn("short by £3.0m", q["reason"])
        ext = q["with_additional_transfers"]
        self.assertEqual(ext["n_transfers"], 2)
        self.assertIn((8, 101), pairs(ext))

    def test_club_limit_reason(self):
        teams = {i: {"team": 77} for i in (3, 4, 5)}
        pl = world([P(101, 2, 12, team=77)], overrides=teams)
        r = plan(pl, moves=[(6, 101)], bank=0)
        self.assertEqual(r["requested"]["status"], "INFEASIBLE")
        self.assertIn("one club", r["requested"]["reason"])

    def test_more_moves_than_free_transfers(self):
        pl = world([P(101, 3, 12), P(102, 3, 11)])
        r = plan(pl, moves=[(8, 101), (9, 102)], free_transfers=1)
        self.assertEqual(r["requested"]["status"], "INFEASIBLE")
        self.assertIn("needs 2", r["requested"]["reason"])
        self.assertIn("hits are disabled", r["requested"]["reason"])
        self.assertTrue(all(a["n_transfers"] <= 1 for a in r["alternatives"]))

    def test_hits_enabled_makes_the_extra_move_feasible_and_priced(self):
        pl = world([P(101, 3, 14), P(102, 3, 13)])
        r = plan(pl, moves=[(11, 101), (12, 102)], free_transfers=1, max_hits=1)
        p = r["requested"]["plan"]
        self.assertEqual((p["hits"], p["hit_points"]), (1, 4))

    def test_zero_free_transfers_hits_off_cannot_buy_a_transfer(self):
        r = plan(world([P(101, 3, 12)]), moves=[(8, 101)], free_transfers=0, bank=100)
        self.assertEqual(r["requested"]["status"], "INFEASIBLE")
        self.assertEqual(r["recommendation"]["action"], "HOLD")
        self.assertEqual(r["alternatives"], [])

    def test_worse_than_hold(self):
        r = plan(world([P(101, 3, 1)]), moves=[(8, 101)])
        self.assertEqual(r["requested"]["status"], "WORSE_THAN_HOLD")
        self.assertEqual(r["recommendation"]["action"], "HOLD")

    def test_below_threshold(self):
        r = plan(world([P(101, 1, 5.3)]), moves=[(1, 101)])
        self.assertEqual(r["requested"]["status"], "BELOW_THRESHOLD")

    def test_bank_arithmetic_across_two_moves(self):
        pl = world([P(101, 3, 12, price=40), P(102, 4, 11, price=45)])
        r = plan(pl, moves=[(8, 101), (13, 102)], free_transfers=2, bank=7)
        self.assertEqual(r["requested"]["plan"]["bank_after"], 7 + (50 - 40) + (50 - 45))

    def test_invalid_intent_combinations_rejected(self):
        pl = world([P(101, 3, 12)])
        with self.assertRaises(ValueError):
            plan(pl, moves=[(8, 101)], out_ids=[9])
        with self.assertRaises(ValueError):
            plan(pl, out_ids=[8], in_ids=[101])
        with self.assertRaises(ValueError):
            plan(pl, moves=[(8,)])
        with self.assertRaises(ValueError):
            plan(pl, moves=[(8, 101), (9, 101)])        # duplicate incoming
        with self.assertRaises(ValueError):
            plan(pl, moves=[(99, 101)])                 # outgoing not in squad
        with self.assertRaises(ValueError):
            plan(pl, moves=[(8, 9)])                    # incoming already in squad


class TestOutgoingOnly(unittest.TestCase):
    def test_outgoing_preserved_and_replacement_optimised_without_extras(self):
        pl = world([P(101, 3, 10), P(102, 3, 9), P(103, 4, 9)])
        r = plan(pl, out_ids=[11], free_transfers=3)
        q = r["requested"]
        self.assertEqual(pairs(q["plan"]), [(11, 101)])
        self.assertEqual(q["plan"]["n_transfers"], 1)
        self.assertEqual(q["intent"], "out_only")

    def test_two_outgoing_get_two_position_matched_replacements(self):
        pl = world([P(101, 3, 10), P(103, 2, 8)])
        r = plan(pl, out_ids=[11, 6], free_transfers=3)
        p = r["requested"]["plan"]
        self.assertEqual(p["n_transfers"], 2)
        self.assertEqual(sorted(i for _, i in pairs(p)), [101, 103])

    def test_unaffordable_replacements_report_closest_miss(self):
        pl = world([P(101, 3, 12, price=200)])
        r = plan(pl, out_ids=[8], bank=0)
        q = r["requested"]
        self.assertEqual(q["status"], "INFEASIBLE")
        self.assertIn("exceed the estimated budget", q["reason"])
        self.assertIn("short by", q["reason"])

    def test_more_outgoing_than_free(self):
        r = plan(world([P(101, 3, 10)]), out_ids=[11, 12], free_transfers=1)
        self.assertEqual(r["requested"]["status"], "INFEASIBLE")
        self.assertIn("needs 2", r["requested"]["reason"])

    def test_no_candidate_of_required_position(self):
        r = plan(world([P(101, 3, 10)]), out_ids=[1])
        self.assertEqual(r["requested"]["status"], "INFEASIBLE")
        self.assertIn("no candidate", r["requested"]["reason"])


class TestIncomingOnly(unittest.TestCase):
    def test_target_preserved_one_transfer_best_exit_chosen(self):
        r = plan(world([P(101, 3, 10)]), in_ids=[101], free_transfers=3)
        q = r["requested"]
        self.assertEqual(q["plan"]["n_transfers"], 1)
        (out, inn), = pairs(q["plan"])
        self.assertEqual(inn, 101)
        self.assertEqual(POS[out], 3)
        self.assertEqual(out, 12)                      # cheapest loss: the 1.0 bench MID

    def test_unaffordable_with_one_transfer_is_not_silently_repaired(self):
        pl = world([P(101, 3, 12, price=80), P(102, 3, 1, price=10)])
        r = plan(pl, in_ids=[101], free_transfers=2, bank=0)
        q = r["requested"]
        self.assertEqual(q["status"], "INFEASIBLE")
        self.assertIsNone(q["plan"])
        self.assertIn("exceed the estimated budget", q["reason"])
        self.assertEqual(q["with_additional_transfers"]["n_transfers"], 2)

    def test_club_blocked_without_a_second_transfer(self):
        teams = {1: {"team": 77}, 3: {"team": 77}, 8: {"team": 77}}      # club 77: GKP, DEF, MID
        pl = world([P(101, 4, 12, team=77), P(102, 2, 5, team=99)], overrides=teams)
        r = plan(pl, in_ids=[101], free_transfers=2, bank=0)
        q = r["requested"]
        self.assertEqual(q["status"], "INFEASIBLE")
        self.assertIn("per-club", q["reason"])
        self.assertEqual(q["with_additional_transfers"]["n_transfers"], 2)

    def test_named_target_enters_pool_even_if_unattractive(self):
        extras = [P(200 + i, 3, 6 + i * 0.1) for i in range(30)]
        r = plan(world(extras + [P(999, 3, 0.5)]), in_ids=[999])
        self.assertEqual(r["requested"]["plan"]["transfers"][0]["in"]["id"], 999)

    def test_named_target_is_also_considered_by_the_overall_search(self):
        r = plan(world([P(101, 3, 14)]), in_ids=[101])
        self.assertEqual(r["recommendation"]["plan"]["transfers"][0]["in"]["id"], 101)


class TestOverallNeverWorseThanRequest(unittest.TestCase):
    def test_property_over_random_worlds(self):
        checked = 0
        for seed in range(25):
            rnd = random.Random(seed)
            extras = [P(100 + i, 1 + i % 4, round(rnd.uniform(0, 11), 1), price=rnd.randint(35, 100),
                        team=20 + rnd.randint(0, 9)) for i in range(30)]
            pl = world(extras, overrides={i: {"price": rnd.randint(40, 90)} for i in SQUAD})
            out = rnd.choice(SQUAD)
            inn = rnd.choice([e["id"] for e in extras if e["position"] == POS[out]])
            r = plan(pl, moves=[(out, inn)], free_transfers=2, bank=rnd.randint(0, 20))
            q = r["requested"]
            if q["plan"] is None:
                continue
            checked += 1
            self.assertEqual(pairs(q["plan"]), [(out, inn)])
            best = max([0.0] + [a["projected_gain_vs_hold"] for a in r["alternatives"]])
            self.assertGreaterEqual(best, q["plan"]["projected_gain_vs_hold"] - 1e-9)
        self.assertGreater(checked, 5)


# ─────────────────────────────────────────────────────────────────────────────
# BUDGET, RULES, COMBINATIONS
# ─────────────────────────────────────────────────────────────────────────────

class TestBudgetAndPrices(unittest.TestCase):
    def test_globally_attractive_plan_unaffordable_is_not_recommended(self):
        r = plan(world([P(101, 3, 20, price=150)]), bank=10, free_transfers=2)
        self.assertEqual(r["recommendation"]["action"], "HOLD")
        self.assertTrue(all(101 not in [i for _, i in pairs(a)] for a in r["alternatives"]))

    def test_bank_makes_swap_affordable(self):
        r = plan(world([P(101, 3, 12, price=120)]), bank=70, moves=[(8, 101)])
        self.assertEqual(r["requested"]["plan"]["bank_after"], 0)

    def test_outgoing_price_funds_incoming(self):
        pl = world([P(101, 3, 12, price=90)], overrides={8: {"price": 90}})
        self.assertEqual(plan(pl, bank=0, moves=[(8, 101)])["requested"]["status"], "RECOMMENDED")

    def test_remaining_bank_and_price_estimate_flagged(self):
        p = plan(world([P(101, 3, 12, price=40)]), bank=5)["recommendation"]["plan"]
        self.assertEqual(p["bank_after"], 5 + 50 - 40)
        self.assertEqual(p["price_source"], "estimated")
        self.assertTrue(any("estimated" in x for x in p["risks"]))

    def test_multi_transfer_budget_is_joint(self):
        pl = world([P(101, 3, 12, price=60), P(102, 4, 12, price=60)])
        p = plan(pl, free_transfers=2, bank=10)["recommendation"]["plan"]
        self.assertEqual(p["n_transfers"], 1)
        self.assertGreaterEqual(p["bank_after"], 0)


class TestSquadRules(unittest.TestCase):
    def test_position_must_match(self):
        r = plan(world([P(101, 4, 15)]), moves=[(8, 101)])
        self.assertEqual(r["requested"]["status"], "INFEASIBLE")

    def test_club_limit_enforced_in_open_search(self):
        teams = {3: {"team": 77, "xpts": [12, 12, 12]}, 4: {"team": 77, "xpts": [11, 11, 11]}}
        pl = world([P(101, 2, 11, team=77), P(102, 2, 10.5, team=77), P(103, 2, 8, team=99)], overrides=teams)
        ins = {i for _, i in pairs(plan(pl, free_transfers=2)["recommendation"]["plan"])}
        self.assertFalse({101, 102} <= ins)

    def test_club_limit_allows_swap_within_club(self):
        teams = {i: {"team": 77} for i in (3, 4, 5)}
        pl = world([P(101, 2, 12, team=77)], overrides=teams)
        self.assertEqual(plan(pl, moves=[(3, 101)])["requested"]["status"], "RECOMMENDED")

    def test_duplicate_incoming_rejected(self):
        with self.assertRaises(ValueError):
            plan(world(), in_ids=[8])

    def test_squad_with_four_from_one_club_rejected(self):
        with self.assertRaises(ValueError):
            plan(world(overrides={i: {"team": 50} for i in (3, 4, 5, 6)}))

    def test_bad_squad_composition_rejected(self):
        with self.assertRaises(ValueError):
            plan(world(overrides={12: {"position": 4}}))

    def test_squad_wrong_size_or_unknown_id(self):
        with self.assertRaises(ValueError):
            plan_transfers(SQUAD[:14], world(), 0, 1)
        with self.assertRaises(ValueError):
            plan_transfers(SQUAD[:-1] + [999], world(), 0, 1)

    def test_result_squads_always_legal(self):
        rnd = random.Random(3)
        extras = [P(100 + i, rnd.choice([1, 2, 3, 4]), rnd.uniform(1, 11), price=rnd.randint(40, 90),
                    team=rnd.randint(1, 6)) for i in range(40)]
        pl = world(extras)
        for i, t in zip(SQUAD, itertools.cycle(range(1, 6))):
            pl[i]["team"] = t
        r = plan(pl, free_transfers=3, bank=30)
        for a in r["alternatives"]:
            squad = set(SQUAD)
            for o, i in pairs(a):
                squad.remove(o)
                squad.add(i)
            self.assertEqual(len(squad), 15)
            counts, clubs = {}, {}
            for p in squad:
                counts[pl[p]["position"]] = counts.get(pl[p]["position"], 0) + 1
                clubs[pl[p]["team"]] = clubs.get(pl[p]["team"], 0) + 1
            self.assertEqual(counts, {1: 2, 2: 5, 3: 5, 4: 3})
            self.assertLessEqual(max(clubs.values()), 3)
            self.assertGreaterEqual(a["bank_after"], 0)


def _brute_best(pl, bank, free):
    """Independent oracle: best net projected score over every legal 0..free transfer plan."""
    score = {p: pl[p]["xpts"][0] for p in pl}
    pos = {p: pl[p]["position"] for p in pl}
    cands = [p for p in pl if p not in SQUAD]
    best = best_xi(SQUAD, score, pos)[0]
    for k in range(1, free + 1):
        for outs in combinations_(SQUAD, k):
            for ins in combinations_(cands, k):
                if sorted(pos[o] for o in outs) != sorted(pos[i] for i in ins):
                    continue
                if sum(pl[i]["price"] for i in ins) - sum(pl[o]["price"] for o in outs) > bank:
                    continue
                squad = [p for p in SQUAD if p not in outs] + list(ins)
                clubs = {}
                for p in squad:
                    clubs[pl[p]["team"]] = clubs.get(pl[p]["team"], 0) + 1
                if max(clubs.values()) > 3:
                    continue
                best = max(best, best_xi(squad, score, pos)[0])
    return best


combinations_ = itertools.combinations


def _greedy_best(pl, bank, free):
    """Best single swap, then best second swap given the first (the 'independent ranking' strategy)."""
    score = {p: pl[p]["xpts"][0] for p in pl}
    pos = {p: pl[p]["position"] for p in pl}
    squad, cur = list(SQUAD), best_xi(SQUAD, score, pos)[0]
    cands = [p for p in pl if p not in SQUAD]
    for _ in range(free):
        move = None
        for o in squad:
            for i in cands:
                if i in squad or pos[i] != pos[o]:
                    continue
                if pl[i]["price"] - pl[o]["price"] > bank:
                    continue
                new = [p for p in squad if p != o] + [i]
                clubs = {}
                for p in new:
                    clubs[pl[p]["team"]] = clubs.get(pl[p]["team"], 0) + 1
                if max(clubs.values()) > 3:
                    continue
                v = best_xi(new, score, pos)[0]
                if v > cur + 1e-9 and (move is None or v > move[0]):
                    move = (v, o, i, new)
        if move is None:
            break
        cur, bank, squad = move[0], bank + pl[move[1]]["price"] - pl[move[2]]["price"], move[3]
    return cur


class TestCombinations(unittest.TestCase):
    def _world(self, seed):
        rnd = random.Random(seed)
        extras = [P(100 + i, 1 + i % 4, round(rnd.uniform(1, 12), 1), price=rnd.randint(40, 110),
                    team=rnd.randint(1, 5)) for i in range(10)]
        pl = world(extras, overrides={i: {"price": rnd.randint(40, 100), "team": 1 + (i % 5)} for i in SQUAD})
        return pl, rnd.randint(0, 15)

    def test_planner_matches_independent_brute_force(self):
        for seed in range(25):
            pl, bank = self._world(seed)
            r = plan(pl, free_transfers=2, bank=bank, **WIDE)
            planner = r["hold"]["score"] + max(0.0, max([a["projected_gain_vs_hold"] for a in r["alternatives"]] or [0]))
            self.assertAlmostEqual(planner, _brute_best(pl, bank, 2), delta=0.011, msg="seed %d" % seed)

    def test_joint_search_beats_greedy_ranking_on_some_worlds(self):
        wins = 0
        for seed in range(60):
            pl, bank = self._world(seed)
            if _brute_best(pl, bank, 2) > _greedy_best(pl, bank, 2) + 0.3:
                wins += 1
                r = plan(pl, free_transfers=2, bank=bank, **WIDE)
                planner = r["hold"]["score"] + max(a["projected_gain_vs_hold"] for a in r["alternatives"])
                self.assertGreater(planner, _greedy_best(pl, bank, 2) + 0.25)
        self.assertGreaterEqual(wins, 1)

    def test_individually_attractive_transfers_conflict_on_budget(self):
        # Either upgrade alone fits the bank; both together do not.
        pl = world([P(101, 3, 12, price=60), P(102, 4, 12, price=60)])
        p = plan(pl, free_transfers=2, bank=10)["recommendation"]["plan"]
        self.assertEqual(p["n_transfers"], 1)


# ─────────────────────────────────────────────────────────────────────────────
# HORIZON, HITS, UNCERTAINTY, LABELLING
# ─────────────────────────────────────────────────────────────────────────────

class TestHorizon(unittest.TestCase):
    def setUp(self):
        self.pl = world([P(101, 1, [10, 1, 1])])

    def test_default_prioritises_next_gw_and_reports_outlook_as_provisional(self):
        p = plan(self.pl, moves=[(1, 101)])["requested"]["plan"]
        self.assertEqual(len(p["outlook"]["best_xi_xpts"]), 3)
        self.assertLess(p["outlook"]["vs_hold"][1], 0)
        self.assertEqual(p["outlook"]["status"], "provisional_unvalidated")
        self.assertIn("backtested", p["outlook"]["note"])
        self.assertTrue(any("GW+2" in x for x in p["risks"]))

    def test_weights_flip_the_decision(self):
        self.assertEqual(plan(self.pl, weights=(1, 1, 1))["recommendation"]["action"], "HOLD")
        self.assertEqual(plan(self.pl, weights=(0, 1, 0))["recommendation"]["action"], "HOLD")
        self.assertEqual(plan(self.pl)["recommendation"]["action"], "TRANSFER")

    def test_weights_are_normalised(self):
        self.assertEqual(plan(self.pl, weights=(2, 0, 0))["inputs"]["weights"], [1.0, 0.0, 0.0])


class TestHits(unittest.TestCase):
    def test_hits_off_by_default(self):
        self.assertEqual(plan(world([P(101, 1, 14)]), free_transfers=0)["recommendation"]["action"], "HOLD")

    def test_optional_hit_is_charged(self):
        p = plan(world([P(101, 1, 14)]), free_transfers=0, max_hits=1)["recommendation"]["plan"]
        self.assertEqual((p["hits"], p["hit_points"]), (1, 4))
        self.assertAlmostEqual(p["net_score"], p["score"] - 4, places=2)

    def test_hit_not_taken_when_not_worth_it(self):
        self.assertEqual(plan(world([P(101, 1, 8)]), free_transfers=0, max_hits=1)
                         ["recommendation"]["action"], "HOLD")

    def test_hit_charged_in_points_regardless_of_weights_sum(self):
        r1 = plan(world([P(101, 1, 14)]), free_transfers=0, max_hits=1, weights=(1, 0, 0))
        r2 = plan(world([P(101, 1, 14)]), free_transfers=0, max_hits=1, weights=(5, 0, 0))
        self.assertEqual(r1["recommendation"]["plan"]["net_score"], r2["recommendation"]["plan"]["net_score"])


class TestUncertaintyAndLabelling(unittest.TestCase):
    def test_low_confidence_status_and_caveats_surface(self):
        pl = world([P(101, 3, 12, conf="LOW", status="d", risks=["Rotation risk"], caveat="extrapolated")])
        p = plan(pl)["recommendation"]["plan"]
        self.assertEqual(p["confidence"], "LOW")
        text = " ".join(p["risks"])
        for needle in ("LOW projection confidence", "availability", "Rotation risk", "extrapolated"):
            self.assertIn(needle, text)

    def test_gains_are_labelled_as_projected(self):
        r = plan(world([P(101, 1, 14)]))
        self.assertFalse(r["basis"]["guaranteed"])
        self.assertIn("projected", r["basis"]["kind"])
        self.assertIn("not a guarantee", r["recommendation"]["reason"])
        p = r["recommendation"]["plan"]
        self.assertTrue(p["projected"])
        self.assertIn("projected_gain_vs_hold", p)
        self.assertNotIn("gain_vs_hold", p)
        self.assertIn("projected_gain", r["recommendation"])

    def test_missing_projection_is_not_a_blank_gameweek(self):
        unknown = {**P(101, 3, 0), "xpts": [None, None, None], "confidence": "LOW"}
        r = plan(world([unknown]), moves=[(8, 101)])
        q = r["requested"]
        self.assertEqual(q["status"], "INCOMPLETE_DATA")                 # not WORSE_THAN_HOLD / BELOW_THRESHOLD
        self.assertIsNone(q["plan"])
        self.assertIn("not a blank gameweek", q["reason"])
        self.assertIn("No estimate was made", q["reason"])
        self.assertEqual(r["recommendation"]["action"], "HOLD")
        # a genuine blank gameweek (0.0) is a real projection and is still evaluated
        blank = P(102, 3, [0, 12, 12])
        q2 = plan(world([blank]), moves=[(8, 102)], weights=(0, 1, 0))["requested"]
        self.assertNotEqual(q2["status"], "INCOMPLETE_DATA")
        self.assertIn("projects 0 next GW", " ".join(q2["plan"]["risks"]))

    def test_missing_projection_on_squad_player_warns(self):
        r = plan(world(overrides={8: {"xpts": [None, 7, 7]}}))
        self.assertTrue(any("P8" in w and "not a blank gameweek" in w for w in r["warnings"]))

    def test_deterministic_including_reordered_inputs(self):
        pl = world([P(101, 3, 10), P(102, 3, 10), P(103, 2, 9), P(104, 2, 9)])
        base = plan(pl, free_transfers=2, moves=[(11, 101)])
        sq = SQUAD[:]
        random.Random(1).shuffle(sq)
        items = list(pl.items())
        random.Random(2).shuffle(items)
        alt = plan_transfers(sq, dict(items), bank=0, free_transfers=2, moves=[(11, 101)])
        self.assertEqual(base, alt)


# ─────────────────────────────────────────────────────────────────────────────
# DEGRADED DATA SAFETY (players whose projection is unavailable)
# ─────────────────────────────────────────────────────────────────────────────

NONE3 = [None, None, None]


class TestDegradedDataSafety(unittest.TestCase):
    def test_squad_player_without_projection_is_never_sold_by_a_ranked_plan(self):
        # Previously: selling MID1 (id 8, valued at 0) looked like +3.0 and was recommended at HIGH confidence.
        pl = world([P(101, 3, 6.0)], overrides={8: {"xpts": NONE3}})
        r = plan(pl, free_transfers=1, top_n=1000)
        sold = {o for a in r["alternatives"] for o, _ in pairs(a)}
        self.assertNotIn(8, sold)
        self.assertNotIn(8, {o for o, _ in pairs(r["recommendation"]["plan"])})

    def test_incomplete_squad_marks_every_plan_and_forces_low_confidence(self):
        pl = world([P(101, 3, 14.0)], overrides={8: {"xpts": NONE3}})
        r = plan(pl, free_transfers=1)
        for p in [r["hold"], r["recommendation"]["plan"]] + r["alternatives"]:
            self.assertFalse(p["data_complete"])
            self.assertEqual(p["confidence"], "LOW")
        self.assertIn("partial data", r["recommendation"]["reason"])
        dq = r["data_quality"]
        self.assertFalse(dq["complete"])
        self.assertEqual(dq["squad_projection_missing"], [8])
        self.assertEqual(dq["hold_valuation"], "understated")
        self.assertEqual(r["search"]["coverage"]["squad_players_missing_projection"], [8])
        self.assertEqual(r["search"]["optimality"], "not_guaranteed")

    def test_complete_data_is_reported_as_complete(self):
        r = plan(world([P(101, 3, 14.0)]))
        self.assertTrue(r["data_quality"]["complete"])
        self.assertTrue(r["hold"]["data_complete"])
        self.assertEqual(r["data_quality"]["hold_valuation"], "complete")
        self.assertIsNone(r["recommendation"]["plan"]["gain_range"])

    def test_gain_that_exists_only_because_a_player_is_valued_at_zero_is_not_recommended(self):
        # GKP1 (id 1) has no projection. Its true value could be anything; at 0 the hold GKP is the 3.0 backup,
        # so a 4.0 replacement for the backup looks like +1.0. If GKP1 scores like the best GKP (4.0) it is +0.0.
        pl = world([P(101, 1, 4.0)], overrides={1: {"xpts": NONE3}})
        r = plan(pl, free_transfers=1, min_gain=0.5)
        self.assertEqual(r["recommendation"]["action"], "HOLD")
        self.assertIn("not reliable", r["recommendation"]["reason"])
        alt = r["alternatives"][0]
        self.assertEqual(alt["gain_range"]["missing_projections_valued_at_zero"], 1.0)
        self.assertEqual(alt["gain_range"]["missing_projections_valued_at_position_best"], 0.0)

    def test_robust_gain_is_still_recommended_with_its_range(self):
        pl = world([P(101, 3, 14.0)], overrides={1: {"xpts": NONE3}})
        r = plan(pl, free_transfers=1)
        rec = r["recommendation"]
        self.assertEqual(rec["action"], "TRANSFER")
        self.assertGreaterEqual(rec["plan"]["gain_range"]["missing_projections_valued_at_position_best"], 0.5)

    def test_candidate_without_projection_is_excluded_and_listed_not_zeroed(self):
        pl = world([{**P(101, 3, 0), "xpts": NONE3}, P(102, 3, 9.0)])
        r = plan(pl, free_transfers=1, top_n=1000)
        self.assertEqual(r["data_quality"]["candidates_projection_missing"], [101])
        self.assertEqual(r["search"]["coverage"]["excluded_for_missing_projection"], [101])
        self.assertTrue(all(101 not in [i for _, i in pairs(a)] for a in r["alternatives"]))
        self.assertTrue(r["hold"]["data_complete"])                    # squad is complete; only a candidate is missing

    def test_named_moves_with_missing_data_are_incomplete_not_evaluated(self):
        pl = world([{**P(101, 3, 0), "xpts": NONE3}], overrides={12: {"xpts": NONE3}})
        for kw in ({"moves": [(8, 101)]}, {"in_ids": [101]}, {"moves": [(12, 101)]}, {"out_ids": [12]}):
            with self.subTest(kw):
                q = plan(pl, **kw)["requested"]
                self.assertEqual(q["status"], "INCOMPLETE_DATA")
                self.assertIsNone(q["plan"])
                self.assertIsNone(q["with_additional_transfers"])

    def test_zero_is_not_confused_with_missing(self):
        pl = world([P(101, 3, [0.0, 0.0, 0.0])])
        r = plan(pl, moves=[(8, 101)])
        self.assertNotEqual(r["requested"]["status"], "INCOMPLETE_DATA")
        self.assertTrue(r["data_quality"]["complete"])


# ─────────────────────────────────────────────────────────────────────────────
# SEARCH COVERAGE AND OPTIMALITY CLAIMS
# ─────────────────────────────────────────────────────────────────────────────

def _pruning_world():
    ex = [P(200 + i, 3, 9.0 - i * 0.05, price=140) for i in range(12)]   # top 8 by score, unaffordable
    ex += [P(300 + i, 3, 4.0, price=20) for i in range(6)]               # best score-per-£
    ex += [P(400, 3, 7.5, price=50)]                                     # the affordable strong option
    return world(ex)


class TestSearchCoverage(unittest.TestCase):
    def test_no_exact_field_remains(self):
        self.assertNotIn("exact", plan(world([P(101, 3, 12)]))["search"])

    def test_pruned_search_never_claims_optimality(self):
        r = plan(_pruning_world(), free_transfers=1, bank=0)
        s = r["search"]
        self.assertTrue(s["pruned"])
        self.assertEqual(s["optimality"], "not_guaranteed")
        self.assertTrue(s["exhaustive_within_search_space"])
        self.assertGreater(s["coverage"]["candidates_excluded"], 0)
        self.assertEqual(s["coverage"]["candidates_supplied"][3], 19)
        self.assertLess(s["coverage"]["candidates_searched"][3], 19)

    def test_the_known_miss_is_real_and_is_reported_not_hidden(self):
        pruned = plan(_pruning_world(), free_transfers=1, bank=0)
        wide = plan(_pruning_world(), free_transfers=1, bank=0, **WIDE)
        self.assertGreater(wide["recommendation"]["projected_gain"],
                           pruned["recommendation"]["projected_gain"] + 1.0)      # +4.0 vs +0.5
        self.assertEqual(wide["search"]["optimality"], "optimal_over_supplied_players")
        self.assertFalse(wide["search"]["pruned"])
        self.assertEqual(pruned["search"]["optimality"], "not_guaranteed")

    def test_named_target_outside_ordinary_pool_still_feeds_the_overall_search(self):
        # 400 is the affordable strong option that default pruning drops (see the known-miss test).
        r = plan(_pruning_world(), free_transfers=1, bank=0, in_ids=[400])
        self.assertEqual(r["recommendation"]["projected_gain"], 4.0)
        self.assertEqual(r["recommendation"]["plan"]["transfers"][0]["in"]["id"], 400)
        self.assertEqual(r["requested"]["plan"]["transfers"][0]["in"]["id"], 400)

    def test_full_coverage_reports_scope_and_zero_excluded(self):
        s = plan(world([P(101, 3, 12), P(102, 4, 9)]), free_transfers=2, **WIDE)["search"]
        self.assertEqual(s["coverage"]["candidates_excluded"], 0)
        self.assertEqual(s["coverage"]["squad_players_not_considered_for_sale"], [])
        self.assertEqual(s["scope"], "supplied_players")
        self.assertIn("shortlist", s["note"])

    def test_squad_players_left_out_of_sale_pool_are_reported(self):
        s = plan(world([P(101, 3, 12)]), out_pool_size=6)["search"]
        self.assertEqual(len(s["coverage"]["squad_players_not_considered_for_sale"]), 9)
        self.assertEqual(s["optimality"], "not_guaranteed")

    def test_shrinking_pools_for_budget_is_reported_and_keeps_named_players(self):
        extras = [P(100 + i, 2 + i % 3, 5 + (i % 7), price=50 + i % 5, team=20 + i % 12) for i in range(120)]
        r = plan(world(extras + [P(999, 3, 0.2, team=60)]), free_transfers=3, bank=20,
                 in_ids=[999], max_evals=2000)
        s = r["search"]
        self.assertTrue(s["coverage"]["limits"]["pools_shrunk_to_fit_budget"])
        self.assertEqual(s["optimality"], "not_guaranteed")
        self.assertLessEqual(s["evaluations"], 2000)
        self.assertIsNotNone(r["requested"]["plan"])

    def test_realistic_pool_is_fast(self):
        rnd = random.Random(11)
        extras = [P(100 + i, 1 + i % 4, rnd.uniform(1, 10), price=rnd.randint(40, 120), team=20 + i % 20)
                  for i in range(160)]
        t = time.time()
        r = plan(world(extras), free_transfers=2, bank=20)
        self.assertLess(time.time() - t, 5.0)
        self.assertEqual(r["search"]["optimality"], "not_guaranteed")


# ─────────────────────────────────────────────────────────────────────────────
# INPUT VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

class TestInputValidation(unittest.TestCase):
    def _bad_candidate(self, **change):
        cand = {**P(101, 3, 5), **change}
        for k in [k for k, v in change.items() if v is _DROP]:
            del cand[k]
        return world([cand])

    def test_invalid_candidates_are_rejected_not_crashed(self):
        cases = {
            "missing price": {"price": _DROP}, "price None": {"price": None}, "price zero": {"price": 0},
            "price float": {"price": 5.5}, "price bool": {"price": True}, "position 7": {"position": 7},
            "position bool": {"position": True}, "position missing": {"position": _DROP},
            "team None": {"team": None}, "team missing": {"team": _DROP},
            "xpts short": {"xpts": [1, 2]}, "xpts str": {"xpts": ["a", 1, 1]}, "xpts NaN": {"xpts": [float("nan"), 1, 1]},
            "xpts inf": {"xpts": [float("inf"), 1, 1]}, "xpts negative": {"xpts": [-1, 1, 1]},
            "xpts missing": {"xpts": _DROP}, "bad confidence": {"confidence": "MAYBE"},
        }
        for label, change in cases.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    plan(self._bad_candidate(**change))

    def test_id_field_must_match_its_key(self):
        pl = world()
        pl[101] = {**P(101, 3, 5), "id": 555}
        with self.assertRaises(ValueError):
            plan(pl)

    def test_none_projection_is_allowed_as_unavailable(self):
        plan(self._bad_candidate(xpts=[None, None, None]))

    def test_all_problems_reported_together(self):
        pl = world([{**P(101, 3, 5), "price": None}, {**P(102, 3, 5), "position": 9}])
        with self.assertRaises(ValueError) as cm:
            plan(pl)
        self.assertIn("player 101", str(cm.exception))
        self.assertIn("player 102", str(cm.exception))

    def test_invalid_scalar_inputs(self):
        pl = world()
        for kw in ({"bank": -1}, {"bank": 2.5}, {"bank": True}, {"free_transfers": -1},
                   {"free_transfers": True}, {"free_transfers": 6}, {"weights": (0, 0, 0)},
                   {"weights": (1, float("nan"), 0)}, {"weights": (1, 0)}, {"max_hits": -1},
                   {"hit_cost": -1}, {"min_gain": -0.1}, {"top_n": 0}, {"in_pool_per_position": 0},
                   {"out_pool_size": 0}, {"max_evals": 0}):
            with self.subTest(kw):
                with self.assertRaises(ValueError):
                    plan(pl, **kw)

    def test_free_transfer_cap_is_overridable_because_the_rule_is_unverified(self):
        plan(world(), free_transfers=6, free_transfer_cap=6)


_DROP = object()


# ─────────────────────────────────────────────────────────────────────────────
# ADAPTERS
# ─────────────────────────────────────────────────────────────────────────────

class TestAdapters(unittest.TestCase):
    def _bootstrap(self):
        el = []
        for pid in (1, 2, 3):
            el.append({"id": pid, "web_name": "N%d" % pid, "element_type": 3, "team": pid,
                       "now_cost": 55, "status": "a", "minutes": 900, "ep_next": str(pid),
                       "form": str(pid), "points_per_game": str(pid)})
        el.append({"id": 4, "web_name": "Out", "element_type": 3, "team": 4, "now_cost": 50,
                   "status": "i", "chance_of_playing_next_round": 0, "minutes": 900,
                   "ep_next": "9", "form": "9", "points_per_game": "9"})
        el.append({"id": 5, "web_name": "NoMins", "element_type": 3, "team": 5, "now_cost": 50,
                   "status": "a", "minutes": 0, "ep_next": "9", "form": "9", "points_per_game": "9"})
        return {"elements": el, "events": [{"id": 6, "finished": True}, {"id": 7, "is_next": True},
                                           {"id": 8}, {"id": 9}, {"id": 10}]}

    def test_shortlist_bounds_filters_and_always_keeps_forced(self):
        ids = shortlist_candidates(self._bootstrap(), squad_ids=[1], forced_ids=[5], per_position=2)
        self.assertIn(1, ids)
        self.assertIn(5, ids)
        self.assertNotIn(4, ids)
        self.assertIn(3, ids)

    def test_dgw_blank_and_now_cost_with_fake_engine(self):
        def fake(el, hist, fx, params):
            return {"xPts": 0.0 if fx is None else float(fx["event"] - 6), "confidence": "HIGH", "risks": []}
        summaries = {1: {"history": [], "fixtures": [{"event": 7}, {"event": 7}, {"event": 9}]}}
        out = project_players(self._bootstrap(), summaries, {}, [1], compute=fake)
        self.assertEqual(out[1]["xpts"], [2.0, 0.0, 3.0])
        self.assertEqual(out[1]["price"], 55)

    def test_missing_summary_is_unavailable_not_blank(self):
        called = []
        out = project_players(self._bootstrap(), {}, {}, [1], compute=lambda *a: called.append(a))
        self.assertEqual(out[1]["xpts"], [None, None, None])
        self.assertEqual(out[1]["confidence"], "LOW")
        self.assertIn("not a blank gameweek", " ".join(out[1]["risks"]))
        self.assertEqual(called, [])
        # a fetched summary with genuinely no fixtures stays a real blank gameweek (0.0, not None)
        blank = project_players(self._bootstrap(), {1: {"history": [], "fixtures": []}}, {}, [1],
                                compute=lambda el, h, fx, p: {"xPts": 0.0, "confidence": "LOW", "risks": []})
        self.assertEqual(blank[1]["xpts"], [0.0, 0.0, 0.0])

    def test_availability_caveat_added_for_flagged_players(self):
        bs = self._bootstrap()
        bs["elements"][0]["chance_of_playing_next_round"] = 50
        summaries = {1: {"history": [], "fixtures": []}, 2: {"history": [], "fixtures": []}}
        fake = lambda el, h, fx, p: {"xPts": 0.0, "confidence": "HIGH", "risks": []}
        out = project_players(bs, summaries, {}, [1, 2], compute=fake)
        self.assertIn("extrapolation", out[1]["outlook_caveat"])
        self.assertNotIn("outlook_caveat", out[2])

    def test_double_gameweek_through_the_real_engine(self):
        from fpl.engine.projection import build_params, compute_xpts
        from fpl.engine.tests.test_pipeline import _ELEMENTS
        elements = [{**e, "now_cost": 60} for e in _ELEMENTS]
        bs = {"elements": elements, "events": [{"id": 5, "is_next": True}]}
        params = build_params(bs)
        fx_a = {"event": 5, "is_home": True, "team_h": 1, "team_a": 2}
        fx_b = {"event": 5, "is_home": False, "team_h": 3, "team_a": 1}
        fx_c = {"event": 7, "is_home": True, "team_h": 1, "team_a": 4}
        hist = [{"minutes": 90, "starts": 1}] * 5
        el = next(e for e in elements if e["id"] == 3001)
        out = project_players(bs, {3001: {"history": hist, "fixtures": [fx_a, fx_b, fx_c]}}, params, [3001])
        single = [compute_xpts(el, hist, f, params)["xPts"] for f in (fx_a, fx_b, fx_c)]
        self.assertEqual(out[3001]["xpts"], [round(single[0] + single[1], 2), 0.0, round(single[2], 2)])
        self.assertGreater(out[3001]["xpts"][0], single[0])
        self.assertEqual(out[3001]["xpts"][1], 0.0)            # genuine blank GW6


if __name__ == "__main__":
    unittest.main()
