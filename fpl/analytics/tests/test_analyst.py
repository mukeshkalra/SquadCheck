import copy
import unittest

from fpl.analytics import queries
from fpl.analytics.analyst import classify as cls
from fpl.analytics.analyst.models import EventClass, Evidence, Finding, RateStat, Window
from fpl.analytics.analyst.rules import run_rules
from fpl.analytics.analyst.stats import cap_confidence, wilson_interval
from fpl.analytics.analyst.models import Confidence
from fpl.analytics.analyst.report import build_report, render_markdown


def _r(n, d):
    return {"n": n, "d": d, "pct": round(n / d * 100, 1) if d else None}


FUNNEL = {
    "lookback_days": 30,
    "funnel": {"visits": 3, "uploaded": _r(3, 3), "scan_valid": _r(3, 3),
               "projection_viewed": _r(3, 3), "decision_reached": _r(3, 3)},
    "scan": {"failed_events": 1, "ambiguous_events": 0},
    "pipeline": {"swap": _r(3, 3), "hold": _r(0, 3)},
    "feedback": {"any": _r(2, 3), "feedback_loved": 2, "feedback_useful": 0, "feedback_ok": 0, "feedback_bad": 0},
    "actions": {"any": _r(1, 3), "action_changed": 1, "action_kept": 0, "action_deciding": 0},
}


def _ev(name, total, sessions):
    return {"event": name, "total_events": total, "sessions": sessions,
            "events_per_session": round(total / sessions, 2), "first_seen": "", "last_seen": ""}


HEALTH = {
    "lookback_days": 7,
    "events": [_ev("position_section_viewed", 16, 3), _ev("screenshot_uploaded", 4, 3),
               _ev("scan_valid", 3, 3), _ev("page_viewed", 3, 3), _ev("decision_reached", 3, 3),
               _ev("projection_viewed", 3, 3), _ev("scan_failed", 1, 1)],
    "missing_events": ["action_deciding_xi", "bench_viewed", "disambig_shown", "feedback_bad", "scan_ambiguous"],
    "core_funnel_missing": [],
}


def _all(report):
    return report["facts"] + report["observations"] + report["gaps"]


class TestClassification(unittest.TestCase):
    def test_every_expected_event_is_classified(self):
        unclassified = [e for e in queries._EXPECTED_EVENTS if cls.classify(e) is None]
        self.assertEqual(unclassified, [])

    def test_no_stale_classified_events(self):
        known = cls.CORE | cls.CONDITIONAL | cls.BEHAVIOURAL
        self.assertEqual(sorted(known - queries._EXPECTED_EVENTS), [])

    def test_classes_are_disjoint(self):
        self.assertFalse(cls.CORE & cls.CONDITIONAL or cls.CORE & cls.BEHAVIOURAL
                         or cls.CONDITIONAL & cls.BEHAVIOURAL)

    def test_core_matches_funnel_steps(self):
        self.assertEqual(cls.CORE, set(queries._FUNNEL_STEPS))

    def test_pinned_behavioural_events(self):
        for e in ("bench_viewed", "recommendation_viewed", "position_section_viewed"):
            self.assertEqual(cls.classify(e), EventClass.BEHAVIOURAL)

    def test_unknown_event_unclassified(self):
        self.assertIsNone(cls.classify("$web_vitals"))


