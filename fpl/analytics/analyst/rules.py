"""
Deterministic rules: (get_funnel output, get_event_health output) -> Findings.

Every Finding cites Evidence; every rate carries its window and denominator.
Facts state only what the tools returned. Observations describe patterns without
causality. Gaps state what the data cannot support. Hypotheses live elsewhere.
"""

from . import classify as cls
from .models import EventClass, Evidence, Finding, RateStat, Window
from .stats import wilson_interval

_STEP_KEYS = ("uploaded", "scan_valid", "projection_viewed", "decision_reached")


def _window(out, source):
    days = (out or {}).get("lookback_days")
    return Window(days, source) if isinstance(days, int) and days > 0 else None


def _rate(name, r, window):
    n, d = r.get("n") or 0, r.get("d") or 0
    return RateStat(name, n, d, window, wilson_interval(n, d))


def _ev(source, path, value):
    return Evidence(source, path, value)


def _totals(health):
    return {e["event"]: e.get("total_events") or 0 for e in (health or {}).get("events", [])}


def _missing_by_class(health, wanted):
    return sorted(e for e in (health or {}).get("missing_events", []) if cls.classify(e) == wanted)


def facts(funnel, health):
    out = []
    fw, hw = _window(funnel, "get_funnel"), _window(health, "get_event_health")

    if fw:
        f = funnel.get("funnel", {})
        visits = f.get("visits") or 0
        out.append(Finding("F_visits", f"{visits} visit session(s) in the last {fw.lookback_days}d.",
                           (_ev("get_funnel", "funnel.visits", visits),), n=visits))
        if visits:
            rates = tuple(_rate(k, f[k], fw) for k in _STEP_KEYS if k in f)
            out.append(Finding("F_funnel_steps", "Step rates, each against its own denominator.",
                               tuple(_ev("get_funnel", f"funnel.{k}", f[k]) for k in _STEP_KEYS if k in f),
                               rates=rates, n=visits))
        p = funnel.get("pipeline", {})
        if (p.get("swap", {}).get("d") or 0) > 0:
            out.append(Finding(
                "F_decisions",
                f"Recorded decisions: SWAP {p['swap']['n']}, HOLD {p['hold']['n']}.",
                (_ev("get_funnel", "pipeline", p),),
                rates=(_rate("swap", p["swap"], fw), _rate("hold", p["hold"], fw)), n=p["swap"]["d"]))
        for key, label in (("feedback", "Feedback given"), ("actions", "Action reported")):
            blk = funnel.get(key, {})
            if blk.get("any", {}).get("d"):
                detail = ", ".join(f"{k.split('_', 1)[1] if '_' in k else k}={v}"
                                   for k, v in blk.items() if k != "any")
                out.append(Finding(f"F_{key}", f"{label} by sessions with a projection ({detail}).",
                                   (_ev("get_funnel", key, blk),),
                                   rates=(_rate(f"{key}_any", blk["any"], fw),), n=blk["any"]["d"]))
        s = funnel.get("scan", {})
        if s:
            out.append(Finding(
                "F_scan_events",
                f"scan_failed events: {s.get('failed_events', 0)}; scan_ambiguous events: "
                f"{s.get('ambiguous_events', 0)} (event counts, last {fw.lookback_days}d).",
                (_ev("get_funnel", "scan", s),)))

    if hw:
        missing = health.get("missing_events", [])
        core_missing = _missing_by_class(health, EventClass.CORE)
        if not core_missing:
            out.append(Finding(
                "F_core_events",
                f"All {len(cls.CORE)} core funnel events were observed in the last {hw.lookback_days}d.",
                (_ev("get_event_health", "missing_events (core subset)", core_missing),)))
        out.append(Finding(
            "F_event_count",
            f"{len(health.get('events', []))} distinct events observed in the last {hw.lookback_days}d.",
            (_ev("get_event_health", "events (count)", len(health.get("events", []))),)))
    return out


