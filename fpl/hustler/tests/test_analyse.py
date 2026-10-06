import ast
import inspect
import json
import unittest
from datetime import datetime, timezone

from fpl.engine.tests import test_pipeline as fx
from fpl.hustler import analyse as A
from fpl.hustler.adapter import SourceUnavailable
from fpl.hustler.analyse import Analysis, LLMResponse, LLMUnavailable, analyse
from fpl.hustler.models import Conversation, EvidenceLevel, MediaRef, Platform

CONV = Conversation(
    conversation_id="reddit-fplratemyteam:abc", source_id="reddit-fplratemyteam",
    platform=Platform.REDDIT, community="FPLRateMyTeam", post_id="abc",
    url="https://www.reddit.com/r/FPLRateMyTeam/comments/abc/x/", title="Saka out for Cherki?",
    body="Wildcard used, 1 FT left, 0.3 in the bank",
    created_at=datetime(2026, 10, 5, tzinfo=timezone.utc), media=(MediaRef("https://i.redd.it/abc.jpeg"),))


class FakeAdapter:
    def __init__(self, image=b"img", error=None):
        self.image, self.error = image, error

    def fetch_image(self, conversation):
        if self.error:
            raise self.error
        return self.image


class FakeData:
    def __init__(self):
        self.summary_calls = []

    def bootstrap(self):
        return fx._make_bootstrap()

    def params(self, bootstrap):
        return fx._PARAMS

    def summaries(self, ids):
        self.summary_calls.append(list(ids))
        return {i: fx._SUMMARIES[i] for i in ids}


def end_turn(text):
    return LLMResponse(text, "end_turn")


class FakeLLM:
    """Records the prompt; responds with a function of the evidence it was given.
    `respond` may return an LLMResponse or a plain string (treated as a finished turn)."""
    def __init__(self, respond):
        self.respond, self.calls = respond, []

    def __call__(self, system, user):
        self.calls.append((system, user))
        out = self.respond(json.loads(user))
        return out if isinstance(out, LLMResponse) else end_turn(out)


def answer(reply, can_answer=True, reason="ok"):
    return json.dumps({"can_answer": can_answer, "reply": reply, "reason": reason})


def grounded(payload):
    p = max(payload["evidence"]["players"], key=lambda q: q["xPts"])
    return answer(f"{p['web_name']} projects {p['xPts']:.1f} xPts this week, so I'd keep him.")


FULL_SCAN = lambda image, elements: fx._make_scan()


def partial_scan(n=15, status="PARTIAL"):
    players = [{"name": e["web_name"], "position": e["element_type"], "is_starting": None}
               for e in fx._ELEMENTS[:n]]
    return lambda image, elements: {"status": status, "view_type": "UNKNOWN", "players": players,
                                    "message": "Could not determine the starting XI", "warnings": []}


def run(scan=FULL_SCAN, llm=None, adapter=None):
    llm = llm or FakeLLM(grounded)
    return analyse(adapter or FakeAdapter(), CONV, FakeData(), scan=scan, llm=llm), llm


