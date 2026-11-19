"""
Chip advice adapter: POST /api/chips.

Request (JSON):  squad_ids (15 ids), bank_m (£m, e.g. 0.5), free_transfers (0..5),
                 chips_available (any of "wildcard", "freehit", "bboost", "3xc"; none ticked by default),
                 starter_ids (optional, the 11 the user starts).
Response:        the chips.advise() result plus gameweek, data-quality flags and timing.

It reuses the transfer planner's data source, candidate shortlist and projection wiring, so a chip
verdict and a transfer suggestion are always computed from the same numbers. Chip history is not
read from FPL: the user ticks the chips they still hold.
"""

import logging
import math
import time
from typing import Dict, List, Optional, Tuple

from . import chips
from .availability import chance_of_playing
from .transfer_adapter import (ApiError, CANDIDATES_PER_POSITION, FplDataSource, MAX_BODY_BYTES, MIN_SUMMARY_BUDGET_S,
                               REQUEST_BUDGET_S, SUMMARY_BUDGET_S, _int_list, _is_int, _next_event, _usable,
                               error_payload)
from .transfer_planner import (HORIZON, MAX_FREE_TRANSFERS, plan_transfers, project_players, shortlist_candidates)

import json

log = logging.getLogger("squadcheck.chips")

ALLOWED_KEYS = {"squad_ids", "bank_m", "free_transfers", "chips_available", "starter_ids"}
CHIP_KEYS = ("wildcard", "freehit", "bboost", "3xc")


def parse_request(raw) -> dict:
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
    ticked = raw.get("chips_available", [])
    if not isinstance(ticked, list) or any(not isinstance(c, str) or c not in CHIP_KEYS for c in ticked):
        errors.append("chips_available must be a list of: %s" % ", ".join(CHIP_KEYS))
        ticked = []
    starters: List[int] = []
    if "starter_ids" in raw:
        starters = _int_list(raw["starter_ids"], "starter_ids", errors)
        if starters and (len(starters) != 11 or len(set(starters)) != 11 or not set(starters) <= set(squad)):
            errors.append("starter_ids must be 11 distinct players from squad_ids")
    if errors:
        raise ApiError(400, "BAD_REQUEST", "; ".join(errors), errors)
    return {"squad_ids": squad, "bank": bank, "free_transfers": free, "chips_available": sorted(set(ticked)),
            "starter_ids": starters}


def windows_for(bootstrap: dict, gw: int) -> Dict[str, int]:
    """{chip: gameweeks left AFTER this one} for every chip whose API window contains gw."""
    out = {}
    for c in bootstrap.get("chips", []) or []:
        try:
            name, a, b = c["name"], int(c["start_event"]), int(c["stop_event"])
        except (KeyError, TypeError, ValueError):
            continue
        if name in CHIP_KEYS and a <= gw <= b:
            out[name] = b - gw
    return out


def windows_or_unknown(bootstrap: dict, gw: int, ticked) -> Tuple[Dict[str, Optional[int]], List[str]]:
    """Windows from the API chip list. If the list is missing or unusable, every ticked chip is treated as
    open with unknown expiry (no loosening, no USE_BY) and the response says so."""
    w = windows_for(bootstrap, gw)
    has_list = isinstance(bootstrap.get("chips"), list) and any(
        isinstance(c, dict) and c.get("name") in CHIP_KEYS for c in bootstrap["chips"])
    if has_list:
        return w, []
    return {c: None for c in ticked}, ["FPL's chip windows were unavailable, so expiry is not applied to the advice."]


def _enrich(info: Dict[int, dict], by_id: dict, summaries: dict, gw: int) -> None:
    """Fields the chip rules need beyond the planner's player info."""
    for pid, p in info.items():
        el = by_id.get(pid) or {}
        p["cop"] = el.get("chance_of_playing_next_round")
        p["chance"] = chance_of_playing(p.get("status"), p["cop"])
        s = summaries.get(pid) or {}
        fx = s.get("fixtures") or []
        counts = [0] * HORIZON
        diffs = []
        for f in sorted((f for f in fx if isinstance(f.get("event"), int) and f["event"] >= gw), key=lambda f: f["event"]):
            k = f["event"] - gw
            if 0 <= k < HORIZON:
                counts[k] += 1
            if len(diffs) < 5 and isinstance(f.get("difficulty"), (int, float)):
                diffs.append(f["difficulty"])
        p["fx_counts"] = counts if fx else [1] * HORIZON        # no fixture data: assume one game, never invent blanks
        p["fix_diff5"] = round(sum(diffs) / len(diffs), 2) if diffs else None
        hist = [h for h in (s.get("history") or []) if isinstance(h.get("minutes"), (int, float))]
        last4 = hist[-4:]
        p["min_last4"] = round(sum(h["minutes"] for h in last4) / len(last4), 1) if last4 else None