class TestModelGuards(unittest.TestCase):
    def test_finding_requires_evidence(self):
        with self.assertRaises(ValueError):
            Finding("x", "text", ())

    def test_rate_requires_window(self):
        with self.assertRaises(ValueError):
            RateStat("x", 1, 3, None)

    def test_rate_rejects_n_above_d(self):
        with self.assertRaises(ValueError):
            RateStat("x", 4, 3, Window(30, "get_funnel"))

    def test_confidence_cap(self):
        self.assertEqual(cap_confidence(Confidence.HIGH, 3), Confidence.VERY_LOW)
        self.assertEqual(cap_confidence(Confidence.HIGH, 29), Confidence.LOW)
        self.assertEqual(cap_confidence(Confidence.HIGH, 99), Confidence.MODERATE)
        self.assertEqual(cap_confidence(Confidence.HIGH, 100), Confidence.HIGH)
        self.assertEqual(cap_confidence(Confidence.HIGH, None), Confidence.HIGH)

    def test_wilson_values(self):
        lo, hi = wilson_interval(3, 3)
        self.assertAlmostEqual(lo, 0.438, places=2)
        self.assertEqual(hi, 1.0)
        self.assertIsNone(wilson_interval(0, 0))


class TestRules(unittest.TestCase):
    def setUp(self):
        self.report = run_rules(FUNNEL, HEALTH)

    def test_every_finding_has_evidence(self):
        for f in _all(self.report):
            self.assertTrue(f.evidence, f.id)
            self.assertTrue(all(isinstance(e, Evidence) for e in f.evidence))

    def test_every_rate_carries_funnel_window_and_denominator(self):
        rates = [r for f in _all(self.report) for r in f.rates]
        self.assertTrue(rates)
        for r in rates:
            self.assertEqual(r.window, Window(30, "get_funnel"))
            self.assertIn("[last 30d, get_funnel]", r.render())
            self.assertIn(f"{r.n}/{r.d}", r.render())

    def test_not_observed_events_are_not_bugs(self):
        ids = {f.id: f for f in self.report["gaps"]}
        self.assertIn("G_not_observed_conditional", ids)
        self.assertIn("G_not_observed_behavioural", ids)
        self.assertNotIn("G_core_missing", ids)
        for k in ("G_not_observed_conditional", "G_not_observed_behavioural"):
            self.assertIn("no bug implied", ids[k].text)
        self.assertIn("bench_viewed", ids["G_not_observed_behavioural"].text)
        self.assertIn("scan_ambiguous", ids["G_not_observed_conditional"].text)

    def test_core_events_fact_when_none_missing(self):
        self.assertIn("F_core_events", {f.id for f in self.report["facts"]})

    def test_missing_core_event_flags_possible_instrumentation_failure(self):
        h = copy.deepcopy(HEALTH)
        h["events"] = [e for e in h["events"] if e["event"] != "decision_reached"]
        h["missing_events"] = h["missing_events"] + ["decision_reached"]
        rep = run_rules(FUNNEL, h)
        gap = {f.id: f for f in rep["gaps"]}["G_core_missing"]
        self.assertIn("decision_reached", gap.text)
        self.assertIn("may indicate instrumentation failure", gap.text)
        self.assertNotIn("F_core_events", {f.id for f in rep["facts"]})

    def test_window_mismatch_gap(self):
        self.assertIn("G_window_mismatch", {f.id for f in self.report["gaps"]})

    def test_upload_outcomes_reconcile(self):
        obs = {f.id: f for f in self.report["observations"]}["O_upload_outcomes"]
        self.assertIn("(4) equal", obs.text)
        self.assertIn("3+1+0", obs.text)

    def test_upload_multiplicity_is_neutral_observation(self):
        obs = {f.id: f for f in self.report["observations"]}
        self.assertNotIn("O_core_multifire", obs)
        text = obs["O_upload_multiplicity"].text
        self.assertIn("more than one upload in a session was observed", text)
        for word in ("instrumentation", "bug", "duplicate", "error"):
            self.assertNotIn(word, text)
        self.assertNotIn("G_core_missing", {f.id for f in self.report["gaps"]})

    def test_hold_not_observed_is_neutral(self):
        obs = {f.id: f for f in self.report["observations"]}["O_decision_mix"]
        self.assertIn("HOLD was not observed", obs.text)
        self.assertIn("valid outcome", obs.text)

    def test_zero_visits_yields_no_rates(self):
        f = copy.deepcopy(FUNNEL)
        f["funnel"] = {"visits": 0, "uploaded": _r(0, 0), "scan_valid": _r(0, 0),
                       "projection_viewed": _r(0, 0), "decision_reached": _r(0, 0)}
        f["pipeline"] = {"swap": _r(0, 0), "hold": _r(0, 0)}
        f["feedback"] = {"any": _r(0, 0)}
        f["actions"] = {"any": _r(0, 0)}
        rep = run_rules(f, HEALTH)
        self.assertFalse([r for x in _all(rep) for r in x.rates])
        self.assertIn("G_no_visits", {x.id for x in rep["gaps"]})

    def test_missing_window_omits_funnel_facts(self):
        f = {k: v for k, v in FUNNEL.items() if k != "lookback_days"}
        rep = run_rules(f, HEALTH)
        self.assertFalse([x for x in rep["facts"] if x.id.startswith("F_") and x.rates])
        self.assertIn("G_no_window_get_funnel", {x.id for x in rep["gaps"]})

    def test_none_inputs_do_not_crash(self):
        rep = run_rules(None, None)
        self.assertEqual(rep["facts"], [])


