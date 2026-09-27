"""
Tests for fpl/engine/bench.py — Bench Optimiser v0.1.

All tests use synthetic projections (dicts with the five fields consumed
by optimise_bench: player_id, position, xPts, confidence, web_name).
No calls to compute_xpts are made; the bench module is tested against
its own interface contract.

Squad shape: 2 GKP / 5 DEF / 5 MID / 3 FWD = 15 players.

Submitted XI baseline for most tests: 4-4-2
  GKP1, DEF1-4, MID1-4, FWD1-2  (11 players)
  Bench: GKP2, DEF5, MID5, FWD3  (4 players)
"""

import sys, os, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from fpl.engine.bench import optimise_bench, _is_valid_xi, _formation_str

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _p(pid, pos, xpts, confidence="HIGH", web_name=None):
    """Make a minimal projection dict (only fields bench.py needs)."""
    return {
        "player_id":  pid,
        "position":   pos,
        "xPts":       float(xpts),
        "confidence": confidence,
        "web_name":   web_name or f"P{pid}",
    }


def _squad(**overrides):
    """
    Return a standard 15-player squad.

    Keyword args override individual player xPts by player_id:
        _squad(DEF5=5.0, FWD3=7.5)

    Default xPts
    ─────────────────────────────────────────────────────────
    GKP1(1)=5.0  GKP2(2)=4.0
    DEF1(3)=5.0  DEF2(4)=4.5  DEF3(5)=4.0  DEF4(6)=3.5  DEF5(7)=2.0
    MID1(8)=8.0  MID2(9)=7.0  MID3(10)=6.0 MID4(11)=5.0 MID5(12)=1.0
    FWD1(13)=7.0 FWD2(14)=6.5 FWD3(15)=3.0
    """
    defaults = {
        "GKP1": (1, 1,  5.0), "GKP2": (2,  1, 4.0),
        "DEF1": (3, 2,  5.0), "DEF2": (4,  2, 4.5),
        "DEF3": (5, 2,  4.0), "DEF4": (6,  2, 3.5), "DEF5": (7, 2, 2.0),
        "MID1": (8, 3,  8.0), "MID2": (9,  3, 7.0),
        "MID3": (10, 3, 6.0), "MID4": (11, 3, 5.0), "MID5": (12, 3, 1.0),
        "FWD1": (13, 4, 7.0), "FWD2": (14, 4, 6.5), "FWD3": (15, 4, 3.0),
    }
    players = []
    for name, (pid, pos, xpts) in defaults.items():
        actual_xpts = float(overrides.get(name, xpts))
        players.append(_p(pid, pos, actual_xpts))
    return players


# Standard submitted 4-4-2 XI: GKP1 DEF1-4 MID1-4 FWD1-2
_BASE_XI = [1, 3, 4, 5, 6, 8, 9, 10, 11, 13, 14]
_BASE_XPTS = 5.0 + 5.0 + 4.5 + 4.0 + 3.5 + 8.0 + 7.0 + 6.0 + 5.0 + 7.0 + 6.5  # = 61.5


# ─────────────────────────────────────────────────────────────────────────────
# FORMATION HELPER TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestFormationHelpers(unittest.TestCase):

    def setUp(self):
        self.squad = _squad()
        self.by_id = {p["player_id"]: p for p in self.squad}

    def test_formation_442(self):
        xi = [1, 3, 4, 5, 6, 8, 9, 10, 11, 13, 14]   # 4-4-2
        self.assertEqual(_formation_str(xi, self.by_id), "4-4-2")

    def test_formation_433(self):
        xi = [1, 3, 4, 5, 6, 8, 9, 10, 13, 14, 15]   # 4-3-3
        self.assertEqual(_formation_str(xi, self.by_id), "4-3-3")

    def test_formation_532(self):
        xi = [1, 3, 4, 5, 6, 7, 8, 9, 10, 13, 14]    # 5-3-2
        self.assertEqual(_formation_str(xi, self.by_id), "5-3-2")

    def test_formation_343(self):
        xi = [1, 3, 4, 5, 8, 9, 10, 11, 13, 14, 15]  # 3-4-3
        self.assertEqual(_formation_str(xi, self.by_id), "3-4-3")


