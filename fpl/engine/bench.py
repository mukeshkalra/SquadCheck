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


def _pair_substitutions(out_ids: list, in_ids: list, by_id: dict) -> list:
    """
    Pair each player leaving the XI with one entering it. Same position pairs first (a goalkeeper is only ever
    swapped for a goalkeeper: an XI has exactly one), each position matched lowest-out with highest-in. What is
    left over is a formation change (for example a midfielder out, a defender in), marked with
    "same_position": False so it is never described as a like-for-like swap.
    """
    pairs, rest_out, rest_in = [], [], []
    for pos in (1, 2, 3, 4):
        outs = [p for p in out_ids if by_id[p]["position"] == pos]
        ins  = [p for p in in_ids  if by_id[p]["position"] == pos]
        n = min(len(outs), len(ins))
        pairs += [(o, i, True) for o, i in zip(outs[:n], ins[:n])]
        rest_out += outs[n:]
        rest_in  += ins[n:]
    rest_out.sort(key=lambda p: by_id[p]["xPts"])
    rest_in.sort(key=lambda p: -by_id[p]["xPts"])
    pairs += [(o, i, False) for o, i in zip(rest_out, rest_in)]
    return [{"out": _player_summary(o, by_id), "in": _player_summary(i, by_id), "same_position": same}
            for o, i, same in pairs]


def _xi_summary(player_ids: list, by_id: dict) -> dict:
    return {
        "player_ids": list(player_ids),
        "formation":  _formation_str(player_ids, by_id),
        "xPts":       round(_xi_xpts(player_ids, by_id), 2),
        "players":    [_player_summary(p, by_id) for p in player_ids],
    }


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC LEGAL-XI EVALUATION
# ─────────────────────────────────────────────────────────────────────────────

def _legal_formations():
    for d in range(_POS_MIN[2], _POS_MAX[2] + 1):
        for m in range(_POS_MIN[3], _POS_MAX[3] + 1):
            f = _XI_SIZE - 1 - d - m
            if _POS_MIN[4] <= f <= _POS_MAX[4]:
                yield d, m, f


# Every legal (DEF, MID, FWD) split behind the one goalkeeper, derived from the constraints above.
LEGAL_FORMATIONS = tuple(_legal_formations())


def best_legal_xi(player_ids, scores: dict, positions: dict):
    """
    Highest-scoring legal XI from `player_ids` (any squad size with at least one GKP).

    scores    : {player_id: float}   value of each player (any units; may be zero or negative)
    positions : {player_id: int}     1=GKP 2=DEF 3=MID 4=FWD

    Returns (total, xi_ids), or (None, []) when no legal XI exists. Exact: for each legal
    formation it takes the top-n players of each position. Ties break on lower player id, so
    the result is deterministic. Equivalent in total to the exhaustive search in optimise_bench
    (pinned by tests); optimise_bench itself is unchanged.
    """
    by_pos = {1: [], 2: [], 3: [], 4: []}
    for pid in player_ids:
        by_pos[positions[pid]].append(pid)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda p: (-scores[p], p))
    if not by_pos[1]:
        return None, []
    cum = {}
    for pos, ids in by_pos.items():
        c, run = [0.0], 0.0
        for pid in ids:
            run += scores[pid]
            c.append(run)
        cum[pos] = c
    best, best_f = None, None
    for d, m, f in LEGAL_FORMATIONS:
        if d > len(by_pos[2]) or m > len(by_pos[3]) or f > len(by_pos[4]):
            continue
        total = cum[1][1] + cum[2][d] + cum[3][m] + cum[4][f]
        if best is None or total > best + 1e-9:
            best, best_f = total, (d, m, f)
    if best is None:
        return None, []
    d, m, f = best_f
    return best, by_pos[1][:1] + by_pos[2][:d] + by_pos[3][:m] + by_pos[4][:f]


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

    substitutions = _pair_substitutions(out_ids, in_ids, by_id)

    return {
        "action":         action,
        "submitted_xi":   _xi_summary(submitted_xi, by_id),
        "recommended_xi": _xi_summary(best_xi,      by_id),
        "delta":          delta,
        "substitutions":  substitutions,
        "threshold":      threshold,
    }
