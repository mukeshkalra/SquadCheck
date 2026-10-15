"""Head-to-head panel data and plan verdicts (presentation only)."""
import unittest

from fpl.engine import head_to_head as h


def el(pid, name, pos=4, cost=78, pts=45, goals=5, xg="2.77", owned="69.9", status="a"):
    return {"id": pid, "web_name": name, "element_type": pos, "now_cost": cost, "total_points": pts,
            "goals_scored": goals, "expected_goals": xg, "selected_by_percent": owned, "status": status}


def hist(n_starts, pts=5):
    return [{"minutes": 90 if k < n_starts else 0, "starts": 1 if k < n_starts else 0, "total_points": pts,
             "team_h_score": 1, "team_a_score": 0} for k in range(6)]


def summ(opp, home, fdr, hs=6):
    return {"history": hist(hs), "fixtures": [{"event": 7, "is_home": home, "team_h": 1 if home else opp,
                                                "team_a": opp if home else 1, "difficulty": fdr}]}


TEAMS = {2: ("Everton", "EVE"), 3: ("Chelsea", "CHE")}
PARAMS = {"team_gkp_xgc": {2: 1.38, 3: 1.77}, "league_avg_xgc": 1.494, "team_xgf": {2: 1.0, 3: 2.0}, "league_avg_xgf": 1.5}


def pair(**kw):
    by_id = {1: el(1, "Pedro"), 2: el(2, "Barry", cost=57, pts=26, goals=3, xg="4.40", owned="8.7")}
    by_id.update(kw.get("by_id", {}))
    summaries = {1: summ(2, False, 3, hs=5), 2: summ(3, True, 4)}
    players = {1: {"xpts": [5.0, 3, 3]}, 2: {"xpts": [6.0, 3, 3]}}
    return h.compare_pair(1, 2, by_id, summaries, players, PARAMS, TEAMS, 7)


class TestComparePair(unittest.TestCase):
    def setUp(self):
        self.c = pair()
        self.rows = {r["key"]: r for r in self.c["rows"]}

    def test_rows_and_edges(self):
        self.assertEqual(self.rows["xpts"]["edge"], "in")
        self.assertEqual(self.rows["points"]["edge"], "out")
        self.assertEqual(self.rows["chances"]["edge"], "in")
        self.assertEqual(self.rows["chances"]["in"], "3 goals · 4.4 xG")
        self.assertEqual(self.rows["starts"]["out"], "5 of 6")
        self.assertEqual(self.rows["fixture"]["in"], "CHE (H) · FPL 4")
        self.assertEqual(self.rows["fixture"]["edge"], "in")
        self.assertIsNone(self.rows["price"]["edge"])

    def test_fdr_conflict_is_explained(self):
        self.assertIn("FPL rates Barry's fixture 4", self.c["fixture_note"])

    def test_for_and_against(self):
        self.assertTrue(any("better chances" in x and "Barry" in x for x in self.c["for"]))
        self.assertTrue(any("45 points" in x for x in self.c["against"]))
        self.assertTrue(any("cheaper" in x for x in self.c["against"]))
        self.assertTrue(any("goals are well ahead" in x for x in self.c["for"]))

    def test_goalkeepers_have_no_xg_row(self):
        c = pair(by_id={1: el(1, "A", pos=1), 2: el(2, "B", pos=1)})
        self.assertNotIn("chances", {r["key"] for r in c["rows"]})

    def test_missing_summary_drops_rows_without_crashing(self):
        c = h.compare_pair(1, 2, {1: el(1, "P"), 2: el(2, "B")}, {}, {}, PARAMS, TEAMS, 7)
        keys = {r["key"] for r in c["rows"]}
        self.assertNotIn("fixture", keys)
        self.assertNotIn("starts", keys)
        self.assertIn("price", keys)

    def test_doubtful_incoming_player_is_flagged(self):
        c = pair(by_id={2: el(2, "Barry", status="d")})
        self.assertTrue(any("not fully fit" in x for x in c["against"]))

    def test_defender_fixture_uses_opponent_attack(self):
        c = pair(by_id={1: el(1, "A", pos=2), 2: el(2, "B", pos=2)})
        f = {r["key"]: r for r in c["rows"]}["fixture"]
        self.assertEqual(f["edge"], "out")        # facing a weaker attack (EVE) is better for a defender


class TestVerdict(unittest.TestCase):
    def plan(self, net, ol):
        return {"transfers": [{}], "projected_gain_vs_hold": net, "outlook": {"vs_hold": ol}}

    def tier(self, net, ol):
        return h.plan_verdict(self.plan(net, ol))["tier"]

    def test_tiers(self):
        self.assertEqual(self.tier(5.0, [5.0, 1.0, 0.0]), "clear")
        self.assertEqual(self.tier(1.8, [1.8, 0.5, 0.0]), "small")
        self.assertEqual(self.tier(1.0, [1.0, 0.5, 0.0]), "marginal")
        self.assertEqual(self.tier(2.0, [2.0, -1.4, 0.0]), "marginal")     # wins now, loses the three-week view
        self.assertEqual(self.tier(5.0, [5.0, -2.0, -3.0]), "marginal")
        self.assertEqual(self.tier(-0.5, [-0.5, 0, 0]), "none")

    def test_boundaries(self):
        self.assertEqual(self.tier(h.SMALL_MIN_GAIN, [1.5, 0.1, 0.0]), "small")
        self.assertEqual(self.tier(h.CLEAR_MIN_GAIN, [4.0, 0.0, 0.0]), "clear")
        self.assertEqual(self.tier(h.CLEAR_MIN_GAIN - 0.01, [3.99, 1.0, 0.0]), "small")

    def test_no_outlook_still_labels_and_is_not_calibrated(self):
        v = h.plan_verdict({"transfers": [{}], "projected_gain_vs_hold": 2.0})
        self.assertEqual(v["tier"], "small")
        self.assertFalse(v["calibrated"])

    def test_hold_and_bad_input(self):
        self.assertIsNone(h.plan_verdict({"transfers": [], "projected_gain_vs_hold": 0}))
        self.assertIsNone(h.plan_verdict({"transfers": [{}], "projected_gain_vs_hold": None}))


class TestAttach(unittest.TestCase):
    def test_attaches_everywhere_once(self):
        t = {"out": {"id": 1}, "in": {"id": 2}}
        plan = {"transfers": [t], "projected_gain_vs_hold": 2.0, "outlook": {"vs_hold": [2.0, 0.5, 0.0]}}
        obj = {"recommendation": {"plan": plan}, "alternatives": [plan, {"transfers": [], "projected_gain_vs_hold": 0}]}
        args = ({1: el(1, "P"), 2: el(2, "B")}, {}, {}, PARAMS, TEAMS, 7)
        h.attach(obj, *args)
        h.attach(obj, *args)
        self.assertIn("compare", t)
        self.assertEqual(plan["verdict"]["tier"], "small")
        self.assertIsNone(obj["alternatives"][1]["verdict"])


if __name__ == "__main__":
    unittest.main()
