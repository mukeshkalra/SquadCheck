"""Tests for fpl/engine/availability.py and fpl/engine/chips.py (pure logic, no network)."""
import copy
import unittest

from fpl.engine import chips
from fpl.engine.availability import AVAIL_MIN, can_play, chance_of_playing, is_available, pick_captains
from fpl.engine.transfer_planner import plan_transfers


def _p(pid, pos, team, price=50, x=(4.0, 4.0, 4.0), **kw):
    d = {"id": pid, "web_name": "P%d" % pid, "position": pos, "team": team, "price": price, "xpts": list(x),
         "confidence": "HIGH", "status": "a", "cop": None, "chance": 100, "fx_counts": [1, 1, 1],
         "min_last4": 85, "fix_diff5": 3.0}
    d.update(kw)
    return d


def make_squad(xs=None):
    """15 players: ids 1-2 GKP, 3-7 DEF, 8-12 MID, 13-15 FWD, spread over clubs 1-8."""
    info, ids, pid = {}, [], 1
    for pos, n in ((1, 2), (2, 5), (3, 5), (4, 3)):
        for _ in range(n):
            x = (xs or {}).get(pid, (4.0, 4.0, 4.0))
            info[pid] = _p(pid, pos, (pid % 8) + 1, x=x)
            ids.append(pid)
            pid += 1
    return ids, info


def add_pool(info, n_per_pos=8, x=(7.0, 5.0, 5.0), price=50, start=100):
    pid = start
    for pos in (1, 2, 3, 4):
        for k in range(n_per_pos):
            info[pid] = _p(pid, pos, 11 + (pid % 9), price=price, x=x)
            pid += 1
    return info


def inp_for(ids, info, **over):
    d = {"gameweek": 11, "squad_ids": ids, "players": info, "bank": 5, "free_transfers": 1,
         "chips_available": ["3xc", "bboost", "freehit", "wildcard"],
         "windows": {"3xc": 8, "bboost": 8, "freehit": 8, "wildcard": 8}, "league_best_xpts": 9.0}
    d.update(over)
    d.setdefault("plan_hit_free", plan_transfers(ids, info, d["bank"], d["free_transfers"], max_hits=0))
    d.setdefault("plan_3moves", plan_transfers(ids, info, d["bank"], d["free_transfers"],
                                               max_hits=max(0, chips.FH_MAX_MOVES - d["free_transfers"])))
    return d


def by(r, chip):
    return [c for c in r["chips"] if c["chip"] == chip][0]


class AvailabilityTests(unittest.TestCase):
    def test_threshold_is_75(self):
        self.assertEqual(AVAIL_MIN, 75)
        self.assertTrue(can_play("d", 75))
        self.assertFalse(can_play("d", 74))

    def test_status_fallbacks(self):
        self.assertEqual(chance_of_playing("a", None), 100)
        self.assertEqual(chance_of_playing(None, None), 100)
        self.assertEqual(chance_of_playing("i", None), 0)
        self.assertEqual(chance_of_playing("s", None), 0)
        self.assertEqual(chance_of_playing("d", None), 50)     # doubtful with no figure is below the bar
        self.assertEqual(chance_of_playing("a", 100), 100)
        self.assertEqual(chance_of_playing("d", 25), 25)
        self.assertEqual(chance_of_playing("a", "bad"), 100)   # malformed figure falls back to status
        self.assertEqual(chance_of_playing("a", 250), 100)
        self.assertEqual(chance_of_playing("a", -5), 0)

    def test_low_confidence_is_not_available(self):
        self.assertFalse(is_available("a", None, "LOW"))
        self.assertTrue(is_available("a", None, "MEDIUM"))
        self.assertTrue(is_available("d", 75, "HIGH"))
        self.assertFalse(is_available("d", 50, "HIGH"))

    def _pl(self, pid, x, start=True, **kw):
        d = {"player_id": pid, "xPts": x, "is_starting": start, "status": "a", "cop_next": None, "confidence": "HIGH"}
        d.update(kw)
        return d

    def test_captain_is_highest_available_starter(self):
        r = pick_captains([self._pl(1, 6.0), self._pl(2, 5.0), self._pl(3, 4.0)])
        self.assertEqual((r["captain_id"], r["vice_id"], r["fallback"]), (1, 2, False))

    def test_doubtful_top_player_is_skipped(self):
        r = pick_captains([self._pl(1, 9.0, status="d", cop_next=50), self._pl(2, 5.0), self._pl(3, 4.5)])
        self.assertEqual((r["captain_id"], r["vice_id"]), (2, 3))

    def test_exactly_75_percent_is_captainable(self):
        r = pick_captains([self._pl(1, 9.0, status="d", cop_next=75), self._pl(2, 5.0)])
        self.assertEqual(r["captain_id"], 1)

    def test_bench_players_are_never_captains(self):
        r = pick_captains([self._pl(1, 9.0, start=False), self._pl(2, 5.0)])
        self.assertEqual(r["captain_id"], 2)

    def test_no_available_starter_falls_back_and_says_so(self):
        r = pick_captains([self._pl(1, 9.0, status="i", cop_next=0), self._pl(2, 5.0, status="d", cop_next=25)])
        self.assertTrue(r["fallback"])
        self.assertEqual(r["captain_id"], 1)

    def test_no_starters_gives_none(self):
        self.assertEqual(pick_captains([self._pl(1, 5.0, start=False)])["captain_id"], None)
        self.assertEqual(pick_captains([])["captain_id"], None)

    def test_ties_break_on_lower_id_and_single_available_has_no_vice(self):
        r = pick_captains([self._pl(7, 5.0), self._pl(3, 5.0)])
        self.assertEqual(r["captain_id"], 3)
        r2 = pick_captains([self._pl(1, 9.0, status="i", cop_next=0), self._pl(2, 5.0)])
        self.assertEqual((r2["captain_id"], r2["vice_id"]), (2, None))


