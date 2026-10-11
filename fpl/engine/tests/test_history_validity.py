"""
Historical-row validity: unplayed fixtures and confirmed pre-arrival zeros are not evidence.

Team ids follow GW6_PARAMS: 1 ARS, 8 CRY, 12 IPS, 13 LEE, 14 LIV, 15 MCI, 16 MUN, 20 SUN.
The player under test plays for ARS (team 1); "prior club" fixtures are MCI v LIV (no ARS).
"""
import copy
import unittest

from fpl.engine.projection import (
    _build_fixture_completed, _build_fixture_teams, _is_unplayed, _usable_history, build_params, compute_xpts,
)
from fpl.engine.tests.test_projection import (
    FIXTURE_ARS_HOME_LEE, GABRIEL, GABRIEL_HISTORY, GW6_PARAMS, HAALAND, HAALAND_HISTORY, SAKA, SAKA_HISTORY,
)

# fixture id -> (team_h, team_a)
FIXTURE_TEAMS = {
    101: (1, 8),      # ARS v CRY   — involves ARS
    102: (14, 1),     # LIV v ARS   — involves ARS
    103: (1, 16),     # ARS v MUN   — involves ARS
    104: (1, 12),     # ARS v IPS   — involves ARS (not yet played in the tests)
    201: (15, 14),    # MCI v LIV   — does NOT involve ARS (prior club)
    202: (16, 20),    # MUN v SUN   — does NOT involve ARS
    301: (1, 8),      # one ARS fixture ...
    315: (15, 14),    # ... and one MCI fixture, for the established-player cases
}
FIXTURE_COMPLETED = {101: True, 102: True, 103: True, 201: True, 202: True, 301: True, 315: True}
PARAMS = {**GW6_PARAMS, "fixture_teams": FIXTURE_TEAMS, "fixture_completed": FIXTURE_COMPLETED}


def row(fixture, minutes, starts=None, h=1, a=0):
    """A completed history row by default; pass h=None / a=None for an unplayed one."""
    return {"fixture": fixture, "minutes": minutes,
            "starts": (1 if minutes >= 60 else 0) if starts is None else starts,
            "team_h_score": h, "team_a_score": a}


# A late arrival: one pre-arrival zero (not an ARS fixture), then two full ARS starts.
LATE_ARRIVAL = {**GABRIEL, "id": 650, "web_name": "Late", "starts": 2, "minutes": 180}
LATE_HISTORY = [row(201, 0), row(101, 90), row(102, 90)]


def run(player, history, params=PARAMS, fixture=FIXTURE_ARS_HOME_LEE):
    return compute_xpts(player, history, fixture, params)


class TestRowClassification(unittest.TestCase):
    def test_null_scores_mean_unplayed(self):
        self.assertTrue(_is_unplayed(row(104, 0, h=None, a=None)))

    def test_one_missing_score_is_not_a_completed_fixture(self):
        self.assertTrue(_is_unplayed(row(104, 0, h=1, a=None)))
        self.assertTrue(_is_unplayed(row(104, 0, h=None, a=1)))

    def test_zero_zero_is_a_completed_fixture(self):
        self.assertFalse(_is_unplayed(row(101, 0, h=0, a=0)))

    def test_rows_without_score_keys_are_not_treated_as_unplayed(self):
        self.assertFalse(_is_unplayed({"minutes": 90, "starts": 1}))        # legacy shape

    def test_fixture_teams_lookup_skips_malformed_entries(self):
        got = _build_fixture_teams([{"id": 1, "team_h": 2, "team_a": 3}, {"id": 2}, None, "x"])
        self.assertEqual(got, {1: (2, 3)})
        self.assertEqual(_build_fixture_teams(None), {})

    def test_build_params_carries_the_lookup_and_defaults_to_empty(self):
        bs = {"elements": [{"id": 1, "element_type": 1, "team": 1, "starts": 5, "minutes": 450,
                            "expected_goals_conceded_per_90": 1.0, "expected_goals_per_90": 0.0,
                            "expected_assists_per_90": 0.0}]}
        self.assertEqual(build_params(bs)["fixture_teams"], {})
        self.assertEqual(build_params(bs, [{"id": 9, "team_h": 1, "team_a": 2}])["fixture_teams"], {9: (1, 2)})


