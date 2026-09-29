"""
Screenshot integration tests using recorded Google Vision fixtures.

Both local dev and Vercel use Google Vision — these tests replay real
GV responses so local failures == production failures.

To update fixtures (run once with a real key):
    GOOGLE_VISION_API_KEY=<key> python3 fpl/engine/tests/update_fixtures.py

To run tests:
    python3 -m unittest fpl.engine.tests.test_scanner_screenshots -v
"""

import io, json, re, sys, unittest, unicodedata
from pathlib import Path

_FIX_DIR = Path(__file__).resolve().parent.parent.parent / "test_images" / "fixtures"


def _norm(name):
    nfkd = unicodedata.normalize("NFKD", name)
    return re.sub(r"[^a-z]", "", "".join(c for c in nfkd if unicodedata.category(c) != "Mn").lower())


def _make_elements(*web_names):
    return [{"web_name": n, "second_name": n, "known_name": None, "removed": False}
            for n in web_names]


def _scan(stem, elements):
    from fpl.engine.scanner import _parse_gv_response, _dispatch_blocks
    fix = _FIX_DIR / f"{stem}.json"
    resp = json.loads(fix.read_text()).get("responses", [{}])[0]
    blocks = _parse_gv_response(resp)
    buf = io.StringIO(); old = sys.stderr; sys.stderr = buf
    result = _dispatch_blocks(blocks, elements)
    sys.stderr = old
    return result


def _skip(stem):
    return unittest.skipUnless((_FIX_DIR / f"{stem}.json").exists(),
                                f"no fixture for {stem} — run update_fixtures.py")


def _assert(tc, result, view, names):
    tc.assertEqual(result["status"], "VALID",
                   f"{result['status']}: {result.get('message')}")
    tc.assertEqual(result["view_type"], view)
    tc.assertGreaterEqual(len(result["players"]), 15)
    tc.assertGreaterEqual(sum(1 for p in result["players"] if p["is_starting"]), 11)
    got = {_norm(p["name"]) for p in result["players"]}
    for n in names:
        tc.assertIn(n, got, f"Missing {n!r} in {sorted(got)}")


class TestScannerScreenshots(unittest.TestCase):

    @_skip("V1 Test Pitch View")
    def test_v1_pitch_view(self):
        els = _make_elements(
            "Pickford", "Van Hecke", "Thiaw", "Muñoz", "Ødegaard",
            "M.Sangaré", "Rogers", "Szoboszlai", "Cherki",
            "Haaland", "Wissa", "Dubravka", "João Pedro", "Keane", "Calafiori",
        )
        _assert(self, _scan("V1 Test Pitch View", els), "PITCH", [
            "pickford", "vanhecke", "thiaw", "munoz",
            "degaard", "msangare", "rogers", "szoboszlai", "cherki",
            "haaland", "wissa", "dubravka", "joaopedro", "keane", "calafiori",
        ])

    @_skip("fpl test prod 2")
    def test_prod_squad_2(self):
        els = _make_elements(
            "Sels", "Hall", "Tarkowski", "N.Williams", "Gibbs-White",
            "Palmer", "Saka", "Cherki", "Barry", "Haaland", "Wissa",
            "Forster", "Konsa", "O'Shea", "Slater",
        )
        _assert(self, _scan("fpl test prod 2", els), "PITCH", [
            "sels", "hall", "tarkowski", "nwilliams",
            "gibbswhite", "palmer", "saka", "cherki",
            "barry", "haaland", "wissa",
            "forster", "konsa", "oshea", "slater",
        ])

    @_skip("list-view-example")
    def test_list_view(self):
        els = _make_elements(
            "Pickford", "Calafiori", "Diop", "Kayode", "Cherki",
            "Gibbs-White", "Rogers", "Semenyo", "Isak", "Haaland", "Barry",
            "Verbruggen", "Yalcouye", "Mendy", "De Cuyper",
        )
        _assert(self, _scan("list-view-example", els), "LIST", [
            "pickford", "calafiori", "diop", "kayode",
            "cherki", "gibbswhite", "rogers", "semenyo",
            "isak", "haaland", "barry",
            "verbruggen", "yalcouye", "mendy",
        ])

    @_skip("pitch-view-example")
    def test_pitch_view_example(self):
        els = _make_elements(
            "Pickford", "Kayode", "Diop", "Calafiori", "Rogers",
            "Gibbs-White", "Cherki", "Semenyo", "Barry", "Haaland", "Isak",
            "Verbruggen", "Yalcouye", "De Cuyper", "Mendy",
        )
        _assert(self, _scan("pitch-view-example", els), "PITCH", [
            "pickford", "kayode", "diop", "calafiori",
            "rogers", "gibbswhite", "cherki", "semenyo",
            "barry", "haaland", "isak",
            "verbruggen", "yalcouye", "decuyper", "mendy",
        ])

    @_skip("fpl pitch 3")
    def test_fpl_pitch_3(self):
        els = _make_elements(
            "Pickford", "Thiaw", "Muñoz", "Keane", "Rogers",
            "Ødegaard", "Szoboszlai", "Cherki", "Wissa", "João Pedro", "Haaland",
            "Dubravka", "M.Sangaré", "Van Hecke", "Calafiori",
        )
        _assert(self, _scan("fpl pitch 3", els), "PITCH", [
            "pickford", "thiaw", "munoz", "keane",
            "rogers", "degaard", "szoboszlai", "cherki",
            "wissa", "joaopedro", "haaland",
            "dubravka", "msangare", "vanhecke", "calafiori",
        ])


    @_skip("FPL test pitch 6")
    def test_fpl_pitch_6(self):
        els = _make_elements(
            "Raya", "Gabriel", "Tarkowski", "Hall",
            "Gibbs-White", "B.Fernández", "Palmer", "Szoboszlai", "Schade",
            "Wissa", "Barry",
            "Steele", "Konsa", "De Cuyper", "João Pedro",
        )
        _assert(self, _scan("FPL test pitch 6", els), "PITCH", [
            "raya", "gabriel", "tarkowski", "hall",
            "gibbswhite", "bfernandez", "palmer", "szoboszlai", "schade",
            "wissa", "barry",
            "steele", "konsa", "decuyper", "joaopedro",
        ])


if __name__ == "__main__":
    unittest.main(verbosity=2)