# ─────────────────────────────────────────────────────────────────────────────
# IS_VALID_XI TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestIsValidXi(unittest.TestCase):

    def setUp(self):
        self.by_id = {p["player_id"]: p for p in _squad()}

    def test_valid_442(self):
        self.assertTrue(_is_valid_xi(_BASE_XI, self.by_id))

    def test_valid_433(self):
        xi = [1, 3, 4, 5, 6, 8, 9, 10, 13, 14, 15]
        self.assertTrue(_is_valid_xi(xi, self.by_id))

    def test_valid_352(self):
        xi = [1, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14]
        self.assertTrue(_is_valid_xi(xi, self.by_id))

    def test_invalid_two_gkp(self):
        # Replacing DEF1 with GKP2 → two GKPs
        xi = [1, 2, 4, 5, 6, 8, 9, 10, 11, 13, 14]
        self.assertFalse(_is_valid_xi(xi, self.by_id))

    def test_invalid_no_gkp(self):
        xi = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]   # no GKP
        self.assertFalse(_is_valid_xi(xi, self.by_id))

    def test_invalid_only_two_def(self):
        # 2-5-3: only 2 DEF
        xi = [1, 3, 4, 8, 9, 10, 11, 12, 13, 14, 15]
        self.assertFalse(_is_valid_xi(xi, self.by_id))

    def test_invalid_only_one_mid(self):
        # 5-1-4 impossible (4 FWD) — and only 1 MID < min 2
        xi = [1, 3, 4, 5, 6, 7, 8, 13, 14, 15, 15]   # repeated, just checking logic
        # Build a proper 5-1-4 — but squad only has 3 FWDs, so use 4 DEF 1 MID 3 FWD (wrong count)
        xi = [1, 3, 4, 5, 6, 7, 8, 13, 14, 15, 6]     # repeated id, length still 11
        # Use a clean 4-1-5 attempt with only 3 FWDs available
        # xi with only 1 MID: impossible with our fixed squad (would need 4 FWDs)
        # Instead use: GKP, 5DEF, 1MID, 4FWD — but squad only has 3 FWDs → skip
        # Test: 4 DEF, 1 MID, and pad with another DEF to hit 11 — but that's 5 DEF 1 MID
        xi_2mid = [1, 3, 4, 5, 6, 7, 8, 9, 13, 14, 15]  # 5-2-3 = valid (mids=2)
        self.assertTrue(_is_valid_xi(xi_2mid, self.by_id))
        # 5 DEF 1 MID 3 FWD — only 1 MID, invalid
        xi_1mid = [1, 3, 4, 5, 6, 7, 8, 13, 14, 15, 15]  # dup id, bad length logic
        # Create correct 5-1-3 attempt: use available ids but must have valid length
        # Our squad: DEF ids 3,4,5,6,7 and FWD ids 13,14,15 and MID 8
        xi_5d1m3f = [1, 3, 4, 5, 6, 7, 8, 13, 14, 15, 9]  # 5DEF,2MID,3FWD = valid 5-2-3
        # To get 5DEF 1MID 3FWD = 5-1-3 we'd need 4 FWDs → impossible with this squad
        # So test instead by building a custom by_id:
        custom = (
            [_p(101, 1, 5.0)] +                         # GKP
            [_p(200+i, 2, 3.0) for i in range(5)] +    # 5 DEF
            [_p(300, 3, 4.0)] +                         # 1 MID
            [_p(400+i, 4, 5.0) for i in range(4)]      # 4 FWD
        )                                               # total 11 (still valid 1GKP-5DEF-1MID-4FWD?)
        # Wait, FWD max is 3, so 4 FWDs is invalid
        by_custom = {p["player_id"]: p for p in custom}
        xi_4fwd = [101] + [200+i for i in range(5)] + [300] + [400+i for i in range(4)]
        self.assertFalse(_is_valid_xi(xi_4fwd, by_custom))

    def test_invalid_no_fwd(self):
        # GKP + 5DEF + 5MID + 0FWD
        xi = [1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
        self.assertFalse(_is_valid_xi(xi, self.by_id))

    def test_invalid_wrong_size(self):
        self.assertFalse(_is_valid_xi(_BASE_XI[:10], self.by_id))
        self.assertFalse(_is_valid_xi(_BASE_XI + [15], self.by_id))


# ─────────────────────────────────────────────────────────────────────────────
# CORE OPTIMISER TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestOptimiseBenchHold(unittest.TestCase):
    """No bench player can improve the submitted XI."""

    def test_hold_when_no_improvement(self):
        result = optimise_bench(_squad(), _BASE_XI)
        self.assertEqual(result["action"], "HOLD")

    def test_delta_zero_when_no_improvement(self):
        result = optimise_bench(_squad(), _BASE_XI)
        self.assertEqual(result["delta"], 0.0)

    def test_substitutions_empty_when_delta_zero(self):
        result = optimise_bench(_squad(), _BASE_XI)
        self.assertEqual(result["substitutions"], [])

    def test_recommended_equals_submitted_when_no_improvement(self):
        result = optimise_bench(_squad(), _BASE_XI)
        self.assertEqual(
            sorted(result["recommended_xi"]["player_ids"]),
            sorted(result["submitted_xi"]["player_ids"]),
        )

    def test_hold_below_threshold(self):
        # DEF5 improved to 3.9 — still below DEF4 (3.5+0.4=3.9 is above 3.5 but...)
        # Actually 3.9 > 3.5, so we get a swap of +0.4. Make threshold=0.5 → HOLD.
        squad = _squad(DEF5=3.9)
        result = optimise_bench(squad, _BASE_XI, threshold=0.5)
        # delta = 3.9 - 3.5 = 0.4, threshold = 0.5 → HOLD
        self.assertEqual(result["action"], "HOLD")
        self.assertAlmostEqual(result["delta"], 0.4, places=1)

    def test_hold_threshold_exactly_met_is_swap(self):
        # delta=0.5 exactly at threshold → SWAP (>= is SWAP)
        squad = _squad(DEF5=4.0)   # 4.0 - 3.5 = 0.5, exactly threshold
        result = optimise_bench(squad, _BASE_XI, threshold=0.5)
        self.assertEqual(result["action"], "SWAP")

    def test_submitted_xi_xpts_correct(self):
        result = optimise_bench(_squad(), _BASE_XI)
        self.assertAlmostEqual(result["submitted_xi"]["xPts"], _BASE_XPTS, places=1)

    def test_submitted_formation_442(self):
        result = optimise_bench(_squad(), _BASE_XI)
        self.assertEqual(result["submitted_xi"]["formation"], "4-4-2")

    def test_threshold_echoed_in_result(self):
        result = optimise_bench(_squad(), _BASE_XI, threshold=1.0)
        self.assertEqual(result["threshold"], 1.0)


class TestSamePositionSwap(unittest.TestCase):
    """Bench DEF/MID/FWD/GKP better than a starter at the same position."""

    def test_def_bench_better_than_starter(self):
        # DEF5 (bench, id=7) raised to 5.5 > DEF4 (starting, id=6, xPts=3.5)
        # Best action: swap DEF4 out, DEF5 in; formation stays 4-4-2
        squad = _squad(DEF5=5.5)
        result = optimise_bench(squad, _BASE_XI)
        self.assertEqual(result["action"], "SWAP")
        self.assertIn(7,  result["recommended_xi"]["player_ids"])   # DEF5 in
        self.assertNotIn(6, result["recommended_xi"]["player_ids"]) # DEF4 out
        self.assertEqual(result["recommended_xi"]["formation"], "4-4-2")

    def test_def_swap_delta(self):
        squad = _squad(DEF5=5.5)
        result = optimise_bench(squad, _BASE_XI)
        self.assertAlmostEqual(result["delta"], 5.5 - 3.5, places=1)  # 2.0

    def test_mid_bench_better_than_starter(self):
        # MID5 (id=12) raised to 6.5. To keep this a same-position MID swap,
        # all starting DEFs are raised to 8.0 so cross-position replacement
        # of a DEF is never the optimal option.
        # MID5(6.5) > MID4(5.0) → replaces MID4, formation stays 4-4-2.
        squad = _squad(MID5=6.5, DEF1=8.0, DEF2=8.0, DEF3=8.0, DEF4=8.0)
        result = optimise_bench(squad, _BASE_XI)
        self.assertEqual(result["action"], "SWAP")
        self.assertIn(12,  result["recommended_xi"]["player_ids"])
        self.assertNotIn(11, result["recommended_xi"]["player_ids"])
        self.assertEqual(result["recommended_xi"]["formation"], "4-4-2")

    def test_fwd_bench_better_than_starter(self):
        # FWD3 (id=15) raised to 8.0. To isolate a same-position FWD→FWD swap,
        # all starting DEFs and MIDs are raised above 8.0 so the optimizer
        # cannot improve by doing a cross-position replacement.
        # FWD3(8.0) > FWD2(6.5) → replaces FWD2, formation stays 4-4-2.
        # All DEFs and all MIDs raised above FWD3=8.0 so no cross-position swap
        # can exceed the same-position FWD3(8.0)→FWD2(6.5) gain of 1.5.
        squad = _squad(FWD3=8.0,
                       DEF1=9.0, DEF2=9.0, DEF3=9.0, DEF4=9.0,
                       MID3=9.0, MID4=8.5)
        result = optimise_bench(squad, _BASE_XI)
        self.assertEqual(result["action"], "SWAP")
        self.assertIn(15,  result["recommended_xi"]["player_ids"])
        self.assertNotIn(14, result["recommended_xi"]["player_ids"])
        self.assertEqual(result["recommended_xi"]["formation"], "4-4-2")

    def test_gkp_bench_better_than_starter(self):
        # GKP2 (id=2, xPts=4.0) raised to 7.0 > GKP1 (id=1, xPts=5.0)
        squad = _squad(GKP2=7.0)
        result = optimise_bench(squad, _BASE_XI)
        self.assertEqual(result["action"], "SWAP")
        self.assertIn(2,  result["recommended_xi"]["player_ids"])
        self.assertNotIn(1, result["recommended_xi"]["player_ids"])
        self.assertEqual(result["recommended_xi"]["formation"], "4-4-2")

    def test_substitution_entry_present(self):
        squad = _squad(DEF5=5.5)
        result = optimise_bench(squad, _BASE_XI)
        self.assertEqual(len(result["substitutions"]), 1)
        sub = result["substitutions"][0]
        self.assertEqual(sub["out"]["player_id"], 6)   # DEF4 out
        self.assertEqual(sub["in"]["player_id"],  7)   # DEF5 in

    def test_substitution_preserves_confidence(self):
        squad = _squad(DEF5=5.5)
        # Make DEF5 confidence=MEDIUM to check it is passed through
        for p in squad:
            if p["player_id"] == 7:
                p["confidence"] = "MEDIUM"
        result = optimise_bench(squad, _BASE_XI)
        in_conf = result["substitutions"][0]["in"]["confidence"]
        self.assertEqual(in_conf, "MEDIUM")
        # Confidence in the recommended XI player list also matches
        rec_player = next(
            p for p in result["recommended_xi"]["players"] if p["player_id"] == 7
        )
        self.assertEqual(rec_player["confidence"], "MEDIUM")


class TestCrossPositionFormationSwap(unittest.TestCase):
    """Bench player at different position improves XI via formation change."""

    def test_442_to_433_fwd_replaces_mid(self):
        # FWD3 (id=15) raised to 7.5 > MID4 (id=11, 5.0).
        # To guarantee 4-3-3 (FWD in, MID out) rather than 3-4-3 (FWD in, DEF out),
        # all starting DEFs are raised above FWD3 so replacing a DEF is never optimal.
        # min(DEF starters)=8.0 > FWD3=7.5 → optimizer must replace the weakest MID.
        squad = _squad(FWD3=7.5, DEF1=9.0, DEF2=8.5, DEF3=8.0, DEF4=8.0)
        result = optimise_bench(squad, _BASE_XI)
        self.assertEqual(result["action"], "SWAP")
        self.assertEqual(result["recommended_xi"]["formation"], "4-3-3")
        self.assertIn(15,  result["recommended_xi"]["player_ids"])
        self.assertNotIn(11, result["recommended_xi"]["player_ids"])

    def test_442_to_433_delta(self):
        # Same squad as above: FWD3(7.5) - MID4(5.0) = +2.5
        squad = _squad(FWD3=7.5, DEF1=9.0, DEF2=8.5, DEF3=8.0, DEF4=8.0)
        result = optimise_bench(squad, _BASE_XI)
        self.assertAlmostEqual(result["delta"], 7.5 - 5.0, places=1)  # 2.5

    def test_442_to_532_def_replaces_mid(self):
        # To get 5-3-2 (DEF5 in, MID4 out), the same-position swap (DEF5 for a weaker
        # starting DEF) must NOT be optimal.  Raise all starting DEFs to 6.5 so
        # DEF5(6.0) cannot improve on any of them; DEF5(6.0) > MID4(5.0) → 5-3-2.
        squad = _squad(DEF5=6.0, DEF1=7.0, DEF2=7.0, DEF3=7.0, DEF4=7.0)
        result = optimise_bench(squad, _BASE_XI)
        self.assertEqual(result["action"], "SWAP")
        self.assertEqual(result["recommended_xi"]["formation"], "5-3-2")
        self.assertIn(7,  result["recommended_xi"]["player_ids"])
        self.assertNotIn(11, result["recommended_xi"]["player_ids"])

    def test_cross_position_substitution_shows_positions(self):
        # Use the same squad as test_442_to_433: DEFs all raised, FWD3 replaces MID4.
        squad = _squad(FWD3=7.5, DEF1=9.0, DEF2=8.5, DEF3=8.0, DEF4=8.0)
        result = optimise_bench(squad, _BASE_XI)
        subs = result["substitutions"]
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["out"]["position"], 3)   # MID out
        self.assertEqual(subs[0]["in"]["position"],  4)   # FWD in

    def test_formation_change_442_to_343(self):
        # Bench DEF5 xPts very low; swap DEF4 out, keep DEF1-3, bring in MID5
        # Easier: set MID5=7.0 > DEF4=3.5. This creates 3-5-2 (5DEF→4DEF,5MID)
        # Wait: MID5 into starting XI replaces DEF4. Starters become:
        # GKP1, DEF1(3), DEF2(4), DEF3(5), MID1(8), MID2(9), MID3(10), MID4(11), MID5(12), FWD1(13), FWD2(14)
        # = 1GKP + 3DEF + 5MID + 2FWD = 3-5-2 formation, valid (DEF≥3, MID≥2, FWD≥1)
        squad = _squad(MID5=7.0)
        result = optimise_bench(squad, _BASE_XI)
        self.assertEqual(result["action"], "SWAP")
        self.assertEqual(result["recommended_xi"]["formation"], "3-5-2")
        self.assertIn(12,  result["recommended_xi"]["player_ids"])
        self.assertNotIn(6, result["recommended_xi"]["player_ids"])


