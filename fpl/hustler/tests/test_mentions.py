import ast
import inspect
import unittest

from fpl.hustler import mentions as M
from fpl.hustler.mentions import (Candidate, Mention, MentionStatus, PickError, apply_picks,
                                  find_mentions, parse_pick, pending)

TEAMS = [{"id": 1, "short_name": "ARS", "name": "Arsenal"}, {"id": 2, "short_name": "EVE", "name": "Everton"},
         {"id": 3, "short_name": "BRE", "name": "Brentford"}, {"id": 4, "short_name": "MCI", "name": "Man City"},
         {"id": 5, "short_name": "BOU", "name": "Bournemouth"}, {"id": 6, "short_name": "FUL", "name": "Fulham"},
         {"id": 7, "short_name": "BHA", "name": "Brighton"}]


def el(pid, web, pos, team, first="", second=None, starts=10, minutes=900, removed=False):
    return {"id": pid, "web_name": web, "first_name": first, "second_name": second or web, "known_name": "",
            "element_type": pos, "team": team, "starts": starts, "minutes": minutes, "removed": removed}


def boot(extra=()):
    base = [el(1, "Saka", 3, 1, "Bukayo"), el(2, "Mykolenko", 2, 2, "Vitalii"), el(3, "Janelt", 3, 3, "Vitaly"),
            el(4, "Affengruber", 2, 4, "David"),
            el(10, "Cherki", 3, 4, "Rayan"), el(13, "De Cuyper", 2, 7, "Maxim"),
            el(14, "Ghost", 4, 1, "Gone", removed=True), el(15, "Haaland", 4, 4, "Erling")]
    return {"elements": base + list(extra), "teams": TEAMS}


KINGS = (el(11, "King", 4, 5, "Joshua", starts=30, minutes=2700), el(12, "King", 3, 6, "Tom", starts=1, minutes=40))
SQUAD = {1, 2, 3, 4}


def find(text, extra=KINGS, squad=SQUAD, **kw):
    return find_mentions(text, boot(extra), squad, **kw)


def by_text(mentions):
    return {m.text: m for m in mentions}


