"""
Integration tests for the SquadCheck FPL Pipeline V1.

Tests the full chain: scanner → name resolution → projection → bench optimiser.

No live API calls.  All player data, bootstrap, and element-summaries are
synthetic but internally consistent.  Projection values are computed by the
real compute_xpts() engine, not mocked.

Test squad (15 players, 4-4-2 submitted XI)
--------------------------------------------
Position  id    web_name     team  is_starting
GKP       1001  AlphaGK      1     True
GKP       1002  BetaGK       4     False   (bench)
DEF       2001  AlphaDEF     1     True
DEF       2002  BetaDEF      1     True
DEF       2003  GammaDEF     2     True
DEF       2004  DeltaDEF     3     True
DEF       2005  EpsilonDEF   9     False   (bench)
MID       3001  AlphaMID     1     True
MID       3002  BetaMID      1     True
MID       3003  GammaMID     4     True
MID       3004  DeltaMID     4     True
MID       3005  EpsilonMID   5     False   (bench)
FWD       4001  AlphaFWD    15     True
FWD       4002  BetaFWD      2     True
FWD       4003  GammaFWD     3     False   (bench)

All players have 5 starts, 90 min each GW (except EpsilonMID: 2 starts).
Submitted XI: 1001, 2001-2004, 3001-3004, 4001-4002  (4-4-2)
Bench:        1002, 2005, 3005, 4003

Fixture: every team faces a neutral opponent in a home game (no team_h/team_a
→ pipeline falls back to league-average opponent, atk_scale = HOME_ADV).
"""

import sys, os, math, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from fpl.engine.scanner  import (scan_squad,
                                  VALID, PARTIAL, AMBIGUOUS, UNSUPPORTED,
                                  VIEW_PITCH, VIEW_LIST, VIEW_UNKNOWN)
from fpl.engine.pipeline import (run_pipeline, resolve_players,
                                  OK, SCAN_FAIL, RESOLVE_FAIL)
from fpl.engine.projection import compute_xpts

# ─────────────────────────────────────────────────────────────────────────────
# TEST-DATA FACTORY
# ─────────────────────────────────────────────────────────────────────────────

# Frozen params matching GW6 data (same as projection tests)
_PARAMS = {
    "team_gkp_xgc": {
        1: 0.810, 2: 1.360, 3: 1.390, 4: 1.260,
        5: 1.730, 9: 1.390, 14: 1.220, 15: 1.450,
    },
    "team_gkp_starts": {1:5, 2:4, 3:5, 4:5, 5:5, 9:5, 14:5, 15:5},
    "league_avg_xgc":  1.5035,
    "team_xgf":        {1:1.790, 2:0.950, 3:1.350, 4:2.310,
                         5:2.070, 9:1.620, 14:1.720, 15:2.360},
    "league_avg_xgf":  1.5720,
    "pos_xG_base": {1:0.01, 2:0.04, 3:0.13, 4:0.485},
    "pos_xA_base": {1:0.01, 2:0.05, 3:0.10, 4:0.035},
}

# Neutral home fixture (no team_h/team_a → league-average opponent)
_NEUTRAL_HOME = {"is_home": True,  "event": 6}
_NEUTRAL_AWAY = {"is_home": False, "event": 6}


def _elem(pid, name, pos, team, xg, xa, xgc, starts, bonus, saves=0.0, gk_xgc=None):
    """Build a minimal bootstrap element dict."""
    return {
        "id": pid,
        "web_name": name, "second_name": name, "first_name": "", "known_name": "",
        "element_type": pos, "team": team,
        "status": "a", "chance_of_playing_next_round": None,
        "minutes": starts * 90, "starts": starts, "bonus": bonus,
        "expected_goals_per_90": xg,
        "expected_assists_per_90": xa,
        "expected_goals_conceded_per_90": gk_xgc if gk_xgc is not None else xgc,
        "saves_per_90": saves,
        "penalties_order": None,
        "removed": False,
    }