class TestSplit(unittest.TestCase):
    def test_pre_arrival_zero_is_left_out_of_both_counts(self):
        use = _usable_history(LATE_HISTORY, 1, FIXTURE_TEAMS)
        self.assertEqual([r["fixture"] for r in use.current_rows], [101, 102])
        self.assertEqual(use.n_damp, 2)
        self.assertEqual(use.n_team, 3)                    # the club still played that gameweek

    def test_prior_club_appearance_counts_for_damping_only(self):
        history = [row(201, 90), row(101, 90), row(102, 90)]
        use = _usable_history(history, 1, FIXTURE_TEAMS)
        current, n_damp = use.current_rows, use.n_damp
        self.assertEqual(len(current), 2)             # minutes / selection evidence
        self.assertEqual(n_damp, 3)                   # attacking-rate damping keeps the prior-club game

    def test_current_club_zero_is_kept(self):
        history = [row(101, 90), row(102, 0), row(103, 90)]
        use = _usable_history(history, 1, FIXTURE_TEAMS)
        current, n_damp = use.current_rows, use.n_damp
        self.assertEqual((len(current), n_damp), (3, 3))

    def test_unplayed_row_is_left_out_of_both_counts(self):
        history = [row(101, 90), row(102, 90), row(104, 0, h=None, a=None)]
        self.assertEqual(_usable_history(history, 1, FIXTURE_TEAMS).n_damp, 2)

    def test_unresolved_fixture_keeps_existing_behaviour(self):
        history = [row(999, 0), row(101, 90)]                       # 999 is not in the lookup
        use = _usable_history(history, 1, FIXTURE_TEAMS)
        current, n_damp = use.current_rows, use.n_damp
        self.assertEqual((len(current), n_damp), (2, 2))

    def test_empty_or_missing_lookup_changes_nothing(self):
        history = [row(201, 0), row(101, 90)]
        self.assertEqual(_usable_history(history, 1, {}).n_damp, 2)

    def test_row_with_no_fixture_id_is_kept(self):
        history = [{"minutes": 0, "starts": 0}, row(101, 90)]
        self.assertEqual(_usable_history(history, 1, FIXTURE_TEAMS).n_damp, 2)


class TestLateArrival(unittest.TestCase):
    """Pre-arrival zero followed by two 90-minute starts."""

    def test_minutes_evidence_is_the_two_real_starts(self):
        r = run(LATE_ARRIVAL, LATE_HISTORY)
        self.assertEqual(r["inputs"]["xMins"], 90.0)
        self.assertEqual(r["inputs"]["P_60plus"], 1.0)
        self.assertEqual(r["inputs"]["P_app"], 1.0)

    def test_damping_weight_uses_two_rows_not_three(self):
        r = run(LATE_ARRIVAL, LATE_HISTORY)
        self.assertAlmostEqual(r["inputs"]["w"], 2 / 4, places=6)

    def test_attacking_and_minutes_evidence_match_a_player_with_just_the_two_starts(self):
        two_starts = run(LATE_ARRIVAL, [row(101, 90), row(102, 90)])
        late = run(LATE_ARRIVAL, LATE_HISTORY)
        for key in ("xMins", "P_app", "P_60plus", "w", "xG_d", "xA_d"):
            self.assertEqual(late["inputs"][key], two_starts["inputs"][key], key)
        for key in ("xAppPts", "xGoalPts", "xAssistPts", "xBonus"):
            self.assertEqual(late["components"][key], two_starts["components"][key], key)

    def test_only_the_team_xgc_weight_still_sees_the_gameweek_he_missed(self):
        # the club played three gameweeks, so its xGC is weighted on three, not two
        two_starts = run(LATE_ARRIVAL, [row(101, 90), row(102, 90)])
        late = run(LATE_ARRIVAL, LATE_HISTORY)
        self.assertNotEqual(late["inputs"]["xGC_d"], two_starts["inputs"]["xGC_d"])

    def test_without_the_lookup_the_old_behaviour_is_unchanged(self):
        no_lookup = run(LATE_ARRIVAL, LATE_HISTORY, params=GW6_PARAMS)      # no "fixture_teams" key
        self.assertEqual(no_lookup["inputs"]["xMins"], 60.0)                 # (0+90+90)/3
        self.assertEqual(no_lookup["inputs"]["P_60plus"], 0.5)
        self.assertLess(no_lookup["xPts"], run(LATE_ARRIVAL, LATE_HISTORY)["xPts"])

    def test_confidence_labels_follow_the_intended_counts(self):
        r = run(LATE_ARRIVAL, LATE_HISTORY)
        self.assertIn("Small sample — 2 GW(s) of data", r["risks"])