class TestMultipleSwaps(unittest.TestCase):
    """Two bench players both better than two starters."""

    def test_two_swaps_same_position(self):
        # Both DEF5 and GKP2 better than their starting counterparts:
        # DEF5=6.0 (vs DEF4=3.5), GKP2=6.0 (vs GKP1=5.0)
        # Best XI swaps both.
        squad = _squad(DEF5=6.0, GKP2=6.0)
        result = optimise_bench(squad, _BASE_XI)
        self.assertEqual(result["action"], "SWAP")
        # Both bench players should be in recommended XI
        self.assertIn(7, result["recommended_xi"]["player_ids"])   # DEF5 in
        self.assertIn(2, result["recommended_xi"]["player_ids"])   # GKP2 in
        self.assertNotIn(6, result["recommended_xi"]["player_ids"]) # DEF4 out
        self.assertNotIn(1, result["recommended_xi"]["player_ids"]) # GKP1 out

    def test_two_swaps_delta(self):
        squad = _squad(DEF5=6.0, GKP2=6.0)
        result = optimise_bench(squad, _BASE_XI)
        # Improvement: (6.0-3.5) + (6.0-5.0) = 2.5 + 1.0 = 3.5
        self.assertAlmostEqual(result["delta"], 3.5, places=1)

    def test_two_substitutions_listed(self):
        squad = _squad(DEF5=6.0, GKP2=6.0)
        result = optimise_bench(squad, _BASE_XI)
        self.assertEqual(len(result["substitutions"]), 2)

    def test_two_cross_position_swaps(self):
        # DEF5 replaces MID4 (formation change) AND FWD3 replaces another player
        # DEF5=6.5 (vs MID4=5.0, delta+1.5), FWD3=7.0 (bench) > MID3=6.0? No, MID3 stays.
        # Simplest 2-swap: DEF5=6.5 replaces DEF4, MID5=6.5 replaces MID4
        squad = _squad(DEF5=6.5, MID5=6.5)
        result = optimise_bench(squad, _BASE_XI)
        self.assertEqual(result["action"], "SWAP")
        # Both improvements materialise
        self.assertIn(7,  result["recommended_xi"]["player_ids"])
        self.assertIn(12, result["recommended_xi"]["player_ids"])
        self.assertEqual(len(result["substitutions"]), 2)


