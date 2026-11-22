"""
Tests for the home (upload) screen of fpl/index.html after the home redesign: four calls in value order
(transfer, chip, bench, captain), a working upload entry, honest example labelling and unchanged analytics.
Static checks on the page source: no network, no browser.
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HTML = (ROOT / "fpl" / "index.html").read_text(encoding="utf-8")
BASE = "fb219e6"      # the last commit before the planner work: committed analytics events must survive


def head_html():
    r = subprocess.run(["git", "show", BASE + ":fpl/index.html"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        raise unittest.SkipTest("no git history available")
    return r.stdout


UP = re.search(r'<section id="s-upload".*?(?=<section id="s-loading")', HTML, re.S).group(0)


def text(fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


class TestHomeContent(unittest.TestCase):
    def test_four_calls_in_value_order(self):
        titles = re.findall(r'<h3>(.*?)</h3>', UP)
        self.assertEqual(titles, ["Who do I buy?", "When do I use a chip?", "Who sits on my bench?", "Who do I captain?"])

    def test_headline_and_lead(self):
        self.assertIn("Transfers, chips, bench, captain.", text(UP))
        self.assertIn("Sorted every gameweek.", text(UP))

    def test_examples_are_labelled_as_made_up(self):
        self.assertIn("Example", UP)
        self.assertIn("made-up numbers", UP)

    def test_no_hard_coded_gameweek_on_the_home_screen(self):
        self.assertIsNone(re.search(r"\bGW\s?\d+\b|\bGameweek\s+\d+\b", text(UP)))

    def test_no_unverified_absolutes(self):
        t = text(UP).lower()
        for word in ("guaranteed", "always right", "never ", "zero "):
            self.assertNotIn(word, t)

    def test_no_features_that_are_not_built(self):
        t = text(UP).lower()
        for claim in ("team name", "reminder", "email you", "mini-league table"):
            self.assertNotIn(claim, t)      # there is no public team-name search, so it is not offered

    def test_free_no_login_no_card(self):
        self.assertIn("Free. No login. No card.", text(UP))
        self.assertIn("No login", text(UP))


class TestFplIdEntry(unittest.TestCase):
    def test_id_form_exists_and_is_numeric(self):
        self.assertIn('id="hm-form"', UP)
        self.assertRegex(UP, r'<input id="hm-id"[^>]*inputmode="numeric"')
        self.assertIn("Where do I find my FPL ID?", UP)

    def test_every_other_cta_opens_the_file_picker(self):
        self.assertEqual(UP.count('data-hm="focus"'), 6)       # four cards, final, sticky
        self.assertIn("fi.click()", HTML)

    def test_upload_is_the_primary_action_and_id_is_secondary(self):
        self.assertLess(UP.index('for="file-input" id="hm-main-cta"'), UP.index('id="hm-form"'))
        self.assertIn("Use my FPL ID instead", UP)
        self.assertIn("will not show until the next deadline passes", text(UP))

    def test_submit_goes_through_the_existing_manual_squad_path(self):
        self.assertIn("fetch('/api/fpl-team'", HTML)
        self.assertIn("body:JSON.stringify({manual_squad:t.manual_squad})", HTML)

    def test_results_show_where_the_team_came_from(self):
        self.assertIn('id="r-source"', HTML)
        self.assertIn("renderSource", HTML)

    def test_failed_lookup_nudges_the_upload_button(self):
        self.assertGreaterEqual(HTML.count("nudgeUpload();"), 3)      # lookup, forecast and unexpected failures
        self.assertIn(".hm-nudge{animation", HTML)

    def test_screenshot_upload_clears_the_id_source(self):
        self.assertIn("_fplSource=null;\n  track('screenshot_uploaded'", HTML)


class TestUploadStillWorks(unittest.TestCase):
    def test_single_file_input_and_upload_link(self):
        self.assertEqual(HTML.count('id="file-input"'), 1)
        self.assertEqual(len(re.findall(r'<label class="up-cta hm-cta" for="file-input"', UP)), 1)

    def test_screenshot_help_is_kept(self):
        self.assertIn("What screenshot?", UP)
        self.assertIn("/fpl/test_images/pitch-view-example-v2.png", UP)

    def test_upload_ctas_keep_the_tracked_class(self):
        self.assertGreaterEqual(UP.count('up-cta'), 3)
        self.assertIn("closest('.hm-final,.hm-sticky')", HTML)

    def test_links_are_only_the_methodology_page(self):
        hrefs = re.findall(r'href="([^"]+)"', UP)
        self.assertEqual(hrefs, ["/fpl/model.html"])

    def test_planner_is_not_linked_from_the_home_page(self):
        self.assertNotIn("planner.html", UP)

    def test_sticky_bar_is_inside_the_upload_screen(self):
        self.assertIn('id="hm-stk"', UP)         # hidden whenever another screen is active


class TestAnalyticsUnchanged(unittest.TestCase):
    def test_committed_events_are_still_sent_and_new_ones_registered(self):
        from fpl.analytics.queries import _EXPECTED_EVENTS
        events = lambda s: set(re.findall(r"track\('([a-z_]+)'", s))
        self.assertLessEqual(events(head_html()), events(HTML))
        self.assertEqual(sorted(e for e in events(HTML) - events(head_html()) if e not in _EXPECTED_EVENTS), [])

    def test_no_inline_handlers_or_tracking_in_the_home_markup(self):
        home = UP.split("<script>")[0]
        self.assertNotRegex(home, r"\bon[a-z]+=")
        self.assertNotIn("track(", home)


if __name__ == "__main__":
    unittest.main()