class TestProgressiveEvidence(unittest.TestCase):
    def test_full_evidence_with_xi_and_bench(self):
        result, llm = run()
        self.assertEqual(result.evidence_level, EvidenceLevel.PLAYERS_AND_XI)
        ev = result.squad_evidence
        self.assertTrue(ev["xi_known"])
        self.assertEqual(len(ev["players"]), 15)
        self.assertEqual(sum(p["is_starting"] for p in ev["players"]), 11)
        self.assertIn(ev["action"], {"SWAP", "HOLD"})
        self.assertFalse(result.human_required)
        self.assertIn("xPts", result.draft_reply)

    def test_players_only_when_xi_unavailable(self):
        result, llm = run(scan=partial_scan())
        self.assertEqual(result.evidence_level, EvidenceLevel.PLAYERS_ONLY)
        ev = result.squad_evidence
        self.assertFalse(ev["xi_known"])
        self.assertEqual(len(ev["players"]), 15)
        self.assertTrue(all(p["is_starting"] is None for p in ev["players"]))
        self.assertNotIn("submitted_xi", ev)
        self.assertNotIn("recommended_xi", ev)
        self.assertEqual(result.scanner_status, "PARTIAL")      # raw status preserved
        self.assertFalse(result.human_required)

    def test_players_only_carries_existing_projection_fields(self):
        ev = run(scan=partial_scan())[0].squad_evidence
        p = ev["players"][0]
        for key in ("web_name", "position", "xPts", "confidence", "components", "inputs",
                    "drivers", "risks", "player_api"):
            self.assertIn(key, p)
        self.assertIsNotNone(ev["gameweek"])

    def test_fewer_than_fifteen_requires_human_and_skips_llm(self):
        result, llm = run(scan=partial_scan(n=11))
        self.assertTrue(result.human_required)
        self.assertIsNone(result.draft_reply)
        self.assertIsNone(result.squad_evidence)
        self.assertEqual(result.evidence_level, EvidenceLevel.INSUFFICIENT)
        self.assertIn("11 of 15", result.human_reason)
        self.assertEqual(llm.calls, [])

    def test_unidentifiable_names_require_human(self):
        players = [{"name": f"Nobody{i}"} for i in range(15)]
        result, llm = run(scan=lambda i, e: {"status": "PARTIAL", "players": players})
        self.assertTrue(result.human_required)
        self.assertIn("Could not reliably identify", result.human_reason)
        self.assertEqual(llm.calls, [])

    def test_question_url_and_image_always_preserved(self):
        result, _ = run(scan=partial_scan(n=3))
        self.assertIs(result.conversation, CONV)
        self.assertEqual(result.conversation.media[0].url, "https://i.redd.it/abc.jpeg")


class TestFailuresDoNotCrash(unittest.TestCase):
    def test_image_retrieval_failure(self):
        result, _ = run(adapter=FakeAdapter(error=SourceUnavailable("HTTP 429")))
        self.assertTrue(result.human_required)
        self.assertIn("Could not retrieve the image", result.human_reason)

    def test_scanner_exception(self):
        def boom(image, elements):
            raise RuntimeError("vision down")
        result, llm = run(scan=boom)
        self.assertTrue(result.human_required)
        self.assertIn("vision down", result.human_reason)
        self.assertEqual(llm.calls, [])

    def test_scanner_unsupported(self):
        result, _ = run(scan=lambda i, e: {"status": "UNSUPPORTED", "players": [], "message": "no"})
        self.assertTrue(result.human_required)
        self.assertEqual(result.scanner_status, "UNSUPPORTED")

    def test_fpl_data_failure(self):
        class Broken(FakeData):
            def summaries(self, ids):
                raise ConnectionError("fpl down")
        result = analyse(FakeAdapter(), CONV, Broken(), scan=partial_scan(), llm=FakeLLM(grounded))
        self.assertTrue(result.human_required)
        self.assertIn("fpl down", result.human_reason)


