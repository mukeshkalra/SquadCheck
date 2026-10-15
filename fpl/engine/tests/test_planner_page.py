"""
Tests for fpl/planner.html (Chunk 4: shell, squad handover, Game plan, player search, constraints).

The page's pure logic (PlannerCore) and its UI script are run in Node with a stub DOM, so these tests need
`node` on the PATH and skip cleanly without it. The directory the page reads is the real /api/players payload
built by transfer_adapter from the mocked FPL fixtures, and every request body the page can build is fed back
through the real transfer_adapter.parse_request, so page and API cannot drift apart unnoticed.
"""

import html as htmllib
import json
import logging
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fpl.engine.transfer_adapter import ApiError, FplDataSource, build_players_response, handle, parse_request
from fpl.engine.tests.test_transfer_adapter import SQUAD, Clock, FakeFpl, body

logging.getLogger("squadcheck.transfer").setLevel(logging.CRITICAL)
ROOT = Path(__file__).resolve().parents[3]
HTML = (ROOT / "fpl" / "planner.html").read_text(encoding="utf-8")
NODE = shutil.which("node")

CORE = re.search(r"/\* CORE:BEGIN.*?\*/(.*?)/\* CORE:END \*/", HTML, re.S).group(1)
UI = re.findall(r"<script>(.*?)</script>", HTML, re.S)[1]


def directory(**changes):
    fake = FakeFpl()
    for e in fake.bootstrap["elements"]:
        e["code"] = 9000 + e["id"]
    payload = build_players_response(FplDataSource(fetch=fake, clock=Clock()))
    for pid, patch in changes.items():
        next(p for p in payload["players"] if p["id"] == int(pid[1:])).update(patch)
    return payload


def node(script, data):
    if not NODE:
        raise unittest.SkipTest("node is not installed")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "run.js"
        f.write_text(script, encoding="utf-8")
        r = subprocess.run([NODE, str(f)], input=json.dumps(data), capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise AssertionError("node failed:\n" + r.stderr[-2000:])
    return json.loads(r.stdout)


CORE_RUNNER = CORE + """
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = input.calls.map(c => PlannerCore[c.fn](...c.args));
console.log(JSON.stringify(out));
"""


def core(*calls):
    """calls: (fn, args...) -> list of results."""
    return node(CORE_RUNNER, {"calls": [{"fn": c[0], "args": list(c[1:])} for c in calls]})


def idx(payload):
    return core(("indexDirectory", payload))[0]


def state(**kw):
    s = {"strategy": None, "ft": 1, "bank": "1.0", "hits": 0, "moves": [{"out": None, "inn": None}],
         "squad_ids": SQUAD}
    s.update(kw)
    return s


NOW = 1_800_000_000_000


class TestHandover(unittest.TestCase):
    def ho(self, raw):
        return core(("parseHandoff", raw, NOW))[0]

    def good(self, **kw):
        o = {"v": 1, "squad_ids": SQUAD, "gameweek": 7, "created": NOW - 1000}
        o.update(kw)
        return o

    def test_valid_handover_as_string_and_object(self):
        for raw in (json.dumps(self.good()), self.good()):
            r = self.ho(raw)
            self.assertEqual((r["ok"], r["squad_ids"], r["gameweek"]), (True, SQUAD, 7))

    def test_gameweek_may_be_missing(self):
        self.assertIsNone(self.ho(self.good(gameweek=None))["gameweek"])

    def test_rejects_bad_handovers(self):
        bad = [
            ("not json", "unreadable"), (None, "unreadable"), ("[]", "unreadable"), ({"v": 2}, "unreadable"),
            (self.good(v=2), "unreadable"),
            (self.good(squad_ids=SQUAD[:14]), "squad"), (self.good(squad_ids=SQUAD + [16]), "squad"),
            (self.good(squad_ids=[1] * 15), "squad"), (self.good(squad_ids=SQUAD[:14] + ["x"]), "squad"),
            (self.good(squad_ids=SQUAD[:14] + [1.5]), "squad"), (self.good(squad_ids=SQUAD[:14] + [-4]), "squad"),
            (self.good(gameweek=0), "unreadable"), (self.good(gameweek="7"), "unreadable"),
            (self.good(created="now"), "unreadable"), (self.good(created=NOW + 10 ** 7), "unreadable"),
            (self.good(created=NOW - 13 * 3600 * 1000), "stale"),
        ]
        for raw, reason in bad:
            r = self.ho(raw)
            self.assertFalse(r["ok"], raw)
            self.assertEqual(r["reason"], reason, raw)

    def test_twelve_hour_boundary(self):
        self.assertTrue(self.ho(self.good(created=NOW - 12 * 3600 * 1000))["ok"])
        self.assertFalse(self.ho(self.good(created=NOW - 12 * 3600 * 1000 - 1))["ok"])


class TestDirectory(unittest.TestCase):
    def test_real_players_payload_is_indexed(self):
        r = idx(directory())
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["players"]), 95)
        p = r["byId"]["12"]
        self.assertEqual((p["name"], p["pos"], p["price"], p["code"], p["teamShort"]), ("S12", 3, 55, 9012, "C12"))

    def test_malformed_entries_are_dropped_not_fatal(self):
        payload = directory()
        payload["players"] += [None, "x", {"id": "a"}, {"id": 5000, "name": "", "position": 2, "team": 1, "price": 50},
                               {"id": 5001, "name": "Z", "position": 7, "team": 1, "price": 50},
                               {"id": 5002, "name": "Z", "position": 2, "team": 1, "price": 0},
                               {"id": 12, "name": "Dup", "position": 3, "team": 1, "price": 50}]
        r = idx(payload)
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["players"]), 95)
        self.assertEqual(r["byId"]["12"]["name"], "S12")

    def test_missing_photo_and_team_names_become_null(self):
        r = idx(directory(p12={"photo_code": None, "team_short": None, "team_name": None}))
        p = r["byId"]["12"]
        self.assertIsNone(p["code"])
        self.assertIsNone(p["teamShort"])

    def test_unusable_payloads(self):
        for bad in (None, {"status": "ERROR"}, {"status": "OK", "players": "x"}, {"status": "OK", "players": []},
                    {"status": "OK", "players": directory()["players"][:10]}):
            self.assertFalse(idx(bad)["ok"], bad)


class TestSquadValidation(unittest.TestCase):
    def validate(self, ids, payload=None):
        d = idx(payload or directory())
        return core(("validateSquad", ids, d["byId"]))[0]

    def test_fixture_squad_is_legal(self):
        self.assertEqual(self.validate(SQUAD), [])

    def test_unknown_player(self):
        self.assertIn("999", " ".join(self.validate(SQUAD[:14] + [999])))

    def test_wrong_composition(self):
        problems = self.validate(SQUAD[:14] + [121])           # a MID instead of a FWD
        self.assertTrue(any("FWD" in p for p in problems))
        self.assertTrue(any("MID" in p for p in problems))

    def test_more_than_three_from_one_club(self):
        payload = directory(**{"p%d" % i: {"team": 1} for i in (8, 9, 10, 11)})
        self.assertIn("More than 3 players from one club.", self.validate(SQUAD, payload))


class TestBank(unittest.TestCase):
    def parse(self, text):
        return core(("parseBank", text))[0]

    def test_valid(self):
        for text, tenths in (("0", 0), ("0.5", 5), ("1", 10), ("12.3", 123), (" 2.0 ", 20), ("100", 1000), ("0.10", 1)):
            r = self.parse(text)
            self.assertEqual((r["ok"], r["tenths"]), (True, tenths), text)

    def test_invalid(self):
        for text in ("", "abc", "-1", "1,5", "1e2", ".5", "1.", "1.05", "1.25", "101", "1000", None):
            self.assertFalse(self.parse(text)["ok"], text)

    def test_value_round_trips_to_the_api_unit(self):
        self.assertEqual(self.parse("12.3")["value"], 12.3)


class TestRules(unittest.TestCase):
    def opts(self, **kw):
        return core(("hitOptions", state(**kw)))[0]

    def test_cap_is_three_moves_including_hits(self):
        for ft, labels in ((0, ["No", "−4", "−8", "−12"]), (1, ["No", "−4", "−8"]),
                           (2, ["No", "−4"]), (3, ["No"])):
            self.assertEqual([o["label"] for o in self.opts(ft=ft)], labels, ft)

    def test_no_is_disabled_with_zero_free_transfers(self):
        o = self.opts(ft=0)
        self.assertTrue(o[0]["disabled"])
        self.assertFalse(any(x["disabled"] for x in o[1:]))
        self.assertFalse(self.opts(ft=1)[0]["disabled"])

    def test_nothing_is_selected_by_default(self):
        for ft in range(4):
            self.assertFalse(any(o["selected"] for o in self.opts(ft=ft, hits=None)))

    def test_sensible_allows_only_no(self):
        for ft in (1, 2):
            o = self.opts(ft=ft, strategy="sensible", hits=2)
            self.assertEqual([x["selected"] for x in o], [True] + [False] * (len(o) - 1))
            self.assertTrue(all(x["disabled"] for x in o[1:]))

    def test_worth_a_hit_needs_a_hit(self):
        o = self.opts(ft=1, strategy="hit")
        self.assertTrue(o[0]["disabled"])
        self.assertFalse(o[1]["disabled"])

    def test_effective_hits_and_move_limits(self):
        cases = [
            (dict(ft=1), 0, 1), (dict(ft=1, hits=1), 1, 2), (dict(ft=1, hits=2), 2, 3), (dict(ft=2), 0, 2),
            (dict(ft=2, hits=1), 1, 3), (dict(ft=3), 0, 3), (dict(ft=3, hits=2), 0, 3),
            (dict(ft=0), 1, 1), (dict(ft=0, hits=3), 3, 3), (dict(ft=1, strategy="sensible", hits=2), 0, 1),
            (dict(ft=2, strategy="sensible"), 0, 2), (dict(ft=1, strategy="hit"), 1, 2), (dict(ft=1, strategy="hit", hits=2), 2, 3),
            (dict(ft=2, strategy="hit", hits=0), 1, 3),
        ]
        for kw, hits, moves in cases:
            r = core(("effHits", state(**kw)), ("maxMoves", state(**kw)))
            self.assertEqual(r, [hits, moves], kw)

    def test_strategy_availability(self):
        blocked = [dict(strategy="sensible", ft=0), dict(strategy="hit", ft=3)]
        for kw in blocked:
            r = core(("strategyBlock", state(**kw)))[0]
            self.assertFalse(r["available"], kw)
            self.assertTrue(r["reason"])
        for kw in (dict(strategy="sensible", ft=1), dict(strategy="hit", ft=2), dict(strategy="all", ft=0),
                   dict(strategy="mind", ft=3), dict(strategy="all", ft=3)):
            self.assertTrue(core(("strategyBlock", state(**kw)))[0]["available"], kw)


class TestSearch(unittest.TestCase):
    def setUp(self):
        payload = directory()
        payload["players"] += [
            {"id": 9001, "name": "Gómez", "team": 3, "team_name": "Club 3", "team_short": "C3", "position": 3, "price": 60, "photo_code": 1},
            {"id": 9002, "name": "Gomes", "team": 4, "team_name": "Club 4", "team_short": "C4", "position": 3, "price": 70, "photo_code": 2},
            {"id": 9003, "name": "Agomez", "team": 5, "team_name": "Club 5", "team_short": "C5", "position": 2, "price": 50, "photo_code": 3},
        ]
        self.d = idx(payload)

    def s(self, **kw):
        base = {"kind": "buy", "q": "", "squadIds": SQUAD, "byId": self.d["byId"], "players": self.d["players"]}
        base.update(kw)
        return core(("search", base))[0]

    def test_sell_lists_only_the_users_squad(self):
        r = self.s(kind="sell")
        self.assertEqual(len(r), 8)                                            # limited, all from the squad
        self.assertTrue(all(p["id"] in SQUAD for p in r))
        self.assertTrue(all(p["id"] in SQUAD for p in self.s(kind="sell", limit=20)))
        self.assertEqual({p["id"] for p in self.s(kind="sell", q="S1", limit=20)}, {1, 10, 11, 12, 13, 14, 15})

    def test_buy_never_offers_squad_players(self):
        r = self.s(q="S", limit=200)
        self.assertTrue(r == [] or not any(p["id"] in SQUAD for p in r))

    def test_buy_needs_a_query(self):
        self.assertEqual(self.s(q=""), [])
        self.assertEqual(self.s(q="   "), [])

    def test_search_is_by_name_only(self):
        self.assertEqual(self.s(q="C12"), [])                    # team short name
        self.assertEqual(self.s(q="Club"), [])                   # team name
        self.assertEqual(self.s(q="MID"), [])                    # position
        self.assertEqual(self.s(q="9012"), [])                   # photo code

    def test_accent_and_case_insensitive_with_prefix_first(self):
        r = self.s(q="GOM")
        self.assertEqual([p["name"] for p in r], ["Gomes", "Gómez", "Agomez"])      # prefix matches first, then A to Z

    def test_position_filter_and_exclusions(self):
        self.assertEqual([p["name"] for p in self.s(q="gom", pos=3)], ["Gomes", "Gómez"])
        self.assertEqual([p["name"] for p in self.s(q="gom", pos=3, exclude=[9001])], ["Gomes"])
        self.assertEqual(self.s(q="gom", pos=1), [])

    def test_result_cap(self):
        self.assertEqual(len(self.s(q="p", limit=3)), 3)
        self.assertLessEqual(len(self.s(q="p")), 8)

    def test_results_carry_what_the_ui_shows(self):
        p = self.s(q="Gomes")[0]
        for key in ("id", "name", "pos", "price", "teamShort", "code"):
            self.assertIn(key, p)


MIND = {"strategy": "mind"}
MOVE = [{"out": 12, "inn": 126}]


