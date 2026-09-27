"""
SquadCheck FPL Bench Optimiser v0.1

Finds the highest-xPts legal XI obtainable by bench/start substitutions.
Works on pre-computed compute_xpts() result dicts; never re-runs the
projection model.

Entry point
-----------
optimise_bench(projections, submitted_xi, threshold=0.5) -> dict

Consumed fields from each projection dict (all produced by compute_xpts):
    player_id  : int
    position   : int   1=GKP  2=DEF  3=MID  4=FWD
    xPts       : float
    confidence : str   HIGH | MEDIUM | LOW   (passed through, never re-derived)
    web_name   : str

FPL formation rules enforced:
    Exactly 11 starters
    Exactly 1 GKP
    3–5 DEF
    2–5 MID
    1–3 FWD
"""

from itertools import combinations
from typing import List, Optional

# ── FPL FORMATION CONSTRAINTS ───────────────────────────────────────────────
_POS_MIN  = {1: 1, 2: 3, 3: 2, 4: 1}
_POS_MAX  = {1: 1, 2: 5, 3: 5, 4: 3}
_XI_SIZE  = 11
_SQ_SIZE  = 15


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _formation_str(player_ids, by_id: dict) -> str:
    """'4-4-2' style string from a list of player ids (GKP excluded)."""
    d = sum(1 for p in player_ids if by_id[p]["position"] == 2)
    m = sum(1 for p in player_ids if by_id[p]["position"] == 3)
    f = sum(1 for p in player_ids if by_id[p]["position"] == 4)
    return f"{d}-{m}-{f}"


def _pos_counts(player_ids, by_id: dict) -> dict:
    counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for p in player_ids:
        counts[by_id[p]["position"]] += 1
    return counts


def _is_valid_xi(player_ids, by_id: dict) -> bool:
    """True when player_ids forms a legal FPL XI (size + formation)."""
    if len(player_ids) != _XI_SIZE:
        return False
    counts = _pos_counts(player_ids, by_id)
    for pos in (1, 2, 3, 4):
        if counts[pos] < _POS_MIN[pos] or counts[pos] > _POS_MAX[pos]:
            return False
    return True


def _xi_xpts(player_ids, by_id: dict) -> float:
    return sum(by_id[p]["xPts"] for p in player_ids)


def _player_summary(pid: int, by_id: dict) -> dict:
    """Minimal player dict for output — passes confidence through unchanged."""
    p = by_id[pid]
    return {
        "player_id":  pid,
        "web_name":   p.get("web_name", ""),
        "position":   p["position"],
        "xPts":       p["xPts"],
        "confidence": p["confidence"],
    }


def _xi_summary(player_ids: list, by_id: dict) -> dict:
    return {
        "player_ids": list(player_ids),
        "formation":  _formation_str(player_ids, by_id),
        "xPts":       round(_xi_xpts(player_ids, by_id), 2),
        "players":    [_player_summary(p, by_id) for p in player_ids],
    }


