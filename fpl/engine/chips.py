"""
SquadCheck chip advice v1: Triple Captain, Bench Boost, Free Hit, Wildcard.

Pure and deterministic: no network, no language model. Logic follows the product doc
"SquadCheck Chip Optimiser: Product Logic" (revised 19 Nov 2026):

  * Bench Boost, Triple Captain and Free Hit are decided on THIS week's squad by must-pass checks.
    They never forecast a better week; the only look ahead is fixture facts (doubles, blanks).
  * Wildcard alone uses a 3-gameweek outlook (weights 0.5 / 0.3 / 0.2), provisional.
  * Expiry: for the last three gameweeks before a window closes the chip's minimum drops by a
    third each week; in the final gameweek the verdict is USE_BY.
  * One chip per gameweek. If two qualify, the larger gain is the headline.

All thresholds are provisional starting values (see the constants below).

Entry point: advise(inp) -> dict. See `advise` for the input shape.
"""

from itertools import product
from typing import Dict, List, Optional, Sequence

from .availability import can_play, is_available
from .bench import best_legal_xi

# ── provisional thresholds ──────────────────────────────────────────────────────
DEAD_SLOT = 2.0            # a bench player projected below this probably does not play
BB_MIN_TOTAL = 16.0        # Bench Boost: minimum bench total (xPts)
TC_MIN_DEFAULT = 7.0       # Triple Captain minimum until a season percentile exists
TC_LEAGUE_RATIO = 0.85     # pick must be >= 85% of the best eligible player in the league this week
FH_PLAY = 4                # Free Hit: unavailable starters needed to play
FH_CONSIDER = 3            # exactly this many = consider; the other tests decide
FH_MARGIN = 10.0           # Free Hit must beat the best transfer plan by this many xPts
FH_MAX_MOVES = 3           # the transfer plan it is compared with: up to 3 moves, hits included
WC_WEIGHTS = (0.5, 0.3, 0.2)
WC_GAIN = 0.05             # weighted gain over the baseline
WC_EXPIRY_GAIN = 0.02      # final-gameweek (USE_BY) Wildcard needs at least this much
WC_CHANGES = 4
WC_PROBLEMS = 4
WC_SUSTAINED = 2           # beats baseline in at least 2 of 3 gameweeks
WC_WINDOW_FIX_DIFF = 3.7   # average FPL fixture difficulty over the next 5 above this = bad run
WC_LOW_MINUTES = 45.0      # average minutes over the last 4 gameweeks below this = losing his place
CAPTAIN_EXTRA = 1.0        # captain's points counted once more
BENCH_WEIGHT = 0.1
SQUAD = {1: 2, 2: 5, 3: 5, 4: 3}
MAX_PER_CLUB = 3

CHIPS = ("3xc", "bboost", "freehit", "wildcard")
NAMES = {"3xc": "Triple Captain", "bboost": "Bench Boost", "freehit": "Free Hit", "wildcard": "Wildcard"}
PLAY, NOT_YET, USE_BY, NA, CONSIDER = "PLAY_NOW", "NOT_YET", "USE_BY", "NOT_AVAILABLE", "CONSIDER"


class ChipInputError(ValueError):
    pass


# ── helpers ─────────────────────────────────────────────────────────────────────
def _x(info, g=0):
    v = (info.get("xpts") or [None])[g] if g < len(info.get("xpts") or []) else None
    return float(v) if isinstance(v, (int, float)) else 0.0


def _playable(info):
    """Chance of playing at least 75% (confidence is not part of this test)."""
    return can_play(info.get("status"), info.get("cop"))


def _available(info):
    return is_available(info.get("status"), info.get("cop"), info.get("confidence"))


def loosen(left):
    """Share of a minimum that applies, given gameweeks left in the window after this one."""
    if left is None:
        return 1.0
    return max(0.0, min(1.0, left / 3.0))


def _best_xi(ids, info, g=0, avoid=None):
    """Best legal XI by xPts for gameweek index g. `avoid`: ids scored 0 (cannot play)."""
    avoid = avoid or set()
    scores = {i: (0.0 if i in avoid else _x(info[i], g)) for i in ids}
    pos = {i: info[i]["position"] for i in ids}
    _, xi_ids = best_legal_xi(list(ids), scores, pos)
    xi_ids = list(xi_ids)
    return xi_ids, [i for i in ids if i not in set(xi_ids)], scores


