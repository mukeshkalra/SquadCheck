"""Flow funnels (planner, bench step), their report facts, and a static check that every event the pages send is known."""
import re
import unittest
from pathlib import Path
from unittest import mock

from fpl.analytics import queries
from fpl.analytics.analyst import classify as cls
from fpl.analytics.analyst.report import build_report, render_markdown
from fpl.analytics.analyst.rules import flow_facts

ROOT = Path(__file__).resolve().parents[3]
ROW = {"cta_viewed": 20, "clicked": 10, "opened": 9, "requested": 8, "loaded": 7, "inspected": 5, "share_confirmed": 3,
       "compare_viewed": 4, "backed": 2, "save_prompt_opened": 1, "save_prompt_accepted": 0, "planner_errors": 1,
       "bench_shown": 6, "bench_submitted": 4, "bench_completed": 3, "bench_failed": 1, "bench_reupload": 1}


def flows(row=ROW):
    with mock.patch.object(queries, "run_query", return_value={"columns": list(row), "results": [list(row.values())]}):
        return queries.get_flow_funnels(30)


class TestFlowFunnels(unittest.TestCase):
    def test_steps_use_the_previous_step_as_denominator(self):
        f = flows()
        self.assertEqual(f["planner"]["clicked"], {"n": 10, "d": 20, "pct": 50.0})
        self.assertEqual(f["planner"]["plans_loaded"]["d"], 8)
        self.assertEqual(f["planner"]["plan_backed"], {"n": 2, "d": 4, "pct": 50.0})
        self.assertEqual(f["bench_step"]["submitted"], {"n": 4, "d": 6, "pct": 66.7})
        self.assertEqual(f["bench_step"]["completed"]["d"], 4)

    def test_empty_window_is_explicit(self):
        f = flows({k: 0 for k in ROW})
        self.assertIsNone(f["planner"]["clicked"]["pct"])
        self.assertEqual(f["bench_step"]["shown"], 0)

    def test_report_gets_planner_and_bench_facts(self):
        out = flow_facts(flows())
        ids = [x.id for x in out]
        self.assertIn("F_planner_funnel", ids)
        self.assertIn("F_planner_save_intent", ids)
        self.assertIn("F_bench_step", ids)

    def test_no_traffic_is_stated_not_assumed(self):
        out = flow_facts(flows({k: 0 for k in ROW}))
        self.assertEqual([x.id for x in out], ["F_planner_none"])

    def test_flows_are_optional(self):
        self.assertEqual(flow_facts(None), [])
        funnel = {"lookback_days": 30, "funnel": {"visits": 0}, "scan": {}, "pipeline": {}, "feedback": {}, "actions": {}}
        health = {"lookback_days": 7, "events": [], "missing_events": [], "core_funnel_missing": []}
        self.assertIsInstance(render_markdown(build_report(funnel, health)), str)


class TestEventRegistry(unittest.TestCase):
    PAGES = ("fpl/index.html", "fpl/planner.html")

    def sent(self):
        names = set()
        for page in self.PAGES:
            names |= set(re.findall(r"track\('([a-z][a-z_]+)'", (ROOT / page).read_text()))
        return {n for n in names if not n.endswith("_")}      # 'feedback_' / 'action_' are prefixes built at runtime

    def test_every_event_the_pages_send_is_classified(self):
        unknown = sorted(e for e in self.sent() if cls.classify(e) is None and e not in queries._EXPECTED_EVENTS)
        self.assertEqual(unknown, [])

    def test_new_flow_events_are_registered(self):
        for e in ("bench_step_shown", "bench_step_completed", "plan_my_move_clicked", "planner_opened", "planner_plans_loaded",
                  "planner_plan_backed", "home_tile_clicked", "upload_cta_clicked"):
            self.assertIn(e, queries._EXPECTED_EVENTS)
            self.assertIsNotNone(cls.classify(e))

    def test_core_funnel_is_unchanged(self):
        self.assertEqual(queries._FUNNEL_STEPS, ["page_viewed", "screenshot_uploaded", "scan_valid", "projection_viewed", "decision_reached"])

    def test_planner_sends_only_from_the_live_site(self):
        src = (ROOT / "fpl/planner.html").read_text()
        self.assertIn("location.hostname==='www.squadcheck.club'||location.hostname==='squadcheck.club'", src.replace(" ", ""))
        self.assertIn("LIVE_HOST && window.posthog", src)

    def test_planner_keeps_the_internal_flag_and_session_id(self):
        src = (ROOT / "fpl/planner.html").read_text()
        for needle in ("'sc_sid'", "'sc_internal'", "internal: true", "get('internal')", "window[area].getItem"):
            self.assertIn(needle, src)

    def test_planner_events_carry_no_typed_text(self):
        src = (ROOT / "fpl/planner.html").read_text()
        for call in re.findall(r"track\('planner_[a-z_]+'[^;]*;", src):
            self.assertNotRegex(call, r"\bwhy\b|S\.call|\bt\.value")


if __name__ == "__main__":
    unittest.main()
