"""
Source adapters: the only place Hustler talks to a platform.

Read-only by construction: GET requests, an explicit User-Agent, no credentials,
no cookies, no way to post. Nothing here fetches an image until `fetch_image` is
called, which the feed never does.
"""

import html
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from datetime import datetime

from .models import Conversation, MediaRef, Platform, Source

USER_AGENT = "SquadCheckHustler/0.1 (read-only research)"
_ATOM = "{http://www.w3.org/2005/Atom}"
_IMAGE_HOSTS = {"i.redd.it"}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_LINK_RE = re.compile(r'<a href="([^"]+)">\[link\]</a>')
_BODY_RE = re.compile(r'<div class="md">(.*?)</div>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


class SourceUnavailable(RuntimeError):
    """The platform refused or failed the request (blocked, rate-limited, offline)."""


def http_get(url, headers):
    """Default fetcher: a plain GET over https. Swapped out in tests."""
    if not url.startswith("https://"):
        raise SourceUnavailable(f"Refusing non-https URL: {url}")
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read(_MAX_IMAGE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise SourceUnavailable(f"HTTP {exc.code} from {urllib.parse.urlparse(url).netloc}") from exc
    except urllib.error.URLError as exc:
        raise SourceUnavailable(f"Could not reach {urllib.parse.urlparse(url).netloc}: {exc.reason}") from exc


class SourceAdapter(ABC):
    source: Source

    @abstractmethod
    def fetch_posts(self):
        """Return recent Conversations with metadata only (no images downloaded)."""

    @abstractmethod
    def fetch_image(self, conversation):
        """Download the conversation's first image and return its bytes."""


class RedditRmtAdapter(SourceAdapter):
    """
    A subreddit whose posts are all rate-my-team requests, read from its public RSS feed.
    RSS gives id, title, link, time and the direct image link, but no comment count
    (num_comments is None) and no flair. Gallery and external-link posts have no direct
    image link and so carry no media.
    """

    def __init__(self, source, fetch=http_get, limit=100):
        if source.platform is not Platform.REDDIT:
            raise ValueError("RedditRmtAdapter needs a Reddit source")
        self.source = source
        self._fetch = fetch
        self._limit = limit

    def _get(self, url):
        return self._fetch(url, {"User-Agent": USER_AGENT})

    def fetch_posts(self):
        url = f"https://www.reddit.com/r/{self.source.community}/new/.rss?limit={self._limit}"
        try:
            root = ET.fromstring(self._get(url))
        except ET.ParseError as exc:
            raise SourceUnavailable("Reddit feed was not valid XML") from exc
        posts, seen = [], set()
        for entry in root.iter(f"{_ATOM}entry"):
            post = self._parse_entry(entry)
            if post and post.post_id not in seen:
                seen.add(post.post_id)
                posts.append(post)
        return posts

    def _parse_entry(self, entry):
        raw_id = entry.findtext(f"{_ATOM}id") or ""
        link = entry.find(f"{_ATOM}link")
        published = entry.findtext(f"{_ATOM}published") or entry.findtext(f"{_ATOM}updated")
        title = (entry.findtext(f"{_ATOM}title") or "").strip()
        if not (raw_id.startswith("t3_") and link is not None and link.get("href") and published and title):
            return None
        content = entry.findtext(f"{_ATOM}content") or ""
        post_id = raw_id[3:]
        return Conversation(
            conversation_id=f"{self.source.source_id}:{post_id}",
            source_id=self.source.source_id,
            platform=self.source.platform,
            community=self.source.community,
            post_id=post_id,
            url=link.get("href"),
            title=html.unescape(title),
            created_at=datetime.fromisoformat(published),
            body=_body_text(content),
            media=_image_media(content),
        )

    def fetch_image(self, conversation):
        image = next((m for m in conversation.media if m.kind == "image"), None)
        if image is None:
            raise ValueError("Conversation has no image")
        parsed = urllib.parse.urlparse(image.url)
        if parsed.scheme != "https" or parsed.hostname not in _IMAGE_HOSTS:
            raise SourceUnavailable(f"Refusing to fetch image from {parsed.hostname}")
        data = self._get(image.url)
        if len(data) > _MAX_IMAGE_BYTES:
            raise SourceUnavailable("Image exceeds the 10 MB limit")
        return data


def _image_media(content):
    for href in _LINK_RE.findall(content):
        if urllib.parse.urlparse(html.unescape(href)).hostname in _IMAGE_HOSTS:
            return (MediaRef(html.unescape(href), "image"),)
    return ()


def _body_text(content):
    match = _BODY_RE.search(content)
    return html.unescape(_TAG_RE.sub(" ", match.group(1))).strip() if match else ""