class TestPlanRequest(unittest.TestCase):
    def build(self, **kw):
        return core(("buildPlanRequest", state(**kw)))[0]

    def ok(self, **kw):
        r = self.build(**kw)
        self.assertTrue(r["ok"], (kw, r))
        parsed = parse_request(r["body"])                       # the real API accepts exactly this body
        return r["body"], parsed

    def bad(self, **kw):
        r = self.build(**kw)
        self.assertFalse(r["ok"], (kw, r))
        self.assertTrue(r["problems"])
        return r["problems"]

    def test_label_to_api_mapping(self):
        m = core(("norm", "x"))[0]                                # core loads
        self.assertEqual(m, "x")
        strategies = node(CORE + "\nconsole.log(JSON.stringify(PlannerCore.STRATEGIES));", {})
        self.assertEqual({k: v["api"] for k, v in strategies.items()},
                         {"mind": None, "hit": "everything", "sensible": "sensible", "all": "everything"})
        self.assertEqual(strategies["hit"]["label"], "Worth a hit?")

    def test_sensible_sends_no_hits(self):
        body_, parsed = self.ok(strategy="sensible")
        self.assertEqual(body_["strategy"], "sensible")
        self.assertNotIn("max_hits", body_)
        self.assertEqual(parsed["max_hits"], 0)
        body_, parsed = self.ok(strategy="sensible", hits=2)          # a stale hits choice is ignored
        self.assertNotIn("max_hits", body_)

    def test_everything_is_the_default_equivalent(self):
        body_, parsed = self.ok(strategy="all")
        self.assertEqual((body_["strategy"], parsed["max_hits"]), ("everything", 0))
        self.assertNotIn("max_hits", body_)
        body_, parsed = self.ok(strategy="all", hits=1)
        self.assertEqual((body_["strategy"], body_["max_hits"]), ("everything", 1))

    def test_worth_a_hit_allows_hits_and_keeps_the_net_ranking_strategy(self):
        body_, parsed = self.ok(strategy="hit")
        self.assertEqual((body_["strategy"], body_["max_hits"]), ("everything", 1))
        body_, _ = self.ok(strategy="hit", hits=2)
        self.assertEqual(body_["max_hits"], 2)
        body_, _ = self.ok(strategy="hit", ft=2)
        self.assertEqual(body_["max_hits"], 1)

    def test_blocked_combinations(self):
        self.assertTrue(any("free transfer" in p for p in self.bad(strategy="sensible", ft=0)))
        self.assertTrue(any("no hit to weigh" in p for p in self.bad(strategy="hit", ft=3)))

    def test_player_in_mind_sends_moves_without_a_strategy_value(self):
        body_, parsed = self.ok(strategy="mind", moves=MOVE)
        self.assertEqual(body_["moves"], [[12, 126]])
        self.assertNotIn("strategy", body_)
        self.assertEqual(parsed["intent"], "moves")

    def test_move_count_respects_free_transfers_plus_hits(self):
        two = [{"out": 12, "inn": 126}, {"out": 11, "inn": 127}]
        self.assertTrue(any("More moves" in p for p in self.bad(strategy="mind", moves=two, ft=1)))
        body_, _ = self.ok(strategy="mind", moves=two, ft=1, hits=1)
        self.assertEqual(len(body_["moves"]), 2)
        body_, _ = self.ok(strategy="mind", moves=two, ft=2)
        self.assertEqual(body_["max_hits"] if "max_hits" in body_ else 0, 0)
        three = two + [{"out": 10, "inn": 128}]
        body_, _ = self.ok(strategy="mind", moves=three, ft=3)
        self.assertEqual(len(body_["moves"]), 3)

    def test_incomplete_moves(self):
        self.assertTrue(any("Choose a player" in p for p in self.bad(strategy="mind")))
        self.assertTrue(any("only one player" in p for p in self.bad(strategy="mind", moves=[{"out": 12, "inn": None}])))
        body_, _ = self.ok(strategy="mind", moves=MOVE + [{"out": None, "inn": None}], ft=1, hits=1)   # empty extra row is ignored
        self.assertEqual(body_["moves"], [[12, 126]])

    def test_zero_free_transfers_always_carries_a_hit(self):
        body_, parsed = self.ok(strategy="all", ft=0)
        self.assertEqual(body_["max_hits"], 1)
        body_, parsed = self.ok(strategy="all", ft=0, hits=3)
        self.assertEqual(body_["max_hits"], 3)

    def test_missing_or_bad_inputs(self):
        self.assertIn("Pick a game plan.", self.bad())
        self.assertTrue(self.bad(strategy="all", bank=""))
        self.assertTrue(self.bad(strategy="all", bank="1.05"))
        self.assertTrue(self.bad(strategy="all", ft=4))
        self.assertTrue(self.bad(strategy="all", ft=-1))
        self.assertTrue(self.bad(strategy="nope"))

    def test_bank_is_sent_in_pounds_millions(self):
        body_, parsed = self.ok(strategy="all", bank="12.3")
        self.assertEqual(body_["bank_m"], 12.3)
        self.assertEqual(parsed["bank"], 123)

    def test_every_buildable_request_is_accepted_by_the_api_and_planned(self):
        for kw in (dict(strategy="sensible"), dict(strategy="all", hits=1), dict(strategy="hit"),
                   dict(strategy="mind", moves=MOVE), dict(strategy="all", ft=0)):
            body_, _ = self.ok(**kw)
            status, payload = handle(json.dumps(body_).encode(), FplDataSource(fetch=FakeFpl(), clock=Clock()))
            self.assertEqual(status, 200, (kw, payload))

    def test_the_page_never_builds_a_request_the_api_rejects(self):
        import itertools
        moves_options = [[{"out": None, "inn": None}], MOVE, MOVE + [{"out": 11, "inn": 127}]]
        for strategy, ft, hits, moves in itertools.product((None, "mind", "hit", "sensible", "all"), range(4),
                                                           (None, 0, 1, 2, 3), moves_options):
            r = self.build(strategy=strategy, ft=ft, hits=hits, moves=moves)
            if r["ok"]:
                try:
                    parse_request(r["body"])
                except ApiError as e:
                    self.fail("%s rejected by the API: %s" % ((strategy, ft, hits, moves), e.details))
                self.assertLessEqual(r["body"]["free_transfers"] + r["body"].get("max_hits", 0), 3)

    def test_sample_squad_is_legal(self):
        d = idx(directory())
        ids = core(("sampleSquad", d["players"]))[0]
        self.assertEqual(len(ids), 15)
        self.assertEqual(core(("validateSquad", ids, d["byId"]))[0], [])


SMOKE = """
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const handlers = {}, els = {}, storage = {};
const mk = sel => els[sel] || (els[sel] = {innerHTML: '', textContent: '', disabled: false, classList: {toggle() {}, add() {}, remove() {}},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, parentElement: {classList: {toggle() {}}}});
global.window = {scrollY: 0, scrollTo() {}, open(url) { (input.opened = input.opened || []).push(url); return input.popupBlocked ? null : {}; }};
Object.defineProperty(global, 'navigator', {value: {clipboard: {writeText: async t => { input.copied = t; }}}, configurable: true});
global.location = {search: input.search || '', hostname: input.hostname === undefined ? 'localhost' : input.hostname};
global.sessionStorage = {getItem: k => (k in storage ? storage[k] : null), setItem: (k, v) => { storage[k] = String(v); }, removeItem: k => { delete storage[k]; }};
global.document = {querySelector: s => mk(s), addEventListener: (t, f) => { handlers[t] = f; }};
global.AbortController = class { constructor() { this.signal = {}; } abort() {} };
global.setInterval = () => 1; global.clearInterval = () => {};
global.fetch = async (url, opts) => {
  if (url === '/api/players') { input.fetchLog = (input.fetchLog || 0) + 1;
    return input.fetchFails ? {ok: false, json: async () => null} : {ok: true, json: async () => input.directory}; }
  if (url === '/api/transfer-plan') {
    (input.posts = input.posts || []).push({method: opts.method, body: opts.body, type: (opts.headers || {})['Content-Type']});
    if (input.planNetworkFail) throw new Error('offline');
    const sent = JSON.parse(opts.body);
    const rule = (input.planRules || []).find(x => (x.has || []).every(k => k in sent) && Object.keys(x.eq || {}).every(k => JSON.stringify(sent[k]) === JSON.stringify(x.eq[k])));
    const r = rule ? rule.response : input.planResponse;
    const dly = (rule && rule.delay) || input.planDelayMs || 0; if (dly) await new Promise(res => setTimeout(res, dly));
    return {ok: r.status < 300, status: r.status, json: async () => { if (r.notJson) throw new Error('x'); return r.json; }};
  }
  throw new Error('unexpected fetch ' + url);
};
if (input.handoff) storage['squadcheck.planner.handoff.v1'] = JSON.stringify(input.handoff);
if (input.unlocked) storage['squadcheck.planner.unlocked.v1'] = '1';
""" + CORE + UI + """
const tick = () => new Promise(r => setTimeout(r, 15));
const html = () => els['#main'].innerHTML;
const click = (act, v, id) => handlers.click({target: {closest: () => ({dataset: {act, v: String(v), id}, tagName: 'BUTTON'})}});
const out = {};
(async () => {
  await tick();
  for (const step of input.steps) {
    if (step.planResponse) input.planResponse = step.planResponse;
    if (step.planRules) input.planRules = step.planRules;
    if (step.popupBlocked !== undefined) input.popupBlocked = step.popupBlocked;
    if (step.act) { click(step.act, step.v === undefined ? '' : step.v, step.id); }
    if (step.why !== undefined) handlers.input({target: {id: 'why', value: step.why, matches: () => false}});
    if (step.key) handlers.keydown({key: step.key, target: {matches: () => false}});
    for (const a of (step.also || [])) click(a.act, a.v === undefined ? '' : a.v, a.id);
    if (step.bank !== undefined) handlers.input({target: {id: 'bank', value: step.bank, matches: () => false, parentElement: {classList: {toggle() {}}}}});
    await new Promise(r => setTimeout(r, step.wait || 15));
    out[step.name] = {html: html(), cta: (html().match(/data-act="next"( disabled)?/) || [])[1] ? 'disabled' : (html().includes('data-act="next"') ? 'enabled' : 'none'),
                      help: ((html().match(/<p class="help" style="margin-top:0">(.*?)<\\/p>/) || [])[1]) || '',
                      state: storage['squadcheck.planner.state.v1'] || null, posts: (input.posts || []).length,
                      stepper: els['#stepper'].innerHTML, ctaLive: (els['[data-act="next"]'] || {}).disabled, helpLive: (els['.stack .help'] || {}).textContent,
                      opened: (input.opened || []).slice(), copied: input.copied || null, unlocked: storage['squadcheck.planner.unlocked.v1'] || null,
                      storageAll: JSON.stringify(storage)};
  }
  out.initial = {html: html(), fetches: input.fetchLog || 0, stepper: els['#stepper'].innerHTML, chips: els['#chips'].innerHTML,
                 posts: input.posts || [], opened: input.opened || [], copied: input.copied || null, handoffStored: 'squadcheck.planner.handoff.v1' in storage};
  console.log(JSON.stringify(out));
})();
"""


def smoke(steps=(), **kw):
    data = {"directory": directory(), "handoff": {"v": 1, "squad_ids": SQUAD, "gameweek": 7, "created": 0}, "steps": list(steps)}
    # the stub clock matters: handovers are accepted for 12 hours, so stamp it "now"
    import time
    data["handoff"]["created"] = int(time.time() * 1000)
    data.update(kw)
    steps = data["steps"]
    acts = [x.get("act") for x in steps]
    if "next" in acts and "hits" not in acts[:acts.index("next")] and not kw.get("raw_steps"):
        # The points-hit choice is required, so a journey that reaches Explore must have made it. "No" stands in for the old silent default.
        steps.insert(acts.index("next"), {"name": "_hits", "act": "hits", "v": "0"})
    if data.get("handoff") and data["handoff"].get("created") == 0:
        data["handoff"]["created"] = int(time.time() * 1000)
    return node(SMOKE, data)


class TestPageFlow(unittest.TestCase):
    def test_no_handover_shows_the_start_screen_and_makes_no_requests(self):
        r = smoke(handoff=None)
        self.assertIn("Start from your team check", r["initial"]["html"])
        self.assertIn('href="./"', r["initial"]["html"])
        self.assertNotIn("sample squad", r["initial"]["html"])
        self.assertEqual(r["initial"]["fetches"], 0)

    def test_dev_flag_offers_a_sample_squad_and_it_boots_the_planner(self):
        r = smoke(handoff=None, search="?dev=1", steps=[{"name": "sample", "act": "sample"}])
        self.assertIn("What’s your game plan?", r["sample"]["html"])

    def test_stale_or_broken_handover_shows_the_start_screen(self):
        for ho in ({"v": 1, "squad_ids": SQUAD[:14], "gameweek": 7, "created": 1}, {"v": 9}):
            self.assertIn("Start from your team check", smoke(handoff=ho)["initial"]["html"])

    def test_game_plan_screen_defaults(self):
        r = smoke()["initial"]
        h = r["html"]
        self.assertEqual(r["fetches"], 1)
        self.assertIn("What’s your game plan?", h)
        self.assertIn("GW7", r["chips"])
        order = [h.index(t) for t in ("I have a player in mind", "Worth a hit?", "Keep it sensible", "Show me everything")]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("strat on", h)                        # nothing selected by default
        self.assertNotIn("Chase upside", h)
        self.assertIn("Free transfers available", h)
        self.assertIn("To continue: pick a game plan, enter your money in the bank, choose whether to allow a points hit.", h)
        self.assertIn("disabled", h[h.index('data-act="next"'):h.index('data-act="next"') + 40])
        for banned in ("upside", "ceiling"):
            self.assertNotIn(banned, h.lower())

    def test_squad_strip_lists_the_fifteen_and_escapes_names(self):
        payload = directory(p3={"name": "<img src=x onerror=alert(1)>"})
        r = smoke(directory=payload)["initial"]["html"]
        self.assertEqual(r.count('class="p"'), 15)
        self.assertNotIn("<img src=x", r)
        self.assertIn("&lt;img src=x", r)

    def test_stepper_marks_only_the_first_step_live(self):
        s = smoke()["initial"]["stepper"]
        self.assertEqual(s.count("stp on"), 1)
        self.assertEqual(s.count('aria-disabled="true"'), 4)

    def test_continue_button_follows_the_constraints(self):
        r = smoke(steps=[
            {"name": "bank", "bank": "0.5"},
            {"name": "sensible", "act": "strat", "v": "sensible"},
            {"name": "ft0", "act": "ft", "v": -1},
            {"name": "hit_ft3", "act": "strat", "v": "hit"},
        ])
        self.assertEqual(r["sensible"]["cta"], "enabled")
        self.assertEqual(r["ft0"]["cta"], "disabled")
        self.assertIn("none", r["ft0"]["help"])
        self.assertIn("strat on", r["ft0"]["html"])

    def test_hit_blocked_at_three_free_transfers(self):
        r = smoke(steps=[{"name": "bank", "bank": "0.5"}, {"name": "s", "act": "strat", "v": "hit"}, {"name": "h", "act": "hits", "v": "1"}, {"name": "up", "act": "ft", "v": 1},
                         {"name": "up2", "act": "ft", "v": 1}])
        self.assertEqual(r["h"]["cta"], "enabled")
        self.assertEqual(r["up2"]["cta"], "disabled")
        self.assertIn("no hit to weigh", r["up2"]["html"])

    def test_player_in_mind_expands_in_place_and_builds_the_request(self):
        r = smoke(planResponse={"status": 200, "json": real_plan(free_transfers=1, moves=[[12, 126]])[1]}, steps=[
            {"name": "bank", "bank": "0.5"},
            {"name": "hits", "act": "hits", "v": "0"},
            {"name": "mind", "act": "strat", "v": "mind"},
            {"name": "sell", "act": "cpick", "v": 12, "id": "sell0"},
            {"name": "buy", "act": "cpick", "v": 126, "id": "buy0"},
            {"name": "next", "act": "next"},
            {"name": "back", "act": "edit"},
        ])
        h = r["mind"]["html"]
        self.assertIn("Move 1", h)
        self.assertLess(h.index("I have a player in mind"), h.index("Move 1"))       # expands inside its own card
        self.assertLess(h.index("Move 1"), h.index("Worth a hit?"))
        self.assertEqual(r["mind"]["cta"], "disabled")
        self.assertEqual(r["buy"]["cta"], "enabled")
        self.assertEqual(r["next"]["posts"], 1)
        self.assertIn("Your options for GW7", r["next"]["html"])
        self.assertIn("What’s your game plan?", r["back"]["html"])
        saved = json.loads(r["back"]["state"])
        self.assertEqual((saved["strategy"], saved["moves"]), ("mind", [{"out": 12, "inn": 126}]))

    def test_changing_the_sell_player_clears_a_buy_of_another_position(self):
        r = smoke(steps=[
            {"name": "m", "act": "strat", "v": "mind"},
            {"name": "s", "act": "cpick", "v": 12, "id": "sell0"},
            {"name": "b", "act": "cpick", "v": 126, "id": "buy0"},
            {"name": "c", "act": "cclear", "id": "sell0"},
            {"name": "s2", "act": "cpick", "v": 3, "id": "sell0"},     # a DEF now
        ])
        state_ = json.loads(r["s2"]["state"])
        self.assertEqual(state_["moves"], [{"out": 3, "inn": None}])

    def test_invalid_bank_blocks_continue_without_losing_focus_state(self):
        r = smoke(steps=[{"name": "s", "act": "strat", "v": "all"}, {"name": "bad", "bank": "1.05"}, {"name": "good", "bank": "2.5"}])
        self.assertEqual(r["s"]["cta"], "disabled")                  # the bank is required
        self.assertEqual(json.loads(r["bad"]["state"])["bank"], "1.05")
        self.assertEqual(json.loads(r["good"]["state"])["bank"], "2.5")

    def test_squad_with_an_unknown_player_shows_a_clear_error(self):
        ho = {"v": 1, "squad_ids": SQUAD[:14] + [999], "gameweek": 7, "created": 0}
        r = smoke(handoff=ho)["initial"]["html"]
        self.assertIn("couldn’t read your squad", r)
        self.assertIn("Try again", r)

    def test_directory_failure_shows_a_retryable_error(self):
        r = smoke(fetchFails=True)["initial"]["html"]
        self.assertIn("player list isn’t available", r)
        self.assertIn("Try again", r)