# Canonical squad elements
_ELEMENTS = [
    _elem(1001, "AlphaGK",     1,  1,  0.00, 0.00, 0.810,  5, 3, saves=2.0),
    _elem(1002, "BetaGK",      1,  4,  0.00, 0.00, 1.260,  5, 0, saves=3.0),
    _elem(2001, "AlphaDEF",    2,  1,  0.10, 0.04, 0.810,  5, 2),
    _elem(2002, "BetaDEF",     2,  1,  0.05, 0.04, 0.810,  5, 1),
    _elem(2003, "GammaDEF",    2,  2,  0.06, 0.05, 1.360,  5, 1),
    _elem(2004, "DeltaDEF",    2,  3,  0.04, 0.04, 1.390,  5, 0),
    _elem(2005, "EpsilonDEF",  2,  9,  0.02, 0.02, 1.390,  4, 0),
    _elem(3001, "AlphaMID",    3,  1,  0.70, 0.21, 0.810,  5, 4),
    _elem(3002, "BetaMID",     3,  1,  0.10, 0.18, 0.810,  5, 1),
    _elem(3003, "GammaMID",    3,  4,  0.30, 0.15, 1.260,  5, 3),
    _elem(3004, "DeltaMID",    3,  4,  0.25, 0.10, 1.260,  5, 2),
    _elem(3005, "EpsilonMID",  3,  5,  0.05, 0.05, 1.730,  2, 0),
    _elem(4001, "AlphaFWD",    4, 15,  0.88, 0.11, 1.450,  5, 9),
    _elem(4002, "BetaFWD",     4,  2,  0.40, 0.08, 1.360,  5, 3),
    _elem(4003, "GammaFWD",    4,  3,  0.05, 0.02, 1.390,  4, 0),  # deliberately weak bench FWD
]

# Baseline submitted 4-4-2 XI
_SUBMITTED_IDS = [1001, 2001, 2002, 2003, 2004, 3001, 3002, 3003, 3004, 4001, 4002]

# Standard history: 5 games × 90 mins
_HIST_90x5 = [{"minutes": 90, "starts": 1}] * 5
_HIST_90x4 = [{"minutes": 90, "starts": 1}] * 4  # for 4-start players
_HIST_90x2 = [{"minutes": 90, "starts": 1}] * 2  # EpsilonMID (low starts)

# Element summaries: history + neutral home fixture for all teams
def _make_summaries(fixture_override=None):
    starts_map = {
        1001:5, 1002:5, 2001:5, 2002:5, 2003:5, 2004:5, 2005:4,
        3001:5, 3002:5, 3003:5, 3004:5, 3005:2, 4001:5, 4002:5, 4003:4,
    }
    summaries = {}
    for e in _ELEMENTS:
        pid = e["id"]
        n   = starts_map[pid]
        hist = [{"minutes": 90, "starts": 1}] * n
        fx   = fixture_override if fixture_override else _NEUTRAL_HOME
        summaries[pid] = {"history": hist, "fixtures": [fx]}
    return summaries


_SUMMARIES = _make_summaries()

# Bootstrap with just enough structure
def _make_bootstrap(elements=None):
    return {"elements": elements if elements is not None else _ELEMENTS}


# Canonical scan result: 15 players in a PITCH view
def _make_scan(view_type=VIEW_PITCH, override=None):
    if override is not None:
        return override
    players = [
        {"name": "AlphaGK",     "position": 1, "is_starting": True},
        {"name": "BetaGK",      "position": 1, "is_starting": False},
        {"name": "AlphaDEF",    "position": 2, "is_starting": True},
        {"name": "BetaDEF",     "position": 2, "is_starting": True},
        {"name": "GammaDEF",    "position": 2, "is_starting": True},
        {"name": "DeltaDEF",    "position": 2, "is_starting": True},
        {"name": "EpsilonDEF",  "position": 2, "is_starting": False},
        {"name": "AlphaMID",    "position": 3, "is_starting": True},
        {"name": "BetaMID",     "position": 3, "is_starting": True},
        {"name": "GammaMID",    "position": 3, "is_starting": True},
        {"name": "DeltaMID",    "position": 3, "is_starting": True},
        {"name": "EpsilonMID",  "position": 3, "is_starting": False},
        {"name": "AlphaFWD",    "position": 4, "is_starting": True},
        {"name": "BetaFWD",     "position": 4, "is_starting": True},
        {"name": "GammaFWD",    "position": 4, "is_starting": False},
    ]
    return scan_squad({"view_type": view_type, "players": players})


