"""
Player insight layer — one human-readable headline per player.

Deterministic rules over existing projection outputs.
All thresholds are in the THRESHOLDS block below for easy tuning.

Fields used (all from the pipeline payload player dict):
  player["xPts"]
  player["confidence"]               HIGH / MEDIUM / LOW
  player["position"]                 1=GK, 2=DEF, 3=MID, 4=FWD
  player["inputs"]["P_60plus"]
  player["inputs"]["atk_scale"]
  player["inputs"]["p_cs"]
  player["risks"]                    list of strings from projection
  player["player_api"]["status"]     a / d / i / s / u
  player["player_api"]["cop_next"]   0–100 or None
"""

# ── THRESHOLDS ────────────────────────────────────────────────────────────────

_MIN_RISK_P60  = 0.50   # P(60+ mins) below this → minutes_risk
_MIN_RISK_COP  = 50     # chance_of_playing_next_round (%) below this → minutes_risk

_CAPT_XPTS     = 8.5    # standalone captain candidate (no squad context needed)
_BIG_XPTS      = 7.5    # big upside threshold

_CS_PROB       = 0.40   # p_cs above this → clean_sheet_chance (GK/DEF only)

_STRONG_ATK    = 1.15   # atk_scale ≥ this → strong_fixture
_TOUGH_ATK     = 0.78   # atk_scale < this → tough_fixture

_LOOKING_XPTS  = 5.0    # xPts ≥ this with HIGH confidence → looking_good

# ── LABEL REGISTRY ────────────────────────────────────────────────────────────

LABELS = {
    "minutes_risk":       {"label": "Minutes risk",       "tone": "caution",  "icon": "⚠️"},
    "high_variance":      {"label": "High variance",      "tone": "caution",  "icon": "⚠️"},
    "tough_fixture":      {"label": "Tough fixture",      "tone": "caution",  "icon": "⚠️"},
    "captain_candidate":  {"label": "Captain candidate",  "tone": "positive", "icon": "🟢"},
    "big_upside":         {"label": "Big upside",         "tone": "positive", "icon": "🟢"},
    "clean_sheet_chance": {"label": "Clean-sheet chance", "tone": "positive", "icon": "🟢"},
    "strong_fixture":     {"label": "Strong fixture",     "tone": "positive", "icon": "🟢"},
    "looking_good":       {"label": "Looking good",       "tone": "positive", "icon": "🟢"},
    "solid_pick":         {"label": "Solid pick",         "tone": "positive", "icon": "👍"},
}

# Strings in a risk entry that signal a minutes/availability problem
_RISK_STRINGS = ("minutes uncertainty", "injury", "suspended", "doubt")


def player_insight(player: dict, is_top_captain: bool = False) -> dict:
    """
    Return one insight for the player.

    Parameters
    ----------
    player        : player dict from pipeline payload.
    is_top_captain: True when this player is the highest-xPts starter in the
                    squad — enables captain_candidate without requiring xPts to
                    hit the standalone threshold.

    Returns
    -------
    {"key": str, "label": str, "tone": str, "icon": str}
    """
    inp    = player.get("inputs") or {}
    api    = player.get("player_api") or {}
    risks  = player.get("risks") or []
    pos    = player.get("position") or 3
    xpts   = player.get("xPts") or 0.0
    conf   = player.get("confidence") or "MEDIUM"

    p60    = inp.get("P_60plus")
    cop    = api.get("cop_next")   # int 0-100 or None
    status = api.get("status") or "a"
    atk    = inp.get("atk_scale")
    p_cs   = inp.get("p_cs")

    # 1. Minutes / availability risk — highest priority, overrides positive signals
    minutes_risk = (
        (p60 is not None and p60 < _MIN_RISK_P60)
        or (cop is not None and cop < _MIN_RISK_COP)
        or status in ("i", "d")
        or any(s in r.lower() for r in risks for s in _RISK_STRINGS)
    )
    if minutes_risk:
        return _tag("minutes_risk")

    # 2. Tough fixture — specific negative beats generic caution
    if atk is not None and atk < _TOUGH_ATK:
        return _tag("tough_fixture")

    # 3. High variance (LOW confidence, no specific negative already shown)
    if conf == "LOW":
        return _tag("high_variance")

    # 4. Captain candidate
    if is_top_captain or xpts >= _CAPT_XPTS:
        return _tag("captain_candidate")

    # 5. Big upside
    if xpts >= _BIG_XPTS:
        return _tag("big_upside")

    # 6. Clean-sheet chance — GK/DEF only
    if pos <= 2 and p_cs is not None and p_cs >= _CS_PROB:
        return _tag("clean_sheet_chance")

    # 7. Strong fixture
    if atk is not None and atk >= _STRONG_ATK:
        return _tag("strong_fixture")

    # 8. Looking good
    if conf == "HIGH" and xpts >= _LOOKING_XPTS:
        return _tag("looking_good")

    # 9. Fallback
    return _tag("solid_pick")


def _tag(key: str) -> dict:
    entry = LABELS[key]
    return {"key": key, **entry}
