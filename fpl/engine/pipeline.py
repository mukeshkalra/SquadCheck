"""
SquadCheck FPL Pipeline V1

Orchestrates the full squad-check flow:

  scanner → name resolution → projection (V0.2) → bench optimiser

Entry point
-----------
run_pipeline(scan_result, bootstrap, element_summaries, params, threshold=0.5) -> dict

Caller responsibilities
-----------------------
- Provide a scan_result from scanner.scan_squad().
- Provide the bootstrap-static API response (used for name resolution).
- Provide element_summaries: {player_id: {"history": [...], "fixtures": [...]}}
  fetched from /api/element-summary/{id}/ for each resolved player.
- Provide params built with projection.build_params(bootstrap).

The pipeline does not make any HTTP calls.
The pipeline does not modify xPts values produced by compute_xpts().
The pipeline does not manufacture transfers, captaincy picks, or chip advice.

V1 payload shape
----------------
{
    "status":          str,    OK | SCAN_FAIL | RESOLVE_FAIL | ERROR
    "scanner_status":  str,    raw scanner status
    "view_type":       str,
    "players":         list,   all 15 players with projection output
    "submitted_xi":    dict,   {player_ids, formation, xPts, players}
    "recommended_xi":  dict,   same shape as submitted_xi
    "delta":           float,  recommended − submitted xPts
    "action":          str,    HOLD | SWAP
    "substitutions":   list,   [{out: player_summary, in: player_summary}]
    "threshold":       float,
    "message":         str,
}

HOLD is a valid successful result.
"""

import re as _re
import unicodedata as _ud

from .scanner  import VALID as _SCAN_VALID
from .projection import compute_xpts
from .bench    import optimise_bench

# Pipeline-level status codes (distinct from scanner status codes)
OK            = "OK"
SCAN_FAIL     = "SCAN_FAIL"
RESOLVE_FAIL  = "RESOLVE_FAIL"
ERROR         = "ERROR"


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(
    scan_result:       dict,
    bootstrap:         dict,
    element_summaries: dict,
    params:            dict,
    threshold:         float = 0.5,
) -> dict:
    """
    Run the full V1 pipeline.

    Parameters
    ----------
    scan_result       : dict from scanner.scan_squad().
    bootstrap         : FPL /api/bootstrap-static/ response dict.
    element_summaries : {player_id: {"history": list, "fixtures": list}}.
                        history  — per-GW records from element-summary.
                        fixtures — upcoming-fixture records; [0] is used.
    params            : dict from projection.build_params(bootstrap).
    threshold         : minimum xPts delta to recommend a bench change (default 0.5).

    Returns
    -------
    V1 payload dict.  status == OK on success; SCAN_FAIL / RESOLVE_FAIL on
    early exit; ERROR on unexpected exception.
    """
    try:
        return _run(scan_result, bootstrap, element_summaries, params, threshold)
    except Exception as exc:
        return _fail(ERROR, scan_result.get("status", "UNKNOWN"),
                     "Unexpected pipeline error: %s" % exc)


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL ORCHESTRATION
# ─────────────────────────────────────────────────────────────────────────────

def _run(scan_result, bootstrap, element_summaries, params, threshold):

    # ── Gate 1: scanner must be VALID ────────────────────────────────────────
    if scan_result.get("status") != _SCAN_VALID:
        return _fail(
            SCAN_FAIL,
            scan_result.get("status", "UNKNOWN"),
            "Scanner status is not VALID: %s" % scan_result.get("message", ""),
        )

    scan_players = scan_result.get("players", [])
    if len(scan_players) != 15:
        return _fail(SCAN_FAIL, _SCAN_VALID,
                     "Expected 15 validated players from scanner, got %d" % len(scan_players))

    # ── Gate 2: resolve all 15 player names → bootstrap element IDs ──────────
    elements = bootstrap.get("elements", [])
    resolved, unresolved = resolve_players(scan_players, elements)

    if unresolved:
        names = ", ".join(r["name"] for r in unresolved)
        return _fail(RESOLVE_FAIL, _SCAN_VALID,
                     "Could not resolve %d player(s): %s" % (len(unresolved), names))

    # ── Gate 3: all 15 resolved IDs must be unique ────────────────────────────
    ids = [r["player_id"] for r in resolved]
    if len(set(ids)) != 15:
        dupes = [i for i in ids if ids.count(i) > 1]
        return _fail(RESOLVE_FAIL, _SCAN_VALID,
                     "Resolved duplicate player IDs: %s" % list(set(dupes)))

    # ── Step: compute projections for all 15 players ─────────────────────────
    elem_by_id = {e["id"]: e for e in elements}
    projections = []
    for r in resolved:
        pid     = r["player_id"]
        player  = elem_by_id[pid]
        summary = element_summaries.get(pid, {})
        history = summary.get("history", [])
        fixtures = summary.get("fixtures", [])
        fixture = fixtures[0] if fixtures else None   # next GW fixture or blank

        proj = compute_xpts(player, history, fixture, params)
        # Tag with the scanner-derived starting status for payload display
        proj["is_starting"] = r["is_starting"]
        projections.append(proj)

    # ── Gate 4: submitted XI must have exactly 11 players ────────────────────
    submitted_xi = [r["player_id"] for r in resolved if r["is_starting"]]
    if len(submitted_xi) != 11:
        return _fail(RESOLVE_FAIL, _SCAN_VALID,
                     "Expected 11 starters, found %d" % len(submitted_xi))

    # ── Step: bench optimiser ─────────────────────────────────────────────────
    bench_result = optimise_bench(projections, submitted_xi, threshold=threshold)

    # ── Derive team name lookup and gameweek ──────────────────────────────────
    team_by_id = {t["id"]: {"name": t.get("name",""), "short_name": t.get("short_name","")}
                  for t in bootstrap.get("teams", [])}

    gameweek = None
    for r in resolved:
        fx_list = element_summaries.get(r["player_id"], {}).get("fixtures", [])
        if fx_list and "event" in fx_list[0]:
            gameweek = fx_list[0]["event"]
            break

    # ── Captain / vice — injected by server when known, else None ────────────
    captain_id      = scan_result.get("captain_id")
    vice_captain_id = scan_result.get("vice_captain_id")

    # ── Assemble V1 payload ───────────────────────────────────────────────────
    return _build_payload(scan_result, projections, bench_result, threshold,
                          elem_by_id, team_by_id, gameweek,
                          captain_id, vice_captain_id)


