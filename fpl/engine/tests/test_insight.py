"""
Tests for the player insight layer.

Covers all 9 insight keys, priority ordering, and edge cases
(None inputs, boundary values, position-gating).
"""

import unittest
from fpl.engine.insight import player_insight


def _pl(xpts=5.0, conf="MEDIUM", pos=3, p60=0.9, cop=100,
        atk=1.0, p_cs=None, risks=None, status="a"):
    """Build a minimal player dict for testing."""
    return {
        "xPts":       xpts,
        "confidence": conf,
        "position":   pos,
        "inputs":     {"P_60plus": p60, "atk_scale": atk, "p_cs": p_cs},
        "risks":      risks or [],
        "player_api": {"cop_next": cop, "status": status},
    }


class TestPlayerInsight(unittest.TestCase):

    # ── minutes_risk ──────────────────────────────────────────────────────────

    def test_minutes_risk_low_p60(self):
        self.assertEqual(player_insight(_pl(p60=0.40))["key"], "minutes_risk")

    def test_minutes_risk_boundary_p60(self):
        # 0.50 is the threshold — exactly 0.50 is NOT a risk
        self.assertNotEqual(player_insight(_pl(p60=0.50))["key"], "minutes_risk")

    def test_minutes_risk_low_cop(self):
        self.assertEqual(player_insight(_pl(cop=25))["key"], "minutes_risk")

    def test_minutes_risk_cop_50_is_safe(self):
        # cop=50 means 50% chance — just at the threshold, not a risk
        self.assertNotEqual(player_insight(_pl(cop=50))["key"], "minutes_risk")

    def test_minutes_risk_injury_status(self):
        self.assertEqual(player_insight(_pl(status="i"))["key"], "minutes_risk")

    def test_minutes_risk_doubt_status(self):
        self.assertEqual(player_insight(_pl(status="d"))["key"], "minutes_risk")

    def test_minutes_risk_via_risks_string(self):
        self.assertEqual(
            player_insight(_pl(risks=["Minutes uncertainty — P(60+)=0.38"]))["key"],
            "minutes_risk",
        )

    def test_none_p60_does_not_trigger(self):
        # None means no data — should not fire minutes_risk on its own
        pl = _pl(p60=None, cop=100, status="a")
        self.assertNotEqual(player_insight(pl)["key"], "minutes_risk")

    def test_none_cop_does_not_trigger(self):
        pl = _pl(p60=0.85, cop=None, status="a")
        self.assertNotEqual(player_insight(pl)["key"], "minutes_risk")

    # ── tough_fixture ─────────────────────────────────────────────────────────

    def test_tough_fixture(self):
        self.assertEqual(player_insight(_pl(atk=0.70))["key"], "tough_fixture")

    def test_tough_fixture_boundary(self):
        # atk=0.78 is the cutoff; exactly 0.78 is NOT tough
        self.assertNotEqual(player_insight(_pl(atk=0.78))["key"], "tough_fixture")

    def test_none_atk_no_fixture_insight(self):
        pl = _pl(atk=None, conf="MEDIUM", xpts=4.0)
        key = player_insight(pl)["key"]
        self.assertNotIn(key, ("tough_fixture", "strong_fixture"))

    # ── high_variance ─────────────────────────────────────────────────────────

    def test_high_variance_low_confidence(self):
        self.assertEqual(player_insight(_pl(conf="LOW", atk=1.0))["key"], "high_variance")

    # ── captain_candidate ─────────────────────────────────────────────────────

    def test_captain_candidate_via_flag(self):
        self.assertEqual(player_insight(_pl(xpts=6.0), is_top_captain=True)["key"], "captain_candidate")

    def test_captain_candidate_via_xpts(self):
        self.assertEqual(player_insight(_pl(xpts=9.0))["key"], "captain_candidate")

    def test_not_captain_below_threshold(self):
        # xpts=8.4 is just below the 8.5 standalone threshold
        key = player_insight(_pl(xpts=8.4))["key"]
        self.assertNotEqual(key, "captain_candidate")

    # ── big_upside ────────────────────────────────────────────────────────────

    def test_big_upside(self):
        self.assertEqual(player_insight(_pl(xpts=7.8))["key"], "big_upside")

    def test_big_upside_boundary(self):
        # 7.5 is the threshold; exactly 7.5 is big upside
        self.assertEqual(player_insight(_pl(xpts=7.5))["key"], "big_upside")

    # ── clean_sheet_chance ────────────────────────────────────────────────────

    def test_clean_sheet_gk(self):
        self.assertEqual(player_insight(_pl(pos=1, p_cs=0.45))["key"], "clean_sheet_chance")

    def test_clean_sheet_def(self):
        self.assertEqual(player_insight(_pl(pos=2, p_cs=0.42))["key"], "clean_sheet_chance")

    def test_clean_sheet_not_for_mid(self):
        pl = _pl(pos=3, p_cs=0.55, xpts=4.0, atk=1.0, conf="MEDIUM")
        self.assertNotEqual(player_insight(pl)["key"], "clean_sheet_chance")

    def test_clean_sheet_not_for_fwd(self):
        pl = _pl(pos=4, p_cs=0.60, xpts=4.0, atk=1.0, conf="MEDIUM")
        self.assertNotEqual(player_insight(pl)["key"], "clean_sheet_chance")

    def test_none_p_cs_no_clean_sheet(self):
        pl = _pl(pos=1, p_cs=None, xpts=4.0, atk=1.0, conf="MEDIUM")
        self.assertNotEqual(player_insight(pl)["key"], "clean_sheet_chance")

    # ── strong_fixture ────────────────────────────────────────────────────────

    def test_strong_fixture(self):
        self.assertEqual(player_insight(_pl(atk=1.25, xpts=4.0, conf="MEDIUM"))["key"], "strong_fixture")

    def test_strong_fixture_boundary(self):
        # 1.15 is the threshold; exactly 1.15 is strong
        self.assertEqual(player_insight(_pl(atk=1.15, xpts=4.0, conf="MEDIUM"))["key"], "strong_fixture")

    # ── looking_good ──────────────────────────────────────────────────────────

    def test_looking_good(self):
        self.assertEqual(player_insight(_pl(conf="HIGH", xpts=5.5, atk=1.05))["key"], "looking_good")

    def test_looking_good_requires_high_conf(self):
        pl = _pl(conf="MEDIUM", xpts=6.0, atk=1.05)
        self.assertNotEqual(player_insight(pl)["key"], "looking_good")

    # ── solid_pick fallback ───────────────────────────────────────────────────

    def test_solid_pick_fallback(self):
        self.assertEqual(
            player_insight(_pl(conf="MEDIUM", xpts=4.0, atk=1.0, p60=0.85))["key"],
            "solid_pick",
        )

    # ── priority ordering ─────────────────────────────────────────────────────

    def test_minutes_risk_beats_strong_fixture(self):
        # Even with a great fixture, minutes risk takes priority
        self.assertEqual(player_insight(_pl(p60=0.35, atk=1.40))["key"], "minutes_risk")

    def test_minutes_risk_beats_high_xpts(self):
        self.assertEqual(player_insight(_pl(p60=0.35, xpts=9.5))["key"], "minutes_risk")

    def test_tough_fixture_beats_high_variance(self):
        # Specific negative > generic caution
        self.assertEqual(player_insight(_pl(atk=0.70, conf="LOW"))["key"], "tough_fixture")

    def test_tough_fixture_beats_high_confidence(self):
        # Even HIGH confidence can't override a tough fixture signal
        pl = _pl(atk=0.70, conf="HIGH", xpts=6.0)
        self.assertEqual(player_insight(pl)["key"], "tough_fixture")

    def test_captain_candidate_beats_big_upside(self):
        # Both high-xPts signals — captain wins
        self.assertEqual(player_insight(_pl(xpts=9.0))["key"], "captain_candidate")

    # ── return shape ─────────────────────────────────────────────────────────

    def test_return_has_required_keys(self):
        result = player_insight(_pl())
        for k in ("key", "label", "tone", "icon"):
            self.assertIn(k, result)

    def test_tone_values(self):
        positive      = player_insight(_pl(xpts=9.0))
        caution       = player_insight(_pl(p60=0.3))
        solid_pick    = player_insight(_pl(conf="MEDIUM", xpts=4.0))
        self.assertEqual(positive["tone"],   "positive")
        self.assertEqual(caution["tone"],    "caution")
        self.assertEqual(solid_pick["key"],  "solid_pick")
        self.assertEqual(solid_pick["tone"], "positive")   # solid_pick is a good thing
        self.assertIn(solid_pick["icon"], ("👍",))


if __name__ == "__main__":
    unittest.main(verbosity=2)