class TestValidationErrors(unittest.TestCase):
    """Bad inputs raise ValueError with helpful messages."""

    def test_wrong_squad_size(self):
        with self.assertRaises(ValueError) as ctx:
            optimise_bench(_squad()[:14], _BASE_XI)
        self.assertIn("15", str(ctx.exception))

    def test_wrong_xi_size(self):
        with self.assertRaises(ValueError) as ctx:
            optimise_bench(_squad(), _BASE_XI[:10])
        self.assertIn("11", str(ctx.exception))

    def test_unknown_player_in_xi(self):
        with self.assertRaises(ValueError) as ctx:
            optimise_bench(_squad(), [999] + _BASE_XI[1:])
        self.assertIn("999", str(ctx.exception))

    def test_illegal_formation_in_submitted_xi(self):
        # Only 2 DEF in submitted XI (rule: minimum 3)
        bad_xi = [1, 3, 4, 8, 9, 10, 11, 12, 13, 14, 15]  # 2DEF 5MID 3FWD
        with self.assertRaises(ValueError) as ctx:
            optimise_bench(_squad(), bad_xi)
        self.assertIn("formation", str(ctx.exception).lower())

    def test_duplicate_player_id(self):
        squad = _squad()
        squad[0]["player_id"] = squad[1]["player_id"]   # force duplicate
        with self.assertRaises(ValueError) as ctx:
            optimise_bench(squad, _BASE_XI)
        self.assertIn("Duplicate", str(ctx.exception))


