"""
SquadCheck FPL Projection Engine — V0.2  (V1 release, frozen)

Computes expected FPL points (xPts) for a player in an upcoming Gameweek.
Every component maps directly to an FPL scoring mechanic.

Entry points
------------
build_params(bootstrap)                           -> params dict (call once per fetch)
compute_xpts(player, history, fixture, params)    -> result dict

Blank GW  : pass fixture=None  -> xPts = 0
Double GW : call twice (one fixture each) and sum xPts

V0.2 changes from V0.1
-----------------------
- FIXTURE_ADJ lookup table removed entirely.
- Fixture context now derived from current-season team signals:
    atk_scale  = (opponent_defensive_quality / league_avg) × home_factor
    CS lambda  = our_defensive_quality × opponent_attacking_quality / home_factor
- Opponent team id is resolved from fixture["team_h"/"team_a"].
  If absent (legacy fixture dict without those fields) the model falls back to a
  league-average opponent (atk_scale = home_factor; CS lambda unchanged).
- HOME_ADV=1.10 replaces any implicit home benefit that was baked into difficulty.
- build_params() gains three new keys:
    team_gkp_starts, team_xgf, league_avg_xgf
- FPL strength_attack_home/away and strength_defence_home/away are confirmed zero
  in the live API and are not used.

See fpl/PROJECTION_ENGINE_V0_1.md and the V0.2 design review for the full
coefficient registry and evaluation framework.
"""

import math
from statistics import median as _median
from typing import Optional

# ── FPL SCORING RULES (exact; unchanged from V0.1) ──────────────────────────
GOAL_PTS     = {1: 6, 2: 6, 3: 5, 4: 4}
ASSIST_PTS   = 3
CS_PTS       = {1: 6, 2: 6, 3: 1, 4: 0}
SAVE_PTS_PER = 3

# ── V0.2 FIXTURE CONSTANT ────────────────────────────────────────────────────
# Home-field advantage in expected goals.
# Heuristic: mean(atk_scale across all home fixtures) ≈ HOME_ADV (validated GW6).
# The FPL strength_attack/defence fields are all 0 in the live API;
# strength_overall_home/away are populated (1–5) but encode pre-season team rank,
# not a clean home/away split.  HOME_ADV therefore remains an explicit heuristic.
HOME_ADV = 1.10   # [heuristic]

# ── POSITIONAL DEFAULTS (new players; V0.1 heuristics, unchanged) ────────────
_POS_DEFAULT_MINS = {1: 60, 2: 55, 3: 50, 4: 45}

# ── BONUS CAP ────────────────────────────────────────────────────────────────
_BONUS_CAP = 1.5   # [heuristic]


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _f(v) -> float:
    """Coerce API string-or-number to float."""
    return float(v)