class TestGenuineCurrentClubZero(unittest.TestCase):
    def test_a_completed_zero_for_the_current_club_still_counts_as_non_selection(self):
        history = [row(101, 90), row(102, 0), row(103, 90)]                 # a real DNP in the middle
        r = run(GABRIEL, history)
        self.assertAlmostEqual(r["inputs"]["xMins"], 0.65 * 60 + 0.35 * 60, places=4)
        self.assertAlmostEqual(r["inputs"]["w"], 3 / 5, places=6)

    def test_it_matches_the_engine_without_a_lookup(self):
        history = [row(101, 90), row(102, 0), row(103, 90)]
        self.assertEqual(run(GABRIEL, history)["xPts"], run(GABRIEL, history, params=GW6_PARAMS)["xPts"])


class TestUnplayedFixture(unittest.TestCase):
    def test_unplayed_row_is_not_a_zero_minute_observation(self):
        played = [row(101, 90), row(102, 90), row(103, 90)]
        with_unplayed = played + [row(104, 0, h=None, a=None)]
        a, b = run(GABRIEL, played), run(GABRIEL, with_unplayed)
        self.assertEqual(a["xPts"], b["xPts"])
        self.assertEqual(a["inputs"], b["inputs"])
        self.assertEqual(b["inputs"]["xMins"], 90.0)

    def test_without_the_fix_an_unplayed_row_would_have_depressed_minutes(self):
        # same rows scored as if the fixture had been played: the zero is then real evidence
        scored_zero = [row(101, 90), row(102, 90), row(103, 90), row(104, 0)]
        self.assertLess(run(GABRIEL, scored_zero)["inputs"]["xMins"], 90.0)

    def test_unplayed_rows_are_excluded_even_when_the_lookup_is_empty(self):
        rows = [row(101, 90), row(102, 90), row(104, 0, h=None, a=None)]
        r = run(GABRIEL, rows, params=GW6_PARAMS)
        self.assertEqual(r["inputs"]["xMins"], 90.0)
        self.assertAlmostEqual(r["inputs"]["w"], 2 / 4, places=6)

    def test_partial_scores_are_treated_as_incomplete(self):
        rows = [row(101, 90), row(102, 90), row(104, 0, h=1, a=None)]
        self.assertEqual(run(GABRIEL, rows)["inputs"]["xMins"], 90.0)

    def test_postponed_representation_is_not_a_completed_zero(self):
        postponed = {"fixture": 104, "minutes": 0, "starts": 0, "team_h_score": None, "team_a_score": None,
                     "kickoff_time": None}
        rows = [row(101, 90), row(102, 90), postponed]
        self.assertEqual(run(GABRIEL, rows)["inputs"]["xMins"], 90.0)