class TestResolution(unittest.TestCase):
    def test_full_example_post(self):
        text = "Saka to Cherki and Mykolenko to King. Affengruber to De Cuyper and Janelt to Barry"
        got = by_text(find(text))
        self.assertEqual({k: v.status for k, v in got.items()},
                         {"Cherki": MentionStatus.RESOLVED, "King": MentionStatus.AMBIGUOUS,
                          "De Cuyper": MentionStatus.RESOLVED, "Barry": MentionStatus.UNRESOLVED})
        self.assertEqual(got["Cherki"].relation, "proposed replacement for Saka")
        self.assertEqual(got["King"].relation, "proposed replacement for Mykolenko")
        self.assertEqual(got["De Cuyper"].relation, "proposed replacement for Affengruber")
        self.assertEqual(got["Barry"].relation, "proposed replacement for Janelt")

    def test_exactly_one_match_is_resolved(self):
        (m,) = find("Thinking about Cherki this week")
        self.assertIs(m.status, MentionStatus.RESOLVED)
        self.assertEqual((m.player_id, m.selected_by, m.relation), (10, None, "mentioned"))
        self.assertEqual(m.candidate.label(), "Rayan Cherki, MCI, MID")

    def test_multiple_matches_are_ambiguous_and_never_chosen(self):
        (m,) = find("Is King worth it?")
        self.assertIs(m.status, MentionStatus.AMBIGUOUS)
        self.assertIsNone(m.player_id)
        self.assertEqual({c.player_id for c in m.candidates}, {11, 12})

    def test_ambiguity_ignores_starts_and_minutes(self):
        """Same matched name stays AMBIGUOUS whichever player has more starts or minutes."""
        for a_starts, a_minutes, b_starts, b_minutes in ((30, 2700, 1, 40), (1, 40, 30, 2700),
                                                         (20, 1800, 20, 1800), (0, 0, 38, 3420), (38, 3420, 0, 0)):
            kings = (el(11, "King", 4, 5, "Joshua", starts=a_starts, minutes=a_minutes),
                     el(12, "King", 3, 6, "Tom", starts=b_starts, minutes=b_minutes))
            (m,) = find("Is King worth it?", extra=kings)
            self.assertIs(m.status, MentionStatus.AMBIGUOUS, (a_starts, b_starts))
            self.assertIsNone(m.player_id)
            self.assertEqual(len(m.candidates), 2)

    def test_a_name_shared_with_a_squad_player_stays_ambiguous(self):
        (m,) = find("Is King worth it?", squad={11})
        self.assertIs(m.status, MentionStatus.AMBIGUOUS)
        self.assertIs(by_text(apply_picks((m,), {"King": 11}))["King"].status, MentionStatus.SELECTED)

    def test_candidate_order_does_not_rank_players(self):
        flipped = tuple(reversed(KINGS))
        order = lambda extra: [c.player_id for c in find("King?", extra=extra)[0].candidates]
        self.assertEqual(order(KINGS), order(flipped))                # sorted by name, never by starts

    def test_no_match_is_dropped_unless_it_is_a_proposed_replacement(self):
        self.assertEqual(find("Barry is great and Zorro too"), ())
        (m,) = find("Janelt to Barry")
        self.assertIs(m.status, MentionStatus.UNRESOLVED)
        self.assertEqual((m.candidates, m.player_id), ((), None))

    def test_removed_players_are_not_candidates(self):
        self.assertEqual(find("What about Ghost?"), ())

    def test_squad_players_are_not_mentions(self):
        self.assertEqual(find("Saka and Mykolenko are both fine"), ())

    def test_lowercase_words_are_not_names(self):
        self.assertEqual(find("should i king or cherki", extra=()), ())

    def test_names_with_particles_and_hyphen_variants(self):
        got = by_text(find("Affengruber to De Cuyper"))
        self.assertEqual(got["De Cuyper"].player_id, 13)

    def test_longest_name_wins_over_a_part_of_it(self):
        texts = [m.text for m in find("Maybe De Cuyper next")]
        self.assertEqual(texts, ["De Cuyper"])

    def test_duplicates_collapse_and_order_follows_the_post(self):
        got = find("Cherki yes, Haaland no, Cherki again")
        self.assertEqual([m.text for m in got], ["Cherki", "Haaland"])

    def test_pattern_variants_set_the_relation(self):
        cases = {"Saka for Cherki": "Saka", "Saka -> Cherki": "Saka", "Saka → Cherki": "Saka",
                 "replace Saka with Cherki": "Saka", "Cherki instead of Saka": "Saka",
                 "Cherki in for Saka": "Saka"}
        for text, squad in cases.items():
            (m,) = find(text)
            self.assertEqual(m.relation, f"proposed replacement for {squad}", text)
        self.assertEqual(find("Cherki or Haaland?")[0].relation, "mentioned")

    def test_limit_prefers_linked_mentions(self):
        text = "Haaland, Cherki, De Cuyper and also Saka to King"
        got = find(text, limit=2)
        self.assertEqual(len(got), 2)
        self.assertIn("King", [m.text for m in got])              # the proposed replacement is kept


class TestInvariants(unittest.TestCase):
    CAND = (Candidate(1, "A B", "B", "ARS", "MID"), Candidate(2, "C B", "B", "EVE", "DEF"))

    def test_only_resolved_and_selected_carry_a_player_id(self):
        Mention("X", "mentioned", MentionStatus.RESOLVED, self.CAND[:1], 1)
        Mention("X", "mentioned", MentionStatus.SELECTED, self.CAND, 2, "human")
        for bad in (
            dict(status=MentionStatus.AMBIGUOUS, candidates=self.CAND, player_id=1),
            dict(status=MentionStatus.AMBIGUOUS, candidates=self.CAND[:1]),
            dict(status=MentionStatus.UNRESOLVED, candidates=self.CAND[:1]),
            dict(status=MentionStatus.RESOLVED, candidates=self.CAND, player_id=1),
            dict(status=MentionStatus.RESOLVED, candidates=self.CAND[:1], player_id=2),
            dict(status=MentionStatus.SELECTED, candidates=self.CAND, player_id=99, selected_by="human"),
            dict(status=MentionStatus.SELECTED, candidates=self.CAND, player_id=1),         # not by a human
            dict(status=MentionStatus.SKIPPED, candidates=self.CAND, player_id=1, selected_by="human"),
        ):
            with self.assertRaises(ValueError, msg=bad):
                Mention("X", "mentioned", **bad)

    def test_module_never_uses_the_first_match_helper_or_activity_heuristics(self):
        tree = ast.parse(inspect.getsource(M))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        self.assertNotIn("_find_id", names)
        self.assertNotIn("resolve_squad_smart", names)
        strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        for word in ("starts", "minutes", "now_cost", "selected_by_percent", "form", "total_points"):
            self.assertNotIn(word, strings)

    def test_no_io_or_llm_in_the_module(self):
        tree = ast.parse(inspect.getsource(M))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertEqual(imported & {"urllib", "http", "requests", "socket", "os", "anthropic", "subprocess"}, set())


