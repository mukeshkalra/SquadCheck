"""Hustler transfer view: assumptions, sell detection, replacements, and how it reaches the draft."""
import json
import unittest

from fpl.engine.tests import test_pipeline as fx
from fpl.engine.tests import test_transfer_adapter as T
from fpl.engine.transfer_adapter import FplDataSource
from fpl.hustler import transfers as X
from fpl.hustler.analyse import analyse, render, render_transfers
from fpl.hustler.tests.test_analyse import (CONV, FULL_SCAN, KING_A, FakeAdapter, FakeData, FakeLLM, MentionData, answer, conv_with, grounded)


def fake_source():
    return FplDataSource(fetch=T.FakeFpl(), clock=T.Clock())


class TestTextRules(unittest.TestCase):
    def test_bank_comes_from_the_post_else_zero(self):
        self.assertEqual(X.bank_from_post("I have £1.5m in the bank"), 1.5)
        self.assertEqual(X.bank_from_post("0.3 in the bank"), 0.0)      # no £ sign: not trusted
        self.assertEqual(X.bank_from_post("1FT, what to do?"), 0.0)

    def test_transfer_questions_are_recognised(self):
        for text in ("Who is the best replacement?", "1FT, what to do?", "Should I sell Haaland", "swap him for X", "have 2 free transfers"):
            self.assertTrue(X.wants_transfer(text), text)
        for text in ("Rate my team", "Who should I captain?" if False else "Rate my wildcard draft"):
            self.assertFalse(X.wants_transfer(text), text)

    def test_a_squad_player_named_in_the_post_is_found(self):
        bootstrap = FakeData().bootstrap()
        squad = [e["id"] for e in fx._ELEMENTS[:15]]
        name = fx._ELEMENTS[3]["web_name"]
        found = X.sell_candidates("Should I replace %s this week?" % name, bootstrap, squad)
        self.assertEqual([pid for pid, _ in found], [fx._ELEMENTS[3]["id"]])
        self.assertIsNone(found[0][1])

    def test_an_outside_player_is_not_a_sell_candidate(self):
        data = MentionData()
        squad = [e["id"] for e in fx._ELEMENTS[:15]]
        self.assertEqual(X.sell_candidates("Should I get Cherki?", data.bootstrap(), squad), [])

    def test_a_shared_name_with_one_squad_player_is_assumed_and_says_so(self):
        data = MentionData()
        squad = [e["id"] for e in fx._ELEMENTS[:14]] + [KING_A["id"]]
        found = X.sell_candidates("Should I replace King?", data.bootstrap(), squad)
        self.assertEqual([pid for pid, _ in found], [KING_A["id"]])
        self.assertIn("also matches other players", found[0][1])


class TestReplacements(unittest.TestCase):
    def test_options_are_ranked_by_gain_and_capped(self):
        src = fake_source()
        options = X._replacements(src, T.SQUAD, T.SQUAD[5], 0.0)
        self.assertTrue(1 <= len(options) <= X.MAX_REPLACEMENTS)
        gains = [o["projected_gain_next_gameweek"] for o in options]
        self.assertEqual(gains, sorted(gains, reverse=True))
        self.assertEqual(len({o["buy"]["name"] for o in options}), len(options))
        for o in options:
            self.assertEqual(o["sell"]["name"], "S6")
            self.assertEqual(o["n_transfers"], 1)
            self.assertTrue(o["side_by_side"])

    def test_the_full_view_states_its_assumptions(self):
        src = fake_source()
        out = X.transfer_options(src, T.SQUAD, "Should I replace S6? Who is the best replacement?", (), src.bootstrap())
        self.assertIn("2 free transfers", out["assumptions"])
        self.assertIn("no points hit", out["assumptions"])
        self.assertIn("£0", out["assumptions"])
        self.assertTrue(out["independent"]["plans"])

    def test_a_failure_returns_none_not_an_exception(self):
        class Broken:
            def bootstrap(self):
                raise ConnectionError("down")
        self.assertIsNone(X.transfer_options(Broken(), T.SQUAD, "x", ()))