def _value(ids, info, g=0, avoid=None):
    """V(squad, g): XI xPts + captain's extra + 0.1 x bench (doc, Shared definitions)."""
    xi, bench, sc = _best_xi(ids, info, g, avoid)
    return (sum(sc[i] for i in xi) + CAPTAIN_EXTRA * max((sc[i] for i in xi), default=0.0)
            + BENCH_WEIGHT * sum(sc[i] for i in bench))


def _wvalue(ids, info, weights=WC_WEIGHTS, avoid=None):
    return sum(w * _value(ids, info, g, avoid) for g, w in enumerate(weights))


def _validate(inp):
    sq = inp.get("squad_ids")
    if not isinstance(sq, list) or len(sq) != 15 or len(set(sq)) != 15:
        raise ChipInputError("squad_ids must be 15 distinct player ids")
    info = inp.get("players")
    if not isinstance(info, dict) or any(i not in info for i in sq):
        raise ChipInputError("players must hold info for every squad id")
    comp = {1: 0, 2: 0, 3: 0, 4: 0}
    for i in sq:
        p = info[i].get("position")
        if p not in comp:
            raise ChipInputError("player %s has no valid position" % i)
        comp[p] += 1
    if comp != SQUAD:
        raise ChipInputError("squad must be 2 GKP, 5 DEF, 5 MID, 3 FWD")
    if not isinstance(inp.get("gameweek"), int):
        raise ChipInputError("gameweek must be an integer")
    for k in ("bank", "free_transfers"):
        if not isinstance(inp.get(k), int) or inp[k] < 0:
            raise ChipInputError("%s must be a non-negative integer" % k)


def _price_xi(ids, info):
    scores = {i: float(info[i]["price"]) for i in ids}
    pos = {i: info[i]["position"] for i in ids}
    return list(best_legal_xi(list(ids), scores, pos)[1])


def squad_after(squad_ids, plan_part):
    """Squad ids after a planner plan's transfers (plan_part = result['recommendation']['plan'] or hold)."""
    out = {t["out"]["id"] for t in (plan_part or {}).get("transfers", [])}
    inn = [t["in"]["id"] for t in (plan_part or {}).get("transfers", [])]
    return [i for i in squad_ids if i not in out] + inn


# ── best-team search (Free Hit and Wildcard) ───────────────────────────────────
def build_team(pool_ids: Sequence[int], info: Dict[int, dict], budget: int, objective,
               start_ids: Optional[Sequence[int]] = None, only_playable=True) -> List[int]:
    """
    Deterministic best 15 from the pool under budget, 2/5/5/3 by position and at most 3 per club.
    Greedy build by score, repaired to budget, then single-swap hill climbing. If start_ids is
    given, the better of the two results is returned. `objective(ids) -> float`.
    """
    cand = [i for i in pool_ids if i in info and (not only_playable or _playable(info[i]))]
    price = lambda i: info[i]["price"]
    key1 = lambda i: (-objective_single(i), i)

    def objective_single(i):
        return sum(w * _x(info[i], g) for g, w in enumerate(WC_WEIGHTS))

    def legal_add(team, i):
        return (sum(1 for t in team if info[t]["team"] == info[i]["team"]) < MAX_PER_CLUB)

    def greedy():
        team = []
        for pos, n in SQUAD.items():
            ranked = sorted((i for i in cand if info[i]["position"] == pos), key=key1)
            got = 0
            for i in ranked:
                if got < n and legal_add(team, i):
                    team.append(i)
                    got += 1
        return team

    def total(team):
        return sum(price(i) for i in team)

    def repair(team):
        # while over budget, swap the player with the smallest score loss per saved tenth
        guard = 0
        while total(team) > budget and guard < 200:
            guard += 1
            best = None
            for t in team:
                for c in cand:
                    if c in team or info[c]["position"] != info[t]["position"] or price(c) >= price(t):
                        continue
                    rest = [x for x in team if x != t]
                    if not legal_add(rest, c):
                        continue
                    loss = (objective_single(t) - objective_single(c)) / float(price(t) - price(c))
                    cand_key = (loss, c)
                    if best is None or cand_key < best[0]:
                        best = (cand_key, t, c)
            if best is None:
                return None
            team = [best[2] if x == best[1] else x for x in team]
        return team if total(team) <= budget else None

    def climb(team):
        cur = objective(team)
        for _ in range(60):
            best = None
            for t in team:
                rest = [x for x in team if x != t]
                for c in cand:
                    if c in team or info[c]["position"] != info[t]["position"]:
                        continue
                    if total(rest) + price(c) > budget or not legal_add(rest, c):
                        continue
                    v = objective(rest + [c])
                    if v > cur + 1e-9 and (best is None or (v, -c) > (best[0], -best[2])):
                        best = (v, t, c)
            if best is None:
                break
            team = [best[2] if x == best[1] else x for x in team]
            cur = best[0]
        return team

    results = []
    g = greedy()
    if len(g) == 15:
        g = repair(g) if total(g) > budget else g
        if g:
            results.append(climb(g))
    if start_ids:
        results.append(climb(list(start_ids)))
    results = [r for r in results if r and len(r) == 15]
    if not results:
        return list(start_ids or [])
    return max(results, key=lambda t: (objective(t), [-i for i in sorted(t)]))


