"""
Deterministic analytics queries over the SquadCheck event schema.

Rules:
  - Returns structured data only — no interpretation, no thresholds, no conclusions.
  - Every rate is {"n": numerator, "d": denominator, "pct": float | None}.
    pct is None when d == 0 to make zero-data cases explicit.
  - Session counts use uniqIf(properties.session_id, ...) throughout for consistency.
    session_id is our custom super-property (per-tab, from sessionStorage).
  - Raw event counts (countIf) are used only where a session fired an event
    multiple times and each instance is meaningful (e.g. feedback rating taps).
"""

from .client import run_query, rows_to_dicts


def _rate(n, d):
    return {"n": n, "d": d, "pct": round(n / d * 100, 1) if d else None}


def _g(row, key):
    """Safe getter — returns 0 for missing or None values."""
    return row.get(key) or 0


# ── Core funnel events in order ───────────────────────────────────────────────
_FUNNEL_STEPS = [
    "page_viewed",
    "screenshot_uploaded",
    "scan_valid",
    "projection_viewed",
    "decision_reached",
]

# ── Full expected event set (post QA fixes) ───────────────────────────────────
_EXPECTED_EVENTS = {
    "page_viewed", "screenshot_uploaded", "scan_valid", "scan_ambiguous",
    "scan_failed", "disambig_shown", "disambig_resolved", "projection_viewed",
    "decision_reached", "position_section_viewed", "player_card_opened",
    "player_numbers_opened", "bench_viewed", "captain_switch_shown",
    "captain_card_viewed", "recommendation_viewed", "recommended_change_viewed",
    "feedback_loved", "feedback_useful", "feedback_ok", "feedback_bad",
    "action_changed_xi", "action_kept_xi", "action_deciding_xi",
    "feedback_reason", "share_native_image", "share_native", "share_download",
    "add_to_home_screen",
}


# ── get_funnel ────────────────────────────────────────────────────────────────

def get_funnel(lookback_days=30):
    """
    Return session-level counts and step-over-step rates for the core funnel,
    plus pipeline recommendation and user feedback/action signals.

    Funnel denominators:
      uploaded      — d = visits       (you visit, then optionally upload)
      scan_valid    — d = uploaded     (upload required before scan)
      projection_viewed — d = uploaded (disambig path also requires upload)
      decision_reached  — d = projection_viewed (fires immediately after)

    Feedback and action signals use sessions (not raw event counts) for
    consistency, since each session should produce at most one of each.
    """
    sql = f"""
    SELECT
      uniqIf(properties.session_id, event = 'page_viewed')
          AS visits,
      uniqIf(properties.session_id, event = 'screenshot_uploaded')
          AS uploaded,
      uniqIf(properties.session_id, event = 'scan_valid')
          AS scan_valid,
      uniqIf(properties.session_id, event = 'projection_viewed')
          AS projection_viewed,
      uniqIf(properties.session_id, event = 'decision_reached')
          AS decision_reached,

      countIf(event = 'scan_failed')
          AS scan_failed,
      countIf(event = 'scan_ambiguous')
          AS scan_ambiguous,

      countIf(event = 'decision_reached' AND properties.action = 'SWAP')
          AS pipeline_swap,
      countIf(event = 'decision_reached' AND properties.action = 'HOLD')
          AS pipeline_hold,

      uniqIf(properties.session_id,
             event IN ('feedback_loved', 'feedback_useful', 'feedback_ok', 'feedback_bad'))
          AS feedback_any,
      countIf(event = 'feedback_loved')   AS feedback_loved,
      countIf(event = 'feedback_useful')  AS feedback_useful,
      countIf(event = 'feedback_ok')      AS feedback_ok,
      countIf(event = 'feedback_bad')     AS feedback_bad,

      uniqIf(properties.session_id,
             event IN ('action_changed_xi', 'action_kept_xi', 'action_deciding_xi'))
          AS action_any,
      countIf(event = 'action_changed_xi')   AS action_changed,
      countIf(event = 'action_kept_xi')      AS action_kept,
      countIf(event = 'action_deciding_xi')  AS action_deciding

    FROM events
    WHERE timestamp >= now() - interval {int(lookback_days)} day
    """
    rows = rows_to_dicts(run_query(sql))
    r = rows[0] if rows else {}

    visits     = _g(r, "visits")
    uploaded   = _g(r, "uploaded")
    projection = _g(r, "projection_viewed")
    p_swap     = _g(r, "pipeline_swap")
    p_hold     = _g(r, "pipeline_hold")
    p_total    = p_swap + p_hold

    return {
        "lookback_days": lookback_days,
        "funnel": {
            "visits":            visits,
            "uploaded":          _rate(_g(r, "uploaded"),          visits),
            "scan_valid":        _rate(_g(r, "scan_valid"),         uploaded),
            "projection_viewed": _rate(projection,                  uploaded),
            "decision_reached":  _rate(_g(r, "decision_reached"),   projection),
        },
        "scan": {
            "failed_events":    _g(r, "scan_failed"),
            "ambiguous_events": _g(r, "scan_ambiguous"),
        },
        "pipeline": {
            "swap": _rate(p_swap, p_total),
            "hold": _rate(p_hold, p_total),
        },
        "feedback": {
            "any":            _rate(_g(r, "feedback_any"), projection),
            "feedback_loved": _g(r, "feedback_loved"),
            "feedback_useful": _g(r, "feedback_useful"),
            "feedback_ok":    _g(r, "feedback_ok"),
            "feedback_bad":   _g(r, "feedback_bad"),
        },
        "actions": {
            "any":             _rate(_g(r, "action_any"), projection),
            "action_changed":  _g(r, "action_changed"),
            "action_kept":     _g(r, "action_kept"),
            "action_deciding": _g(r, "action_deciding"),
        },
    }


# ── get_event_health ──────────────────────────────────────────────────────────

def get_event_health(lookback_days=7):
    """
    Return per-event counts, session coverage, and events_per_session ratio
    for all events received in the lookback window.

    events_per_session = total_events / sessions.
      This ratio is factual — the caller decides if it's anomalous.
      For single-fire events (page_viewed, decision_reached) expect ~1.0.
      Unexpectedly high values may indicate an instrumentation bug.

    missing_events — expected events with zero fires in this window.
    core_funnel_missing — subset of missing_events that are in the core
      funnel path (page_viewed → … → decision_reached). Zero fires here
      means the funnel data is incomplete.
    """
    sql = f"""
    SELECT
      event,
      count()                      AS total_events,
      uniq(properties.session_id)  AS sessions,
      min(timestamp)               AS first_seen,
      max(timestamp)               AS last_seen
    FROM events
    WHERE timestamp >= now() - interval {int(lookback_days)} day
    GROUP BY event
    ORDER BY total_events DESC
    """
    rows = rows_to_dicts(run_query(sql))

    seen = {r["event"] for r in rows if r.get("event")}
    missing = sorted(_EXPECTED_EVENTS - seen)
    core_missing = sorted(e for e in missing if e in set(_FUNNEL_STEPS))

    events = []
    for r in rows:
        total = _g(r, "total_events")
        sess  = _g(r, "sessions")
        events.append({
            "event":              r.get("event"),
            "total_events":       total,
            "sessions":           sess,
            "events_per_session": round(total / sess, 2) if sess else None,
            "first_seen":         str(r.get("first_seen") or ""),
            "last_seen":          str(r.get("last_seen") or ""),
        })

    return {
        "lookback_days":      lookback_days,
        "events":             events,
        "missing_events":     missing,
        "core_funnel_missing": core_missing,
    }
