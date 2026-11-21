"""
Post-deploy smoke test against the real site: python3 scripts/smoke_live.py [base_url]

The unit tests mock FPL, so they cannot see problems that only show up from Vercel's network
(FPL answering 403 to a cold instance, a missing route, a function that never deployed).
Run this after every deploy. Exit code 1 if any check fails.
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://www.squadcheck.club").rstrip("/")
FPL_ID = 1234567          # any real, public FPL team


def call(path, body=None, method=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json", "User-Agent": "squadcheck-smoke/1.0"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw), time.time() - t
            except ValueError:
                return r.status, {}, time.time() - t
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read()), time.time() - t
        except Exception:
            return e.code, {}, time.time() - t


failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not ok:
        failures.append(name)


st, _, _ = call("/fpl/")
check("home page loads", st == 200)

st, team, dt = call("/api/fpl-team", {"fpl_id": FPL_ID})
check("fpl-team lookup", st == 200 and team.get("status") == "OK", "%s %s %.1fs" % (st, team.get("error", ""), dt))
st, _, _ = call("/api/fpl-team", {"fpl_id": "abc"})
check("fpl-team rejects a bad ID", st == 400)

if team.get("manual_squad"):
    st, res, dt = call("/api/squad-check", {"manual_squad": team["manual_squad"]})
    check("squad-check from that team", st == 200 and res.get("status") == "OK", "%s %.1fs" % (st, dt))
    if res.get("status") == "OK":
        ids = [p["player_id"] for p in res["players"]]
        st, ch, dt = call("/api/chips", {"squad_ids": ids, "bank_m": 0.5, "free_transfers": 1,
                                          "chips_available": ["bboost", "3xc"]})
        check("chips", st == 200 and "headline" in ch, "%s %s %.1fs" % (st, ch.get("error", ""), dt))
        st, tp, dt = call("/api/transfer-plan", {"squad_ids": ids, "bank_m": 0.5, "free_transfers": 1})
        check("transfer-plan", st == 200, "%s %s %.1fs" % (st, tp.get("error", ""), dt))

st, pl, _ = call("/api/players")
check("players directory", st == 200 and pl.get("count", 0) > 300)

print(("\n%d check(s) failed" % len(failures)) if failures else "\nall checks passed")
sys.exit(1 if failures else 0)
