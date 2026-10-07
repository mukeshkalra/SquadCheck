"""
Players mentioned in a post but not in the submitted squad: identity only.

Pure and deterministic: no I/O, no LLM, no projection. Identity is resolved conservatively:
  exactly one active FPL player matches the name  -> RESOLVED
  more than one matches                           -> AMBIGUOUS  (never chosen for the operator)
  none matches                                    -> UNRESOLVED
  an explicit operator pick (--pick "Name=id")    -> SELECTED, the only way out of AMBIGUOUS
Nothing here uses starts, minutes, price or popularity to choose between players, and the
engine's first-match helper (`_find_id`) is deliberately not used.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from fpl.engine.pipeline import _find_all_matches

MAX_MENTIONS = 6

_POSITION = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
_PARTICLES = {"de", "van", "von", "da", "di", "dos", "der", "den", "ter", "ten", "le", "la", "el", "du"}
_TOKEN = re.compile(r"[^\W\d_][\w'’\-]*")
_FORWARD = {"to", "for", "with", "into", "->", "→", "=>"}            # "<squad player> to <name>"
_REVERSE = {"instead of", "in for", "in place of"}                   # "<name> instead of <squad player>"
_NAME = r"(?:(?i:" + "|".join(sorted(_PARTICLES)) + r")\s+)?[A-Z][\w'’\-]*"
_AFTER_SQUAD = re.compile(r"\s*(?i:to|for|with|into|->|→|=>)\s+(" + _NAME + ")")


class MentionStatus(str, Enum):
    RESOLVED = "resolved"        # exactly one match
    AMBIGUOUS = "ambiguous"      # several matches: needs the operator
    UNRESOLVED = "unresolved"    # no match
    SELECTED = "selected"        # operator chose one candidate
    SKIPPED = "skipped"          # operator said it is not a player


class PickError(ValueError):
    """An operator pick that is malformed or not one of the mention's candidates."""


def key(text):
    return " ".join(text.casefold().split())


@dataclass(frozen=True)
class Candidate:
    player_id: int
    full_name: str
    web_name: str
    team: str
    position: str

    def label(self):
        return f"{self.full_name}, {self.team}, {self.position}"


@dataclass(frozen=True)
class Mention:
    text: str                        # as written in the post
    relation: str                    # "proposed replacement for <squad player>" | "mentioned"
    status: MentionStatus
    candidates: Tuple[Candidate, ...] = ()
    player_id: Optional[int] = None  # set only for RESOLVED and SELECTED
    selected_by: Optional[str] = None

    def __post_init__(self):
        ids = {c.player_id for c in self.candidates}
        s = self.status
        if s is MentionStatus.RESOLVED:
            ok = len(ids) == 1 and self.player_id in ids and self.selected_by is None
        elif s is MentionStatus.AMBIGUOUS:
            ok = len(ids) >= 2 and self.player_id is None and self.selected_by is None
        elif s is MentionStatus.UNRESOLVED:
            ok = not ids and self.player_id is None and self.selected_by is None
        elif s is MentionStatus.SELECTED:
            ok = len(ids) >= 2 and self.player_id in ids and self.selected_by == "human"
        else:
            ok = self.player_id is None and self.selected_by == "human"
        if not ok:
            raise ValueError(f"Inconsistent mention state for {self.text!r} ({s.value})")

    @property
    def candidate(self):
        """The one player this mention refers to, for RESOLVED and SELECTED only."""
        return next((c for c in self.candidates if c.player_id == self.player_id), None)


# ── Finding mentions ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    text: str
    hits: tuple


def _candidate(element, teams):
    full = f"{element.get('first_name') or ''} {element.get('second_name') or ''}".strip()
    return Candidate(element["id"], full or element["web_name"], element["web_name"],
                     teams.get(element.get("team"), ""), _POSITION.get(element.get("element_type"), "?"))


def _case_ok(words):
    return words[0][0].isupper() and all(w[0].isupper() or w.casefold() in _PARTICLES for w in words[1:])


def _spans(text, elements):
    """Longest-first exact name matches in the text; spans that match nobody consume nothing."""
    toks = [(m.start(), m.end(), m.group()) for m in _TOKEN.finditer(text)]
    used, spans = set(), []
    for n in (3, 2, 1):
        for i in range(len(toks) - n + 1):
            idx = range(i, i + n)
            if any(j in used for j in idx):
                continue
            if any(text[toks[j][1]:toks[j + 1][0]].strip() for j in range(i, i + n - 1)):
                continue                                    # punctuation between the words
            words = [toks[j][2] for j in idx]
            if not _case_ok(words):
                continue
            start, end = toks[i][0], toks[i + n - 1][1]
            hits = _find_all_matches(text[start:end], elements)
            if hits:
                spans.append(_Span(start, end, text[start:end], tuple(hits)))
                used.update(idx)
    return sorted(spans, key=lambda s: s.start)


