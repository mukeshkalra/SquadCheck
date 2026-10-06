import ast
import inspect
import unittest
from datetime import datetime, timedelta, timezone
from html import escape

from fpl.hustler import adapter as adapter_mod
from fpl.hustler import feed
from fpl.hustler.adapter import RedditRmtAdapter, SourceAdapter, SourceUnavailable
from fpl.hustler.models import Platform, Source

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
SRC = feed.SOURCE


def _entry(pid, title="Any thoughts??", days=1.0, link=None, body=None, raw_id=None):
    when = (NOW - timedelta(days=days)).isoformat()
    if link is None:
        link = f"https://i.redd.it/{pid}.jpeg"
    inner = f'<table><tr><td><span><a href="{link}">[link]</a></span></td></tr></table>'
    if body:
        inner += f'<div class="md"><p>{body}</p></div>'
    return (f'<entry><author><name>/u/someone</name></author><content type="html">{escape(inner)}</content>'
            f'<id>{raw_id or "t3_" + pid}</id>'
            f'<link href="https://www.reddit.com/r/FPLRateMyTeam/comments/{pid}/x/" />'
            f'<updated>{when}</updated><published>{when}</published><title>{title}</title></entry>')


def _feed_xml(*entries):
    return ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
            + "".join(entries) + "</feed>").encode()


class FakeFetch:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _adapter(*entries, fetch=None):
    fetch = fetch or FakeFetch(_feed_xml(*entries))
    return RedditRmtAdapter(SRC, fetch=fetch), fetch


class TestAdapter(unittest.TestCase):
    def test_parses_post_metadata(self):
        a, _ = _adapter(_entry("abc", title="Rate my team", days=2, body="Wildcard or hold?"))
        (post,) = a.fetch_posts()
        self.assertEqual(post.post_id, "abc")
        self.assertEqual(post.title, "Rate my team")
        self.assertEqual(post.community, "FPLRateMyTeam")
        self.assertEqual(post.url, "https://www.reddit.com/r/FPLRateMyTeam/comments/abc/x/")
        self.assertEqual(post.body, "Wildcard or hold?")
        self.assertEqual(post.created_at, NOW - timedelta(days=2))
        self.assertEqual([(m.url, m.kind) for m in post.media], [("https://i.redd.it/abc.jpeg", "image")])
        self.assertIsNone(post.num_comments)                 # RSS cannot supply it

    def test_uses_only_a_read_only_get_with_a_user_agent(self):
        a, fetch = _adapter(_entry("abc"))
        a.fetch_posts()
        (url, headers), = fetch.calls
        self.assertEqual(set(headers), {"User-Agent"})
        self.assertIn("/r/FPLRateMyTeam/new/.rss", url)

    def test_non_direct_image_posts_carry_no_media(self):
        a, _ = _adapter(_entry("gal", link="https://www.reddit.com/gallery/gal"),
                        _entry("ext", link="https://example.com/pic.png"))
        self.assertEqual([p.media for p in a.fetch_posts()], [(), ()])

    def test_bad_and_duplicate_entries_are_skipped(self):
        a, _ = _adapter(_entry("abc"), _entry("abc"), _entry("zzz", raw_id="t1_comment"), _entry("ok", title=""))
        self.assertEqual([p.post_id for p in a.fetch_posts()], ["abc"])

    def test_blocked_or_broken_feed_raises_source_unavailable(self):
        for payload in (SourceUnavailable("HTTP 403 from www.reddit.com"), b"<html>not xml"):
            a, _ = _adapter(fetch=FakeFetch(payload))
            with self.assertRaises(SourceUnavailable):
                a.fetch_posts()

    def test_only_reddit_sources_accepted(self):
        with self.assertRaises(ValueError):
            RedditRmtAdapter(Source("d", Platform.DISCORD, "x", "https://d"))

    def test_adapter_is_the_seam(self):
        self.assertTrue(issubclass(RedditRmtAdapter, SourceAdapter))
        self.assertEqual(SourceAdapter.__abstractmethods__, {"fetch_posts", "fetch_image"})