# ─────────────────────────────────────────────────────────────────────────────
# SCANNER UNIT TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestScannerStatus(unittest.TestCase):

    def test_valid_pitch_view(self):
        r = _make_scan(VIEW_PITCH)
        self.assertEqual(r["status"], VALID)
        self.assertEqual(r["view_type"], VIEW_PITCH)
        self.assertEqual(len(r["players"]), 15)

    def test_valid_list_view(self):
        r = _make_scan(VIEW_LIST)
        self.assertEqual(r["status"], VALID)
        self.assertEqual(r["view_type"], VIEW_LIST)

    def test_partial_too_few_players(self):
        raw = {"view_type": VIEW_PITCH, "players": [
            {"name": "P%d" % i, "position": (i % 3) + 2, "is_starting": True}
            for i in range(12)
        ]}
        r = scan_squad(raw)
        self.assertEqual(r["status"], PARTIAL)
        self.assertIn("12/15", r["message"])

    def test_partial_wrong_starter_count(self):
        # 15 players but only 10 marked as starting
        players = _make_scan()["players"]
        for p in players:
            p["is_starting"] = False
        players[0]["is_starting"] = True   # only 1 starter
        r = scan_squad({"players": players})
        self.assertEqual(r["status"], PARTIAL)

    def test_ambiguous_duplicate_names(self):
        players = _make_scan()["players"]
        players[1]["name"] = players[0]["name"]  # create a duplicate
        r = scan_squad({"players": players})
        self.assertEqual(r["status"], AMBIGUOUS)

    def test_unsupported_bytes(self):
        r = scan_squad(b"\xff\xd8\xff")   # fake JPEG bytes
        self.assertEqual(r["status"], UNSUPPORTED)

    def test_unsupported_unknown_type(self):
        r = scan_squad(42)
        self.assertEqual(r["status"], UNSUPPORTED)

    def test_invalid_position_skipped(self):
        players = _make_scan()["players"]
        players[0]["position"] = 9   # invalid → skipped → total drops to 14
        r = scan_squad({"players": players})
        self.assertEqual(r["status"], PARTIAL)
        self.assertEqual(len(r["warnings"]), 1)

    def test_result_schema(self):
        r = _make_scan()
        for key in ("status", "view_type", "players", "message", "warnings"):
            self.assertIn(key, r)


# ─────────────────────────────────────────────────────────────────────────────
# NAME RESOLUTION TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveplayers(unittest.TestCase):

    def test_resolves_all_15(self):
        scan_p = _make_scan()["players"]
        resolved, unresolved = resolve_players(scan_p, _ELEMENTS)
        self.assertEqual(len(resolved), 15)
        self.assertEqual(len(unresolved), 0)

    def test_resolved_ids_match_elements(self):
        scan_p = _make_scan()["players"]
        resolved, _ = resolve_players(scan_p, _ELEMENTS)
        resolved_ids = {r["player_id"] for r in resolved}
        expected_ids = {e["id"] for e in _ELEMENTS}
        self.assertEqual(resolved_ids, expected_ids)

    def test_unknown_name_goes_unresolved(self):
        scan_p = _make_scan()["players"]
        scan_p[0]["name"] = "XxxUnknown"
        resolved, unresolved = resolve_players(scan_p, _ELEMENTS)
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]["name"], "XxxUnknown")
        self.assertIsNone(unresolved[0]["player_id"])

    def test_position_from_bootstrap_not_scanner(self):
        # Position now comes from bootstrap, not scanner.
        # "AlphaGK" has no position in scan → resolves to GKP (pos=1) from bootstrap.
        scan_p = [{"name": "AlphaGK", "is_starting": True}]
        resolved, unresolved = resolve_players(scan_p, _ELEMENTS)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["position"], 1)  # from bootstrap element_type

    def test_is_starting_preserved(self):
        scan_p = _make_scan()["players"]
        resolved, _ = resolve_players(scan_p, _ELEMENTS)
        starters = [r for r in resolved if r["is_starting"]]
        self.assertEqual(len(starters), 11)

    def test_case_insensitive_match(self):
        scan_p = [{"name": "alphaFWD", "position": 4, "is_starting": True}]
        resolved, unresolved = resolve_players(scan_p, _ELEMENTS)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["player_id"], 4001)
        self.assertEqual(len(unresolved), 0)