class TestEstablishedPlayersUnchanged(unittest.TestCase):
    CASES = ((HAALAND, HAALAND_HISTORY), (GABRIEL, GABRIEL_HISTORY), (SAKA, SAKA_HISTORY))

    def test_complete_histories_without_scores_are_identical_to_before(self):
        for player, history in self.CASES:
            self.assertEqual(run(player, history), run(player, history, params=GW6_PARAMS), player["web_name"])

    def test_completed_rows_for_the_current_club_are_identical_to_before(self):
        for player, history in self.CASES:
            rows = [row(300 + player["team"], h["minutes"], h["starts"]) for h in history]
            self.assertEqual(run(player, rows), run(player, history, params=GW6_PARAMS), player["web_name"])

    def test_inputs_are_not_mutated(self):
        history = copy.deepcopy(LATE_HISTORY)
        params = copy.deepcopy(PARAMS)
        run(LATE_ARRIVAL, history, params=params)
        self.assertEqual(history, LATE_HISTORY)
        self.assertEqual(params, PARAMS)


class TestPriorClubAppearance(unittest.TestCase):
    HISTORY = [row(201, 90), row(202, 90), row(101, 90), row(102, 90)]       # two prior-club games first

    def test_minutes_come_from_current_club_rows_only(self):
        r = run(LATE_ARRIVAL, self.HISTORY)
        self.assertEqual(r["inputs"]["xMins"], 90.0)

    def test_damping_counts_the_prior_club_games(self):
        r = run(LATE_ARRIVAL, self.HISTORY)
        self.assertAlmostEqual(r["inputs"]["w"], 4 / 6, places=6)

    def test_a_prior_club_bench_game_does_not_depress_current_club_minutes(self):
        r = run(LATE_ARRIVAL, [row(201, 20, starts=0), row(101, 90), row(102, 90)])
        self.assertEqual(r["inputs"]["xMins"], 90.0)
        self.assertAlmostEqual(r["inputs"]["w"], 3 / 5, places=6)


class TestFailSafe(unittest.TestCase):
    def test_unresolved_fixtures_behave_as_before(self):
        rows = [row(901, 0), row(902, 90), row(903, 90)]                     # none in the lookup
        self.assertEqual(run(LATE_ARRIVAL, rows), run(LATE_ARRIVAL, rows, params=GW6_PARAMS))

    def test_empty_lookup_behaves_as_before(self):
        params = {**GW6_PARAMS, "fixture_teams": {}}
        self.assertEqual(run(LATE_ARRIVAL, LATE_HISTORY, params=params),
                         run(LATE_ARRIVAL, LATE_HISTORY, params=GW6_PARAMS))

    def test_no_invented_team_information(self):
        # a fixture missing from the lookup is never classed as another club's, even with 0 minutes
        use = _usable_history([row(901, 0)], 1, FIXTURE_TEAMS)
        current, n = use.current_rows, use.n_damp
        self.assertEqual((len(current), n), (1, 1))

    def test_none_lookup_value_is_tolerated(self):
        params = {**GW6_PARAMS, "fixture_teams": None}
        self.assertEqual(run(LATE_ARRIVAL, LATE_HISTORY, params=params)["inputs"]["xMins"], 60.0)


class TestSampleCountSemantics(unittest.TestCase):
    """n for minutes, n for player damping, and the team xGC weight (which shares the damping w)."""

    def test_minutes_branch_follows_minutes_rows_not_damping_rows(self):
        # three damping rows but only two minutes rows: the "fewer than 3 rows" branch (plain average)
        history = [row(201, 90), row(101, 60, starts=1), row(102, 90)]
        r = run(LATE_ARRIVAL, history)
        self.assertAlmostEqual(r["inputs"]["xMins"], 75.0, places=4)         # (60+90)/2, not the 65/35 blend

    def test_recent_start_caps_use_minutes_rows(self):
        history = [row(201, 90), row(101, 0, starts=0), row(102, 0, starts=0)]
        r = run(LATE_ARRIVAL, history)
        self.assertLessEqual(r["inputs"]["xMins"], 25.0)                      # 0 recent starts at the current club

    def test_no_current_club_rows_falls_back_to_the_no_history_minutes(self):
        r = run(LATE_ARRIVAL, [row(201, 0), row(202, 0)])
        none = run(LATE_ARRIVAL, [])
        self.assertEqual(r["inputs"]["xMins"], none["inputs"]["xMins"])      # same as a player with no history
        self.assertAlmostEqual(r["inputs"]["w"], 0.0, places=6)