def _enumerate_valid_xis(all_ids: list, by_id: dict):
    """
    Yield every legal 11-player subset of all_ids.

    Algorithm: fix one GKP, choose 10 from outfield, filter by formation.
    For a 2GKP/5DEF/5MID/3FWD squad this yields ≤550 valid XIs — fast enough
    for exhaustive search without any heuristic pruning.
    """
    gkps     = [p for p in all_ids if by_id[p]["position"] == 1]
    outfield = [p for p in all_ids if by_id[p]["position"] != 1]
    for gkp in gkps:
        for out10 in combinations(outfield, 10):
            xi = [gkp] + list(out10)
            # Formation check on outfield counts only; GKP constraint
            # is guaranteed by construction (exactly one GKP in xi).
            d = sum(1 for p in out10 if by_id[p]["position"] == 2)
            m = sum(1 for p in out10 if by_id[p]["position"] == 3)
            f = sum(1 for p in out10 if by_id[p]["position"] == 4)
            if d >= 3 and m >= 2 and f >= 1:
                yield xi


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def optimise_bench(
    projections:  List[dict],
    submitted_xi: List[int],
    threshold:    float = 0.5,
) -> dict:
    """
    Find the highest-xPts legal XI obtainable by bench/start swaps.

    Parameters
    ----------
    projections  : 15 compute_xpts() result dicts for the full squad.
    submitted_xi : 11 player_ids currently in the starting XI.
                   Must be a subset of the ids in projections.
    threshold    : Minimum xPts improvement to recommend a change (default 0.5).

    Returns
    -------
    dict
        action          "SWAP" | "HOLD"
        submitted_xi    {player_ids, formation, xPts, players}
        recommended_xi  {player_ids, formation, xPts, players}
                        Best found XI (equals submitted when no improvement exists).
        delta           float  recommended_xi.xPts − submitted_xi.xPts
                        Positive even under HOLD if a below-threshold gain exists.
        substitutions   list of {out: player_summary, in: player_summary}
                        Empty when action is HOLD and delta == 0.
                        Populated under HOLD when a below-threshold gain exists
                        (so the caller can see what is available).
        threshold       float  the threshold used

    Raises
    ------
    ValueError  wrong squad size, wrong XI size, unknown player ids,
                or submitted XI violates formation rules.
    """

    # ── VALIDATE ─────────────────────────────────────────────────────────────
    if len(projections) != _SQ_SIZE:
        raise ValueError(
            f"projections must contain {_SQ_SIZE} players; got {len(projections)}"
        )
    if len(submitted_xi) != _XI_SIZE:
        raise ValueError(
            f"submitted_xi must contain {_XI_SIZE} player ids; got {len(submitted_xi)}"
        )

    by_id: dict = {}
    for p in projections:
        pid = p["player_id"]
        if pid in by_id:
            raise ValueError(f"Duplicate player_id {pid} in projections")
        by_id[pid] = p

    unknown = [pid for pid in submitted_xi if pid not in by_id]
    if unknown:
        raise ValueError(
            f"submitted_xi contains player_ids not found in projections: {unknown}"
        )

    if not _is_valid_xi(submitted_xi, by_id):
        counts = _pos_counts(submitted_xi, by_id)
        raise ValueError(
            f"submitted_xi violates FPL formation constraints. "
            f"Position counts (1=GKP,2=DEF,3=MID,4=FWD): {dict(counts)}"
        )

    all_ids = list(by_id.keys())

    # ── SUBMITTED XI BASELINE ────────────────────────────────────────────────
    sub_xpts = _xi_xpts(submitted_xi, by_id)

    # ── SEARCH ───────────────────────────────────────────────────────────────
    best_xi   = list(submitted_xi)
    best_xpts = sub_xpts

    for xi in _enumerate_valid_xis(all_ids, by_id):
        xpts = _xi_xpts(xi, by_id)
        if xpts > best_xpts:
            best_xpts = xpts
            best_xi   = xi

    # ── BUILD RESULT ─────────────────────────────────────────────────────────
    delta  = round(best_xpts - sub_xpts, 2)
    action = "SWAP" if delta >= threshold else "HOLD"

    # Substitutions: symmetric difference between submitted and best XI.
    # Under HOLD with delta==0 the sets are identical and this list is empty.
    sub_set  = set(submitted_xi)
    best_set = set(best_xi)
    out_ids  = sorted(sub_set  - best_set, key=lambda p:  by_id[p]["xPts"])
    in_ids   = sorted(best_set - sub_set,  key=lambda p: -by_id[p]["xPts"])

    substitutions = [
        {"out": _player_summary(o, by_id), "in": _player_summary(i, by_id)}
        for o, i in zip(out_ids, in_ids)
    ]

    return {
        "action":         action,
        "submitted_xi":   _xi_summary(submitted_xi, by_id),
        "recommended_xi": _xi_summary(best_xi,      by_id),
        "delta":          delta,
        "substitutions":  substitutions,
        "threshold":      threshold,
    }