class TestDraft(unittest.TestCase):
    def _reply(self, text):
        return FakeLLM(lambda p: answer(text))

    def _is_human(self, llm, expect):
        result, _ = run(llm=llm)
        self.assertTrue(result.human_required)
        self.assertIsNone(result.draft_reply)
        self.assertIn(expect, result.human_reason)
        self.assertEqual(len(result.squad_evidence["players"]), 15)      # evidence always kept
        return result

    def test_can_answer_false_means_human_required(self):
        llm = FakeLLM(lambda p: answer("", can_answer=False, reason="the question needs players not in the squad"))
        self._is_human(llm, "the question needs players not in the squad")

    def test_can_answer_false_ignores_any_reply_text(self):
        llm = FakeLLM(lambda p: answer("Keep Saka.", can_answer=False, reason="not enough data"))
        self._is_human(llm, "not enough data")

    def test_truncated_response_is_never_a_draft(self):
        # Even if the cut-off text happens to be valid JSON, max_tokens means it is not trusted.
        llm = FakeLLM(lambda p: LLMResponse(grounded(p), "max_tokens"))
        result = self._is_human(llm, "truncated (max_tokens)")
        self.assertNotIn("valid JSON", result.human_reason)

    def test_truncated_plain_text_reports_truncation_not_bad_json(self):
        llm = FakeLLM(lambda p: LLMResponse("Cherki and Tarkowski are not in the squad so I can", "max_tokens"))
        result = self._is_human(llm, "truncated")
        self.assertNotIn("valid JSON", result.human_reason)

    def test_refusal_is_reported_with_its_category(self):
        llm = FakeLLM(lambda p: LLMResponse("", "refusal", "general_harms"))
        self._is_human(llm, "refusal: general_harms")
        self._is_human(FakeLLM(lambda p: LLMResponse("", "refusal")), "refusal: unspecified")

    def test_unexpected_stop_reason(self):
        self._is_human(FakeLLM(lambda p: LLMResponse(grounded(p), "pause_turn")), "unexpected LLM stop reason")

    def test_invalid_json(self):
        self._is_human(FakeLLM(lambda p: "Sure! Here is my reply"), "did not return valid JSON")

    def test_wrong_json_shape(self):
        for raw in ("[]", json.dumps({"reply": "hi"}), json.dumps({"can_answer": "yes", "reply": "x", "reason": "y"}),
                    json.dumps({"can_answer": True, "reply": None, "reason": "y"})):
            self._is_human(FakeLLM(lambda p, raw=raw: raw), "expected shape")

    def test_can_answer_true_with_empty_reply(self):
        self._is_human(self._reply("  "), "empty reply")

    def test_promotion_and_links_are_discarded(self):
        for text in ("Try SquadCheck, it rates Salah 6.1", "see https://example.com", "see foo.club"):
            self._is_human(self._reply(text), "Draft discarded")

    def test_invented_numbers_are_discarded(self):
        self._is_human(self._reply("AlphaMID projects 99.9 xPts"), "99.9")

    def test_numbers_from_evidence_or_post_are_allowed(self):
        p = run()[0].squad_evidence["players"][0]
        text = f"{p['web_name']} projects {p['xPts']:.1f}. With 1 FT and 0.3 in the bank, fine."
        result, _ = run(llm=self._reply(text))
        self.assertFalse(result.human_required, result.human_reason)

    def test_llm_unavailable_keeps_evidence(self):
        def down(system, user):
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
        result = analyse(FakeAdapter(), CONV, FakeData(), scan=FULL_SCAN, llm=down)
        self.assertTrue(result.human_required)
        self.assertIn("ANTHROPIC_API_KEY", result.human_reason)
        self.assertEqual(result.evidence_level, EvidenceLevel.PLAYERS_AND_XI)

    def test_llm_crash_keeps_evidence(self):
        def crash(system, user):
            raise ValueError("boom")
        result = analyse(FakeAdapter(), CONV, FakeData(), scan=FULL_SCAN, llm=crash)
        self.assertTrue(result.human_required)
        self.assertIn("LLM call failed (ValueError: boom)", result.human_reason)
        self.assertIsNotNone(result.squad_evidence)

    def test_llm_error_message_is_shown_but_secrets_are_redacted(self):
        import os
        old = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "env-secret-value-123"
        try:
            def leaky(system, user):
                raise RuntimeError("401 bad key env-secret-value-123 / sk-ant-api03-AbC_dEf-123 on output_config")
            result = analyse(FakeAdapter(), CONV, FakeData(), scan=FULL_SCAN, llm=leaky)
        finally:
            if old is None:
                os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                os.environ["ANTHROPIC_API_KEY"] = old
        self.assertIn("RuntimeError: 401 bad key", result.human_reason)
        self.assertIn("on output_config", result.human_reason)
        self.assertNotIn("env-secret-value-123", result.human_reason)
        self.assertNotIn("sk-ant-api03", result.human_reason)
        self.assertEqual(result.human_reason.count("[redacted]"), 2)

    def test_long_llm_error_messages_are_truncated(self):
        def noisy(system, user):
            raise RuntimeError("x" * 5000)
        result = analyse(FakeAdapter(), CONV, FakeData(), scan=FULL_SCAN, llm=noisy)
        self.assertLess(len(result.human_reason), 400)

    def test_prompt_carries_question_evidence_and_rules(self):
        result, llm = run(scan=partial_scan())
        (system, user), = llm.calls
        payload = json.loads(user)
        self.assertEqual(payload["post"]["title"], CONV.title)
        self.assertEqual(payload["post"]["body"], CONV.body)
        self.assertEqual(payload["evidence_level"], "players_only")
        self.assertEqual(len(payload["evidence"]["players"]), 15)
        for rule in ("ONLY the supplied data", "Never mention SquadCheck", "never include links",
                     "players_only", "can_answer", "120 words"):
            self.assertIn(rule, system)

    def test_prompt_forbids_expanding_abbreviations_and_outside_facts(self):
        system = " ".join(run()[1].calls[0][0].split())
        for phrase in ("do not expand abbreviations", "do not add outside knowledge",
                       "only if it appears in that same player's own data"):
            self.assertIn(phrase, system)

    def test_prompt_asks_for_partial_answers_not_all_or_nothing(self):
        system = " ".join(run()[1].calls[0][0].split())        # ignore line wrapping
        for phrase in ("Answer every part of the question the data supports",
                       "cannot assess it from what you have",
                       "at least part of the question",
                       "none of it can be addressed",
                       "what you covered and what you could not"):
            self.assertIn(phrase, system)
        self.assertNotIn("do not write one", system)           # the old all-or-nothing rule
        self.assertNotIn("genuinely relevant answer to the question", system)

    def test_default_llm_reports_missing_key_or_package(self):
        import os
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with self.assertRaises(LLMUnavailable):
                A.AnthropicLLM()("s", "u")
        finally:
            if old:
                os.environ["ANTHROPIC_API_KEY"] = old


