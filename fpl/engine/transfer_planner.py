"""
SquadCheck Transfer Planner v0.2

Pure, deterministic search over complete transfer plans for a 15-player squad.
Free transfers are a budget, not a quota: HOLD is always a candidate, and a
transfer is only recommended when it is projected to beat holding by `min_gain`.

Entry point
-----------
plan_transfers(squad_ids, players, bank, free_transfers, ...) -> dict

Player info (one dict per id in `players`), prices in tenths of £m (FPL now_cost units):
    position (1..4), team, price, xpts [GW+1, GW+2, GW+3] (None = projection unavailable),
    optional: id, web_name, confidence (HIGH|MEDIUM|LOW), status, risks, outlook_caveat

Three things are kept separate in the result:
    hold            the do-nothing baseline
    requested       the user's literal intent, evaluated on its own terms (never repaired)
    recommendation  the best overall plan, which may add transfers, use other players, or HOLD

User intent
-----------
    moves=[(out, in), ...]  exact transfers, pairing preserved. Evaluated literally.
    out_ids=[...]           outgoing fixed, replacements optimised; exactly len(out_ids) transfers.
    in_ids=[...]            incoming fixed, who goes out optimised; exactly len(in_ids) transfers.
`requested["with_additional_transfers"]` is a separate, labelled plan that contains the
named players plus extra transfers; it is never substituted for the literal plan.

Squad value = best legal XI from the 15 under the weighted xPts score, so the scanner's
starter/bench split is never used. All numbers are projections, not guarantees.

Gain definitions (over the weighted score above; the default weighting is next GW only)
    projected_gain_before_hits  score(plan) - score(hold): what the new squad is projected to add,
                                before any points hit is charged
    projected_gain_vs_hold      net_score(plan) - score(hold)
                                = projected_gain_before_hits - hit_points
                                (existing field; ranking and the recommendation use this one)
    outlook.vs_hold             per-GW best-XI difference against hold, also before hits (provisional)

keep_ids=[...]  squad players who must not be sold. A real search constraint, not a label: they are
                removed from every outgoing pool, moves / out_ids may not include them, and the
                result echoes them in inputs.keep_ids and search.coverage.squad_players_kept.
                They do not count as pruning, so optimality is reported for the constrained space.

Search scope: only the players supplied in `players`, pruned to bounded pools. The result's
`search.coverage` says what was and was not searched; `search.optimality` is only
"optimal_over_supplied_players" when nothing supplied was pruned.

Price source: `price` is whatever the caller supplies. v1 callers pass bootstrap now_cost for
both outgoing and incoming players, so affordability is ESTIMATED (selling price can differ).
"""

import math
from itertools import combinations, product
from math import comb
from typing import Dict, List, Optional, Sequence

from fpl.engine.bench import best_legal_xi

HORIZON = 3
DEFAULT_WEIGHTS = (1.0, 0.0, 0.0)   # [provisional] next GW only; outlook is reported separately
SQUAD_COMPOSITION = {1: 2, 2: 5, 3: 5, 4: 3}   # GKP/DEF/MID/FWD; a test pins this to pipeline._SQUAD_POS
MAX_PER_CLUB = 3                    # [verified 2026-10-11: official API game_settings.squad_team_limit]
HIT_COST = 4                        # [verified 2026-10-11: premierleague.com "FPL basics: how to make transfers", 20 Jul 2026]
MAX_FREE_TRANSFERS = 5              # [verified 2026-10-11: premierleague.com 2026/27 FPL changes; API max_extra_free_transfers=4]
_EPS = 1e-9
_CONFIDENCE = ("HIGH", "MEDIUM", "LOW")

BASIS = {
    "kind": "projected_expected_points",
    "guaranteed": False,
    "note": ("All points and gains are model projections (expected values), not guarantees. "
             "Affordability uses current prices, not selling prices."),
}
OUTLOOK_NOTE = ("GW+2/GW+3 figures are provisional: the multi-gameweek projection has not been "
                "backtested, and player availability is extrapolated from the next-round flag.")


# ─────────────────────────────────────────────────────────────────────────────
# SQUAD VALUE
# ─────────────────────────────────────────────────────────────────────────────

# The legal-XI valuation lives in bench.py (public `best_legal_xi`); one implementation for both.
best_xi = best_legal_xi


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _validate_players(players):
    """Every supplied entry must be usable; problems are reported together."""
    problems = []
    for pid, info in players.items():
        if not _is_int(pid):
            problems.append("player key %r is not an int id" % (pid,))
            continue
        if not isinstance(info, dict):
            problems.append("player %s: info is not a dict" % pid)
            continue
        if "id" in info and info["id"] != pid:
            problems.append("player %s: id field %r does not match its key" % (pid, info["id"]))
        if not _is_int(info.get("position")) or info.get("position") not in (1, 2, 3, 4):
            problems.append("player %s: position must be 1..4, got %r" % (pid, info.get("position")))
        if not _is_int(info.get("team")):
            problems.append("player %s: team must be an int, got %r" % (pid, info.get("team")))
        if not _is_int(info.get("price")) or info["price"] <= 0:
            problems.append("player %s: price must be a positive int (tenths of £m), got %r"
                            % (pid, info.get("price")))
        x = info.get("xpts")
        if not isinstance(x, (list, tuple)) or len(x) != HORIZON \
                or any(not (v is None or _is_num(v)) for v in x):
            problems.append("player %s: xpts must be %d numbers (or None for unavailable), got %r"
                            % (pid, HORIZON, x))
        elif any(v is not None and v < 0 for v in x):
            problems.append("player %s: xpts must not be negative" % pid)
        if info.get("confidence") not in (None,) + _CONFIDENCE:
            problems.append("player %s: confidence must be one of %s" % (pid, _CONFIDENCE))
    if problems:
        shown = problems[:10]
        more = "" if len(problems) <= 10 else " (+%d more)" % (len(problems) - 10)
        raise ValueError("invalid player data: " + "; ".join(shown) + more)