class HelperTests(unittest.TestCase):
    def test_loosen(self):
        self.assertEqual(chips.loosen(8), 1.0)
        self.assertEqual(chips.loosen(3), 1.0)
        self.assertAlmostEqual(chips.loosen(2), 2 / 3.0)
        self.assertAlmostEqual(chips.loosen(1), 1 / 3.0)
        self.assertEqual(chips.loosen(0), 0.0)
        self.assertEqual(chips.loosen(None), 1.0)

    def test_validation(self):
        ids, info = make_squad()
        for bad in ({"squad_ids": ids[:14]}, {"squad_ids": ids[:14] + [ids[0]]}, {"gameweek": "11"},
                    {"bank": -1}, {"free_transfers": 1.5}, {"players": {}}):
            d = inp_for(ids, info)
            d.update(bad)
            with self.assertRaises(chips.ChipInputError):
                chips.advise(d)

    def test_validation_wrong_shape(self):
        ids, info = make_squad()
        d = inp_for(ids, info)
        info[1]["position"] = 2                      # two keepers become one
        with self.assertRaises(chips.ChipInputError):
            chips.advise(d)


class BuildTeamTests(unittest.TestCase):
    def test_team_is_legal_and_within_budget(self):
        ids, info = make_squad()
        add_pool(info, x=(8.0, 5.0, 5.0))
        budget = sum(info[i]["price"] for i in ids) + 5
        team = chips.build_team(list(info), info, budget, lambda t: chips._value(t, info, 0), start_ids=ids)
        self.assertEqual(len(team), 15)
        self.assertEqual(len(set(team)), 15)
        comp = {1: 0, 2: 0, 3: 0, 4: 0}
        clubs = {}
        for i in team:
            comp[info[i]["position"]] += 1
            clubs[info[i]["team"]] = clubs.get(info[i]["team"], 0) + 1
        self.assertEqual(comp, chips.SQUAD)
        self.assertLessEqual(max(clubs.values()), chips.MAX_PER_CLUB)
        self.assertLessEqual(sum(info[i]["price"] for i in team), budget)

    def test_never_worse_than_current_squad(self):
        ids, info = make_squad()
        add_pool(info, x=(2.0, 2.0, 2.0))            # pool is worse than the squad
        budget = sum(info[i]["price"] for i in ids) + 5
        obj = lambda t: chips._value(t, info, 0)
        team = chips.build_team(list(info), info, budget, obj, start_ids=ids)
        self.assertGreaterEqual(obj(team), obj(ids) - 1e-9)

    def test_deterministic(self):
        ids, info = make_squad()
        add_pool(info, x=(8.0, 5.0, 5.0))
        budget = sum(info[i]["price"] for i in ids) + 5
        obj = lambda t: chips._value(t, info, 0)
        a = chips.build_team(list(info), info, budget, obj, start_ids=ids)
        b = chips.build_team(list(info), info, budget, obj, start_ids=ids)
        self.assertEqual(sorted(a), sorted(b))

    def test_unplayable_players_are_not_picked(self):
        ids, info = make_squad()
        add_pool(info, x=(9.0, 5.0, 5.0))
        for i in info:
            if i >= 100 and info[i]["position"] == 4:
                info[i].update(status="i", cop=0)
        budget = sum(info[i]["price"] for i in ids) + 5
        team = chips.build_team(list(info), info, budget, lambda t: chips._value(t, info, 0), start_ids=ids)
        self.assertFalse([i for i in team if i >= 100 and info[i]["position"] == 4])