# ─────────────────────────────────────────────────────────────────────────────
# BOOTSTRAP VALIDATION  (pre-pipeline filter)
# ─────────────────────────────────────────────────────────────────────────────

def _norm_name(name: str) -> str:
    """
    Normalise a name for fuzzy matching against the FPL bootstrap.

    Steps: NFKD decompose → drop combining marks → lowercase → keep a-z only.

    Examples:
      'Gibbs - White' → 'gibbswhite'   (OCR spaced hyphen)
      'Muñoz'         → 'munoz'         (accent stripped)
      'João Pedro'    → 'joaopedro'     (ã → a, space dropped)
      'N.Williams'    → 'nwilliams'     (dot dropped)
      'O\'Shea'       → 'oshea'         (apostrophe dropped)
      'Ødegaard'      → 'degaard'       (Ø has no ASCII equivalent)
    """
    nfkd = _ud.normalize("NFKD", name)
    no_marks = "".join(c for c in nfkd if _ud.category(c) != "Mn")
    return _re.sub(r"[^a-z]", "", no_marks.lower())


def bootstrap_filter(players: list, elements: list) -> tuple:
    """
    Remove players whose names cannot be matched to any active FPL player.

    Uses position-free, normalised name matching so OCR variants resolve:
      'Gibbs - White' matches 'Gibbs-White' in the bootstrap.
      'Knox', 'Vitality', 'IllCMC' don't match anything → rejected.

    Returns (clean, rejected) lists.  Only call this when scanner returned
    VALID — the intent is to strip residual noise, not to validate format.
    """
    known: set[str] = set()
    for e in elements:
        if e.get("removed", False):
            continue
        for field in ("web_name", "second_name", "known_name"):
            v = (e.get(field) or "").strip()
            if len(v) >= 3:
                known.add(_norm_name(v))

    clean, rejected = [], []
    for p in players:
        (clean if _norm_name(p["name"]) in known else rejected).append(p)
    return clean, rejected


# ─────────────────────────────────────────────────────────────────────────────
# NAME RESOLUTION
# ─────────────────────────────────────────────────────────────────────────────

def resolve_players(scan_players: list, elements: list) -> tuple:
    """
    Match scanner player records (name + position) to bootstrap element IDs.

    Strategy (tried in order, stops at first unambiguous match):
      1. Exact match on web_name (case-insensitive)
      2. Exact match on second_name (case-insensitive)
      3. Exact match on known_name (case-insensitive, if non-empty)
      4. Exact match on "first_name second_name" full name

    Position must match element_type.  Removed players are skipped.

    Returns (resolved, unresolved) where each entry is a dict
    {name, position, is_starting, player_id}.  player_id is None in
    unresolved entries.
    """
    resolved   = []
    unresolved = []

    for sp in scan_players:
        pid = _find_id(sp["name"], sp["position"], elements)
        entry = {
            "name":        sp["name"],
            "position":    sp["position"],
            "is_starting": sp["is_starting"],
            "player_id":   pid,
        }
        (resolved if pid is not None else unresolved).append(entry)

    return resolved, unresolved


