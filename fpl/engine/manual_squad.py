"""
Manual bench / captain step for screenshots whose XI and bench could not be read.

When the scanner finds a legal 15-player squad but cannot tell who starts (for example the FPL
Transfers screen, which shows all 15 on the pitch), the page asks the user for their bench and
captain. This module is the server side of that step. It never touches the scanner: it only
reads a scan result and builds a VALID-shaped one from the user's answers, which then runs through
the unchanged resolve / projection / bench pipeline.
"""
from typing import List, Optional

from fpl.engine.pipeline import resolve_squad_smart, _norm_name

SQUAD_SHAPE = {1: 2, 2: 5, 3: 5, 4: 3}      # GKP / DEF / MID / FWD in a 15-man FPL squad
_MAX_NAME = 60


class ManualSquadError(ValueError):
    """The user's answers cannot form a legal squad; the message is safe to show."""


def _resolve(names: List[str], bench: set, elements: list):
    players = [{"name": n, "is_starting": _norm_name(n) not in bench} for n in names]
    return resolve_squad_smart(players, elements)


def _shape_ok(resolved: list) -> bool:
    counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for r in resolved:
        counts[r["position"]] = counts.get(r["position"], 0) + 1
    return counts == SQUAD_SHAPE


def roster_offer(scan_result: dict, bootstrap: dict) -> Optional[list]:
    """
    The 15 players to show in the manual step, or None when the step should not be offered.
    Offered only when the scan holds exactly 15 distinct names that all resolve to one FPL player
    each and form a legal squad shape (2 GKP, 5 DEF, 5 MID, 3 FWD).
    """
    players = scan_result.get("players") or []
    names, seen = [], set()
    for p in players:
        n = (p.get("name") or "").strip()
        if n and _norm_name(n) not in seen:
            seen.add(_norm_name(n))
            names.append(n)
    if len(names) != 15 or any(len(n) > _MAX_NAME for n in names):
        return None
    elements = bootstrap.get("elements", [])
    try:
        resolved, ambiguous, unresolved = _resolve(names, set(), elements)
    except Exception:
        return None
    if ambiguous or unresolved or len(resolved) != 15 or not _shape_ok(resolved):
        return None
    ids = [r["player_id"] for r in resolved]
    if len(set(ids)) != 15:
        return None
    by_id = {e["id"]: e for e in elements}
    teams = {t["id"]: t.get("short_name", "") for t in bootstrap.get("teams", [])}
    return [{"name": r["name"], "position": r["position"],
             "team": teams.get((by_id.get(r["player_id"]) or {}).get("team"), "")} for r in resolved]


def manual_scan_result(req, bootstrap: dict) -> dict:
    """
    Build a VALID-shaped scan result from the user's answers.
    req = {"names": [15 names], "bench": [4 names], "captain": name|None, "vice": name|None}
    Raises ManualSquadError for anything that is not a legal squad, XI, bench or captain choice.
    """
    if not isinstance(req, dict):
        raise ManualSquadError("The squad details were not understood.")
    names, bench = req.get("names"), req.get("bench")
    cap, vice = req.get("captain"), req.get("vice")
    ok_str = lambda s: isinstance(s, str) and 0 < len(s.strip()) <= _MAX_NAME
    if not (isinstance(names, list) and len(names) == 15 and all(ok_str(n) for n in names)):
        raise ManualSquadError("Expected 15 player names.")
    if len({_norm_name(n) for n in names}) != 15:
        raise ManualSquadError("The 15 players must all be different.")
    if not (isinstance(bench, list) and len(bench) == 4 and all(ok_str(n) for n in bench)):
        raise ManualSquadError("Choose exactly 4 bench players.")
    norm = {_norm_name(n) for n in names}
    bench_n = {_norm_name(n) for n in bench}
    if len(bench_n) != 4 or not bench_n <= norm:
        raise ManualSquadError("The bench must be 4 different players from your squad.")
    for label, v in (("captain", cap), ("vice-captain", vice)):
        if v is not None and not ok_str(v):
            raise ManualSquadError("The %s was not understood." % label)
    if vice is not None and cap is None:
        raise ManualSquadError("Choose a captain before a vice-captain.")
    if cap is not None and _norm_name(cap) in bench_n:
        raise ManualSquadError("The captain must be a starter.")
    if cap is not None and vice is not None and _norm_name(cap) == _norm_name(vice):
        raise ManualSquadError("The captain and vice-captain must be different players.")
    if vice is not None and _norm_name(vice) in bench_n:
        raise ManualSquadError("The vice-captain must be a starter.")
    for v in (cap, vice):
        if v is not None and _norm_name(v) not in norm:
            raise ManualSquadError("The captain and vice-captain must be in your squad.")

    resolved, ambiguous, unresolved = _resolve([n.strip() for n in names], bench_n, bootstrap.get("elements", []))
    if ambiguous or unresolved or len(resolved) != 15 or not _shape_ok(resolved):
        raise ManualSquadError("We couldn't match those 15 players to a legal FPL squad.")
    starters = [r for r in resolved if r["is_starting"]]
    pos = lambda p: sum(1 for r in starters if r["position"] == p)
    if pos(1) != 1:
        raise ManualSquadError("The bench must have exactly one goalkeeper.")
    if pos(2) < 3 or pos(3) < 2 or pos(4) < 1:
        raise ManualSquadError("That bench leaves an illegal starting formation (need at least 3 DEF, 2 MID, 1 FWD).")
    return {
        "status": "VALID", "view_type": "MANUAL", "message": "",
        "players": [{"name": r["name"], "position": r["position"], "is_starting": r["is_starting"]} for r in resolved],
        "captain_name": cap.strip() if cap is not None else None,
        "vice_captain_name": vice.strip() if vice is not None else None,
    }
