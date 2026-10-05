"""
Domain model for Growth Hustler v1 — contracts and invariants only.

Rules (enforced at construction, covered by tests):
  - No publishing: nothing here holds credentials, makes network calls or has a
    "posted" state. Every reply is a private draft for a human.
  - Eligibility is a set of hard gates, not a score. An Opportunity can only be
    built from an assessment where every gate passed; its reasoning is the gate list.
  - Ranking uses only comment count and community size, so the reason a post was
    selected is always readable.
  - Community policy is a hard constraint: a draft is checked against it.
  - Identity and geometry are separate: 15 distinct players is evidence on its own;
    XI/bench is optional evidence, required only by questions that need it.
  - Everything is internal: it carries `internal=True` and may never be switched off,
    matching the `internal` event property set by the web app.
"""

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import Enum, IntEnum
from typing import Optional, Tuple

SQUAD_SIZE = 15
COMMUNITY_SIZE_SIGNAL = 5000   # a useful signal, never a gate
_BRAND = "squadcheck"
_LINK = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|club|co|io|net|org|uk)\b", re.I)


def analytics_properties():
    """Event properties every Hustler-originated analytics event must carry."""
    return {"internal": True, "origin": "hustler"}


def _require_internal(obj):
    if obj.internal is not True:
        raise ValueError(f"{type(obj).__name__}.internal must be True")


def _require_aware(value, name):
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")


# ── Enums ─────────────────────────────────────────────────────────────────────

class Platform(str, Enum):
    REDDIT = "reddit"
    DISCORD = "discord"
    X = "x"


class QuestionType(str, Enum):
    PLAYER_PROJECTION = "player_projection"   # players only
    CAPTAIN_CHOICE = "captain_choice"         # players only
    STARTING_XI = "starting_xi"               # needs XI/bench
    BENCH_ORDER = "bench_order"               # needs XI/bench


class EvidenceLevel(str, Enum):
    INSUFFICIENT = "insufficient"
    PLAYERS_ONLY = "players_only"
    PLAYERS_AND_XI = "players_and_xi"


_NEEDS_XI = {QuestionType.STARTING_XI, QuestionType.BENCH_ORDER}


class OpportunityStatus(str, Enum):
    NEW = "new"
    SELECTED = "selected"     # human chose "Analyse this"
    ANALYSED = "analysed"
    DISMISSED = "dismissed"


_TRANSITIONS = {
    OpportunityStatus.NEW: {OpportunityStatus.SELECTED, OpportunityStatus.DISMISSED},
    OpportunityStatus.SELECTED: {OpportunityStatus.ANALYSED, OpportunityStatus.DISMISSED},
    OpportunityStatus.ANALYSED: set(),
    OpportunityStatus.DISMISSED: set(),
}


class AnalysisStatus(str, Enum):
    COMPLETE = "complete"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class Stance(str, Enum):
    REPLY = "reply"
    REPLY_WITH_CAVEATS = "reply_with_caveats"
    DO_NOT_REPLY = "do_not_reply"


class Confidence(IntEnum):
    VERY_LOW = 0
    LOW = 1
    MODERATE = 2
    HIGH = 3


# ── Source ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CommunityPolicy:
    """
    Manual, per-community rules. Defaults are restrictive: anything not
    explicitly allowed is treated as forbidden. `reviewed` is when a human
    last read the community's rules; an unreviewed policy blocks drafting.
    """
    allows_replies: bool = False
    allows_personalised_advice: bool = False
    allows_self_promotion: bool = False
    allows_external_links: bool = False
    allows_ai_content: bool = False
    reviewed: Optional[date] = None
    notes: str = ""

    def violations(self, text, personalised_advice=False):
        """Rules a draft would break in this community. Empty means compatible."""
        found = []
        if personalised_advice and not self.allows_personalised_advice:
            found.append("personalised advice not allowed")
        if _BRAND in text.lower() and not self.allows_self_promotion:
            found.append("self-promotion not allowed")
        if _LINK.search(text) and not self.allows_external_links:
            found.append("external links not allowed")
        if text and not self.allows_ai_content:
            found.append("AI-generated content not allowed")
        return tuple(found)