def observations(funnel, health):
    out = []
    fw = _window(funnel, "get_funnel")

    if fw:
        f = funnel.get("funnel", {})
        visits = f.get("visits") or 0
        steps = [(k, f[k]) for k in _STEP_KEYS if k in f and (f[k].get("d") or 0) > 0]
        if visits and steps:
            lowest_k, lowest = min(steps, key=lambda kv: kv[1]["n"] / kv[1]["d"])
            if lowest["n"] == lowest["d"]:
                text = "No funnel step lost a session in this window."
            else:
                text = f"Lowest step rate is {lowest_k} ({lowest['n']}/{lowest['d']})."
            out.append(Finding("O_step_loss", text, (_ev("get_funnel", f"funnel.{lowest_k}", lowest),), n=visits))

        p = funnel.get("pipeline", {})
        sw, ho = p.get("swap", {}), p.get("hold", {})
        if (sw.get("d") or 0) > 0 and (sw["n"] == 0 or ho["n"] == 0):
            seen, unseen = ("SWAP", "HOLD") if ho["n"] == 0 else ("HOLD", "SWAP")
            out.append(Finding(
                "O_decision_mix",
                f"Only {seen} was observed ({max(sw['n'], ho['n'])}/{sw['d']}); {unseen} was not observed. "
                "HOLD/no-change is a valid outcome.",
                (_ev("get_funnel", "pipeline", p),), n=sw["d"]))

    totals = _totals(health)
    if health and totals.get("screenshot_uploaded"):
        up = totals["screenshot_uploaded"]
        parts = {k: totals.get(k, 0) for k in ("scan_valid", "scan_failed", "scan_ambiguous")}
        outcomes = sum(parts.values())
        rel = "equal" if up == outcomes else "differ by %d from" % abs(up - outcomes)
        out.append(Finding(
            "O_upload_outcomes",
            f"screenshot_uploaded events ({up}) {rel} scan_valid+scan_failed+scan_ambiguous "
            f"events ({parts['scan_valid']}+{parts['scan_failed']}+{parts['scan_ambiguous']}) "
            f"[last {health['lookback_days']}d, event counts].",
            (_ev("get_event_health", "events[screenshot_uploaded].total_events", up),
             _ev("get_event_health", "events[scan_*].total_events", parts))))

    if health:
        rows = {e["event"]: e for e in health.get("events", [])}
        up = rows.get("screenshot_uploaded")
        if up and (up.get("events_per_session") or 0) > 1:
            out.append(Finding(
                "O_upload_multiplicity",
                f"screenshot_uploaded: {up['total_events']} events across {up['sessions']} sessions "
                f"({up['events_per_session']} per session); more than one upload in a session was observed "
                f"[last {health['lookback_days']}d, event counts].",
                (_ev("get_event_health", "events[screenshot_uploaded].events_per_session",
                     up["events_per_session"]),), n=up["sessions"]))
        cand = [e for e in rows.values() if not e["event"].startswith("$")
                and (e.get("events_per_session") or 0) >= 2]
        if cand:
            top = max(cand, key=lambda e: e["events_per_session"])
            out.append(Finding(
                "O_top_events_per_session",
                f"{top['event']} has the highest events-per-session: {top['events_per_session']} "
                f"({top['total_events']} events across {top['sessions']} sessions).",
                (_ev("get_event_health", f"events[{top['event']}].events_per_session", top["events_per_session"]),),
                n=top["sessions"]))
    return out


def gaps(funnel, health):
    out = []
    fw, hw = _window(funnel, "get_funnel"), _window(health, "get_event_health")

    for name, out_, win in (("get_funnel", funnel, fw), ("get_event_health", health, hw)):
        if out_ is not None and win is None:
            out.append(Finding(f"G_no_window_{name}", f"{name} output has no valid lookback_days; "
                               "its figures cannot be stated with a window and are omitted.",
                               (_ev(name, "lookback_days", (out_ or {}).get("lookback_days")),)))
    if fw and hw and fw.lookback_days != hw.lookback_days:
        out.append(Finding(
            "G_window_mismatch",
            f"The windows differ by design: get_funnel ({fw.lookback_days}d) measures funnel behaviour, "
            f"get_event_health ({hw.lookback_days}d) checks recent instrumentation. "
            "Figures from the two are not directly comparable.",
            (_ev("get_funnel", "lookback_days", fw.lookback_days),
             _ev("get_event_health", "lookback_days", hw.lookback_days))))

    if fw and (funnel.get("funnel", {}).get("visits") or 0) == 0:
        out.append(Finding("G_no_visits", f"No visits in the last {fw.lookback_days}d; no rate can be computed.",
                           (_ev("get_funnel", "funnel.visits", 0),)))

    if hw:
        core = _missing_by_class(health, EventClass.CORE)
        if core:
            out.append(Finding(
                "G_core_missing",
                f"Core funnel events not observed in the last {hw.lookback_days}d: {core}. "
                "This may indicate instrumentation failure, or no traffic reached that step.",
                (_ev("get_event_health", "missing_events (core subset)", core),)))
        for klass, label in ((EventClass.CONDITIONAL, "branch/error-path"),
                             (EventClass.BEHAVIOURAL, "optional-behaviour")):
            miss = _missing_by_class(health, klass)
            if miss:
                out.append(Finding(
                    f"G_not_observed_{klass.value}",
                    f"{len(miss)} {label} events not observed in the last {hw.lookback_days}d "
                    f"(no bug implied): {miss}.",
                    (_ev("get_event_health", f"missing_events ({klass.value} subset)", miss),)))

    if funnel is not None:
        out.append(Finding(
            "G_no_segmentation",
            "Neither tool returns per-user or per-source breakdowns, so internal/test sessions cannot be "
            "separated from external ones and sessions cannot be attributed to a channel.",
            (_ev("get_funnel", "(top-level keys)", sorted(funnel.keys())),)))
    return out


def run_rules(funnel, health):
    return {"facts": facts(funnel, health), "observations": observations(funnel, health),
            "gaps": gaps(funnel, health)}
