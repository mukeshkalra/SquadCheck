"""
Tests for the results page in fpl/index.html (Chunk 8): the four agreed blocks, the Projection view, and the
results-to-planner handover. The page's own inline script runs in Node against a stub DOM, so these tests need
`node` on the PATH and skip cleanly without it. No network, no live FPL: payloads are built here.

The handover writer is checked against the planner's own reader (PlannerCore.parseHandoff in fpl/planner.html),
so the two sides of the documented schema cannot drift apart unnoticed.
"""

import json
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INDEX = ROOT / "fpl" / "index.html"
PLANNER = ROOT / "fpl" / "planner.html"
HTML = INDEX.read_text(encoding="utf-8")
NODE = shutil.which("node")
CORE = re.search(r"/\* CORE:BEGIN.*?\*/(.*?)/\* CORE:END \*/", PLANNER.read_text(encoding="utf-8"), re.S).group(1)
KEY = "squadcheck.planner.handoff.v1"

POS = {1: [1, 2], 2: [3, 4, 5, 6, 7], 3: [8, 9, 10, 11, 12], 4: [13, 14, 15]}
STARTERS = {1, 3, 4, 5, 6, 8, 9, 10, 11, 13, 14}


def player(pid, pos):
    return {"player_id": pid, "web_name": "Player%d" % pid, "position": pos, "team_id": pid, "xPts": round(2 + (pid % 7) * 0.6, 1),
            "confidence": "HIGH", "is_starting": pid in STARTERS, "player_api": {"photo_code": 1000 + pid},
            "inputs": {"xMins": 85.0, "atk_scale": 1.05, "opp_xgc_d": 1.1}, "components": {}, "drivers": [], "risks": []}


def payload(**kw):
    players = [player(pid, pos) for pos, ids in POS.items() for pid in ids]
    xi = sum(p["xPts"] for p in players if p["is_starting"])
    p = {"players": players, "gameweek": 7, "action": "HOLD", "delta": 0.4, "captain_id": 14, "vice_captain_id": 13,
         "submitted_xi": {"xPts": round(xi, 1), "formation": "4-4-2"}, "recommended_xi": {"xPts": round(xi, 1)}, "substitutions": [], "status": "OK"}
    p.update(kw)
    return p


