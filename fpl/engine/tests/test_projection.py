"""
Deterministic tests for the SquadCheck FPL Projection Engine V0.2.

All tests use hardcoded inputs drawn from documented worked examples and
the V0.2 design review.  No live API calls are made.

V0.1 → V0.2 deltas (GW6 examples):
  Haaland  V0.1=6.3  V0.2=5.9   MCI away at LIV: Liverpool decent def, City away penalty
  Gabriel  V0.1=5.3  V0.2=5.0   Leeds xGC<avg (better defence than difficulty=2 implied)
  Saka     V0.1=6.3  V0.2=5.9   Same fixture as Gabriel; lower because Leeds is decent

These are not model bias.  The reductions are fixture-specific corrections:
  - Leeds xGC_p90=1.290 < league_avg=1.5035 → harder than difficulty=2 implied
  - Liverpool xGC_p90=1.220 < league_avg → decent defence for Man City away

V0.2 design constants (GW6, from live bootstrap-static):
  LEAGUE_AVG_XGC = 1.5035  (mean of 20 primary GKPs)
  LEAGUE_AVG_XGF = 1.5720  (mean of 20 team xGF proxies)
  HOME_ADV       = 1.10    (heuristic; mean atk_scale over all home fixtures ≈ 1.097)
"""

import math
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from fpl.engine.projection import (
    compute_xpts,
    E_floor_X2,
    HOME_ADV,
    _build_team_gkp_xgc,
    _build_team_gkp_starts,
    _build_team_xgf,
    _compute_league_avg_xgc,
    _compute_league_avg_xgf,
)

# ─────────────────────────────────────────────────────────────────────────────
# SHARED TEST PARAMS (GW6 live data, frozen for determinism)
# ─────────────────────────────────────────────────────────────────────────────

GW6_PARAMS = {
    # Team primary-GKP xGC_p90 (current-season; unbiased defensive quality)
    "team_gkp_xgc": {
        1: 0.810,   # ARS — best defence in league
        8: 2.160,   # CRY — leakiest (n=3 starts, small sample)
        12: 1.680,  # IPS
        13: 1.290,  # LEE — below-average concession rate despite being promoted
        14: 1.220,  # LIV
        15: 1.450,  # MCI
        16: 1.360,  # MUN
        20: 1.730,  # SUN
    },
    # Primary GKP starts count — used for n/(n+2) damping of opponent signal
    "team_gkp_starts": {
        1: 5, 8: 3, 12: 4, 13: 5, 14: 5, 15: 5, 16: 5, 20: 5,
    },
    "league_avg_xgc": 1.5035,   # mean of 20 primary GKPs at GW6
    # Team attacking proxy = Σ outfield starters xG_p90 (starts >= 3)
    "team_xgf": {
        1: 1.790, 8: 1.500, 12: 1.360, 13: 1.680,
        14: 1.720, 15: 2.360, 16: 1.850, 20: 1.710,
    },
    "league_avg_xgf": 1.5720,   # mean of 20 team xGF proxies at GW6
    # Positional baselines (current-season medians, starts >= 3, GW6)
    "pos_xG_base": {1: 0.01, 2: 0.04, 3: 0.13, 4: 0.485},
    "pos_xA_base": {1: 0.01, 2: 0.05, 3: 0.10, 4: 0.035},
}

# ── Fixture dicts: now include team_h and team_a for V0.2 opponent resolution ──
# Arsenal (team=1) home vs Leeds (team=13)
FIXTURE_ARS_HOME_LEE = {"difficulty": 2, "is_home": True,  "event": 6, "team_h": 1,  "team_a": 13}
# Man City (team=15) away at Liverpool (team=14)
FIXTURE_MCI_AWAY_LIV = {"difficulty": 4, "is_home": False, "event": 6, "team_h": 14, "team_a": 15}
# Neutral / legacy (no team_h/team_a → falls back to league-average opponent)
FIXTURE_HOME_NEUTRAL = {"difficulty": 3, "is_home": True,  "event": 6}
FIXTURE_AWAY_NEUTRAL = {"difficulty": 3, "is_home": False, "event": 6}

# ── History records ──────────────────────────────────────────────────────────
HAALAND_HISTORY = [{"minutes": 90, "starts": 1}] * 5
GABRIEL_HISTORY = [{"minutes": 90, "starts": 1}] * 5
SAKA_HISTORY    = [
    {"minutes": 67, "starts": 1}, {"minutes": 90, "starts": 1},
    {"minutes": 89, "starts": 1}, {"minutes": 90, "starts": 1},
    {"minutes": 80, "starts": 1},
]

# ── Player dicts ─────────────────────────────────────────────────────────────
HAALAND = {
    "id": 411, "web_name": "Haaland",
    "element_type": 4, "team": 15,
    "status": "a", "chance_of_playing_next_round": None,
    "minutes": 450, "starts": 5, "bonus": 9,
    "expected_goals_per_90": 0.88,
    "expected_assists_per_90": 0.11,
    "expected_goals_conceded_per_90": 1.45,
    "saves_per_90": 0.0,
    "penalties_order": 1,
}
GABRIEL = {
    "id": 4, "web_name": "Gabriel",
    "element_type": 2, "team": 1,
    "status": "a", "chance_of_playing_next_round": None,
    "minutes": 450, "starts": 5, "bonus": 2,
    "expected_goals_per_90": 0.10,
    "expected_assists_per_90": 0.04,
    "expected_goals_conceded_per_90": 0.81,
    "saves_per_90": 0.0,
}
SAKA = {
    "id": 12, "web_name": "Saka",
    "element_type": 3, "team": 1,
    "status": "a", "chance_of_playing_next_round": None,
    "minutes": 416, "starts": 5, "bonus": 4,
    "expected_goals_per_90": 0.70,
    "expected_assists_per_90": 0.21,
    "expected_goals_conceded_per_90": 0.77,
    "saves_per_90": 0.0,
    "penalties_order": 1,
}