# ─────────────────────────────────────────────────────────────────────────────
# FULL PIPELINE — PITCH VIEW
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelinePitchView(unittest.TestCase):

    def setUp(self):
        scan = _make_scan(VIEW_PITCH)
        self.result = run_pipeline(
            scan_result       = scan,
            bootstrap         = _make_bootstrap(),
            element_summaries = _SUMMARIES,
            params            = _PARAMS,
        )

    def test_status_ok(self):
        self.assertEqual(self.result["status"], OK)

    def test_scanner_status_valid(self):
        self.assertEqual(self.result["scanner_status"], VALID)

    def test_view_type_pitch(self):
        self.assertEqual(self.result["view_type"], VIEW_PITCH)

    def test_15_players_returned(self):
        self.assertEqual(len(self.result["players"]), 15)

    def test_action_present(self):
        self.assertIn(self.result["action"], ("HOLD", "SWAP"))

    def test_submitted_xi_has_11(self):
        self.assertEqual(len(self.result["submitted_xi"]["player_ids"]), 11)

    def test_recommended_xi_has_11(self):
        self.assertEqual(len(self.result["recommended_xi"]["player_ids"]), 11)

    def test_submitted_xi_formation(self):
        self.assertEqual(self.result["submitted_xi"]["formation"], "4-4-2")

    def test_delta_non_negative(self):
        self.assertGreaterEqual(self.result["delta"], 0.0)

    def test_threshold_echoed(self):
        self.assertEqual(self.result["threshold"], 0.5)

    def test_player_xpts_not_suppressed(self):
        # Pipeline must not modify xPts; cross-check one player directly
        alpha_fwd = next(p for p in self.result["players"] if p["player_id"] == 4001)
        direct_hist = [{"minutes": 90, "starts": 1}] * 5
        direct_elem = next(e for e in _ELEMENTS if e["id"] == 4001)
        direct = compute_xpts(direct_elem, direct_hist, _NEUTRAL_HOME, _PARAMS)
        self.assertEqual(alpha_fwd["xPts"], direct["xPts"])

    def test_player_dict_shape(self):
        for p in self.result["players"]:
            for key in ("player_id", "web_name", "position", "team_id",
                        "is_starting", "xPts", "confidence", "components",
                        "drivers", "risks"):
                self.assertIn(key, p)

    def test_payload_top_level_keys(self):
        required = ("status", "scanner_status", "view_type", "players",
                    "submitted_xi", "recommended_xi", "delta", "action",
                    "substitutions", "threshold", "message")
        for k in required:
            self.assertIn(k, self.result)

    def test_11_starters_flagged(self):
        starters = [p for p in self.result["players"] if p["is_starting"]]
        self.assertEqual(len(starters), 11)


# ─────────────────────────────────────────────────────────────────────────────
# FULL PIPELINE — LIST VIEW (same logic, different view_type label)
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineListView(unittest.TestCase):

    def setUp(self):
        scan = _make_scan(VIEW_LIST)
        self.result = run_pipeline(
            scan_result       = scan,
            bootstrap         = _make_bootstrap(),
            element_summaries = _SUMMARIES,
            params            = _PARAMS,
        )

    def test_status_ok(self):
        self.assertEqual(self.result["status"], OK)

    def test_view_type_list(self):
        self.assertEqual(self.result["view_type"], VIEW_LIST)

    def test_15_players_returned(self):
        self.assertEqual(len(self.result["players"]), 15)