@dataclass(frozen=True)
class Source:
    source_id: str
    platform: Platform
    community: str
    url: str
    enabled: bool = True
    members: Optional[int] = None
    policy: CommunityPolicy = field(default_factory=CommunityPolicy)
    notes: str = ""
    last_reviewed: Optional[date] = None

    def __post_init__(self):
        if not (self.source_id and self.community and self.url):
            raise ValueError("Source needs source_id, community and url")
        if self.members is not None and self.members < 0:
            raise ValueError("Source.members cannot be negative")


# ── Conversation ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MediaRef:
    """Metadata only. Nothing is fetched or classified at discovery time."""
    url: str
    kind: str = "image"

    def __post_init__(self):
        if not self.url:
            raise ValueError("MediaRef.url is required")


@dataclass(frozen=True)
class Conversation:
    conversation_id: str
    source_id: str
    platform: Platform
    community: str
    post_id: str
    url: str                      # direct link to the post
    title: str
    created_at: datetime
    body: str = ""
    num_comments: int = 0
    score: Optional[int] = None
    media: Tuple[MediaRef, ...] = ()

    def __post_init__(self):
        if not (self.conversation_id and self.source_id and self.post_id and self.title):
            raise ValueError("Conversation needs conversation_id, source_id, post_id and title")
        if not self.url.startswith(("http://", "https://")):
            raise ValueError("Conversation.url must be a direct post URL")
        if self.num_comments < 0:
            raise ValueError("Conversation.num_comments cannot be negative")
        _require_aware(self.created_at, "Conversation.created_at")


# ── Opportunity ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Judgement:
    """
    Whether the post is FPL-related and whether SquadCheck can help.
    Supplied by a human or a later component; this layer does not infer it.
    """
    fpl_related: bool
    squadcheck_can_help: bool
    question_type: Optional[QuestionType] = None

    def __post_init__(self):
        if self.squadcheck_can_help and self.question_type is None:
            raise ValueError("squadcheck_can_help requires a question_type")


@dataclass(frozen=True)
class EligibilityConfig:
    min_age_days: float = 1.0
    max_age_days: float = 7.0

    def __post_init__(self):
        if not 0 <= self.min_age_days <= self.max_age_days:
            raise ValueError("Need 0 <= min_age_days <= max_age_days")


@dataclass(frozen=True)
class Gate:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Assessment:
    conversation_id: str
    gates: Tuple[Gate, ...]
    question_type: Optional[QuestionType]
    age_days: float
    num_comments: int
    community: str
    members: Optional[int]

    @property
    def eligible(self):
        return all(g.passed for g in self.gates)

    def why(self):
        """Plain-language explanation: the gate results, then the ranking inputs."""
        lines = ["Eligible: " + ("YES" if self.eligible else "NO")]
        lines += [f"{'✓' if g.passed else '✗'} {g.detail}" for g in self.gates]
        if self.eligible:
            size = "size unknown" if self.members is None else f"{self.members:,} members"
            if self.members is not None and self.members > COMMUNITY_SIZE_SIGNAL:
                size += f" (above {COMMUNITY_SIZE_SIGNAL:,} signal)"
            lines += [f"{self.num_comments} comments", f"community: {size}"]
        return lines


def assess(source, conversation, judgement, now, config=EligibilityConfig()):
    """Run the four hard gates. Any failure means the post is not an opportunity."""
    _require_aware(now, "now")
    if (source.source_id, source.platform) != (conversation.source_id, conversation.platform):
        raise ValueError("Conversation does not belong to this Source")
    age = (now - conversation.created_at).total_seconds() / 86400
    policy = source.policy

    if not source.enabled:
        policy_detail, policy_ok = "community is disabled", False
    elif policy.reviewed is None:
        policy_detail, policy_ok = "community policy not reviewed", False
    elif not policy.allows_replies:
        policy_detail, policy_ok = "community policy does not allow replies", False
    elif not policy.allows_ai_content:
        policy_detail, policy_ok = "community policy does not allow AI-generated content", False
    else:
        policy_detail, policy_ok = "response permitted by community policy", True

    gates = (
        Gate("fpl_related", judgement.fpl_related,
             "FPL question" if judgement.fpl_related else "not clearly FPL-related"),
        Gate("answerable", judgement.squadcheck_can_help,
             "SquadCheck can answer the player-projection part" if judgement.squadcheck_can_help
             else "SquadCheck cannot materially help"),
        Gate("recent", config.min_age_days <= age <= config.max_age_days,
             f"{age:.0f} days old" if config.min_age_days <= age <= config.max_age_days
             else f"{age:.1f} days old, outside {config.min_age_days:g}-{config.max_age_days:g} day window"),
        Gate("policy_compatible", policy_ok, policy_detail),
    )
    return Assessment(conversation.conversation_id, gates, judgement.question_type, age,
                      conversation.num_comments, conversation.community, source.members)


