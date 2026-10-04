"""
Assemble and render the Growth Analyst report.

Deterministic: no LLM, no network except the optional live entry point.
Run live (read-only): python3 -m fpl.analytics.analyst.report
"""

from . import classify as cls
from .hypotheses import generate
from .models import Confidence, ConfidenceEntry, Report
from .rules import run_rules
from .stats import cap_confidence

SMALL_SAMPLE = 30


def _by_id(findings):
    return {f.id: f for f in findings}


def _confidence(facts, observations, gaps):
    f, o, g = _by_id(facts), _by_id(observations), _by_id(gaps)
    out = []

    def add(conclusion, requested, n, basis):
        out.append(ConfidenceEntry(conclusion, cap_confidence(requested, n), basis, n))

    if "F_event_count" in f:
        add("Instrumentation is delivering events", Confidence.HIGH, None, f["F_event_count"].text)
    if "F_core_events" in f:
        add("The core funnel is instrumented end to end", Confidence.HIGH, None, f["F_core_events"].text)
    if "G_core_missing" in g:
        add("The core funnel is instrumented end to end", Confidence.LOW, None, g["G_core_missing"].text)

    if "F_funnel_steps" in f:
        fs = f["F_funnel_steps"]
        add("Funnel step conversion rates", Confidence.HIGH, fs.n,
            f"n={fs.n} sessions. " + "; ".join(r.render() for r in fs.rates))
    for key, label in (("F_decisions", "SWAP/HOLD mix"), ("F_feedback", "Feedback rate"), ("F_actions", "Action rate")):
        if key in f:
            fx = f[key]
            add(label, Confidence.HIGH, fx.n, f"n={fx.n}. " + "; ".join(r.render() for r in fx.rates))

    unseen = [k for k in ("G_not_observed_conditional", "G_not_observed_behavioural") if k in g]
    if unseen:
        add("Whether unseen conditional/behavioural events are instrumented correctly", Confidence.VERY_LOW, None,
            "Absence is explained equally well by untaken paths and by an unwired event.")
    return out


def _next_investigation(facts, observations, gaps):
    f, g = _by_id(facts), _by_id(gaps)
    if "G_core_missing" in g:
        return ("Walk the product flow once end to end and confirm every core event arrives, "
                "starting with the missing one(s) named in the gaps.")
    if "G_no_visits" in g:
        return "Re-run get_funnel with a longer lookback_days to find a window that contains visits."
    visits = f["F_visits"].n if "F_visits" in f else None
    if visits is not None and visits < SMALL_SAMPLE and "G_no_segmentation" in g:
        return ("Split the sessions in the window by distinct_id and list each session's ordered event sequence "
                "(this needs a new deterministic query; the current tools do not return it). It tests the retry and "
                "internal-traffic hypotheses and gives a denominator before any rate is relied on.")
    if "G_window_mismatch" in g:
        return "Re-run get_funnel and get_event_health over the same lookback window so figures can be compared."
    return "No gap needs follow-up; re-run the analysis as the sample grows."


def build_report(funnel, health):
    r = run_rules(funnel, health)
    report = Report(facts=r["facts"], observations=r["observations"], gaps=r["gaps"])
    report.hypotheses = generate(r["facts"] + r["observations"] + r["gaps"])
    report.confidence = _confidence(r["facts"], r["observations"], r["gaps"])
    report.next_investigation = _next_investigation(r["facts"], r["observations"], r["gaps"])
    return report


def _finding_lines(f):
    lines = [f"- {f.text}"]
    lines += [f"  - {rate.render()}" for rate in f.rates]
    lines.append("  - evidence: " + ", ".join(f"{e.source}:{e.path}" for e in f.evidence))
    return lines


def render_markdown(report):
    out = ["# Growth Analyst report", "", "## 1. Known facts"]
    for f in report.facts:
        out += _finding_lines(f)
    out += ["", "## 2. Interesting observations"]
    for f in report.observations:
        out += _finding_lines(f)
    out += ["", "## 3. Unknowns / data gaps"]
    for f in report.gaps:
        out += _finding_lines(f)
    out += ["", "## 4. Hypotheses"]
    for h in report.hypotheses:
        out += [f"- HYPOTHESIS (unverified): {h.text}", f"  - from: {h.from_finding}", f"  - would test: {h.would_test}"]
    out += ["", "## 5. Confidence"]
    for c in report.confidence:
        n = f" (n={c.n})" if c.n is not None else ""
        out.append(f"- {c.conclusion}: **{c.confidence.label}**{n}. {c.basis}")
    out += ["", "## 6. Recommended next investigation", report.next_investigation]
    return "\n".join(out)


def main(funnel_days=30, health_days=7):
    from ..queries import get_event_health, get_funnel
    print(render_markdown(build_report(get_funnel(funnel_days), get_event_health(health_days))))


if __name__ == "__main__":
    main()