def E_floor_X2(lam: float) -> float:
    """
    E[floor(X / 2)] for X ~ Poisson(lam).
    FPL deducts 1 point per 2 goals conceded (floor division).
    Iterates to convergence (p_k < 1e-9, ~15 terms for typical λ).
    Returns 0.0 for lam ≤ 0.
    """
    if lam <= 0.0:
        return 0.0
    result = 0.0
    p_k    = math.exp(-lam)
    k      = 0
    while p_k > 1e-9:
        result += (k // 2) * p_k
        k      += 1
        p_k    *= lam / k
    return result


# ─────────────────────────────────────────────────────────────────────────────
# BOOTSTRAP PREPARATION  (call once per session)
# ─────────────────────────────────────────────────────────────────────────────

def _build_team_gkp_xgc(elements: list) -> dict:
    """
    Map each team_id to the primary GKP's expected_goals_conceded_per_90.

    Primary GKP = most starts; tie-break on minutes.
    GKPs play every minute, so their xGC_p90 ≈ the team's season defensive rate.
    Returns {team_id: float}.
    """
    best: dict = {}
    for e in elements:
        if e.get("element_type") != 1:
            continue
        if e.get("removed", False):
            continue
        tid = e["team"]
        key = (_f(e.get("starts", 0)), _f(e.get("minutes", 0)))
        cur = best.get(tid)
        if cur is None or key > (_f(cur.get("starts", 0)), _f(cur.get("minutes", 0))):
            best[tid] = e
    return {tid: _f(e["expected_goals_conceded_per_90"]) for tid, e in best.items()}


def _build_team_gkp_starts(elements: list) -> dict:
    """
    Map each team_id to the primary GKP's starts count.

    Shares the same primary-GKP selection logic as _build_team_gkp_xgc.
    Used as n for the n/(n+2) opponent damping weight in the V0.2 fixture model:
    low starts → more uncertainty → opponent xGC pulled harder toward league average.
    Returns {team_id: int}.
    """
    best: dict = {}
    for e in elements:
        if e.get("element_type") != 1:
            continue
        if e.get("removed", False):
            continue
        tid = e["team"]
        key = (_f(e.get("starts", 0)), _f(e.get("minutes", 0)))
        cur = best.get(tid)
        if cur is None or key > (_f(cur.get("starts", 0)), _f(cur.get("minutes", 0))):
            best[tid] = e
    return {tid: int(e.get("starts", 0)) for tid, e in best.items()}


def _build_team_xgf(elements: list, min_starts: int = 3) -> dict:
    """
    Team attacking proxy = sum of expected_goals_per_90 for outfield starters.

    Outfield only (element_type ≠ 1).  Same min_starts filter as positional baselines.
    Used as the opponent attacking quality signal for CS/GC estimation.
    Recomputed each GW.  Returns {team_id: float}.
    """
    xgf: dict = {}
    for e in elements:
        if e.get("element_type") == 1:
            continue
        if _f(e.get("starts", 0)) < min_starts:
            continue
        tid = e["team"]
        xgf[tid] = xgf.get(tid, 0.0) + _f(e["expected_goals_per_90"])
    return xgf


def _compute_league_avg_xgc(team_gkp_xgc: dict) -> float:
    """Mean xGC_p90 across all primary GKPs. Damping target for defensive quality."""
    vals = list(team_gkp_xgc.values())
    return sum(vals) / len(vals) if vals else 1.50


def _compute_league_avg_xgf(team_xgf: dict) -> float:
    """Mean xGF proxy across all teams. Denominator for opponent attacking quality."""
    vals = list(team_xgf.values())
    return sum(vals) / len(vals) if vals else 1.57


def _compute_positional_baselines(elements: list, min_starts: int = 3):
    """
    Median xG_p90 and xA_p90 for starters at each position.
    Same population (starts ≥ min_starts) applied to both xG and xA.
    GKP floor: 0.01 (median is 0.000 at GW6; floor avoids exact-zero baseline).
    Returns (pos_xG_base, pos_xA_base) keyed by element_type.
    """
    xg_by_pos: dict = {1: [], 2: [], 3: [], 4: []}
    xa_by_pos: dict = {1: [], 2: [], 3: [], 4: []}
    for e in elements:
        pos = e.get("element_type")
        if pos not in xg_by_pos:
            continue
        if _f(e.get("starts", 0)) < min_starts:
            continue
        xg_by_pos[pos].append(_f(e["expected_goals_per_90"]))
        xa_by_pos[pos].append(_f(e["expected_assists_per_90"]))

    xG_base, xA_base = {}, {}
    for pos in (1, 2, 3, 4):
        xG_base[pos] = max(0.01, _median(xg_by_pos[pos])) if xg_by_pos[pos] else 0.01
        xA_base[pos] = max(0.01, _median(xa_by_pos[pos])) if xa_by_pos[pos] else 0.01
    return xG_base, xA_base


def build_params(bootstrap: dict) -> dict:
    """
    Build the params dict from a bootstrap-static API response.
    Call once per session; pass to every compute_xpts call.

    V0.2 keys (new):
        team_gkp_starts : {team_id: int}   primary GKP starts (for opponent damping)
        team_xgf        : {team_id: float}  team attacking proxy (Σ outfield starter xG)
        league_avg_xgf  : float             mean of team_xgf values

    V0.1 keys (unchanged):
        team_gkp_xgc    : {team_id: float}
        league_avg_xgc  : float
        pos_xG_base     : {1..4: float}
        pos_xA_base     : {1..4: float}
    """
    elements        = bootstrap["elements"]
    team_gkp_xgc    = _build_team_gkp_xgc(elements)
    team_gkp_starts = _build_team_gkp_starts(elements)
    league_avg_xgc  = _compute_league_avg_xgc(team_gkp_xgc)
    xG_base, xA_base = _compute_positional_baselines(elements)
    team_xgf        = _build_team_xgf(elements)
    league_avg_xgf  = _compute_league_avg_xgf(team_xgf)
    return {
        "team_gkp_xgc":    team_gkp_xgc,
        "team_gkp_starts": team_gkp_starts,
        "league_avg_xgc":  league_avg_xgc,
        "team_xgf":        team_xgf,
        "league_avg_xgf":  league_avg_xgf,
        "pos_xG_base":     xG_base,
        "pos_xA_base":     xA_base,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CONFIDENCE
# ─────────────────────────────────────────────────────────────────────────────

def _minutes_conf(status, cop_next, recent_starts_3, recent_avg_mins, n_gws) -> str:
    if n_gws <= 1 or recent_starts_3 <= 1:
        return "LOW"
    if status in ("i", "d") and (cop_next is None or cop_next <= 50):
        return "LOW"
    if cop_next is not None and cop_next <= 50:
        return "LOW"
    if cop_next is not None and cop_next <= 75:
        return "MEDIUM"
    if recent_starts_3 <= 2 or recent_avg_mins < 55:
        return "MEDIUM"
    if recent_avg_mins < 75:
        return "MEDIUM"
    return "HIGH"


def _projection_conf(n_gws, starts) -> str:
    if n_gws <= 2:
        return "LOW"
    if n_gws < 5 or starts < n_gws:
        return "MEDIUM"
    return "HIGH"


def _overall_conf(mc, pc) -> str:
    rank = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}
    r = min(rank[mc], rank[pc])
    return "HIGH" if r == 2 else ("MEDIUM" if r == 1 else "LOW")


# ─────────────────────────────────────────────────────────────────────────────
# DRIVERS / RISKS
# ─────────────────────────────────────────────────────────────────────────────

def _drivers_risks(player, pos, xMins, P_app, P_60plus, p_cs, lam,
                   xG_d, xA_d, atk_scale, n_gws) -> tuple:
    starts = player.get("starts", 0)
    drivers, risks = [], []

    if xMins >= 85 and P_app >= 1.0:
        drivers.append(
            "Nailed starter — %d/%d starts, %d mins" % (
                starts, n_gws, player.get("minutes", 0)))
    elif xMins >= 60:
        drivers.append("Regular starter — %d/%d starts" % (starts, n_gws))

    if xG_d >= 0.40:
        drivers.append("Strong xG rate — %.3f per 90 (damped)" % xG_d)
    if xA_d >= 0.15:
        drivers.append("Strong xA rate — %.3f per 90 (damped)" % xA_d)
    if pos in (1, 2) and p_cs >= 0.40:
        drivers.append("Good CS prospect — P(CS)=%.3f" % p_cs)
    if player.get("penalties_order") == 1:
        drivers.append("Confirmed penalty taker")
    if atk_scale >= 1.20:
        drivers.append("Strong attacking fixture — atk_scale=%.3f" % atk_scale)
    elif atk_scale >= 1.08:
        drivers.append("Favourable attacking fixture — atk_scale=%.3f" % atk_scale)

    cop = player.get("chance_of_playing_next_round")
    if cop is not None:
        risks.append("Injury/doubt — %d%% chance of playing" % cop)
    if 0 < xMins and P_60plus < 0.85:
        risks.append("Minutes uncertainty — P(60+)=%.2f" % P_60plus)
    if atk_scale <= 0.75:
        risks.append("Very difficult attacking fixture — atk_scale=%.3f" % atk_scale)
    elif atk_scale <= 0.90:
        risks.append("Difficult attacking fixture — atk_scale=%.3f" % atk_scale)
    if n_gws < 4:
        risks.append("Small sample — %d GW(s) of data" % n_gws)
    if pos in (1, 2) and lam > 1.20:
        risks.append("Leaky defence — expected GC λ=%.3f" % lam)

    return drivers, risks


# ─────────────────────────────────────────────────────────────────────────────
# NULL RESULT
# ─────────────────────────────────────────────────────────────────────────────

def _null_result(player: dict, reason: str) -> dict:
    _zero = {k: 0.0 for k in (
        "xAppPts", "xGoalPts", "xAssistPts",
        "xCSPts", "xGCDeductPts", "xSavePts", "xBonus", "_xPts_raw",
    )}
    return {
        "player_id":         player["id"],
        "web_name":          player.get("web_name", ""),
        "position":          player.get("element_type"),
        "team_id":           player.get("team"),
        "xPts":              0.0,
        "confidence":        "LOW",
        "_minutes_conf":     "LOW",
        "_projection_conf":  "LOW",
        "components":        _zero,
        "inputs":            {},
        "drivers":           [],
        "risks":             [reason],
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PROJECTION
# ─────────────────────────────────────────────────────────────────────────────

def compute_xpts(
    player:  dict,
    history: list,
    fixture: Optional[dict],
    params:  dict,
) -> dict:
    """
    Compute expected FPL points for one player in one upcoming fixture.

    Parameters
    ----------
    player  : element dict from bootstrap-static
    history : per-GW history list from element-summary (current season only)
    fixture : upcoming fixture dict from element-summary fixtures[].
              Must include 'is_home', and ideally 'team_h'/'team_a' for V0.2 model.
              Pass None for a blank GW → xPts = 0.0.
              For a double GW call once per fixture and sum xPts.
    params  : dict from build_params()

    Returns
    -------
    dict: xPts, confidence, components (all FPL components), inputs (intermediates),
          drivers, risks.  _minutes_conf and _projection_conf are internal sub-scores.
    """

    # ── BLANK GW ─────────────────────────────────────────────────────────────
    if fixture is None:
        return _null_result(player, "Blank Gameweek — no fixture")

    # ── AVAILABILITY GATE ─────────────────────────────────────────────────────
    status   = player.get("status", "a")
    cop_next = player.get("chance_of_playing_next_round")

    if status == "u":
        return _null_result(player, "Player unavailable (transferred out)")
    if status == "s" and cop_next == 0:
        return _null_result(player, "Suspended — confirmed out")
    if status == "i" and cop_next == 0:
        return _null_result(player, "Injured — confirmed out")

    pos    = player["element_type"]
    starts = player.get("starts", 0)
    n_gws  = len(history)

    # ── xMINS ─────────────────────────────────────────────────────────────────
    gw_mins   = [h["minutes"] for h in history]
    gw_starts = [h["starts"]  for h in history]

    if n_gws >= 3:
        recent_avg = sum(gw_mins[-3:]) / 3.0
        season_avg = sum(gw_mins) / n_gws
        base_xMins = 0.65 * recent_avg + 0.35 * season_avg   # [heuristic]
    elif n_gws >= 1:
        recent_avg = sum(gw_mins) / n_gws
        season_avg = recent_avg
        base_xMins = recent_avg
    else:
        recent_avg = _f(_POS_DEFAULT_MINS[pos])
        season_avg = recent_avg
        base_xMins = recent_avg

    recent_starts_3 = sum(gw_starts[-3:]) if n_gws >= 3 else sum(gw_starts)
    if recent_starts_3 == 0: base_xMins = min(base_xMins, 25.0)   # [heuristic]
    if recent_starts_3 == 1: base_xMins = min(base_xMins, 60.0)   # [heuristic]

    cop_fraction = (cop_next / 100.0) if cop_next is not None else 1.0
    xMins = max(0.0, min(90.0, cop_fraction * base_xMins))

    # ── P(appearance) and P(60+ mins) ─────────────────────────────────────────
    if cop_next is not None:
        P_app    = cop_fraction
        base_60  = base_xMins
        P_60plus = cop_fraction * max(0.0, min(1.0, (base_60 - 30.0) / 60.0))
    else:
        P_app    = max(0.0, min(1.0, xMins / 45.0))           # [heuristic]
        P_60plus = max(0.0, min(1.0, (xMins - 30.0) / 60.0)) # [heuristic]

    # ── APPEARANCE POINTS ─────────────────────────────────────────────────────
    xAppPts = (P_app + P_60plus) if xMins > 0 else 0.0        # [FPL rules]

    # ── DAMPING WEIGHT ────────────────────────────────────────────────────────
    # w = n/(n+2): prior weight=2 [heuristic]; stronger at small n, lighter as evidence grows
    w = n_gws / (n_gws + 2.0)

    # ── V0.2 FIXTURE MODEL ────────────────────────────────────────────────────
    #
    # Design constraints implemented:
    #  1. FIXTURE_ADJ is completely removed — no double-counting.
    #  2. Opponent id resolved from fixture["team_h"/"team_a"].
    #     If those keys are absent (legacy fixture dict), neutral fallback is used
    #     (atk_scale = HOME_ADV; CS lambda = our_xGC_d × 1.0 / HOME_ADV).
    #  3. Opponent xGC is damped using the opponent GKP's starts count, same
    #     n/(n+2) principle as individual player xG damping.
    #  4. HOME_ADV is not added on top of a difficulty multiplier — it IS the
    #     multiplier (FIXTURE_ADJ is gone).  For a neutral opponent (opp_xGC =
    #     league_avg): atk_scale = HOME_ADV at home, 1/HOME_ADV away.
    #
    is_home = fixture.get("is_home", True)
    home_factor = HOME_ADV if is_home else (1.0 / HOME_ADV)  # [heuristic]

    # Resolve opponent
    if "team_a" in fixture and "team_h" in fixture:
        opponent_id = fixture["team_a"] if is_home else fixture["team_h"]
    else:
        opponent_id = None   # neutral fallback

    # Opponent defensive quality — damped toward league average
    opp_xgc_raw = (params["team_gkp_xgc"].get(opponent_id, params["league_avg_xgc"])
                   if opponent_id is not None else params["league_avg_xgc"])
    opp_n_gkp   = (params["team_gkp_starts"].get(opponent_id, 0)
                   if opponent_id is not None else 0)
    w_opp       = opp_n_gkp / (opp_n_gkp + 2.0)              # [heuristic: same prior as player]
    opp_xgc_d   = w_opp * opp_xgc_raw + (1.0 - w_opp) * params["league_avg_xgc"]

    # Attacking scale: fixture-specific goal-scoring environment for our players
    # = (opponent defensive quality / league average) × home advantage
    # [current-season data + heuristic HOME_ADV; no FIXTURE_ADJ]
    atk_scale = (opp_xgc_d / params["league_avg_xgc"]) * home_factor

    # Opponent attacking quality: drives CS lambda
    # = their team xGF proxy / league average
    opp_xgf_raw = (params["team_xgf"].get(opponent_id, params["league_avg_xgf"])
                   if opponent_id is not None else params["league_avg_xgf"])
    opp_atk_q   = opp_xgf_raw / params["league_avg_xgf"]      # [current-season data]

    # ── GOAL POINTS ───────────────────────────────────────────────────────────
    xG_api = _f(player["expected_goals_per_90"])
    xG_d   = w * xG_api + (1 - w) * params["pos_xG_base"][pos]
    xG_adj = xG_d * (xMins / 90.0) * atk_scale
    xGoalPts = xG_adj * GOAL_PTS[pos]                         # [FPL rule]

    # ── ASSIST POINTS ─────────────────────────────────────────────────────────
    xA_api = _f(player["expected_assists_per_90"])
    xA_d   = w * xA_api + (1 - w) * params["pos_xA_base"][pos]
    xA_adj = xA_d * (xMins / 90.0) * atk_scale
    xAssistPts = xA_adj * ASSIST_PTS                          # [FPL rule]

    # ── CLEAN SHEET LAMBDA ────────────────────────────────────────────────────
    # Source: our primary GKP's xGC_p90 (unbiased by game selection for nailed starters).
    # Never the projected player's own xGC_p90 (selection-biased for rotation players).
    team_xGC   = params["team_gkp_xgc"][player["team"]]
    league_avg = params["league_avg_xgc"]
    xGC_d      = w * team_xGC + (1 - w) * league_avg          # damp toward league mean
    #
    # lam = our defensive baseline × opponent attacking quality / home advantage.
    # When opp_atk_q=1 (avg opponent) and home_factor=1 (neutral): lam = xGC_d.
    # No FIXTURE_ADJ applied — fixture effect enters only through opp_atk_q.
    lam = xGC_d * opp_atk_q / home_factor                     # [Poisson parameter]

    # ── CLEAN SHEET POINTS ────────────────────────────────────────────────────
    p_cs   = math.exp(-lam)
    xCSPts = P_60plus * p_cs * CS_PTS[pos]                    # [FPL rule; Poisson]

    # ── GOALS CONCEDED DEDUCTION (GKP / DEF only) ─────────────────────────────
    # FPL: -1 pt per 2 team goals conceded (full game); player must participate (P_app).
    if pos in (1, 2):
        xGCDeductPts = -P_app * E_floor_X2(lam)               # [FPL rule; Poisson]
    else:
        xGCDeductPts = 0.0

    # ── SAVE POINTS (GKP only) ────────────────────────────────────────────────
    if pos == 1:
        saves_p90 = _f(player.get("saves_per_90", 0.0))
        xSavePts  = saves_p90 * (xMins / 90.0) / SAVE_PTS_PER  # [FPL rule]
    else:
        xSavePts = 0.0

    # ── BONUS ─────────────────────────────────────────────────────────────────
    _pos_bonus_base = {1: 0.30, 2: 0.40, 3: 0.55, 4: 0.75}   # [heuristic]
    if starts > 0:
        raw_rate = player.get("bonus", 0) / starts
        w_bonus  = min(starts / 10.0, 1.0)                    # [heuristic: 10-start threshold]
        blended  = w_bonus * raw_rate + (1 - w_bonus) * _pos_bonus_base[pos]
        xBonus   = min(blended * P_60plus, _BONUS_CAP)        # [heuristic cap]
    elif xMins > 0:
        xBonus = _pos_bonus_base[pos] * 0.3                   # [heuristic fallback for new players]
    else:
        xBonus = 0.0                                           # never played → no bonus expected

    # ── TOTAL ──────────────────────────────────────────────────────────────────
    xPts_raw = (xAppPts + xGoalPts + xAssistPts
                + xCSPts + xGCDeductPts + xSavePts + xBonus)
    xPts = max(0.0, round(xPts_raw, 1))

    # ── CONFIDENCE ─────────────────────────────────────────────────────────────
    mc      = _minutes_conf(status, cop_next, recent_starts_3, recent_avg, n_gws)
    pc      = _projection_conf(n_gws, starts)
    overall = _overall_conf(mc, pc)

    drivers, risks = _drivers_risks(
        player, pos, xMins, P_app, P_60plus, p_cs, lam,
        xG_d, xA_d, atk_scale, n_gws,
    )

    return {
        "player_id":        player["id"],
        "web_name":         player.get("web_name", ""),
        "position":         pos,
        "team_id":          player["team"],
        "xPts":             xPts,
        "confidence":       overall,
        "_minutes_conf":    mc,
        "_projection_conf": pc,
        "components": {
            "xAppPts":      round(xAppPts,      6),
            "xGoalPts":     round(xGoalPts,     6),
            "xAssistPts":   round(xAssistPts,   6),
            "xCSPts":       round(xCSPts,       6),
            "xGCDeductPts": round(xGCDeductPts, 6),
            "xSavePts":     round(xSavePts,     6),
            "xBonus":       round(xBonus,       6),
            "_xPts_raw":    round(xPts_raw,     6),
        },
        "inputs": {
            "xMins":       round(xMins,      4),
            "P_app":       round(P_app,      6),
            "P_60plus":    round(P_60plus,   6),
            "w":           round(w,          6),
            "atk_scale":   round(atk_scale,  6),
            "home_factor": round(home_factor, 4),
            "is_home":     is_home,
            "opponent_id": opponent_id,
            "opp_xgc_d":   round(opp_xgc_d,  6),
            "opp_atk_q":   round(opp_atk_q,  6),
            "team_xGC":    team_xGC,
            "xGC_d":       round(xGC_d,      6),
            "lam":         round(lam,         6),
            "p_cs":        round(p_cs,        6),
            "xG_d":        round(xG_d,        6),
            "xA_d":        round(xA_d,        6),
        },
        "drivers": drivers,
        "risks":   risks,
    }