# ── per-chip checks ─────────────────────────────────────────────────────────────
def _chk(ok, text):
    return {"ok": bool(ok), "text": text}


def _fx_facts(ids, info, gw, who):
    """Fixture facts only (known, not projected): doubles and blanks in the next two gameweeks."""
    facts = []
    for k in (1, 2):
        dbl = [info[i]["web_name"] for i in ids if (info[i].get("fx_counts") or [1, 1, 1])[k] > 1]
        if dbl:
            facts.append("GW%d is a double gameweek for %s." % (gw + k, ", ".join(dbl[:3])))
    return facts


def tc_advice(inp, info, ids, left):
    gw = inp["gameweek"]
    xi, bench, _ = _best_xi(ids, info)
    avail = [i for i in xi if _available(info[i])]
    out = {"chip": "3xc", "name": NAMES["3xc"], "next_check": "before the GW%d deadline" % (gw + 1)}
    if not avail:
        out.update(status=NOT_YET, pick=None, gain_xpts=0.0,
                   checks=[_chk(False, "No starter has at least a 75% chance of playing")],
                   line="No one in your team is likely enough to play to triple-captain this week.",
                   facts=[])
        return out
    pick = sorted(avail, key=lambda i: (-_x(info[i]), i))[0]
    px = _x(info[pick])
    minimum = float(inp.get("tc_min", TC_MIN_DEFAULT)) * loosen(left)
    league = float(inp.get("league_best_xpts") or 0.0)
    ratio = (px / league) if league > 0 else 1.0
    checks = [
        _chk(True, "%s has a %d%% chance of playing (needs 75%%)" % (info[pick]["web_name"], info[pick].get("chance", 100))),
        _chk(ratio >= TC_LEAGUE_RATIO,
             "%s is %d%% of the best eligible captain in the league this week (needs 85%%)" % (info[pick]["web_name"], round(ratio * 100))),
        _chk(px >= minimum - 1e-9,
             "%.1f projected points, %s the %.1f minimum" % (px, "at or above" if px >= minimum - 1e-9 else "under", minimum)),
    ]
    ok = all(c["ok"] for c in checks)
    status = USE_BY if left == 0 else (PLAY if ok else NOT_YET)
    name = info[pick]["web_name"]
    if left == 0:
        line = "This chip lapses at this deadline. %s is your best option, so use it or lose it." % name
    elif ok:
        line = "%s is your captain and clears every check. Triple Captain adds about %.1f points this week." % (name, px)
    else:
        line = "%s is your pick, but not every check passes yet. We look again next week." % name
    out.update(status=status, pick=pick, pick_name=name, gain_xpts=round(px, 2), checks=checks, line=line,
               numbers={"pick_xpts": round(px, 2), "minimum": round(minimum, 2), "league_best": round(league, 2)},
               facts=_fx_facts([pick], info, gw, name))
    return out


