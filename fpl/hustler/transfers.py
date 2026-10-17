"""
Transfer options for a Hustler post: what the Transfer Planner finds for the poster's squad.

Every post gets an independent view (the planner's best moves). When the post names a squad
player to move on, it also gets that player's best replacements, and any move the poster
proposes is judged too. The poster's real free transfers and bank are unknown, so the
assumptions are fixed and always stated: 2 free transfers, the bank written in the post (else
£0) and no points hit. Nothing here raises: a failure returns None and the draft goes ahead
without transfer options.
"""
import re
import logging

from fpl.engine.transfer_adapter import (
    ApiError, CANDIDATES_PER_POSITION, FplDataSource, build_response, parse_request,
    shortlist_candidates, project_players,
)
from fpl.hustler.mentions import _spans

log = logging.getLogger(__name__)

FREE_TRANSFERS = 2
MAX_SELL = 2             # squad players named for sale
MAX_REPLACEMENTS = 4     # options shown per sold player
MAX_NAMED_MOVES = 3      # "A for B" moves the poster proposes
MAX_INDEPENDENT = 3      # plans shown in the independent view
_TRY = 8                 # candidates tried per sold player, ranked by next-gameweek projection

_INTENT = re.compile(
    r"\b(replace\w*|sell\w*|swap\w*|instead of|upgrade\w*|ditch\w*|dump\w*|get rid|move on|transfer\w*|"
    r"free transfer|\d\s?ft|ft|who (?:should|to|do|can)|bring in|best option|what to do)\b", re.IGNORECASE)
_BANK = re.compile(r"£\s*(\d+(?:\.\d+)?)\s*m?\b[^.\n]{0,15}?(?:itb|in the bank|in bank)", re.IGNORECASE)

_source = None


def live_source():
    """One shared FPL data source (cached bootstrap and player histories)."""
    global _source
    if _source is None:
        _source = FplDataSource()
    return _source


def wants_transfer(text):
    return bool(_INTENT.search(text or ""))


def bank_from_post(text):
    m = _BANK.search(text or "")
    if not m:
        return 0.0
    value = round(float(m.group(1)), 1)
    return value if 0 <= value <= 100 else 0.0


def sell_candidates(text, bootstrap, squad_ids):
    """
    Squad players the post names, as [(player_id, note)], in order of appearance. A name shared with
    other players still counts when exactly one of them is in the squad; the note then says so.
    """
    squad = set(squad_ids)
    teams = {t["id"]: t.get("short_name", "") for t in bootstrap.get("teams", [])}
    found, seen = [], set()
    for span in _spans(text or "", bootstrap.get("elements", [])):
        mine = [h for h in span.hits if h["id"] in squad]
        if len(mine) != 1 or mine[0]["id"] in seen:
            continue
        seen.add(mine[0]["id"])
        note = None
        if len(span.hits) > 1:
            note = "'%s' also matches other players; assumed to be %s (%s), who is in the squad" % (
                span.text, mine[0].get("web_name", ""), teams.get(mine[0].get("team"), ""))
        found.append((mine[0]["id"], note))
    return found[:MAX_SELL]


def _side(p):
    return {"name": p.get("web_name"), "club": p.get("team_short"), "price_m": round(p.get("price", 0) / 10, 1)}


def _row_facts(compare):
    return [{"what": r.get("label"), "sold": r.get("out"), "bought": r.get("in")} for r in (compare or {}).get("rows", [])]


def _single_option(plan, sell_id):
    """One transfer out of a plan, with its numbers; None when the plan does not sell that player."""
    t = next((t for t in plan.get("transfers", []) if t["out"]["id"] == sell_id), None)
    if t is None:
        return None
    verdict = plan.get("verdict") or {}
    cmp_ = t.get("compare") or {}
    return {"sell": _side(t["out"]), "buy": _side(t["in"]),
            "projected_gain_next_gameweek": plan.get("projected_gain_vs_hold"),
            "projected_gain_later_weeks": verdict.get("later_weeks"),
            "side_by_side": _row_facts(cmp_), "points_for": list(cmp_.get("for", [])),
            "points_against": list(cmp_.get("against", [])), "n_transfers": plan.get("n_transfers")}


def _request(squad_ids, bank_m, **extra):
    return parse_request({"squad_ids": list(squad_ids), "bank_m": bank_m, "free_transfers": FREE_TRANSFERS,
                          "max_hits": 0, **extra})


def _requested_plan(response):
    return ((response.get("plan") or {}).get("requested") or {}).get("plan")