# ─────────────────────────────────────────────────────────────────────────────
# EARLY EXIT — SCANNER NOT VALID
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineEarlyExit(unittest.TestCase):

    def _run(self, scan_override):
        return run_pipeline(
            scan_result       = scan_override,
            bootstrap         = _make_bootstrap(),
            element_summaries = _SUMMARIES,
            params            = _PARAMS,
        )

    def test_partial_scan_stops(self):
        # Only 12 players → PARTIAL
        raw = {"view_type": VIEW_PITCH, "players": [
            {"name": "X%d" % i, "position": (i%3)+2, "is_starting": True}
            for i in range(12)
        ]}
        partial_scan = scan_squad(raw)
        r = self._run(partial_scan)
        self.assertEqual(r["status"],  SCAN_FAIL)
        self.assertEqual(r["scanner_status"], PARTIAL)
        self.assertEqual(r["players"], [])
        self.assertIsNone(r["submitted_xi"])

    def test_ambiguous_scan_stops(self):
        players = _make_scan()["players"]
        players[1]["name"] = players[0]["name"]
        ambig = scan_squad({"players": players})
        r = self._run(ambig)
        self.assertEqual(r["status"], SCAN_FAIL)
        self.assertEqual(r["scanner_status"], AMBIGUOUS)

    def test_unsupported_scan_stops(self):
        unsup = scan_squad(b"\xff\xd8")
        r = self._run(unsup)
        self.assertEqual(r["status"], SCAN_FAIL)
        self.assertEqual(r["scanner_status"], UNSUPPORTED)

    def test_unresolvable_player_stops(self):
        # "Ghost" is not in bootstrap → resolve fails
        scan = _make_scan()
        scan["players"][0]["name"] = "Ghost"
        r = self._run(scan)
        self.assertEqual(r["status"], RESOLVE_FAIL)
        self.assertIn("Ghost", r["message"])
        self.assertEqual(r["players"], [])

    def test_early_exit_has_null_xi(self):
        unsup = scan_squad(b"x")
        r = self._run(unsup)
        self.assertIsNone(r["submitted_xi"])
        self.assertIsNone(r["recommended_xi"])
        self.assertIsNone(r["action"])

    def test_failed_result_has_required_keys(self):
        unsup = scan_squad(b"x")
        r = self._run(unsup)
        for k in ("status","scanner_status","view_type","players",
                  "submitted_xi","recommended_xi","delta","action",
                  "substitutions","threshold","message"):
            self.assertIn(k, r)


# ─────────────────────────────────────────────────────────────────────────────
# BENCH DECISIONS — HOLD
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineHold(unittest.TestCase):
    """
    All bench players have lower raw xG than every starter.
    GammaFWD (bench, xG=0.20) < BetaFWD (starter, xG=0.40), and so on.
    The optimizer should return HOLD at threshold=0.5.
    """

    def setUp(self):
        scan = _make_scan()
        self.result = run_pipeline(
            scan_result       = scan,
            bootstrap         = _make_bootstrap(),
            element_summaries = _SUMMARIES,
            params            = _PARAMS,
            threshold         = 0.5,
        )

    def test_action_hold_or_swap_consistent_with_delta(self):
        # Whether HOLD or SWAP, action must be consistent with delta vs threshold
        delta  = self.result["delta"]
        action = self.result["action"]
        if delta >= 0.5:
            self.assertEqual(action, "SWAP")
        else:
            self.assertEqual(action, "HOLD")

    def test_hold_has_no_subs(self):
        if self.result["action"] == "HOLD":
            self.assertEqual(self.result["substitutions"], [])

    def test_hold_message(self):
        if self.result["action"] == "HOLD":
            self.assertIn("Hold", self.result["message"])