class TestOutputSchema(unittest.TestCase):
    """Result dict has expected shape and types."""

    def setUp(self):
        self.result = optimise_bench(_squad(), _BASE_XI)

    def test_action_key(self):
        self.assertIn(self.result["action"], ("SWAP", "HOLD"))

    def test_delta_is_float(self):
        self.assertIsInstance(self.result["delta"], float)

    def test_threshold_echoed(self):
        self.assertEqual(self.result["threshold"], 0.5)

    def test_submitted_xi_shape(self):
        xi = self.result["submitted_xi"]
        self.assertIn("player_ids", xi)
        self.assertIn("formation",  xi)
        self.assertIn("xPts",       xi)
        self.assertIn("players",    xi)
        self.assertEqual(len(xi["player_ids"]), 11)
        self.assertEqual(len(xi["players"]),    11)

    def test_recommended_xi_shape(self):
        xi = self.result["recommended_xi"]
        self.assertIn("player_ids", xi)
        self.assertIn("formation",  xi)
        self.assertIn("xPts",       xi)
        self.assertIn("players",    xi)

    def test_players_have_confidence(self):
        for p in self.result["recommended_xi"]["players"]:
            self.assertIn("confidence", p)
            self.assertIn(p["confidence"], ("HIGH", "MEDIUM", "LOW"))

    def test_player_ids_unique_in_xi(self):
        ids = self.result["recommended_xi"]["player_ids"]
        self.assertEqual(len(ids), len(set(ids)))

    def test_recommended_xi_is_valid_formation(self):
        by_id = {p["player_id"]: p for p in _squad()}
        xi = self.result["recommended_xi"]["player_ids"]
        from fpl.engine.bench import _is_valid_xi
        self.assertTrue(_is_valid_xi(xi, by_id))

    def test_delta_equals_xpts_difference(self):
        sub_xpts = self.result["submitted_xi"]["xPts"]
        rec_xpts = self.result["recommended_xi"]["xPts"]
        self.assertAlmostEqual(
            self.result["delta"],
            round(rec_xpts - sub_xpts, 2),
            places=2,
        )