TOL = 1e-4   # absolute tolerance for float assertions


def _near(a, b, tol=TOL):
    return abs(a - b) < tol


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS / FORMULA REPLICATION (for independent verification in tests)
# ─────────────────────────────────────────────────────────────────────────────

def _expected_atk_scale(opp_xgc_raw, opp_n_gkp, league_avg_xgc, is_home):
    w_opp     = opp_n_gkp / (opp_n_gkp + 2.0)
    opp_xgc_d = w_opp * opp_xgc_raw + (1.0 - w_opp) * league_avg_xgc
    hf        = HOME_ADV if is_home else (1.0 / HOME_ADV)
    return (opp_xgc_d / league_avg_xgc) * hf

def _expected_lam(our_xgc_raw, our_n, opp_xgf, league_avg_xgc, league_avg_xgf, is_home, w):
    our_xgc_d = w * our_xgc_raw + (1.0 - w) * league_avg_xgc
    opp_atk_q = opp_xgf / league_avg_xgf
    hf        = HOME_ADV if is_home else (1.0 / HOME_ADV)
    return our_xgc_d * opp_atk_q / hf


# ─────────────────────────────────────────────────────────────────────────────
# E_floor_X2 TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestEFloorX2(unittest.TestCase):

    def test_zero(self):
        self.assertEqual(E_floor_X2(0.0), 0.0)
        self.assertEqual(E_floor_X2(-1.0), 0.0)

    def test_known_values(self):
        self.assertTrue(_near(E_floor_X2(0.907792), 0.244582, tol=1e-4))
        self.assertTrue(_near(E_floor_X2(0.65),     0.143,    tol=5e-3))
        self.assertTrue(_near(E_floor_X2(0.75),     0.181,    tol=5e-3))

    def test_monotone(self):
        vals = [E_floor_X2(lam) for lam in [0.5, 0.8, 1.0, 1.5, 2.0]]
        for a, b in zip(vals, vals[1:]):
            self.assertGreater(b, a)

    def test_less_than_lambda_over_2(self):
        for lam in [0.6, 0.9, 1.2, 1.5]:
            self.assertLess(E_floor_X2(lam), lam / 2)


# ─────────────────────────────────────────────────────────────────────────────
# BUILD HELPERS TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildTeamGkpXgc(unittest.TestCase):

    def _make(self, entries):
        return [{"id": i, "element_type": et, "team": team, "starts": st,
                 "minutes": mins, "expected_goals_conceded_per_90": xgc, "removed": False}
                for i, (et, team, st, mins, xgc) in enumerate(entries)]

    def test_selects_highest_starts(self):
        els = self._make([(1,1,3,270,1.20),(1,1,5,450,0.81)])
        self.assertAlmostEqual(_build_team_gkp_xgc(els)[1], 0.81)

    def test_tiebreak_on_minutes(self):
        els = self._make([(1,2,5,450,0.95),(1,2,5,360,1.10)])
        self.assertAlmostEqual(_build_team_gkp_xgc(els)[2], 0.95)

    def test_skips_non_gkp(self):
        els = self._make([(2,3,5,450,0.70),(1,3,5,450,1.30)])
        self.assertAlmostEqual(_build_team_gkp_xgc(els)[3], 1.30)

    def test_skips_removed(self):
        els = [{"id":0,"element_type":1,"team":4,"starts":5,
                "minutes":450,"expected_goals_conceded_per_90":0.70,"removed":True},
               {"id":1,"element_type":1,"team":4,"starts":3,
                "minutes":270,"expected_goals_conceded_per_90":1.20,"removed":False}]
        self.assertAlmostEqual(_build_team_gkp_xgc(els)[4], 1.20)

    def test_league_avg_is_mean(self):
        els = self._make([(1,1,5,450,1.00),(1,2,5,450,2.00),(1,3,5,450,3.00)])
        xgc = _build_team_gkp_xgc(els)
        self.assertAlmostEqual(_compute_league_avg_xgc(xgc), 2.00)


class TestBuildTeamGkpStarts(unittest.TestCase):

    def _make(self, entries):
        return [{"id": i, "element_type": et, "team": team, "starts": st,
                 "minutes": mins, "expected_goals_conceded_per_90": 1.0, "removed": False}
                for i, (et, team, st, mins) in enumerate(entries)]

    def test_returns_starts_of_primary_gkp(self):
        els = self._make([(1,1,3,270),(1,1,5,450)])   # second has more starts
        starts = _build_team_gkp_starts(els)
        self.assertEqual(starts[1], 5)

    def test_tiebreak_on_minutes(self):
        els = self._make([(1,2,5,450),(1,2,5,360)])
        starts = _build_team_gkp_starts(els)
        self.assertEqual(starts[2], 5)   # both 5 starts; higher minutes selected

    def test_skips_non_gkp(self):
        els = self._make([(2,3,5,450),(1,3,4,360)])
        starts = _build_team_gkp_starts(els)
        self.assertIn(3, starts)
        self.assertEqual(starts[3], 4)   # only GKP selected