def real_plan(**kw):
    """A real /api/transfer-plan response built by the adapter from the mocked FPL fixtures."""
    fake = FakeFpl()
    for e in fake.bootstrap["elements"]:
        e["code"] = 9000 + e["id"]
    status, payload = handle(body(**kw), FplDataSource(fetch=fake, clock=Clock()))
    return status, json.loads(json.dumps(payload))


def cards(payload):
    return core(("buildCards", payload))[0]


def plan_cards(**kw):
    status, payload = real_plan(**kw)
    assert status == 200, payload
    return payload, cards(payload)


class TestExploreCards(unittest.TestCase):
    def ranked(self, v):
        return [c for c in v["cards"] if c["kind"] == "rank"]

    def test_cards_follow_the_planners_own_order_and_end_with_hold(self):
        payload, v = plan_cards(free_transfers=1)
        alts = payload["plan"]["alternatives"]
        self.assertTrue(alts)
        r = self.ranked(v)
        self.assertEqual(len(r), min(3, len(alts)))
        for c, a in zip(r, alts):
            self.assertEqual(c["model"]["net"], a["projected_gain_vs_hold"])
        nets = [c["model"]["net"] for c in r]
        self.assertEqual(nets, sorted(nets, reverse=True))
        self.assertEqual([c["rank"] for c in r], list(range(1, len(r) + 1)))
        self.assertEqual(v["cards"][-1]["kind"], "hold")
        self.assertEqual(v["totalRanked"], len(alts))

    def test_every_figure_is_a_response_field_and_the_three_measures_stay_apart(self):
        payload, v = plan_cards(free_transfers=0, max_hits=2, strategy="everything")
        alts = payload["plan"]["alternatives"]
        hit = [(c, a) for c, a in zip(self.ranked(v), alts) if a["hit_points"] > 0]
        self.assertTrue(hit)
        for c, a in hit:
            m = c["model"]
            self.assertEqual(m["before"], a["projected_gain_before_hits"])
            self.assertEqual(m["net"], a["projected_gain_vs_hold"])
            self.assertEqual(m["hitPts"], a["hit_points"])
            self.assertEqual(m["outlook"], a["outlook"]["vs_hold"])
            self.assertEqual(m["bankAfter"], a["bank_after"])
            self.assertAlmostEqual(m["before"] - m["hitPts"], m["net"], delta=0.011)
            self.assertNotEqual(m["before"], m["net"])
            self.assertEqual(len(m["outlook"]), 3)
            self.assertEqual(m["costLabel"], "−%d hit" % a["hit_points"])

    def test_models_expose_no_ceiling_or_upside_field(self):
        payload, v = plan_cards(free_transfers=2, max_hits=1)
        text = json.dumps(v).lower()
        for word in ("ceiling", "upside"):
            self.assertNotIn(word, text)
        keys = set(self.ranked(v)[0]["model"])
        self.assertEqual(keys, {"title", "n", "hits", "hitPts", "before", "net", "outlook", "outlookStatus", "moves", "why", "watch",
                                "risks", "costLabel", "bankAfter", "confidence", "freeLeft", "belowBar", "dataComplete", "sig", "ftUsed", "xiIds"})

    def test_default_gain_is_labelled_as_next_gameweek_only(self):
        payload, v = plan_cards(free_transfers=1)
        self.assertEqual(payload["plan"]["inputs"]["weights"], [1.0, 0.0, 0.0])
        self.assertEqual(v["basis"], {"key": "next", "label": "the next gameweek (GW7)"})
        for c in self.ranked(v):
            self.assertIn("the next gameweek (GW7)", c["model"]["why"])
            self.assertNotIn("three", c["model"]["why"].lower())

    def test_basis_follows_the_weights_the_planner_reports(self):
        payload, _ = plan_cards(free_transfers=1)
        for weights, key in (([1, 0, 0], "next"), ([0.5, 0.5, 0], "weighted"), ([0, 1, 0], "weighted"), ([1 / 3, 1 / 3, 1 / 3], "weighted")):
            payload["plan"]["inputs"]["weights"] = weights
            self.assertEqual(cards(payload)["basis"]["key"], key, weights)
        del payload["plan"]["inputs"]["weights"]
        self.assertEqual(cards(payload)["basis"]["key"], "next")                     # the planner's default

    def test_recommendation_is_marked_on_the_matching_card_only(self):
        for kw in (dict(free_transfers=2), dict(free_transfers=1, max_hits=1), dict(free_transfers=0)):
            payload, v = plan_cards(**kw)
            marked = [c["id"] for c in v["cards"] if c["recommended"]]
            action = payload["plan"]["recommendation"]["action"]
            self.assertEqual(len(marked), 1, kw)
            if action == "HOLD":
                self.assertEqual(marked, ["hold"])
            else:
                rec = payload["plan"]["recommendation"]["plan"]["transfers"]
                got = next(c for c in v["cards"] if c["recommended"])
                self.assertEqual(sorted((m["out"]["id"], m["inn"]["id"]) for m in got["model"]["moves"]),
                                 sorted((t["out"]["id"], t["in"]["id"]) for t in rec))
            self.assertEqual(v["verdict"]["action"], action)
            self.assertEqual(v["verdict"]["reason"], payload["plan"]["recommendation"]["reason"])

    def test_hold_only_when_nothing_can_be_done(self):
        payload, v = plan_cards(free_transfers=0, strategy="sensible")
        self.assertEqual([c["kind"] for c in v["cards"]], ["hold"])
        self.assertEqual(v["totalRanked"], 0)
        self.assertTrue(v["cards"][0]["recommended"])

    def test_sensible_cards_never_carry_a_hit(self):
        payload, v = plan_cards(free_transfers=2, strategy="sensible")
        for c in v["cards"]:
            self.assertEqual(c["model"]["hits"], 0)
            self.assertEqual(c["model"]["before"], c["model"]["net"]) if c["kind"] == "rank" else None

    def test_your_move_comes_first_and_is_not_duplicated(self):
        payload, v = plan_cards(free_transfers=1, moves=[[12, 126]])
        self.assertEqual(v["cards"][0]["kind"], "mine")
        self.assertEqual(v["cards"][0]["status"], payload["plan"]["requested"]["status"])
        self.assertEqual(v["cards"][0]["reason"], payload["plan"]["requested"]["reason"])
        sigs = [c["model"]["sig"] for c in v["cards"] if c["model"]]
        self.assertEqual(len(sigs), len(set(sigs)))

    def test_a_move_that_cannot_be_done_shows_the_planners_reason_and_no_numbers(self):
        payload, v = plan_cards(free_transfers=1, moves=[[12, 126], [11, 127]])
        mine = v["cards"][0]
        self.assertEqual(mine["status"], "INFEASIBLE")
        self.assertIsNone(mine["model"])
        self.assertTrue(mine["reason"])
        self.assertEqual(v["cards"][-1]["kind"], "hold")

    def test_extra_transfer_variant_is_kept_separate_and_labelled(self):
        _, payload = real_plan(free_transfers=2, out_ids=[12])
        req = payload["plan"]["requested"]
        if not req.get("with_additional_transfers"):
            self.skipTest("fixture produced no extra-transfers variant")
        v = cards(payload)
        extra = next(c for c in v["cards"] if c["kind"] == "extra")
        self.assertEqual(extra["label"], req["with_additional_transfers"]["label"])
        self.assertLess([c["kind"] for c in v["cards"]].index("mine"), [c["kind"] for c in v["cards"]].index("extra"))

    def test_below_the_bar_flag_uses_the_planners_threshold(self):
        payload, _ = plan_cards(free_transfers=1)
        payload["plan"]["inputs"]["min_gain"] = 1e6
        v = cards(payload)
        self.assertTrue(all(c["model"]["belowBar"] for c in v["cards"] if c["kind"] == "rank"))
        self.assertFalse(v["cards"][-1]["model"]["belowBar"])                       # Hold is never "below the bar"
        payload["plan"]["inputs"]["min_gain"] = -1e6
        self.assertFalse(any(c["model"]["belowBar"] for c in cards(payload)["cards"] if c["model"]))

    def test_degraded_and_data_quality_are_passed_through(self):
        payload, _ = plan_cards(free_transfers=1)
        payload["status"] = "DEGRADED"
        payload["data"]["degraded_reasons"] = ["3 player projection(s) unavailable"]
        payload["plan"]["data_quality"] = {"complete": False, "note": "Players without a projection are never bought or sold."}
        v = cards(payload)
        self.assertEqual(v["degraded"], {"status": "DEGRADED", "reasons": ["3 player projection(s) unavailable"]})
        self.assertFalse(v["dataQuality"]["complete"])

    def test_missing_optional_fields_do_not_break_the_cards(self):
        payload, _ = plan_cards(free_transfers=2, max_hits=1)
        for a in payload["plan"]["alternatives"]:
            a.pop("outlook", None); a["confidence"] = None; a.pop("risks", None); a.pop("bank_after", None)
            for t in a["transfers"]:
                for side in ("out", "in"):
                    for k in ("photo_code", "team_short", "xpts", "price"):
                        t[side].pop(k, None)
        v = cards(payload)
        m = self.ranked(v)[0]["model"]
        self.assertIsNone(m["outlook"])
        self.assertIsNone(m["confidence"])
        self.assertIsNone(m["bankAfter"])
        self.assertIsNone(m["moves"][0]["out"]["code"])
        self.assertTrue(m["watch"])

    def test_order_follows_net_gain_even_when_pre_hit_gain_would_order_differently(self):
        payload, _ = plan_cards(free_transfers=1, max_hits=2)
        alts = payload["plan"]["alternatives"]
        self.assertGreaterEqual(len(alts), 3)
        for i, a in enumerate(alts):                                   # make the pre-hit gain rank in the opposite order
            a["projected_gain_before_hits"] = float(i)
        v = cards(payload)
        self.assertEqual([c["model"]["net"] for c in self.ranked(v)], [a["projected_gain_vs_hold"] for a in alts[:3]])
        self.assertEqual([c["model"]["before"] for c in self.ranked(v)], [0.0, 1.0, 2.0])

    def test_watch_out_uses_the_planners_own_risks(self):
        payload, _ = plan_cards(free_transfers=1)
        a = payload["plan"]["alternatives"][0]
        a["risks"] = ["Foo: availability flag 'd'", "Affordability is estimated from current prices, not selling prices", "Bar: LOW projection confidence", "Baz: third risk"]
        m = self.ranked(cards(payload))[0]["model"]
        self.assertEqual(m["watch"], "Foo: availability flag 'd' Bar: LOW projection confidence")
        self.assertEqual(m["risks"], ["Foo: availability flag 'd'", "Bar: LOW projection confidence", "Baz: third risk"])
        a["risks"] = ["Affordability is estimated from current prices, not selling prices"]
        m = self.ranked(cards(payload))[0]["model"]
        self.assertIn("flagged no specific risks", m["watch"])

    def test_explanations_are_built_from_the_numbers(self):
        payload, v = plan_cards(free_transfers=1)
        c = self.ranked(v)[0]["model"]
        self.assertIn("more than holding", c["why"])
        self.assertIn("%.1f points" % abs(c["before"]), c["why"].replace("1.0 point ", "1.0 points "))
        self.assertEqual(c["title"], "Swap %s for %s" % (c["moves"][0]["out"]["name"], c["moves"][0]["inn"]["name"]))
        h = v["cards"][-1]["model"]
        self.assertEqual((h["title"], h["costLabel"], h["net"]), ("Hold", "Hold", 0.0))


class TestPlanResponseHandling(unittest.TestCase):
    def parse(self, status, js):
        return core(("parsePlanResponse", status, js))[0]

    def test_success_and_degraded_are_both_accepted(self):
        _, payload = real_plan(free_transfers=1)
        self.assertTrue(self.parse(200, payload)["ok"])
        payload["status"] = "DEGRADED"
        self.assertTrue(self.parse(200, payload)["ok"])

    def test_unreadable_success_bodies_are_rejected(self):
        _, payload = real_plan(free_transfers=1)
        for bad in (None, {}, {"status": "ERROR"}, {**payload, "plan": None}, {**payload, "plan": {**payload["plan"], "alternatives": "x"}},
                    {**payload, "status": "WEIRD"}):
            r = self.parse(200, bad)
            self.assertEqual((r["ok"], r["kind"]), (False, "shape"))

    def test_request_errors_show_the_apis_own_safe_message(self):
        r = self.parse(400, {"status": "BAD_REQUEST", "error": "Invalid request", "details": ["a", "b", "c", "d"]})
        self.assertEqual((r["ok"], r["kind"]), (False, "request"))
        self.assertEqual(r["message"], "Invalid request a b c")
        self.assertIn("planner", self.parse(422, None)["message"])
        for s in (409, 413, 422):
            self.assertEqual(self.parse(s, {"error": "msg", "details": []})["kind"], "request")

    def test_server_errors_never_show_raw_detail(self):
        for s in (500, 502, 503, 504):
            r = self.parse(s, {"status": "ERROR", "error": "secret internal detail", "details": ["trace"]})
            self.assertEqual(r["kind"], "upstream")
            self.assertNotIn("secret", r["message"])
            self.assertNotIn("trace", r["message"])


