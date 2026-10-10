"""
Hustler v1 feed: recent rate-my-team posts that have an image, newest first.

One source for the first experiment. No classifier, no LLM, no image inspection.
Run live with:  python3 -m fpl.hustler.feed
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from .adapter import RedditRmtAdapter, SourceUnavailable
from .models import (CommunityPolicy, Conversation, EligibilityConfig, Opportunity, Platform,
                     Source, assess, rank)

MAX_FEED = 10

SOURCE = Source(
    source_id="reddit-fplratemyteam",
    platform=Platform.REDDIT,
    community="FPLRateMyTeam",
    url="https://www.reddit.com/r/FPLRateMyTeam/",
    policy=CommunityPolicy(),
    notes="Every post is a rate-my-team request; read via public RSS.",
)


@dataclass(frozen=True)
class FeedItem:
    opportunity: Opportunity
    conversation: Conversation


def build_feed(adapter, now, limit=MAX_FEED, config=EligibilityConfig()):
    """Eligible posts ranked by recency then comments, at most `limit` (1-10)."""
    if not 1 <= limit <= MAX_FEED:
        raise ValueError(f"limit must be between 1 and {MAX_FEED}")
    by_id = {}
    for conversation in adapter.fetch_posts():
        assessment = assess(adapter.source, conversation, now, config)
        if assessment.eligible:
            opportunity = Opportunity(conversation.conversation_id, assessment)
            by_id[opportunity.opportunity_id] = FeedItem(opportunity, conversation)
    ranked = rank([item.opportunity for item in by_id.values()])[:limit]
    return [by_id[o.opportunity_id] for o in ranked]


def render(items):
    lines = []
    for n, item in enumerate(items, 1):
        c, why = item.conversation, item.opportunity.reasoning
        post_id = c.conversation_id.rsplit(":", 1)[-1]
        lines += [f"{n}. {c.title}", f"   id: {post_id}", f"   {c.url}", f"   image: {c.media[0].url}",
                  f"   posted: {c.created_at:%Y-%m-%d %H:%M} UTC"]
        lines += [f"   {line}" for line in why]
        if c.body:
            lines.append(f"   body: {c.body[:120]}")
        lines.append("")
    return "\n".join(lines) if lines else "No eligible posts."


def main():
    try:
        items = build_feed(RedditRmtAdapter(SOURCE), datetime.now(timezone.utc))
    except SourceUnavailable as exc:
        print(f"Feed unavailable: {exc}")
        return 1
    print(render(items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