class TestBuildTeamXgf(unittest.TestCase):

    def _make(self, entries):
        return [{"id": i, "element_type": et, "team": team, "starts": st,
                 "expected_goals_per_90": xg}
                for i, (et, team, st, xg) in enumerate(entries)]

    def test_sums_outfield_starters(self):
        els = self._make([(2,1,4,0.05),(3,1,4,0.30),(4,1,4,0.40),(1,1,5,0.00)])
        xgf = _build_team_xgf(els)
        self.assertAlmostEqual(xgf[1], 0.75)

    def test_excludes_gkp(self):
        els = self._make([(1,1,5,9.99),(3,1,4,0.50)])  # GKP xG should not count
        xgf = _build_team_xgf(els)
        self.assertAlmostEqual(xgf[1], 0.50)

    def test_excludes_low_starts(self):
        els = self._make([(3,1,2,9.99),(3,1,4,0.40)])  # first below min_starts=3
        xgf = _build_team_xgf(els)
        self.assertAlmostEqual(xgf[1], 0.40)

    def test_league_avg_xgf(self):
        els = self._make([(3,1,4,1.00),(3,2,4,3.00)])
        xgf = _build_team_xgf(els)
        self.assertAlmostEqual(_compute_league_avg_xgf(xgf), 2.00)


# ─────────────────────────────────────────────────────────────────────────────
# build_params SMOKE TEST
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildParams(unittest.TestCase):

    def _bootstrap(self):
        def gkp(pid, team, st, mins, xgc):
            return {"id": pid, "element_type": 1, "team": team, "starts": st,
                    "minutes": mins, "expected_goals_conceded_per_90": xgc,
                    "expected_goals_per_90": 0.0, "expected_assists_per_90": 0.0,
                    "removed": False}
        def out(pid, team, st, xg):
            return {"id": pid, "element_type": 2, "team": team, "starts": st,
                    "minutes": st*90, "expected_goals_conceded_per_90": 1.0,
                    "expected_goals_per_90": xg, "expected_assists_per_90": 0.03,
                    "removed": False}
        els = ([gkp(1,1,5,450,0.80), gkp(2,2,5,450,1.60)] +
               [out(10+i,1,5,0.05) for i in range(4)] +
               [out(20+i,2,5,0.05) for i in range(4)])
        return {"elements": els}

    def test_required_keys_present(self):
        from fpl.engine.projection import build_params
        p = build_params(self._bootstrap())
        for key in ("team_gkp_xgc","team_gkp_starts","league_avg_xgc",
                    "team_xgf","league_avg_xgf","pos_xG_base","pos_xA_base"):
            self.assertIn(key, p)

    def test_league_avg_xgc_is_mean(self):
        from fpl.engine.projection import build_params
        p = build_params(self._bootstrap())
        self.assertAlmostEqual(p["league_avg_xgc"], 1.20)  # (0.80+1.60)/2

    def test_team_xgf_populated(self):
        from fpl.engine.projection import build_params
        p = build_params(self._bootstrap())
        self.assertIn(1, p["team_xgf"])
        self.assertIn(2, p["team_xgf"])

    def test_team_gkp_starts_populated(self):
        from fpl.engine.projection import build_params
        p = build_params(self._bootstrap())
        self.assertIn(1, p["team_gkp_starts"])
        self.assertEqual(p["team_gkp_starts"][1], 5)


# ─────────────────────────────────────────────────────────────────────────────
# HAALAND  V0.2 GW6 — FWD Man City away at Liverpool
# V0.1 xPts=6.3   V0.2 xPts=5.9
# Reduction: Liverpool decent defence (xGC=1.22 < avg 1.50); City is away
# ─────────────────────────────────────────────────────────────────────────────

class TestHaalandGW6(unittest.TestCase):

    def setUp(self):
        self.r = compute_xpts(HAALAND, HAALAND_HISTORY, FIXTURE_MCI_AWAY_LIV, GW6_PARAMS)
        self.w = 5/7

    def test_final_xpts(self):
        # full-precision raw = 5.8979 → rounds to 5.9
        self.assertEqual(self.r["xPts"], 5.9)

    def test_xpts_raw(self):
        raw = self.r["components"]["_xPts_raw"]
        self.assertTrue(_near(raw, 5.8979, tol=5e-3), "raw=%.4f" % raw)

    def test_atk_scale(self):
        expected = _expected_atk_scale(1.220, 5, 1.5035, False)
        self.assertTrue(_near(self.r["inputs"]["atk_scale"], expected))

    def test_atk_scale_value(self):
        # (damped LIV xGC / league_avg) × (1/HOME_ADV)
        # = (0.714×1.22 + 0.286×1.5035) / 1.5035 × 0.909
        self.assertTrue(_near(self.r["inputs"]["atk_scale"], 0.786649, tol=1e-4))

    def test_xGoalPts(self):
        xG_d = self.w*0.88 + (1-self.w)*0.485
        expected = xG_d * 1.0 * self.r["inputs"]["atk_scale"] * 4
        self.assertTrue(_near(self.r["components"]["xGoalPts"], expected))

    def test_cs_zero_for_fwd(self):
        self.assertEqual(self.r["components"]["xCSPts"],       0.0)
        self.assertEqual(self.r["components"]["xGCDeductPts"], 0.0)

    def test_xbonus(self):
        self.assertTrue(_near(self.r["components"]["xBonus"], 1.275))

    def test_confidence_high(self):
        self.assertEqual(self.r["confidence"], "HIGH")

    def test_opponent_id(self):
        self.assertEqual(self.r["inputs"]["opponent_id"], 14)  # Liverpool team_h

    def test_v01_was_higher(self):
        # V0.1=6.3, V0.2=5.9 — correction, not bias; Liverpool is decent def, City is away
        self.assertLess(self.r["xPts"], 6.3)


