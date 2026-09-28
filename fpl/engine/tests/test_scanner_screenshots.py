"""
Integration tests for the scanner against real FPL screenshots.

These tests are LOCAL ONLY — they skip automatically when the image files are
absent (e.g. in CI).  Run them before any scanner change to catch regressions:

    python3 -m unittest fpl.engine.tests.test_scanner_screenshots -v

Place screenshots in:  fpl/test_images/

Each test asserts:
  - status == VALID
  - view_type correct
  - exactly 15 players, exactly 11 starters
  - all expected player names present (normalised — see _norm)
"""

import re
import unittest
import unicodedata
from pathlib import Path

_IMG_DIR = Path(__file__).resolve().parent.parent.parent / "test_images"


# ── helpers ───────────────────────────────────────────────────────────────────

def _norm(name: str) -> str:
    """NFKD → drop combining marks → lowercase → a-z only.
    'Gibbs - White' → 'gibbswhite',  'Muñoz' → 'munoz',  'N.Williams' → 'nwilliams'
    """
    nfkd = unicodedata.normalize("NFKD", name)
    no_marks = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z]", "", no_marks.lower())


def _scan(filename: str) -> dict:
    from fpl.engine.scanner import scan_squad
    return scan_squad((_IMG_DIR / filename).read_bytes())


def _skip(filename: str):
    path = _IMG_DIR / filename
    return unittest.skipUnless(path.exists(), "test image not found: %s" % path)


def _assert_squad(tc, result, view, expected_names):
    """Common assertions reused by every test."""
    tc.assertEqual(result["status"], "VALID",
                   "Scanner returned %s: %s" % (result["status"], result.get("message")))
    tc.assertEqual(result["view_type"], view)
    tc.assertEqual(len(result["players"]), 15,
                   "Expected 15 players, got %d: %s" % (
                       len(result["players"]),
                       [p["name"] for p in result["players"]]))
    tc.assertEqual(sum(1 for p in result["players"] if p["is_starting"]), 11)
    got = {_norm(p["name"]) for p in result["players"]}
    for n in expected_names:
        tc.assertIn(n, got,
                    "Expected %r in scanned names.\nGot: %s" % (n, sorted(got)))


# ── test cases ────────────────────────────────────────────────────────────────

class TestScannerScreenshots(unittest.TestCase):

    @_skip("V1 Test Pitch View.jpg")
    def test_v1_pitch_view(self):
        """Original test squad: Pickford / Van Hecke / Thiaw / Muñoz / …"""
        _assert_squad(self, _scan("V1 Test Pitch View.jpg"), "PITCH", [
            # GKP
            "pickford",
            # DEF
            "vanhecke", "thiaw", "munoz",
            # MID
            "degaard",      # Ødegaard — Ø has no ASCII equivalent
            "msangare",     # M.Sangaré
            "rogers", "szoboszlai", "cherki",
            # FWD
            "haaland", "wissa",
            # Bench
            "dubravka",
            "joaopedro",    # João Pedro
            "keane", "calafiori",
        ])

    @_skip("fpl test prod 2.jpg")
    def test_prod_squad_2(self):
        """Second production squad: Sels / Hall / Tarkowski / N.Williams / …"""
        _assert_squad(self, _scan("fpl test prod 2.jpg"), "PITCH", [
            # GKP
            "sels",
            # DEF
            "hall", "tarkowski", "nwilliams",
            # MID
            "gibbswhite", "palmer", "saka", "cherki",
            # FWD
            "barry", "haaland", "wissa",
            # Bench
            "forster", "konsa", "oshea", "slater",
        ])

    @_skip("list-view-example.png")
    def test_list_view(self):
        """List View example: same squad as pitch-view-example, different layout."""
        _assert_squad(self, _scan("list-view-example.png"), "LIST", [
            "pickford",
            "calafiori", "diop", "kayode",
            "cherki", "gibbswhite", "rogers", "semenyo",
            "isak", "haaland", "barry",
            "verbruggen",
            "yalcouye",     # Yalcouyé
            "decuyper",     # De Cuyper
            "mendy",
        ])

    @_skip("pitch-view-example.jpg")
    def test_pitch_view_example(self):
        """Pitch View example: Pickford / Kayode / Diop / Calafiori / …"""
        _assert_squad(self, _scan("pitch-view-example.jpg"), "PITCH", [
            "pickford",
            "kayode", "diop", "calafiori",
            "rogers", "gibbswhite", "cherki", "semenyo",
            "barry", "haaland", "isak",
            "verbruggen",
            "yalcouye",
            "decuyper",
            "mendy",
        ])

    @_skip("screenshot fpl team .jpg")
    def test_screenshot_fpl_team(self):
        """Same squad as pitch-view-example — smoke-tests a different source image."""
        r = _scan("screenshot fpl team .jpg")
        self.assertEqual(r["status"], "VALID")
        self.assertEqual(len(r["players"]), 15)
        self.assertEqual(r["view_type"], "PITCH")

    @_skip("fpl pitch 3.jpeg")
    def test_fpl_pitch_3(self):
        """Opponent-fixture display mode (HUL A, COV A, etc.) + kit noise above GKP.
        Previously returned 'Ste' as GKP instead of Pickford due to kit fragment
        creating an extra cluster. Fixed by name-length tiebreaker in window selection."""
        _assert_squad(self, _scan("fpl pitch 3.jpeg"), "PITCH", [
            "pickford",
            "thiaw", "munoz", "keane",
            "rogers", "degaard", "szoboszlai", "cherki",
            "wissa", "joaopedro", "haaland",
            "dubravka", "msangare", "vanhecke", "calafiori",
        ])


if __name__ == "__main__":
    unittest.main(verbosity=2)