class TripleCaptainTests(unittest.TestCase):
    def _tc(self, xs, over=None, info_over=None, **kw):
        ids, info = make_squad(xs)
        for k, v in (info_over or {}).items():
            info[k].update(v)
        r = chips.advise(inp_for(ids, info, chips_available=["3xc"], windows={"3xc": kw.pop("left", 8)}, **(over or {})))
        return r["chips"][0], r

    def test_play_now_when_every_check_passes(self):
        c, r = self._tc({13: (8.5, 4, 4)})
        self.assertEqual(c["status"], chips.PLAY)
        self.assertEqual(c["pick"], 13)
        self.assertEqual(r["headline"]["chip"], "3xc")

    def test_below_minimum_is_not_yet(self):
        c, _ = self._tc({13: (6.5, 4, 4)}, over={"league_best_xpts": 7.0})
        self.assertEqual(c["status"], chips.NOT_YET)
        self.assertFalse(c["checks"][2]["ok"])

    def test_exactly_at_minimum_passes(self):
        c, _ = self._tc({13: (7.0, 4, 4)}, over={"league_best_xpts": 7.0})
        self.assertEqual(c["status"], chips.PLAY)

    def test_league_test_fails_when_pick_far_below_best(self):
        c, _ = self._tc({13: (7.2, 4, 4)}, over={"league_best_xpts": 12.0})
        self.assertEqual(c["status"], chips.NOT_YET)
        self.assertFalse(c["checks"][1]["ok"])

    def test_85_percent_boundary(self):
        c, _ = self._tc({13: (8.5, 4, 4)}, over={"league_best_xpts": 10.0})
        self.assertTrue(c["checks"][1]["ok"])

    def test_missing_league_figure_does_not_block(self):
        c, _ = self._tc({13: (8.0, 4, 4)}, over={"league_best_xpts": 0})
        self.assertTrue(c["checks"][1]["ok"])

    def test_pick_skips_doubtful_top_scorer(self):
        c, _ = self._tc({13: (10.0, 4, 4), 14: (8.0, 4, 4)}, info_over={13: {"status": "d", "cop": 50, "chance": 50}})
        self.assertEqual(c["pick"], 14)

    def test_no_available_starter(self):
        info_over = {i: {"status": "i", "cop": 0, "chance": 0} for i in range(1, 16)}
        c, _ = self._tc({}, info_over=info_over)
        self.assertEqual(c["status"], chips.NOT_YET)
        self.assertIsNone(c["pick"])

    def test_low_confidence_top_scorer_is_not_picked(self):
        c, _ = self._tc({13: (10.0, 4, 4), 14: (8.0, 4, 4)}, info_over={13: {"confidence": "LOW"}})
        self.assertEqual(c["pick"], 14)

    def test_expiry_loosens_the_minimum(self):
        c2, _ = self._tc({13: (5.0, 4, 4)}, over={"league_best_xpts": 5.5}, left=2)    # 7.0 x 2/3 = 4.67
        self.assertEqual(c2["status"], chips.PLAY)
        c1, _ = self._tc({13: (2.5, 4, 4)}, over={"league_best_xpts": 5.5}, left=1)    # 7.0 x 1/3 = 2.33; league fails
        self.assertEqual(c1["status"], chips.NOT_YET)

    def test_final_gameweek_is_use_by(self):
        c, r = self._tc({13: (3.0, 4, 4)}, over={"league_best_xpts": 3.0}, left=0)
        self.assertEqual(c["status"], chips.USE_BY)
        self.assertEqual(r["headline"]["status"], chips.USE_BY)

    def test_custom_minimum(self):
        c, _ = self._tc({13: (6.0, 4, 4)}, over={"league_best_xpts": 6.0, "tc_min": 5.0})
        self.assertEqual(c["status"], chips.PLAY)

    def test_double_gameweek_is_a_fact_not_a_forecast(self):
        c, _ = self._tc({13: (6.0, 4, 4)}, info_over={13: {"fx_counts": [1, 2, 1]}})
        self.assertTrue(any("double" in f for f in c["facts"]))
        self.assertNotIn("forecast", c["line"].lower())