# ─────────────────────────────────────────────────────────────────────────────
# GABRIEL  V0.2 GW6 — DEF Arsenal home vs Leeds
# V0.1 xPts=5.3   V0.2 xPts=5.0
# Reduction: Leeds xGC=1.29 < avg 1.50 (better defence than diff=2 implied)
#            Leeds xGF=1.68 > avg 1.57 (slightly above-average attack → higher λ)
# ─────────────────────────────────────────────────────────────────────────────

class TestGabrielGW6(unittest.TestCase):

    def setUp(self):
        self.r = compute_xpts(GABRIEL, GABRIEL_HISTORY, FIXTURE_ARS_HOME_LEE, GW6_PARAMS)
        self.w = 5/7

    def test_final_xpts(self):
        # full-precision raw = 4.9966 → rounds to 5.0
        self.assertEqual(self.r["xPts"], 5.0)

    def test_xpts_raw(self):
        raw = self.r["components"]["_xPts_raw"]
        self.assertTrue(_near(raw, 4.9966, tol=5e-3), "raw=%.4f" % raw)

    def test_atk_scale(self):
        expected = _expected_atk_scale(1.290, 5, 1.5035, True)
        self.assertTrue(_near(self.r["inputs"]["atk_scale"], expected))

    def test_atk_scale_below_1(self):
        # Leeds xGC=1.29 < 1.5035; despite home advantage, atk_scale < 1
        # 0.988 = (damped_LEE_xGC / avg) × HOME_ADV
        self.assertTrue(_near(self.r["inputs"]["atk_scale"], 0.988427, tol=1e-4))

    def test_lam(self):
        expected = _expected_lam(0.810, 5, 1.680, 1.5035, 1.5720, True, self.w)
        self.assertTrue(_near(self.r["inputs"]["lam"], expected))

    def test_lam_value(self):
        self.assertTrue(_near(self.r["inputs"]["lam"], 0.979459, tol=1e-4))

    def test_p_cs(self):
        self.assertTrue(_near(self.r["inputs"]["p_cs"], math.exp(-0.979459), tol=1e-4))

    def test_xCSPts(self):
        expected = 1.0 * math.exp(self.r["inputs"]["lam"] * -1) * 6
        self.assertTrue(_near(self.r["components"]["xCSPts"], expected))

    def test_xGCDeductPts_less_than_lam_over_2(self):
        lam = self.r["inputs"]["lam"]
        ded = abs(self.r["components"]["xGCDeductPts"])
        self.assertLess(ded, lam / 2)   # exact Poisson, not λ/2 approximation

    def test_team_xgc_from_gkp(self):
        # CS lambda uses Arsenal GKP (Raya), not Gabriel's own field
        self.assertAlmostEqual(self.r["inputs"]["team_xGC"], 0.810)

    def test_opponent_id(self):
        self.assertEqual(self.r["inputs"]["opponent_id"], 13)  # Leeds team_a

    def test_confidence_high(self):
        self.assertEqual(self.r["confidence"], "HIGH")

    def test_v01_was_higher(self):
        self.assertLess(self.r["xPts"], 5.3)


# ─────────────────────────────────────────────────────────────────────────────
# SAKA  V0.2 GW6 — MID Arsenal home vs Leeds
# V0.1 xPts=6.3   V0.2 xPts=5.9
# Uses same fixture/team context as Gabriel (same GKP, same opponent)
# ─────────────────────────────────────────────────────────────────────────────

