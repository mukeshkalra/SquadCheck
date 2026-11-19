"""
Availability rule shared by the captain pick and the chip advice.

A player is AVAILABLE when his chance of playing next round is at least AVAIL_MIN (75) and the
projection confidence is not LOW. Chance of playing is read from FPL's chance_of_playing_next_round;
when FPL leaves it empty, status 'a' means 100 and any other status is treated as below the bar.

Pure functions, no network.
"""

AVAIL_MIN = 75
_OUT_STATUS = ("i", "s", "u", "n")      # injured, suspended, unavailable, not in the squad list


def chance_of_playing(status, cop):
    """0..100. `cop` is chance_of_playing_next_round (None when FPL gives no figure)."""
    if cop is not None:
        try:
            return max(0, min(100, int(cop)))
        except (TypeError, ValueError):
            pass
    if status in (None, "a"):
        return 100
    if status in _OUT_STATUS:
        return 0
    return 50            # 'd' (doubtful) with no figure: below the bar, never guessed upwards


def is_available(status, cop, confidence):
    return chance_of_playing(status, cop) >= AVAIL_MIN and confidence != "LOW"


def can_play(status, cop):
    """Chance of playing alone (used where confidence is not part of the test)."""
    return chance_of_playing(status, cop) >= AVAIL_MIN


def pick_captains(players):
    """
    players: iterable of dicts with player_id, xPts, status, cop_next, confidence, is_starting.
    Captain = highest-xPts AVAILABLE starter; vice = next. Ties break on lower player_id so the
    result is stable. If no starter is available the highest-xPts starter is returned with
    fallback=True, and if there are no starters, captain_id is None.
    """
    starters = [p for p in players if p.get("is_starting")]
    key = lambda p: (-(p.get("xPts") or 0.0), p["player_id"])
    avail = sorted((p for p in starters if is_available(p.get("status"), p.get("cop_next"), p.get("confidence"))), key=key)
    if avail:
        return {"captain_id": avail[0]["player_id"],
                "vice_id": avail[1]["player_id"] if len(avail) > 1 else None,
                "fallback": False}
    ranked = sorted(starters, key=key)
    if not ranked:
        return {"captain_id": None, "vice_id": None, "fallback": False}
    return {"captain_id": ranked[0]["player_id"],
            "vice_id": ranked[1]["player_id"] if len(ranked) > 1 else None,
            "fallback": True}