def prep_bench(squad, info, pool, bank, free_left):
    """
    Bench preparation (doc, Bench Boost): with the free transfers still unused, upgrade the weakest
    bench player to the best affordable playable player in the same position, one transfer at a
    time, only while the total xPts of all 15 rises. Never takes a points hit and never touches the
    starting XI. Returns (squad, swaps).
    """
    squad, swaps = list(squad), []
    for _ in range(max(0, free_left)):
        xi, bench, sc = _best_xi(squad, info)
        best = None
        for b in sorted(bench, key=lambda i: (sc[i], i)):
            clubs = {}
            for t in squad:
                if t != b:
                    clubs[info[t]["team"]] = clubs.get(info[t]["team"], 0) + 1
            for c in pool:
                if c in squad or c not in info or info[c]["position"] != info[b]["position"] or not _playable(info[c]):
                    continue
                if info[c]["price"] > info[b]["price"] + bank or clubs.get(info[c]["team"], 0) >= MAX_PER_CLUB:
                    continue
                gain = _x(info[c]) - sc[b]
                if gain > 1e-9 and (best is None or (gain, -c) > (best[0], -best[2])):
                    best = (gain, b, c)
            if best is not None:
                break
        if best is None:
            break
        _, b, c = best
        bank += info[b]["price"] - info[c]["price"]
        squad = [c if i == b else i for i in squad]
        swaps.append({"out": info[b]["web_name"], "in": info[c]["web_name"], "gain": round(best[0], 2)})
    return squad, swaps, bank


def bb_advice(inp, info, ids, plan_squad, left, swaps=None):
    gw = inp["gameweek"]
    sq = plan_squad
    xi, bench, sc = _best_xi(sq, info)
    out = {"chip": "bboost", "name": NAMES["bboost"], "next_check": "before the GW%d deadline" % (gw + 1)}
    total = sum(sc[i] for i in bench)
    dead = [i for i in bench if sc[i] < DEAD_SLOT]
    unavail = [i for i in bench if not _available(info[i])]
    minimum = BB_MIN_TOTAL * loosen(left)
    checks = []
    if unavail:
        checks.append(_chk(False, "%s may not play (needs a 75%% chance of playing)" % ", ".join(info[i]["web_name"] for i in unavail[:3])))
    else:
        checks.append(_chk(True, "All 4 bench players have at least a 75% chance of playing"))
    if dead:
        checks.append(_chk(False, "%s projects under %.1f points, a dead slot" % (", ".join(info[i]["web_name"] for i in dead[:3]), DEAD_SLOT)))
    else:
        checks.append(_chk(True, "No dead slot: every bench player projects at least %.1f" % DEAD_SLOT))
    checks.append(_chk(total >= minimum - 1e-9,
                       "Bench total %.1f is %s the %.1f minimum" % (total, "at or above" if total >= minimum - 1e-9 else "under", minimum)))
    ok = all(c["ok"] for c in checks)
    status = USE_BY if left == 0 else (PLAY if ok else NOT_YET)
    if left == 0:
        line = "This chip lapses at this deadline. Your bench would add about %.1f points, so use it or lose it." % total
    elif ok:
        line = "Your bench is strong and fit this week. Bench Boost adds about %.1f points." % total
    else:
        line = "Your bench would add about %.1f points, and not every check passes. We look again next week." % total
    out.update(status=status, gain_xpts=round(total, 2), checks=checks, line=line, swaps=swaps or [],
               bench=[{"id": i, "web_name": info[i]["web_name"], "xpts": round(sc[i], 2)} for i in bench],
               numbers={"bench_total": round(total, 2), "minimum": round(minimum, 2)},
               facts=_fx_facts(bench, info, gw, "bench"))
    return out