class TestAnthropicRequest(unittest.TestCase):
    """The real request shape, checked against a stub SDK (no network, no key)."""

    def _call(self, message):
        import os
        import sys
        import types
        calls = []

        class Messages:
            def create(self, **kwargs):
                calls.append(kwargs)
                return message

        stub = types.ModuleType("anthropic")
        stub.Anthropic = lambda: types.SimpleNamespace(messages=Messages())
        saved = (sys.modules.get("anthropic"), os.environ.get("ANTHROPIC_API_KEY"))
        sys.modules["anthropic"], os.environ["ANTHROPIC_API_KEY"] = stub, "test-key"
        try:
            return A.AnthropicLLM()("sys", "user"), calls[0]
        finally:
            if saved[0] is None:
                sys.modules.pop("anthropic", None)
            else:
                sys.modules["anthropic"] = saved[0]
            if saved[1] is None:
                os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                os.environ["ANTHROPIC_API_KEY"] = saved[1]

    def _msg(self, stop_reason="end_turn", details=None, blocks=None):
        import types
        blocks = blocks if blocks is not None else [
            types.SimpleNamespace(type="thinking", thinking=""),
            types.SimpleNamespace(type="text", text='{"can_answer": false}')]
        return types.SimpleNamespace(content=blocks, stop_reason=stop_reason, stop_details=details)

    def test_request_uses_schema_low_effort_and_headroom(self):
        _, req = self._call(self._msg())
        self.assertEqual(req["model"], "claude-sonnet-5-5")
        self.assertEqual(req["max_tokens"], 4000)
        cfg = req["output_config"]
        self.assertEqual(cfg["effort"], "low")
        self.assertEqual(cfg["format"]["type"], "json_schema")
        schema = cfg["format"]["schema"]
        self.assertEqual(set(schema["required"]), {"can_answer", "reply", "reason"})
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("thinking", req)            # thinking settings left to effort
        self.assertNotIn("temperature", req)

    def test_response_carries_text_and_stop_reason(self):
        resp, _ = self._call(self._msg())
        self.assertEqual((resp.text, resp.stop_reason, resp.stop_category), ('{"can_answer": false}', "end_turn", None))

    def test_thinking_blocks_are_not_part_of_the_text(self):
        resp, _ = self._call(self._msg())
        self.assertNotIn("thinking", resp.text)

    def test_refusal_category_is_read(self):
        import types
        resp, _ = self._call(self._msg("refusal", types.SimpleNamespace(category="cyber"), blocks=[]))
        self.assertEqual((resp.stop_reason, resp.stop_category, resp.text), ("refusal", "cyber", ""))

    def test_max_tokens_is_passed_through(self):
        resp, _ = self._call(self._msg("max_tokens"))
        self.assertEqual(resp.stop_reason, "max_tokens")