class TestExplorePage(unittest.TestCase):
    def setUp(self):
        self.status, self.payload = real_plan(free_transfers=1)
        self.ok = {"status": 200, "json": self.payload}

    def go(self, *extra, strategy="all", bank="0.5", **kw):
        steps = [{"name": "bank", "bank": bank}, {"name": "strat", "act": "strat", "v": strategy}, {"name": "next", "act": "next"}] + list(extra)
        return smoke(planResponse=kw.pop("planResponse", self.ok), steps=steps, **kw)

    def test_bank_is_blank_and_required_by_default(self):
        r = smoke(steps=[{"name": "s", "act": "strat", "v": "all"}, {"name": "hit", "act": "hits", "v": "0"}, {"name": "typed", "bank": "0.5"}, {"name": "cleared", "bank": ""}])
        h = r["initial"]["html"]
        self.assertIn('id="bank" type="text" inputmode="decimal" value=""', h)
        self.assertIn("Required. Example: 0.5. Check your FPL Transfers page", h)
        self.assertIn('placeholder=""', h)                                    # no look-alike default value in the box
        self.assertEqual(r["s"]["cta"], "disabled")
        self.assertIn("enter your money in the bank", r["s"]["help"])
        self.assertIs(r["typed"]["ctaLive"], False)                  # typing updates the button without re-rendering the field
        self.assertIs(r["cleared"]["ctaLive"], True)

    def test_bank_and_points_hit_are_marked_required_and_the_button_says_why_it_is_inactive(self):
        r = smoke(steps=[{"name": "s", "act": "strat", "v": "all"}, {"name": "bank", "bank": "0.5"}, {"name": "hit", "act": "hits", "v": "0"}])
        h = r["s"]["html"]
        self.assertEqual(h.count('<span class="req">Required</span>'), 2)             # both fields say so
        self.assertRegex(h, r'class="cr need" id="row-bank"')                         # and both are highlighted while missing
        self.assertRegex(h, r'class="cr need" id="row-hits"')
        self.assertNotIn("game plan", r["s"]["help"].lower())                           # a plan is chosen, so it is not named
        self.assertEqual(r["s"]["help"], "To continue: enter your money in the bank, choose whether to allow a points hit.")
        self.assertIn('Choose one. "No" means no points hit.', h)
        self.assertEqual(r["s"]["cta"], "disabled")
        self.assertIn("enter your money in the bank, choose whether to allow a points hit", r["s"]["help"])
        self.assertEqual(r["bank"]["helpLive"], "To continue: choose whether to allow a points hit.")   # bank typed: only the hit is still named
        self.assertIs(r["bank"]["ctaLive"], True)
        self.assertEqual(r["hit"]["cta"], "enabled")                                   # both answered: the button is live, with no reason shown
        self.assertEqual(r["hit"]["help"], "")
        self.assertNotRegex(r["hit"]["html"], r'class="cr need" id="row-(bank|hits)"')

    def test_keep_it_sensible_needs_no_hit_choice_but_everything_else_does(self):
        base = {"ft": 1, "bank": "0.5", "hits": None}
        self.assertTrue(core(("buildPlanRequest", state(strategy="sensible", **base)))[0]["ok"])
        for strategy in ("all", "hit", "mind"):
            r = core(("buildPlanRequest", state(strategy=strategy, **base)))[0]
            self.assertFalse(r["ok"], strategy)
            self.assertIn("Choose whether to allow a points hit.", r["problems"], strategy)
        for h in (0, 1):
            self.assertNotIn("Choose whether to allow a points hit.", core(("buildPlanRequest", state(strategy="all", ft=1, bank="0.5", hits=h)))[0].get("problems", []))

    def test_no_amount_is_prefilled_or_suggested_inside_the_bank_box(self):
        h = smoke()["initial"]["html"]
        self.assertIn('id="bank" type="text" inputmode="decimal" value="" placeholder=""', h)

    def test_explore_is_blocked_until_the_bank_is_valid(self):
        for bank in ("", "abc", "1.05", "-2", "101"):
            r = smoke(planResponse=self.ok, steps=[{"name": "s", "act": "strat", "v": "all"}, {"name": "b", "bank": bank}, {"name": "n", "act": "next"}])
            self.assertEqual(r["n"]["posts"], 0, bank)
            self.assertIn("What’s your game plan?", r["n"]["html"])

    def test_free_transfers_default_is_flagged_until_the_user_touches_it(self):
        r = smoke(steps=[{"name": "up", "act": "ft", "v": 1}, {"name": "down", "act": "ft", "v": -1}])
        first = smoke()["initial"]["html"]
        self.assertIn("We’ve assumed 1. Check your FPL Transfers page.", first)
        self.assertIn('class="h warnc"', first)
        self.assertNotIn("assumed", r["up"]["html"])
        self.assertNotIn("assumed", r["down"]["html"])
        self.assertTrue(json.loads(r["up"]["state"])["ftTouched"])

    def test_dev_shortcut_only_works_on_a_local_host(self):
        dev = {"v": 1, "squad_ids": SQUAD, "gameweek": 7, "created": 0, "dev": True}
        for host in ("squadcheck.club", "www.squadcheck.club", "squadcheck-abc.vercel.app", "localhost.evil.com", ""):
            empty = smoke(handoff=None, search="?dev=1", hostname=host)["initial"]
            self.assertNotIn("sample squad", empty["html"].lower(), host)               # shortcut is not offered
            self.assertIn("Start from your team check", empty["html"], host)
            leaked = smoke(handoff=dev, search="?dev=1", hostname=host)["initial"]
            self.assertNotIn("SAMPLE", leaked["html"] + leaked["chips"], host)          # a stored sample squad is refused too
            self.assertFalse(leaked["handoffStored"], host)
        for host in ("localhost", "127.0.0.1", "[::1]"):
            self.assertIn("sample squad", smoke(handoff=None, search="?dev=1", hostname=host)["initial"]["html"].lower(), host)

    def test_the_planner_stays_noindex_at_launch(self):
        self.assertIn('<meta name="robots" content="noindex,nofollow">', HTML)

    def test_sample_squad_is_visibly_identified_and_never_enters_the_normal_journey(self):
        dev = {"v": 1, "squad_ids": SQUAD, "gameweek": 7, "created": 0, "dev": True}
        shown = smoke(handoff=dev, search="?dev=1")["initial"]
        self.assertIn("SAMPLE SQUAD", shown["html"])
        self.assertIn("not your players", shown["html"])
        self.assertIn("SAMPLE", shown["chips"])
        normal = smoke(handoff={"v": 1, "squad_ids": SQUAD, "gameweek": 7, "created": 0})["initial"]
        self.assertNotIn("SAMPLE", normal["html"] + normal["chips"])
        leaked = smoke(handoff=dev)["initial"]                              # same tab, no ?dev=1
        self.assertIn("Start from your team check", leaked["html"])
        self.assertNotIn("What’s your game plan?", leaked["html"])
        self.assertFalse(leaked["handoffStored"])                            # the leftover sample handover is cleared
        self.assertNotIn("sample squad", leaked["html"])                     # and the shortcut is not offered

    def test_explore_posts_the_exact_request_and_renders_the_real_cards(self):
        r = self.go()
        post = r["next"]
        self.assertEqual(post["posts"], 1)
        posts = smoke(planResponse=self.ok, steps=[{"name": "b", "bank": "0.5"}, {"name": "s", "act": "strat", "v": "all"}, {"name": "n", "act": "next"}])["initial"]["posts"]
        self.assertEqual(posts[0]["method"], "POST")
        self.assertEqual(posts[0]["type"], "application/json")
        self.assertEqual(json.loads(posts[0]["body"]), {"squad_ids": SQUAD, "bank_m": 0.5, "free_transfers": 1, "strategy": "everything"})
        h = post["html"]
        self.assertIn("Your options for GW7", h)
        self.assertIn("The planner’s call", h)
        self.assertIn("Net gain vs hold", h)
        self.assertIn("Outlook vs hold, before hits:", h)
        self.assertIn("GW7 ", h)
        self.assertIn("projections for the next gameweek (GW7)", h)
        self.assertEqual(h.count('aria-pressed="true"'), 1)
        self.assertEqual(h.count('data-act="pickplan"'), len(cards(self.payload)["cards"]))
        for banned in ("ceiling", "upside"):
            self.assertNotIn(banned, h.lower())

    def test_sensible_and_hit_requests_carry_their_api_values(self):
        for strategy, expect in (("sensible", {"strategy": "sensible"}), ("hit", {"strategy": "everything", "max_hits": 1})):
            r = smoke(planResponse=self.ok, steps=[{"name": "b", "bank": "0.5"}, {"name": "s", "act": "strat", "v": strategy}, {"name": "n", "act": "next"}])
            sent = json.loads(r["initial"]["posts"][0]["body"])
            for k, v in expect.items():
                self.assertEqual(sent[k], v, strategy)
            self.assertNotIn("keep_ids", sent)

    def test_selecting_inspecting_and_going_back(self):
        r = self.go({"name": "pick", "act": "pickplan", "v": "hold"}, {"name": "inspect", "act": "inspect"},
                    {"name": "back", "act": "toexplore"}, {"name": "edit", "act": "edit"})
        self.assertEqual(r["pick"]["html"].count("sel-on"), 1)
        self.assertIn('data-v="hold"', r["pick"]["html"])
        self.assertIn("What goes into this suggestion", r["inspect"]["html"])           # Hold is never gated
        self.assertEqual(r["inspect"]["opened"], [])
        self.assertIn("Your options for GW7", r["back"]["html"])
        self.assertIn("What’s your game plan?", r["edit"]["html"])

    def test_repeat_with_the_same_settings_reuses_the_answer_but_changes_refetch(self):
        r = self.go({"name": "edit", "act": "edit"}, {"name": "again", "act": "next"},
                    {"name": "edit2", "act": "edit"}, {"name": "ft", "act": "ft", "v": 1}, {"name": "changed", "act": "next"})
        self.assertEqual(r["again"]["posts"], 1)
        self.assertIn("Your options for GW7", r["again"]["html"])
        self.assertEqual(r["changed"]["posts"], 2)

    def test_names_from_the_response_are_escaped(self):
        payload = json.loads(json.dumps(self.payload))
        for a in payload["plan"]["alternatives"]:
            for t in a["transfers"]:
                t["in"]["web_name"] = "<img src=x onerror=alert(1)>"
        r = self.go(planResponse={"status": 200, "json": payload})
        self.assertNotIn("<img src=x", r["next"]["html"])
        self.assertIn("&lt;img src=x", r["next"]["html"])

    def test_missing_outlook_shows_the_fallback_and_no_substitute_numbers(self):
        payload = json.loads(json.dumps(self.payload))
        for a in payload["plan"]["alternatives"]:
            a.pop("outlook", None)
        h = self.go(planResponse={"status": 200, "json": payload})["next"]["html"]
        self.assertIn("Three-gameweek outlook unavailable.", h)
        self.assertNotIn("Outlook vs hold", h)
        self.assertNotIn("Provisional", h)
        payload2 = json.loads(json.dumps(self.payload))
        for a in payload2["plan"]["alternatives"]:
            a["outlook"]["vs_hold"] = [0.1, 0.2]                              # malformed: must not be completed or inferred
        h2 = self.go(planResponse={"status": 200, "json": payload2})["next"]["html"]
        self.assertIn("Three-gameweek outlook unavailable.", h2)
        self.assertNotIn("Outlook vs hold", h2)

    def test_the_outlook_is_shown_when_the_planner_provides_it(self):
        h = self.go()["next"]["html"]
        self.assertIn("Outlook vs hold, before hits:", h)
        self.assertNotIn("outlook unavailable", h)

    def test_loading_copy_is_a_cautious_upper_bound_not_a_measured_claim(self):
        r = smoke(planResponse=self.ok, planDelayMs=200, steps=[{"name": "b", "bank": "0.5"}, {"name": "s", "act": "strat", "v": "all"},
                                                               {"name": "n", "act": "next", "wait": 30}])
        h = r["n"]["html"]
        self.assertIn("Checking your options", h)
        self.assertIn("up to a minute", h)
        for claim in ("usually", "typically", "seconds", "quick", "fast", "average", "normally"):
            self.assertNotIn(claim, h.lower())

    def test_degraded_banner_is_shown(self):
        payload = json.loads(json.dumps(self.payload))
        payload["status"] = "DEGRADED"
        payload["data"]["degraded_reasons"] = ["2 player projection(s) unavailable"]
        h = self.go(planResponse={"status": 200, "json": payload})["next"]["html"]
        self.assertIn("Some data was unavailable", h)
        self.assertIn("2 player projection(s) unavailable", h)

    def test_failures_are_clear_and_retryable_and_never_leak_detail(self):
        leak = {"status": "ERROR", "error": "secret internal detail", "details": ["trace"]}
        r = self.go({"name": "retry", "act": "retryplans", "planResponse": self.ok}, planResponse={"status": 502, "json": leak})
        self.assertIn("FPL’s data isn’t available right now", r["next"]["html"])
        self.assertNotIn("secret", r["next"]["html"])
        self.assertIn("Try again", r["next"]["html"])
        self.assertIn("Your options for GW7", r["retry"]["html"])
        self.assertEqual(r["retry"]["posts"], 2)

    def test_request_errors_show_the_safe_api_message(self):
        h = self.go(planResponse={"status": 400, "json": {"status": "BAD_REQUEST", "error": "Invalid request", "details": ["bank_m is required"]}})["next"]["html"]
        self.assertIn("Invalid request bank_m is required", h)

    def test_network_failure_and_unreadable_answers(self):
        h = self.go(planNetworkFail=True)["next"]["html"]
        self.assertIn("couldn’t reach SquadCheck", h)
        h = self.go(planResponse={"status": 200, "notJson": True})["next"]["html"]
        self.assertIn("couldn’t read", h)

    def test_leaving_while_waiting_ignores_the_late_answer(self):
        r = smoke(planResponse=self.ok, planDelayMs=120, steps=[
            {"name": "b", "bank": "0.5"}, {"name": "s", "act": "strat", "v": "all"}, {"name": "n", "act": "next", "wait": 20},
            {"name": "cancel", "act": "cancel", "wait": 20}, {"name": "late", "wait": 250}])
        self.assertIn("Checking your options", r["n"]["html"])
        self.assertIn("What’s your game plan?", r["cancel"]["html"])
        self.assertIn("What’s your game plan?", r["late"]["html"])
        self.assertNotIn("Your options for GW7", r["late"]["html"])

    def test_stepper_follows_the_phase(self):
        r = self.go({"name": "inspect", "act": "inspect"}, {"name": "wa", "act": "wa"}, {"name": "done", "act": "wadone"})
        self.assertEqual((r["next"]["stepper"].count("stp done"), r["next"]["stepper"].count("stp on")), (1, 1))
        self.assertIn("Explore", r["inspect"]["stepper"].split("stp on")[1][:80])             # the gate belongs to the Explore step
        self.assertEqual((r["done"]["stepper"].count("stp done"), r["done"]["stepper"].count("stp on")), (2, 1))
        self.assertEqual(r["done"]["stepper"].count('aria-disabled="true"'), 2)


def sgf(n):
    return ("−" if n <= -0.05 else "+") + "%.1f" % abs(n)


def api(body_):
    fake = FakeFpl()
    for e in fake.bootstrap["elements"]:
        e["code"] = 9000 + e["id"]
    status, payload = handle(json.dumps(body_).encode(), FplDataSource(fetch=fake, clock=Clock()))
    assert status == 200, payload
    return json.loads(json.dumps(payload))


POSMAP = {str(p["id"]): p["position"] for p in directory()["players"]}
BASE_BODY = {"squad_ids": SQUAD, "bank_m": 0.5, "free_transfers": 1, "strategy": "everything"}
TW_RUNNER = CORE + """
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
console.log(JSON.stringify(input.calls.map(c => PlannerCore.tweakRequest(c.base, c.tweak, Object.assign({}, c.ctx, {pos: id => c.pos[id]})))));
"""


def tweak(base, tw, moves, hits=0, in_pos=None):
    ctx = {"moves": moves, "inPos": in_pos or [POSMAP[str(m[1])] for m in moves], "hits": hits}
    return node(TW_RUNNER, {"calls": [{"base": base, "tweak": tw, "ctx": ctx, "pos": POSMAP}]})[0]


