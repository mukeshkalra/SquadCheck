"""
Tests for fpl.analytics.queries.

All PostHog HTTP calls are mocked via unittest.mock.patch.
No credentials or live network access required.

Patch target: fpl.analytics.queries.run_query
(patch where the name is used, not where it is defined)
"""

import unittest
from unittest.mock import patch

from fpl.analytics.queries import (
    get_funnel,
    get_event_health,
    get_scan_failures,
    _SCAN_FAILURE_LIMIT,
    _FUNNEL_STEPS,
    _EXPECTED_EVENTS,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _resp(col_names, rows):
    """Build a minimal fake PostHog HogQL query response."""
    return {
        "columns": [{"name": c} for c in col_names],
        "results": rows,
    }


_FUNNEL_COLS = [
    "visits", "uploaded", "scan_valid", "projection_viewed", "decision_reached",
    "scan_failed", "scan_ambiguous",
    "pipeline_swap", "pipeline_hold",
    "feedback_any", "feedback_loved", "feedback_useful", "feedback_ok", "feedback_bad",
    "action_any", "action_changed", "action_kept", "action_deciding",
]


def _funnel_row(visits=1000, uploaded=400, scan_valid=320, projection=300,
                decision=300, scan_failed=10, scan_ambiguous=5,
                p_swap=120, p_hold=180,
                fb_any=80, fb_loved=30, fb_useful=25, fb_ok=15, fb_bad=10,
                act_any=90, act_changed=40, act_kept=35, act_deciding=15):
    return _resp(_FUNNEL_COLS, [[
        visits, uploaded, scan_valid, projection, decision,
        scan_failed, scan_ambiguous,
        p_swap, p_hold,
        fb_any, fb_loved, fb_useful, fb_ok, fb_bad,
        act_any, act_changed, act_kept, act_deciding,
    ]])


_HEALTH_COLS = ["event", "total_events", "sessions", "first_seen", "last_seen"]


# ── get_funnel ────────────────────────────────────────────────────────────────

class TestGetFunnel(unittest.TestCase):

    @patch("fpl.analytics.queries.run_query")
    def test_funnel_step_rates(self, mock_rq):
        mock_rq.return_value = _funnel_row(
            visits=1000, uploaded=400, scan_valid=320, projection=300, decision=298,
        )
        r = get_funnel()

        self.assertEqual(r["funnel"]["visits"], 1000)

        # uploaded: 400 / 1000 = 40.0%
        self.assertEqual(r["funnel"]["uploaded"]["n"], 400)
        self.assertEqual(r["funnel"]["uploaded"]["d"], 1000)
        self.assertAlmostEqual(r["funnel"]["uploaded"]["pct"], 40.0)

        # scan_valid: 320 / 400 = 80.0%
        self.assertEqual(r["funnel"]["scan_valid"]["d"], 400)
        self.assertAlmostEqual(r["funnel"]["scan_valid"]["pct"], 80.0)

        # projection_viewed: 300 / 400 = 75.0%
        self.assertEqual(r["funnel"]["projection_viewed"]["d"], 400)
        self.assertAlmostEqual(r["funnel"]["projection_viewed"]["pct"], 75.0)

        # decision_reached: 298 / 300 = 99.3%
        self.assertEqual(r["funnel"]["decision_reached"]["d"], 300)
        self.assertAlmostEqual(r["funnel"]["decision_reached"]["pct"], 99.3)

    @patch("fpl.analytics.queries.run_query")
    def test_zero_visits_gives_none_pct(self, mock_rq):
        mock_rq.return_value = _funnel_row(visits=0, uploaded=0, scan_valid=0,
                                           projection=0, decision=0)
        r = get_funnel()
        self.assertEqual(r["funnel"]["visits"], 0)
        self.assertIsNone(r["funnel"]["uploaded"]["pct"])
        self.assertIsNone(r["funnel"]["scan_valid"]["pct"])
        self.assertIsNone(r["funnel"]["projection_viewed"]["pct"])
        self.assertIsNone(r["funnel"]["decision_reached"]["pct"])

    @patch("fpl.analytics.queries.run_query")
    def test_zero_uploaded_gives_none_scan_pct(self, mock_rq):
        mock_rq.return_value = _funnel_row(visits=100, uploaded=0, scan_valid=0,
                                           projection=0, decision=0)
        r = get_funnel()
        self.assertIsNone(r["funnel"]["scan_valid"]["pct"])
        self.assertIsNone(r["funnel"]["projection_viewed"]["pct"])

    @patch("fpl.analytics.queries.run_query")
    def test_pipeline_split(self, mock_rq):
        mock_rq.return_value = _funnel_row(p_swap=120, p_hold=180)
        r = get_funnel()
        # swap: 120 / 300 = 40%
        self.assertEqual(r["pipeline"]["swap"]["n"], 120)
        self.assertEqual(r["pipeline"]["swap"]["d"], 300)
        self.assertAlmostEqual(r["pipeline"]["swap"]["pct"], 40.0)
        # hold: 180 / 300 = 60%
        self.assertAlmostEqual(r["pipeline"]["hold"]["pct"], 60.0)

    @patch("fpl.analytics.queries.run_query")
    def test_pipeline_all_hold_no_swap(self, mock_rq):
        mock_rq.return_value = _funnel_row(p_swap=0, p_hold=300)
        r = get_funnel()
        # d = 0 + 300 = 300, so pct = 0/300 = 0.0 (not None)
        self.assertEqual(r["pipeline"]["swap"]["n"], 0)
        self.assertEqual(r["pipeline"]["swap"]["d"], 300)
        self.assertAlmostEqual(r["pipeline"]["swap"]["pct"], 0.0)
        self.assertAlmostEqual(r["pipeline"]["hold"]["pct"], 100.0)

    @patch("fpl.analytics.queries.run_query")
    def test_pipeline_zero_total_gives_none(self, mock_rq):
        mock_rq.return_value = _funnel_row(p_swap=0, p_hold=0)
        r = get_funnel()
        self.assertIsNone(r["pipeline"]["swap"]["pct"])
        self.assertIsNone(r["pipeline"]["hold"]["pct"])

    @patch("fpl.analytics.queries.run_query")
    def test_feedback_signals(self, mock_rq):
        mock_rq.return_value = _funnel_row(
            projection=300, fb_any=80, fb_loved=30, fb_useful=25, fb_ok=15, fb_bad=10,
        )
        r = get_funnel()
        # feedback_any: 80 / 300 = 26.7%
        self.assertEqual(r["feedback"]["any"]["n"], 80)
        self.assertEqual(r["feedback"]["any"]["d"], 300)
        self.assertAlmostEqual(r["feedback"]["any"]["pct"], 26.7)
        self.assertEqual(r["feedback"]["feedback_loved"], 30)
        self.assertEqual(r["feedback"]["feedback_useful"], 25)
        self.assertEqual(r["feedback"]["feedback_ok"], 15)
        self.assertEqual(r["feedback"]["feedback_bad"], 10)

    @patch("fpl.analytics.queries.run_query")
    def test_action_signals(self, mock_rq):
        mock_rq.return_value = _funnel_row(
            projection=300, act_any=90, act_changed=40, act_kept=35, act_deciding=15,
        )
        r = get_funnel()
        # action_any: 90 / 300 = 30%
        self.assertEqual(r["actions"]["any"]["n"], 90)
        self.assertEqual(r["actions"]["any"]["d"], 300)
        self.assertAlmostEqual(r["actions"]["any"]["pct"], 30.0)
        self.assertEqual(r["actions"]["action_changed"], 40)
        self.assertEqual(r["actions"]["action_kept"], 35)
        self.assertEqual(r["actions"]["action_deciding"], 15)

    @patch("fpl.analytics.queries.run_query")
    def test_scan_signals(self, mock_rq):
        mock_rq.return_value = _funnel_row(scan_failed=12, scan_ambiguous=7)
        r = get_funnel()
        self.assertEqual(r["scan"]["failed_events"], 12)
        self.assertEqual(r["scan"]["ambiguous_events"], 7)

    @patch("fpl.analytics.queries.run_query")
    def test_lookback_days_in_sql(self, mock_rq):
        mock_rq.return_value = _funnel_row()
        get_funnel(lookback_days=14)
        self.assertIn("interval 14 day", mock_rq.call_args[0][0])

    @patch("fpl.analytics.queries.run_query")
    def test_empty_response_returns_zeros(self, mock_rq):
        mock_rq.return_value = {"columns": [], "results": []}
        r = get_funnel()
        self.assertEqual(r["funnel"]["visits"], 0)
        self.assertIsNone(r["funnel"]["uploaded"]["pct"])
        self.assertEqual(r["scan"]["failed_events"], 0)
        self.assertEqual(r["feedback"]["feedback_loved"], 0)
        self.assertEqual(r["actions"]["action_changed"], 0)

    @patch("fpl.analytics.queries.run_query")
    def test_output_keys_present(self, mock_rq):
        mock_rq.return_value = _funnel_row()
        r = get_funnel()
        self.assertIn("lookback_days", r)
        self.assertIn("funnel", r)
        self.assertIn("scan", r)
        self.assertIn("pipeline", r)
        self.assertIn("feedback", r)
        self.assertIn("actions", r)
        for step in ("visits", "uploaded", "scan_valid", "projection_viewed", "decision_reached"):
            self.assertIn(step, r["funnel"])
        for k in ("swap", "hold"):
            self.assertIn(k, r["pipeline"])
        for k in ("any", "feedback_loved", "feedback_useful", "feedback_ok", "feedback_bad"):
            self.assertIn(k, r["feedback"])
        for k in ("any", "action_changed", "action_kept", "action_deciding"):
            self.assertIn(k, r["actions"])


# ── get_event_health ──────────────────────────────────────────────────────────

class TestGetEventHealth(unittest.TestCase):

    @patch("fpl.analytics.queries.run_query")
    def test_missing_events_flagged(self, mock_rq):
        mock_rq.return_value = _resp(_HEALTH_COLS, [
            ["page_viewed", 1000, 980, "2026-09-27", "2026-10-04"],
        ])
        r = get_event_health()
        self.assertIn("screenshot_uploaded", r["missing_events"])
        self.assertNotIn("page_viewed", r["missing_events"])

    @patch("fpl.analytics.queries.run_query")
    def test_core_funnel_missing_subset(self, mock_rq):
        # Only page_viewed fires — all other funnel steps missing
        mock_rq.return_value = _resp(_HEALTH_COLS, [
            ["page_viewed", 100, 98, "2026-09-27", "2026-10-04"],
        ])
        r = get_event_health()
        for step in _FUNNEL_STEPS:
            if step != "page_viewed":
                self.assertIn(step, r["core_funnel_missing"])
        self.assertNotIn("page_viewed", r["core_funnel_missing"])

    @patch("fpl.analytics.queries.run_query")
    def test_core_funnel_missing_empty_when_all_fire(self, mock_rq):
        rows = [[e, 10, 9, "2026-09-27", "2026-10-04"] for e in sorted(_EXPECTED_EVENTS)]
        mock_rq.return_value = _resp(_HEALTH_COLS, rows)
        r = get_event_health()
        self.assertEqual(r["core_funnel_missing"], [])
        self.assertEqual(r["missing_events"], [])

    @patch("fpl.analytics.queries.run_query")
    def test_events_per_session_computed(self, mock_rq):
        mock_rq.return_value = _resp(_HEALTH_COLS, [
            ["page_viewed", 1000, 980, "2026-09-27", "2026-10-04"],
            ["position_section_viewed", 3920, 980, "2026-09-27", "2026-10-04"],
        ])
        r = get_event_health()
        pv = next(e for e in r["events"] if e["event"] == "page_viewed")
        pos = next(e for e in r["events"] if e["event"] == "position_section_viewed")
        self.assertAlmostEqual(pv["events_per_session"], 1.02)
        self.assertAlmostEqual(pos["events_per_session"], 4.0)  # pre-fix bug signature

    @patch("fpl.analytics.queries.run_query")
    def test_events_per_session_none_when_sessions_zero(self, mock_rq):
        mock_rq.return_value = _resp(_HEALTH_COLS, [
            ["page_viewed", 0, 0, "", ""],
        ])
        r = get_event_health()
        self.assertIsNone(r["events"][0]["events_per_session"])

    @patch("fpl.analytics.queries.run_query")
    def test_extra_events_not_in_missing(self, mock_rq):
        all_rows = [[e, 5, 4, "2026-09-27", "2026-10-04"] for e in sorted(_EXPECTED_EVENTS)]
        extra = [["posthog_internal_event", 3, 3, "2026-09-27", "2026-10-04"]]
        mock_rq.return_value = _resp(_HEALTH_COLS, all_rows + extra)
        r = get_event_health()
        self.assertEqual(r["missing_events"], [])
        event_names = [e["event"] for e in r["events"]]
        self.assertIn("posthog_internal_event", event_names)

    @patch("fpl.analytics.queries.run_query")
    def test_lookback_days_in_sql(self, mock_rq):
        mock_rq.return_value = {"columns": [], "results": []}
        get_event_health(lookback_days=3)
        self.assertIn("interval 3 day", mock_rq.call_args[0][0])

    @patch("fpl.analytics.queries.run_query")
    def test_empty_response(self, mock_rq):
        mock_rq.return_value = {"columns": [], "results": []}
        r = get_event_health()
        self.assertEqual(r["events"], [])
        self.assertEqual(len(r["missing_events"]), len(_EXPECTED_EVENTS))
        self.assertEqual(sorted(r["core_funnel_missing"]), sorted(_FUNNEL_STEPS))

    @patch("fpl.analytics.queries.run_query")
    def test_output_keys_present(self, mock_rq):
        mock_rq.return_value = {"columns": [], "results": []}
        r = get_event_health()
        self.assertIn("lookback_days", r)
        self.assertIn("events", r)
        self.assertIn("missing_events", r)
        self.assertIn("core_funnel_missing", r)

    @patch("fpl.analytics.queries.run_query")
    def test_event_row_keys(self, mock_rq):
        mock_rq.return_value = _resp(_HEALTH_COLS, [
            ["page_viewed", 100, 95, "2026-09-27", "2026-10-04"],
        ])
        r = get_event_health()
        row = r["events"][0]
        for k in ("event", "total_events", "sessions", "events_per_session",
                  "first_seen", "last_seen"):
            self.assertIn(k, row)



# ── get_scan_failures ─────────────────────────────────────────────────────────

_FAIL_COLS = ["status", "message", "n", "sessions", "first_seen", "last_seen"]


class TestGetScanFailures(unittest.TestCase):

    @patch("fpl.analytics.queries.run_query")
    def test_groups_carry_status_message_count_and_timestamps(self, mock_rq):
        mock_rq.return_value = _resp(_FAIL_COLS, [
            ["VALID", "Expected 11 starters, found 15", 4, 3, "2026-10-04 09:00:00", "2026-10-04 13:30:00"],
            ["APP_ERROR", "Server error 500", 2, 2, "2026-10-05 08:00:00", "2026-10-05 08:05:00"],
        ])
        r = get_scan_failures()
        self.assertEqual(r["total"], 6)
        self.assertEqual(r["groups"][0], {
            "status": "VALID", "message": "Expected 11 starters, found 15",
            "count": 4, "sessions": 3,
            "first_seen": "2026-10-04 09:00:00", "last_seen": "2026-10-04 13:30:00",
        })
        self.assertEqual(r["groups"][1]["status"], "APP_ERROR")
        self.assertFalse(r["truncated"])

    @patch("fpl.analytics.queries.run_query")
    def test_missing_status_and_message_become_empty_strings(self, mock_rq):
        mock_rq.return_value = _resp(_FAIL_COLS, [[None, None, 1, 1, None, None]])
        g = get_scan_failures()["groups"][0]
        self.assertEqual((g["status"], g["message"], g["first_seen"], g["last_seen"]), ("", "", "", ""))
        self.assertEqual(g["count"], 1)

    @patch("fpl.analytics.queries.run_query")
    def test_none_counts_become_zero(self, mock_rq):
        mock_rq.return_value = _resp(_FAIL_COLS, [["UNSUPPORTED", "x", None, None, "", ""]])
        g = get_scan_failures()["groups"][0]
        self.assertEqual((g["count"], g["sessions"]), (0, 0))

    @patch("fpl.analytics.queries.run_query")
    def test_empty_response(self, mock_rq):
        mock_rq.return_value = _resp(_FAIL_COLS, [])
        self.assertEqual(get_scan_failures(),
                         {"lookback_days": 30, "total": 0, "groups": [], "truncated": False})

    @patch("fpl.analytics.queries.run_query")
    def test_truncated_flag_when_limit_hit(self, mock_rq):
        rows = [["S", "m%d" % i, 1, 1, "", ""] for i in range(_SCAN_FAILURE_LIMIT)]
        mock_rq.return_value = _resp(_FAIL_COLS, rows)
        r = get_scan_failures()
        self.assertTrue(r["truncated"])
        self.assertEqual(len(r["groups"]), _SCAN_FAILURE_LIMIT)

    @patch("fpl.analytics.queries.run_query")
    def test_sql_filters_event_and_window_and_is_deterministic(self, mock_rq):
        mock_rq.return_value = _resp(_FAIL_COLS, [])
        get_scan_failures(lookback_days=3)
        sql = mock_rq.call_args[0][0]
        self.assertIn("event = 'scan_failed'", sql)
        self.assertIn("interval 3 day", sql)
        self.assertIn("GROUP BY status, message", sql)
        self.assertIn("ORDER BY n DESC, last_seen DESC, status, message", sql)
        self.assertIn("LIMIT %d" % _SCAN_FAILURE_LIMIT, sql)

    @patch("fpl.analytics.queries.run_query")
    def test_default_lookback_and_output_keys(self, mock_rq):
        mock_rq.return_value = _resp(_FAIL_COLS, [])
        r = get_scan_failures()
        self.assertEqual(set(r), {"lookback_days", "total", "groups", "truncated"})
        self.assertEqual(r["lookback_days"], 30)
        self.assertIn("interval 30 day", mock_rq.call_args[0][0])


# ── client error handling (no HTTP) ──────────────────────────────────────────

class TestClientEnvGuard(unittest.TestCase):
    """Verify the client raises clearly when credentials are missing."""

    def test_missing_env_raises_environment_error(self):
        import os
        from fpl.analytics.client import run_query

        orig_key = os.environ.pop("POSTHOG_PERSONAL_API_KEY", None)
        orig_pid = os.environ.pop("POSTHOG_PROJECT_ID", None)
        try:
            with self.assertRaises(EnvironmentError) as ctx:
                run_query("SELECT 1")
            msg = str(ctx.exception)
            # Error message must not contain any credential values
            self.assertNotIn("phx_", msg)
            self.assertNotIn("phc_", msg)
            self.assertIn("POSTHOG_PERSONAL_API_KEY", msg)
        finally:
            if orig_key is not None:
                os.environ["POSTHOG_PERSONAL_API_KEY"] = orig_key
            if orig_pid is not None:
                os.environ["POSTHOG_PROJECT_ID"] = orig_pid


class TestRowsToDicts(unittest.TestCase):
    """rows_to_dicts must handle PostHog's real column format (plain strings)."""

    def test_plain_string_columns(self):
        from fpl.analytics.client import rows_to_dicts
        resp = {"columns": ["n", "swap"], "results": [[41, 2], [0, 1]]}
        self.assertEqual(
            rows_to_dicts(resp),
            [{"n": 41, "swap": 2}, {"n": 0, "swap": 1}],
        )

    def test_dict_columns(self):
        from fpl.analytics.client import rows_to_dicts
        self.assertEqual(rows_to_dicts(_resp(["n"], [[5]])), [{"n": 5}])

    def test_empty_response(self):
        from fpl.analytics.client import rows_to_dicts
        self.assertEqual(rows_to_dicts({}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