def fh_advice(inp, info, ids, pool, left, plan3_squad, plan3_hits, starters):
    gw = inp["gameweek"]
    out = {"chip": "freehit", "name": NAMES["freehit"], "next_check": "before the GW%d deadline" % (gw + 1)}
    cannot = {i for i in ids if (info[i].get("fx_counts") or [1])[0] == 0 or not _playable(info[i])}
    n_out = sum(1 for i in starters if i in cannot)
    budget = sum(info[i]["price"] for i in ids) + inp["bank"]
    obj = lambda team: _value(team, info, 0)
    fh_team = build_team(pool, info, budget, obj, start_ids=ids)
    fh_val = obj(fh_team)
    plan_val = _value(plan3_squad, info, 0) - 4.0 * plan3_hits
    margin = FH_MARGIN * loosen(left)
    beat = fh_val - plan_val
    # Wildcard flag: the problem lasts. Next week 3 or more of the same starters also cannot play
    # (no game, or under 75% to play on this round's flag). Fixture and availability facts only.
    nxt_out = sum(1 for i in starters
                  if (info[i].get("fx_counts") or [1, 1])[1] == 0 or not _playable(info[i]))
    flag = nxt_out >= FH_CONSIDER
    c1 = n_out >= FH_PLAY
    c1_consider = n_out == FH_CONSIDER
    checks = [
        _chk(c1, "%d of your 11 starters cannot play (no game, out, or under 75%% to play); needs %d%s"
             % (n_out, FH_PLAY, "" if not c1_consider else ", or 3 plus the next checks")),
        _chk(beat >= margin - 1e-9,
             "A Free Hit team beats your best transfers by %.1f points (needs %.0f)" % (beat, margin)),
        _chk(not flag, ("Next week %d of those starters still cannot play, so this looks like a Wildcard job" % nxt_out) if flag
             else "Next week your squad is fine, so this is not a Wildcard job"),
    ]
    good = (c1 or c1_consider) and beat >= margin - 1e-9 and not flag
    if c1_consider and good:
        status = CONSIDER
    elif c1 and good:
        status = PLAY
    else:
        status = NOT_YET
    if left == 0 and not flag:
        status = USE_BY
    if status == PLAY:
        line = "%d of your starters cannot play, and transfers cannot fix that. A Free Hit team scores about %.0f more points than your best transfers." % (n_out, beat)
    elif status == CONSIDER:
        line = "3 of your starters cannot play. Free Hit is worth considering: it beats your best transfers by %.0f points." % beat
    elif status == USE_BY:
        line = "This chip lapses at this deadline. A Free Hit team would score %.0f more points than your best transfers, so use it or lose it." % beat
    elif flag:
        line = "Your problem lasts beyond this week, so a Wildcard fits better than a Free Hit."
    else:
        line = "Transfers can handle this week. Free Hit is for weeks when 4 or more starters cannot play."
    out.update(status=status, gain_xpts=round(beat, 2), checks=checks, line=line, wildcard_fits_better=bool(flag),
               numbers={"unavailable_starters": n_out, "free_hit_team": round(fh_val, 2), "best_transfer_plan": round(plan_val, 2),
                        "margin": round(margin, 2), "next_week_unavailable_starters": nxt_out},
               team=_team_view(fh_team, ids, info, 0),
               facts=_blank_facts(ids, info, gw))
    return out


def _blank_facts(ids, info, gw):
    facts = []
    for k in (1, 2):
        n = sum(1 for i in ids if (info[i].get("fx_counts") or [1, 1, 1])[k] == 0)
        if n >= 3:
            facts.append("%d of your players have no game in GW%d." % (n, gw + k))
    return facts


def _team_view(team, current, info, g):
    cur = set(current)
    xi, bench, sc = _best_xi(team, info, g)
    cap = max(xi, key=lambda i: (sc[i], -i)) if xi else None
    row = lambda i: {"id": i, "web_name": info[i]["web_name"], "position": info[i]["position"],
                     "xpts": round(sc[i], 2), "is_new": i not in cur}
    return {"xi": [row(i) for i in xi], "bench": [row(i) for i in bench], "captain_id": cap,
            "in": [info[i]["web_name"] for i in team if i not in cur],
            "out": [info[i]["web_name"] for i in current if i not in set(team)]}


def problem_players(ids, info, g_bench_dead):
    """Squad players with a structural problem. Returns {id: [reasons]}."""
    probs = {}
    for i in ids:
        p, r = info[i], []
        if not _playable(p) and (p.get("cop") is not None and p["cop"] <= 25 or p.get("status") in ("i", "s", "u")):
            r.append("injured or unavailable")
        mins = p.get("min_last4")
        if isinstance(mins, (int, float)) and mins < WC_LOW_MINUTES and p.get("status") != "n":
            r.append("not playing much")
        fd = p.get("fix_diff5")
        if isinstance(fd, (int, float)) and fd > WC_WINDOW_FIX_DIFF:
            r.append("tough fixtures")
        if i in g_bench_dead:
            r.append("dead bench slot")
        if r:
            probs[i] = r
    return probs


