"""
Live verification of the analytics layer against real PostHog data.

Usage (credentials from environment only — never hardcode them):
  POSTHOG_PERSONAL_API_KEY=<key> POSTHOG_PROJECT_ID=<id> python3 -m fpl.analytics.verify_live

Prints only sanitized, high-level results. Credential values are never printed.
"""

import json
import os
import sys

# Verify credentials are present before importing anything that might error badly
_key_set = bool(os.environ.get("POSTHOG_PERSONAL_API_KEY", "").strip())
_pid_set = bool(os.environ.get("POSTHOG_PROJECT_ID", "").strip())
if not _key_set or not _pid_set:
    missing = []
    if not _key_set: missing.append("POSTHOG_PERSONAL_API_KEY")
    if not _pid_set: missing.append("POSTHOG_PROJECT_ID")
    print(f"ABORT: missing environment variables: {', '.join(missing)}")
    sys.exit(1)

print("credentials: both env vars present (values not shown)")
print()

# ── connection check ──────────────────────────────────────────────────────────
print("=== 1. Connection check ===")
try:
    from fpl.analytics.client import run_query, rows_to_dicts
    result = run_query("SELECT count() AS n FROM events WHERE timestamp >= now() - interval 30 day")
    rows = rows_to_dicts(result)
    total = rows[0].get("n", 0) if rows else 0
    print(f"  OK — HogQL API reachable. Events in last 30 days: {total}")
    _conn_ok = True
except Exception as e:
    print(f"  FAIL — {type(e).__name__}: {e}")
    _conn_ok = False

# ── property resolution check ─────────────────────────────────────────────────
print()
print("=== 2. Property resolution ===")
if _conn_ok:
    checks = [
        ("properties.session_id",  "SELECT count() AS n FROM events WHERE isNotNull(properties.session_id)"),
        ("properties.action",      "SELECT countIf(properties.action = 'SWAP') AS swap, countIf(properties.action = 'HOLD') AS hold FROM events WHERE event = 'decision_reached'"),
        ("properties.is_return",   "SELECT countIf(properties.is_return = true) AS ret FROM events WHERE event = 'page_viewed'"),
        ("properties.utm_source",  "SELECT count() AS n FROM events WHERE isNotNull(properties.utm_source) AND properties.utm_source != ''"),
    ]
    for label, sql in checks:
        try:
            r = rows_to_dicts(run_query(sql))
            vals = {k: v for k, v in (r[0] if r else {}).items()}
            print(f"  OK  {label}: {vals}")
        except Exception as e:
            print(f"  ERR {label}: {type(e).__name__}: {e}")

# ── get_funnel() ──────────────────────────────────────────────────────────────
print()
print("=== 3. get_funnel() ===")
_funnel_ok = False
try:
    from fpl.analytics.queries import get_funnel
    funnel = get_funnel(lookback_days=30)

    # Structure check
    required_keys = {"lookback_days", "funnel", "scan", "pipeline", "feedback", "actions"}
    missing_keys = required_keys - funnel.keys()
    if missing_keys:
        print(f"  WARN structure: missing top-level keys: {missing_keys}")
    else:
        print("  OK  structure: all top-level keys present")

    f = funnel["funnel"]
    print(f"  visits:            {f['visits']}")
    for step in ("uploaded", "scan_valid", "projection_viewed", "decision_reached"):
        v = f.get(step, {})
        pct = f"{v['pct']}%" if v.get("pct") is not None else "null (no data yet)"
        print(f"  {step:<22} n={v.get('n',0):>5}  d={v.get('d',0):>5}  pct={pct}")

    p = funnel["pipeline"]
    print(f"  pipeline swap:     n={p['swap']['n']:>5}  pct={p['swap']['pct']}")
    print(f"  pipeline hold:     n={p['hold']['n']:>5}  pct={p['hold']['pct']}")

    fb = funnel["feedback"]
    print(f"  feedback_any:      n={fb['any']['n']:>5}  d={fb['any']['d']:>5}  pct={fb['any']['pct']}")
    print(f"  by rating:         loved={fb['feedback_loved']}  useful={fb['feedback_useful']}  ok={fb['feedback_ok']}  bad={fb['feedback_bad']}")

    act = funnel["actions"]
    print(f"  action_any:        n={act['any']['n']:>5}  d={act['any']['d']:>5}  pct={act['any']['pct']}")
    print(f"  by action:         changed={act['action_changed']}  kept={act['action_kept']}  deciding={act['action_deciding']}")

    sc = funnel["scan"]
    print(f"  scan_failed:       {sc['failed_events']}")
    print(f"  scan_ambiguous:    {sc['ambiguous_events']}")

    _funnel_ok = True
    print("  RESULT: get_funnel() succeeded")
except Exception as e:
    print(f"  FAIL — {type(e).__name__}: {e}")

# ── get_event_health() ────────────────────────────────────────────────────────
print()
print("=== 4. get_event_health() ===")
_health_ok = False
try:
    from fpl.analytics.queries import get_event_health
    health = get_event_health(lookback_days=7)

    required_keys = {"lookback_days", "events", "missing_events", "core_funnel_missing"}
    missing_keys = required_keys - health.keys()
    if missing_keys:
        print(f"  WARN structure: missing top-level keys: {missing_keys}")
    else:
        print("  OK  structure: all top-level keys present")

    events = health["events"]
    print(f"  distinct events seen (last 7 days): {len(events)}")

    if events:
        print("  event                       total  sessions  per_session")
        for e in events[:15]:  # top 15 by volume
            eps = f"{e['events_per_session']:.2f}" if e["events_per_session"] is not None else "—"
            print(f"  {e['event']:<30} {e['total_events']:>6}  {e['sessions']:>8}  {eps:>11}")
        if len(events) > 15:
            print(f"  ... and {len(events) - 15} more")

    missing = health["missing_events"]
    core_missing = health["core_funnel_missing"]
    print(f"  missing_events ({len(missing)}): {missing if missing else '(none)'}")
    print(f"  core_funnel_missing ({len(core_missing)}): {core_missing if core_missing else '(none)'}")

    # Check events_per_session structure
    if events:
        row = events[0]
        row_keys = set(row.keys())
        expected = {"event", "total_events", "sessions", "events_per_session", "first_seen", "last_seen"}
        if expected <= row_keys:
            print("  OK  event row structure: all required keys present")
        else:
            print(f"  WARN event row missing keys: {expected - row_keys}")

    _health_ok = True
    print("  RESULT: get_event_health() succeeded")
except Exception as e:
    print(f"  FAIL — {type(e).__name__}: {e}")

# ── summary ───────────────────────────────────────────────────────────────────
print()
print("=== Summary ===")
print(f"  Connection:       {'OK' if _conn_ok else 'FAIL'}")
print(f"  get_funnel():     {'OK' if _funnel_ok else 'FAIL'}")
print(f"  get_event_health(): {'OK' if _health_ok else 'FAIL'}")
ready = _conn_ok and _funnel_ok and _health_ok
print(f"  Analytics layer ready: {'YES' if ready else 'NO — see errors above'}")
