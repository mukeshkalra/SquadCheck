"""
Event classification for the Growth Analyst.

  CORE         - the funnel path; a missing event may indicate instrumentation failure.
  CONDITIONAL  - branch/error paths; only fire when that path is taken.
  BEHAVIOURAL  - optional user actions or views; only fire if the user does them.

Missing CONDITIONAL / BEHAVIOURAL events are "not observed", never evidence of a bug.
Classification is by current evidence; nothing here is promoted to CORE without it.
"""

from typing import Optional

from ..queries import _EXPECTED_EVENTS, _FUNNEL_STEPS
from .models import EventClass

CORE = frozenset(_FUNNEL_STEPS)

CONDITIONAL = frozenset({
    "scan_ambiguous", "scan_failed", "disambig_shown", "disambig_resolved",
    "captain_switch_shown",
})

BEHAVIOURAL = frozenset({
    "position_section_viewed", "player_card_opened", "player_numbers_opened",
    "bench_viewed", "captain_card_viewed", "recommendation_viewed",
    "recommended_change_viewed", "feedback_loved", "feedback_useful",
    "feedback_ok", "feedback_bad", "feedback_reason", "action_changed_xi",
    "action_kept_xi", "action_deciding_xi", "share_native_image", "share_native",
    "share_download", "add_to_home_screen",
})


def classify(event: str) -> Optional[EventClass]:
    """EventClass for a known event, or None for anything unclassified (e.g. $web_vitals)."""
    if event in CORE:
        return EventClass.CORE
    if event in CONDITIONAL:
        return EventClass.CONDITIONAL
    if event in BEHAVIOURAL:
        return EventClass.BEHAVIOURAL
    return None