class TestPicks(unittest.TestCase):
    def setUp(self):
        self.ms = find("Saka to Cherki and Mykolenko to King and Janelt to Barry")

    def test_parse_pick(self):
        self.assertEqual(parse_pick("King=222"), ("King", 222))
        self.assertEqual(parse_pick(" De Cuyper = 5 "), ("De Cuyper", 5))
        self.assertEqual(parse_pick("King=none"), ("King", None))
        self.assertEqual(parse_pick("King=NONE"), ("King", None))
        for bad in ("King", "=5", "King=", "King=abc", "King=1.5", "King=-"):
            with self.assertRaises(PickError, msg=bad):
                parse_pick(bad)

    def test_a_candidate_pick_becomes_selected(self):
        out = by_text(apply_picks(self.ms, {"King": 12}))
        k = out["King"]
        self.assertIs(k.status, MentionStatus.SELECTED)
        self.assertEqual((k.player_id, k.selected_by), (12, "human"))
        self.assertEqual(k.candidate.label(), "Tom King, FUL, MID")
        self.assertEqual(len(k.candidates), 2)                         # candidates kept for audit
        self.assertIs(out["Cherki"].status, MentionStatus.RESOLVED)    # others untouched

    def test_pick_is_case_and_spacing_insensitive(self):
        out = by_text(apply_picks(self.ms, {"  king ": 11}))
        self.assertEqual(out["King"].player_id, 11)

    def test_none_skips_the_mention(self):
        out = by_text(apply_picks(self.ms, {"King": None}))
        self.assertIs(out["King"].status, MentionStatus.SKIPPED)
        self.assertIsNone(out["King"].player_id)
        self.assertEqual(pending(tuple(out.values())), ())

    def test_arbitrary_player_ids_are_rejected(self):
        for pid in (10, 15, 1, 99999, 0):           # a real FPL id that is not a King, a squad id, nonsense
            with self.assertRaises(PickError, msg=pid) as ctx:
                apply_picks(self.ms, {"King": pid})
            self.assertIn("not a candidate", str(ctx.exception))

    def test_candidate_of_another_mention_is_rejected(self):
        two = find("Is King worth it? And what about Brown?", extra=KINGS + (
            el(21, "Brown", 3, 1, "A"), el(22, "Brown", 4, 2, "B")))
        with self.assertRaises(PickError):
            apply_picks(two, {"King": 21})
        self.assertIs(by_text(apply_picks(two, {"King": 11, "Brown": 22}))["Brown"].status, MentionStatus.SELECTED)

    def test_unknown_or_non_ambiguous_mentions_cannot_be_picked(self):
        with self.assertRaises(PickError) as a:
            apply_picks(self.ms, {"Haaland": 15})
        self.assertIn("no such mention", str(a.exception))
        for text in ("Cherki", "Barry"):
            with self.assertRaises(PickError) as b:
                apply_picks(self.ms, {text: 10})
            self.assertIn("not ambiguous", str(b.exception))

    def test_no_picks_changes_nothing(self):
        self.assertEqual(apply_picks(self.ms, {}), self.ms)
        self.assertEqual([m.text for m in pending(self.ms)], ["King"])

    def test_picks_do_not_mutate_the_input(self):
        before = tuple(self.ms)
        apply_picks(self.ms, {"King": 11})
        self.assertEqual(self.ms, before)


if __name__ == "__main__":
    unittest.main()