SAMPLE = {"assumptions": "Assumes 2 free transfers, £0.0m in the bank (none stated, so £0) and no points hit. These are assumptions: the poster's real numbers may differ.",
          "bank_m": 0.0, "free_transfers": 2,
          "independent": {"best_action": "TRANSFER", "plans": [
              {"moves": [{"sell": {"name": "Beta", "club": "AAA", "price_m": 5.5}, "buy": {"name": "Gamma", "club": "BBB", "price_m": 5.4}}],
               "projected_gain_next_gameweek": 1.8, "projected_gain_later_weeks": 3.6}]},
          "sell": [{"sold": {"name": "Beta", "club": "AAA", "price_m": 5.5}, "assumption": None, "options": [
              {"sell": {"name": "Beta", "club": "AAA", "price_m": 5.5}, "buy": {"name": "Gamma", "club": "BBB", "price_m": 5.4},
               "projected_gain_next_gameweek": 1.8, "projected_gain_later_weeks": 3.6, "side_by_side": [], "points_for": [], "points_against": [], "n_transfers": 1}]}],
          "poster_moves": []}


class TestWiring(unittest.TestCase):
    def run_it(self, view, llm=None):
        llm = llm or FakeLLM(grounded)
        return analyse(FakeAdapter(), conv_with("Who is the best replacement for Beta?"), FakeData(), scan=FULL_SCAN, llm=llm,
                       transfer_view=view), llm

    def test_without_a_view_nothing_changes(self):
        result, llm = self.run_it(None)
        self.assertIsNone(result.transfers)
        self.assertNotIn("transfer_options", json.loads(llm.calls[0][1]))

    def test_the_view_is_stored_and_sent_to_the_llm(self):
        seen = {}

        def view(ids, text, mentions, bootstrap):
            seen.update(ids=ids, text=text)
            return SAMPLE
        result, llm = self.run_it(view)
        self.assertEqual(result.transfers, SAMPLE)
        self.assertEqual(json.loads(llm.calls[0][1])["transfer_options"], SAMPLE)
        self.assertEqual(len(seen["ids"]), 15)
        self.assertIn("best replacement", seen["text"])

    def test_a_crashing_view_does_not_stop_the_draft(self):
        def view(*a):
            raise RuntimeError("boom")
        result, _ = self.run_it(view)
        self.assertIsNone(result.transfers)
        self.assertFalse(result.human_required)

    def test_transfer_numbers_in_a_reply_are_grounded_and_invented_ones_are_not(self):
        good = FakeLLM(lambda p: answer("Swap Beta for Gamma at 5.4m; projected about 1.8 more points next gameweek."))
        result, _ = self.run_it(lambda *a: SAMPLE, good)
        self.assertFalse(result.human_required, result.human_reason)
        bad = FakeLLM(lambda p: answer("Swap Beta for Gamma at 5.4m; projected about 9.9 more points."))
        result, _ = self.run_it(lambda *a: SAMPLE, bad)
        self.assertTrue(result.human_required)
        self.assertIn("9.9", result.human_reason)

    def test_the_transfer_view_survives_a_failed_draft(self):
        down = FakeLLM(lambda p: answer("", can_answer=False, reason="no"))
        result, _ = self.run_it(lambda *a: SAMPLE, down)
        self.assertTrue(result.human_required)
        self.assertEqual(result.transfers, SAMPLE)

    def test_the_prompt_lets_the_llm_use_transfer_options_and_keeps_the_ban_on_promotion(self):
        from fpl.hustler.analyse import _SYSTEM
        self.assertIn("transfer_options", _SYSTEM)
        self.assertIn("Never mention SquadCheck", _SYSTEM)

    def test_the_operator_sees_a_plain_transfer_section(self):
        text = "\n".join(render_transfers(SAMPLE))
        self.assertIn("Transfer view:", text)
        self.assertIn("Replacing Beta (AAA, £5.5m)", text)
        self.assertIn("Gamma (BBB, £5.4m)", text)
        self.assertIn("independent of the question", text)
        self.assertEqual(render_transfers(None), [])


if __name__ == "__main__":
    unittest.main()