def run_page(actions, pl=None, storage_blocked=False, extra=None):
    """Run the page's inline script on a stub DOM and return a snapshot after each action."""
    if not NODE:
        raise unittest.SkipTest("node is not installed")
    data = {"path": str(INDEX), "payload": pl if pl is not None else payload(), "actions": actions, "storageBlocked": storage_blocked}
    data.update(extra or {})
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "run.js"
        f.write_text(HARNESS, encoding="utf-8")
        r = subprocess.run([NODE, str(f)], input=json.dumps(data), capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise AssertionError("node failed:\n" + r.stderr[-2500:])
    return json.loads(r.stdout)


HARNESS = r"""
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = fs.readFileSync(input.path, 'utf8');
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).find(s => s.includes('let _P = null'));
const els = {}, screens = ['s-upload', 's-loading', 's-disambig', 's-error', 's-result', 's-proj'];
const mk = id => els[id] || (els[id] = {id, innerHTML: '', textContent: '', style: {}, value: '', checked: false, dataset: {}, children: [],
  classList: {_s: new Set(), add(...a) { a.forEach(x => this._s.add(x)); }, remove(...a) { a.forEach(x => this._s.delete(x)); },
              toggle(x, f) { ((f === undefined) ? !this._s.has(x) : f) ? this._s.add(x) : this._s.delete(x); }, contains(x) { return this._s.has(x); }},
  addEventListener() {}, removeEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {},
  getAttribute() { return null; }, scrollIntoView() {}, focus() {}, closest() { return null; }, appendChild() {}, remove() {}});
screens.forEach(mk);
const missing = new Set(input.missingIds || []);
const document = {getElementById: id => (missing.has(id) ? null : mk(id)), querySelector: () => mk('_q'), querySelectorAll: sel => (sel === '.screen' ? screens.map(mk) : []),
                  addEventListener() {}, createElement: () => mk('_c' + Math.random()), body: mk('_body'), documentElement: mk('_html'), readyState: 'complete'};
const storage = {};
let blockNow = false;                      // storage is blocked only after the page has loaded (its own session id is written at load)
const sessionStorage = {getItem: k => (k in storage ? storage[k] : null), removeItem: k => { delete storage[k]; },
                        setItem: (k, v) => { if (blockNow) throw new Error('blocked'); storage[k] = String(v); }};
const localStorage = {getItem: () => null, setItem() {}, removeItem() {}};
const location = {search: '', href: 'http://localhost/fpl/', pathname: '/fpl/'};
class IO { observe() {} unobserve() {} disconnect() {} }
const winListeners = {};
const win = {scrollTo() {}, addEventListener(t, f) { (winListeners[t] = winListeners[t] || []).push(f); }, matchMedia: () => ({matches: false, addEventListener() {}}), innerWidth: 400};
const quiet = {log() {}, warn() {}, error() {}, info() {}};
const api = new Function('document', 'sessionStorage', 'localStorage', 'location', 'IntersectionObserver', 'window', 'navigator', 'console', 'fetch',
  'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'requestAnimationFrame', 'URLSearchParams', 'history',
  script + '\n;return {renderResult, openPlanner, openProjection, backToResults, plannerHandoff, selectCaptain, show, setP: v => { _P = v; }};')(
  document, sessionStorage, localStorage, location, IO, win, {userAgent: '', share: undefined}, quiet, async () => ({ok: false, json: async () => ({})}),
  () => 0, () => {}, () => 0, () => {}, f => 0, URLSearchParams, {pushState() {}, replaceState() {}});
blockNow = !!input.storageBlocked;
const ids = ['r-hero', 'r-planner', 'r-attention', 'r-captain', 'r-finalcall', 'r-players', 'r-awesome', 'pl-note'];
const snap = name => ({name, html: Object.fromEntries(ids.map(i => [i, els[i] ? els[i].innerHTML : null])), note: els['pl-note'] ? {text: els['pl-note'].textContent, display: els['pl-note'].style.display} : null,
                       active: screens.filter(s => els[s].classList.contains('active')),
                       storage: Object.fromEntries(Object.entries(storage).filter(([k]) => k.startsWith('squadcheck.planner'))), href: location.href});
const out = [];
for (const a of input.actions) {
  if (a === 'render') { api.setP(input.payload); api.renderResult(input.payload); }
  else if (a === 'planner') api.openPlanner();
  else if (a === 'projection') api.openProjection();
  else if (a === 'back') api.backToResults();
  else if (a === 'installprompt') { let prevented = false, prompted = false; const e = {preventDefault() { prevented = true; }, prompt() { prompted = true; }, userChoice: Promise.resolve({outcome: 'dismissed'})};
    let err = null; try { (winListeners.beforeinstallprompt || []).forEach(f => f(e)); } catch (x) { err = String(x); }
    out.push({name: a, err, prevented, listeners: (winListeners.beforeinstallprompt || []).length}); continue; }
  else if (a.startsWith('captain:')) api.selectCaptain(+a.split(':')[1]);
  else if (a === 'handoff') { out.push({name: a, result: api.plannerHandoff(input.payload)}); continue; }
  out.push(snap(a));
}
console.log(JSON.stringify(out));
"""


class TestStructure(unittest.TestCase):
    """The markup: four blocks in order, Projection on its own view, nothing lost, home page untouched."""

    def section(self, sid):
        if sid == "s-upload":
            m = re.search(r'<section id="s-upload".*?(?=<section id="s-loading")', HTML, re.S)
        else:
            m = re.search(r'<section id="%s".*?</section><!-- /%s -->' % (sid, sid), HTML, re.S)
        self.assertIsNotNone(m, sid)
        return m.group(0)

    def test_the_results_page_has_the_four_blocks_in_order(self):
        res = self.section("s-result")
        # Redesign (2026-10-16): Projection, then "Your calls this week" = planner card + tap-to-open Bench and Captain tiles.
        order = [res.index(i) for i in ('id="blk-proj"', 'id="blk-calls"', 'id="r-planner"', 'id="ct-bench"', 'id="ct-capt"')]
        self.assertEqual(order, sorted(order))
        self.assertIn('<span class="rb-t">Projection</span>', res)
        self.assertIn('<span class="rb-t">Your calls this week</span>', res)
        self.assertRegex(res, r'id="blk-proj">\s*<div class="rb-hd"><span class="rb-no">1</span>')
        for tile in ("ct-bench", "ct-capt"):
            self.assertRegex(res, r'<button type="button" class="ct" id="%s" aria-expanded="false" aria-controls="' % tile)

    def test_each_existing_renderer_still_has_its_container_inside_the_right_block(self):
        res = self.section("s-result")
        pos = lambda s: res.index(s)
        self.assertTrue(pos('id="blk-proj"') < pos('id="r-hero"') < pos('id="blk-calls"'))
        self.assertTrue(pos('id="blk-calls"') < pos('id="r-planner"') < pos('id="r-calls"'))
        self.assertTrue(pos('id="r-calls"') < pos('id="cp-bench"') < pos('id="r-attention"') < pos('id="cp-capt"') < pos('id="r-captain"') < pos('id="r-finalcall"'))

    def test_player_by_player_detail_lives_on_its_own_view_with_a_way_back(self):
        res, proj = self.section("s-result"), self.section("s-proj")
        self.assertIn('id="r-players"', proj)
        self.assertIn('id="r-awesome"', proj)
        self.assertNotIn('id="r-players"', res)
        self.assertNotIn('id="r-awesome"', res)
        self.assertEqual(proj.count('onclick="backToResults()"'), 2)
        self.assertIn("Who's carrying your week", proj)

    def test_everything_users_still_need_on_the_results_page_is_still_there(self):
        res = self.section("s-result")
        for needle in ('id="r-finalcall"', "10+ factors go into every forecast.", "How did this feel?", 'onclick="doShare()"',
                       'onclick="goUpload()"', 'id="a2hs-result"', "Forecasts use current-season FPL data.", "fbRate('loved')"):
            self.assertIn(needle, res)

    def test_the_planner_is_entered_from_the_results_page_only(self):
        self.assertEqual(HTML.count("location.href = '/fpl/planner.html'"), 1)            # one entry point, in openPlanner()
        self.assertEqual(re.findall(r"""href=["'][^"']*planner""", HTML), [])               # no plain link that would skip the handover

    def test_the_no_login_trust_line_is_unchanged(self):
        up = self.section("s-upload")
        self.assertIn("<span>✓ No login</span>", up)
        self.assertIn("<span>✓ No FPL ID</span>", up)
        self.assertIn("<span>✓ Free</span>", up)

    def test_existing_analytics_events_are_kept_and_new_ones_registered(self):
        before = subprocess.run(["git", "show", "fb219e6:fpl/index.html"], cwd=ROOT, capture_output=True, text=True)
        if before.returncode:
            self.skipTest("no git history available")
        # Instrumentation was extended on purpose (see fpl/INSTRUMENTATION.md): nothing removed or renamed,
        # and every added event is registered with the analyst.
        from fpl.analytics.queries import _EXPECTED_EVENTS
        events = lambda s: set(re.findall(r"track\('([a-z_]+)'", s))
        self.assertLessEqual(events(before.stdout), events(HTML))
        self.assertEqual(sorted(e for e in events(HTML) - events(before.stdout) if e not in _EXPECTED_EVENTS), [])

    def test_no_account_or_saving_promises_were_introduced(self):
        res = self.section("s-result") + self.section("s-proj")
        new_js = HTML[HTML.index("/* ── PROJECTION VIEW"):HTML.index("/* ── PLAYERS")]
        for text in (res, new_js):
            for claim in ("sign in", "sign-in", "account", "saved", "we'll email", "review"):
                self.assertNotIn(claim, re.sub(r"<!--.*?-->", "", text).lower() if text is res else text.lower().replace("we couldn't", ""))


class TestHandoverSchema(unittest.TestCase):
    def handoff(self, pl):
        return run_page(["handoff"], pl)[0]["result"]

    def test_a_valid_squad_produces_exactly_the_documented_schema(self):
        before = int(time.time() * 1000)
        r = self.handoff(payload())
        self.assertTrue(r["ok"])
        d = r["data"]
        self.assertEqual(set(d), {"v", "squad_ids", "gameweek", "created"})
        self.assertEqual(d["v"], 1)
        self.assertEqual(d["gameweek"], 7)
        self.assertEqual(d["squad_ids"], list(range(1, 16)))
        self.assertGreaterEqual(d["created"], before - 1000)
        self.assertLessEqual(d["created"], int(time.time() * 1000) + 1000)

    def test_a_missing_gameweek_is_written_as_null_not_invented(self):
        for gw in (None,):
            pl = payload(gameweek=gw)
            self.assertIsNone(self.handoff(pl)["data"]["gameweek"])
        pl = payload()
        del pl["gameweek"]
        self.assertIsNone(self.handoff(pl)["data"]["gameweek"])

    def test_the_planners_own_reader_accepts_what_the_page_writes(self):
        d = self.handoff(payload())["data"]
        reader = CORE + "\nconst d = JSON.parse(require('fs').readFileSync(0,'utf8'));\nconsole.log(JSON.stringify([PlannerCore.parseHandoff(JSON.stringify(d), d.created + 5000), PlannerCore.parseHandoff(JSON.stringify(d), d.created + 13*3600*1000)]));"
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "r.js"
            f.write_text(reader)
            out = json.loads(subprocess.run([NODE, str(f)], input=json.dumps(d), capture_output=True, text=True, timeout=30).stdout)
        self.assertTrue(out[0]["ok"])
        self.assertEqual((out[0]["squad_ids"], out[0]["gameweek"]), (list(range(1, 16)), 7))
        self.assertEqual((out[1]["ok"], out[1]["reason"]), (False, "stale"))                  # an old handover is refused by the planner

    def test_invalid_squads_are_refused_and_never_padded(self):
        bad = {}
        pl = payload(); pl["players"] = pl["players"][:14]; bad["14 players"] = pl
        pl = payload(); pl["players"] = pl["players"] + [player(16, 4)]; bad["16 players"] = pl
        pl = payload(); pl["players"][1]["player_id"] = 1; bad["duplicate id"] = pl
        pl = payload(); pl["players"][0]["player_id"] = "1"; bad["string id"] = pl
        pl = payload(); pl["players"][0]["player_id"] = 1.5; bad["fractional id"] = pl
        pl = payload(); pl["players"][0]["player_id"] = 0; bad["zero id"] = pl
        pl = payload(); pl["players"][0]["player_id"] = None; bad["null id"] = pl
        pl = payload(); pl["players"][0]["position"] = 2; bad["wrong composition"] = pl
        pl = payload(); pl["players"][2]["position"] = 9; bad["unknown position"] = pl
        pl = payload(); del pl["players"]; bad["no players"] = pl
        pl = payload(); pl["players"] = "nope"; bad["players not a list"] = pl
        pl = payload(); pl["players"][3] = None; bad["null player"] = pl
        for name, pl in bad.items():
            r = self.handoff(pl)
            self.assertFalse(r["ok"], name)
            self.assertIn("planner can't start", r["message"], name)
            self.assertNotIn("data", r, name)

    def test_invalid_gameweeks_are_refused(self):
        for gw in (0, -3, 60, 99, 1.5, "7", "x", True, [7]):
            r = self.handoff(payload(gameweek=gw))
            self.assertFalse(r["ok"], gw)
            self.assertIn("gameweek", r["message"].lower())

    def test_a_missing_payload_is_refused(self):
        r = run_page(["handoff"], pl={}, extra={})[0]["result"]
        self.assertFalse(r["ok"])


class TestResultsRendering(unittest.TestCase):
    def test_every_block_renders_and_the_page_does_not_crash(self):
        s = run_page(["render"])[0]
        for key in ("r-hero", "r-planner", "r-attention", "r-captain", "r-finalcall", "r-players", "r-awesome"):
            self.assertTrue(s["html"][key], key)

    def test_projection_block_has_the_total_the_top_players_and_a_clear_way_to_the_detail(self):
        s = run_page(["render"])[0]
        h = s["html"]["r-hero"]
        pl = payload()
        starts = [p for p in pl["players"] if p["is_starting"]]
        total = pl["submitted_xi"]["xPts"] + next(p["xPts"] for p in starts if p["player_id"] == 14)
        self.assertIn('<div class="hero-pts">%.1f</div>' % total, h)
        self.assertIn("Gameweek 7", h)
        top = sorted(starts, key=lambda p: -p["xPts"])[:3]
        for p in top:
            self.assertIn(p["web_name"], h)
        self.assertEqual(h.count("rb-pl"), 3)
        self.assertIn('onclick="openProjection()"', h)
        self.assertIn("See who's carrying your week", h)

    def test_the_planner_block_offers_the_entry_for_a_readable_squad(self):
        h = run_page(["render"])[0]["html"]["r-planner"]
        self.assertIn("Should you use your free transfer?", h)
        self.assertIn('onclick="openPlanner()"', h)
        self.assertIn("Plan my move", h)
        for claim in ("sign in", "account", "saved", "email"):
            self.assertNotIn(claim, h.lower())

    def test_the_planner_block_fails_safe_for_an_unreadable_squad(self):
        pl = payload()
        pl["players"] = pl["players"][:14]
        h = run_page(["render"], pl)[0]["html"]["r-planner"]
        self.assertNotIn("openPlanner()", h)
        self.assertIn("the planner can't start", h)
        self.assertIn('role="alert"', h)

    def test_the_other_blocks_keep_their_content(self):
        s = run_page(["render"])[0]["html"]
        self.assertIn("Bench check", s["r-attention"])
        self.assertIn("Captain pick", s["r-captain"])
        self.assertIn("Honestly? I'd hold.", s["r-finalcall"])
        self.assertIn("pos-section", s["r-players"])
        self.assertIn("What's looking good", s["r-awesome"])

    def test_a_bench_swap_still_renders_in_the_bench_and_final_call_blocks(self):
        pl = payload(action="SWAP", delta=1.4)
        out, inn = pl["players"][3], pl["players"][11]          # a starting defender, a benched midfielder... swap needs same logic only
        pl["substitutions"] = [{"out": {"player_id": out["player_id"], "web_name": out["web_name"], "xPts": out["xPts"]},
                                "in": {"player_id": inn["player_id"], "web_name": inn["web_name"], "xPts": inn["xPts"]}}]
        pl["recommended_xi"] = {"xPts": pl["submitted_xi"]["xPts"] + 1.4}
        s = run_page(["render"], pl)[0]["html"]
        self.assertIn("Changes to make", s["r-attention"])
        self.assertIn("Make the change", s["r-finalcall"])

    def test_choosing_a_captain_still_refreshes_the_hero_players_and_final_call(self):
        pl = payload(captain_id=None)
        s = run_page(["render", "captain:13"], pl)
        self.assertIn("Who is your captain", s[0]["html"]["r-captain"])
        self.assertNotIn("Who is your captain", s[1]["html"]["r-captain"])
        self.assertNotEqual(s[0]["html"]["r-hero"], s[1]["html"]["r-hero"])


class TestNavigationAndHandover(unittest.TestCase):
    def test_projection_opens_on_its_own_view_and_the_way_back_returns(self):
        s = run_page(["render", "projection", "back"])
        self.assertEqual(s[1]["active"], ["s-proj"])
        self.assertEqual(s[2]["active"], ["s-result"])

    def test_plan_my_move_writes_the_handover_and_goes_to_the_planner(self):
        s = run_page(["render", "planner"])[1]
        self.assertEqual(list(s["storage"]), [KEY])
        d = json.loads(s["storage"][KEY])
        self.assertEqual((d["v"], d["gameweek"], d["squad_ids"]), (1, 7, list(range(1, 16))))
        self.assertEqual(set(d), {"v", "squad_ids", "gameweek", "created"})
        self.assertEqual(s["href"], "/fpl/planner.html")

    def test_an_unreadable_squad_writes_nothing_and_stays_on_the_results(self):
        pl = payload()
        pl["players"][0]["player_id"] = None
        s = run_page(["render", "planner"], pl)[1]
        self.assertEqual(s["storage"], {})
        self.assertEqual(s["href"], "http://localhost/fpl/")
        self.assertIn("can't start", s["html"]["r-planner"])

    def test_a_bad_gameweek_writes_nothing(self):
        s = run_page(["render", "planner"], payload(gameweek=99))[1]
        self.assertEqual(s["storage"], {})
        self.assertEqual(s["href"], "http://localhost/fpl/")

    def test_blocked_storage_fails_safely_with_a_message_and_no_navigation(self):
        s = run_page(["render", "planner"], storage_blocked=True)[1]
        self.assertEqual(s["storage"], {})
        self.assertEqual(s["href"], "http://localhost/fpl/")
        self.assertEqual(s["note"]["display"], "block")
        self.assertIn("blocking storage", s["note"]["text"])

    def test_a_new_check_replaces_the_previous_handover_and_never_reuses_old_data(self):
        a = run_page(["render", "planner"])[1]
        first = json.loads(a["storage"][KEY])
        time.sleep(0.01)
        pl = payload()
        for i, p in enumerate(pl["players"]):
            p["player_id"] = 100 + i
        b = run_page(["render", "planner"], pl)[1]
        second = json.loads(b["storage"][KEY])
        self.assertEqual(second["squad_ids"], list(range(100, 115)))
        self.assertNotEqual(first["squad_ids"], second["squad_ids"])

    def test_no_sample_data_is_ever_passed(self):
        s = run_page(["render", "planner"])[1]
        d = json.loads(s["storage"][KEY])
        self.assertNotIn("dev", d)
        self.assertTrue(all(k in {"v", "squad_ids", "gameweek", "created"} for k in d))


class TestAddToHomeScreen(unittest.TestCase):
    """The add-to-home-screen handler must never throw when its containers are missing (it used to)."""

    def test_install_prompt_with_missing_containers_does_not_throw(self):
        snap = run_page(["render", "installprompt"], extra={"missingIds": ["a2hs-wrap", "a2hs-result"]})[-1]
        self.assertEqual(snap["listeners"], 1)
        self.assertIsNone(snap["err"])
        self.assertTrue(snap["prevented"])

    def test_install_prompt_still_reveals_the_container_when_present(self):
        self.assertIn("_a2hsShow('a2hs-wrap','block')", HTML)
        snap = run_page(["render", "installprompt"])[-1]
        self.assertIsNone(snap["err"])

    def test_no_direct_unguarded_a2hs_lookups_remain(self):
        self.assertNotRegex(HTML, r"\$\('a2hs-(wrap|result)'\)\.style")


if __name__ == "__main__":
    unittest.main()