class TestTeamXgcWeighting(unittest.TestCase):
    """
    The club's xGC is a team-level quantity: the primary keeper's per-90 rate over the games
    the club has played. Its shrinkage weight therefore counts completed gameweeks in the
    player's history whichever club they were for (n_team), not the player's own damping
    count (n_damp). Otherwise a late arrival's club data would be shrunk harder just because
    he wasn't there yet.
    """
    def xgc_weight(self, r):
        team, league = PARAMS["team_gkp_xgc"][1], PARAMS["league_avg_xgc"]
        return (r["inputs"]["xGC_d"] - league) / (team - league)

    def test_pre_arrival_zero_still_counts_as_a_club_gameweek(self):
        r = run(LATE_ARRIVAL, LATE_HISTORY)                       # 3 completed gameweeks
        self.assertAlmostEqual(self.xgc_weight(r), 3 / 5, places=5)
        self.assertAlmostEqual(r["inputs"]["w"], 2 / 4, places=6)  # the player's own damping is separate

    def test_prior_club_appearances_count_too(self):
        r = run(LATE_ARRIVAL, [row(201, 90), row(202, 90), row(101, 90), row(102, 90)])
        self.assertAlmostEqual(self.xgc_weight(r), 4 / 6, places=5)

    def test_unplayed_and_in_progress_rows_do_not_count(self):
        completed = {**FIXTURE_COMPLETED, 104: False}
        params = {**PARAMS, "fixture_completed": completed}
        history = [row(101, 90), row(102, 90), row(104, 20, h=0, a=0)]
        r = run(GABRIEL, history, params=params)
        self.assertAlmostEqual(self.xgc_weight(r), 2 / 4, places=5)

    def test_ordinary_player_weight_is_the_same_as_before(self):
        for player, history in ((HAALAND, HAALAND_HISTORY), (GABRIEL, GABRIEL_HISTORY), (SAKA, SAKA_HISTORY)):
            n = len(history)
            r = run(player, history, params=GW6_PARAMS)
            team = GW6_PARAMS["team_gkp_xgc"][player["team"]]
            league = GW6_PARAMS["league_avg_xgc"]
            w = n / (n + 2.0)
            self.assertAlmostEqual(r["inputs"]["xGC_d"], w * team + (1 - w) * league, places=5, msg=player["web_name"])