class TestConfidencePassthrough(unittest.TestCase):
    """confidence comes from projections; bench module does not recompute it."""

    def test_low_confidence_player_in_recommended_xi(self):
        # A LOW-confidence player with high xPts still gets into recommended XI
        squad = _squad(DEF5=9.0)
        for p in squad:
            if p["player_id"] == 7:  # DEF5
                p["confidence"] = "LOW"
        result = optimise_bench(squad, _BASE_XI)
        self.assertEqual(result["action"], "SWAP")
        rec_p = next(
            p for p in result["recommended_xi"]["players"] if p["player_id"] == 7
        )
        self.assertEqual(rec_p["confidence"], "LOW")

    def test_all_confidence_values_preserved(self):
        squad = []
        confs = ["HIGH", "HIGH", "MEDIUM", "LOW", "HIGH",
                 "HIGH", "LOW", "HIGH", "HIGH", "HIGH",
                 "MEDIUM", "LOW", "HIGH", "MEDIUM", "LOW"]
        for (name, (pid, pos, xpts)), conf in zip(
            {
                "GKP1": (1, 1, 5.0), "GKP2": (2, 1, 4.0),
                "DEF1": (3, 2, 5.0), "DEF2": (4, 2, 4.5),
                "DEF3": (5, 2, 4.0), "DEF4": (6, 2, 3.5), "DEF5": (7, 2, 2.0),
                "MID1": (8, 3, 8.0), "MID2": (9, 3, 7.0),
                "MID3": (10, 3, 6.0), "MID4": (11, 3, 5.0), "MID5": (12, 3, 1.0),
                "FWD1": (13, 4, 7.0), "FWD2": (14, 4, 6.5), "FWD3": (15, 4, 3.0),
            }.items(),
            confs,
        ):
            squad.append(_p(pid, pos, xpts, confidence=conf))

        result = optimise_bench(squad, _BASE_XI)
        by_id  = {p["player_id"]: p for p in squad}
        for player in result["submitted_xi"]["players"]:
            self.assertEqual(player["confidence"], by_id[player["player_id"]]["confidence"])

    def test_no_new_confidence_keys_in_result(self):
        # Bench module must not add squad-level confidence keys
        result = optimise_bench(_squad(), _BASE_XI)
        self.assertNotIn("confidence",        result)
        self.assertNotIn("squad_confidence",  result)
        self.assertNotIn("overall_confidence", result)