class TestTweakRequests(unittest.TestCase):
    def ok(self, base, tw, moves, hits=0):
        r = tweak(base, tw, moves, hits)
        self.assertTrue(r["ok"], r)
        parse_request(r["body"])                                   # the real API accepts every body the page can build
        return r["body"]

    def bad(self, base, tw, moves, hits=0):
        r = tweak(base, tw, moves, hits)
        self.assertFalse(r["ok"], r)
        return r["problems"][0]

    def same_except(self, body_, base, *changed):
        a = {k: v for k, v in body_.items() if k not in changed}
        b = {k: v for k, v in base.items() if k not in changed}
        self.assertEqual(a, b)

    def test_keep_adds_the_player_and_changes_nothing_else(self):
        b = self.ok(BASE_BODY, {"kind": "keep", "out": 12}, [[12, 126]])
        self.assertEqual(b["keep_ids"], [12])
        self.same_except(b, BASE_BODY, "keep_ids")

    def test_keep_accumulates_earlier_keep_choices(self):
        base = {**BASE_BODY, "keep_ids": [13]}
        self.assertEqual(self.ok(base, {"kind": "keep", "out": 12}, [[12, 126]])["keep_ids"], [13, 12])
        self.assertEqual(self.ok(base, {"kind": "keep", "out": 12}, [[12, 126]], 0)["squad_ids"], SQUAD)

    def test_keep_rejects_a_player_who_is_not_sold_or_was_named_for_sale(self):
        self.assertIn("Choose one of the players", self.bad(BASE_BODY, {"kind": "keep", "out": 3}, [[12, 126]]))
        self.assertIn("can’t also keep", self.bad({**BASE_BODY, "strategy": None, "moves": [[12, 126]]}, {"kind": "keep", "out": 12}, [[12, 126]]))

    def test_no_hit_removes_hits_and_turns_everything_into_sensible(self):
        base = {**BASE_BODY, "max_hits": 1}
        b = self.ok(base, {"kind": "nohit"}, [[12, 126]], hits=1)
        self.assertNotIn("max_hits", b)
        self.assertEqual(b["strategy"], "sensible")
        self.same_except(b, base, "max_hits", "strategy")

    def test_no_hit_keeps_named_moves_and_adds_no_strategy(self):
        base = {"squad_ids": SQUAD, "bank_m": 0.5, "free_transfers": 1, "moves": [[12, 126]], "max_hits": 1}
        b = self.ok(base, {"kind": "nohit"}, [[12, 126]], hits=1)
        self.assertEqual(b, {"squad_ids": SQUAD, "bank_m": 0.5, "free_transfers": 1, "moves": [[12, 126]]})

    def test_no_hit_is_refused_when_the_plan_has_none(self):
        self.assertIn("already has no hit", self.bad(BASE_BODY, {"kind": "nohit"}, [[12, 126]], hits=0))

    def test_one_more_hit_raises_the_allowance_by_one(self):
        self.assertEqual(self.ok(BASE_BODY, {"kind": "hit"}, [[12, 126]])["max_hits"], 1)
        self.assertEqual(self.ok({**BASE_BODY, "max_hits": 1}, {"kind": "hit"}, [[12, 126]], 1)["max_hits"], 2)
        self.assertEqual(self.ok({**BASE_BODY, "free_transfers": 0, "max_hits": 1}, {"kind": "hit"}, [[12, 126]], 1)["max_hits"], 2)

    def test_one_more_hit_turns_sensible_into_everything(self):
        b = self.ok({**BASE_BODY, "strategy": "sensible"}, {"kind": "hit"}, [[12, 126]])
        self.assertEqual((b["strategy"], b["max_hits"]), ("everything", 1))

    def test_one_more_hit_respects_the_three_move_cap(self):
        self.assertIn("3-move limit", self.bad({**BASE_BODY, "max_hits": 2}, {"kind": "hit"}, [[12, 126]], 2))
        self.assertIn("3-move limit", self.bad({**BASE_BODY, "free_transfers": 2, "max_hits": 1}, {"kind": "hit"}, [[12, 126]], 1))
        self.assertIn("3-move limit", self.bad({**BASE_BODY, "free_transfers": 3}, {"kind": "hit"}, [[12, 126]]))

    def test_bring_names_the_moves_with_the_replacement(self):
        b = self.ok(BASE_BODY, {"kind": "bring", "index": 0, "inn": 127}, [[12, 126]])
        self.assertEqual(b["moves"], [[12, 127]])
        self.assertEqual(b["strategy"], "everything")
        self.same_except(b, BASE_BODY, "moves")

    def test_bring_replaces_only_the_chosen_move_and_prices_in_the_hits_it_needs(self):
        base = {**BASE_BODY, "max_hits": 1}
        b = self.ok(base, {"kind": "bring", "index": 1, "inn": 129}, [[12, 126], [11, 127]], hits=1)
        self.assertEqual(b["moves"], [[12, 126], [11, 129]])
        self.assertEqual(b["max_hits"], 1)
        b = self.ok(BASE_BODY, {"kind": "bring", "index": 0, "inn": 130}, [[12, 126], [11, 127]])
        self.assertEqual((b["moves"], b["max_hits"]), ([[12, 130], [11, 127]], 1))

    def test_bring_keeps_keep_choices_and_validates_the_replacement(self):
        base = {**BASE_BODY, "keep_ids": [13]}
        self.assertEqual(self.ok(base, {"kind": "bring", "index": 0, "inn": 127}, [[12, 126]])["keep_ids"], [13])
        self.assertIn("same position", self.bad(BASE_BODY, {"kind": "bring", "index": 0, "inn": 101}, [[12, 126]]))
        self.assertIn("already in your squad", self.bad(BASE_BODY, {"kind": "bring", "index": 0, "inn": 5}, [[12, 126]]))
        self.assertIn("already in this plan", self.bad(BASE_BODY, {"kind": "bring", "index": 0, "inn": 126}, [[12, 126]]))
        self.assertIn("already in this plan", self.bad(BASE_BODY, {"kind": "bring", "index": 0, "inn": 127}, [[12, 126], [11, 127]]))
        self.assertIn("Choose a replacement", self.bad(BASE_BODY, {"kind": "bring", "index": 0, "inn": None}, [[12, 126]]))
        self.assertIn("Choose which player", self.bad(BASE_BODY, {"kind": "bring", "index": 4, "inn": 127}, [[12, 126]]))

    def test_availability_reasons_match_the_request_rules(self):
        blocked = node(CORE + "\nconsole.log(JSON.stringify([PlannerCore.tweakBlocked({free_transfers:1,max_hits:2},'hit',{hits:2}), PlannerCore.tweakBlocked({free_transfers:1},'nohit',{hits:0}), PlannerCore.tweakBlocked({free_transfers:1},'hit',{hits:0}), PlannerCore.tweakBlocked({free_transfers:1,max_hits:1},'nohit',{hits:1}), PlannerCore.tweakBlocked({free_transfers:1},'keep',{hits:0})]));", {})
        self.assertTrue(blocked[0] and blocked[1])
        self.assertEqual(blocked[2:], [None, None, None])

    def test_no_tweak_ever_builds_a_body_the_api_rejects_or_drops_the_users_choices(self):
        import itertools
        bases = [BASE_BODY, {**BASE_BODY, "max_hits": 1}, {**BASE_BODY, "strategy": "sensible"}, {**BASE_BODY, "keep_ids": [13]},
                 {"squad_ids": SQUAD, "bank_m": 2.5, "free_transfers": 2, "strategy": "everything", "max_hits": 1},
                 {"squad_ids": SQUAD, "bank_m": 0.5, "free_transfers": 1, "moves": [[12, 126]], "max_hits": 1}]
        for base, tw in itertools.product(bases, [{"kind": "keep", "out": 12}, {"kind": "nohit"}, {"kind": "hit"}, {"kind": "bring", "index": 0, "inn": 127}]):
            r = tweak(base, tw, [[12, 126]], hits=base.get("max_hits", 0))
            if not r["ok"]:
                continue
            b = r["body"]
            parse_request(b)
            for k in ("squad_ids", "bank_m", "free_transfers"):
                self.assertEqual(b[k], base[k], (base, tw))
            self.assertLessEqual(b["free_transfers"] + b.get("max_hits", 0), 3, (base, tw))
            self.assertTrue(set(base.get("keep_ids", [])) <= set(b.get("keep_ids", [])), (base, tw))
            if base.get("moves") and tw["kind"] != "bring":
                self.assertEqual(b["moves"], base["moves"])


def outcome(view, kind, had_moves, before):
    return core(("tweakOutcome", view, kind, had_moves, before))[0]


class TestTweakOutcomesWithRealPlanner(unittest.TestCase):
    """Each outcome is computed from a real planner response to the body the page builds."""

    def run_tweak(self, base_body, tw, hits=None):
        payload = api(base_body)
        view = cards(payload)
        card = next(c for c in view["cards"] if c["model"])
        m = card["model"]
        moves = [[x["out"]["id"], x["inn"]["id"]] for x in m["moves"]]
        built = tweak({**base_body, "bank_m": base_body["bank_m"]}, tw(m), moves, m["hits"] if hits is None else hits)
        self.assertTrue(built["ok"], built)
        after_payload = api(built["body"])
        return payload, card, built["body"], after_payload, outcome(cards(after_payload), tw(m)["kind"], "moves" in built["body"], m)

    def test_keep_really_stops_the_planner_selling_that_player(self):
        base = {**BASE_BODY, "bank_m": 5.0}
        _, card, body_, after_payload, o = self.run_tweak(base, lambda m: {"kind": "keep", "out": m["moves"][0]["out"]["id"]})
        kept = body_["keep_ids"][0]
        sold = {t["out"]["id"] for a in after_payload["plan"]["alternatives"] for t in a["transfers"]}
        self.assertNotIn(kept, sold)
        self.assertEqual(after_payload["plan"]["inputs"]["keep_ids"], [kept])
        alts = after_payload["plan"]["alternatives"]
        after = o["after"]["model"]
        if alts:
            self.assertEqual(after["net"], alts[0]["projected_gain_vs_hold"])
            self.assertEqual(after["before"], alts[0]["projected_gain_before_hits"])
        else:
            self.assertEqual(o["after"]["kind"], "hold")

    def test_rows_compare_the_current_plan_with_the_new_answer_without_blending_measures(self):
        base = {**BASE_BODY, "bank_m": 5.0}
        _, card, _, after_payload, o = self.run_tweak(base, lambda m: {"kind": "keep", "out": m["moves"][0]["out"]["id"]})
        b, a = card["model"], o["after"]["model"]
        rows = {r[0]: r for r in o["rows"]}
        self.assertEqual(rows["Net gain vs hold (next gameweek)"][1:], [sgf(b["net"]), sgf(a["net"])])
        self.assertEqual(rows["Gain before hits"][1:], [sgf(b["before"]), sgf(a["before"])])
        self.assertEqual(rows["Hit cost"][1][0:1], "0" if not b["hitPts"] else "−")
        self.assertNotIn("outlook", " ".join(r[0].lower() for r in o["rows"]))                 # the outlook is never mixed into the table

    def test_verdict_follows_the_real_net_gain_difference(self):
        base = {**BASE_BODY, "bank_m": 5.0}
        _, card, _, _, o = self.run_tweak(base, lambda m: {"kind": "keep", "out": m["moves"][0]["out"]["id"]})
        d = o["after"]["model"]["net"] - card["model"]["net"]
        if abs(d) < 0.05:
            self.assertEqual(o["verdict"], "No change in net gain.")
        elif d > 0:
            self.assertEqual(o["verdict"], "Net gain improves by %.1f points." % d)
        else:
            self.assertEqual(o["verdict"], "Net gain drops by %.1f points." % abs(d))

    def test_no_hit_on_a_plan_with_a_hit_is_answered_by_the_planner_not_assumed(self):
        base = {"squad_ids": SQUAD, "bank_m": 5.0, "free_transfers": 0, "strategy": "everything", "max_hits": 2}
        payload, card, body_, after_payload, o = self.run_tweak(base, lambda m: {"kind": "nohit"})
        self.assertGreater(card["model"]["hits"], 0)
        self.assertEqual(body_, {"squad_ids": SQUAD, "bank_m": 5.0, "free_transfers": 0, "strategy": "sensible"})
        self.assertEqual(after_payload["plan"]["alternatives"], [])                          # with no free transfer nothing is left but Hold
        self.assertEqual((o["after"]["kind"], o["after"]["model"]["title"], o["after"]["model"]["net"]), ("hold", "Hold", 0.0))
        d = 0.0 - card["model"]["net"]
        self.assertEqual(o["verdict"], "No change in net gain." if abs(d) < 0.05 else ("Net gain improves by %.1f points." % d if d > 0 else "Net gain drops by %.1f points." % abs(d)))

    def test_one_more_hit_is_a_real_request_and_the_answer_is_whatever_the_planner_says(self):
        base = {**BASE_BODY, "bank_m": 5.0}
        _, card, body_, after_payload, o = self.run_tweak(base, lambda m: {"kind": "hit"})
        self.assertEqual(body_["max_hits"], 1)
        self.assertEqual(after_payload["plan"]["inputs"]["max_hits"], 1)
        top = after_payload["plan"]["alternatives"][0]
        self.assertEqual(o["after"]["model"]["net"], top["projected_gain_vs_hold"])
        self.assertEqual(o["after"]["model"]["hitPts"], top["hit_points"])

    def test_bring_evaluates_exactly_the_moves_asked_for(self):
        base = {**BASE_BODY, "bank_m": 5.0}
        payload = api(base)
        view = cards(payload)
        m = view["cards"][0]["model"]
        out_id, in_id, in_pos = m["moves"][0]["out"]["id"], m["moves"][0]["inn"]["id"], m["moves"][0]["inn"]["pos"]
        new = next(int(i) for i, pos in sorted(POSMAP.items(), key=lambda kv: int(kv[0])) if pos == in_pos and int(i) not in SQUAD and int(i) != in_id)
        built = tweak(base, {"kind": "bring", "index": 0, "inn": new}, [[out_id, in_id]], 0)
        self.assertTrue(built["ok"])
        after_payload = api(built["body"])
        o = outcome(cards(after_payload), "bring", True, m)
        req = after_payload["plan"]["requested"]
        if req["plan"]:
            self.assertEqual(o["after"]["kind"], "mine")
            self.assertEqual([(x["out"]["id"], x["inn"]["id"]) for x in o["after"]["model"]["moves"]], [(out_id, new)])
            self.assertEqual(o["after"]["model"]["net"], req["plan"]["projected_gain_vs_hold"])
        else:
            self.assertIsNone(o["after"])
            self.assertEqual(o["reason"], req["reason"])

    def test_a_request_the_planner_cannot_do_is_reported_with_its_reason_and_no_numbers(self):
        base = {"squad_ids": SQUAD, "bank_m": 0.0, "free_transfers": 1, "strategy": "everything"}
        view = cards(api({**base, "bank_m": 5.0}))
        m = view["cards"][0]["model"]
        moves = [[x["out"]["id"], x["inn"]["id"]] for x in m["moves"]]
        priciest = max((p for p in directory()["players"] if p["id"] not in SQUAD and p["position"] == m["moves"][0]["inn"]["pos"]), key=lambda p: p["price"])
        built = tweak(base, {"kind": "bring", "index": 0, "inn": priciest["id"]}, moves, 0)
        self.assertTrue(built["ok"])
        payload = api(built["body"])
        o = outcome(cards(payload), "bring", True, m)
        if payload["plan"]["requested"]["plan"] is None:
            self.assertIsNone(o["after"])
            self.assertEqual(o["reason"], payload["plan"]["requested"]["reason"])
            self.assertNotIn("rows", o)
        else:
            self.skipTest("fixture prices allowed the move")