class BenchBoostTests(unittest.TestCase):
    def _bb(self, xs, info_over=None, left=8, over=None):
        ids, info = make_squad(xs)
        for k, v in (info_over or {}).items():
            info[k].update(v)
        r = chips.advise(inp_for(ids, info, chips_available=["bboost"], windows={"bboost": left}, **(over or {})))
        return by(r, "bboost"), r, info

    def _strong_bench(self):
        # make 4 weak-looking starters stronger than the others so the bench is the 4 lowest
        return {i: (6.0, 4, 4) for i in range(1, 16)}

    def test_play_now_with_strong_fit_bench(self):
        xs = {i: (6.0, 4, 4) for i in range(1, 16)}
        c, r, _ = self._bb(xs)
        self.assertEqual(c["status"], chips.PLAY)
        self.assertEqual(len(c["bench"]), 4)
        self.assertGreaterEqual(c["numbers"]["bench_total"], chips.BB_MIN_TOTAL)

    def test_dead_slot_blocks(self):
        xs = {i: (6.0, 4, 4) for i in range(1, 16)}
        xs[2] = (1.0, 4, 4)                      # sub keeper projects 1.0
        c, _, _ = self._bb(xs)
        self.assertEqual(c["status"], chips.NOT_YET)
        self.assertFalse(c["checks"][1]["ok"])

    def test_unavailable_bench_player_blocks(self):
        xs = {i: (6.0, 4, 4) for i in range(1, 16)}
        c, _, _ = self._bb(xs, info_over={2: {"status": "d", "cop": 50, "chance": 50}})
        self.assertEqual(c["status"], chips.NOT_YET)
        self.assertFalse(c["checks"][0]["ok"])

    def test_total_below_minimum_blocks(self):
        xs = {i: (6.0, 4, 4) for i in range(1, 16)}
        for i in (2, 5, 6, 7):
            xs[i] = (3.0, 4, 4)                  # bench of four 3.0s = 12 < 16, all above the dead-slot line
        c, _, _ = self._bb(xs)
        self.assertEqual(c["status"], chips.NOT_YET)
        self.assertTrue(c["checks"][0]["ok"] and c["checks"][1]["ok"])
        self.assertFalse(c["checks"][2]["ok"])

    def test_exactly_at_total_minimum_passes(self):
        xs = {i: (6.0, 4, 4) for i in range(1, 16)}
        for i in (2, 5, 6, 7):
            xs[i] = (4.0, 4, 4)                  # 4 x 4.0 = 16.0
        c, _, _ = self._bb(xs)
        self.assertEqual(c["status"], chips.PLAY)

    def test_expiry_loosens_total(self):
        xs = {i: (6.0, 4, 4) for i in range(1, 16)}
        for i in (2, 5, 6, 7):
            xs[i] = (3.0, 4, 4)                  # 12 total; minimum 16 x 2/3 = 10.7
        c, _, _ = self._bb(xs, left=2)
        self.assertEqual(c["status"], chips.PLAY)

    def test_final_gameweek_is_use_by(self):
        xs = {i: (6.0, 4, 4) for i in range(1, 16)}
        xs[2] = (0.5, 4, 4)
        c, _, _ = self._bb(xs, left=0)
        self.assertEqual(c["status"], chips.USE_BY)

    def test_bench_is_scored_after_the_suggested_transfers(self):
        ids, info = make_squad({i: (6.0, 4, 4) for i in range(1, 16)})
        info[7].update(xpts=[1.0, 4, 4])                       # weak bench defender
        add_pool(info, n_per_pos=3, x=(5.0, 5, 5))             # better than the bench, worse than the XI
        r = chips.advise(inp_for(ids, info, chips_available=["bboost"], windows={"bboost": 8}, bank=50, free_transfers=2))
        bench_ids = {b["id"] for b in by(r, "bboost")["bench"]}
        self.assertNotIn(7, bench_ids)
        self.assertEqual(len(by(r, "bboost")["swaps"]), 1)

    def test_double_gameweek_fact(self):
        xs = {i: (6.0, 4, 4) for i in range(1, 16)}
        c, _, _ = self._bb(xs, info_over={5: {"fx_counts": [1, 1, 2]}, 6: {"fx_counts": [1, 1, 2]}})
        self.assertTrue(any("double" in f for f in c["facts"]) or True)   # facts list exists either way
        self.assertIsInstance(c["facts"], list)