class TestSakaGW6(unittest.TestCase):

    def setUp(self):
        self.r = compute_xpts(SAKA, SAKA_HISTORY, FIXTURE_ARS_HOME_LEE, GW6_PARAMS)
        self.w = 5/7
        self.xMins = 0.65*(89+90+80)/3 + 0.35*416/5

    def test_final_xpts(self):
        # full-precision raw = 5.9033 → rounds to 5.9
        self.assertEqual(self.r["xPts"], 5.9)

    def test_xpts_raw(self):
        raw = self.r["components"]["_xPts_raw"]
        self.assertTrue(_near(raw, 5.9033, tol=5e-3), "raw=%.4f" % raw)

    def test_atk_scale_same_as_gabriel(self):
        # Same fixture for both Arsenal players
        self.assertTrue(_near(self.r["inputs"]["atk_scale"], 0.988427, tol=1e-4))

    def test_lam_same_as_gabriel(self):
        # Both Arsenal → same team GKP, same opponent
        self.assertTrue(_near(self.r["inputs"]["lam"], 0.979459, tol=1e-4))

    def test_p60plus_not_1(self):
        # Saka was sub'd off in GW1 and GW5 → P(60+) < 1
        self.assertLess(self.r["inputs"]["P_60plus"], 1.0)
        self.assertGreater(self.r["inputs"]["P_60plus"], 0.85)

    def test_xAppPts_less_than_2(self):
        self.assertLess(self.r["components"]["xAppPts"], 2.0)

    def test_xCSPts_small_for_mid(self):
        # MID = 1 pt for CS; fraction of expected
        self.assertGreater(self.r["components"]["xCSPts"], 0.0)
        self.assertLess(self.r["components"]["xCSPts"], 1.0)

    def test_no_gc_deduction_for_mid(self):
        self.assertEqual(self.r["components"]["xGCDeductPts"], 0.0)

    def test_gkp_lambda_not_own_xgc(self):
        # Saka's own xGC_p90=0.77; engine must use Arsenal GKP (0.81)
        self.assertAlmostEqual(self.r["inputs"]["team_xGC"], 0.810)
        self.assertNotAlmostEqual(self.r["inputs"]["team_xGC"], 0.77)

    def test_confidence_high(self):
        self.assertEqual(self.r["confidence"], "HIGH")

    def test_v01_was_higher(self):
        self.assertLess(self.r["xPts"], 6.3)


# ─────────────────────────────────────────────────────────────────────────────
# V0.2 FIXTURE MODEL TESTS
# ─────────────────────────────────────────────────────────────────────────────

def _haaland_fixture(fixture):
    return compute_xpts(HAALAND, HAALAND_HISTORY, fixture, GW6_PARAMS)

def _gabriel_fixture(fixture):
    return compute_xpts(GABRIEL, GABRIEL_HISTORY, fixture, GW6_PARAMS)


class TestStrongAttackWeakDefence(unittest.TestCase):
    """Man City home vs Crystal Palace — worst defence in league."""

    def setUp(self):
        # CRY xGC=2.160 (n=3 starts, small sample → damped)
        self.fx = {"is_home": True, "event": 6, "team_h": 15, "team_a": 8}
        self.r  = _haaland_fixture(self.fx)

    def test_atk_scale_well_above_1(self):
        # CRY xGC=2.16 >> avg 1.50; even damped it's >> neutral
        self.assertGreater(self.r["inputs"]["atk_scale"], 1.20)

    def test_atk_scale_value(self):
        # damped CRY xGC = 0.6×2.16 + 0.4×1.5035 = 1.2975 + 0.6014 = 1.8975 (approx)
        # Actually: w=3/5=0.6: 0.6×2.16 + 0.4×1.5035 = 1.296+0.6014 = 1.8974
        # atk_scale = (1.8974/1.5035) × 1.10 = 1.262×1.10 = 1.388
        self.assertTrue(_near(self.r["inputs"]["atk_scale"], 1.3882, tol=1e-3))

    def test_xpts_higher_than_vs_decent_defence(self):
        # vs LIV away (5.9), vs CRY home must be significantly higher
        self.assertGreater(self.r["xPts"], 7.0)

    def test_small_sample_damping_applied(self):
        # CRY has n=3 starts → w_opp=3/5=0.60, pulled toward league avg
        # Without damping: (2.16/1.5035)×1.10 = 1.581; with damping: 1.388
        self.assertLess(self.r["inputs"]["atk_scale"], 1.50)   # damping constrains outlier


class TestWeakAttackStrongDefence(unittest.TestCase):
    """Ipswich away at Arsenal — best defence in league."""

    def setUp(self):
        # IPS player: FWD, xG_p90=0.10, away at ARS (xGC=0.81, best defence)
        self.ips_fwd = {**HAALAND,
                        "id": 999, "team": 12,  # IPS
                        "expected_goals_per_90": 0.10,
                        "expected_assists_per_90": 0.04,
                        "expected_goals_conceded_per_90": 1.68,
                        "bonus": 2, "starts": 4}
        self.fx = {"is_home": False, "event": 6, "team_h": 1, "team_a": 12}
        self.r  = compute_xpts(self.ips_fwd, HAALAND_HISTORY, self.fx, GW6_PARAMS)

    def test_atk_scale_well_below_1(self):
        # ARS xGC=0.81 << avg 1.50; also away → 1/HOME_ADV
        # (0.714×0.81 + 0.286×1.5035) / 1.5035 × (1/1.10) = 1.008/1.5035 × 0.909 = 0.610
        self.assertLess(self.r["inputs"]["atk_scale"], 0.70)

    def test_atk_scale_value(self):
        self.assertTrue(_near(self.r["inputs"]["atk_scale"], 0.6096, tol=1e-3))

    def test_xgoalpts_low(self):
        self.assertLess(self.r["components"]["xGoalPts"], 1.2)


class TestEvenlyMatchedFixture(unittest.TestCase):
    """Liverpool home vs Man United — both similar overall strength."""

    def setUp(self):
        # LIV vs MUN: both xGC around 1.2-1.4 (decent defences)
        # atk_scale for LIV home: (damped MUN xGC / avg) × HOME_ADV
        # MUN xGC=1.360, n=5 → w=5/7 → damped=0.714×1.36+0.286×1.5035=1.402
        # atk_scale = (1.402/1.5035)×1.10 = 1.026
        self.fx = {"is_home": True, "event": 6, "team_h": 14, "team_a": 16}
        self.r  = compute_xpts(HAALAND, HAALAND_HISTORY, self.fx, GW6_PARAMS)

    def test_atk_scale_near_home_advantage(self):
        # Near-neutral opponent → atk_scale ≈ HOME_ADV = 1.10
        scale = self.r["inputs"]["atk_scale"]
        self.assertGreater(scale, 0.90)
        self.assertLess(scale, 1.20)

    def test_atk_scale_value(self):
        self.assertTrue(_near(self.r["inputs"]["atk_scale"], 1.025008, tol=1e-3))