def wc_advice(inp, info, ids, pool, left, base_squad):
    gw = inp["gameweek"]
    out = {"chip": "wildcard", "name": NAMES["wildcard"], "next_check": "before the GW%d deadline" % (gw + 1)}
    budget = sum(info[i]["price"] for i in ids) + inp["bank"]
    obj = lambda team: _wvalue(team, info)
    wc_team = build_team(pool, info, budget, obj, start_ids=ids)
    wc_w, base_w = obj(wc_team), obj(base_squad)
    gain = (wc_w - base_w) / base_w if base_w > 0 else 0.0
    per_gw = [(_value(wc_team, info, g) - _value(base_squad, info, g)) for g in range(3)]
    sustained = sum(1 for d in per_gw if d > 1e-9)
    changes = len(set(wc_team) - set(ids))
    _, bench, sc = _best_xi(base_squad, info)
    dead = {i for i in bench if sc[i] < DEAD_SLOT}
    probs = problem_players(ids, info, dead)
    need_gain = WC_GAIN * loosen(left)
    checks = [
        _chk(len(probs) >= WC_PROBLEMS, "%d %s with a problem, such as injuries, lost places or tough fixtures (needs %d)" % (len(probs), "player" if len(probs) == 1 else "players", WC_PROBLEMS)),
        _chk(gain >= need_gain - 1e-9, "A rebuild adds %.1f%% over 3 weeks (needs %.1f%%)" % (gain * 100, need_gain * 100)),
        _chk(changes >= WC_CHANGES, "%d changes to your squad (needs %d)" % (changes, WC_CHANGES)),
        _chk(sustained >= WC_SUSTAINED, "It beats your current team in %d of 3 weeks (needs %d)" % (sustained, WC_SUSTAINED)),
    ]
    ok = all(c["ok"] for c in checks)
    status = PLAY if ok else NOT_YET
    if left == 0 and gain >= WC_EXPIRY_GAIN:
        status = USE_BY
    if status == PLAY:
        line = "Your squad has %d problems and a rebuild adds %.1f%% over the next 3 weeks." % (len(probs), gain * 100)
    elif status == USE_BY:
        line = "This chip lapses at this deadline. A rebuild adds %.1f%%, so use it or lose it." % (gain * 100)
    else:
        line = "Your squad is in reasonable shape. A rebuild adds %.1f%% over the next 3 weeks, which is not enough yet." % (gain * 100)
    hits_saved = 4 * max(0, changes - inp["free_transfers"])
    out.update(status=status, gain_xpts=round(wc_w - base_w, 2), checks=checks, line=line,
               numbers={"gain_pct": round(gain * 100, 2), "changes": changes, "problems": len(probs),
                        "weeks_better": sustained, "hits_saved": hits_saved},
               problems=[{"id": i, "web_name": info[i]["web_name"], "reasons": r} for i, r in probs.items()],
               team=_team_view(wc_team, ids, info, 0), outlook="3-week outlook, provisional",
               facts=_blank_facts(ids, info, gw))
    return out