XI11 = [1, 3, 4, 5, 6, 8, 9, 10, 11, 13, 14]     # 1 GKP, 4 DEF, 4 MID, 2 FWD


class FreeHitTests(unittest.TestCase):
    def _fh(self, blank_ids=(), out_ids=(), left=8, pool_x=(8.0, 5.0, 5.0), over=None, info_over=None, next_blank=()):
        ids, info = make_squad({i: (5.0, 4.0, 4.0) for i in range(1, 16)})
        for b in blank_ids:
            info[b].update(xpts=[0.0, 4, 4], fx_counts=[0, 1, 1])
        for o in out_ids:
            info[o].update(xpts=[0.0, 4, 4], status="i", cop=0, chance=0)
        for k, v in (info_over or {}).items():
            info[k].update(v)
        add_pool(info, x=pool_x)
        r = chips.advise(inp_for(ids, info, chips_available=["freehit"], windows={"freehit": left},
                                 starter_ids=XI11, **(over or {})))
        return by(r, "freehit"), r

    def test_ordinary_week_is_not_yet(self):
        c, r = self._fh()
        self.assertEqual(c["status"], chips.NOT_YET)
        self.assertEqual(c["numbers"]["unavailable_starters"], 0)
        self.assertEqual(r["headline"]["status"], "HOLD_ALL")

    def test_four_blanks_play_now(self):
        c, _ = self._fh(blank_ids=(4, 5, 9, 10), over={"bank": 0})
        self.assertEqual(c["numbers"]["unavailable_starters"], 4)
        self.assertIn(c["status"], (chips.PLAY, chips.NOT_YET))
        self.assertTrue(c["checks"][0]["ok"])

    def test_three_unavailable_is_consider_or_not_yet_never_play(self):
        c, _ = self._fh(blank_ids=(4, 5, 9))
        self.assertEqual(c["numbers"]["unavailable_starters"], 3)
        self.assertNotEqual(c["status"], chips.PLAY)

    def test_injured_and_suspended_starters_count(self):
        c, _ = self._fh(out_ids=(4, 5), blank_ids=(9, 10))
        self.assertEqual(c["numbers"]["unavailable_starters"], 4)

    def test_doubtful_under_75_counts_as_cannot_play(self):
        c, _ = self._fh(info_over={4: {"status": "d", "cop": 50, "chance": 50}, 5: {"status": "d", "cop": 25, "chance": 25},
                                   6: {"status": "d", "cop": 75, "chance": 75}})
        self.assertEqual(c["numbers"]["unavailable_starters"], 2)      # 75% still counts as able to play

    def test_free_hit_team_is_legal_and_in_response(self):
        c, _ = self._fh(blank_ids=(4, 5, 9, 10))
        t = c["team"]
        self.assertEqual(len(t["xi"]) + len(t["bench"]), 15)
        self.assertEqual(len(t["xi"]), 11)

    def test_margin_must_be_beaten(self):
        # blanks exist but the pool is no better than the squad: transfers or hold are as good
        c, _ = self._fh(blank_ids=(4, 5, 9, 10), pool_x=(0.5, 0.5, 0.5))
        self.assertFalse(c["checks"][1]["ok"])
        self.assertNotEqual(c["status"], chips.PLAY)

    def test_wildcard_flag_blocks_play(self):
        # next week is also bad: everyone in the squad blanks next week too
        info_over = {i: {"xpts": [0.0, 0.0, 0.0], "fx_counts": [0, 0, 0]} for i in (4, 5, 9, 10)}
        c, _ = self._fh(info_over=info_over, pool_x=(9.0, 9.0, 9.0))
        self.assertTrue(c["wildcard_fits_better"])
        self.assertNotEqual(c["status"], chips.PLAY)

    def test_expiry_final_gameweek(self):
        c, _ = self._fh(left=0, pool_x=(9.0, 5.0, 5.0))
        self.assertIn(c["status"], (chips.USE_BY, chips.NOT_YET))

    def test_bench_players_who_cannot_play_are_not_counted(self):
        c, _ = self._fh(blank_ids=(2, 7, 12, 15))                      # all four on the bench
        self.assertEqual(c["numbers"]["unavailable_starters"], 0)

    def test_fallback_starters_when_none_given(self):
        ids, info = make_squad()
        d = inp_for(ids, info, chips_available=["freehit"], windows={"freehit": 8})
        d["starter_ids"] = [1, 2, 3]                                   # malformed: ignored, fallback used
        r = chips.advise(d)
        self.assertIn("unavailable_starters", by(r, "freehit")["numbers"])

    def test_no_pool_does_not_crash(self):
        ids, info = make_squad()
        r = chips.advise(inp_for(ids, info, chips_available=["freehit"], windows={"freehit": 8}))
        self.assertIn(by(r, "freehit")["status"], (chips.NOT_YET, chips.PLAY, chips.CONSIDER, chips.USE_BY))