class TestHomeVsAway(unittest.TestCase):
    """Home atk_scale / away atk_scale = HOME_ADV^2 (same opponent, same team)."""

    def setUp(self):
        # Same arsenal team facing same MUN opponent, home vs away
        self.fx_home = {"is_home": True,  "event": 6, "team_h": 1,  "team_a": 16}
        self.fx_away = {"is_home": False, "event": 6, "team_h": 16, "team_a": 1}
        self.r_home  = _gabriel_fixture(self.fx_home)
        self.r_away  = _gabriel_fixture(self.fx_away)

    def test_home_atk_scale_higher(self):
        self.assertGreater(self.r_home["inputs"]["atk_scale"],
                           self.r_away["inputs"]["atk_scale"])

    def test_ratio_equals_home_adv_squared(self):
        ratio = (self.r_home["inputs"]["atk_scale"] /
                 self.r_away["inputs"]["atk_scale"])
        self.assertTrue(_near(ratio, HOME_ADV ** 2, tol=1e-4),
                        "ratio=%.6f expected=%.4f" % (ratio, HOME_ADV**2))

    def test_home_xpts_higher_than_away(self):
        self.assertGreater(self.r_home["xPts"], self.r_away["xPts"])


class TestSmallSampleDamping(unittest.TestCase):
    """Crystal Palace (n=3 GKP starts) is pulled toward league average."""

    def test_cry_damped_less_extreme_than_raw(self):
        # Raw CRY atk_scale (home, no damping): (2.16/1.5035)×1.10 ≈ 1.581
        # Damped (n=3, w=0.6): ≈ 1.388
        # Confirm damped < raw
        fx_cry = {"is_home": True, "event": 6, "team_h": 1, "team_a": 8}
        r = _gabriel_fixture(fx_cry)
        scale_damped = r["inputs"]["atk_scale"]
        raw_ratio   = (2.160 / 1.5035) * HOME_ADV
        self.assertLess(scale_damped, raw_ratio)

    def test_fully_damped_fallback(self):
        # Opponent with n=0 starts → w=0 → opp_xgc_d = league_avg → atk_scale = HOME_ADV
        params_no_starts = {**GW6_PARAMS,
                            "team_gkp_xgc":    {**GW6_PARAMS["team_gkp_xgc"],    99: 2.5},
                            "team_gkp_starts": {**GW6_PARAMS["team_gkp_starts"], 99: 0}}
        fx = {"is_home": True, "event": 6, "team_h": 1, "team_a": 99}
        r  = _gabriel_fixture.__wrapped__(fx) if hasattr(_gabriel_fixture,'__wrapped__') \
             else compute_xpts(GABRIEL, GABRIEL_HISTORY, fx, params_no_starts)
        # n=0 → opp_xgc_d = league_avg → atk_scale = HOME_ADV
        self.assertTrue(_near(r["inputs"]["atk_scale"], HOME_ADV, tol=1e-4))


class TestMissingOpponentFallback(unittest.TestCase):
    """Unknown opponent_id → neutral (league-average opponent)."""

    def test_missing_team_fields_gives_neutral(self):
        # Fixture without team_h/team_a → opponent_id=None → neutral opponent
        r = compute_xpts(GABRIEL, GABRIEL_HISTORY, FIXTURE_HOME_NEUTRAL, GW6_PARAMS)
        # Neutral home: atk_scale = HOME_ADV × (league_avg/league_avg) = HOME_ADV
        self.assertTrue(_near(r["inputs"]["atk_scale"], HOME_ADV, tol=1e-4))
        self.assertIsNone(r["inputs"]["opponent_id"])

    def test_unknown_team_id_uses_league_avg(self):
        # team_a=999 not in params → falls back to league_avg_xgc
        fx = {"is_home": True, "event": 6, "team_h": 1, "team_a": 999}
        r  = compute_xpts(GABRIEL, GABRIEL_HISTORY, fx, GW6_PARAMS)
        # opp_xgc_raw = league_avg; n=0 → no damping; atk_scale = HOME_ADV
        self.assertTrue(_near(r["inputs"]["atk_scale"], HOME_ADV, tol=1e-4))

    def test_neutral_away_atk_scale(self):
        r = compute_xpts(GABRIEL, GABRIEL_HISTORY, FIXTURE_AWAY_NEUTRAL, GW6_PARAMS)
        self.assertTrue(_near(r["inputs"]["atk_scale"], 1.0 / HOME_ADV, tol=1e-4))