class TestShareText(unittest.TestCase):
    def test_message_and_link(self):
        r = core(("shareText", "Swap Gvardiol for Hall", 7), ("shareText", "Hold", None), ("waLink", "a b & c?"))
        self.assertEqual(r[0], "I’m weighing Swap Gvardiol for Hall for GW7 on SquadCheck. Would you do it? https://squadcheck.club")
        self.assertEqual(r[1], "I’m weighing Hold on SquadCheck. Would you do it? https://squadcheck.club")
        self.assertEqual(r[2], "https://wa.me/?text=a%20b%20%26%20c%3F")

    def test_the_message_makes_no_claims_about_results(self):
        text = core(("shareText", "Swap A for B", 7))[0].lower()
        for word in ("guarantee", "will score", "points", "gain", "sure", "best"):
            self.assertNotIn(word, text)


class TestWhatGoesIn(unittest.TestCase):
    def facts(self, payload, which=0):
        v = cards(payload)
        c = [x for x in v["cards"] if x["model"]][which]
        return c, v, {f["k"]: f["v"] for f in core(("whatGoesIn", c, v))[0]}

    def test_every_line_is_read_from_the_response(self):
        payload = api({**BASE_BODY, "bank_m": 5.0})
        c, v, f = self.facts(payload)
        m, alt = c["model"], payload["plan"]["alternatives"][0]
        mv = m["moves"][0]
        self.assertIn("%s %.1f vs %s %.1f" % (mv["inn"]["name"], mv["inn"]["xp"], mv["out"]["name"], mv["out"]["xp"]), f["Projected points for GW7"])
        pos = {s["id"]: s["position"] for s in payload["squad"]}
        pos.update({t["in"]["id"]: t["in"]["position"] for t in alt["transfers"]})
        xi = [pos[i] for i in alt["xi_ids"]]
        form = "%d-%d-%d" % (xi.count(2), xi.count(3), xi.count(4))
        self.assertIn("(%s)" % form, f["Your best XI after the move"])
        self.assertIn("No points hit", f["Points hits"])
        self.assertIn("£%.1fm" % (alt["bank_after"] / 10), f["Money"])
        self.assertIn("selling price may differ", payload["plan"]["inputs"]["price_source"])        # the planner says it is an estimate
        self.assertIn("Current prices are used, not your selling prices, so this is an estimate.", f["Money"])
        self.assertNotIn("now_cost", f["Money"])                                                     # no internal field names on screen
        self.assertIn("No availability flags", f["Availability"])
        self.assertIn("Every player in this plan has a projection", f["Data completeness"])

    def test_hits_availability_and_completeness_follow_the_data(self):
        payload = api({"squad_ids": SQUAD, "bank_m": 5.0, "free_transfers": 0, "strategy": "everything", "max_hits": 1})
        alt = payload["plan"]["alternatives"][0]
        alt["transfers"][0]["in"]["status"] = "d"
        alt["data_complete"] = False
        payload["plan"]["data_quality"] = {"complete": False, "note": "Players without a projection are never bought or sold."}
        c, v, f = self.facts(payload)
        self.assertIn("1 hit at 4.0 points each = −4" if "4.0" in f["Points hits"] else "1 hit at 4 points each = −4", f["Points hits"])
        self.assertIn("Doubtful", f["Availability"])
        self.assertEqual(f["Data completeness"], "Players without a projection are never bought or sold.")

    def test_hold_describes_itself_without_move_specific_lines(self):
        payload = api({**BASE_BODY, "free_transfers": 0, "strategy": "sensible"})
        v = cards(payload)
        hold = v["cards"][-1]
        keys = [f["k"] for f in core(("whatGoesIn", hold, v))[0]]
        self.assertIn("What Hold is", keys)
        self.assertNotIn("Availability", keys)

    def test_the_not_factored_list_states_the_planners_own_limits(self):
        lim = node(CORE + "\nconsole.log(JSON.stringify(PlannerCore.NOT_FACTORED));", {})
        text = " ".join(lim).lower()
        for needle in ("selling prices", "provisional", "backtested", "without a projection"):
            self.assertIn(needle, text)
        self.assertNotIn("ceiling", text + json.dumps(self.facts(api({**BASE_BODY, "bank_m": 5.0}))[2]).lower())


class Flow(unittest.TestCase):
    """Shared set-up: Explore on real planner output, then Inspect, with the gate already passed unless a test says otherwise."""

    def setUp(self):
        self.base = real_plan(free_transfers=1, strategy="everything")[1]
        self.view = cards(self.base)
        self.top = self.view["cards"][0]
        self.m = self.top["model"]
        self.out_id = self.m["moves"][0]["out"]["id"]
        self.in_id = self.m["moves"][0]["inn"]["id"]

    def ok(self, payload):
        return {"status": 200, "json": payload}

    def head(self, strategy="all", ft_down=0, extra=()):
        steps = [{"name": "bank", "bank": "0.5"}, {"name": "strat", "act": "strat", "v": strategy}]
        steps += [{"name": "ft%d" % i, "act": "ft", "v": -1} for i in range(ft_down)]
        if ft_down:                                  # with no free transfer "No" is unavailable: the user must choose a hit
            steps.append({"name": "hits", "act": "hits", "v": "1"})
        return steps + [{"name": "next", "act": "next"}] + list(extra)

    def to_compare(self, **kw):
        return self.head(**kw) + [{"name": "inspect", "act": "inspect"}]

    def posts(self, r):
        return [json.loads(p["body"]) for p in r["initial"]["posts"]]

    def new_in(self):
        pos = self.m["moves"][0]["inn"]["pos"]
        return next(int(i) for i, p in sorted(POSMAP.items(), key=lambda kv: int(kv[0])) if p == pos and int(i) not in SQUAD and int(i) != self.in_id)


class TestGate(Flow):
    def go(self, *extra, **kw):
        return smoke(planResponse=self.ok(self.base), steps=self.to_compare() + list(extra), **kw)

    def test_inspect_asks_the_user_to_share_and_says_exactly_what_to_do(self):
        r = self.go()
        h = r["inspect"]["html"]
        self.assertIn("Share to unlock the full breakdown", h)
        for step in ("Tap Share on WhatsApp", "Send it to a friend or a group", "tap I’ve shared it"):
            self.assertIn(step, h.replace("<b>", "").replace("</b>", ""))
        self.assertIn("We can’t tell whether a message was sent. Continuing is on trust.", h)
        self.assertIn("I’m weighing %s for GW7 on SquadCheck" % self.m["title"], htmllib.unescape(h))
        self.assertNotIn("I’ve shared it, continue", h)                              # not offered until WhatsApp was opened
        self.assertNotIn("What goes into this suggestion", h)
        self.assertEqual(r["inspect"]["opened"], [])
        self.assertIsNone(r["inspect"]["unlocked"])

    def test_sharing_opens_whatsapp_with_the_message_and_does_not_claim_it_was_sent(self):
        r = self.go({"name": "wa", "act": "wa"})
        step = r["wa"]
        self.assertEqual(len(step["opened"]), 1)
        url = step["opened"][0]
        self.assertTrue(url.startswith("https://wa.me/?text="))
        from urllib.parse import unquote
        self.assertEqual(unquote(url.split("text=", 1)[1]), "I’m weighing %s for GW7 on SquadCheck. Would you do it? https://squadcheck.club" % self.m["title"])
        h = step["html"]
        self.assertIn("WhatsApp should have opened in a new tab", h)
        self.assertIn("I’ve shared it, continue", h)
        for claim in ("message sent", "shared successfully", "thanks for sharing", "unlocked!"):
            self.assertNotIn(claim, h.lower())
        self.assertIsNone(step["unlocked"])                                              # opening WhatsApp alone unlocks nothing

    def test_continuing_unlocks_the_breakdown_for_the_session(self):
        r = self.go({"name": "wa", "act": "wa"}, {"name": "done", "act": "wadone"}, {"name": "back", "act": "toexplore"}, {"name": "again", "act": "inspect"})
        self.assertIn("What goes into this suggestion", r["done"]["html"])
        self.assertEqual(r["done"]["unlocked"], "1")
        self.assertIn("What goes into this suggestion", r["again"]["html"])               # no second gate
        self.assertEqual(len(r["again"]["opened"]), 1)

    def test_an_unlock_from_earlier_in_the_session_is_remembered(self):
        r = smoke(planResponse=self.ok(self.base), unlocked=True, steps=self.to_compare())
        self.assertIn("What goes into this suggestion", r["inspect"]["html"])

    def test_hold_is_never_gated(self):
        r = self.go({"name": "back", "act": "toexplore"}, {"name": "pick", "act": "pickplan", "v": "hold"}, {"name": "hold", "act": "inspect"})
        self.assertIn("Share to unlock", r["inspect"]["html"])
        self.assertIn("What goes into this suggestion", r["hold"]["html"])
        self.assertIn("What Hold is", r["hold"]["html"])
        self.assertEqual(r["hold"]["opened"], [])
        self.assertIsNone(r["hold"]["unlocked"])

    def test_back_from_the_gate_keeps_it_locked(self):
        r = self.go({"name": "back", "act": "toexplore"})
        self.assertIn("Your options for GW7", r["back"]["html"])
        self.assertIsNone(r["back"]["unlocked"])

    def test_a_blocked_popup_is_explained_and_the_message_can_be_copied(self):
        r = self.go({"name": "wa", "act": "wa", "popupBlocked": True}, {"name": "copy", "act": "wacopy"})
        self.assertIn("couldn’t open WhatsApp from here", r["wa"]["html"])
        self.assertIn("Copy the message", r["wa"]["html"])
        self.assertEqual(r["copy"]["copied"], "I’m weighing %s for GW7 on SquadCheck. Would you do it? https://squadcheck.club" % self.m["title"])
        self.assertIn("Copied", r["copy"]["html"])

    def test_page_opens_only_a_whatsapp_link(self):
        code = TestStaticContract.CODE
        self.assertEqual(len(re.findall(r"window\.open\(", code)), 1)
        self.assertIn("window.open(C.waLink(text)", code)


class TestComparePanel(Flow):
    def compare(self, payload=None, **kw):
        return smoke(planResponse=self.ok(payload or self.base), unlocked=True, steps=self.to_compare(**kw))["inspect"]["html"]

    def test_the_numbers_are_separate_blocks_with_their_own_labels(self):
        h = self.compare()
        for label in ("Net gain vs hold", "Gain before hits", "Hit cost", "Three-gameweek outlook vs hold", "Bank after"):
            self.assertIn(label, h)
        self.assertIn('Net gain vs hold</span><b class="pos">%s</b>' % sgf(self.m["net"]), h)
        self.assertIn("after hit costs. Plans are ranked by this.", h)
        self.assertEqual(h.count("Plans are ranked by this"), 1)
        self.assertEqual(h.count("Provisional"), 1)
        self.assertIn("Provisional: weeks 2 and 3 have not been backtested.", h)
        self.assertIn("GW7 " + sgf(self.m["outlook"][0]) + " · GW8 " + sgf(self.m["outlook"][1]) + " · GW9 " + sgf(self.m["outlook"][2]), h)
        self.assertNotIn("three-gameweek gain", h.lower())

    def test_a_plan_with_a_hit_shows_gain_before_hits_and_net_gain_apart(self):
        payload = real_plan(free_transfers=0, strategy="everything", max_hits=1)[1]
        m = cards(payload)["cards"][0]["model"]
        self.assertEqual(m["hits"], 1)
        h = smoke(planResponse=self.ok(payload), unlocked=True, steps=self.to_compare(ft_down=1))["inspect"]["html"]
        self.assertIn('Net gain vs hold</span><b class="%s">%s</b>' % ("neg" if m["net"] < 0 else "pos", sgf(m["net"])), h)
        self.assertIn('Gain before hits</span><b>%s</b>' % sgf(m["before"]), h)
        self.assertIn('Hit cost</span><b class="neg">−%d</b>' % m["hitPts"], h)
        self.assertNotEqual(sgf(m["before"]), sgf(m["net"]))

    def test_missing_outlook_shows_the_fallback_in_the_panel(self):
        payload = json.loads(json.dumps(self.base))
        for a in payload["plan"]["alternatives"]:
            a.pop("outlook", None)
        h = self.compare(payload)
        self.assertIn("Three-gameweek outlook unavailable.", h)
        self.assertNotIn("Provisional: weeks 2 and 3", h)

    def test_what_goes_into_this_and_the_limits_are_shown(self):
        h = htmllib.unescape(self.compare())
        for k in ("What goes into this suggestion", "Your best XI after the move", "Points hits", "Money", "Club limit", "Availability", "Data completeness", "Not factored in"):
            self.assertIn(k, h)
        self.assertIn("Your actual selling prices", h)
        self.assertNotIn("ceiling", h.lower())

    def test_players_are_shown_with_their_next_gameweek_projection(self):
        h = self.compare()
        mv = self.m["moves"][0]
        self.assertIn(mv["out"]["name"], h)
        self.assertIn("%.1f xPts (GW7)" % mv["out"]["xp"], h)
        self.assertIn("%.1f xPts (GW7)" % mv["inn"]["xp"], h)

    def test_the_planners_risks_are_listed(self):
        payload = json.loads(json.dumps(self.base))
        payload["plan"]["alternatives"][0]["risks"] = ["Foo: availability flag 'd'", "Affordability is estimated from current prices, not selling prices"]
        h = htmllib.unescape(self.compare(payload))
        self.assertIn("What the planner flagged", h)
        self.assertIn("Foo: availability flag 'd'", h)

    def test_hold_has_no_tweaks(self):
        r = smoke(planResponse=self.ok(self.base), unlocked=True, steps=self.head() + [{"name": "pick", "act": "pickplan", "v": "hold"}, {"name": "inspect", "act": "inspect"}])
        self.assertNotIn("Tweak it and see what changes", r["inspect"]["html"])

    def test_back_this_plan_opens_your_call(self):
        r = smoke(planResponse=self.ok(self.base), unlocked=True, steps=self.to_compare() + [{"name": "back", "act": "backplan"}, {"name": "to", "act": "tocompare"}])
        self.assertIn("my call.", r["back"]["html"])
        self.assertIn("What goes into this suggestion", r["to"]["html"])