class TestContractAndSafety(unittest.TestCase):
    def test_analysis_invariants(self):
        ok = dict(conversation=CONV, scanner_status="VALID", evidence_level=EvidenceLevel.PLAYERS_ONLY,
                  squad_evidence={"x": 1})
        Analysis(**ok, draft_reply="hi", human_required=False)
        for bad in (dict(draft_reply="hi", human_required=True, human_reason="r"),
                    dict(draft_reply=None, human_required=False),
                    dict(draft_reply=None, human_required=True),            # no reason
                    dict(draft_reply="hi", human_required=False, internal=False)):
            with self.assertRaises(ValueError):
                Analysis(**ok, **bad)
        with self.assertRaises(ValueError):
            Analysis(**{**ok, "squad_evidence": None}, draft_reply="hi", human_required=False)

    def test_results_are_internal(self):
        self.assertTrue(run()[0].internal)
        self.assertTrue(run(scan=partial_scan(n=2))[0].internal)

    def test_no_posting_or_analytics_surface(self):
        tree = ast.parse(inspect.getsource(A))
        names = set()
        for n in ast.walk(tree):
            for attr in ("id", "attr", "arg", "name"):
                if isinstance(getattr(n, attr, None), str):
                    names.add(getattr(n, attr).lower())
        for word in ("publish", "submit", "post_reply", "comment", "posthog", "capture", "password", "cookie"):
            self.assertEqual([x for x in names if word in x], [], word)

    def test_render_shows_draft_or_reason(self):
        self.assertIn("DRAFT (edit before posting)", A.render(run()[0]))
        text = A.render(run(scan=partial_scan(n=4))[0])
        self.assertIn("HUMAN REQUIRED", text)
        self.assertIn(CONV.url, text)

    def test_render_layout_puts_the_reddit_url_with_the_draft(self):
        lines = A.render(run()[0]).splitlines()
        self.assertEqual(lines[:4], ["Opportunity", f"Title: {CONV.title}", f"Reddit: {CONV.url}",
                                     "Image: https://i.redd.it/abc.jpeg"])
        self.assertEqual(lines[4:7], ["", "Question:", CONV.body])
        order = [lines.index(x) for x in ("Question:", "Evidence:", "DRAFT (edit before posting):")]
        self.assertEqual(order, sorted(order))

    def test_render_keeps_url_and_image_separate_and_exact(self):
        text = A.render(run()[0])
        self.assertEqual(text.count(CONV.url), 1)
        self.assertIn("Reddit: https://www.reddit.com/r/FPLRateMyTeam/comments/abc/x/", text)
        self.assertIn("Image: https://i.redd.it/abc.jpeg", text)
        self.assertNotEqual(CONV.url, CONV.media[0].url)

    def test_render_url_is_the_input_url_not_constructed(self):
        odd = Conversation(**{**CONV.__dict__, "url": "https://www.reddit.com/r/FPLRateMyTeam/comments/zzz/odd-slug/"})
        result = analyse(FakeAdapter(), odd, FakeData(), scan=FULL_SCAN, llm=FakeLLM(grounded))
        self.assertIn("Reddit: https://www.reddit.com/r/FPLRateMyTeam/comments/zzz/odd-slug/", A.render(result))

    def test_render_without_post_text_or_image_still_works(self):
        bare = Conversation(**{**CONV.__dict__, "body": "", "media": ()})
        result = analyse(FakeAdapter(error=SourceUnavailable("x")), bare, FakeData(), scan=FULL_SCAN)
        text = A.render(result)
        self.assertIn("(no text in the post, image only)", text)
        self.assertIn("Image: n/a", text)
        self.assertIn(f"Reddit: {bare.url}", text)

    def test_reddit_url_never_reaches_the_llm_or_the_draft(self):
        result, llm = run()
        sent = llm.calls[0][1]
        self.assertNotIn(CONV.url, sent)
        self.assertNotIn("reddit.com", sent)
        self.assertNotIn(CONV.media[0].url, sent)
        self.assertNotIn("reddit.com", result.draft_reply)
        self.assertEqual(result.conversation.url, CONV.url)          # still carried on the result

    def test_does_not_modify_the_scan_result(self):
        scan_result = fx._make_scan()
        before = json.dumps(scan_result, sort_keys=True, default=str)
        run(scan=lambda i, e: scan_result)
        self.assertEqual(json.dumps(scan_result, sort_keys=True, default=str), before)


if __name__ == "__main__":
    unittest.main()
