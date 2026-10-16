"""Manual bench / captain step: roster offer and the VALID-shaped result built from the user's answers."""
import unittest

from fpl.engine.manual_squad import ManualSquadError, manual_scan_result, roster_offer
from fpl.engine.pipeline import run_pipeline
from fpl.engine.projection import build_params
from fpl.engine.tests.test_transfer_adapter import element

SHAPE = [(1, 2), (2, 5), (3, 5), (4, 3)]
NAMES = {1: ["Alphagk", "Betagk"], 2: ["Dalpha", "Dbeta", "Dgamma", "Ddelta", "Depsilon"],
         3: ["Malpha", "Mbeta", "Mgamma", "Mdelta", "Mepsilon"], 4: ["Falpha", "Fbeta", "Fgamma"]}


def bootstrap():
    els, pid = [], 1
    for pos, names in NAMES.items():
        for n in names:
            els.append(element(pid, pos, 1 + pid % 10, price=50, quality=0.1, name=n))
            pid += 1
    return {"elements": els, "teams": [{"id": t, "name": "T%d" % t, "short_name": "T%02d" % t} for t in range(1, 12)],
            "events": [{"id": 7, "is_next": True}]}


ALL = [n for pos in (1, 2, 3, 4) for n in NAMES[pos]]
BENCH = ["Betagk", "Depsilon", "Mepsilon", "Fgamma"]


def scan(names=ALL):
    return {"status": "PARTIAL", "players": [{"name": n, "is_starting": True} for n in names]}


def req(**kw):
    base = {"names": ALL, "bench": BENCH, "captain": "Falpha", "vice": "Malpha"}
    base.update(kw)
    return base


class TestRosterOffer(unittest.TestCase):
    def test_offered_for_a_legal_fifteen(self):
        r = roster_offer(scan(), bootstrap())
        self.assertEqual(len(r), 15)
        self.assertEqual({x["position"] for x in r}, {1, 2, 3, 4})
        self.assertTrue(all(x["team"].startswith("T") for x in r))

    def test_not_offered_without_fifteen_names(self):
        self.assertIsNone(roster_offer(scan(ALL[:14]), bootstrap()))
        self.assertIsNone(roster_offer(scan(ALL + ["Extra"]), bootstrap()))
        self.assertIsNone(roster_offer({"players": []}, bootstrap()))

    def test_not_offered_for_duplicate_names(self):
        self.assertIsNone(roster_offer(scan(ALL[:14] + [ALL[0]]), bootstrap()))

    def test_not_offered_when_a_name_does_not_resolve(self):
        self.assertIsNone(roster_offer(scan(ALL[:14] + ["Nobodyatall"]), bootstrap()))

    def test_not_offered_for_an_illegal_squad_shape(self):
        b = bootstrap()
        b["elements"][2]["element_type"] = 1          # 3 GKP, 4 DEF
        self.assertIsNone(roster_offer(scan(), b))

    def test_not_offered_when_ambiguous(self):
        b = bootstrap()
        b["elements"].append(dict(b["elements"][0], id=999))   # two players called Alphagk
        self.assertIsNone(roster_offer(scan(), b))


class TestManualResult(unittest.TestCase):
    def test_valid_answers_give_a_valid_scan_result(self):
        r = manual_scan_result(req(), bootstrap())
        self.assertEqual(r["status"], "VALID")
        self.assertEqual(len(r["players"]), 15)
        self.assertEqual(sorted(p["name"] for p in r["players"] if not p["is_starting"]), sorted(BENCH))
        self.assertEqual((r["captain_name"], r["vice_captain_name"]), ("Falpha", "Malpha"))

    def test_captain_is_optional(self):
        r = manual_scan_result(req(captain=None, vice=None), bootstrap())
        self.assertIsNone(r["captain_name"])
        self.assertIsNone(r["vice_captain_name"])

    def test_rejects_bad_bench(self):
        b = bootstrap()
        for bad, why in [
            (BENCH[:3], "count"), (["Betagk", "Alphagk", "Mepsilon", "Fgamma"], "two gks"),
            (["Depsilon", "Mepsilon", "Fgamma", "Dalpha"], "no gk"),
            (["Betagk", "Dbeta", "Dgamma", "Depsilon"], "illegal formation (2 DEF)"),
            (["Betagk", "Depsilon", "Mepsilon", "Mepsilon"], "duplicate"),
            (["Betagk", "Depsilon", "Mepsilon", "Stranger"], "not in squad"),
            ("not a list", "type")]:
            with self.subTest(why), self.assertRaises(ManualSquadError):
                manual_scan_result(req(bench=bad), b)

    def test_rejects_bad_captain_choices(self):
        b = bootstrap()
        for kw, why in [({"captain": "Depsilon"}, "bench captain"), ({"vice": "Mepsilon"}, "bench vice"),
                        ({"vice": "Falpha"}, "same player"), ({"captain": None, "vice": "Malpha"}, "vice only"),
                        ({"captain": "Stranger"}, "not in squad"), ({"captain": 7}, "type")]:
            with self.subTest(why), self.assertRaises(ManualSquadError):
                manual_scan_result(req(**kw), b)

    def test_rejects_bad_names(self):
        b = bootstrap()
        for names in (ALL[:14], ALL[:14] + [ALL[0]], ALL[:14] + [""], ALL[:14] + ["x" * 61], None):
            with self.subTest(str(names)[:30]), self.assertRaises(ManualSquadError):
                manual_scan_result(req(names=names), b)

    def test_rejects_non_dict(self):
        with self.assertRaises(ManualSquadError):
            manual_scan_result([], bootstrap())

    def test_result_runs_through_the_unchanged_pipeline(self):
        b = bootstrap()
        r = manual_scan_result(req(), b)
        ids = {e["web_name"]: e["id"] for e in b["elements"]}
        r["captain_id"], r["vice_captain_id"] = ids["Falpha"], ids["Malpha"]
        out = run_pipeline(scan_result=r, bootstrap=b, element_summaries={e["id"]: {"history": [], "fixtures": []} for e in b["elements"]},
                           params=build_params(b), threshold=0.5)
        self.assertEqual(out["status"], "OK", out.get("message"))
        self.assertEqual(sum(1 for p in out["players"] if p["is_starting"]), 11)
        self.assertEqual(out["captain_id"], ids["Falpha"])


if __name__ == "__main__":
    unittest.main()