class TestTweaksInThePage(Flow):
    def setUp(self):
        super().setUp()
        o, n = self.out_id, self.new_in()
        self.new = n
        self.keep_resp = self.ok(real_plan(free_transfers=1, strategy="everything", keep_ids=[o])[1])
        self.hit_resp = self.ok(real_plan(free_transfers=1, strategy="everything", max_hits=1)[1])
        self.bring_resp = self.ok(real_plan(free_transfers=1, strategy="everything", moves=[[o, n]])[1])
        self.both_resp = self.ok(real_plan(free_transfers=1, strategy="everything", keep_ids=[o], max_hits=1)[1])
        self.rules = [{"has": ["keep_ids", "max_hits"], "response": self.both_resp}, {"has": ["keep_ids"], "response": self.keep_resp},
                      {"has": ["moves"], "response": self.bring_resp}, {"has": ["max_hits"], "response": self.hit_resp}]

    def go(self, *extra, rules=None, **kw):
        return smoke(planResponse=self.ok(self.base), planRules=self.rules if rules is None else rules, unlocked=True, steps=self.to_compare() + list(extra), **kw)

    def expected(self, **extra):
        return {**BASE_BODY, **extra}

    def row(self, html_, label):
        m = re.search(r'<span class="l">%s</span><span class="a">(.*?)</span><span class="b">(.*?)</span>' % re.escape(label), html_)
        return None if not m else (htmllib.unescape(m.group(1)), htmllib.unescape(m.group(2)))

    def test_keep_sends_one_real_request_and_shows_the_planners_answer(self):
        r = self.go({"name": "open", "act": "twk", "v": "keep"}, {"name": "run", "act": "twkrun"})
        self.assertEqual(self.posts(r)[-1], self.expected(keep_ids=[self.out_id]))
        self.assertEqual(len(self.posts(r)), 2)                                           # Explore, then exactly one tweak request
        h = r["run"]["html"]
        self.assertIn("From a new planner run", h)
        after = cards(self.keep_resp["json"])
        top = next(c for c in after["cards"] if c["kind"] == "rank")["model"] if any(c["kind"] == "rank" for c in after["cards"]) else after["cards"][-1]["model"]
        self.assertEqual(self.row(h, "Net gain vs hold (next gameweek)"), (sgf(self.m["net"]), sgf(top["net"])))
        self.assertEqual(self.row(h, "Plan"), (self.m["title"], top["title"]))

    def test_nothing_is_shown_as_the_new_answer_while_the_request_is_in_flight(self):
        rules = [{**r, "delay": 250} for r in self.rules]
        r = self.go({"name": "open", "act": "twk", "v": "keep"}, {"name": "run", "act": "twkrun", "wait": 40}, {"name": "later", "wait": 500}, rules=rules)
        h = r["run"]["html"]
        self.assertIn("Asking the planner", h)
        self.assertNotIn('data-act="twkuse"', h)
        self.assertNotIn("From a new planner run", h)
        self.assertIn(self.m["title"], h)                                                # the original plan is still the one shown
        self.assertIn('data-act="twkuse"', r["later"]["html"])

    def test_use_this_version_replaces_the_plan_and_later_tweaks_build_on_it(self):
        r = self.go({"name": "open", "act": "twk", "v": "keep"}, {"name": "run", "act": "twkrun"}, {"name": "use", "act": "twkuse"},
                    {"name": "open2", "act": "twk", "v": "hit"}, {"name": "run2", "act": "twkrun"})
        self.assertIn("Edited: kept a player", r["use"]["html"])
        self.assertEqual(self.posts(r)[-1], self.expected(keep_ids=[self.out_id], max_hits=1))
        self.assertIn("From a new planner run", r["run2"]["html"])

    def test_one_more_hit_raises_the_allowance_and_keeps_everything_else(self):
        r = self.go({"name": "open", "act": "twk", "v": "hit"}, {"name": "run", "act": "twkrun"})
        self.assertEqual(self.posts(r)[-1], self.expected(max_hits=1))

    def test_one_more_hit_is_unavailable_at_the_three_move_cap(self):
        steps = [{"name": "bank", "bank": "0.5"}, {"name": "strat", "act": "strat", "v": "all"}, {"name": "ft", "act": "ft", "v": 1}, {"name": "hits", "act": "hits", "v": 1},
                 {"name": "next", "act": "next"}, {"name": "inspect", "act": "inspect"}]
        payload = real_plan(free_transfers=2, strategy="everything", max_hits=1)[1]
        r = smoke(planResponse=self.ok(payload), unlocked=True, steps=steps)
        h = r["inspect"]["html"]
        self.assertRegex(h, r'data-act="twk" data-v="hit"[^>]*disabled')
        self.assertIn("Already at the 3-move limit", h)
        self.assertEqual(r["inspect"]["posts"], 1)

    def test_without_the_hit_asks_for_a_sensible_plan_with_no_hits(self):
        base = real_plan(free_transfers=0, strategy="everything", max_hits=1)[1]
        self.assertGreater(cards(base)["cards"][0]["model"]["hits"], 0)
        sensible = self.ok(real_plan(free_transfers=0, strategy="sensible")[1])
        rules = [{"eq": {"strategy": "sensible"}, "response": sensible}]
        r = smoke(planResponse=self.ok(base), planRules=rules, unlocked=True, steps=self.to_compare(ft_down=1) + [
            {"name": "open", "act": "twk", "v": "nohit"}, {"name": "run", "act": "twkrun"}, {"name": "use", "act": "twkuse"}])
        self.assertEqual(self.posts(r)[-1], {"squad_ids": SQUAD, "bank_m": 0.5, "free_transfers": 0, "strategy": "sensible"})
        d = 0.0 - cards(base)["cards"][0]["model"]["net"]
        self.assertIn("No change in net gain." if abs(d) < 0.05 else ("Net gain improves by %.1f points." % d if d > 0 else "Net gain drops by %.1f points." % abs(d)), r["run"]["html"])
        self.assertIn("Edited: without the hit", r["use"]["html"])
        self.assertNotIn("Tweak it and see what changes", r["use"]["html"])                # the result is Hold: no more tweaks

    def test_without_the_hit_is_unavailable_when_the_plan_has_no_hit(self):
        r = self.go()
        self.assertRegex(r["inspect"]["html"], r'data-act="twk" data-v="nohit"[^>]*disabled')
        self.assertIn("This plan already has no hit.", r["inspect"]["html"])

    def test_buy_someone_else_names_the_moves_and_waits_for_a_choice(self):
        r = self.go({"name": "open", "act": "twk", "v": "bring"}, {"name": "idle"}, {"name": "pick", "act": "cpick", "v": self.new, "id": "bring"},
                    {"name": "run", "act": "twkrun"})
        self.assertRegex(r["idle"]["html"], r'data-act="twkrun" disabled')
        self.assertEqual(self.posts(r)[-1], self.expected(moves=[[self.out_id, self.new]]))
        self.assertIn("From a new planner run", r["run"]["html"])

    def test_two_tweaks_cannot_be_in_flight_at_once(self):
        rules = [{**r, "delay": 300} for r in self.rules]
        r = self.go({"name": "open", "act": "twk", "v": "keep"},
                    {"name": "run", "act": "twkrun", "also": [{"act": "twkrun"}, {"act": "twkretry"}], "wait": 40}, {"name": "later", "wait": 600}, rules=rules)
        self.assertEqual(len(self.posts(r)), 2)
        self.assertRegex(r["run"]["html"], r'data-act="twk" data-v="nohit"[^>]*disabled')
        self.assertRegex(r["run"]["html"], r'data-act="twk" data-v="bring"[^>]*disabled')

    def test_cancelling_ignores_the_late_answer(self):
        rules = [{**r, "delay": 250} for r in self.rules]
        r = self.go({"name": "open", "act": "twk", "v": "keep"}, {"name": "run", "act": "twkrun", "wait": 30},
                    {"name": "cancel", "act": "twkcancel", "wait": 30}, {"name": "late", "wait": 500}, rules=rules)
        self.assertIn("Check it", r["cancel"]["html"])
        self.assertNotIn("From a new planner run", r["late"]["html"])
        self.assertNotIn('data-act="twkuse"', r["late"]["html"])
        self.assertIn(self.m["title"], r["late"]["html"])

    def test_a_failed_tweak_keeps_the_original_plan_and_can_be_retried(self):
        leak = {"status": 502, "json": {"status": "ERROR", "error": "secret upstream detail", "details": ["trace"]}}
        failing = [{"has": ["keep_ids"], "response": leak}]
        r = self.go({"name": "open", "act": "twk", "v": "keep"}, {"name": "run", "act": "twkrun"},
                    {"name": "retry", "act": "twkretry", "planRules": self.rules}, rules=failing)
        h = r["run"]["html"]
        self.assertIn("FPL’s data isn’t available right now", h)
        self.assertNotIn("secret", h)
        self.assertNotIn("From a new planner run", h)
        self.assertIn(self.m["title"], h)
        self.assertIn("Try again", h)
        self.assertEqual(len(self.posts(r)), 3)
        self.assertIn("From a new planner run", r["retry"]["html"])

    def test_an_old_result_never_appears_under_a_different_tweak_or_choice(self):
        r = self.go({"name": "open", "act": "twk", "v": "keep"}, {"name": "run", "act": "twkrun"}, {"name": "switch", "act": "twk", "v": "hit"},
                    {"name": "back", "act": "twk", "v": "keep"})
        self.assertIn("From a new planner run", r["run"]["html"])
        self.assertNotIn("From a new planner run", r["switch"]["html"])
        self.assertNotIn("From a new planner run", r["back"]["html"])

    def test_reset_returns_to_the_original_plan(self):
        r = self.go({"name": "open", "act": "twk", "v": "keep"}, {"name": "run", "act": "twkrun"}, {"name": "use", "act": "twkuse"}, {"name": "reset", "act": "twkreset"})
        self.assertIn("Edited:", r["use"]["html"])
        self.assertNotIn("Edited:", r["reset"]["html"])
        self.assertIn(self.m["title"], r["reset"]["html"])

    def test_every_tweak_keeps_the_users_constraints(self):
        r = self.go({"name": "k", "act": "twk", "v": "keep"}, {"name": "kr", "act": "twkrun"}, {"name": "h", "act": "twk", "v": "hit"}, {"name": "hr", "act": "twkrun"},
                    {"name": "b", "act": "twk", "v": "bring"}, {"name": "bp", "act": "cpick", "v": self.new, "id": "bring"}, {"name": "br", "act": "twkrun"})
        for body_ in self.posts(r):
            self.assertEqual((body_["squad_ids"], body_["bank_m"], body_["free_transfers"]), (SQUAD, 0.5, 1))
        self.assertEqual(len(self.posts(r)), 4)

    def test_named_moves_survive_a_tweak_and_a_named_player_cannot_also_be_kept(self):
        named = real_plan(free_transfers=1, moves=[[12, 126]])[1]
        hit = self.ok(real_plan(free_transfers=1, moves=[[12, 126]], max_hits=1)[1])
        steps = [{"name": "bank", "bank": "0.5"}, {"name": "mind", "act": "strat", "v": "mind"}, {"name": "s", "act": "cpick", "v": 12, "id": "sell0"},
                 {"name": "b", "act": "cpick", "v": 126, "id": "buy0"}, {"name": "next", "act": "next"}, {"name": "inspect", "act": "inspect"},
                 {"name": "open", "act": "twk", "v": "hit"}, {"name": "run", "act": "twkrun"},
                 {"name": "k", "act": "twk", "v": "hit"}, {"name": "k2", "act": "twk", "v": "keep"}, {"name": "kr", "act": "twkrun"}]
        r = smoke(planResponse=self.ok(named), planRules=[{"has": ["max_hits"], "response": hit}], unlocked=True, steps=steps)
        first = self.posts(r)[0]
        self.assertEqual(first["moves"], [[12, 126]])
        self.assertNotIn("strategy", first)
        self.assertEqual(self.posts(r)[1], {**first, "max_hits": 1})
        self.assertIn("You asked to sell this player, so you can’t also keep them.", htmllib.unescape(r["kr"]["html"]))
        self.assertEqual(len(self.posts(r)), 2)                                           # the refused keep sent nothing

    def test_worth_a_hit_semantics_survive_the_no_hit_tweak(self):
        r = smoke(planResponse=self.ok(real_plan(free_transfers=1, strategy="everything", max_hits=1)[1]), unlocked=True,
                  steps=self.head(strategy="hit") + [{"name": "inspect", "act": "inspect"}])
        self.assertEqual(self.posts(r)[0], self.expected(max_hits=1))                       # hits permitted, strategy "everything"
        m = cards(real_plan(free_transfers=1, strategy="everything", max_hits=1)[1])["cards"][0]["model"]
        if m["hits"]:
            self.assertNotRegex(r["inspect"]["html"], r'data-act="twk" data-v="nohit"[^>]*disabled')
        else:
            self.assertRegex(r["inspect"]["html"], r'data-act="twk" data-v="nohit"[^>]*disabled')



class TestCallCardText(unittest.TestCase):
    def test_text_is_built_from_the_plans_own_figures(self):
        payload = api({**BASE_BODY, "bank_m": 5.0})
        v = cards(payload)
        c = v["cards"][0]
        k = core(("callCard", c, v))[0]
        m = c["model"]
        self.assertEqual(k["title"], m["title"])
        self.assertEqual(k["moves"], ["%s → %s" % (x["out"]["name"], x["inn"]["name"]) for x in m["moves"]])
        self.assertEqual(k["proj"], "Planner projection: %s points vs holding for the next gameweek (GW7). A projection, not a promise." % sgf(m["net"]))
        self.assertEqual(k["text"].split("\n"), ["SquadCheck · GW7", "My call: " + m["title"]] + k["moves"] + [k["proj"], "Would you back it?", "https://squadcheck.club"])

    def test_a_hit_is_stated_separately_from_the_net_figure(self):
        payload = api({"squad_ids": SQUAD, "bank_m": 5.0, "free_transfers": 0, "strategy": "everything", "max_hits": 1})
        v = cards(payload)
        m = v["cards"][0]["model"]
        k = core(("callCard", v["cards"][0], v))[0]
        self.assertIn("%s points vs holding" % sgf(m["net"]), k["proj"])
        self.assertIn("after the %d-point hit" % m["hitPts"], k["proj"])
        self.assertNotIn(sgf(m["before"]) + " points", k["proj"]) if sgf(m["before"]) != sgf(m["net"]) else None

    def test_hold_and_risks(self):
        payload = api({**BASE_BODY, "free_transfers": 0, "strategy": "sensible"})
        v = cards(payload)
        k = core(("callCard", v["cards"][-1], v))[0]
        self.assertEqual((k["title"], k["proj"], k["moves"]), ("Hold", "Holding this week.", []))
        self.assertEqual(k["risks"], ["You miss out if a better move was available."])
        payload2 = api({**BASE_BODY, "bank_m": 5.0})
        payload2["plan"]["alternatives"][0]["risks"] = ["A: r1", "B: r2", "C: r3", "D: r4", "Affordability is estimated from current prices, not selling prices"]
        v2 = cards(payload2)
        self.assertEqual(core(("callCard", v2["cards"][0], v2))[0]["risks"], ["A: r1", "B: r2", "C: r3"])
        payload2["plan"]["alternatives"][0]["risks"] = []
        v3 = cards(payload2)
        self.assertIn("flagged no specific risks", core(("callCard", v3["cards"][0], v3))[0]["risks"][0])

    def test_the_text_never_promises_or_overclaims(self):
        payload = api({**BASE_BODY, "bank_m": 5.0})
        v = cards(payload)
        text = core(("callCard", v["cards"][0], v))[0]["text"].lower()
        for word in ("guarantee", "will score", "certain", "best", "ceiling", "upside", "saved"):
            self.assertNotIn(word, text)


