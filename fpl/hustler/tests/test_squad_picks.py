"""Squad names that match several players: the operator chooses, nothing is guessed or discarded."""
import unittest

from fpl.engine.tests import test_pipeline as fx
from fpl.hustler.analyse import analyse, render
from fpl.hustler.tests.test_analyse import FakeAdapter, FakeLLM, MentionData, _player, conv_with, grounded

SQUAD = fx._ELEMENTS[:15]
FWD_IN_SQUAD = next(e for e in SQUAD if e["element_type"] == 4)
KING_X = _player(7781, "King", 4, 14, "Joshua")        # two forwards, both free to fill the open forward slot
KING_Y = _player(7782, "King", 4, 15, "Dan")


class KingData(MentionData):
    def __init__(self):
        super().__init__(extra=(KING_X, KING_Y))


def scan_with_king(image, elements):
    players = [{"name": ("King" if e["id"] == FWD_IN_SQUAD["id"] else e["web_name"]), "position": e["element_type"],
                "is_starting": None} for e in SQUAD]
    return {"status": "PARTIAL", "view_type": "UNKNOWN", "players": players, "message": "partial"}


def run(picks=None, llm=None):
    llm = llm or FakeLLM(grounded)
    return analyse(FakeAdapter(), conv_with("Who should I bench?"), KingData(), scan=scan_with_king, llm=llm, picks=picks), llm


class TestSquadChoice(unittest.TestCase):
    def test_an_ambiguous_squad_name_asks_and_does_not_discard_or_call_the_llm(self):
        result, llm = run()
        self.assertTrue(result.needs_selection)
        self.assertTrue(result.human_required)
        self.assertEqual(llm.calls, [])
        self.assertEqual([c["name"] for c in result.squad_choices], ["King"])
        self.assertEqual({c.player_id for c in result.squad_choices[0]["candidates"]}, {7781, 7782})
        self.assertIn("King", result.human_reason)

    def test_the_choice_is_asked_for_even_if_the_other_players_are_clear(self):
        result, _ = run()
        self.assertIsNone(result.squad_evidence)
        self.assertIsNone(result.draft_reply)

    def test_a_pick_lets_the_analysis_continue_with_that_player(self):
        result, llm = run({"squad:King": 7782})
        self.assertFalse(result.human_required, result.human_reason)
        self.assertIn(7782, {p["player_id"] for p in result.squad_evidence["players"]})
        self.assertNotIn(7781, {p["player_id"] for p in result.squad_evidence["players"]})

    def test_a_pick_that_is_not_a_player_with_that_name_is_refused(self):
        result, llm = run({"squad:King": FWD_IN_SQUAD["id"]})
        self.assertTrue(result.human_required)
        self.assertIn("Invalid pick", result.human_reason)
        self.assertEqual(llm.calls, [])

    def test_a_squad_player_cannot_be_skipped(self):
        result, _ = run({"squad:King": None})
        self.assertIn("cannot be skipped", result.human_reason)

    def test_mention_picks_are_not_mistaken_for_squad_picks(self):
        result, _ = run({"squad:King": 7782, "Nobody": 1})
        self.assertIn("Invalid pick", result.human_reason)      # unknown mention, as before
        self.assertTrue(result.human_required)

    def test_the_cli_text_tells_the_operator_how_to_choose(self):
        result, _ = run()
        text = render(result)
        self.assertIn('--pick "squad:King=<id>"', text)
        self.assertIn("7781", text)
        self.assertIn("NEEDS SELECTION", text)


# ── two squad players who share a name: each entry is asked separately ─────────
PALMER_GK = _player(7791, "Palmer", 1, 14, "Alex")
PALMER_MID = _player(7792, "Palmer", 3, 15, "Cole")


class PalmerData(MentionData):
    def __init__(self):
        super().__init__(extra=(PALMER_GK, PALMER_MID))


def palmer_scan(image, elements):
    players = []
    for p in fx._make_scan()["players"]:
        p = {k: v for k, v in p.items() if k != "position"}        # the real scanner gives no position
        if p["name"] in ("BetaGK", "BetaMID"):
            p["name"] = "Palmer"
        players.append(p)
    return {"status": "AMBIGUOUS", "view_type": "PITCH", "players": players, "message": "Duplicate names: palmer", "warnings": []}


def run_palmer(picks=None):
    llm = FakeLLM(grounded)
    return analyse(FakeAdapter(), conv_with("Who should I bench?"), PalmerData(), scan=palmer_scan, llm=llm, picks=picks), llm


class TestSameNameSquadPlayers(unittest.TestCase):
    def test_each_repeated_entry_is_asked_separately(self):
        result, llm = run_palmer()
        self.assertTrue(result.needs_selection)
        self.assertEqual(llm.calls, [])
        self.assertEqual([c["key"] for c in result.squad_choices], ["Palmer#1", "Palmer#2"])
        self.assertIn("entry 1 of 2", result.squad_choices[0]["label"])
        for c in result.squad_choices:
            self.assertEqual({x.player_id for x in c["candidates"]}, {7791, 7792})

    def test_picking_both_entries_continues_and_uses_exactly_those_players(self):
        result, _ = run_palmer({"squad:Palmer#1": 7791, "squad:Palmer#2": 7792})
        self.assertFalse(result.human_required, result.human_reason)
        ids = {p["player_id"] for p in result.squad_evidence["players"]}
        self.assertTrue({7791, 7792} <= ids)
        self.assertEqual(len(ids), 15)

    def test_the_order_of_the_picks_is_the_users_not_ours(self):
        result, _ = run_palmer({"squad:Palmer#1": 7792, "squad:Palmer#2": 7791})
        self.assertFalse(result.human_required, result.human_reason)

    def test_one_pick_still_asks_for_the_other(self):
        result, llm = run_palmer({"squad:Palmer#1": 7791})
        self.assertTrue(result.needs_selection)
        self.assertEqual(llm.calls, [])
        self.assertEqual([c["key"] for c in result.squad_choices], ["Palmer#2"])

    def test_the_same_player_for_both_entries_is_refused_not_accepted(self):
        result, llm = run_palmer({"squad:Palmer#1": 7791, "squad:Palmer#2": 7791})
        self.assertTrue(result.human_required)
        self.assertIn("more than one entry", result.human_reason)
        self.assertEqual(llm.calls, [])

    def test_the_scan_split_is_trusted_when_only_the_repeated_name_made_it_ambiguous(self):
        result, _ = run_palmer({"squad:Palmer#1": 7791, "squad:Palmer#2": 7792})
        self.assertTrue(result.squad_evidence["xi_known"])

    def test_the_cli_text_names_the_entry(self):
        text = render(run_palmer()[0])
        self.assertIn('--pick "squad:Palmer#1=<id>"', text)
        self.assertIn('--pick "squad:Palmer#2=<id>"', text)


if __name__ == "__main__":
    unittest.main()