def _find_id(name: str, position: int, elements: list):
    """Return a single matching player_id or None if zero / ambiguous."""
    q = name.lower().strip()
    hits = []
    for e in elements:
        if e.get("element_type") != position:
            continue
        if e.get("removed", False):
            continue
        web   = (e.get("web_name",    "") or "").lower().strip()
        sec   = (e.get("second_name", "") or "").lower().strip()
        known = (e.get("known_name",  "") or "").lower().strip()
        first = (e.get("first_name",  "") or "").lower().strip()
        full  = ("%s %s" % (first, sec)).strip()
        if q in (web, sec, known, full):
            hits.append(e["id"])

    if not hits:
        # Normalised fallback: handles OCR variants like 'Gibbs - White' → 'Gibbs-White'
        qn = _norm_name(q)
        for e in elements:
            if e.get("element_type") != position or e.get("removed", False):
                continue
            web  = (e.get("web_name",    "") or "")
            sec  = (e.get("second_name", "") or "")
            knwn = (e.get("known_name",  "") or "")
            fst  = (e.get("first_name",  "") or "")
            full = ("%s %s" % (fst, sec)).strip()
            if qn in (_norm_name(web), _norm_name(sec), _norm_name(knwn), _norm_name(full)):
                hits.append(e["id"])

    return hits[0] if len(hits) == 1 else None


# ─────────────────────────────────────────────────────────────────────────────
# PAYLOAD ASSEMBLY
# ─────────────────────────────────────────────────────────────────────────────

def _build_payload(scan_result, projections, bench_result, threshold,
                   elem_by_id, team_by_id=None, gameweek=None,
                   captain_id=None, vice_captain_id=None):
    action = bench_result["action"]
    delta  = bench_result["delta"]

    subs = bench_result["substitutions"]
    if action == "SWAP" and subs:
        swap_desc = "; ".join(
            "%s out → %s in" % (s["out"]["web_name"], s["in"]["web_name"])
            for s in subs
        )
        message = "Swap recommended (+%.1f xPts): %s" % (delta, swap_desc)
    elif action == "SWAP":
        message = "Swap recommended (+%.1f xPts)" % delta
    elif delta > 0:
        message = ("Hold — best available gain (%.1f xPts) is below the %.1f threshold"
                   % (delta, threshold))
    else:
        message = "Hold — starting XI is optimal for this Gameweek"

    tb = team_by_id or {}
    players_out = []
    for p in projections:
        pid        = p["player_id"]
        elem       = elem_by_id.get(pid, {})
        team_info  = tb.get(elem.get("team"), {})
        opp_id     = p["inputs"].get("opponent_id")
        opp_info   = tb.get(opp_id, {}) if opp_id else {}

        # Extend inputs with resolved opponent name (non-destructive copy)
        inputs_out = dict(p["inputs"])
        inputs_out["opponent_name"] = opp_info.get("short_name", "")

        players_out.append({
            "player_id":         pid,
            "web_name":          p["web_name"],
            "position":          p["position"],
            "team_id":           p["team_id"],
            "is_starting":       p.get("is_starting", False),
            "xPts":              p["xPts"],
            "confidence":        p["confidence"],
            "_minutes_conf":     p["_minutes_conf"],
            "_projection_conf":  p["_projection_conf"],
            "components":        p["components"],
            "inputs":            inputs_out,
            "drivers":           p["drivers"],
            "risks":             p["risks"],
            "player_api": {
                "photo_code":     elem.get("code"),   # p{code}.png for player photo URL
                "xG_p90":         elem.get("expected_goals_per_90"),
                "xA_p90":         elem.get("expected_assists_per_90"),
                "starts":         elem.get("starts"),
                "minutes":        elem.get("minutes"),
                "status":         elem.get("status"),
                "cop_next":       elem.get("chance_of_playing_next_round"),
                "team_name":      team_info.get("short_name", ""),
                "team_name_full": team_info.get("name", ""),
            },
        })

    return {
        "status":          OK,
        "scanner_status":  scan_result.get("status"),
        "view_type":       scan_result.get("view_type", "UNKNOWN"),
        # ── data-contract V1 additions ──────────────────────────────────────
        "gameweek":        gameweek,          # int | None
        "captain_id":      captain_id,        # injected by server when identifiable
        "vice_captain_id": vice_captain_id,   # injected by server when identifiable
        "captain_note":    ("xPts are per-player projections. "
                            "Captain 2x multiplier is NOT applied. "
                            "Actual team score will be higher."),
        # ────────────────────────────────────────────────────────────────────
        "players":         players_out,
        "submitted_xi":    bench_result["submitted_xi"],
        "recommended_xi":  bench_result["recommended_xi"],
        "delta":           delta,
        "action":          action,
        "substitutions":   subs,
        "threshold":       threshold,
        "message":         message,
    }


def _fail(status, scanner_status, message):
    return {
        "status":         status,
        "scanner_status": scanner_status,
        "view_type":      "UNKNOWN",
        "players":        [],
        "submitted_xi":   None,
        "recommended_xi": None,
        "delta":          0.0,
        "action":         None,
        "substitutions":  [],
        "threshold":      None,
        "message":        message,
    }