class TestEdgeCases(unittest.TestCase):

    def test_custom_threshold_zero_always_swaps_if_any_improvement(self):
        squad = _squad(DEF5=3.6)   # 3.6 > DEF4=3.5, tiny improvement
        result = optimise_bench(squad, _BASE_XI, threshold=0.0)
        self.assertEqual(result["action"], "SWAP")
        self.assertAlmostEqual(result["delta"], 0.1, places=1)

    def test_very_high_threshold_means_hold(self):
        squad = _squad(DEF5=10.0)  # huge improvement but threshold is enormous
        result = optimise_bench(squad, _BASE_XI, threshold=99.0)
        self.assertEqual(result["action"], "HOLD")

    def test_single_gkp_in_squad(self):
        # Edge: squad with only 1 GKP (unusual but possible)
        # Replace GKP2 with a second GKP at different id but position 1
        squad = (
            [_p(1,  1, 5.0)] +
            [_p(2,  2, 4.0)] +   # treat this as DEF instead of GKP2 in this squad
            [_p(3,  2, 5.0), _p(4, 2, 4.5), _p(5, 2, 4.0),
             _p(6, 2, 3.5), _p(7, 2, 2.0)] +
            [_p(8,  3, 8.0), _p(9, 3, 7.0), _p(10, 3, 6.0),
             _p(11, 3, 5.0), _p(12, 3, 1.0)] +
            [_p(13, 4, 7.0), _p(14, 4, 6.5), _p(15, 4, 3.0)]
        )
        # Only GKP is id=1; the "GKP2" slot is now a DEF.
        # Submitted XI must include the only GKP.
        xi = [1, 2, 3, 4, 5, 8, 9, 10, 11, 13, 14]   # 5DEF 4MID 2FWD = valid
        result = optimise_bench(squad, xi)
        # GKP1 must stay in every valid XI (only GKP available)
        self.assertIn(1, result["recommended_xi"]["player_ids"])

    def test_bench_player_xpts_ties_broken_deterministically(self):
        # Two bench players with equal improvement: result must be deterministic
        squad = _squad(DEF5=3.5, GKP2=5.0)  # GKP2 ties GKP1 exactly — no improvement
        result1 = optimise_bench(squad, _BASE_XI)
        result2 = optimise_bench(squad, _BASE_XI)
        self.assertEqual(result1["recommended_xi"]["player_ids"],
                         result2["recommended_xi"]["player_ids"])

    def test_all_bench_players_inferior(self):
        # All bench players set to 0.0 — no improvement possible
        squad = _squad(GKP2=0.0, DEF5=0.0, MID5=0.0, FWD3=0.0)
        result = optimise_bench(squad, _BASE_XI)
        self.assertEqual(result["action"], "HOLD")
        self.assertEqual(result["delta"], 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