class TestReport(unittest.TestCase):
    def setUp(self):
        self.report = build_report(FUNNEL, HEALTH)
        self.md = render_markdown(self.report)

    def test_exactly_six_sections_in_order(self):
        heads = [l for l in self.md.splitlines() if l.startswith("## ")]
        self.assertEqual(heads, [
            "## 1. Known facts", "## 2. Interesting observations", "## 3. Unknowns / data gaps",
            "## 4. Hypotheses", "## 5. Confidence", "## 6. Recommended next investigation"])

    def test_hypotheses_labelled_and_separate_from_facts(self):
        self.assertTrue(self.report.hypotheses)
        facts_md = self.md.split("## 2.")[0]
        self.assertNotIn("HYPOTHESIS", facts_md)
        hyp_lines = [l for l in self.md.splitlines() if l.startswith("- HYPOTHESIS (unverified):")]
        self.assertEqual(len(hyp_lines), len(self.report.hypotheses))
        for h in self.report.hypotheses:
            self.assertTrue(h.would_test)

    def test_small_n_confidence_is_very_low(self):
        by = {c.conclusion: c for c in self.report.confidence}
        for k in ("Funnel step conversion rates", "SWAP/HOLD mix", "Feedback rate", "Action rate"):
            self.assertEqual(by[k].confidence, Confidence.VERY_LOW, k)
        self.assertEqual(by["The core funnel is instrumented end to end"].confidence, Confidence.HIGH)

    def test_large_n_can_reach_high(self):
        f = copy.deepcopy(FUNNEL)
        f["funnel"] = {"visits": 500, "uploaded": _r(400, 500), "scan_valid": _r(380, 400),
                       "projection_viewed": _r(380, 400), "decision_reached": _r(300, 380)}
        by = {c.conclusion: c for c in build_report(f, HEALTH).confidence}
        self.assertEqual(by["Funnel step conversion rates"].confidence, Confidence.HIGH)

    def test_next_step_small_sample_asks_for_session_split(self):
        self.assertIn("distinct_id", self.report.next_investigation)

    def test_next_step_core_missing_takes_priority(self):
        h = copy.deepcopy(HEALTH)
        h["missing_events"] = h["missing_events"] + ["decision_reached"]
        self.assertIn("end to end", build_report(FUNNEL, h).next_investigation)

    def test_no_product_change_language(self):
        text = self.md.lower()
        for phrase in ("should change", "we should", "redesign", "add a button", "remove the", "ship"):
            self.assertNotIn(phrase, text)

    def test_every_rate_line_has_window(self):
        for l in self.md.splitlines():
            if "(" in l and "%" in l and "CI" in l:
                self.assertIn("[last ", l)


if __name__ == "__main__":
    unittest.main()
