"""
SquadCheck transfer-planning data adapter and request handler.

Connects the pure planner (`transfer_planner.plan_transfers`) to live FPL data:

    squad ids (from the scanner flow) -> validated request -> FPL data source
    -> bounded candidates + element-summaries -> projections -> plan_transfers -> response

Nothing here changes scanner or projection behaviour, and the planner never touches the network:
all I/O lives in `FplDataSource`, whose fetch function, clock and limits are injectable.

Entry points
------------
handle(body: bytes, source) -> (http_status, payload)     used by api/transfer-plan.py and fpl/server.py
parse_request(raw: dict) -> dict                          explicit input validation
build_response(request, source) -> dict                   the orchestration

Data failures are explicit: a summary that could not be fetched (failed, timed out or malformed) is
passed on as *unavailable*, never as a zero projection or a blank gameweek. If nothing usable could
be fetched the request fails with 502 instead of guessing.
"""

import concurrent.futures
import gzip
import json
import logging
import math
import threading
import time
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

from fpl.engine import head_to_head
from fpl.engine.projection import build_params
from fpl.engine.transfer_planner import (
    MAX_FREE_TRANSFERS, plan_transfers, project_players, shortlist_candidates,
)

_BS_URL  = "https://fantasy.premierleague.com/api/bootstrap-static/"
_SUM_URL = "https://fantasy.premierleague.com/api/element-summary/{}/"
_FX_URL  = "https://fantasy.premierleague.com/api/fixtures/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SquadCheck/1.0; +https://squadcheck.club)"}

CANDIDATES_PER_POSITION = 12     # bootstrap pre-filter size per position (named players are added on top)
SUMMARY_BUDGET_S = 12.0          # wall-clock allowance for all candidate summaries in one request
MAX_BODY_BYTES = 64 * 1024
MAX_NAMED = 5
ALLOWED_KEYS = {"squad_ids", "bank_m", "free_transfers", "moves", "out_ids", "in_ids", "max_hits", "keep_ids",
                "strategy"}
# Optional "strategy" narrows what the planner may do; it never changes how plans are ranked (net gain).
#   sensible    no points hits: the request must not allow any (max_hits absent or 0)
#   everything  no narrowing: exactly the behaviour of omitting the field
# "upside" is deliberately not accepted yet: the planner has no ceiling/upside metric (projections are
# expected values), so there is nothing genuine to rank by. See parse_request.
STRATEGIES = ("sensible", "everything")


log = logging.getLogger("squadcheck.transfer")