class TestYourCall(Flow):
    def call(self, *extra, **kw):
        return smoke(planResponse=self.ok(self.base), unlocked=True, steps=self.to_compare() + [{"name": "back", "act": "backplan"}] + list(extra), **kw)

    def test_the_screen_shows_the_plan_the_measures_the_risk_and_the_disclaimer(self):
        h = self.call()["back"]["html"]
        m = self.m
        self.assertIn("my call.", h)
        self.assertIn(m["title"], h)
        self.assertIn('Net gain vs hold</span><b class="pos">%s</b>' % sgf(m["net"]), h)
        for label in ("Gain before hits", "Hit cost", "Three-gameweek outlook vs hold", "Bank after"):
            self.assertIn(label, h)
        self.assertEqual(h.count("Provisional"), 1)
        self.assertIn("Main risk", h)
        self.assertIn("A projection, not a promise.", h)
        self.assertIn("Why I’m backing it", h)

    def test_nothing_claims_the_call_is_saved_or_retrievable(self):
        h = htmllib.unescape(self.call()["back"]["html"])
        self.assertIn("Recorded for this visit only. It isn’t saved to an account, and refreshing or closing this page clears it.", h)
        self.assertIn("Sign-in isn’t live yet, so nothing is saved today.", h)
        self.assertIn("For now your call is kept in this browser tab only.", h)
        for claim in ("call saved", "saved to your account", "we’ll email you", "we will email", "you can come back", "retrieve"):
            self.assertNotIn(claim, h.lower())

    def test_the_benefits_block_is_present_and_framed_as_what_an_account_will_give(self):
        h = htmllib.unescape(self.call()["back"]["html"])
        self.assertIn("Lock it in. Find out if you were right.", h)
        self.assertIn("This is what an account will give you:", h)
        for b in ("A verdict, not a guess", "No more “what if?”", "A record you can point to"):
            self.assertIn(b, h)
        self.assertIn('data-act="locksheet"', h)

    def test_the_main_risk_comes_from_the_planner(self):
        payload = json.loads(json.dumps(self.base))
        payload["plan"]["alternatives"][0]["risks"] = ["Foo: availability flag 'd'", "Bar: LOW projection confidence"]
        h = htmllib.unescape(smoke(planResponse=self.ok(payload), unlocked=True, steps=self.to_compare() + [{"name": "back", "act": "backplan"}])["back"]["html"])
        self.assertIn("Foo: availability flag 'd'", h)
        self.assertIn("Bar: LOW projection confidence", h)

    def test_reasons_are_the_users_own_words_and_survive_updates(self):
        r = self.call({"name": "typed", "why": "Because I rate him"}, {"name": "chip", "act": "reason", "v": "Form"}, {"name": "chip2", "act": "reason", "v": "Budget"},
                      {"name": "off", "act": "reason", "v": "Form"})
        h = htmllib.unescape(r["chip"]["html"])
        self.assertRegex(h, r'<textarea id="why"[^>]*>Because I rate him Recent form backs it\.</textarea>')
        self.assertIn('aria-pressed="true"', r["chip"]["html"])
        h2 = htmllib.unescape(r["chip2"]["html"])
        self.assertIn("Because I rate him Recent form backs it. It fits my bank without forcing anything else.", h2)
        self.assertEqual(r["off"]["html"].count('class="chip on"'), 1)
        self.assertEqual(htmllib.unescape(r["back"]["html"]).count("Recent form backs it."), 0)               # nothing is pre-filled for the user
        self.assertEqual(re.search(r"<textarea[^>]*>(.*?)</textarea>", r["back"]["html"], re.S).group(1), "")

    def test_a_different_plan_starts_a_fresh_call_but_the_same_plan_keeps_its_words(self):
        r = self.call({"name": "typed", "why": "Mine"}, {"name": "comp", "act": "tocompare"}, {"name": "again", "act": "backplan"},
                      {"name": "x", "act": "toexplore"}, {"name": "pick", "act": "pickplan", "v": "hold"},
                      {"name": "hold", "act": "inspect"}, {"name": "hcall", "act": "backplan"})
        area = lambda step: re.search(r"<textarea.*?</textarea>", r[step]["html"], re.S).group(0)
        self.assertIn(">Mine</textarea>", area("again"))                                                     # same plan: words kept for this visit
        self.assertNotIn("Mine", area("hcall"))                                                              # different plan: a fresh call

    def test_share_card_preview_matches_the_text_and_is_optional(self):
        r = self.call({"name": "show", "act": "sharetoggle"}, {"name": "copy", "act": "copycard"}, {"name": "hide", "act": "sharetoggle"})
        v = self.view
        k = core(("callCard", v["cards"][0], v))[0]
        h = htmllib.unescape(r["show"]["html"])
        self.assertIn(k["title"], h)
        self.assertIn(k["proj"], h)
        self.assertIn("Would you back it?", h)
        self.assertIn("Preview only. Sharing is optional and nothing is sent from here.", h)
        self.assertEqual(r["copy"]["copied"], k["text"])
        self.assertIn("Copied", r["copy"]["html"])
        self.assertNotIn("Would you back it?", htmllib.unescape(r["hide"]["html"]))
        self.assertEqual(r["copy"]["opened"], [])                                                          # copying never opens or sends anything

    def test_hold_can_be_backed_without_a_gate_and_has_no_numbers(self):
        r = smoke(planResponse=self.ok(self.base), steps=self.head() + [{"name": "pick", "act": "pickplan", "v": "hold"}, {"name": "inspect", "act": "inspect"},
                  {"name": "back", "act": "backplan"}, {"name": "show", "act": "sharetoggle"}])
        h = r["back"]["html"]
        self.assertIn("my call.", h)
        self.assertIn("You keep all 15 players.", h)
        self.assertNotIn("Net gain vs hold", h)
        self.assertIn("Holding this week.", htmllib.unescape(r["show"]["html"]))
        self.assertEqual(r["show"]["opened"], [])

    def test_sign_in_is_a_stub_that_saves_nothing(self):
        r = self.call({"name": "type", "why": "secret reason"}, {"name": "open", "act": "locksheet"}, {"name": "go", "act": "signinstub"},
                      {"name": "again", "act": "locksheet"}, {"name": "esc", "key": "Escape"})
        h = htmllib.unescape(r["open"]["html"])
        self.assertIn("Sign-in isn’t available yet. Nothing on this page saves or sends anything", h)
        self.assertLess(h.index("Continue with Google"), h.index("Email me a sign-in link"))                    # Google first
        self.assertRegex(h, r'<input type="checkbox" checked>')                                              # email-my-review is on by default
        self.assertIn("Email me my GW7 review once the points are in", h)
        self.assertNotIn("sheetx", r["go"]["html"])
        self.assertIn("Sign-in isn’t live yet, so nothing was saved. Your call stays in this tab only.", htmllib.unescape(r["go"]["html"]))
        self.assertIn("sheetx", r["again"]["html"])
        self.assertNotIn("sheetx", r["esc"]["html"])
        for step in ("type", "open", "go", "again", "esc"):
            dump = r[step]["storageAll"]
            self.assertNotIn("secret reason", dump)                                                           # nothing the user typed is stored
            self.assertNotIn("squadcheck.planner.call", dump)
        keys = set(json.loads(r["go"]["storageAll"]))
        self.assertTrue(keys <= {"squadcheck.planner.handoff.v1", "squadcheck.planner.state.v1", "squadcheck.planner.unlocked.v1"}, keys)

    def test_the_call_follows_the_working_plan_not_the_original(self):
        keep = self.ok(real_plan(free_transfers=1, strategy="everything", keep_ids=[self.out_id])[1])
        r = smoke(planResponse=self.ok(self.base), planRules=[{"has": ["keep_ids"], "response": keep}], unlocked=True, steps=self.to_compare() + [
            {"name": "o", "act": "twk", "v": "keep"}, {"name": "run", "act": "twkrun"}, {"name": "use", "act": "twkuse"}, {"name": "back", "act": "backplan"}])
        after = cards(keep["json"])
        pick = next((c for c in after["cards"] if c["kind"] == "rank"), after["cards"][-1])["model"]["title"]
        self.assertNotEqual(pick, self.m["title"]) if pick != self.m["title"] else None
        self.assertIn("You\u2019re backing %s." % pick, htmllib.unescape(r["back"]["html"]))
        self.assertIn("Edited:", htmllib.unescape(r["use"]["html"]))

    def test_the_stepper_marks_your_call_live_and_review_inert(self):
        s = self.call()["back"]["stepper"]
        self.assertEqual((s.count("stp done"), s.count("stp on")), (3, 1))
        self.assertIn("Your call", s.split("stp on")[1][:100])
        self.assertEqual(s.count('aria-disabled="true"'), 1)

    def test_nothing_on_the_page_uses_persistent_storage_beyond_the_session(self):
        self.assertNotIn("localStorage", TestStaticContract.CODE)
        self.assertNotIn("indexedDB", TestStaticContract.CODE)
        self.assertNotIn("document.cookie", TestStaticContract.CODE)
        for claim in ("Call saved", "saved to your account", "Saved to your account"):
            self.assertNotIn(claim, HTML)


class TestEditedPlanInExplore(TestTweaksInThePage):
    def edited(self, *extra, **kw):
        return self.go({"name": "o", "act": "twk", "v": "keep"}, {"name": "run", "act": "twkrun"}, {"name": "use", "act": "twkuse"}, {"name": "ex", "act": "toexplore"}, *extra, **kw)

    def test_the_edited_plan_is_shown_apart_from_the_original_ranking(self):
        r = self.edited()
        h = htmllib.unescape(r["ex"]["html"])
        self.assertIn("Your edited plan", h)
        self.assertIn("Edited: kept a player. Not part of the original ranking.", h)
        self.assertIn("so it is not in the ranked list below, and the original ranking is unchanged", h)
        self.assertLess(h.index("Your edited plan"), h.index("Original ranked plans"))
        edited_card = re.search(r'<div class="plan[^"]*"[^>]*data-v="work".*?</div></div>', r["ex"]["html"], re.S).group(0)
        self.assertNotIn("Ranked #", edited_card)

    def test_the_original_ranking_is_unchanged_and_the_edited_plan_is_selected(self):
        before = smoke(planResponse=self.ok(self.base), planRules=self.rules, unlocked=True, steps=self.to_compare() + [{"name": "x", "act": "toexplore"}])["x"]["html"]
        r = self.edited()
        ids = lambda h: re.findall(r'data-act="pickplan" data-v="(\w+)"', h)
        self.assertEqual([i for i in ids(r["ex"]["html"]) if i != "work"], ids(before))
        self.assertEqual(ids(r["ex"]["html"])[0], "work")
        self.assertEqual(r["ex"]["html"].count('aria-pressed="true"'), 1)
        self.assertRegex(r["ex"]["html"], r'data-act="pickplan" data-v="work"[^>]*>|aria-pressed="true" data-act="pickplan" data-v="work"')
        self.assertIn("Continue with my edited plan", r["ex"]["html"])

    def test_choosing_an_original_plan_warns_that_it_replaces_the_edited_one(self):
        r = self.edited({"name": "pick", "act": "pickplan", "v": self.top["id"]}, {"name": "work", "act": "pickplan", "v": "work"})
        self.assertIn("Inspecting an original plan replaces your edited plan.", r["pick"]["html"])
        self.assertNotIn("Inspecting an original plan replaces", r["work"]["html"])
        self.assertNotIn("Inspecting an original plan replaces", r["ex"]["html"])

    def test_continuing_with_the_edited_plan_does_not_reset_it(self):
        r = self.edited({"name": "go", "act": "inspect"})
        self.assertIn("Tweak it and see what changes", r["go"]["html"])                                       # it really opened Compare
        self.assertIn("What goes into this suggestion", r["go"]["html"])
        self.assertIn("Edited: kept a player", r["go"]["html"])
        self.assertEqual(len(self.posts(r)), 2)                                                              # no new request, no reset

    def test_inspecting_an_original_plan_replaces_the_edited_plan_only_when_asked(self):
        r = self.edited({"name": "pick", "act": "pickplan", "v": self.top["id"]}, {"name": "go", "act": "inspect"}, {"name": "back", "act": "toexplore"})
        self.assertNotIn("Edited:", r["go"]["html"])
        self.assertIn(self.m["title"], r["go"]["html"])
        self.assertNotIn("Your edited plan", r["back"]["html"])

    def test_the_stepper_returns_to_compare_with_the_edit_intact(self):
        r = self.edited({"name": "step", "act": "step", "v": 3})
        self.assertIn("Edited: kept a player", r["step"]["html"])

    def test_new_game_plan_settings_clear_the_edited_plan(self):
        r = self.edited({"name": "edit", "act": "edit"}, {"name": "bank", "bank": "0.6"}, {"name": "next", "act": "next"})
        self.assertNotIn("Your edited plan", r["next"]["html"])
        self.assertIn("Your options for GW7", r["next"]["html"])

    def test_resetting_removes_the_edited_block(self):
        r = self.go({"name": "o", "act": "twk", "v": "keep"}, {"name": "run", "act": "twkrun"}, {"name": "use", "act": "twkuse"},
                    {"name": "reset", "act": "twkreset"}, {"name": "ex", "act": "toexplore"})
        self.assertNotIn("Your edited plan", r["ex"]["html"])

    def test_keep_is_explained_as_a_recalculation(self):
        r = self.go({"name": "o", "act": "twk", "v": "keep"}, {"name": "run", "act": "twkrun"})
        self.assertIn("may be a different transfer plan or Hold, not simply this plan minus one move", htmllib.unescape(r["o"]["html"]))
        self.assertIn("This is the planner’s best plan with that player kept. It can differ from the plan you were looking at.", htmllib.unescape(r["run"]["html"]))
        self.assertIn("Allow one more points hit", r["o"]["html"])                                           # label unchanged


class TestStaticContract(unittest.TestCase):
    BODY = re.sub(r"<!--.*?-->", "", HTML, flags=re.S)
    CODE = re.sub(r"/\*.*?\*/|(?<!:)//[^\n]*", "", BODY, flags=re.S)

    def test_only_the_two_planner_endpoints_are_requested(self):
        fetches = re.findall(r"fetch\(([^)]*)\)", self.CODE)
        self.assertEqual(sorted(re.search(r"'(/api/[a-z-]+)'", f).group(1) for f in fetches), ["/api/players", "/api/transfer-plan"])

    def test_no_external_scripts_and_not_indexable(self):
        self.assertEqual(re.findall(r"<script[^>]+src=", self.BODY), [])
        self.assertIn('<meta name="robots" content="noindex,nofollow">', HTML)

    def test_handover_key_and_contract_are_documented(self):
        self.assertIn("squadcheck.planner.handoff.v1", HTML)
        self.assertEqual(HTML.count("squadcheck.planner.handoff.v1"), 2)           # contract comment and the core constant
        self.assertIn("SQUAD HANDOVER CONTRACT", HTML)

    def test_no_ceiling_or_upside_claims_in_visible_copy(self):
        visible = re.sub(r"<script.*?</script>", lambda m: "".join(re.findall(r"'([^'\n]{12,})'", m.group(0))), self.BODY, flags=re.S)
        for word in ("upside", "ceiling"):
            self.assertNotIn(word, visible.lower())

    def test_the_default_gain_is_never_called_a_three_gameweek_gain(self):
        for phrase in ("3 GW", "3-GW", "three-gameweek gain", "next 3", "next three"):
            self.assertNotIn(phrase, self.BODY)
        mentions = [m.start() for m in re.finditer("three gameweeks", self.BODY)]
        self.assertEqual(len(mentions), 1)                                          # only the outlook sentence
        self.assertIn("outlook", self.BODY[mentions[0] - 120:mentions[0]].lower())

    def test_results_page_hands_over_through_the_documented_key_only(self):
        index = (ROOT / "fpl" / "index.html").read_text(encoding="utf-8")
        self.assertIn("const PLANNER_HANDOFF_KEY = 'squadcheck.planner.handoff.v1';", index)
        self.assertIn("location.href = '/fpl/planner.html'", index)
        self.assertEqual(index.count("squadcheck.planner."), 1)                                   # nothing else of the planner's storage is touched
        self.assertNotRegex(index, r"""href=["'][^"']*planner""")                                 # no plain link that would skip the handover


if __name__ == "__main__":
    unittest.main()