def build_response(request: dict, source: FplDataSource) -> dict:
    t0 = time.perf_counter()
    bootstrap = source.bootstrap()
    by_id = {e.get("id"): e for e in bootstrap["elements"] if isinstance(e, dict)}
    params = source.params(bootstrap)
    squad = request["squad_ids"]
    unknown = sorted(p for p in squad if p not in by_id)
    if unknown:
        raise ApiError(400, "BAD_REQUEST", "Unknown player id(s): %s" % unknown, [str(p) for p in unknown])
    gameweek = _next_event(bootstrap)
    if gameweek is None:
        raise ApiError(409, "NO_UPCOMING_GAMEWEEK", "FPL data shows no upcoming gameweek to advise on")
    broken = sorted(p for p in squad if not _usable(by_id[p]))
    if broken:
        raise ApiError(502, "UPSTREAM_ERROR", "FPL data is missing required fields for player(s): %s" % broken)

    candidates = [p for p in shortlist_candidates(bootstrap, squad, per_position=CANDIDATES_PER_POSITION)
                  if p in set(squad) or _usable(by_id.get(p))]
    budget = max(MIN_SUMMARY_BUDGET_S, min(SUMMARY_BUDGET_S, REQUEST_BUDGET_S - (time.perf_counter() - t0)))
    summaries, fetch = source.summaries(candidates, budget)
    if not summaries:
        raise ApiError(502, "UPSTREAM_ERROR", "No player projection data could be fetched from FPL")
    players = project_players(bootstrap, summaries, params, candidates)
    _enrich(players, by_id, summaries, gameweek)

    free, bank = request["free_transfers"], request["bank"]
    try:
        plan0 = plan_transfers(squad, players, bank, free, max_hits=0)
        plan3 = plan_transfers(squad, players, bank, free, max_hits=max(0, chips.FH_MAX_MOVES - free))
    except ValueError as exc:
        raise ApiError(422, "INVALID_PLAN_INPUT", str(exc))

    eligible = [p for p in players.values() if chips._available(p) and p["xpts"][0] is not None]
    league_best = max((p["xpts"][0] for p in eligible), default=0.0)

    windows, window_warn = windows_or_unknown(bootstrap, gameweek, request["chips_available"])
    inp = {"gameweek": gameweek, "squad_ids": squad, "players": players, "pool": list(players),
           "bank": bank, "free_transfers": free, "chips_available": request["chips_available"],
           "windows": windows, "plan_hit_free": plan0, "plan_3moves": plan3,
           "league_best_xpts": league_best, "starter_ids": request["starter_ids"]}
    try:
        advice = chips.advise(inp)
        advice["warnings"] = list(advice.get("warnings", [])) + window_warn
    except chips.ChipInputError as exc:
        raise ApiError(422, "INVALID_CHIP_INPUT", str(exc))

    unavailable = sorted(set(fetch["failed"]) | set(fetch["timed_out"]) | set(fetch["malformed"]))
    reasons = []
    if unavailable:
        reasons.append("%d player projection(s) unavailable" % len(unavailable))
    if not source.fixtures_ok:
        reasons.append("fixture list unavailable")
    advice.update({
        "status": "DEGRADED" if reasons else "OK",
        "request": {"squad_ids": squad, "bank_m": bank / 10.0, "free_transfers": free,
                    "chips_available": request["chips_available"]},
        "data": {"degraded": bool(reasons), "degraded_reasons": reasons, "candidates_considered": len(candidates),
                 "league_best_xpts": round(league_best, 2), "price_source": "now_cost (estimate)",
                 "free_hit_note": "The comparison team is searched over a shortlist of about 60 players, not every player."},
        "timing_ms": {"total": round((time.perf_counter() - t0) * 1000, 1)},
    })
    return advice


def handle(body: bytes, source: FplDataSource) -> Tuple[int, dict]:
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
    except Exception:
        log.exception("chips failed unexpectedly")
        return 500, {"status": "ERROR", "error": "Internal error", "details": []}