class TestFixtureStatus(unittest.TestCase):
    """
    The /fixtures flags separate not started (started False), in progress (started True,
    finished_provisional False — scores and part-played minutes are already non-null) and
    finished (finished or finished_provisional True). Scores alone cannot tell the last two apart.
    """
    BASE = [row(101, 90), row(102, 90)]
    PARAMS_WITH_STATUS = {**PARAMS, "fixture_completed": {101: True, 102: True, 103: True, 104: False}}

    def test_in_progress_match_with_scores_and_part_played_minutes_is_excluded(self):
        live = row(104, 20, h=0, a=0)                              # non-null scores, 20 minutes so far
        self.assertTrue(_is_unplayed(live, {104: False}))
        with_live = run(GABRIEL, self.BASE + [live], params=self.PARAMS_WITH_STATUS)
        base = run(GABRIEL, self.BASE, params=self.PARAMS_WITH_STATUS)
        self.assertEqual(with_live["inputs"], base["inputs"])
        self.assertEqual(with_live["xPts"], base["xPts"])

    def test_without_the_status_the_same_row_is_counted(self):
        live = row(104, 20, h=0, a=0)
        self.assertFalse(_is_unplayed(live, {}))                    # scores alone: looks completed
        self.assertFalse(_is_unplayed(live, None))

    def test_fallback_when_no_status_is_available_is_the_documented_limitation(self):
        # fixtures lookup down: a live row has non-null scores, so only the score rule applies and it is counted
        live = row(104, 20, h=0, a=0)
        no_status = {**PARAMS, "fixture_completed": {}}
        r = run(GABRIEL, self.BASE + [live], params=no_status)
        self.assertLess(r["inputs"]["xMins"], 90.0)                         # the part-played row drags minutes down
        self.assertAlmostEqual(r["inputs"]["w"], 3 / 5, places=6)           # ... and is counted in the sample
        # the score rule still removes a genuinely unplayed row without any status
        unplayed = row(104, 0, h=None, a=None)
        self.assertEqual(run(GABRIEL, self.BASE + [unplayed], params=no_status)["inputs"]["xMins"], 90.0)

    def test_in_progress_zero_minutes_is_not_a_non_selection(self):
        live = row(104, 0, h=0, a=0)                                # 0-0 and he has not come on yet
        r = run(GABRIEL, self.BASE + [live], params=self.PARAMS_WITH_STATUS)
        self.assertEqual(r["inputs"]["xMins"], 90.0)

    def test_not_started_fixture_is_excluded_even_with_numeric_looking_scores(self):
        self.assertTrue(_is_unplayed(row(104, 0, h=0, a=0), {104: False}))

    def test_finished_provisional_counts_as_completed(self):
        fixtures = [{"id": 7, "team_h": 1, "team_a": 8, "finished": False, "finished_provisional": True}]
        self.assertEqual(_build_fixture_completed(fixtures), {7: True})
        self.assertFalse(_is_unplayed(row(7, 90), {7: True}))

    def test_status_builder_distinguishes_the_three_states(self):
        fixtures = [
            {"id": 1, "finished": True, "finished_provisional": True, "started": True},     # finished
            {"id": 2, "finished": False, "finished_provisional": True, "started": True},    # just ended
            {"id": 3, "finished": False, "finished_provisional": False, "started": True},   # in progress
            {"id": 4, "finished": False, "finished_provisional": False, "started": False},  # not started
        ]
        self.assertEqual(_build_fixture_completed(fixtures), {1: True, 2: True, 3: False, 4: False})

    def test_fixtures_without_status_flags_are_left_out_not_guessed(self):
        self.assertEqual(_build_fixture_completed([{"id": 9, "team_h": 1, "team_a": 2}, None, "x", {}]), {})
        self.assertEqual(_build_fixture_completed(None), {})

    def test_postponed_or_unresolved_scores_remain_excluded_by_the_score_rule(self):
        postponed = {"fixture": 105, "minutes": 0, "starts": 0, "team_h_score": None, "team_a_score": None}
        self.assertTrue(_is_unplayed(postponed, {}))                # no status available: conservative score rule
        self.assertTrue(_is_unplayed(postponed, {105: False}))

    def test_unresolved_fixture_falls_back_to_the_score_rule_only(self):
        self.assertFalse(_is_unplayed(row(999, 0, h=1, a=1), {104: False}))     # 999 has no status entry
        self.assertTrue(_is_unplayed(row(999, 0, h=None, a=None), {104: False}))

    def test_a_completed_fixture_with_missing_scores_is_still_excluded(self):
        self.assertTrue(_is_unplayed(row(101, 90, h=None, a=None), {101: True}))   # conflicting evidence: be conservative

    def test_genuine_completed_current_club_zero_is_kept(self):
        history = [row(101, 90), row(102, 0), row(103, 90)]
        r = run(GABRIEL, history, params=self.PARAMS_WITH_STATUS)
        self.assertAlmostEqual(r["inputs"]["w"], 3 / 5, places=6)
        self.assertAlmostEqual(r["inputs"]["xMins"], 0.65 * 60 + 0.35 * 60, places=4)

    def test_build_params_carries_the_status_lookup(self):
        bs = {"elements": [{"id": 1, "element_type": 1, "team": 1, "starts": 5, "minutes": 450,
                            "expected_goals_conceded_per_90": 1.0, "expected_goals_per_90": 0.0,
                            "expected_assists_per_90": 0.0}]}
        fixtures = [{"id": 5, "team_h": 1, "team_a": 2, "finished": False, "finished_provisional": False}]
        self.assertEqual(build_params(bs, fixtures)["fixture_completed"], {5: False})
        self.assertEqual(build_params(bs)["fixture_completed"], {})

if __name__ == "__main__":
    unittest.main(verbosity=2)