def _validate(squad_ids, players, bank, free_transfers, out_ids, in_ids, moves, weights,
              max_hits, hit_cost, min_gain, top_n, in_pool_per_position, out_pool_size,
              max_evals, free_transfer_cap, keep_ids=()):
    if len(squad_ids) != 15 or len(set(squad_ids)) != 15:
        raise ValueError("squad must be 15 unique player ids; got %d (%d unique)"
                         % (len(squad_ids), len(set(squad_ids))))
    missing = [p for p in squad_ids if p not in players]
    if missing:
        raise ValueError("squad players missing from player data: %s" % missing)
    _validate_players(players)
    counts = {1: 0, 2: 0, 3: 0, 4: 0}
    clubs: Dict[int, int] = {}
    for pid in squad_ids:
        counts[players[pid]["position"]] += 1
        clubs[players[pid]["team"]] = clubs.get(players[pid]["team"], 0) + 1
    if counts != SQUAD_COMPOSITION:
        raise ValueError("squad composition %s != required %s" % (counts, SQUAD_COMPOSITION))
    if max(clubs.values()) > MAX_PER_CLUB:
        raise ValueError("squad already has more than %d players from one club" % MAX_PER_CLUB)
    if not _is_int(bank) or bank < 0:
        raise ValueError("bank must be a non-negative int in tenths of £m")
    if not _is_int(free_transfers) or free_transfers < 0:
        raise ValueError("free_transfers must be a non-negative int")
    if free_transfers > free_transfer_cap:
        raise ValueError("free_transfers=%d exceeds the cap of %d (official FPL rule as of 2026/27; "
                         "override free_transfer_cap if the rules change)"
                         % (free_transfers, free_transfer_cap))
    if not _is_int(max_hits) or max_hits < 0:
        raise ValueError("max_hits must be a non-negative int")
    if not _is_num(hit_cost) or hit_cost < 0:
        raise ValueError("hit_cost must be a non-negative number")
    if not _is_num(min_gain) or min_gain < 0:
        raise ValueError("min_gain must be a non-negative number")
    if not _is_int(top_n) or top_n < 1:
        raise ValueError("top_n must be a positive int")
    if in_pool_per_position is not None and (not _is_int(in_pool_per_position) or in_pool_per_position < 1):
        raise ValueError("in_pool_per_position must be a positive int or None (no limit)")
    if not _is_int(out_pool_size) or out_pool_size < 1:
        raise ValueError("out_pool_size must be a positive int")
    if not _is_int(max_evals) or max_evals < 1:
        raise ValueError("max_evals must be a positive int")
    if len(weights) != HORIZON or any(not _is_num(w) or w < 0 for w in weights) or not any(weights):
        raise ValueError("weights must be %d non-negative numbers, not all zero" % HORIZON)

    if moves and (out_ids or in_ids):
        raise ValueError("pass either moves=[(out, in), ...] or out_ids / in_ids, not both")
    if out_ids and in_ids:
        raise ValueError("out_ids and in_ids together do not say who replaces whom; "
                         "use moves=[(out, in), ...] for explicit pairs")
    for m in moves:
        if not (isinstance(m, (tuple, list)) and len(m) == 2):
            raise ValueError("each move must be an (out_id, in_id) pair; got %r" % (m,))
    named_out = [m[0] for m in moves] + list(out_ids)
    named_in = [m[1] for m in moves] + list(in_ids)
    for pid in named_out:
        if pid not in squad_ids:
            raise ValueError("out player %s is not in the squad" % pid)
    for pid in named_in:
        if pid in squad_ids:
            raise ValueError("in player %s is already in the squad" % pid)
        if pid not in players:
            raise ValueError("in player %s missing from player data" % pid)
    if len(set(named_out)) != len(named_out) or len(set(named_in)) != len(named_in):
        raise ValueError("duplicate ids among the named outgoing / incoming players")
    for pid in keep_ids:
        if not _is_int(pid) or pid not in squad_ids:
            raise ValueError("keep player %r is not in the squad" % (pid,))
    if len(set(keep_ids)) != len(keep_ids):
        raise ValueError("duplicate ids in keep_ids")
    clash = sorted(set(keep_ids) & set(named_out))
    if clash:
        raise ValueError("player(s) %s cannot be both sold and kept" % clash)


