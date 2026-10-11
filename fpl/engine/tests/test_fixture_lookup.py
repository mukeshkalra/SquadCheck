"""
The fixtures lookup/cache wrappers in api/squad-check.py and fpl/server.py.

Both modules keep a module-level cache and take their clock from `time`; the tests swap in a
fake clock and a fake `_fetch`, so no network is used. Each module is exercised through the
same cases via a small adapter that hides the (differently named) cache keys and functions.
"""
import copy
import importlib.util
import unittest
from pathlib import Path

from fpl.engine.projection import compute_xpts
from fpl.engine.tests.test_projection import FIXTURE_ARS_HOME_LEE, GABRIEL, GW6_PARAMS

ROOT = Path(__file__).resolve().parents[3]


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


API = load("squad_check_api_under_test", "api/squad-check.py")
SERVER = load("fpl_server_under_test", "fpl/server.py")

FIXTURES = [{"id": 101, "team_h": 1, "team_a": 8, "finished": True, "finished_provisional": True},
            {"id": 104, "team_h": 1, "team_a": 12, "finished": False, "finished_provisional": False}]
BOOTSTRAP = {"elements": [{"id": 1, "element_type": 1, "team": 1, "starts": 5, "minutes": 450,
                           "expected_goals_conceded_per_90": 1.0, "expected_goals_per_90": 0.0,
                           "expected_assists_per_90": 0.0}]}


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def time(self):
        return self.now


class Fetcher:
    """Replaces `_fetch`: returns scripted responses (or raises them) and counts calls."""
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), 0

    def __call__(self, url):
        self.calls += 1
        item = self.responses[min(self.calls, len(self.responses)) - 1]
        if isinstance(item, Exception):
            raise item
        return item


class Adapter:
    """Uniform access to the two modules' wrappers."""
    def __init__(self, module, fixtures_fn, params_fn, ttl, retry):
        self.m, self.ttl, self.retry = module, ttl, retry
        self.fixtures, self.params = getattr(module, fixtures_fn), getattr(module, params_fn)


ADAPTERS = {
    "api": lambda: Adapter(API, "_fixtures", "_params", API._CACHE_TTL, API._FX_RETRY),
    "server": lambda: Adapter(SERVER, "get_fixtures", "get_params", SERVER._PARAMS_TTL, 60),
}


class FixtureLookupCases:
    """Mixin: the same behaviour is required of both caller files."""
    which = None

    def setUp(self):
        self.a = ADAPTERS[self.which]()
        self.saved_cache = copy.deepcopy(self.a.m._cache)
        self.saved_time, self.saved_fetch = self.a.m.time, self.a.m._fetch
        self.clock = Clock()
        self.a.m.time = self.clock
        for key, value in self.a.m._cache.items():                 # start every case from a cold cache
            self.a.m._cache[key] = {} if isinstance(value, dict) else (None if value is None or not isinstance(value, (int, float)) else 0)

    def tearDown(self):
        self.a.m.time, self.a.m._fetch = self.saved_time, self.saved_fetch
        self.a.m._cache.clear()
        self.a.m._cache.update(self.saved_cache)

    def use(self, *responses):
        self.fetch = Fetcher(*responses)
        self.a.m._fetch = self.fetch
        return self.fetch

    # ── fixtures wrapper ────────────────────────────────────────────────────────
    def test_successful_fetch_is_returned_and_reused_from_the_cache(self):
        self.use(FIXTURES)
        self.assertEqual(self.a.fixtures(), FIXTURES)
        self.clock.now += self.a.ttl - 1
        self.assertEqual(self.a.fixtures(), FIXTURES)
        self.assertEqual(self.fetch.calls, 1)

    def test_cache_is_refreshed_after_the_ttl(self):
        self.use(FIXTURES, FIXTURES[:1])
        self.a.fixtures()
        self.clock.now += self.a.ttl + 1
        self.assertEqual(self.a.fixtures(), FIXTURES[:1])
        self.assertEqual(self.fetch.calls, 2)

    def test_failed_fetch_returns_empty_and_is_not_retried_straight_away(self):
        self.use(OSError("boom"), FIXTURES)
        self.assertEqual(self.a.fixtures(), [])
        self.clock.now += self.a.retry - 1
        self.assertEqual(self.a.fixtures(), [])
        self.assertEqual(self.fetch.calls, 1)

    def test_failed_fetch_is_retried_after_the_interval(self):
        self.use(OSError("boom"), FIXTURES)
        self.a.fixtures()
        self.clock.now += self.a.retry + 1
        self.assertEqual(self.a.fixtures(), FIXTURES)
        self.assertEqual(self.fetch.calls, 2)

    def test_malformed_payloads_are_treated_as_failures(self):
        for bad in ({"detail": "rate limited"}, "not json", None, 42):
            self.setUp()
            self.use(bad, FIXTURES)
            self.assertEqual(self.a.fixtures(), [], repr(bad))
            self.assertIsNone(self.a.m._cache["fixtures"], repr(bad))      # a bad payload is never cached
            self.clock.now += self.a.retry + 1
            self.assertEqual(self.a.fixtures(), FIXTURES, repr(bad))

    def test_empty_list_is_accepted_but_yields_no_lookup(self):
        self.use([])
        self.assertEqual(self.a.fixtures(), [])
        self.assertEqual(self.a.params(BOOTSTRAP)["fixture_teams"], {})

    # ── params wrapper ──────────────────────────────────────────────────────────
    def test_params_carry_the_lookup_and_status_and_are_cached(self):
        self.use(FIXTURES)
        first = self.a.params(BOOTSTRAP)
        self.assertEqual(first["fixture_teams"], {101: (1, 8), 104: (1, 12)})
        self.assertEqual(first["fixture_completed"], {101: True, 104: False})
        self.assertIs(self.a.params(BOOTSTRAP), first)
        self.assertEqual(self.fetch.calls, 1)

    def test_params_are_not_cached_while_the_lookup_is_unavailable(self):
        self.use(OSError("boom"), FIXTURES)
        degraded = self.a.params(BOOTSTRAP)
        self.assertEqual(degraded["fixture_teams"], {})
        self.assertIsNone(self.a.m._cache["params"])
        self.clock.now += self.a.retry + 1
        recovered = self.a.params(BOOTSTRAP)
        self.assertEqual(recovered["fixture_teams"], {101: (1, 8), 104: (1, 12)})

    def test_unplayed_rows_are_still_excluded_when_the_lookup_fails(self):
        self.use(OSError("boom"))
        params = {**GW6_PARAMS, **{k: v for k, v in self.a.params(BOOTSTRAP).items()
                                   if k in ("fixture_teams", "fixture_completed")}}
        self.assertEqual(params["fixture_teams"], {})
        played = [{"fixture": 1, "minutes": 90, "starts": 1, "team_h_score": 1, "team_a_score": 0},
                  {"fixture": 2, "minutes": 90, "starts": 1, "team_h_score": 2, "team_a_score": 1}]
        unplayed = {"fixture": 3, "minutes": 0, "starts": 0, "team_h_score": None, "team_a_score": None}
        r = compute_xpts(GABRIEL, played + [unplayed], FIXTURE_ARS_HOME_LEE, params)
        self.assertEqual(r["inputs"]["xMins"], 90.0)
        self.assertAlmostEqual(r["inputs"]["w"], 2 / 4, places=6)


class TestApiFixtureLookup(FixtureLookupCases, unittest.TestCase):
    which = "api"


class TestServerFixtureLookup(FixtureLookupCases, unittest.TestCase):
    which = "server"


if __name__ == "__main__":
    unittest.main(verbosity=2)