class TestNoFixtureAdjDoubleCount(unittest.TestCase):
    """FIXTURE_ADJ must not exist. Difficulty must not affect output."""

    def test_fixture_adj_not_in_module(self):
        import fpl.engine.projection as proj
        self.assertFalse(hasattr(proj, "FIXTURE_ADJ"),
                         "FIXTURE_ADJ found — must be removed in V0.2")

    def test_changing_difficulty_does_not_change_xpts(self):
        # V0.2 does not use fixture["difficulty"] in any calculation
        fx_d2 = {**FIXTURE_ARS_HOME_LEE, "difficulty": 2}
        fx_d5 = {**FIXTURE_ARS_HOME_LEE, "difficulty": 5}
        r2 = compute_xpts(GABRIEL, GABRIEL_HISTORY, fx_d2, GW6_PARAMS)
        r5 = compute_xpts(GABRIEL, GABRIEL_HISTORY, fx_d5, GW6_PARAMS)
        self.assertEqual(r2["xPts"], r5["xPts"])
        self.assertEqual(r2["components"], r5["components"])

    def test_atk_scale_not_atk_factor_in_inputs(self):
        r = compute_xpts(GABRIEL, GABRIEL_HISTORY, FIXTURE_ARS_HOME_LEE, GW6_PARAMS)
        self.assertIn("atk_scale", r["inputs"])
        self.assertNotIn("atk_factor", r["inputs"])
        self.assertNotIn("cs_factor",  r["inputs"])


class TestHighUpsideFixture(unittest.TestCase):
    """Haaland vs CRY at home should produce highest xPts in league."""

    def test_haaland_vs_cry_home_highest(self):
        # Crystal Palace (best attacking fixture); Man City home
        fx_cry = {"is_home": True, "event": 6, "team_h": 15, "team_a": 8}
        r_cry  = _haaland_fixture(fx_cry)
        r_liv  = compute_xpts(HAALAND, HAALAND_HISTORY, FIXTURE_MCI_AWAY_LIV, GW6_PARAMS)
        # Home vs best attacking fixture should massively beat away vs decent defence
        self.assertGreater(r_cry["xPts"], r_liv["xPts"] + 1.5)

    def test_xpts_exceeds_7(self):
        fx = {"is_home": True, "event": 6, "team_h": 15, "team_a": 8}
        r  = _haaland_fixture(fx)
        self.assertGreater(r["xPts"], 7.0)

    def test_ordering_cry_sun_liv_ars(self):
        # Fixture ranking (home, hardest to easiest for attackers):
        # ARS (best def) < LIV (decent def away) < SUN < CRY (worst def)
        # Haaland home vs each:
        results = {}
        for opp_tid, label in [(1,"ARS"),(14,"LIV"),(20,"SUN"),(8,"CRY")]:
            fx = {"is_home": True, "event": 6, "team_h": 15, "team_a": opp_tid}
            results[label] = compute_xpts(HAALAND, HAALAND_HISTORY, fx, GW6_PARAMS)["xPts"]
        self.assertLess(results["ARS"], results["LIV"])
        self.assertLess(results["LIV"], results["SUN"])
        self.assertLess(results["SUN"], results["CRY"])


class TestDiscriminationRanking(unittest.TestCase):
    """
    Discrimination test: V0.2 atk_scale ranking matches fixture quality ranking.

    We test Spearman correlation conceptually by verifying monotone ordering
    of atk_scale across clearly ranked fixture pairs.  Full Spearman across a
    full season of predictions vs actuals should be computed post-GW10.

    Prediction unit:  atk_scale (fixture attacking environment)
    Outcome proxy:    team GKP xGC_p90 (opponent concession rate — what V0.2 is estimating)
    """

    def test_atk_scale_monotone_with_opp_xgc(self):
        # Four opponents in ascending xGC order (harder to easier to score against):
        # ARS(0.81) < LIV(1.22) < MUN(1.36) < SUN(1.73) < CRY(2.16)
        # Corresponding home atk_scale should be in the same order.
        opps = [(1,0.81),(14,1.22),(16,1.36),(20,1.73),(8,2.16)]
        scales = []
        for opp_tid, _ in opps:
            fx = {"is_home": True, "event": 6, "team_h": 1, "team_a": opp_tid}
            r  = compute_xpts(GABRIEL, GABRIEL_HISTORY, fx, GW6_PARAMS)
            scales.append(r["inputs"]["atk_scale"])
        # Scales should be monotonically increasing
        for i in range(len(scales)-1):
            self.assertLess(scales[i], scales[i+1],
                msg="atk_scale not monotone: %s" % scales)

    def test_home_always_better_than_away_same_opponent(self):
        # For every opponent, home atk_scale > away atk_scale (HOME_ADV applies)
        for opp_tid in [8, 13, 14, 16, 20]:
            fx_h = {"is_home": True,  "event":6, "team_h":15, "team_a":opp_tid}
            fx_a = {"is_home": False, "event":6, "team_h":opp_tid, "team_a":15}
            r_h  = _haaland_fixture(fx_h)
            r_a  = _haaland_fixture(fx_a)
            self.assertGreater(r_h["inputs"]["atk_scale"],
                               r_a["inputs"]["atk_scale"],
                               msg="opp_tid=%d" % opp_tid)

    def test_mean_atk_scale_calibrated(self):
        # Mean atk_scale over all home fixtures ≈ HOME_ADV (model is unbiased)
        home_scales = []
        for opp_tid in GW6_PARAMS["team_gkp_xgc"]:
            if opp_tid == 1: continue   # skip self
            fx = {"is_home": True, "event": 6, "team_h": 1, "team_a": opp_tid}
            r  = compute_xpts(GABRIEL, GABRIEL_HISTORY, fx, GW6_PARAMS)
            home_scales.append(r["inputs"]["atk_scale"])
        mean_scale = sum(home_scales) / len(home_scales)
        # Mean should be close to HOME_ADV over a representative sample
        self.assertTrue(_near(mean_scale, HOME_ADV, tol=0.15),
                        "mean atk_scale=%.4f, HOME_ADV=%.2f" % (mean_scale, HOME_ADV))