@dataclass(frozen=True)
class Opportunity:
    opportunity_id: str
    assessment: Assessment
    status: OpportunityStatus = OpportunityStatus.NEW
    internal: bool = True

    def __post_init__(self):
        if not self.opportunity_id:
            raise ValueError("Opportunity.opportunity_id is required")
        if not self.assessment.eligible:
            raise ValueError("An ineligible post is not an opportunity")
        _require_internal(self)

    @property
    def conversation_id(self):
        return self.assessment.conversation_id

    @property
    def question_type(self):
        return self.assessment.question_type

    @property
    def reasoning(self):
        return self.assessment.why()

    @property
    def rank_key(self):
        """Most comments first, then larger community; unknown size ranks as 0."""
        return (-self.assessment.num_comments, -(self.assessment.members or 0), self.opportunity_id)

    def advance(self, status):
        if status not in _TRANSITIONS[self.status]:
            raise ValueError(f"Cannot move opportunity from {self.status.value} to {status.value}")
        return replace(self, status=status)


def rank(opportunities):
    return sorted(opportunities, key=lambda o: o.rank_key)


# ── Analysis ──────────────────────────────────────────────────────────────────

def scan_evidence(scan_result):
    """
    Read an existing scanner result without changing its semantics.
    Returns (distinct player names, xi_available, bench_available).
    Players count even when the scanner status is not VALID; XI/bench is trusted
    only when the scanner says VALID, since its split is validated there.
    """
    names, seen = [], set()
    for p in scan_result.get("players", []):
        name = (p.get("name") or "").strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    valid = scan_result.get("status") == "VALID" and len(names) == SQUAD_SIZE
    return tuple(names), valid, valid


@dataclass(frozen=True)
class AnalysisResult:
    question_type: QuestionType
    players_identified: Tuple[str, ...]
    xi_available: bool
    bench_available: bool
    scanner_status: Optional[str]          # raw scanner status, never reinterpreted
    analysis_status: AnalysisStatus
    confidence: Confidence
    recommended_stance: Stance
    private_findings: Tuple[str, ...] = ()
    public_reply: str = ""                 # a draft for a human; never sent
    reply_is_personalised: bool = False
    internal: bool = True

    def __post_init__(self):
        _require_internal(self)
        names = [n.lower() for n in self.players_identified]
        if len(set(names)) != len(names) or len(names) > SQUAD_SIZE:
            raise ValueError("players_identified must be at most 15 distinct players")
        if (self.xi_available or self.bench_available) and len(names) != SQUAD_SIZE:
            raise ValueError("XI/bench evidence requires all 15 players")
        enough = self.evidence_met
        if self.analysis_status is AnalysisStatus.COMPLETE and not enough:
            raise ValueError("Analysis cannot be complete without the evidence the question needs")
        if self.analysis_status is AnalysisStatus.INSUFFICIENT_EVIDENCE:
            if self.public_reply or self.recommended_stance is not Stance.DO_NOT_REPLY \
                    or self.confidence > Confidence.LOW:
                raise ValueError("Insufficient evidence means no reply, DO_NOT_REPLY and low confidence")
        if self.recommended_stance is Stance.DO_NOT_REPLY and self.public_reply:
            raise ValueError("DO_NOT_REPLY cannot carry a public reply")

    @property
    def evidence_available(self):
        if len(self.players_identified) < SQUAD_SIZE:
            return EvidenceLevel.INSUFFICIENT
        if self.xi_available and self.bench_available:
            return EvidenceLevel.PLAYERS_AND_XI
        return EvidenceLevel.PLAYERS_ONLY

    @property
    def evidence_met(self):
        level = self.evidence_available
        if self.question_type in _NEEDS_XI:
            return level is EvidenceLevel.PLAYERS_AND_XI
        return level is not EvidenceLevel.INSUFFICIENT

    def draft_violations(self, policy):
        """Community rules the draft would break; the caller must hold it back if non-empty."""
        return policy.violations(self.public_reply, self.reply_is_personalised)