def find_mentions(text, bootstrap, squad_ids, limit=MAX_MENTIONS):
    """Non-squad players named in `text`, in order of appearance, with their identity status."""
    elements = bootstrap.get("elements", [])
    teams = {t["id"]: t.get("short_name", "") for t in bootstrap.get("teams", [])}
    squad_ids = set(squad_ids)
    spans = _spans(text, elements)
    squad_spans = [s for s in spans if len(s.hits) == 1 and s.hits[0]["id"] in squad_ids]
    found = {}                                               # key -> (position, Mention)

    def add(position, mention):
        k = key(mention.text)
        old = found.get(k)
        if old is None or (old[1].relation == "mentioned" and mention.relation != "mentioned"):
            found[k] = (position, mention)

    for n, span in enumerate(spans):
        if span in squad_spans:
            continue
        relation = _relation(text, spans, n, squad_spans)
        candidates = tuple(sorted((_candidate(e, teams) for e in span.hits),
                                  key=lambda c: (c.full_name, c.team, c.player_id)))
        if len(candidates) == 1:
            add(span.start, Mention(span.text, relation, MentionStatus.RESOLVED, candidates,
                                    candidates[0].player_id))
        else:
            add(span.start, Mention(span.text, relation, MentionStatus.AMBIGUOUS, candidates))

    for squad in squad_spans:                                # "<squad player> to <unknown name>"
        m = _AFTER_SQUAD.match(text, squad.end)
        if m and not any(s.start <= m.start(1) < s.end for s in spans):
            add(m.start(1), Mention(m.group(1), f"proposed replacement for {squad.hits[0]['web_name']}",
                                    MentionStatus.UNRESOLVED))

    ordered = sorted(found.values(), key=lambda pm: pm[0])
    linked = [pm for pm in ordered if pm[1].relation != "mentioned"]
    rest = [pm for pm in ordered if pm[1].relation == "mentioned"]
    chosen = (linked + rest)[:limit]
    return tuple(m for _, m in sorted(chosen, key=lambda pm: pm[0]))


def _relation(text, spans, n, squad_spans):
    span = spans[n]
    if n > 0 and spans[n - 1] in squad_spans:
        gap = text[spans[n - 1].end:span.start].strip().casefold()
        if gap in _FORWARD:
            return f"proposed replacement for {spans[n - 1].hits[0]['web_name']}"
    if n + 1 < len(spans) and spans[n + 1] in squad_spans:
        gap = text[span.end:spans[n + 1].start].strip().casefold()
        if gap in _REVERSE:
            return f"proposed replacement for {spans[n + 1].hits[0]['web_name']}"
    return "mentioned"


# ── Selection ─────────────────────────────────────────────────────────────────

def parse_pick(arg):
    """'King=222' -> ('King', 222); 'King=none' -> ('King', None)."""
    text, sep, value = arg.rpartition("=")
    if not sep or not text.strip():
        raise PickError(f'--pick must look like "Name=player_id" (got {arg!r})')
    value = value.strip().casefold()
    if value == "none":
        return text.strip(), None
    try:
        return text.strip(), int(value)
    except ValueError:
        raise PickError(f'--pick {arg!r}: player id must be a whole number or "none"') from None


def apply_picks(mentions, picks):
    """
    Apply operator picks ({mention text: player id or None}). Strict: a pick must name an
    existing AMBIGUOUS mention and, unless None (skip), one of that mention's own candidates.
    """
    by_key = {key(m.text): m for m in mentions}
    chosen = {}
    for text, player_id in picks.items():
        k = key(text)
        mention = by_key.get(k)
        if mention is None:
            names = ", ".join(m.text for m in mentions) or "none"
            raise PickError(f'--pick "{text}": no such mention in this post (mentions: {names})')
        if mention.status is not MentionStatus.AMBIGUOUS:
            raise PickError(f'--pick "{text}": "{mention.text}" is not ambiguous ({mention.status.value})')
        if player_id is not None and player_id not in {c.player_id for c in mention.candidates}:
            ids = ", ".join(str(c.player_id) for c in mention.candidates)
            raise PickError(f'--pick "{text}": {player_id} is not a candidate for "{mention.text}" '
                            f"(candidate ids: {ids})")
        chosen[k] = player_id
    out = []
    for m in mentions:
        k = key(m.text)
        if k not in chosen:
            out.append(m)
        elif chosen[k] is None:
            out.append(Mention(m.text, m.relation, MentionStatus.SKIPPED, m.candidates, None, "human"))
        else:
            out.append(Mention(m.text, m.relation, MentionStatus.SELECTED, m.candidates, chosen[k], "human"))
    return tuple(out)


def pending(mentions):
    return tuple(m for m in mentions if m.status is MentionStatus.AMBIGUOUS)
