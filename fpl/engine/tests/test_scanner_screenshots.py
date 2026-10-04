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


# ── Synthetic layout (not a recorded Vision response) ─────────────────────────
# Mirrors a cropped new-layout pitch screenshot: plain GKP/FWD/DEF/DEF bench
# labels, no "Substitutes" text. Coordinates follow the production diagnostics
# convention (larger y = higher on screen).

_CROPPED_ROWS = [   # (y, [(name, team token), ...])
    (0.75, [("Tzolakis", "EVE ( H )")]),
    (0.59, [("Vuskovic", "SUN ( A )"), ("Konsa", "LEE ( H )"), ("Calafiori", "LEE ( H )")]),
    (0.43, [("Semenyo", "LIV ( A )"), ("Schade", "AVL ( A )"), ("Saka", "LEE ( H )"),
            ("Groß", "SUN ( A )"), ("Rogers", "BOU ( H )")]),
    (0.27, [("Haaland", "LIV ( A )"), ("João Pedro", "BOU ( H )")]),
    (0.08, [("Leno", "IPS ( A )"), ("Kostoulas", "SUN ( A )"), ("Thomas", "NEW ( H )"),
            ("Muharemović", "ARS ( A )")]),
]
_CROPPED_STARTERS = {"Tzolakis", "Vuskovic", "Konsa", "Calafiori", "Semenyo", "Schade",
                     "Saka", "Groß", "Rogers", "Haaland", "João Pedro"}


def _cropped_blocks(rows=_CROPPED_ROWS):
    blocks = [{"text": t, "x": 0.1 + 0.2 * i, "y": 0.13, "w": 0.05, "h": 0.01}
              for i, t in enumerate(("GKP", "FWD", "DEF", "DEF"))]
    blocks += [{"text": "Pitch", "x": 0.15, "y": 0.92, "w": 0.07, "h": 0.01},
               {"text": "List", "x": 0.41, "y": 0.92, "w": 0.05, "h": 0.01}]
    for y, row in rows:
        for i, (name, team) in enumerate(row):
            x = 0.1 + 0.15 * i
            blocks.append({"text": name, "x": x, "y": y, "w": 0.08, "h": 0.01})
            blocks.append({"text": team, "x": x, "y": y - 0.025, "w": 0.08, "h": 0.013})
    return blocks


def _dispatch(blocks, elements):
    from fpl.engine.scanner import _dispatch_blocks
    buf = io.StringIO(); old = sys.stderr; sys.stderr = buf
    try:
        return _dispatch_blocks(blocks, elements)
    finally:
        sys.stderr = old


class TestCroppedNoBenchMarker(unittest.TestCase):
    NAMES = [n for _, row in _CROPPED_ROWS for n, _ in row]

    def test_xi_inferred_from_vertical_order(self):
        r = _dispatch(_cropped_blocks(), _make_elements(*self.NAMES))
        self.assertEqual(r["status"], "VALID", r.get("message"))
        self.assertEqual({p["name"] for p in r["players"] if p["is_starting"]}, _CROPPED_STARTERS)
        self.assertEqual(sum(1 for p in r["players"] if not p["is_starting"]), 4)
        self.assertTrue(any("inferred from vertical order" in w for w in r["warnings"]))

    def test_no_guess_when_split_cuts_through_a_row(self):
        rows = [(0.75, _CROPPED_ROWS[0][1]), (0.59, _CROPPED_ROWS[1][1]), (0.43, _CROPPED_ROWS[2][1]),
                (0.27, _CROPPED_ROWS[3][1]), (0.265, _CROPPED_ROWS[4][1])]   # bench row ~ FWD row
        r = _dispatch(_cropped_blocks(rows), _make_elements(*self.NAMES))
        self.assertNotEqual(r["status"], "VALID")

    def test_validate_rejects_more_than_11_starters(self):
        from fpl.engine.scanner import scan_squad
        players = [{"name": "P%d" % i, "position": 3, "is_starting": True} for i in range(15)]
        r = scan_squad({"players": players})
        self.assertEqual(r["status"], "PARTIAL")
        self.assertIn("expected 11", r["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
