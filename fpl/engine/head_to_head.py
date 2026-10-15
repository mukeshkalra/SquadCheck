"""
Head-to-head comparison and verdict for planner transfers.

Presentation only: every figure is read from FPL bootstrap / element-summary data or from the
planner's own projections. Nothing here feeds back into plan ranking.

Verdict cut-offs are rule-of-thumb heuristics, NOT calibrated against past outcomes
(`calibrated: false` is sent to the client so the page can say so).
"""
from typing import Optional

VERDICT_CALIBRATED = False
CLEAR_MIN_GAIN = 4.0        # net next-gameweek gain for "Clear edge"
SMALL_MIN_GAIN = 1.5        # net next-gameweek gain for "Small edge"
XG_EDGE_MIN_ABS = 0.4       # season xG gap that counts as "better chances"
XG_EDGE_MIN_REL = 0.2
FIXTURE_RATIO_BAND = 0.10   # opponent within ±10% of the league average reads as "average"
FIXTURE_PRO_CON_GAP = 0.15
POINTS_GAP_NOTE = 8         # season-points gap worth a note
PRICE_GAP_NOTE = 10         # £1.0m, in tenths
HOT_GOALS_OVER_XG = 1.5
COLD_GOALS_UNDER_XG = 1.0

_GK, _DEF = 1, 2


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _played_rows(summary) -> list:
    rows = (summary or {}).get("history")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("minutes") is not None
            and r.get("team_h_score", 0) is not None and r.get("team_a_score", 0) is not None]


def _fixture_view(el, summary, nxt, params, teams):
    fx_all = (summary or {}).get("fixtures")
    fxs = [x for x in fx_all if isinstance(x, dict) and x.get("event") == nxt] if isinstance(fx_all, list) and nxt is not None else []
    if not fxs:
        return None
    fx = fxs[0]
    home = bool(fx.get("is_home"))
    opp = fx.get("team_a") if home else fx.get("team_h")
    short = (teams.get(opp) or (None, None))[1] or "?"
    fdr = fx.get("difficulty")
    out = {"opp": short, "home": home, "fdr": fdr if isinstance(fdr, int) else None, "extra": len(fxs) - 1,
           "ratio": None, "tone": "neutral", "text": None}
    defensive = el.get("element_type") in (_GK, _DEF)   # defenders care about the opponent's attack, attackers about its defence
    have = (params.get("team_xgf" if defensive else "team_gkp_xgc") or {}).get(opp)
    avg = params.get("league_avg_xgf" if defensive else "league_avg_xgc")
    if isinstance(have, (int, float)) and isinstance(avg, (int, float)) and avg > 0:
        ratio = have / avg
        pct = int(round(abs(ratio - 1) * 100))
        out["ratio"] = round(ratio, 2)
        if ratio > 1 + FIXTURE_RATIO_BAND:
            out["tone"] = "bad" if defensive else "good"
            out["text"] = ("Strong attack: creates %d%% more chances than average" if defensive
                           else "Leaky defence: gives up %d%% more chances than average") % pct
        elif ratio < 1 - FIXTURE_RATIO_BAND:
            out["tone"] = "good" if defensive else "bad"
            out["text"] = ("Weak attack: creates %d%% fewer chances than average" if defensive
                           else "Tight defence: gives up %d%% fewer chances than average") % pct
        else:
            out["text"] = "Average on chances " + ("created" if defensive else "conceded")
    out["fdr_conflict"] = bool(out["fdr"] is not None and ((out["fdr"] >= 4 and out["tone"] == "good")
                                                           or (out["fdr"] <= 2 and out["tone"] == "bad")))
    return out


def _side(pid, by_id, summaries, players, params, teams, nxt) -> dict:
    el = by_id.get(pid) or {}
    summ = summaries.get(pid)
    rows = _played_rows(summ)
    last3, last6 = rows[-3:], rows[-6:]
    xp = (players.get(pid) or {}).get("xpts")
    return {
        "name": el.get("web_name", ""), "pos": el.get("element_type"), "price": el.get("now_cost"),
        "xp": xp[0] if isinstance(xp, list) and xp and isinstance(xp[0], (int, float)) else None,
        "points": el.get("total_points") if isinstance(el.get("total_points"), int) else None,
        "last3": sum(r.get("total_points") or 0 for r in last3) if last3 else None,
        "goals": el.get("goals_scored") if isinstance(el.get("goals_scored"), int) else None,
        "xg": _f(el.get("expected_goals")),
        "starts": sum(1 for r in last6 if (r.get("starts") or 0) >= 1) if last6 else None, "games": len(last6),
        "owned": _f(el.get("selected_by_percent")), "status": el.get("status"),
        "fixture": _fixture_view(el, summ, nxt, params, teams),
    }