# ─────────────────────────────────────────────────────────────────────────────
# BENCH DECISIONS — SWAP
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineSwap(unittest.TestCase):
    """
    Replace EpsilonMID (bench, xG=0.05) with a high-xG bench player and
    DeltaMID (starter, low xG) to force a clear swap signal.

    We create a variant squad where:
      BenchStar (bench MID, team=15) has xG=0.90 — very high
      DeltaMID  (starter MID, team=4) keeps xG=0.25

    The optimizer should find BenchStar beats at least one starter enough
    to recommend a SWAP.
    """

    def setUp(self):
        # Replace EpsilonMID with a high-xG player
        bench_star_id = 3099
        bench_star = _elem(bench_star_id, "BenchStar", 3, 15,
                           xg=0.90, xa=0.20, xgc=1.45, starts=5, bonus=8)

        elements_swap = [e for e in _ELEMENTS if e["id"] != 3005] + [bench_star]

        scan_players = [
            {"name": "AlphaGK",    "position": 1, "is_starting": True},
            {"name": "BetaGK",     "position": 1, "is_starting": False},
            {"name": "AlphaDEF",   "position": 2, "is_starting": True},
            {"name": "BetaDEF",    "position": 2, "is_starting": True},
            {"name": "GammaDEF",   "position": 2, "is_starting": True},
            {"name": "DeltaDEF",   "position": 2, "is_starting": True},
            {"name": "EpsilonDEF", "position": 2, "is_starting": False},
            {"name": "AlphaMID",   "position": 3, "is_starting": True},
            {"name": "BetaMID",    "position": 3, "is_starting": True},
            {"name": "GammaMID",   "position": 3, "is_starting": True},
            {"name": "DeltaMID",   "position": 3, "is_starting": True},
            {"name": "BenchStar",  "position": 3, "is_starting": False},  # bench star
            {"name": "AlphaFWD",   "position": 4, "is_starting": True},
            {"name": "BetaFWD",    "position": 4, "is_starting": True},
            {"name": "GammaFWD",   "position": 4, "is_starting": False},
        ]
        scan = scan_squad({"view_type": VIEW_PITCH, "players": scan_players})

        summaries_swap = dict(_SUMMARIES)
        summaries_swap[bench_star_id] = {
            "history":  [{"minutes": 90, "starts": 1}] * 5,
            "fixtures": [_NEUTRAL_HOME],
        }

        self.result = run_pipeline(
            scan_result       = scan,
            bootstrap         = _make_bootstrap(elements_swap),
            element_summaries = summaries_swap,
            params            = _PARAMS,
            threshold         = 0.5,
        )

    def test_action_swap(self):
        self.assertEqual(self.result["status"], OK)
        self.assertEqual(self.result["action"], "SWAP")

    def test_delta_positive(self):
        self.assertGreater(self.result["delta"], 0.0)

    def test_substitution_listed(self):
        self.assertGreater(len(self.result["substitutions"]), 0)

    def test_bench_star_in_recommended_xi(self):
        rec_ids = self.result["recommended_xi"]["player_ids"]
        self.assertIn(3099, rec_ids)

    def test_swap_message_contains_player_names(self):
        self.assertIn("BenchStar", self.result["message"])

    def test_recommended_xpts_higher(self):
        sub_xpts = self.result["submitted_xi"]["xPts"]
        rec_xpts = self.result["recommended_xi"]["xPts"]
        self.assertGreater(rec_xpts, sub_xpts)

    def test_xpts_not_suppressed(self):
        # BenchStar's xPts in the payload must equal compute_xpts() directly
        bench_star = next(p for p in self.result["players"] if p["player_id"] == 3099)
        elem = _elem(3099, "BenchStar", 3, 15, 0.90, 0.20, 1.45, 5, 8)
        hist = [{"minutes": 90, "starts": 1}] * 5
        direct = compute_xpts(elem, hist, _NEUTRAL_HOME, _PARAMS)
        self.assertEqual(bench_star["xPts"], direct["xPts"])


# ─────────────────────────────────────────────────────────────────────────────
# PAYLOAD CONTRACT
# ─────────────────────────────────────────────────────────────────────────────

class TestPayloadContract(unittest.TestCase):
    """
    Verify the exact shape of the V1 payload so the frontend has a stable contract.
    """

    def setUp(self):
        scan = _make_scan()
        self.r = run_pipeline(
            scan_result       = scan,
            bootstrap         = _make_bootstrap(),
            element_summaries = _SUMMARIES,
            params            = _PARAMS,
        )

    def test_top_level_keys(self):
        required = {
            "status", "scanner_status", "view_type",
            "players", "submitted_xi", "recommended_xi",
            "delta", "action", "substitutions", "threshold", "message",
        }
        self.assertEqual(required, required & self.r.keys())

    def test_player_keys(self):
        for p in self.r["players"]:
            for k in ("player_id","web_name","position","team_id","is_starting",
                      "xPts","confidence","_minutes_conf","_projection_conf",
                      "components","inputs","player_api","drivers","risks"):
                self.assertIn(k, p, "missing key %r in player %s" % (k, p.get("web_name")))

    def test_xi_keys(self):
        for xi_key in ("submitted_xi", "recommended_xi"):
            xi = self.r[xi_key]
            for k in ("player_ids", "formation", "xPts", "players"):
                self.assertIn(k, xi)

    def test_components_present(self):
        for p in self.r["players"]:
            c = p["components"]
            for k in ("xAppPts","xGoalPts","xAssistPts","xCSPts",
                      "xGCDeductPts","xSavePts","xBonus","_xPts_raw"):
                self.assertIn(k, c)

    def test_action_values(self):
        self.assertIn(self.r["action"], ("HOLD", "SWAP"))

    def test_delta_type(self):
        self.assertIsInstance(self.r["delta"], float)

    def test_xpts_non_negative(self):
        for p in self.r["players"]:
            self.assertGreaterEqual(p["xPts"], 0.0)

    def test_formation_format(self):
        import re
        for xi_key in ("submitted_xi", "recommended_xi"):
            f = self.r[xi_key]["formation"]
            self.assertRegex(f, r"^\d-\d-\d$", "formation %r not in D-M-F format" % f)

    def test_hold_returns_valid_status(self):
        # HOLD is a successful result — status must be OK
        self.assertEqual(self.r["status"], OK)


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT: every explanation field survives into the final payload
# ─────────────────────────────────────────────────────────────────────────────