# ── entry point ─────────────────────────────────────────────────────────────────
def advise(inp: dict) -> dict:
    """
    inp:
      gameweek            int, the coming gameweek
      squad_ids           15 ids
      players             {id: info}; info = position, team, price (tenths), xpts [GW+1..GW+3],
                          web_name, status, cop (chance_of_playing_next_round), confidence,
                          chance (0..100), fx_counts [n, n, n], min_last4, fix_diff5
                          (pool candidates carry the same fields)
      pool                ids searched for Free Hit / Wildcard teams (default: all players)
      bank, free_transfers
      starter_ids         optional: the 11 the user starts (Free Hit counts unavailable ones); default: best XI by price
      chips_available     iterable of "3xc" | "bboost" | "freehit" | "wildcard"
      windows             {chip: gameweeks left AFTER this one (0 = this is the final gameweek)}
      plan_hit_free       planner result with no hits, for Bench Boost and the Wildcard baseline
      plan_3moves         planner result allowing up to 3 moves, hits included, for the Free Hit test
      league_best_xpts    best xPts this gameweek among eligible players in the league
      tc_min              optional Triple Captain minimum (default 7.0)
    Returns {"gameweek", "headline", "chips": [...], "warnings": [...]}.
    """
    _validate(inp)
    ids = list(inp["squad_ids"])
    info = inp["players"]
    pool = list(inp.get("pool") or info.keys())
    chips = set(inp.get("chips_available") or [])
    windows = inp.get("windows") or {}
    warn = []
    gaps = [info[i]["web_name"] for i in ids if any(v is None for v in (info[i].get("xpts") or [None]))]
    if gaps:
        warn.append("No projection for %s; valued at 0, so advice involving them is unreliable." % ", ".join(gaps[:4]))

    plan0 = inp.get("plan_hit_free") or {}
    plan3 = inp.get("plan_3moves") or {}
    rec0 = (plan0.get("recommendation") or {}).get("plan") or plan0.get("hold") or {}
    rec3 = (plan3.get("recommendation") or {}).get("plan") or plan3.get("hold") or {}
    sq0 = squad_after(ids, rec0) if rec0.get("transfers") else ids
    sq3 = squad_after(ids, rec3) if rec3.get("transfers") else ids
    hits3 = int(rec3.get("hits", 0)) if rec3.get("transfers") else 0

    starters = list(inp.get("starter_ids") or [])
    if len(starters) != 11 or any(i not in ids for i in starters) or len(set(starters)) != 11:
        # fallback: the XI the user most likely fields, by price (expensive players are usually regulars)
        starters = _price_xi(ids, info)
    prep_sq, prep_swaps, _ = prep_bench(sq0, info, pool, (plan0.get("recommendation") or {}).get("plan", {}).get("bank_after", inp["bank"])
                                        if rec0.get("transfers") else inp["bank"],
                                        inp["free_transfers"] - len(rec0.get("transfers", [])) if rec0.get("transfers") else inp["free_transfers"])
    results = []
    for chip in CHIPS:
        if chip not in chips or chip not in windows:
            results.append({"chip": chip, "name": NAMES[chip], "status": NA,
                            "line": "Not on your list." if chip not in chips else "This chip has no open window.",
                            "checks": [], "facts": []})
            continue
        left = windows[chip]
        if chip == "3xc":
            results.append(tc_advice(dict(inp, league_best_xpts=inp.get("league_best_xpts")), info, ids, left))
        elif chip == "bboost":
            results.append(bb_advice(inp, info, ids, prep_sq, left, prep_swaps))
        elif chip == "freehit":
            results.append(fh_advice(inp, info, ids, pool, left, sq3, hits3, starters))
        else:
            results.append(wc_advice(inp, info, ids, pool, left, sq0))

    # one chip per gameweek: the larger gain is the headline
    live = [r for r in results if r["status"] in (PLAY, USE_BY)]
    headline = {"status": "HOLD_ALL", "text": "Hold your chips. We check again next gameweek.", "chip": None}
    if live:
        win = max(live, key=lambda r: (r["status"] == USE_BY, r.get("gain_xpts", 0.0)))
        win_status = win["status"]
        for r in live:
            if r is not win:
                lapses = r["status"] == USE_BY
                r["status"] = NOT_YET
                r["line"] = ("%s is better this week, and only one chip can be played per gameweek%s." %
                             (win["name"], ". This chip will lapse at the deadline" if lapses else ""))
                r["superseded_by"] = win["chip"]
        headline = {"status": win_status, "chip": win["chip"],
                    "text": ("Play %s now" % win["name"]) if win_status == PLAY else ("Use %s before it lapses" % win["name"])}
        if win["chip"] == "wildcard":
            win["note"] = "After a Wildcard, Bench Boost or Triple Captain are worth checking the following week on the new squad."
    return {"gameweek": inp["gameweek"], "headline": headline, "chips": results, "warnings": warn,
            "label": "Provisional thresholds. Rechecked every gameweek."}