# Hard upper bound on element-summary requests one API call can trigger:
# 15 squad + MAX_NAMED named incoming + (3 ranking keys x CANDIDATES_PER_POSITION // 2) for each of 4 positions.
MAX_SUMMARIES_PER_REQUEST = 15 + 5 + 4 * 3 * (CANDIDATES_PER_POSITION // 2)   # players; calls <= x SUMMARY_ATTEMPTS
SUMMARY_ATTEMPT_TIMEOUT_S = 4.0  # per attempt; connect stalls under parallel load were taking the full 15 s default
SUMMARY_ATTEMPTS = 2             # one retry, only for network-level (OSError) failures
BOOTSTRAP_DEADLINE_S = 20.0      # hard total-time limit for the bootstrap fetch (urllib's own timeout is per socket read)
FIXTURES_DEADLINE_S = 5.0        # fixtures are optional: past this the request proceeds without them
REQUEST_BUDGET_S = 25.0          # target wall-clock for one request; summaries get what is left (see build_response)
MIN_SUMMARY_BUDGET_S = 2.0


class ApiError(Exception):
    """An error with an HTTP status and a stable machine-readable code."""

    def __init__(self, http_status: int, code: str, message: str, details: Optional[list] = None):
        super().__init__(message)
        self.http_status, self.code, self.message, self.details = http_status, code, message, details or []


def _http_fetch(url: str, timeout: float = 15):
    """GET + parse JSON. Asks for gzip: the FPL bootstrap is ~1.8 MB plain but ~0.17 MB gzipped, which
    was the difference between a 1-3 s and a 30+ s download on a slow link."""
    req = urllib.request.Request(url, headers={**_HEADERS, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        return json.loads(raw)


# ─────────────────────────────────────────────────────────────────────────────
# DATA SOURCE (all network I/O and caching)
# ─────────────────────────────────────────────────────────────────────────────

class FplDataSource:
    """
    bootstrap / fixtures / params / element-summaries with small TTL caches.

    fetch  : url -> parsed JSON (raises on failure); injectable for tests.
    clock  : () -> seconds; injectable for tests.
    A failed fixtures fetch gives params without fixtures (the existing, documented fallback) and is
    retried after `fixtures_retry`; `fixtures_ok` records whether the last params used fixtures.
    Summaries are cached per player for `summary_ttl`; only valid summaries are cached.
    """

    def __init__(self, fetch: Optional[Callable] = None, clock: Callable = time.time, ttl: float = 300,
                 fixtures_retry: float = 60, summary_ttl: float = 300, max_workers: int = 15,
                 bootstrap_deadline: float = BOOTSTRAP_DEADLINE_S, fixtures_deadline: float = FIXTURES_DEADLINE_S):
        self._fetch = fetch or _http_fetch
        # summaries use a short per-attempt timeout (the injected fetch, if any, is used as given)
        self._summary_fetch = fetch or (lambda url: _http_fetch(url, SUMMARY_ATTEMPT_TIMEOUT_S))
        self._clock = clock
        self._ttl, self._fx_retry, self._sum_ttl, self._workers = ttl, fixtures_retry, summary_ttl, max_workers
        self._lock = threading.RLock()
        self._bs_deadline, self._fx_deadline = bootstrap_deadline, fixtures_deadline
        self._aux: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._misc: Dict[str, concurrent.futures.Future] = {}      # in-flight bootstrap / fixtures fetches
        self._pool: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._inflight: Dict[int, concurrent.futures.Future] = {}
        self._bootstrap, self._bs_ts = None, 0.0
        self._fixtures, self._fx_ts, self._fx_fail_ts = None, 0.0, None
        self._params, self._params_ts = None, 0.0
        self._summaries: Dict[int, tuple] = {}
        self.fixtures_ok = False

    # -- shared single-flight fetch with a hard deadline -------------------------
    def _shared_fetch(self, key: str, url: str, store: Callable, deadline: float):
        """One in-flight fetch per `key` across all concurrent requests. Each caller waits at most
        `deadline` seconds in total (raises concurrent.futures.TimeoutError); the fetch itself keeps
        running and, if it succeeds later, `store` caches it for the next request."""
        with self._lock:
            fut = self._misc.get(key)
            if fut is None:
                if self._aux is None:
                    self._aux = concurrent.futures.ThreadPoolExecutor(max_workers=2)
                fut = self._aux.submit(self._fetch, url)
                self._misc[key] = fut
                fut.add_done_callback(lambda f, key=key: self._misc_done(key, f, store))
        return fut.result(timeout=deadline)

    def _misc_done(self, key: str, fut, store: Callable) -> None:
        with self._lock:
            if self._misc.get(key) is fut:
                del self._misc[key]
        try:
            store(fut.result())
        except Exception as exc:
            log.warning("%s fetch did not produce usable data: %r", key, exc)

    # -- bootstrap ------------------------------------------------------------
    def _store_bootstrap(self, data) -> None:
        if not (isinstance(data, dict) and isinstance(data.get("elements"), list) and data["elements"]
                and isinstance(data.get("events"), list) and data["events"]):
            raise ValueError("bootstrap payload is missing elements or events")
        with self._lock:
            if self._bootstrap is data:
                return
            self._bootstrap, self._bs_ts = data, self._clock()
            self._params = None                    # derived from bootstrap

    def bootstrap(self) -> dict:
        with self._lock:
            if self._bootstrap is not None and self._clock() - self._bs_ts <= self._ttl:
                return self._bootstrap
        try:
            data = self._shared_fetch("bootstrap", _BS_URL, self._store_bootstrap, self._bs_deadline)
            self._store_bootstrap(data)
        except concurrent.futures.TimeoutError:
            log.warning("bootstrap fetch exceeded %.0fs", self._bs_deadline)
            raise ApiError(502, "UPSTREAM_ERROR", "FPL bootstrap data did not arrive in time")
        except ValueError as exc:
            log.warning("bootstrap payload malformed: %s", exc)
            raise ApiError(502, "UPSTREAM_ERROR", "FPL bootstrap data is incomplete")
        except Exception as exc:
            log.warning("bootstrap fetch failed: %r", exc)
            raise ApiError(502, "UPSTREAM_ERROR", "Could not fetch FPL bootstrap data")
        with self._lock:
            return self._bootstrap

    # -- params (fixtures optional) ---------------------------------------------
    def _store_fixtures(self, data) -> None:
        if not isinstance(data, list):
            raise ValueError("unexpected fixtures payload")
        with self._lock:
            if self._fixtures is data:
                return
            self._fixtures, self._fx_ts, self._fx_fail_ts = data, self._clock(), None

    def _fixtures_list(self) -> list:
        now = self._clock()
        with self._lock:
            if self._fixtures is not None and now - self._fx_ts <= self._ttl:
                return self._fixtures
            if self._fx_fail_ts is not None and now - self._fx_fail_ts < self._fx_retry:
                return []
        try:
            data = self._shared_fetch("fixtures", _FX_URL, self._store_fixtures, self._fx_deadline)
            self._store_fixtures(data)
        except Exception as exc:                  # optional data: proceed without it, retry after a while
            log.warning("fixtures unavailable (%r); retrying in %.0fs", exc, self._fx_retry)
            with self._lock:
                self._fx_fail_ts = now
            return []
        return data

    def params(self, bootstrap: dict) -> dict:
        now = self._clock()
        with self._lock:
            if self._params is not None and now - self._params_ts <= self._ttl:
                return self._params
        fixtures = self._fixtures_list()
        params = build_params(bootstrap, fixtures)
        self.fixtures_ok = bool(fixtures)
        if fixtures:                        # not cached without fixtures, so the next request retries
            with self._lock:
                self._params, self._params_ts = params, now
        return params

    # -- element summaries --------------------------------------------------------
    @staticmethod
    def _valid_summary(s) -> bool:
        return isinstance(s, dict) and isinstance(s.get("history"), list) and isinstance(s.get("fixtures"), list)

    def _submit(self, pid: int) -> concurrent.futures.Future:
        """One in-flight fetch per player across all concurrent requests (caller holds the lock).
        The shared pool also bounds concurrent upstream summary calls per instance to `max_workers`."""
        fut = self._inflight.get(pid)
        if fut is None:
            if self._pool is None:
                self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=self._workers)
            fut = self._pool.submit(self._fetch_summary, _SUM_URL.format(pid))
            self._inflight[pid] = fut
            fut.add_done_callback(lambda f, pid=pid: self._finished(pid, f))
        return fut

    def _fetch_summary(self, url: str):
        """Short timeout, then one retry on a network-level failure (not on bad JSON). At most
        SUMMARY_ATTEMPTS upstream calls per player per fetch."""
        for attempt in range(SUMMARY_ATTEMPTS):
            try:
                return self._summary_fetch(url)
            except OSError:
                if attempt == SUMMARY_ATTEMPTS - 1:
                    raise

    def _finished(self, pid: int, fut: concurrent.futures.Future) -> None:
        """Runs when a fetch completes, even if the request that wanted it has already given up, so a
        late result is cached for the next request instead of being thrown away."""
        with self._lock:
            if self._inflight.get(pid) is fut:
                del self._inflight[pid]
            try:
                data = fut.result()
            except Exception as exc:
                log.warning("summary %s fetch failed: %r", pid, exc)
                return
            if self._valid_summary(data):
                self._summaries[pid] = (self._clock(), data)

    def summaries(self, ids, budget_s: float = SUMMARY_BUDGET_S) -> Tuple[dict, dict]:
        """
        Cache first; the rest through shared single-flight fetches, waiting at most `budget_s`.
        Returns ({id: summary} for valid ones only, stats). Failed, timed-out and malformed ids are
        listed in stats and absent from the result; they are never replaced by an empty summary.
        Fetches that outlive the budget keep running and populate the cache for the next request.
        """
        ids = sorted(set(ids))
        now = self._clock()
        out, hits, futures = {}, 0, {}
        with self._lock:
            for pid in ids:
                cached = self._summaries.get(pid)
                if cached is not None and now - cached[0] <= self._sum_ttl:
                    out[pid] = cached[1]
                    hits += 1
                else:
                    futures[self._submit(pid)] = pid
        stats = {"requested": len(ids), "cache_hits": hits, "fetched": 0,
                 "failed": [], "timed_out": [], "malformed": []}
        if futures:
            done, pending = concurrent.futures.wait(list(futures), timeout=budget_s)
            for f in done:
                pid = futures[f]
                try:
                    data = f.result()
                except Exception:
                    stats["failed"].append(pid)
                    continue
                if not self._valid_summary(data):
                    stats["malformed"].append(pid)
                    continue
                out[pid] = data
                stats["fetched"] += 1
            stats["timed_out"] = sorted(futures[f] for f in pending)
            stats["failed"].sort()
            stats["malformed"].sort()
        return out, stats


# ─────────────────────────────────────────────────────────────────────────────
# REQUEST VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _int_list(v, label, errors) -> List[int]:
    if not isinstance(v, list) or any(not _is_int(x) for x in v):
        errors.append("%s must be a list of integer player ids" % label)
        return []
    return list(v)


def parse_request(raw) -> dict:
    """Validate the JSON body; every problem is reported together. Nothing is defaulted silently
    except the optional fields (moves/out_ids/in_ids empty, max_hits 0)."""
    if not isinstance(raw, dict):
        raise ApiError(400, "BAD_REQUEST", "Request body must be a JSON object")
    errors: List[str] = []
    unknown = sorted(set(raw) - ALLOWED_KEYS)
    if unknown:
        errors.append("unknown field(s): %s" % ", ".join(map(str, unknown)))
    for key in ("squad_ids", "bank_m", "free_transfers"):
        if key not in raw:
            errors.append("%s is required" % key)

    squad = _int_list(raw["squad_ids"], "squad_ids", errors) if "squad_ids" in raw else []
    if squad:
        if len(squad) != 15:
            errors.append("squad_ids must contain exactly 15 players; got %d" % len(squad))
        dupes = sorted({p for p in squad if squad.count(p) > 1})
        if dupes:
            errors.append("squad_ids contains duplicate ids: %s" % dupes)

    bank = None
    if "bank_m" in raw:
        b = raw["bank_m"]
        if isinstance(b, bool) or not isinstance(b, (int, float)) or not math.isfinite(b):
            errors.append("bank_m must be a number (£m, e.g. 0.5)")
        elif b < 0 or b > 100:
            errors.append("bank_m must be between 0 and 100")
        else:
            bank = int(round(b * 10))
            if abs(b * 10 - bank) > 1e-6:
                errors.append("bank_m must be a multiple of 0.1")
                bank = None

    free = raw.get("free_transfers")
    if "free_transfers" in raw and (not _is_int(free) or free < 0 or free > MAX_FREE_TRANSFERS):
        errors.append("free_transfers must be an integer from 0 to %d" % MAX_FREE_TRANSFERS)
        free = None

    max_hits = raw.get("max_hits", 0)
    if not _is_int(max_hits) or max_hits < 0 or max_hits > MAX_NAMED:
        errors.append("max_hits must be an integer from 0 to %d" % MAX_NAMED)
        max_hits = 0

    strategy = None
    if "strategy" in raw:
        s = raw["strategy"]
        if s == "upside":
            errors.append('strategy "upside" is not available: the planner has no ceiling or upside metric '
                          '(projections are expected values). Use "everything" with max_hits to include hits')
        elif not isinstance(s, str) or s not in STRATEGIES:
            errors.append("strategy must be one of: %s" % ", ".join(STRATEGIES))
        else:
            strategy = s
            if s == "sensible" and max_hits > 0:
                errors.append('strategy "sensible" means no points hits; remove max_hits or use "everything"')

    moves: List[Tuple[int, int]] = []
    if "moves" in raw:
        m = raw["moves"]
        if not isinstance(m, list) or any(not (isinstance(p, list) and len(p) == 2 and all(_is_int(x) for x in p))
                                          for p in m):
            errors.append("moves must be a list of [out_id, in_id] pairs")
        else:
            moves = [(p[0], p[1]) for p in m]
    out_ids = _int_list(raw["out_ids"], "out_ids", errors) if "out_ids" in raw else []
    in_ids = _int_list(raw["in_ids"], "in_ids", errors) if "in_ids" in raw else []

    keep_ids = _int_list(raw["keep_ids"], "keep_ids", errors) if "keep_ids" in raw else []

    if moves and (out_ids or in_ids):
        errors.append("use either moves or out_ids / in_ids, not both")
    if out_ids and in_ids:
        errors.append("out_ids and in_ids together do not say who replaces whom; use moves for explicit pairs")
    if max(len(moves), len(out_ids), len(in_ids)) > MAX_NAMED:
        errors.append("at most %d named transfers are supported" % MAX_NAMED)
    named_out = [m[0] for m in moves] + out_ids
    named_in = [m[1] for m in moves] + in_ids
    if len(set(named_out)) != len(named_out) or len(set(named_in)) != len(named_in):
        errors.append("named outgoing / incoming ids must be distinct")
    if len(set(keep_ids)) != len(keep_ids):
        errors.append("keep_ids must be distinct")
    if squad:
        for p in keep_ids:
            if p not in squad:
                errors.append("kept player %s is not in squad_ids" % p)
    clash = sorted(set(keep_ids) & set(named_out))
    if clash:
        errors.append("player(s) %s cannot be both sold and kept" % clash)
    if squad:
        for p in named_out:
            if p not in squad:
                errors.append("outgoing player %s is not in squad_ids" % p)
        for p in named_in:
            if p in squad:
                errors.append("incoming player %s is already in squad_ids" % p)

    if errors:
        raise ApiError(400, "BAD_REQUEST", "Invalid request", errors)
    intent = "moves" if moves else "out_only" if out_ids else "in_only" if in_ids else None
    return {"squad_ids": squad, "bank": bank, "free_transfers": free, "moves": moves, "out_ids": out_ids,
            "in_ids": in_ids, "max_hits": max_hits, "keep_ids": keep_ids, "strategy": strategy,
            "intent": intent}


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATION
# ─────────────────────────────────────────────────────────────────────────────

_REQUIRED_ELEMENT_FIELDS = ("element_type", "team", "now_cost")


def _usable(element) -> bool:
    return (isinstance(element, dict) and element.get("element_type") in (1, 2, 3, 4)
            and _is_int(element.get("team")) and _is_int(element.get("now_cost")) and element["now_cost"] > 0)


def _next_event(bootstrap) -> Optional[int]:
    events = bootstrap.get("events", [])
    for e in events:
        if e.get("is_next"):
            return e.get("id")
    for e in events:
        if not e.get("finished"):
            return e.get("id")
    return None


def _team_lookup(bootstrap) -> dict:
    """{team id: (name, short_name)} from bootstrap; empty if the teams list is absent or malformed."""
    out = {}
    teams = bootstrap.get("teams")
    if isinstance(teams, list):
        for t in teams:
            if isinstance(t, dict) and _is_int(t.get("id")):
                name, short = t.get("name"), t.get("short_name")
                out[t["id"]] = (name if isinstance(name, str) and name else None,
                                short if isinstance(short, str) and short else None)
    return out


def _player_meta(pid, team_id, by_id: dict, teams: dict) -> dict:
    """Presentation metadata for one player, from FPL bootstrap only. Missing or malformed values are
    None; nothing is guessed. The planner never sees these fields."""
    el = by_id.get(pid) or {}
    code = el.get("code")
    name, short = teams.get(team_id, (None, None))
    return {"photo_code": code if _is_int(code) and code > 0 else None,
            "team_name": name, "team_short": short}


def _attach_player_meta(obj, by_id: dict, teams: dict) -> None:
    """Add photo_code / team_name / team_short, in place, to every player dict in a planner result
    (a player dict is one with id, web_name, position and team)."""
    if isinstance(obj, dict):
        if {"id", "web_name", "position", "team"} <= obj.keys():
            obj.update(_player_meta(obj["id"], obj["team"], by_id, teams))
        for v in obj.values():
            _attach_player_meta(v, by_id, teams)
    elif isinstance(obj, list):
        for v in obj:
            _attach_player_meta(v, by_id, teams)


def build_response(request: dict, source: FplDataSource, summary_budget_s: Optional[float] = None) -> dict:
    ms = lambda a, b: round((b - a) * 1000, 1)
    t0 = time.perf_counter()
    bootstrap = source.bootstrap()
    by_id = {e.get("id"): e for e in bootstrap["elements"] if isinstance(e, dict)}
    params = source.params(bootstrap)
    t1 = time.perf_counter()

    squad = request["squad_ids"]
    named = set(request["in_ids"]) | {m[1] for m in request["moves"]}
    unknown = sorted(p for p in set(squad) | named | set(request["out_ids"]) if p not in by_id)
    if unknown:
        raise ApiError(400, "BAD_REQUEST", "Unknown player id(s): %s" % unknown, [str(p) for p in unknown])
    gameweek = _next_event(bootstrap)
    if gameweek is None:
        raise ApiError(409, "NO_UPCOMING_GAMEWEEK", "FPL data shows no upcoming gameweek to plan for")
    broken = sorted(p for p in set(squad) | named if not _usable(by_id[p]))
    if broken:
        raise ApiError(502, "UPSTREAM_ERROR",
                       "FPL data is missing required fields (position, club, price) for player(s): %s" % broken)

    candidates = [p for p in shortlist_candidates(bootstrap, squad, forced_ids=sorted(named),
                                                  per_position=CANDIDATES_PER_POSITION)
                  if p in set(squad) | named or _usable(by_id.get(p))]
    t2 = time.perf_counter()

    if summary_budget_s is None:      # whatever is left of the request budget, within [MIN, SUMMARY_BUDGET_S]
        summary_budget_s = max(MIN_SUMMARY_BUDGET_S, min(SUMMARY_BUDGET_S, REQUEST_BUDGET_S - (t2 - t0)))
    summaries, fetch = source.summaries(candidates, summary_budget_s)
    t3 = time.perf_counter()
    if not summaries:
        log.warning("no summaries usable: failed=%d timed_out=%d malformed=%d", len(fetch["failed"]),
                    len(fetch["timed_out"]), len(fetch["malformed"]))
        raise ApiError(502, "UPSTREAM_ERROR", "No player projection data could be fetched from FPL")
    players = project_players(bootstrap, summaries, params, candidates)
    t4 = time.perf_counter()

    try:
        plan = plan_transfers(
            squad, players, bank=request["bank"], free_transfers=request["free_transfers"],
            moves=request["moves"], out_ids=request["out_ids"], in_ids=request["in_ids"],
            max_hits=request["max_hits"], keep_ids=request["keep_ids"])
    except ValueError as exc:
        raise ApiError(422, "INVALID_PLAN_INPUT", str(exc))
    teams = _team_lookup(bootstrap)
    _attach_player_meta(plan, by_id, teams)
    try:        # presentation only: a failure here must never take the plan down
        head_to_head.attach(plan, by_id, summaries, players, params, teams, gameweek)
    except Exception:
        log.exception("head-to-head comparison failed; plan returned without it")
    t5 = time.perf_counter()

    unavailable = sorted(set(fetch["failed"]) | set(fetch["timed_out"]) | set(fetch["malformed"]))
    reasons = []
    if unavailable:
        reasons.append("%d player projection(s) unavailable (failed %d, timed out %d, malformed %d)"
                       % (len(unavailable), len(fetch["failed"]), len(fetch["timed_out"]), len(fetch["malformed"])))
    if not source.fixtures_ok:
        reasons.append("fixture list unavailable: late-arrival and unplayed history rows are not filtered")
    degraded = bool(reasons)
    return {
        "status": "DEGRADED" if degraded else "OK",
        "gameweek": gameweek,
        "request": {"squad_ids": squad, "bank_m": request["bank"] / 10.0, "bank": request["bank"],
                    "free_transfers": request["free_transfers"], "max_hits": request["max_hits"],
                    "intent": request["intent"], "moves": [list(m) for m in request["moves"]],
                    "out_ids": request["out_ids"], "in_ids": request["in_ids"],
                    "keep_ids": request["keep_ids"], "strategy": request["strategy"]},
        "squad": [{"id": p, "web_name": by_id[p].get("web_name", ""), "position": by_id[p]["element_type"],
                   "team": by_id[p]["team"], "price": by_id[p]["now_cost"],
                   **_player_meta(p, by_id[p]["team"], by_id, teams)} for p in squad],
        "plan": plan,
        "data": {
            "candidates_considered": len(candidates),
            "named_players_retained": sorted(named),
            "summaries": {**fetch, "unavailable_ids": unavailable},
            "degraded": degraded,
            "completeness": "degraded" if degraded else "complete",
            "degraded_reasons": reasons,
            "retry_suggested": bool(fetch["failed"] or fetch["timed_out"]),
            "fixtures_available": source.fixtures_ok,
            "price_source": "now_cost (estimate; selling price may differ)",
        },
        "timing_ms": {"bootstrap_and_params": ms(t0, t1), "shortlist": ms(t1, t2),
                      "summary_fetch": ms(t2, t3), "projection": ms(t3, t4), "planning": ms(t4, t5),
                      "total": ms(t0, t5)},
    }


def error_payload(err: ApiError) -> dict:
    return {"status": err.code, "error": err.message, "details": err.details}


def handle(body: bytes, source: FplDataSource) -> Tuple[int, dict]:
    """Parse, validate and plan. Returns (http_status, json-serialisable payload); never raises."""
    try:
        if len(body) > MAX_BODY_BYTES:
            raise ApiError(413, "BAD_REQUEST", "Request body too large")
        try:
            raw = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "BAD_REQUEST", "Request body is not valid JSON")
        return 200, build_response(parse_request(raw), source)
    except ApiError as err:
        return err.http_status, error_payload(err)
    except Exception:                             # unexpected: log the detail, return nothing internal
        log.exception("transfer-plan failed unexpectedly")
        return 500, {"status": "ERROR", "error": "Internal error", "details": []}


# ─────────────────────────────────────────────────────────────────────────────
# PLAYER DIRECTORY  (GET /api/players)
# ─────────────────────────────────────────────────────────────────────────────

# Cache for browsers and the Vercel CDN, matching the source's 300 s bootstrap TTL. Only sent on success.
PLAYERS_CACHE_CONTROL = "public, max-age=60, s-maxage=300, stale-while-revalidate=600"


def build_players_response(source: FplDataSource) -> dict:
    """Every usable FPL player for name search: id, name, club, position, price, photo code.

    Reads the same cached bootstrap as /api/transfer-plan (no extra upstream request, no second
    cache). Entries without a usable id, name, position, club or price are skipped and counted; a
    missing photo code is reported as null, never guessed. Prices are tenths of £m, like every other
    transfer-plan response."""
    bootstrap = source.bootstrap()
    teams = _team_lookup(bootstrap)
    by_id: Dict[int, dict] = {}
    skipped = 0
    for e in bootstrap["elements"]:
        name = e.get("web_name") if isinstance(e, dict) else None
        if (not _usable(e) or not _is_int(e.get("id")) or not isinstance(name, str) or not name.strip()
                or e["id"] in by_id):
            skipped += 1
            continue
        by_id[e["id"]] = e
    if not by_id:
        raise ApiError(502, "UPSTREAM_ERROR", "FPL player data is incomplete")
    players = []
    for pid in sorted(by_id):
        e = by_id[pid]
        meta = _player_meta(pid, e["team"], by_id, teams)
        players.append({"id": pid, "name": e["web_name"].strip(), "team": e["team"],
                        "team_name": meta["team_name"], "team_short": meta["team_short"],
                        "position": e["element_type"], "price": e["now_cost"],
                        "photo_code": meta["photo_code"]})
    return {"status": "OK", "gameweek": _next_event(bootstrap), "count": len(players), "skipped": skipped,
            "price_unit": "tenths of £m (now_cost)", "players": players}


def handle_players(source: FplDataSource) -> Tuple[int, dict]:
    """Returns (http_status, json-serialisable payload); never raises and never exposes internals."""
    try:
        return 200, build_players_response(source)
    except ApiError as err:
        return err.http_status, error_payload(err)
    except Exception:
        log.exception("players directory failed unexpectedly")
        return 500, {"status": "ERROR", "error": "Internal error", "details": []}