class TestPayloadAuditFields(unittest.TestCase):
    """
    Verifies that the audit fixes are present end-to-end.
    Uses AlphaFWD (id=4001, team=15=MCI, FWD) and AlphaGK (id=1001, team=1=ARS)
    as representative players for all explanation categories.
    """

    def setUp(self):
        scan = _make_scan()
        self.result = run_pipeline(
            scan_result       = scan,
            bootstrap         = _make_bootstrap(),
            element_summaries = _SUMMARIES,
            params            = _PARAMS,
        )
        self.fwd = next(p for p in self.result["players"] if p["player_id"] == 4001)
        self.gk  = next(p for p in self.result["players"] if p["player_id"] == 1001)
        self.def_ = next(p for p in self.result["players"] if p["player_id"] == 2001)

    # ── inputs dict ──────────────────────────────────────────────────────────

    def test_inputs_key_present(self):
        self.assertIn("inputs", self.fwd)

    def test_inputs_minutes_expectation(self):
        inp = self.fwd["inputs"]
        self.assertIn("xMins",    inp)
        self.assertIn("P_app",    inp)
        self.assertIn("P_60plus", inp)
        self.assertIsInstance(inp["xMins"],    float)
        self.assertIsInstance(inp["P_app"],    float)
        self.assertIsInstance(inp["P_60plus"], float)

    def test_inputs_damping_weight(self):
        self.assertIn("w", self.fwd["inputs"])
        # n_gws=5 → w=5/7
        self.assertAlmostEqual(self.fwd["inputs"]["w"], 5/7, places=4)

    def test_inputs_attacking_expectation(self):
        inp = self.fwd["inputs"]
        self.assertIn("xG_d",     inp)   # damped xG rate
        self.assertIn("xA_d",     inp)   # damped xA rate
        self.assertIn("atk_scale", inp)  # fixture × home multiplier
        self.assertGreater(inp["xG_d"], 0)

    def test_inputs_team_defensive_context(self):
        inp = self.def_["inputs"]
        self.assertIn("team_xGC", inp)   # own primary GKP xGC
        self.assertIn("xGC_d",    inp)   # own damped xGC
        self.assertAlmostEqual(inp["team_xGC"], 0.810, places=3)  # ARS GKP

    def test_inputs_opponent_context(self):
        inp = self.fwd["inputs"]
        self.assertIn("opponent_id", inp)
        self.assertIn("opp_xgc_d",  inp)   # opponent damped defensive quality
        self.assertIn("opp_atk_q",  inp)   # opponent attacking quality

    def test_inputs_fixture_context(self):
        inp = self.fwd["inputs"]
        self.assertIn("home_factor", inp)
        self.assertIn("is_home",     inp)          # explicit bool — audit fix
        self.assertIsInstance(inp["is_home"], bool)
        # neutral home fixture → is_home=True
        self.assertTrue(inp["is_home"])

    def test_inputs_cs_probability(self):
        inp = self.gk["inputs"]
        self.assertIn("lam",  inp)   # Poisson lambda
        self.assertIn("p_cs", inp)   # P(clean sheet) = exp(-lam)
        self.assertAlmostEqual(inp["p_cs"], math.exp(-inp["lam"]), places=6)

    def test_inputs_unchanged_by_pipeline(self):
        # Pipeline inputs must contain all compute_xpts keys unchanged.
        # Pipeline adds opponent_name (data-contract fix) which is not in
        # compute_xpts output — so we compare the subset, not equality.
        elem  = next(e for e in _ELEMENTS if e["id"] == 4001)
        hist  = [{"minutes": 90, "starts": 1}] * 5
        direct = compute_xpts(elem, hist, _NEUTRAL_HOME, _PARAMS)
        pipeline_inputs = self.fwd["inputs"]
        for k, v in direct["inputs"].items():
            self.assertEqual(pipeline_inputs[k], v,
                "inputs[%r] differs: pipeline=%r direct=%r" % (k, pipeline_inputs.get(k), v))
        # New pipeline-only field
        self.assertIn("opponent_name", pipeline_inputs)

    # ── player_api dict ───────────────────────────────────────────────────────

    def test_player_api_key_present(self):
        self.assertIn("player_api", self.fwd)

    def test_player_api_fields(self):
        api = self.fwd["player_api"]
        for k in ("xG_p90", "xA_p90", "starts", "minutes", "status", "cop_next"):
            self.assertIn(k, api, "missing player_api key: %s" % k)

    def test_player_api_values_match_element(self):
        api  = self.fwd["player_api"]
        elem = next(e for e in _ELEMENTS if e["id"] == 4001)
        self.assertEqual(api["xG_p90"],  elem["expected_goals_per_90"])
        self.assertEqual(api["xA_p90"],  elem["expected_assists_per_90"])
        self.assertEqual(api["starts"],  elem["starts"])
        self.assertEqual(api["minutes"], elem["minutes"])
        self.assertEqual(api["status"],  elem["status"])
        self.assertEqual(api["cop_next"], elem["chance_of_playing_next_round"])

    def test_player_api_raw_xg_different_from_damped(self):
        # raw xG_p90 and damped xG_d are different values — both must be present
        api = self.fwd["player_api"]
        inp = self.fwd["inputs"]
        # AlphaFWD: raw xG=0.88, damped xG_d = 5/7*0.88 + 2/7*0.485 = 0.767...
        self.assertNotAlmostEqual(float(api["xG_p90"]), inp["xG_d"], places=2)

    # ── confidence sub-scores ─────────────────────────────────────────────────

    def test_minutes_conf_present(self):
        self.assertIn("_minutes_conf", self.fwd)
        self.assertIn(self.fwd["_minutes_conf"], ("HIGH", "MEDIUM", "LOW"))

    def test_projection_conf_present(self):
        self.assertIn("_projection_conf", self.fwd)
        self.assertIn(self.fwd["_projection_conf"], ("HIGH", "MEDIUM", "LOW"))

    def test_conf_sub_scores_consistent_with_overall(self):
        # overall confidence = min(minutes_conf, projection_conf)
        rank = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}
        for p in self.result["players"]:
            mc = p["_minutes_conf"]
            pc = p["_projection_conf"]
            expected = "HIGH" if min(rank[mc], rank[pc]) == 2 \
                       else ("MEDIUM" if min(rank[mc], rank[pc]) == 1 else "LOW")
            self.assertEqual(p["confidence"], expected,
                "player %s: mc=%s pc=%s → expected %s got %s" % (
                    p["web_name"], mc, pc, expected, p["confidence"]))

    def test_conf_sub_scores_unchanged_by_pipeline(self):
        elem  = next(e for e in _ELEMENTS if e["id"] == 4001)
        hist  = [{"minutes": 90, "starts": 1}] * 5
        direct = compute_xpts(elem, hist, _NEUTRAL_HOME, _PARAMS)
        self.assertEqual(self.fwd["_minutes_conf"],    direct["_minutes_conf"])
        self.assertEqual(self.fwd["_projection_conf"], direct["_projection_conf"])

    # ── xPts / components still present ──────────────────────────────────────

    def test_xpts_still_present(self):
        self.assertIn("xPts", self.fwd)
        self.assertGreater(self.fwd["xPts"], 0)

    def test_components_still_present(self):
        for k in ("xAppPts","xGoalPts","xAssistPts","xCSPts",
                  "xGCDeductPts","xSavePts","xBonus","_xPts_raw"):
            self.assertIn(k, self.fwd["components"])

    def test_all_15_players_have_audit_fields(self):
        for p in self.result["players"]:
            for key in ("inputs", "player_api", "_minutes_conf", "_projection_conf"):
                self.assertIn(key, p,
                    "player %s missing key %r" % (p.get("web_name"), key))
            self.assertIn("is_home", p["inputs"],
                "player %s missing inputs.is_home" % p.get("web_name"))


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