def _replacements(source, squad_ids, sell_id, bank_m):
    """Best replacements for one sold player: the planner's own pick first, then the next-ranked candidates."""
    best = build_response(_request(squad_ids, bank_m, out_ids=[sell_id]), source)
    options = []
    first = _single_option(_requested_plan(best) or {}, sell_id)
    if first:
        options.append(first)
    bootstrap = source.bootstrap()
    by_id = {e["id"]: e for e in bootstrap["elements"]}
    sold = by_id[sell_id]
    ids = [p for p in shortlist_candidates(bootstrap, list(squad_ids), per_position=CANDIDATES_PER_POSITION)
           if p not in set(squad_ids) and by_id.get(p, {}).get("element_type") == sold["element_type"]]
    summaries, _ = source.summaries(ids)
    ranked = project_players(bootstrap, summaries, source.params(bootstrap), ids)
    names = {o["buy"]["name"] for o in options}
    order = sorted((p for p in ranked if (ranked[p].get("xpts") or [None])[0] is not None),
                   key=lambda p: -ranked[p]["xpts"][0])
    for pid in order[:_TRY]:
        if len(options) >= MAX_REPLACEMENTS:
            break
        if by_id[pid].get("web_name") in names:
            continue
        try:
            opt = _single_option(_requested_plan(build_response(_request(squad_ids, bank_m, moves=[[sell_id, pid]]), source)) or {}, sell_id)
        except ApiError:
            continue                      # illegal for this squad (budget, club limit): not an option
        if opt:
            names.add(opt["buy"]["name"])
            options.append(opt)
    options.sort(key=lambda o: -(o["projected_gain_next_gameweek"] or -99))
    return options


def _independent(source, squad_ids, bank_m):
    response = build_response(_request(squad_ids, bank_m), source)
    plan = response.get("plan") or {}
    rec = plan.get("recommendation") or {}
    plans = []
    for alt in (plan.get("alternatives") or [])[:MAX_INDEPENDENT]:
        verdict = alt.get("verdict") or {}
        plans.append({"moves": [{"sell": _side(t["out"]), "buy": _side(t["in"])} for t in alt.get("transfers", [])],
                      "projected_gain_next_gameweek": alt.get("projected_gain_vs_hold"),
                      "projected_gain_later_weeks": verdict.get("later_weeks")})
    return {"best_action": rec.get("action"), "plans": plans}


def transfer_options(source, squad_ids, text, mentions=(), bootstrap=None):
    """
    The transfer evidence for one post, or None when it cannot be built.
    {"assumptions": str, "independent": {...}, "sell": [{"sold": ..., "assumption": str|None, "options": [...]}],
     "poster_moves": [option, ...]}
    """
    try:
        bootstrap = bootstrap or source.bootstrap()
        bank = bank_from_post(text)
        out = {"assumptions": "Assumes %d free transfers, %s in the bank (%s) and no points hit. These are assumptions: "
                              "the poster's real numbers may differ." % (
                                  FREE_TRANSFERS, "£%.1fm" % bank,
                                  "as stated in the post" if bank else "none stated, so £0"),
               "bank_m": bank, "free_transfers": FREE_TRANSFERS}
        out["independent"] = _independent(source, squad_ids, bank)
        by_id = {e["id"]: e for e in bootstrap["elements"]}
        teams = {t["id"]: t.get("short_name", "") for t in bootstrap.get("teams", [])}
        out["sell"] = []
        if wants_transfer(text):
            for pid, note in sell_candidates(text, bootstrap, squad_ids):
                try:
                    out["sell"].append({"sold": _side({"web_name": by_id[pid]["web_name"], "price": by_id[pid]["now_cost"],
                                                       "team_short": teams.get(by_id[pid].get("team"), "")}), "assumption": note,
                                        "options": _replacements(source, squad_ids, pid, bank)})
                except ApiError as exc:
                    log.warning("replacements failed for %s: %s", pid, exc)
        moves = []
        web = {by_id[p]["web_name"]: p for p in squad_ids if p in by_id}
        for m in mentions:
            sold_name = m.relation.replace("proposed replacement for ", "", 1) if m.relation.startswith("proposed replacement for ") else None
            if sold_name in web and getattr(m, "player_id", None):
                moves.append((web[sold_name], m.player_id))
        out["poster_moves"] = []
        for sell_id, buy_id in moves[:MAX_NAMED_MOVES]:
            try:
                opt = _single_option(_requested_plan(build_response(_request(squad_ids, bank, moves=[[sell_id, buy_id]]), source)) or {}, sell_id)
            except ApiError:
                opt = None
            if opt:
                out["poster_moves"].append(opt)
        return out
    except Exception:
        log.exception("transfer options failed; drafting without them")
        return None


def live_view(squad_ids, text, mentions=(), bootstrap=None):
    """Default hook for analyse(): the real planner on live FPL data."""
    return transfer_options(live_source(), squad_ids, text, mentions, bootstrap)