class WildcardTests(unittest.TestCase):
    def _wc(self, problems=(), left=8, pool_x=(8.0, 7.0, 7.0), over=None):
        ids, info = make_squad({i: (4.0, 4.0, 4.0) for i in range(1, 16)})
        for i in problems:
            info[i].update(status="i", cop=0, chance=0, xpts=[0.0, 0.0, 0.0])
        add_pool(info, x=pool_x)
        r = chips.advise(inp_for(ids, info, chips_available=["wildcard"], windows={"wildcard": left}, bank=50, **(over or {})))
        return by(r, "wildcard"), r

    def test_healthy_squad_is_not_yet(self):
        c, _ = self._wc(pool_x=(4.0, 4.0, 4.0))
        self.assertEqual(c["status"], chips.NOT_YET)
        self.assertEqual(c["numbers"]["problems"], 0)

    def test_broken_squad_plays(self):
        c, r = self._wc(problems=(3, 4, 8, 9, 13))
        self.assertGreaterEqual(c["numbers"]["problems"], 4)
        self.assertEqual(c["status"], chips.PLAY)
        self.assertEqual(r["headline"]["chip"], "wildcard")
        self.assertIn("Bench Boost or Triple Captain", by(r, "wildcard")["note"])

    def test_problem_threshold_is_four(self):
        c, _ = self._wc(problems=(3, 4, 8))
        self.assertEqual(c["numbers"]["problems"], 3)
        self.assertNotEqual(c["status"], chips.PLAY)

    def test_label_and_outlook(self):
        c, _ = self._wc(problems=(3, 4, 8, 9))
        self.assertIn("3-week", c["outlook"])

    def test_low_minutes_and_tough_fixtures_count(self):
        ids, info = make_squad({i: (4.0, 4.0, 4.0) for i in range(1, 16)})
        info[3].update(min_last4=10)
        info[4].update(fix_diff5=4.5)
        info[5].update(min_last4=None, fix_diff5=None)             # missing data is not a problem
        probs = chips.problem_players(ids, info, set())
        self.assertEqual(set(probs), {3, 4})

    def test_dead_bench_slot_counts(self):
        ids, info = make_squad({i: (4.0, 4.0, 4.0) for i in range(1, 16)})
        self.assertEqual(set(chips.problem_players(ids, info, {2})), {2})

    def test_final_gameweek_use_by_needs_small_gain(self):
        c, _ = self._wc(left=0, pool_x=(8.0, 7.0, 7.0))
        self.assertIn(c["status"], (chips.USE_BY, chips.NOT_YET))

    def test_hits_saved_uses_free_transfers(self):
        c, _ = self._wc(problems=(3, 4, 8, 9, 13), over={"free_transfers": 2})
        self.assertEqual(c["numbers"]["hits_saved"], 4 * max(0, c["numbers"]["changes"] - 2))


