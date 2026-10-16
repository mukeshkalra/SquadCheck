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
    "bench_step_shown", "bench_step_progress", "bench_step_invalid", "bench_step_captain_skipped",
    "bench_step_reupload_clicked", "bench_step_submitted", "bench_step_failed", "bench_step_completed",
    "planner_cta_unavailable", "planner_handover_failed", "planner_plans_failed", "planner_tweak_failed",
    "planner_error",
})

BEHAVIOURAL = frozenset({
    "position_section_viewed", "player_card_opened", "player_numbers_opened",
    "bench_viewed", "captain_card_viewed", "recommendation_viewed",
    "recommended_change_viewed", "feedback_loved", "feedback_useful",
    "feedback_ok", "feedback_bad", "feedback_reason", "action_changed_xi",
    "action_kept_xi", "action_deciding_xi", "share_native_image", "share_native",
    "share_download", "add_to_home_screen",
    "home_tile_clicked", "upload_cta_clicked", "methodology_link_clicked",
    "planner_cta_viewed", "plan_my_move_clicked",
    "planner_opened", "planner_screen_viewed", "planner_action", "planner_bank_entered", "planner_player_search",
    "planner_plans_requested", "planner_plans_loaded", "planner_plan_inspected",
    "planner_share_clicked", "planner_share_message_copied", "planner_share_confirmed", "planner_plan_backed",
    "planner_tweak_opened", "planner_tweak_run", "planner_tweak_result", "planner_tweak_used",
    "planner_save_prompt_opened", "planner_save_prompt_accepted", "planner_share_card_previewed",
    "planner_share_card_copied",
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