def _xpts(info):
    """(values, missing_gw_indexes). Unavailable (None) projections score 0 and are flagged."""
    vals, missing = [], []
    for g, v in enumerate(info["xpts"]):
        if v is None:
            vals.append(0.0)
            missing.append(g)
        else:
            vals.append(float(v))
    return vals, missing


def _m(tenths):
    return "£%.1fm" % (tenths / 10.0)


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def plan_transfers(
    squad_ids:      Sequence[int],
    players:        Dict[int, dict],
    bank:           int,
    free_transfers: int,
    out_ids:        Sequence[int] = (),
    in_ids:         Sequence[int] = (),
    moves:          Sequence[tuple] = (),
    weights:        Sequence[float] = DEFAULT_WEIGHTS,
    max_hits:       int = 0,
    hit_cost:       float = HIT_COST,
    min_gain:       float = 0.5,
    in_pool_per_position: Optional[int] = 8,
    out_pool_size:  int = 10,
    max_evals:      int = 60000,
    top_n:          int = 5,
    free_transfer_cap: int = MAX_FREE_TRANSFERS,
    keep_ids:       Sequence[int] = (),
) -> dict:
    """
    Parameters
    ----------
    squad_ids       : the user's 15 player ids (order irrelevant, starters not needed).
    players         : {id: info} for the squad and every candidate (see module doc).
    bank            : remaining bank in tenths of £m (required input).
    free_transfers  : free transfers available this GW (required input).
    moves / out_ids / in_ids : optional user intent (see module doc); at most one style.
    weights         : per-GW weights; normalised to sum to 1. Default next GW only.
                    The 3-GW outlook is reported separately regardless. [provisional]
    max_hits        : extra transfers beyond free_transfers to consider (default 0 = no hits).
                    Each costs `hit_cost` points, subtracted from the plan's score.
    min_gain        : projected gain over HOLD needed to recommend a transfer.
    in_pool_per_position : incoming candidates searched per position (None = all supplied).
    out_pool_size   : squad players considered for sale in open-ended search (15 = all).
    max_evals       : soft budget on squad evaluations; pools shrink (never below the
                    named players) until the planned search fits.
    keep_ids        : squad players that must not be sold (search constraint; see module doc).
    """
    squad_ids, out_ids, in_ids = list(squad_ids), list(out_ids), list(in_ids)
    keep_ids = list(keep_ids)
    moves = [tuple(m) if isinstance(m, (tuple, list)) else m for m in moves]
    weights = tuple(weights)
    _validate(squad_ids, players, bank, free_transfers, out_ids, in_ids, moves, weights,
              max_hits, hit_cost, min_gain, top_n, in_pool_per_position, out_pool_size,
              max_evals, free_transfer_cap, keep_ids)
    keep_set = set(keep_ids)
    wsum = float(sum(weights))
    weights = tuple(w / wsum for w in weights)

    position = {pid: info["position"] for pid, info in players.items()}
    team     = {pid: info["team"] for pid, info in players.items()}
    price    = {pid: info["price"] for pid, info in players.items()}
    gw, gaps = {}, {}
    for pid, info in players.items():
        gw[pid], gaps[pid] = _xpts(info)
    score = {pid: sum(w * x for w, x in zip(weights, gw[pid])) for pid in players}
    name = lambda p: players[p].get("web_name") or str(p)

    squad_set = set(squad_ids)
    max_k     = free_transfers + max_hits
    warnings: List[str] = []

    def hits_for(k):
        return max(0, k - free_transfers)

    def penalty(k):
        return hits_for(k) * hit_cost

    for p in squad_ids:
        if gaps[p]:
            warnings.append("%s has no projection data for GW+%s; valued at 0 for those gameweeks "
                            "(data unavailable, not a blank gameweek)"
                            % (name(p), ",".join(str(g + 1) for g in gaps[p])))

    # Players whose projection is unavailable (fetch failed / timed out / malformed). They are never
    # bought or sold by a ranked plan: valuing them at 0 would invent a gain from selling them (or a
    # loss from buying them). Plans that keep them still see them at 0 in both hold and plan.
    incomplete = {p for p in players if gaps[p]}
    squad_incomplete = sorted(p for p in squad_ids if p in incomplete)

    # Robustness when squad players lack projections: re-score the hold and each plan with those players
    # valued like the best known supplied player of their position. A gain that does not survive this is not
    # reported as a recommendation, because it may only exist because those players were valued at 0.
    score_hi, hold_hi = score, None
    if squad_incomplete:
        score_hi = dict(score)
        for p in squad_incomplete:
            known = [q for q in players if q not in incomplete and position[q] == position[p]]
            score_hi[p] = max([score[q] for q in known] or [0.0])
        hold_hi = best_xi(squad_ids, score_hi, position)[0]

    # ── Intent ────────────────────────────────────────────────────────────────
    intent = "moves" if moves else "out_only" if out_ids else "in_only" if in_ids else None
    forced_out = set(out_ids) | {m[0] for m in moves}
    forced_in  = set(in_ids) | {m[1] for m in moves}
    n_named    = len(moves) or len(out_ids) or len(in_ids)

    # ── HOLD ──────────────────────────────────────────────────────────────────
    hold_score, hold_xi = best_xi(squad_ids, score, position)
    hold_gw = [best_xi(squad_ids, {p: gw[p][g] for p in squad_set}, position)[0]
               for g in range(HORIZON)]

    # ── Pools ─────────────────────────────────────────────────────────────────
    supplied = {pos: [p for p in players if position[p] == pos and p not in squad_set]
                for pos in (1, 2, 3, 4)}

    def build_in_pool(per_pos):
        pool = {}
        for pos in (1, 2, 3, 4):
            cands = [p for p in supplied[pos] if p not in forced_in]
            by_score = sorted(cands, key=lambda p: (-score[p], p))[:per_pos]
            # cheap enablers: best score per £ so budget-freeing swaps stay reachable
            by_value = sorted(cands, key=lambda p: (-(score[p] / max(price[p], 1)), p))[:max(1, per_pos // 2)]
            chosen = set(by_score) | set(by_value) | {p for p in forced_in if position[p] == pos}
            pool[pos] = sorted(chosen, key=lambda p: (-score[p], p))
        return pool

    def build_out_pool(size):
        rest = sorted((p for p in squad_ids if p not in forced_out and p not in keep_set),
                      key=lambda p: (score[p], p))
        return sorted(forced_out) + rest[:max(0, size - len(forced_out))]

    def slots_for(out, fi):
        slots = {pos: 0 for pos in (1, 2, 3, 4)}
        for p in out:
            slots[position[p]] += 1
        for p in fi:
            slots[position[p]] -= 1
        return slots

    def level_cost(out_cands, in_pool, k, fo, fi):
        n_extra = k - len(fo)
        if n_extra < 0 or n_extra > len(out_cands):
            return 0
        total = 0
        for extra in combinations(out_cands, n_extra):
            slots = slots_for(list(fo) + list(extra), fi)
            if any(s < 0 for s in slots.values()):
                continue
            cnt = 1
            for pos, s in slots.items():
                cnt *= comb(len([p for p in in_pool[pos] if p not in fi]), s)
            total += cnt
        return total

    empty_pool = {1: [], 2: [], 3: [], 4: []}
    kmin_named = max(1, len(forced_out), len(forced_in))

    def passes_for(out_pool, in_pool):
        """(label, forced_out, forced_in, ks, out candidates, in pool) for every pass."""
        free_out = [p for p in out_pool if p not in forced_out]
        out = [("open", frozenset(), frozenset(), range(1, max_k + 1),
                [p for p in out_pool], in_pool)]
        if intent:
            out.append(("extended", frozenset(forced_out), frozenset(forced_in),
                        range(kmin_named, max_k + 1), free_out, in_pool))
            allowed = n_named <= max_k
            if intent == "out_only":
                out.append(("literal", frozenset(forced_out), frozenset(),
                            [len(forced_out)] if allowed else [], [], in_pool))
            elif intent == "in_only":
                out.append(("literal", frozenset(), frozenset(forced_in),
                            [len(forced_in)] if allowed else [],
                            [p for p in squad_ids if p not in keep_set], empty_pool))
        return out

    def total_cost(out_pool, in_pool):
        return sum(level_cost(oc, ip, k, fo, fi)
                   for _, fo, fi, ks, oc, ip in passes_for(out_pool, in_pool) for k in ks)

    max_supplied = max((len(v) for v in supplied.values()), default=0)
    per_pos = max_supplied if in_pool_per_position is None else min(in_pool_per_position, max(max_supplied, 1))
    out_size = min(15, max(out_pool_size, len(forced_out)))
    in_pool, out_pool = build_in_pool(per_pos), build_out_pool(out_size)
    shrunk = False
    while total_cost(out_pool, in_pool) > max_evals and (per_pos > 2 or out_size > max(len(forced_out), max_k)):
        shrunk = True
        if per_pos > 2:
            per_pos -= 1
        else:
            out_size -= 1
        in_pool, out_pool = build_in_pool(per_pos), build_out_pool(out_size)
    budget_exceeded = total_cost(out_pool, in_pool) > max_evals
    if budget_exceeded:
        warnings.append("Search size exceeds max_evals even at minimum pool sizes; "
                        "the full (small) space was still searched but took longer than budgeted.")

    # ── Evaluate / enumerate ──────────────────────────────────────────────────
    plans: Dict[tuple, dict] = {}
    evals = 0

    def check(out, ins, stats=None):
        """Return the evaluated plan, or None if illegal (reason counted in `stats`)."""
        nonlocal evals
        if incomplete and (incomplete.intersection(out) or incomplete.intersection(ins)):
            if stats is not None:
                stats["incomplete"] = stats.get("incomplete", 0) + 1
            return None
        key_ = (tuple(sorted(out)), tuple(sorted(ins)))
        if key_ in plans:
            return plans[key_]
        spend = sum(price[p] for p in ins) - sum(price[p] for p in out)
        if spend > bank:
            if stats is not None:
                stats["budget"] = stats.get("budget", 0) + 1
                stats["min_short"] = min(stats.get("min_short", spend - bank), spend - bank)
            return None
        new_squad = [p for p in squad_ids if p not in out] + list(ins)
        clubs: Dict[int, int] = {}
        for p in new_squad:
            clubs[team[p]] = clubs.get(team[p], 0) + 1
            if clubs[team[p]] > MAX_PER_CLUB:
                if stats is not None:
                    stats["club"] = stats.get("club", 0) + 1
                return None
        evals += 1
        total, xi = best_xi(new_squad, score, position)
        k = len(out)
        plans[key_] = {"out": key_[0], "in": key_[1], "n_transfers": k, "score": total,
                       "net_score": total - penalty(k), "bank_after": bank - spend}
        return plans[key_]

    pass_stats: Dict[str, dict] = {}
    for label, fo, fi, ks, out_cands, ip in passes_for(out_pool, in_pool):
        stats = pass_stats.setdefault(label, {"considered": 0})
        for k in ks:
            n_extra = k - len(fo)
            if n_extra < 0 or n_extra > len(out_cands):
                continue
            for extra in combinations(out_cands, n_extra):
                out = list(fo) + list(extra)
                slots = slots_for(out, fi)
                if any(s < 0 for s in slots.values()):
                    continue
                groups = [list(combinations([p for p in ip[pos] if p not in fi], slots[pos]))
                          for pos in (1, 2, 3, 4)]
                for choice in product(*groups):
                    ins = list(fi) + [p for g in choice for p in g]
                    stats["considered"] += 1
                    check(out, ins, stats)

    # ── Literal "moves" plan (evaluated directly, independent of any pool) ────
    literal_reasons: List[str] = []
    literal_plan = None
    if intent == "moves":
        outs, ins = [m[0] for m in moves], [m[1] for m in moves]
        if len(moves) > max_k:
            literal_reasons.append("needs %d transfer(s) but only %d free%s"
                                   % (len(moves), free_transfers, "" if max_hits else "; hits are disabled"))
        for o, i in moves:
            if position[o] != position[i]:
                literal_reasons.append("%s -> %s: positions differ (a transfer must keep the squad's "
                                       "position counts)" % (name(o), name(i)))
        spend = sum(price[i] for i in ins) - sum(price[o] for o in outs)
        if spend > bank:
            literal_reasons.append("estimated cost %s exceeds funds: incoming %s, outgoing %s, bank %s "
                                   "(short by %s)" % (_m(spend), _m(sum(price[i] for i in ins)),
                                                      _m(sum(price[o] for o in outs)), _m(bank),
                                                      _m(spend - bank)))
        new_squad = [p for p in squad_ids if p not in outs] + ins
        clubs: Dict[int, int] = {}
        for p in new_squad:
            clubs[team[p]] = clubs.get(team[p], 0) + 1
        for c, n in sorted(clubs.items()):
            if n > MAX_PER_CLUB:
                literal_reasons.append("would leave %d players from one club (club %s); the limit is %d"
                                       % (n, c, MAX_PER_CLUB))
        if not literal_reasons:
            literal_plan = check(outs, ins)

    # ── Classify plans ────────────────────────────────────────────────────────
    def key(plan):
        return (-plan["net_score"], plan["n_transfers"], plan["out"], plan["in"])

    ranked = sorted(plans.values(), key=key)

    def is_literal(plan):
        o, i = set(plan["out"]), set(plan["in"])
        if intent == "moves":
            return o == forced_out and i == forced_in
        if intent == "out_only":
            return o == forced_out and plan["n_transfers"] == len(forced_out)
        if intent == "in_only":
            return i == forced_in and plan["n_transfers"] == len(forced_in)
        return False

    def contains_named(plan):
        return forced_out <= set(plan["out"]) and forced_in <= set(plan["in"])

    if intent in ("out_only", "in_only"):
        lits = [p for p in ranked if is_literal(p)]
        literal_plan = lits[0] if lits else None
    extended = [p for p in ranked if intent and contains_named(p) and not is_literal(p)]

    # ── Assemble ──────────────────────────────────────────────────────────────
    def describe(p):
        info = players[p]
        d = {"id": p, "web_name": info.get("web_name", str(p)), "position": info["position"],
             "team": info["team"], "price": info["price"], "xpts": gw[p],
             "confidence": info.get("confidence"), "status": info.get("status")}
        if gaps[p]:
            d["projection_unavailable_gws"] = [g + 1 for g in gaps[p]]
        return d

    def outlook_for(squad):
        return [best_xi(squad, {p: gw[p][g] for p in squad}, position)[0] for g in range(HORIZON)]

    def risks_for(out, ins, outlook):
        risks = []
        for p in ins:
            info, nm = players[p], name(p)
            if info.get("confidence") == "LOW":
                risks.append("%s: LOW projection confidence" % nm)
            if info.get("status") not in (None, "a"):
                risks.append("%s: availability flag '%s'" % (nm, info.get("status")))
            if gaps[p]:
                risks.append("%s: projection data unavailable for GW+%s (valued at 0; this is missing "
                             "data, not a blank gameweek)" % (nm, ",".join(str(g + 1) for g in gaps[p])))
            elif gw[p][0] == 0.0:
                risks.append("%s: projects 0 next GW (blank gameweek, unavailable or no minutes)" % nm)
            if info.get("outlook_caveat"):
                risks.append("%s: %s" % (nm, info["outlook_caveat"]))
            for r in info.get("risks") or []:
                risks.append("%s: %s" % (nm, r))
        if out:
            if outlook[1] + outlook[2] < hold_gw[1] + hold_gw[2] - _EPS:
                risks.append("Trades GW+2/GW+3 points for next-GW points (provisional outlook is lower than holding)")
            risks.append("Affordability is estimated from current prices, not selling prices")
        return risks

    def robust_gain(plan):
        if not squad_incomplete:
            return None
        squad = [p for p in squad_ids if p not in plan["out"]] + list(plan["in"])
        return best_xi(squad, score_hi, position)[0] - penalty(plan["n_transfers"]) - hold_hi

    def finish(plan):
        out, ins = list(plan["out"]), list(plan["in"])
        squad = [p for p in squad_ids if p not in out] + ins
        rg = robust_gain(plan)
        outlook = outlook_for(squad)
        confs = [players[p].get("confidence") for p in ins if players[p].get("confidence")]
        if any(gaps[p] for p in ins):
            confs.append("LOW")
        order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        conf = min(confs, key=lambda c: order[c]) if confs else None
        if squad_incomplete:
            conf = "LOW"
        k = plan["n_transfers"]
        return {
            "n_transfers": k,
            "free_transfers_used": min(k, free_transfers),
            "free_transfers_remaining": max(0, free_transfers - k),
            "hits": hits_for(k),
            "hit_points": penalty(k),
            "transfers": [{"out": describe(o), "in": describe(i)} for o, i in _pair(out, ins, position)],
            "score": round(plan["score"], 2),
            "net_score": round(plan["net_score"], 2),
            "projected_gain_vs_hold": round(plan["net_score"] - hold_score, 2),
            "projected_gain_before_hits": round(plan["score"] - hold_score, 2),
            "bank_after": plan["bank_after"],
            "xi_ids": best_xi(squad, score, position)[1],
            "outlook": {"status": "provisional_unvalidated", "note": OUTLOOK_NOTE,
                        "best_xi_xpts": [round(v, 2) for v in outlook],
                        "vs_hold": [round(o - h, 2) for o, h in zip(outlook, hold_gw)]},
            "confidence": conf,
            "risks": risks_for(out, ins, outlook),
            "price_source": "estimated",
            "projected": True,
            "data_complete": not squad_incomplete,
            "gain_range": None if rg is None else {
                "missing_projections_valued_at_zero": round(plan["net_score"] - hold_score, 2),
                "missing_projections_valued_at_position_best": round(rg, 2)},
        }

    hold_plan = {
        "n_transfers": 0, "free_transfers_used": 0, "free_transfers_remaining": free_transfers,
        "hits": 0, "hit_points": 0, "transfers": [],
        "score": round(hold_score, 2), "net_score": round(hold_score, 2),
        "projected_gain_vs_hold": 0.0, "projected_gain_before_hits": 0.0, "bank_after": bank,
        "xi_ids": hold_xi,
        "outlook": {"status": "provisional_unvalidated", "note": OUTLOOK_NOTE,
                    "best_xi_xpts": [round(v, 2) for v in hold_gw], "vs_hold": [0.0] * HORIZON},
        "confidence": "LOW" if squad_incomplete else None, "risks": [], "price_source": "estimated",
        "projected": True, "data_complete": not squad_incomplete,
    }

    def robust(plan):
        rg = robust_gain(plan)
        return rg is None or rg >= min_gain

    top = ranked[0] if ranked and ranked[0]["net_score"] > hold_score + _EPS else None
    best = top
    not_robust = None
    if top is not None and squad_incomplete and top["net_score"] - hold_score >= min_gain:
        passing = next((pl_ for pl_ in ranked
                        if pl_["net_score"] - hold_score >= min_gain and robust(pl_)), None)
        if passing is None:
            not_robust, best = top, None
        else:
            best = passing
    recommendation = {"action": "HOLD", "plan": hold_plan, "projected_gain": 0.0,
                      "projected_gain_before_hits": 0.0,
                      "reason": "No transfer plan is projected to beat holding"}
    if not_robust is not None:
        recommendation["reason"] = (
            "Best projected gain (+%.2f) is not reliable: it depends on valuing squad players with missing "
            "projections at 0 (it falls to %+.2f if they score like their position's best)"
            % (not_robust["net_score"] - hold_score, robust_gain(not_robust)))
    if best is not None:
        gain = best["net_score"] - hold_score
        if gain >= min_gain:
            recommendation = {
                "action": "TRANSFER", "plan": finish(best), "projected_gain": round(gain, 2),
                "projected_gain_before_hits": round(best["score"] - hold_score, 2),
                "reason": ("Projected gain of +%.2f xPts over holding (an estimate, not a guarantee; "
                           "threshold %.2f)" % (gain, min_gain))}
        else:
            recommendation["reason"] = ("Best projected gain (+%.2f) is below the %.2f threshold"
                                        % (gain, min_gain))
    if squad_incomplete:
        recommendation["reason"] += ("; partial data: %d squad player(s) have no projection and are valued at 0 "
                                     "in both the hold and every plan (%s)"
                                     % (len(squad_incomplete), ", ".join(name(p) for p in squad_incomplete)))
    rec_key = (best["out"], best["in"]) if recommendation["action"] == "TRANSFER" else None

    requested = None
    if intent:
        requested = {"intent": intent, "status": None, "reason": None, "plan": None,
                     "with_additional_transfers": None, "matches_overall_recommendation": False,
                     "pairing_preserved": True}
        if intent in ("out_only", "in_only") and n_named > max_k:
            literal_reasons.append("needs %d transfer(s) but only %d free%s"
                                   % (n_named, free_transfers, "" if max_hits else "; hits are disabled"))
        if literal_plan is not None and not literal_reasons:
            plan = finish(literal_plan)
            gain = literal_plan["net_score"] - hold_score
            if gain < -_EPS:
                status, why = "WORSE_THAN_HOLD", "Projected to score %.2f xPts less than holding" % -gain
            elif gain < min_gain:
                status, why = "BELOW_THRESHOLD", ("Projected gain +%.2f is below the %.2f threshold"
                                                  % (gain, min_gain))
            elif not robust(literal_plan):
                status, why = "BELOW_THRESHOLD", (
                    "Projected gain +%.2f depends on valuing squad players with missing projections at 0; "
                    "it is not reliable" % gain)
            else:
                status, why = "RECOMMENDED", ("Projected gain of +%.2f xPts over holding "
                                              "(an estimate, not a guarantee)" % gain)
            requested.update(status=status, reason=why, plan=plan,
                             matches_overall_recommendation=rec_key == (literal_plan["out"], literal_plan["in"]))
        else:
            if not literal_reasons:
                st = pass_stats.get("literal", {})
                if not st.get("considered"):
                    literal_reasons.append("no candidate of the required position in the searched pool")
                else:
                    parts = []
                    if st.get("budget"):
                        parts.append("%d combination(s) exceed the estimated budget (closest is short by %s)"
                                     % (st["budget"], _m(st["min_short"])))
                    if st.get("club"):
                        parts.append("%d breach the %d-per-club limit" % (st["club"], MAX_PER_CLUB))
                    literal_reasons.append("no legal option among %d searched: %s"
                                           % (st["considered"], "; ".join(parts) or "unknown"))
            requested.update(status="INFEASIBLE", reason="; ".join(literal_reasons))
        better = [p for p in extended if p["net_score"] > (literal_plan["net_score"] if literal_plan else -1e18)]
        if better:
            ext = finish(better[0])
            ext["label"] = ("Includes transfers you did not request; shown separately and never "
                            "substituted for your requested plan")
            requested["with_additional_transfers"] = ext

    named_incomplete = sorted(p for p in (forced_out | forced_in) if p in incomplete)
    if intent and named_incomplete:
        requested.update(
            status="INCOMPLETE_DATA", plan=None, with_additional_transfers=None,
            matches_overall_recommendation=False,
            reason=("Cannot evaluate: no projection data for %s (data unavailable, not a blank gameweek). "
                    "No estimate was made." % ", ".join(name(p) for p in named_incomplete)))

    # ── Search coverage ───────────────────────────────────────────────────────
    n_supplied = sum(len(v) for v in supplied.values())
    searched = {pos: sorted(set(in_pool[pos]) | {p for p in forced_in if position[p] == pos})
                for pos in (1, 2, 3, 4)}
    n_searched = sum(len(v) for v in searched.values())
    squad_unsold = sorted(p for p in squad_ids if p not in out_pool and p not in keep_set)
    pruned = n_searched < n_supplied or bool(squad_unsold) or bool(incomplete)
    search = {
        "scope": "supplied_players",
        "optimality": "not_guaranteed" if (pruned or budget_exceeded) else "optimal_over_supplied_players",
        "exhaustive_within_search_space": True,
        "pruned": pruned,
        "budget_exceeded": budget_exceeded,
        "coverage": {
            "candidates_supplied": {pos: len(v) for pos, v in supplied.items()},
            "candidates_searched": {pos: len(v) for pos, v in searched.items()},
            "candidates_excluded": n_supplied - n_searched,
            "squad_players_considered_for_sale": len(out_pool),
            "squad_players_not_considered_for_sale": squad_unsold,
            "squad_players_kept": sorted(keep_set),
            "excluded_for_missing_projection": sorted(p for p in incomplete if p not in squad_set),
            "squad_players_missing_projection": squad_incomplete,
            "limits": {"in_pool_per_position": in_pool_per_position, "out_pool_size": out_pool_size,
                       "pools_shrunk_to_fit_budget": shrunk},
        },
        "evaluations": evals,
        "max_evals": max_evals,
        "note": ("Only the supplied players were considered, and the supplied list may itself be a "
                 "shortlist of the league. A plan outside the searched space can exist."),
    }

    data_quality = {
        "complete": not incomplete,
        "squad_projection_missing": squad_incomplete,
        "candidates_projection_missing": sorted(p for p in incomplete if p not in squad_set),
        "hold_valuation": "understated" if squad_incomplete else "complete",
        "note": ("Players without a projection are never bought or sold by a ranked plan and are valued at 0 "
                 "wherever they stay in the squad; a named move involving one is reported as INCOMPLETE_DATA."),
    }
    return {
        "basis": BASIS,
        "data_quality": data_quality,
        "inputs": {"bank": bank, "free_transfers": free_transfers, "weights": list(weights),
                   "max_hits": max_hits, "hit_cost": hit_cost, "min_gain": min_gain,
                   "price_source": "now_cost (estimate; selling price may differ)",
                   "intent": intent, "out_ids": out_ids, "in_ids": in_ids, "moves": moves,
                   "keep_ids": keep_ids},
        "hold": hold_plan,
        "recommendation": recommendation,
        "requested": requested,
        "alternatives": [finish(p) for p in ranked[:top_n]],
        "search": search,
        "warnings": warnings,
    }


def _pair(out, ins, position):
    """Pair each outgoing player with an incoming one of the same position (display only)."""
    ins = sorted(ins)
    pairs = []
    for o in sorted(out):
        i = next(x for x in ins if position[x] == position[o])
        ins.remove(i)
        pairs.append((o, i))
    return pairs


# ─────────────────────────────────────────────────────────────────────────────
# ADAPTERS (bootstrap + projection engine)
# ─────────────────────────────────────────────────────────────────────────────

_UNAVAILABLE = ("i", "s", "u", "n")


def shortlist_candidates(bootstrap: dict, squad_ids: Sequence[int], forced_ids: Sequence[int] = (),
                         per_position: int = 12) -> List[int]:
    """
    Bounded candidate ids from bootstrap data only (no per-player requests). Per position the
    union of the top entries by FPL ep_next, form and points-per-game; players with no minutes
    or confirmed out are skipped. Squad and forced ids are always included. This is a pre-filter
    to bound summary fetches; ranking is done by the projection engine afterwards.
    """
    elements = bootstrap.get("elements", [])
    chosen = set(squad_ids) | set(forced_ids)

    def num(e, k):
        try:
            return float(e.get(k) or 0)
        except (TypeError, ValueError):
            return 0.0

    third = max(1, per_position // 2)
    for pos in (1, 2, 3, 4):
        pool = [e for e in elements
                if e.get("element_type") == pos and e["id"] not in chosen
                and num(e, "minutes") > 0
                and not (e.get("status") in _UNAVAILABLE
                         and e.get("chance_of_playing_next_round") == 0)]
        picks = set()
        for k in ("ep_next", "form", "points_per_game"):
            top = sorted(pool, key=lambda e: (-num(e, k), e["id"]))[:third]
            picks.update(e["id"] for e in top)
        chosen |= picks
    return sorted(chosen)


def project_players(bootstrap: dict, summaries: dict, params: dict, ids: Sequence[int],
                    compute=None) -> Dict[int, dict]:
    """
    Build planner player info using the existing projection engine.
    xpts[g] is the sum of compute_xpts over that gameweek's fixtures (double GW = two) or 0.0
    for a genuinely blank gameweek, for the next HORIZON gameweeks. A player whose
    element-summary was not fetched gets xpts [None, None, None] (projection unavailable),
    which the planner reports as missing data and never as a blank gameweek.
    Prices are bootstrap now_cost.
    `summaries` = {id: {"history": [...], "fixtures": [...]}} (element-summary shape).
    """
    if compute is None:
        from fpl.engine.projection import compute_xpts as compute
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    elem_by_id = {e["id"]: e for e in bootstrap.get("elements", [])}
    events = bootstrap.get("events", [])
    nxt = next((e["id"] for e in events if e.get("is_next")), None)
    if nxt is None:
        nxt = next((e["id"] for e in events if not e.get("finished")), None)
    out: Dict[int, dict] = {}
    for pid in ids:
        el = elem_by_id.get(pid)
        if el is None:
            continue
        summary = summaries.get(pid)
        info = {
            "id": pid, "web_name": el.get("web_name", ""), "position": el["element_type"],
            "team": el["team"], "price": el["now_cost"], "status": el.get("status"),
        }
        if not summary or "fixtures" not in summary:
            info.update(xpts=[None] * HORIZON, confidence="LOW",
                        risks=["No element-summary data was available: projection unavailable "
                               "(not a blank gameweek)"])
            out[pid] = info
            continue
        history = summary.get("history", [])
        by_event: Dict[int, list] = {}
        for fx in summary["fixtures"]:
            if fx.get("event") is not None and nxt is not None and fx["event"] >= nxt:
                by_event.setdefault(fx["event"], []).append(fx)
        xpts, confs, risks = [], [], []
        for g in range(HORIZON):
            fxs = by_event.get(nxt + g, []) if nxt is not None else []
            if not fxs:
                r = compute(el, history, None, params)
                xpts.append(0.0)
                if g == 0:
                    confs.append(r["confidence"])
                    risks.extend(r.get("risks", []))
                continue
            rs = [compute(el, history, fx, params) for fx in fxs]
            xpts.append(round(sum(r["xPts"] for r in rs), 2))
            if g == 0:
                confs.extend(r["confidence"] for r in rs)
                for r in rs:
                    risks.extend(r.get("risks", []))
        info.update(xpts=xpts, confidence=min(confs, key=lambda c: order.get(c, 0)) if confs else "LOW",
                    risks=list(dict.fromkeys(risks)))
        if el.get("chance_of_playing_next_round") is not None or el.get("status") not in (None, "a"):
            info["outlook_caveat"] = ("GW+2/GW+3 reuse this round's availability flag "
                                      "(extrapolation beyond the next gameweek)")
        out[pid] = info
    return out