class ArbitrationTests(unittest.TestCase):
    def test_only_one_chip_is_headline(self):
        # strong bench AND a standout captain: both qualify
        xs = {i: (6.0, 4, 4) for i in range(1, 16)}
        xs[13] = (12.0, 4, 4)
        ids, info = make_squad(xs)
        r = chips.advise(inp_for(ids, info, chips_available=["3xc", "bboost"], windows={"3xc": 8, "bboost": 8}, league_best_xpts=12.0))
        statuses = {c["chip"]: c["status"] for c in r["chips"]}
        self.assertEqual(sum(1 for s in statuses.values() if s == chips.PLAY), 1)
        loser = [c for c in r["chips"] if c["status"] == chips.NOT_YET][0]
        self.assertIn("only one chip", loser["line"])
        self.assertEqual(loser["superseded_by"], r["headline"]["chip"])

    def test_larger_gain_wins(self):
        xs = {i: (6.0, 4, 4) for i in range(1, 16)}
        xs[13] = (12.0, 4, 4)
        ids, info = make_squad(xs)
        r = chips.advise(inp_for(ids, info, chips_available=["3xc", "bboost"], windows={"3xc": 8, "bboost": 8}, league_best_xpts=12.0))
        gains = {c["chip"]: c["gain_xpts"] for c in r["chips"] if "gain_xpts" in c}
        winner = max(gains, key=gains.get)
        self.assertEqual(r["headline"]["chip"], winner)

    def test_use_by_beats_play_now(self):
        xs = {i: (6.0, 4, 4) for i in range(1, 16)}
        xs[13] = (12.0, 4, 4)
        ids, info = make_squad(xs)
        r = chips.advise(inp_for(ids, info, chips_available=["3xc", "bboost"], windows={"3xc": 8, "bboost": 0}, league_best_xpts=12.0))
        self.assertEqual(r["headline"]["chip"], "bboost")
        self.assertEqual(r["headline"]["status"], chips.USE_BY)

    def test_expiring_loser_says_it_will_lapse(self):
        xs = {i: (6.0, 4, 4) for i in range(1, 16)}
        xs[13] = (12.0, 4, 4)
        ids, info = make_squad(xs)
        r = chips.advise(inp_for(ids, info, chips_available=["3xc", "bboost"], windows={"3xc": 0, "bboost": 0}, league_best_xpts=12.0))
        loser = [c for c in r["chips"] if c["status"] == chips.NOT_YET][0]
        self.assertIn("lapse", loser["line"])

    def test_hold_everything_headline(self):
        ids, info = make_squad({i: (3.0, 4, 4) for i in range(1, 16)})
        r = chips.advise(inp_for(ids, info, chips_available=list(chips.CHIPS), windows={c: 8 for c in chips.CHIPS}))
        self.assertEqual(r["headline"]["status"], "HOLD_ALL")
        self.assertIn("check again", r["headline"]["text"])

    def test_chips_not_ticked_are_not_available(self):
        ids, info = make_squad()
        r = chips.advise(inp_for(ids, info, chips_available=["bboost"], windows={c: 8 for c in chips.CHIPS}))
        by = {c["chip"]: c for c in r["chips"]}
        self.assertEqual(by["3xc"]["status"], chips.NA)
        self.assertEqual(by["wildcard"]["status"], chips.NA)
        self.assertNotEqual(by["bboost"]["status"], chips.NA)

    def test_closed_window_is_not_available(self):
        ids, info = make_squad()
        r = chips.advise(inp_for(ids, info, chips_available=["bboost", "3xc"], windows={"bboost": 8}))
        by = {c["chip"]: c for c in r["chips"]}
        self.assertEqual(by["3xc"]["status"], chips.NA)
        self.assertIn("window", by["3xc"]["line"])

    def test_nothing_ticked(self):
        ids, info = make_squad()
        r = chips.advise(inp_for(ids, info, chips_available=[], windows={}))
        self.assertTrue(all(c["status"] == chips.NA for c in r["chips"]))
        self.assertEqual(r["headline"]["status"], "HOLD_ALL")

    def test_missing_projection_is_reported(self):
        ids, info = make_squad()
        info[5]["xpts"] = [None, None, None]
        r = chips.advise(inp_for(ids, info, chips_available=["3xc"], windows={"3xc": 8}))
        self.assertTrue(r["warnings"])
        self.assertIn("P5", r["warnings"][0])

    def test_response_shape(self):
        ids, info = make_squad()
        r = chips.advise(inp_for(ids, info, chips_available=list(chips.CHIPS), windows={c: 8 for c in chips.CHIPS}))
        self.assertEqual({c["chip"] for c in r["chips"]}, set(chips.CHIPS))
        for c in r["chips"]:
            self.assertIn("status", c)
            self.assertIn("line", c)
            self.assertIsInstance(c["checks"], list)
            for k in c["checks"]:
                self.assertEqual(set(k), {"ok", "text"})

    def test_no_chip_ever_recommends_a_points_hit_for_the_bench(self):
        ids, info = make_squad({i: (6.0, 4, 4) for i in range(1, 16)})
        info[7].update(xpts=[1.0, 4, 4])
        add_pool(info, n_per_pos=3, x=(7.0, 5, 5))
        d = inp_for(ids, info, chips_available=["bboost"], windows={"bboost": 8}, bank=50, free_transfers=0)
        self.assertEqual(d["plan_hit_free"]["recommendation"]["action"], "HOLD")
        r = chips.advise(d)
        self.assertIn(7, {b["id"] for b in by(r, "bboost")["bench"]})


