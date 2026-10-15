"""
Tests for the home (upload) screen of fpl/index.html (Chunk 9): the four feature tiles and the example result
cards, with the upload flow, the trust line and the analytics events left as they were. Static checks on the page
source against the committed version: no network, no browser.
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HTML = (ROOT / "fpl" / "index.html").read_text(encoding="utf-8")


# The last commit before the planner work: the home page is compared against it, not against HEAD.
BASE = "fb219e6"


def head_html():
    r = subprocess.run(["git", "show", BASE + ":fpl/index.html"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        raise unittest.SkipTest("no git history available")
    return r.stdout


def upload(html):
    return re.search(r'<section id="s-upload".*?(?=<section id="s-loading")', html, re.S).group(0)


UP = upload(HTML)
TILES = re.search(r'<div class="ft-wrap" id="features">.*?\n  </div>\n', UP, re.S).group(0)
PLAN_EX = re.search(r'  <div class="ex-plan" id="ex-plan">.*?\n  </div>\n', UP, re.S).group(0)


def text(fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


class TestFeatureTiles(unittest.TestCase):
    def test_four_tiles_in_the_agreed_order(self):
        tiles = re.findall(r'<a class="ft-tile[^"]*" href="(#[a-z-]+)"><span class="ft-tile-no">(\d)</span><span class="ft-tile-t">(.*?)</span>', TILES)
        self.assertEqual([t[1] for t in tiles], ["1", "2", "3", "4"])
        titles = [re.sub(r"<.*", "", t[2]) for t in tiles]
        self.assertEqual(titles, ["Projection", "Free Transfer Planner", "Bench advice", "Captain's call"])

    def test_no_unapproved_feature_is_advertised(self):
        low = text(UP).lower()
        for word in ("chip", "wildcard", "free hit", "triple captain", "bench boost"):
            self.assertNotIn(word, low)

    def test_every_tile_points_at_an_example_card_that_exists(self):
        for target in re.findall(r'class="ft-tile[^"]*" href="#([a-z-]+)"', TILES):
            self.assertEqual(len(re.findall(r'id="%s"' % target, UP)), 1, target)

    def test_planner_description_is_accurate_and_makes_no_account_promises(self):
        desc = text(TILES + PLAN_EX).lower()
        self.assertIn("free transfer", desc)
        self.assertIn("after you check your team", desc)
        for word in ("sign in", "sign-in", "sign up", "account", "saved", "save your", "email", "review", "history", "remind"):
            self.assertNotIn(word, desc)
        self.assertNotRegex(desc, r"three-gameweek (gain|points)")

    def test_planner_is_not_linked_from_the_home_page(self):
        self.assertNotIn("planner.html", UP)
        self.assertEqual(re.findall(r"""href=["'][^"']*planner""", HTML), [])
        self.assertEqual(HTML.count("location.href = '/fpl/planner.html'"), 1)       # still only the validated results-page handover

    def test_only_in_page_anchors_are_added(self):
        for href in re.findall(r'href="([^"]+)"', TILES):
            self.assertRegex(href, r"^#[a-z-]+$")


class TestSubheadline(unittest.TestCase):
    def test_sub_headline_mentions_the_transfer_planner_without_overpromising(self):
        sub = text(re.search(r'<p class="up-sub">.*?</p>', UP, re.S).group(0))
        self.assertIn("Transfer Planner", sub)
        for kept in ("points forecast", "captain pick", "bench swap"):
            self.assertIn(kept, sub)
        for word in ("sign in", "account", "saved", "email"):
            self.assertNotIn(word, sub.lower())


class TestExampleCards(unittest.TestCase):
    def test_all_four_features_have_an_example(self):
        for i in ("ex-hero", "ex-plan", "ex-bench", "ex-captain"):
            self.assertEqual(UP.count('id="%s"' % i), 1, i)

    def test_planner_example_is_labelled_as_an_illustration(self):
        t = text(PLAN_EX)
        self.assertIn("Example", t)
        self.assertIn("Illustration with example numbers, not your squad", t)
        self.assertIn("next gameweek", t)
        self.assertIn("no hit", t)

    def test_example_is_unmistakably_made_up(self):
        t = text(PLAN_EX)
        self.assertIn("Example only · made-up numbers · not real planner output", t)
        self.assertIn("(example number)", t)
        for real in ("Gvardiol", "Hall", "Haaland", "Salah", "Saka"):                  # no real players in the planner example
            self.assertNotIn(real, t)
        self.assertIn("Defender A", t)

    def test_no_hard_coded_gameweek_on_the_home_screen(self):
        self.assertNotRegex(re.search(r'<span class="live-text">.*?</span>', UP).group(0), r"(?i)gameweek\s*\d|gw\s*\d")
        self.assertNotRegex(text(UP.split('class="ex-wrap"')[0]), r"(?i)gameweek \d+")

    def test_three_measures_are_not_blurred_in_the_example(self):
        t = text(PLAN_EX).lower()
        self.assertIn("provisional", t)
        self.assertNotIn("ceiling", t)
        self.assertNotIn("upside", t)

    def test_existing_example_content_is_intact(self):
        for needle in ('class="ex-pts-num">80<', "HOLD ✓", "Captain Call", "Bench Call", "+6 pts", "+3 pts", 'href="/fpl/model.html"', "How we calculate your score"):
            self.assertIn(needle, UP)


class TestUploadFlowAndEventsUnchanged(unittest.TestCase):
    def test_home_page_is_the_old_one_plus_only_the_new_blocks(self):
        old = upload(head_html())
        new = UP.replace(TILES, "").replace(PLAN_EX, "")
        for i in ("ex-hero", "ex-bench", "ex-captain"):                                 # ids added to existing example elements
            new = new.replace(' id="%s"' % i, "")
        norm = lambda x: re.sub(r"\s+", " ", x)
        new = new.replace("Free</span>\n  </div>", "Free &nbsp;·&nbsp; Gameweek 6</span>\n  </div>")   # deliberate edit 1: stale hard-coded gameweek removed
        new = new.replace('Your FPL points forecast, captain pick and bench swap — in seconds. Plus a free Transfer Planner for your next move.', 'Your FPL points forecast. Your captain pick. Your best XI and bench swap — in seconds.')                 # deliberate edit 2: sub-headline mentions the Transfer Planner
        self.assertEqual(norm(old), norm(new))

    def test_upload_controls_and_trust_line_survive(self):
        self.assertIn('<input type="file" id="file-input" accept="image/*"', UP)
        self.assertIn('for="file-input"', UP)
        self.assertIn("Check my team's points — Free", UP)
        for s in ("<span>✓ Free</span>", "<span>✓ No login</span>", "<span>✓ No FPL ID</span>"):
            self.assertIn(s, UP)
        self.assertEqual(UP.count("Check my team's points — Free"), 2)

    def test_trust_line_comes_before_the_tiles(self):
        self.assertLess(UP.index("✓ No login"), UP.index('id="features"'))
        self.assertLess(UP.index('id="features"'), UP.index('class="ex-wrap"'))

    def test_analytics_event_names_are_exactly_the_committed_ones(self):
        events = lambda s: sorted(set(re.findall(r"track\('([a-z_]+)'", s)))
        self.assertEqual(events(HTML), events(head_html()))

    def test_no_event_handlers_were_added_to_the_new_blocks(self):
        self.assertNotRegex(TILES + PLAN_EX, r"\bon[a-z]+=")
        self.assertNotIn("track(", TILES + PLAN_EX)


if __name__ == "__main__":
    unittest.main()