def _edge(a, b, higher_is_better=True, tol=0.0):
    if a is None or b is None or abs(a - b) <= tol:
        return None
    return "out" if (a > b) == higher_is_better else "in"


def _have_xg(o, i) -> bool:
    return o["pos"] != _GK and i["pos"] != _GK and None not in (o["xg"], i["xg"])


def compare_pair(out_id, in_id, by_id, summaries, players, params, teams, nxt) -> dict:
    o = _side(out_id, by_id, summaries, players, params, teams, nxt)
    i = _side(in_id, by_id, summaries, players, params, teams, nxt)
    fo, fi = o["fixture"], i["fixture"]
    attackers = o["pos"] not in (_GK, _DEF)      # attackers want a leaky opponent; defenders a weak attack
    rows = []

    if o["xp"] is not None and i["xp"] is not None:
        rows.append({"key": "xpts", "label": "Our projection, next gameweek", "out": "%.1f" % o["xp"],
                     "in": "%.1f" % i["xp"], "edge": _edge(o["xp"], i["xp"], tol=0.05)})
    if o["points"] is not None and i["points"] is not None:
        l3 = lambda s: " (%d last 3)" % s["last3"] if s["last3"] is not None else ""
        rows.append({"key": "points", "label": "Points this season", "out": "%d%s" % (o["points"], l3(o)),
                     "in": "%d%s" % (i["points"], l3(i)), "edge": _edge(o["points"], i["points"])})
    if _have_xg(o, i) and None not in (o["goals"], i["goals"]):
        rows.append({"key": "chances", "label": "Goals vs chances (xG)",
                     "out": "%d %s · %.1f xG" % (o["goals"], "goal" if o["goals"] == 1 else "goals", o["xg"]), "in": "%d %s · %.1f xG" % (i["goals"], "goal" if i["goals"] == 1 else "goals", i["xg"]),
                     "edge": _edge(o["xg"], i["xg"], tol=0.05)})
    if o["starts"] is not None and i["starts"] is not None and o["games"] and i["games"]:
        rows.append({"key": "starts", "label": "Starts in last 6 games", "out": "%d of %d" % (o["starts"], o["games"]),
                     "in": "%d of %d" % (i["starts"], i["games"]),
                     "edge": _edge(o["starts"] / o["games"], i["starts"] / i["games"], tol=0.05)})
    if fo and fi:
        cell = lambda f: "%s (%s)%s%s" % (f["opp"], "H" if f["home"] else "A", " · FPL " + str(f["fdr"]) if f["fdr"] else "",
                                          " +%d more" % f["extra"] if f["extra"] else "")
        edge = None if fo["ratio"] is None or fi["ratio"] is None else _edge(fo["ratio"], fi["ratio"], higher_is_better=attackers, tol=0.05)
        rows.append({"key": "fixture", "label": "Next fixture", "out": cell(fo), "in": cell(fi), "edge": edge})
    if o["price"] is not None and i["price"] is not None:
        rows.append({"key": "price", "label": "Price", "out": "£%.1fm" % (o["price"] / 10), "in": "£%.1fm" % (i["price"] / 10), "edge": None})
    if o["owned"] is not None and i["owned"] is not None:
        rows.append({"key": "owned", "label": "Owned by", "out": "%.1f%%" % o["owned"], "in": "%.1f%%" % i["owned"], "edge": None})

    fixture_note = None
    for s in (i, o):
        f = s["fixture"]
        if f and f["fdr_conflict"]:
            fixture_note = ("FPL rates %s's fixture %d, but our numbers read it differently (%s). Our projection uses the chances "
                            "actually created and conceded, so the two views can differ." % (s["name"], f["fdr"], (f["text"] or "").lower()))
            break

    pros, cons = [], []
    if _have_xg(o, i):
        if i["xg"] - o["xg"] >= XG_EDGE_MIN_ABS and i["xg"] >= o["xg"] * (1 + XG_EDGE_MIN_REL):
            pros.append("%s has had better chances than %s this season (%.1f xG to %.1f)." % (i["name"], o["name"], i["xg"], o["xg"]))
        elif o["xg"] - i["xg"] >= XG_EDGE_MIN_ABS and o["xg"] >= i["xg"] * (1 + XG_EDGE_MIN_REL):
            cons.append("%s has had better chances than %s this season (%.1f xG to %.1f)." % (o["name"], i["name"], o["xg"], i["xg"]))
    if fo and fi and fo["ratio"] is not None and fi["ratio"] is not None:
        d = (fi["ratio"] - fo["ratio"]) * (1 if attackers else -1)
        if d >= FIXTURE_PRO_CON_GAP:
            pros.append("Easier next fixture on our numbers: %s (%s)." % (fi["opp"], (fi["text"] or "").lower()))
        elif d <= -FIXTURE_PRO_CON_GAP:
            cons.append("Harder next fixture on our numbers: %s (%s)." % (fi["opp"], (fi["text"] or "").lower()))
    if o["points"] is not None and i["points"] is not None:
        if o["points"] - i["points"] >= POINTS_GAP_NOTE:
            cons.append("%s has %d points this season to %s's %d." % (o["name"], o["points"], i["name"], i["points"]))
        elif i["points"] - o["points"] >= POINTS_GAP_NOTE:
            pros.append("%s has %d points this season to %s's %d." % (i["name"], i["points"], o["name"], o["points"]))
    if o["pos"] != _GK and None not in (o["goals"], o["xg"]) and o["goals"] - o["xg"] >= HOT_GOALS_OVER_XG:
        pros.append("%s's %d goals are well ahead of his %.1f xG, so some of that scoring may not repeat." % (o["name"], o["goals"], o["xg"]))
    if i["pos"] != _GK and None not in (i["goals"], i["xg"]) and i["xg"] - i["goals"] >= COLD_GOALS_UNDER_XG:
        pros.append("%s's %.1f xG is ahead of his %d goals, so goals are due." % (i["name"], i["xg"], i["goals"]))
    if None not in (i["starts"], o["starts"]) and i["games"] and o["games"] and i["starts"] / i["games"] + 0.15 < o["starts"] / o["games"]:
        cons.append("%s has started %d of his last %d, fewer than %s (%d of %d)." % (i["name"], i["starts"], i["games"], o["name"], o["starts"], o["games"]))
    if i["status"] not in (None, "a"):
        cons.append("%s is not fully fit according to FPL." % i["name"])
    if None not in (o["price"], i["price"]) and o["price"] - i["price"] >= PRICE_GAP_NOTE:
        cons.append("%s is £%.1fm cheaper, which only helps if you can spend it." % (i["name"], (o["price"] - i["price"]) / 10))

    return {"rows": rows, "fixture_note": fixture_note, "for": pros[:3], "against": cons[:3],
            "out_name": o["name"], "in_name": i["name"]}