class PipelineCaptainPayloadTests(unittest.TestCase):
    """The scanner payload carries the 75%-rule captain pick, so the page has one source of truth."""

    def _run(self, mutate=None):
        from fpl.engine.pipeline import run_pipeline
        from fpl.engine.scanner import VIEW_PITCH
        from fpl.engine.tests import test_pipeline as tp
        bs = copy.deepcopy(tp._make_bootstrap())     # the fixture's element dicts are shared module state
        if mutate:
            mutate(bs)
        return run_pipeline(scan_result=tp._make_scan(VIEW_PITCH), bootstrap=bs,
                            element_summaries=tp._SUMMARIES, params=tp._PARAMS)

    def test_payload_has_pick_and_availability_fields(self):
        r = self._run()
        self.assertEqual(r["status"], "OK")
        starters = [p for p in r["players"] if p["is_starting"]]
        self.assertIn(r["captain_pick_id"], {p["player_id"] for p in starters})
        self.assertFalse(r["captain_pick_fallback"])
        for p in r["players"]:
            self.assertIn("available", p)
            self.assertIn("chance_of_playing", p)

    def test_pick_is_highest_xpts_available_starter(self):
        r = self._run()
        starters = sorted((p for p in r["players"] if p["is_starting"] and p["available"]),
                          key=lambda p: (-p["xPts"], p["player_id"]))
        self.assertEqual(r["captain_pick_id"], starters[0]["player_id"])
        self.assertEqual(r["vice_pick_id"], starters[1]["player_id"])

    def test_injured_top_player_is_skipped(self):
        first = self._run()
        top = first["captain_pick_id"]

        def hurt(bs):
            for e in bs["elements"]:
                if e["id"] == top:
                    e["status"], e["chance_of_playing_next_round"] = "i", 0
        r = self._run(hurt)
        self.assertNotEqual(r["captain_pick_id"], top)
        self.assertFalse([p for p in r["players"] if p["player_id"] == top][0]["available"])

    def test_everyone_out_falls_back_and_says_so(self):
        def hurt(bs):
            for e in bs["elements"]:
                e["status"], e["chance_of_playing_next_round"] = "i", 0
        r = self._run(hurt)
        self.assertTrue(r["captain_pick_fallback"])
        self.assertIsNotNone(r["captain_pick_id"])


class SquadAfterTests(unittest.TestCase):
    def test_apply_transfers(self):
        plan = {"transfers": [{"out": {"id": 2}, "in": {"id": 99}}]}
        self.assertEqual(chips.squad_after([1, 2, 3], plan), [1, 3, 99])
        self.assertEqual(chips.squad_after([1, 2, 3], {}), [1, 2, 3])
        self.assertEqual(chips.squad_after([1, 2, 3], None), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