class TestFetchImage(unittest.TestCase):
    def test_downloads_the_direct_image_only_when_asked(self):
        fetch = FakeFetch(_feed_xml(_entry("abc")))
        a = RedditRmtAdapter(SRC, fetch=fetch)
        (post,) = a.fetch_posts()
        self.assertEqual(len(fetch.calls), 1)                # listing only, no image yet
        fetch.payload = b"\xff\xd8img"
        self.assertEqual(a.fetch_image(post), b"\xff\xd8img")
        self.assertEqual(fetch.calls[-1][0], "https://i.redd.it/abc.jpeg")

    def test_refuses_other_hosts_and_missing_images(self):
        a, _ = _adapter(_entry("gal", link="https://www.reddit.com/gallery/gal"))
        (post,) = a.fetch_posts()
        with self.assertRaises(ValueError):
            a.fetch_image(post)
        evil = post.__class__(**{**post.__dict__, "media": (adapter_mod.MediaRef("https://evil.example/x.jpg"),)})
        with self.assertRaises(SourceUnavailable):
            a.fetch_image(evil)

    def test_oversized_image_rejected(self):
        a = RedditRmtAdapter(SRC, fetch=FakeFetch(_feed_xml(_entry("abc"))))
        (post,) = a.fetch_posts()
        a._fetch = FakeFetch(b"x" * (adapter_mod._MAX_IMAGE_BYTES + 1))
        with self.assertRaises(SourceUnavailable):
            a.fetch_image(post)


class TestFeed(unittest.TestCase):
    def test_keeps_only_recent_posts_with_images(self):
        a, _ = _adapter(_entry("new", days=1), _entry("old", days=20),
                        _entry("gal", days=1, link="https://www.reddit.com/gallery/gal"))
        items = feed.build_feed(a, NOW)
        self.assertEqual([i.conversation.post_id for i in items], ["new"])

    def test_fifteen_day_boundary(self):
        a, _ = _adapter(_entry("in", days=14.9), _entry("out", days=15.1))
        self.assertEqual([i.conversation.post_id for i in feed.build_feed(a, NOW)], ["in"])

    def test_newest_first(self):
        a, _ = _adapter(_entry("b", days=3), _entry("c", days=0.5), _entry("a", days=9))
        self.assertEqual([i.conversation.post_id for i in feed.build_feed(a, NOW)], ["c", "b", "a"])

    def test_caps_at_limit_and_validates_it(self):
        a, _ = _adapter(*[_entry(f"p{i}", days=i / 10) for i in range(15)])
        self.assertEqual(len(feed.build_feed(a, NOW)), 10)
        self.assertEqual(len(feed.build_feed(a, NOW, limit=5)), 5)
        for bad in (0, 11):
            with self.assertRaises(ValueError):
                feed.build_feed(a, NOW, limit=bad)

    def test_items_carry_url_and_reasoning(self):
        a, _ = _adapter(_entry("abc", days=2))
        (item,) = feed.build_feed(a, NOW)
        self.assertTrue(item.conversation.url.startswith("https://www.reddit.com/"))
        self.assertEqual(item.opportunity.reasoning,
                         ["Eligible: YES", "✓ 2 days old", "✓ image attached", "comment count not available"])
        self.assertTrue(item.opportunity.internal)

    def test_empty_feed(self):
        a, _ = _adapter()
        self.assertEqual(feed.build_feed(a, NOW), [])
        self.assertEqual(feed.render([]), "No eligible posts.")

    def test_render_shows_url_title_and_reasoning(self):
        a, _ = _adapter(_entry("abc", title="Rate my team", days=2))
        text = feed.render(feed.build_feed(a, NOW))
        for part in ("1. Rate my team", "comments/abc", "https://i.redd.it/abc.jpeg", "Eligible: YES"):
            self.assertIn(part, text)

    def test_feed_never_downloads_images(self):
        a, fetch = _adapter(_entry("abc"), _entry("def"))
        feed.build_feed(a, NOW)
        self.assertTrue(all("i.redd.it" not in url for url, _ in fetch.calls))


class TestSafety(unittest.TestCase):
    def test_no_posting_or_credential_surface(self):
        for mod in (adapter_mod, feed):
            tree = ast.parse(inspect.getsource(mod))
            names = set()
            for n in ast.walk(tree):
                for attr in ("id", "attr", "arg", "name"):
                    if isinstance(getattr(n, attr, None), str):
                        names.add(getattr(n, attr).lower())
            for word in ("publish", "submit", "password", "cookie", "oauth", "authorization", "api_key", "token"):
                self.assertEqual([x for x in names if word in x and x != "published"], [], mod.__name__)
            bodies = [n for n in ast.walk(tree) if isinstance(n, ast.keyword) and n.arg == "data"]
            self.assertEqual(bodies, [], "request bodies are not allowed")

    def test_requests_are_get_only(self):
        self.assertIn('method="GET"', inspect.getsource(adapter_mod))
        self.assertNotIn('"POST"', inspect.getsource(adapter_mod))

    def test_only_one_source_is_configured(self):
        self.assertEqual((feed.SOURCE.community, feed.SOURCE.platform), ("FPLRateMyTeam", Platform.REDDIT))


if __name__ == "__main__":
    unittest.main()