def plan_verdict(plan: dict) -> Optional[dict]:
    """Tier for a plan with at least one transfer, from the net next-gameweek gain and the provisional
    three-gameweek outlook. A label only: it never changes ranking."""
    if not plan.get("transfers"):
        return None
    net = plan.get("projected_gain_vs_hold")
    if not isinstance(net, (int, float)):
        return None
    ol = (plan.get("outlook") or {}).get("vs_hold")
    ok = isinstance(ol, list) and len(ol) == 3 and all(isinstance(x, (int, float)) for x in ol)
    later = ol[1] + ol[2] if ok else None
    base = {"net": round(net, 2), "later_weeks": None if later is None else round(later, 2), "calibrated": VERDICT_CALIBRATED}
    if net <= 0:
        return {**base, "tier": "none", "label": "Not worth it", "text": "Projected no better than holding."}
    if ok and later < 0 and ol[0] + later < SMALL_MIN_GAIN:      # the later weeks give back most of the gain
        return {**base, "tier": "marginal", "label": "Marginal",
                "text": "Wins next gameweek but gives most of it back over the following two. Only worth it if you will use the money saved."}
    if net >= CLEAR_MIN_GAIN and (later is None or later >= 0):
        return {**base, "tier": "clear", "label": "Clear edge",
                "text": "A big projected gain, well beyond the usual miss of about 1.75 points on a single player."}
    if net >= SMALL_MIN_GAIN:
        return {**base, "tier": "small", "label": "Small edge",
                "text": "A real but small projected gain: about one typical forecast miss. A fair use of a free transfer, not a sure thing."}
    return {**base, "tier": "marginal", "label": "Marginal",
            "text": "Too small to tell apart from luck. Consider holding unless you will use the money saved."}


def attach(obj, by_id, summaries, players, params, teams, nxt) -> None:
    """Add `compare` to every transfer and `verdict` to every plan, in place."""
    if isinstance(obj, dict):
        ts = obj.get("transfers")
        if isinstance(ts, list) and "projected_gain_vs_hold" in obj and "verdict" not in obj:
            for t in ts:
                if isinstance(t, dict) and "compare" not in t and isinstance(t.get("out"), dict) and isinstance(t.get("in"), dict):
                    t["compare"] = compare_pair(t["out"]["id"], t["in"]["id"], by_id, summaries, players, params, teams, nxt)
            obj["verdict"] = plan_verdict(obj)
        for v in list(obj.values()):
            attach(v, by_id, summaries, players, params, teams, nxt)
    elif isinstance(obj, list):
        for v in obj:
            attach(v, by_id, summaries, players, params, teams, nxt)
