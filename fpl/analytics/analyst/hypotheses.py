"""
Static hypothesis table. A hypothesis is a possible explanation for a finding, never a fact.

Each entry names the finding that triggers it and what data could confirm or refute it.
Entries propose no product changes.
"""

from .models import Hypothesis

# (id, triggering finding id, hypothesis text, what would test it)
_TABLE = (
    ("H_upload_retries", "O_upload_multiplicity",
     "Multiple uploads in a session are re-uploads, for example after a failed or unsatisfactory scan.",
     "Per-session event sequence (ordered scan_failed / scan_valid / screenshot_uploaded) by session_id."),
    ("H_internal_sessions", "G_no_segmentation",
     "Some sessions in the window come from internal testing rather than external users.",
     "Break sessions down by distinct_id and compare against known internal identifiers."),
    ("H_conditional_paths_untaken", "G_not_observed_conditional",
     "The branch/error paths were not taken by the sessions in the window, so their events did not fire.",
     "For each unseen event, check whether any session met its trigger condition; exercise the path once manually."),
    ("H_behaviours_untaken_or_unwired", "G_not_observed_behavioural",
     "Either no session performed these optional actions, or an event is not wired as intended. "
     "The current data cannot distinguish the two.",
     "Perform each action once in a controlled session and confirm the event arrives."),
    ("H_single_action_small_n", "O_decision_mix",
     "A single observed decision type reflects the few sessions seen, not the underlying SWAP/HOLD split.",
     "Repeat the decision-mix count once more external sessions exist; inspect the squads behind each decision."),
    ("H_multi_fire_by_design", "O_top_events_per_session",
     "The high events-per-session figure reflects several legitimate firings per session "
     "(for example one per element viewed).",
     "Inspect the distinct property values per session for that event."),
    ("H_core_event_missing", "G_core_missing",
     "Either the instrumentation for the missing core event is failing, or no session reached that step.",
     "Walk the flow once end to end and confirm each core event arrives; compare with upstream step counts."),
)


def generate(findings):
    """Hypotheses for the findings present. `findings` is an iterable of Finding."""
    present = {f.id for f in findings}
    return [Hypothesis(hid, text, trigger, test) for hid, trigger, text, test in _TABLE if trigger in present]