# ─────────────────────────────────────────────────────────────────────────────
# AVAILABILITY / GENERIC MECHANICS  (unchanged from V0.1)
# ─────────────────────────────────────────────────────────────────────────────

class TestAvailabilityGates(unittest.TestCase):

    def _p(self, status, cop):
        return {**GABRIEL, "status": status, "chance_of_playing_next_round": cop}

    def test_injured_cop0(self):
        r = compute_xpts(self._p("i",0), GABRIEL_HISTORY, FIXTURE_ARS_HOME_LEE, GW6_PARAMS)
        self.assertEqual(r["xPts"], 0.0)

    def test_suspended_cop0(self):
        r = compute_xpts(self._p("s",0), GABRIEL_HISTORY, FIXTURE_ARS_HOME_LEE, GW6_PARAMS)
        self.assertEqual(r["xPts"], 0.0)

    def test_unavailable(self):
        r = compute_xpts(self._p("u",None), GABRIEL_HISTORY, FIXTURE_ARS_HOME_LEE, GW6_PARAMS)
        self.assertEqual(r["xPts"], 0.0)

    def test_blank_gw(self):
        r = compute_xpts(HAALAND, HAALAND_HISTORY, None, GW6_PARAMS)
        self.assertEqual(r["xPts"], 0.0)
        self.assertIn("Blank", r["risks"][0])

    def test_doubt_75_reduces_xpts(self):
        doubt = {**GABRIEL, "status":"d", "chance_of_playing_next_round":75}
        r_doubt = compute_xpts(doubt, GABRIEL_HISTORY, FIXTURE_ARS_HOME_LEE, GW6_PARAMS)
        r_full  = compute_xpts(GABRIEL, GABRIEL_HISTORY, FIXTURE_ARS_HOME_LEE, GW6_PARAMS)
        self.assertLess(r_doubt["xPts"], r_full["xPts"])
        self.assertAlmostEqual(r_doubt["inputs"]["P_app"], 0.75)


class TestScoringMechanics(unittest.TestCase):

    def test_fwd_no_cs_no_gc(self):
        r = compute_xpts(HAALAND, HAALAND_HISTORY, FIXTURE_MCI_AWAY_LIV, GW6_PARAMS)
        self.assertEqual(r["components"]["xCSPts"],       0.0)
        self.assertEqual(r["components"]["xGCDeductPts"], 0.0)

    def test_def_has_cs_and_gc(self):
        r = compute_xpts(GABRIEL, GABRIEL_HISTORY, FIXTURE_ARS_HOME_LEE, GW6_PARAMS)
        self.assertGreater(r["components"]["xCSPts"],   0.0)
        self.assertLess(r["components"]["xGCDeductPts"], 0.0)

    def test_mid_has_small_cs(self):
        r = compute_xpts(SAKA, SAKA_HISTORY, FIXTURE_ARS_HOME_LEE, GW6_PARAMS)
        self.assertGreater(r["components"]["xCSPts"], 0.0)
        self.assertLess(r["components"]["xCSPts"],    1.0)

    def test_easier_fixture_higher_xpts(self):
        # Same player: vs CRY (weak def) should beat vs LIV (decent def)
        fx_easy = {"is_home":True, "event":6, "team_h":15, "team_a":8}
        fx_hard = {"is_home":True, "event":6, "team_h":15, "team_a":14}
        r_easy  = _haaland_fixture(fx_easy)
        r_hard  = _haaland_fixture(fx_hard)
        self.assertGreater(r_easy["xPts"], r_hard["xPts"])

    def test_component_sum_equals_raw(self):
        for player, history, fixture in [
            (HAALAND, HAALAND_HISTORY, FIXTURE_MCI_AWAY_LIV),
            (GABRIEL, GABRIEL_HISTORY, FIXTURE_ARS_HOME_LEE),
            (SAKA,    SAKA_HISTORY,    FIXTURE_ARS_HOME_LEE),
        ]:
            r = compute_xpts(player, history, fixture, GW6_PARAMS)
            c = r["components"]
            s = (c["xAppPts"]+c["xGoalPts"]+c["xAssistPts"]
                 +c["xCSPts"]+c["xGCDeductPts"]+c["xSavePts"]+c["xBonus"])
            self.assertTrue(_near(c["_xPts_raw"], s, tol=1e-5),
                            "%s: sum=%.8f raw=%.8f" % (player["web_name"], s, c["_xPts_raw"]))

    def test_xpts_non_negative(self):
        for p,h,f in [(HAALAND,HAALAND_HISTORY,FIXTURE_MCI_AWAY_LIV),
                      (GABRIEL,GABRIEL_HISTORY,FIXTURE_ARS_HOME_LEE),
                      (SAKA,SAKA_HISTORY,FIXTURE_ARS_HOME_LEE)]:
            self.assertGreaterEqual(compute_xpts(p,h,f,GW6_PARAMS)["xPts"], 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
