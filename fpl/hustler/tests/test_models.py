import ast
import dataclasses
import inspect
import unittest
from datetime import date, datetime, timedelta, timezone

from fpl.hustler import models as m
from fpl.hustler.models import (
    AnalysisResult, AnalysisStatus, CommunityPolicy, Confidence, Conversation,
    EligibilityConfig, EvidenceLevel, MediaRef, Opportunity,
    OpportunityStatus, Platform, QuestionType, Source, Stance,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
OK_POLICY = CommunityPolicy(allows_replies=True, allows_ai_content=True, reviewed=date(2026, 10, 1))
PLAYERS = tuple(f"Player{i}" for i in range(15))


def _source(**kw):
    base = dict(source_id="r-fpl", platform=Platform.REDDIT, community="FantasyPL",
                url="https://reddit.com/r/FantasyPL", members=42000, policy=OK_POLICY)
    return Source(**{**base, **kw})


def _conv(age_days=2, comments=34, **kw):
    base = dict(conversation_id="c1", source_id="r-fpl", platform=Platform.REDDIT,
                community="FantasyPL", post_id="abc", url="https://reddit.com/r/FantasyPL/comments/abc",
                title="Who to captain?", created_at=NOW - timedelta(days=age_days),
                num_comments=comments, media=(MediaRef("https://i.redd.it/x.jpg"),))
    return Conversation(**{**base, **kw})


def _assess(source=None, conv=None, config=EligibilityConfig()):
    return m.assess(source or _source(), conv or _conv(), NOW, config)


def _opp(oid="o1", **kw):
    return Opportunity(oid, _assess(**kw))


def _result(**kw):
    base = dict(question_type=QuestionType.PLAYER_PROJECTION, players_identified=PLAYERS,
                xi_available=False, bench_available=False, scanner_status="PARTIAL",
                analysis_status=AnalysisStatus.COMPLETE, confidence=Confidence.MODERATE,
                recommended_stance=Stance.REPLY)
    return AnalysisResult(**{**base, **kw})


class TestEligibility(unittest.TestCase):
    def test_both_gates_pass(self):
        a = _assess()
        self.assertTrue(a.eligible)
        self.assertEqual([g.name for g in a.gates], ["recent", "has_image"])
        self.assertEqual(a.why(), ["Eligible: YES", "✓ 2 days old", "✓ image attached", "34 comments"])

    def test_each_gate_blocks(self):
        cases = {
            "recent": dict(conv=_conv(age_days=16)),
            "has_image": dict(conv=_conv(media=())),
        }
        for gate, kw in cases.items():
            a = _assess(**kw)
            self.assertFalse(a.eligible, gate)
            self.assertEqual([g.name for g in a.gates if not g.passed], [gate])
            self.assertEqual(a.why()[0], "Eligible: NO")

    def test_non_image_media_does_not_count(self):
        self.assertFalse(_assess(conv=_conv(media=(MediaRef("https://v.redd.it/x", kind="video"),))).eligible)

    def test_window_is_zero_to_fifteen_days_and_configurable(self):
        self.assertTrue(_assess(conv=_conv(age_days=0)).eligible)
        self.assertTrue(_assess(conv=_conv(age_days=15)).eligible)
        self.assertFalse(_assess(conv=_conv(age_days=15.1)).eligible)
        self.assertFalse(_assess(conv=_conv(age_days=10), config=EligibilityConfig(0, 7)).eligible)
        with self.assertRaises(ValueError):
            EligibilityConfig(5, 1)

    def test_nothing_else_gates_an_opportunity(self):
        for src in (_source(policy=CommunityPolicy()), _source(members=None), _source(members=10)):
            a = _assess(source=src)
            self.assertTrue(a.eligible)
            self.assertEqual([g.name for g in a.gates], ["recent", "has_image"])
        self.assertIs(_opp(source=_source(policy=CommunityPolicy())).status, OpportunityStatus.NEW)

    def test_disabled_source_is_not_scanned(self):
        with self.assertRaises(ValueError):
            _assess(source=_source(enabled=False))

    def test_wrong_source_rejected(self):
        with self.assertRaises(ValueError):
            _assess(source=_source(source_id="other"))

    def test_naive_datetimes_rejected(self):
        with self.assertRaises(ValueError):
            _conv(created_at=datetime(2026, 10, 3))
        with self.assertRaises(ValueError):
            m.assess(_source(), _conv(), datetime(2026, 10, 5))


class TestOpportunity(unittest.TestCase):
    def test_ineligible_post_is_not_an_opportunity(self):
        with self.assertRaises(ValueError):
            Opportunity("o1", _assess(conv=_conv(age_days=30)))

    def test_ranking_is_newest_first_then_comments(self):
        old_busy = _opp("a", conv=_conv(age_days=5, comments=500))
        new_quiet = _opp("b", conv=_conv(age_days=1, comments=2))
        same_age_busy = _opp("c", conv=_conv(age_days=1, comments=40))
        self.assertEqual([o.opportunity_id for o in m.rank([old_busy, new_quiet, same_age_busy])],
                         ["c", "b", "a"])

    def test_community_size_does_not_affect_ranking(self):
        big = _opp("a", source=_source(members=900000))
        small = _opp("b", source=_source(members=10))
        self.assertEqual([o.opportunity_id for o in m.rank([small, big])], ["a", "b"])  # id tie-break

    def test_ranking_is_deterministic_on_ties(self):
        a, b = _opp("a"), _opp("b")
        self.assertEqual(m.rank([b, a]), m.rank([a, b]))

    def test_no_numeric_score(self):
        self.assertNotIn("score", " ".join(f.name for f in dataclasses.fields(Opportunity)))
        self.assertFalse(hasattr(_opp(), "opportunity_score"))

    def test_status_transitions(self):
        o = _opp()
        sel = o.advance(OpportunityStatus.SELECTED)
        self.assertEqual(sel.advance(OpportunityStatus.ANALYSED).status, OpportunityStatus.ANALYSED)
        with self.assertRaises(ValueError):
            o.advance(OpportunityStatus.ANALYSED)       # must be selected first
        with self.assertRaises(ValueError):
            sel.advance(OpportunityStatus.ANALYSED).advance(OpportunityStatus.SELECTED)

    def test_no_published_state(self):
        values = {s.value for s in OpportunityStatus} | {s.value for s in AnalysisStatus}
        self.assertFalse(any(w in v for v in values for w in ("publish", "post", "sent", "submit")))


class TestPolicy(unittest.TestCase):
    def test_defaults_are_restrictive(self):
        p = CommunityPolicy()
        self.assertFalse(any([p.allows_replies, p.allows_personalised_advice, p.allows_self_promotion,
                              p.allows_external_links, p.allows_ai_content]))
        self.assertIsNone(p.reviewed)

    def test_violations(self):
        self.assertEqual(OK_POLICY.violations("Salah projects 6.1 xPts"), ())
        self.assertIn("self-promotion not allowed", OK_POLICY.violations("Try SquadCheck for this"))
        self.assertIn("external links not allowed", OK_POLICY.violations("see squadcheck.club"))
        self.assertIn("external links not allowed", OK_POLICY.violations("see https://x.io/a"))
        self.assertIn("personalised advice not allowed",
                      OK_POLICY.violations("Captain Haaland", personalised_advice=True))
        self.assertIn("AI-generated content not allowed", CommunityPolicy().violations("hello"))

    def test_permissive_policy_allows_everything(self):
        p = CommunityPolicy(True, True, True, True, True, date(2026, 10, 1))
        self.assertEqual(p.violations("Try SquadCheck: https://squadcheck.club", True), ())

    def test_draft_checked_against_policy(self):
        r = _result(public_reply="Use SquadCheck", reply_is_personalised=True)
        self.assertEqual(len(r.draft_violations(OK_POLICY)), 2)

    def test_constraints_tell_the_drafter_what_to_avoid(self):
        self.assertEqual(CommunityPolicy().constraints(),
                         ("no personalised advice", "no self-promotion", "no external links"))
        self.assertEqual(CommunityPolicy(allows_personalised_advice=True, allows_self_promotion=True,
                                         allows_external_links=True).constraints(), ())

    def test_drafting_blocked_reasons(self):
        self.assertIn("not reviewed", CommunityPolicy(allows_replies=True, allows_ai_content=True).drafting_blocked_reason())
        self.assertIn("replies", CommunityPolicy(allows_ai_content=True, reviewed=date(2026, 10, 1)).drafting_blocked_reason())
        self.assertIn("AI-generated", CommunityPolicy(allows_replies=True, reviewed=date(2026, 10, 1)).drafting_blocked_reason())
        self.assertIsNone(OK_POLICY.drafting_blocked_reason())


class TestApplyPolicy(unittest.TestCase):
    def test_compliant_draft_passes_unchanged(self):
        r = _result(public_reply="Salah projects 6.1 xPts")
        self.assertIs(m.apply_policy(r, OK_POLICY), r)

    def test_violating_draft_is_declined_not_rewritten(self):
        r = _result(public_reply="Try SquadCheck at squadcheck.club", reply_is_personalised=True)
        out = m.apply_policy(r, OK_POLICY)
        self.assertEqual(out.public_reply, "")
        self.assertIs(out.recommended_stance, Stance.DO_NOT_REPLY)
        self.assertIn("Reply declined:", out.private_findings[-1])
        self.assertIn("self-promotion", out.private_findings[-1])

    def test_analysis_survives_a_declined_reply(self):
        r = _result(public_reply="Try SquadCheck", private_findings=("Salah 6.1 xPts",))
        out = m.apply_policy(r, OK_POLICY)
        self.assertIs(out.analysis_status, AnalysisStatus.COMPLETE)
        self.assertEqual(out.players_identified, r.players_identified)
        self.assertEqual(out.private_findings[0], "Salah 6.1 xPts")

    def test_blocked_community_declines_even_a_clean_draft(self):
        out = m.apply_policy(_result(public_reply="Salah projects 6.1 xPts"), CommunityPolicy())
        self.assertEqual(out.public_reply, "")
        self.assertIn("not reviewed", out.private_findings[-1])

    def test_blocked_community_with_no_draft_still_records_decline(self):
        out = m.apply_policy(_result(), CommunityPolicy())
        self.assertIs(out.recommended_stance, Stance.DO_NOT_REPLY)

    def test_input_is_not_mutated(self):
        r = _result(public_reply="Try SquadCheck")
        m.apply_policy(r, OK_POLICY)
        self.assertEqual(r.public_reply, "Try SquadCheck")


class TestAnalysisEvidence(unittest.TestCase):
    def test_fifteen_players_alone_is_evidence(self):
        r = _result()
        self.assertEqual(r.evidence_available, EvidenceLevel.PLAYERS_ONLY)
        self.assertTrue(r.evidence_met)

    def test_fewer_than_fifteen_is_insufficient(self):
        r = _result(players_identified=PLAYERS[:10], analysis_status=AnalysisStatus.INSUFFICIENT_EVIDENCE,
                    confidence=Confidence.VERY_LOW, recommended_stance=Stance.DO_NOT_REPLY)
        self.assertEqual(r.evidence_available, EvidenceLevel.INSUFFICIENT)

    def test_xi_question_needs_xi(self):
        with self.assertRaises(ValueError):
            _result(question_type=QuestionType.STARTING_XI)     # complete without XI
        r = _result(question_type=QuestionType.STARTING_XI, xi_available=True, bench_available=True)
        self.assertEqual(r.evidence_available, EvidenceLevel.PLAYERS_AND_XI)

    def test_xi_question_with_players_only_is_insufficient(self):
        r = _result(question_type=QuestionType.BENCH_ORDER,
                    analysis_status=AnalysisStatus.INSUFFICIENT_EVIDENCE,
                    confidence=Confidence.LOW, recommended_stance=Stance.DO_NOT_REPLY)
        self.assertFalse(r.evidence_met)
        self.assertEqual(r.public_reply, "")

    def test_insufficient_cannot_carry_reply_or_high_confidence(self):
        base = dict(question_type=QuestionType.BENCH_ORDER,
                    analysis_status=AnalysisStatus.INSUFFICIENT_EVIDENCE,
                    confidence=Confidence.LOW, recommended_stance=Stance.DO_NOT_REPLY)
        for bad in (dict(public_reply="hi"), dict(confidence=Confidence.HIGH),
                    dict(recommended_stance=Stance.REPLY)):
            with self.assertRaises(ValueError):
                _result(**{**base, **bad})

    def test_xi_requires_all_fifteen(self):
        with self.assertRaises(ValueError):
            _result(players_identified=PLAYERS[:11], xi_available=True,
                    analysis_status=AnalysisStatus.INSUFFICIENT_EVIDENCE,
                    confidence=Confidence.LOW, recommended_stance=Stance.DO_NOT_REPLY)

    def test_players_must_be_distinct_and_at_most_fifteen(self):
        with self.assertRaises(ValueError):
            _result(players_identified=PLAYERS[:14] + ("player0",))
        with self.assertRaises(ValueError):
            _result(players_identified=PLAYERS + ("Extra",))

    def test_do_not_reply_has_no_reply(self):
        with self.assertRaises(ValueError):
            _result(recommended_stance=Stance.DO_NOT_REPLY, public_reply="hi")

    def test_scanner_status_is_stored_raw(self):
        self.assertEqual(_result(scanner_status="PARTIAL").scanner_status, "PARTIAL")


class TestScanEvidence(unittest.TestCase):
    def _scan(self, status, n=15):
        return {"status": status, "players": [{"name": f"P{i}"} for i in range(n)]}

    def test_partial_scan_still_gives_players_but_not_xi(self):
        names, xi, bench = m.scan_evidence(self._scan("PARTIAL"))
        self.assertEqual(len(names), 15)
        self.assertFalse(xi or bench)

    def test_valid_scan_gives_xi_and_bench(self):
        names, xi, bench = m.scan_evidence(self._scan("VALID"))
        self.assertEqual((len(names), xi, bench), (15, True, True))

    def test_duplicates_collapse_and_short_lists_stay_short(self):
        scan = {"status": "AMBIGUOUS", "players": [{"name": "Salah"}, {"name": " salah "}, {"name": ""}]}
        self.assertEqual(m.scan_evidence(scan), (("Salah",), False, False))

    def test_valid_status_with_fewer_than_fifteen_is_not_xi(self):
        self.assertEqual(m.scan_evidence(self._scan("VALID", 11))[1:], (False, False))


class TestInternalAndSafety(unittest.TestCase):
    def test_internal_marker_cannot_be_disabled(self):
        with self.assertRaises(ValueError):
            Opportunity("o1", _assess(), internal=False)
        with self.assertRaises(ValueError):
            _result(internal=False)
        self.assertTrue(_opp().internal and _result().internal)

    def test_analytics_properties_are_internal(self):
        props = m.analytics_properties()
        self.assertEqual(props, {"internal": True, "origin": "hustler"})
        props["internal"] = False
        self.assertTrue(m.analytics_properties()["internal"])      # caller cannot mutate the source

    def test_no_network_or_posting_imports(self):
        tree = ast.parse(inspect.getsource(m))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        banned = {"requests", "urllib", "http", "httpx", "socket", "praw", "aiohttp", "smtplib", "os", "subprocess", "fpl"}
        self.assertEqual(imported & banned, set())

    def test_no_publishing_names(self):
        names = {n for n, _ in inspect.getmembers(m)}
        for cls in (Source, Conversation, Opportunity, AnalysisResult, CommunityPolicy):
            names |= {n for n, _ in inspect.getmembers(cls)}
            names |= {f.name for f in dataclasses.fields(cls)}
        bad = [n for n in names if any(w in n.lower() for w in ("publish", "submit", "send", "credential", "token", "password"))]
        self.assertEqual(bad, [])

    def test_media_is_metadata_only(self):
        self.assertEqual({f.name for f in dataclasses.fields(MediaRef)}, {"url", "kind"})
        with self.assertRaises(ValueError):
            MediaRef("")


class TestPlatformAgnostic(unittest.TestCase):
    def test_other_platforms_use_same_model(self):
        src = _source(source_id="d1", platform=Platform.DISCORD, community="fpl-chat")
        conv = _conv(source_id="d1", platform=Platform.DISCORD, community="fpl-chat")
        self.assertTrue(m.assess(src, conv, NOW).eligible)

    def test_only_source_mentions_reddit(self):
        src = inspect.getsource(m).lower()
        lines = [l for l in src.splitlines() if "reddit" in l]
        self.assertEqual(len(lines), 1)              # the Platform enum member


if __name__ == "__main__":
    unittest.main()
