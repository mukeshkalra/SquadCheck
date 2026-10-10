"""
"Analyse this": one human-selected RMT post -> squad evidence + an optional draft reply.

Boundaries:
  Reddit = retrieval (adapter) | scanner = extraction | FPL API = data
  projection engine = maths    | LLM = interpret the question and word the reply
  human = final judgement and posting. Nothing here can post.

Evidence is progressive: 15 players + XI/bench -> full; 15 players only -> player-level;
anything less -> a human is required. A draft is only kept if it is relevant, grounded in
the supplied evidence and free of SquadCheck promotion and links; otherwise `draft_reply`
is None and `human_required` explains why. Everything here is internal (never analytics).

Players named in the post but not in the squad are resolved by `mentions.py`. Exactly one
FPL match is projected like a squad player; several matches stop the run for an explicit
operator pick (--pick "Name=player_id") before the LLM is called. The LLM never receives
data about an ambiguous or unresolved player, only the fact that it could not be assessed.

Run live with:  python3 -m fpl.hustler.analyse <post_id or post URL> [--pick "Name=id"] [--skip-ambiguous]
Needs GOOGLE_VISION_API_KEY (scanner) and ANTHROPIC_API_KEY (+ the anthropic package).
"""

import argparse
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fpl.engine.pipeline import _build_payload, _norm_name, resolve_squad_smart, run_pipeline
from fpl.engine.projection import compute_xpts
from fpl.engine.scanner import scan_squad

from .adapter import RedditRmtAdapter, SourceUnavailable
from .mentions import MentionStatus, PickError, apply_picks, find_mentions, key, parse_pick, pending
from .models import (SQUAD_SIZE, CommunityPolicy, Conversation, EvidenceLevel, scan_evidence)

_ROOT = Path(__file__).resolve().parent.parent.parent
_MODEL = "claude-sonnet-5-5"
_THRESHOLD = 0.5

# Draft rules for this experiment: no SquadCheck mention, no links. Personalised advice is
# the whole point of a rate-my-team reply. AI-content permission is deliberately NOT
# asserted: a human edits and posts every reply; this only enforces what we promised.
_DRAFT_RULES = CommunityPolicy(allows_replies=True, allows_personalised_advice=True,
                               allows_ai_content=True)

_SYSTEM = """You help a human write a reply to a Reddit fantasy-football (FPL) rate-my-team post.
You get the post and data about the poster's squad as JSON.

Rules:
- Answer the poster's actual question, concisely and naturally, as a fellow FPL player would.
  Use plain words: no "xPts", probabilities, "confidence" or other model jargon.
- Use ONLY the supplied data. Never invent statistics, fixtures, injuries, prices or projections.
  Every number you write must appear in the data or in the post.
- Back every claim about a player with the plain FPL facts in that player's own "fpl" block: price_m,
  total_points, minutes (out of minutes_possible), form, status and news, and next_fixture (opponent,
  home or away, difficulty out of 5). Do not quote the engine's projections (xPts, components,
  drivers, risks); they only help you decide which players matter.
- "comparisons" holds rankings, club groupings and price-plus-bank sums already worked out for
  you. Quote them; do not rank, count or add anything yourself. Money after selling a player is
  approximate (the poster's own selling price may differ), so say "roughly".
- Use names and codes exactly as they appear in the data: do not expand abbreviations (a team
  code stays a code) and do not add outside knowledge such as news or history that is not in the
  data. State a fact about a player only if it appears in that same player's own data.
- mentioned_players are named in the post but are not in the squad; they have their own data, so
  use it exactly as you would for squad players. unassessed_mentions are names in the post you
  have no data for: say plainly that you cannot assess them.
- If evidence_level is players_only, the starting XI and bench are unknown: do not assume
  who starts or sits.
- Never mention SquadCheck, never name any tool or product, never include links.
- Answer every part of the question the data supports. For any part it does not support (for
  example a player who is not in the squad data), say plainly that you cannot assess it from what
  you have. Do not guess about it, and do not compare or imply anything about it beyond what the
  post itself says.

Structure of the reply:
1. Start with the answer to the main question. No preamble and no disclaimer first.
2. Then the facts that support it, covering each question the post asks, in the order it asks them.
3. You may add one short general judgement (for example the risk of owning several players from
   one club) only if you label it as a judgement and it states no numbers or facts about players.
4. If a limitation matters (for example the XI is unknown), end with one short sentence saying so.

Return a JSON object with:
- can_answer: true if the data lets you usefully address at least part of the question; false only
  if none of it can be addressed from the data, or the post is not asking about the squad.
- reply: the draft reply, about 190 words or fewer; an empty string when can_answer is false.
- reason: one short sentence stating what you covered and what you could not."""


_MAX_TOKENS = 4000    # headroom only: the reply is kept short by the prompt and low effort

_SCHEMA = {
    "type": "object",
    "properties": {
        "can_answer": {"type": "boolean"},
        "reply": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["can_answer", "reply", "reason"],
    "additionalProperties": False,
}


class LLMUnavailable(RuntimeError):
    """No LLM could be called (missing package or key)."""


@dataclass(frozen=True)
class LLMResponse:
    text: str
    stop_reason: str                      # "end_turn" is the only value whose text may be used
    stop_category: Optional[str] = None   # set on a refusal


class AnthropicLLM:
    """Default LLM: Claude via the anthropic SDK (imported lazily), JSON-schema output, low effort."""

    def __init__(self, model=_MODEL):
        self.model = model

    def __call__(self, system, user):
        try:
            import anthropic
        except ImportError as exc:
            raise LLMUnavailable("the anthropic package is not installed") from exc
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
        message = anthropic.Anthropic().messages.create(
            model=self.model, max_tokens=_MAX_TOKENS, system=system,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user", "content": user}])
        details = getattr(message, "stop_details", None)
        return LLMResponse(
            text="".join(block.text for block in message.content if getattr(block, "text", None)),
            stop_reason=message.stop_reason,
            stop_category=getattr(details, "category", None) if details else None)


class FplApiData:
    """Existing FPL data helpers (cached bootstrap, params, element summaries) from the API module."""

    def __init__(self):
        spec = importlib.util.spec_from_file_location("squad_check_api", _ROOT / "api" / "squad-check.py")
        self._api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self._api)

    def bootstrap(self):
        return self._api._bootstrap()

    def params(self, bootstrap):
        return self._api._params(bootstrap)

    def summaries(self, player_ids):
        return self._api._summaries(player_ids)


@dataclass(frozen=True)
class Analysis:
    conversation: Conversation            # question, URL and image link, always preserved
    scanner_status: Optional[str]
    evidence_level: EvidenceLevel
    squad_evidence: Optional[dict]
    draft_reply: Optional[str]
    human_required: bool
    human_reason: str = ""
    mentions: tuple = ()                  # Mention objects, for the operator; never sent to the LLM
    needs_selection: bool = False         # ambiguous mentions are waiting for an operator pick
    internal: bool = True

    def __post_init__(self):
        if self.needs_selection and not self.human_required:
            raise ValueError("needs_selection implies human_required")
        if self.internal is not True:
            raise ValueError("Analysis.internal must be True")
        if self.human_required == (self.draft_reply is not None):
            raise ValueError("human_required must be True exactly when there is no draft_reply")
        if self.human_required and not self.human_reason:
            raise ValueError("human_required needs a reason")
        if self.draft_reply is not None and self.squad_evidence is None:
            raise ValueError("A draft reply needs squad evidence")


def analyse(adapter, conversation, data, scan=scan_squad, llm=None, picks=None, skip_ambiguous=False):
    """
    Never raises for retrieval, scanner or LLM trouble; those hand the post to the human.
    `picks` is {mention text: player id or None}; `skip_ambiguous` continues without
    resolving ambiguous mentions (they are then reported to the LLM as not assessable).
    """
    llm = llm or AnthropicLLM()

    def human(reason, status=None, evidence=None, level=EvidenceLevel.INSUFFICIENT, mentions=()):
        return Analysis(conversation, status, level, evidence, None, True, reason, mentions)

    try:
        image = adapter.fetch_image(conversation)
    except (SourceUnavailable, ValueError) as exc:
        return human(f"Could not retrieve the image: {exc}")
    try:
        bootstrap = data.bootstrap()
        scan_result = scan(image, bootstrap.get("elements", []))
    except Exception as exc:
        return human(f"Scanner failed to run: {type(exc).__name__}: {exc}")
    status = scan_result.get("status")

    try:
        evidence, reason = _build_evidence(scan_result, bootstrap, data)
    except Exception as exc:
        return human(f"Squad analysis failed: {type(exc).__name__}: {exc}", status)
    if evidence is None:
        return human(f"{reason} (scanner status {status}).", status)
    level = EvidenceLevel(evidence["level"])

    squad_ids = {p["player_id"] for p in evidence["players"]}
    try:
        mentions = find_mentions(f"{conversation.title}\n{conversation.body}", bootstrap, squad_ids)
    except Exception as exc:
        return human(f"Could not resolve mentioned players: {type(exc).__name__}: {exc}", status, evidence, level)
    try:
        mentions = apply_picks(mentions, picks or {})
    except PickError as exc:
        return human(f"Invalid pick: {exc}", status, evidence, level, mentions)
    waiting = pending(mentions)
    if waiting and not skip_ambiguous:
        names = ", ".join(m.text for m in waiting)
        return Analysis(conversation, status, level, evidence, None, True,
                        f"Mentions need selection: {names}. No draft generated.",
                        mentions, needs_selection=True)
    evidence = _with_mentions(evidence, mentions, bootstrap, data, squad_ids)

    try:
        reply, why = _draft(llm, conversation, evidence)
    except LLMUnavailable as exc:
        return human(f"No draft: {exc}.", status, evidence, level, mentions)
    except Exception as exc:
        return human(f"No draft: LLM call failed ({type(exc).__name__}: {_safe_message(exc)}).",
                     status, evidence, level, mentions)
    if reply is None:
        return human(why, status, evidence, level, mentions)
    return Analysis(conversation, status, level, evidence, reply, False, "", mentions)


_SECRET_ENV = ("ANTHROPIC_API_KEY", "GOOGLE_VISION_API_KEY")
_KEY_LIKE = re.compile(r"sk-ant-[\w-]+")


def _safe_message(exc, limit=300):
    """An exception's text with any key-like value removed, for showing to the human."""
    text = str(exc)
    for name in _SECRET_ENV:
        value = os.environ.get(name)
        if value:
            text = text.replace(value, "[redacted]")
    return _KEY_LIKE.sub("[redacted]", text)[:limit]


# ── Evidence ──────────────────────────────────────────────────────────────────

def _gameweeks_played(bootstrap):
    return sum(1 for ev in bootstrap.get("events", []) if ev.get("finished"))


def _fpl_facts(element, summary, bootstrap):
    """Plain FPL data for one player (price, points, minutes, form, status, next fixture). No engine output."""
    facts = {}
    if element.get("now_cost") is not None:
        facts["price_m"] = round(element["now_cost"] / 10, 1)
    for key in ("total_points", "minutes"):
        if element.get(key) is not None:
            facts[key] = element[key]
    if element.get("form") not in (None, ""):
        facts["form"] = float(element["form"])
    if element.get("status"):
        facts["status"] = element["status"]
    if element.get("news"):
        facts["news"] = element["news"]
    if element.get("chance_of_playing_next_round") is not None:
        facts["chance_of_playing_next_round"] = element["chance_of_playing_next_round"]
    played = _gameweeks_played(bootstrap)
    if played:
        facts["minutes_possible"] = 90 * played
    fixtures = summary.get("fixtures", [])
    if fixtures:
        f = fixtures[0]
        short = {t["id"]: t.get("short_name", "") for t in bootstrap.get("teams", [])}
        opponent = short.get(f.get("team_a") if f.get("is_home") else f.get("team_h"))
        nxt = {"gameweek": f.get("event"), "opponent": opponent,
               "home": f.get("is_home"), "difficulty": f.get("difficulty")}
        facts["next_fixture"] = {k: v for k, v in nxt.items() if v is not None}
    return facts


def _attach_facts(players, bootstrap, summaries):
    elem_by_id = {e["id"]: e for e in bootstrap.get("elements", [])}
    return [{**p, "fpl": _fpl_facts(elem_by_id.get(p["player_id"], {}),
                                    summaries.get(p["player_id"], {}), bootstrap)}
            for p in players]


_BANK = re.compile(r"£\s*(\d+(?:\.\d+)?)\s*m?\b[^.\n]{0,15}?(?:itb|in the bank|in bank)", re.IGNORECASE)


def _comparisons(evidence, post_text):
    """Rankings, club groupings and price-plus-bank sums, worked out in code so the LLM only quotes them."""
    players = [p for p in evidence.get("players", []) if p.get("fpl")]
    name = lambda p: p.get("web_name")
    out = {}
    outfield = [p for p in players if p.get("position") != 1]
    for key, label in (("total_points", "outfield_by_total_points_low_to_high"),
                       ("minutes", "outfield_by_minutes_low_to_high")):
        ranked = sorted((p for p in outfield if key in p["fpl"]), key=lambda p: (p["fpl"][key], name(p)))
        if ranked:
            out[label] = [{"player": name(p), key: p["fpl"][key]} for p in ranked]
    clubs = {}
    for p in players:
        clubs.setdefault(p.get("team"), []).append(p)
    grouped = {}
    for club, members in clubs.items():
        if club and len(members) >= 2:
            fixtures = [{"player": name(p), **p["fpl"]["next_fixture"]}
                        for p in members if p["fpl"].get("next_fixture")]
            same = len(fixtures) == len(members) and len(
                {(f.get("opponent"), f.get("home")) for f in fixtures}) == 1
            grouped[club] = {"count": len(members), "players": [name(p) for p in members],
                             "next_fixtures": fixtures, "all_play_the_same_match": same}
    if grouped:
        out["clubs_with_several_players"] = grouped
    match = _BANK.search(post_text or "")
    if match:
        bank = float(match.group(1))
        out["bank_from_post_m"] = bank
        out["price_plus_bank_m"] = [{"player": name(p), "price_m": p["fpl"]["price_m"],
                                     "price_plus_bank_m": round(p["fpl"]["price_m"] + bank, 1)}
                                    for p in players if "price_m" in p["fpl"]]
    return out


def _build_evidence(scan_result, bootstrap, data):
    """Returns (evidence, None) or (None, reason). Reuses the scanner, resolver and engine."""
    names, xi_known, _ = scan_evidence(scan_result)
    if len(names) != SQUAD_SIZE:
        return None, f"Only {len(names)} of {SQUAD_SIZE} players could be identified"

    elements = bootstrap.get("elements", [])
    seen, players = set(), []
    for p in scan_result["players"]:
        key = (p.get("name") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            players.append({**p, "is_starting": bool(p.get("is_starting"))})   # resolver needs the key
    resolved, ambiguous, unresolved = resolve_squad_smart(players, elements)
    if ambiguous or unresolved:
        bad = ", ".join(x["name"] for x in ambiguous + unresolved)
        return None, f"Could not reliably identify: {bad}"
    ids = [r["player_id"] for r in resolved]
    if len(set(ids)) != SQUAD_SIZE:
        return None, "Identified players are not 15 distinct FPL players"

    summaries = data.summaries(ids)
    params = data.params(bootstrap)
    pos_by_name = {_norm_name(r["name"]): r["position"] for r in resolved}

    if xi_known:
        full_scan = {**scan_result, "players": [
            {**p, "position": pos_by_name.get(_norm_name(p["name"]), p.get("position"))}
            for p in scan_result["players"]]}
        payload = run_pipeline(full_scan, bootstrap, summaries, params, _THRESHOLD)
        if payload.get("status") == "OK":
            return {
                "level": EvidenceLevel.PLAYERS_AND_XI.value, "xi_known": True,
                "scanner_status": scan_result.get("status"), "view_type": scan_result.get("view_type"),
                "gameweek": payload["gameweek"], "players": _attach_facts(payload["players"], bootstrap, summaries),
                "submitted_xi": payload["submitted_xi"], "recommended_xi": payload["recommended_xi"],
                "action": payload["action"], "delta": payload["delta"],
                "substitutions": payload["substitutions"], "message": payload["message"],
            }, None

    elem_by_id = {e["id"]: e for e in elements}
    team_by_id = {t["id"]: {"name": t.get("name", ""), "short_name": t.get("short_name", "")}
                  for t in bootstrap.get("teams", [])}
    projections, gameweek = [], None
    for r in resolved:
        pid = r["player_id"]
        summary = summaries.get(pid, {})
        fixtures = summary.get("fixtures", [])
        fixture = fixtures[0] if fixtures else None
        if gameweek is None and fixture and "event" in fixture:
            gameweek = fixture["event"]
        projections.append({**compute_xpts(elem_by_id[pid], summary.get("history", []), fixture, params),
                            "is_starting": None})
    # The engine's own payload builder supplies the per-player fields; its bench fields are unused.
    placeholder = {"action": "HOLD", "delta": 0.0, "substitutions": [],
                   "submitted_xi": [], "recommended_xi": []}
    payload = _build_payload(scan_result, projections, placeholder, _THRESHOLD, elem_by_id,
                             team_by_id, gameweek, None, None)
    return {
        "level": EvidenceLevel.PLAYERS_ONLY.value, "xi_known": False,
        "scanner_status": scan_result.get("status"), "view_type": scan_result.get("view_type"),
        "gameweek": payload["gameweek"],
        "players": [{**p, "is_starting": None} for p in _attach_facts(payload["players"], bootstrap, summaries)],
    }, None


def _with_mentions(evidence, mentions, bootstrap, data, squad_ids):
    """
    Evidence plus the mentioned players. Only RESOLVED and SELECTED mentions are projected,
    with the same engine as the squad. Ambiguous and unresolved mentions are passed on as
    text, a relation and a reason only: no candidate names, ids or numbers.
    """
    wanted = []
    for m in mentions:
        pid = m.player_id
        if pid is not None and pid not in squad_ids and pid not in wanted:
            wanted.append(pid)
    projected, failure = {}, None
    if wanted:
        try:
            projected = _project_extra(wanted, bootstrap, data)
        except Exception as exc:
            failure = type(exc).__name__

    mentioned, unassessed = [], []
    for m in mentions:
        if m.status in (MentionStatus.RESOLVED, MentionStatus.SELECTED):
            if m.player_id in squad_ids:
                continue                                    # already in the squad evidence
            player = projected.get(m.player_id)
            if player is None:
                unassessed.append({"text": m.text, "relation": m.relation,
                                   "reason": f"projection failed ({failure or 'no data'})"})
                continue
            mentioned.append({**player, "in_squad": False, "is_starting": None, "relation": m.relation,
                              "matched_text": m.text,
                              "identity": "unique match" if m.status is MentionStatus.RESOLVED
                              else "selected by operator"})
        elif m.status is MentionStatus.AMBIGUOUS:
            unassessed.append({"text": m.text, "relation": m.relation,
                               "reason": "matches more than one player; not assessed"})
        elif m.status is MentionStatus.UNRESOLVED:
            unassessed.append({"text": m.text, "relation": m.relation,
                               "reason": "no matching FPL player found"})
    out = dict(evidence)
    if mentioned:
        out["mentioned_players"] = mentioned
    if unassessed:
        out["unassessed_mentions"] = unassessed
    return out


def _project_extra(player_ids, bootstrap, data):
    """Existing projection engine and payload builder for players outside the squad."""
    elements = bootstrap.get("elements", [])
    elem_by_id = {e["id"]: e for e in elements}
    team_by_id = {t["id"]: {"name": t.get("name", ""), "short_name": t.get("short_name", "")}
                  for t in bootstrap.get("teams", [])}
    summaries = data.summaries(player_ids)
    params = data.params(bootstrap)
    projections = []
    for pid in player_ids:
        summary = summaries.get(pid, {})
        fixtures = summary.get("fixtures", [])
        fixture = fixtures[0] if fixtures else None
        projections.append({**compute_xpts(elem_by_id[pid], summary.get("history", []), fixture, params),
                            "is_starting": None})
    placeholder = {"action": "HOLD", "delta": 0.0, "substitutions": [],
                   "submitted_xi": [], "recommended_xi": []}
    payload = _build_payload({}, projections, placeholder, _THRESHOLD, elem_by_id, team_by_id,
                             None, None, None)
    return {p["player_id"]: p for p in _attach_facts(payload["players"], bootstrap, summaries)}


# ── Draft ─────────────────────────────────────────────────────────────────────

def _draft(llm, conversation, evidence):
    """Returns (reply, None) when usable, else (None, reason a human needs to read)."""
    user = json.dumps({
        "post": {"title": conversation.title, "body": conversation.body},
        "evidence_level": evidence["level"],
        "evidence": evidence,
        "comparisons": _comparisons(evidence, f"{conversation.title}\n{conversation.body}"),
    }, default=str, ensure_ascii=False)
    response = llm(_SYSTEM, user)

    # The stop reason is checked before the text is looked at: a cut-off or refused
    # response is never parsed, so a truncated answer can never become a draft.
    if response.stop_reason == "max_tokens":
        return None, "No draft: the LLM output was truncated (max_tokens)."
    if response.stop_reason == "refusal":
        return None, f"No draft: the LLM declined to answer (refusal: {response.stop_category or 'unspecified'})."
    if response.stop_reason != "end_turn":
        return None, f"No draft: unexpected LLM stop reason '{response.stop_reason}'."

    try:
        answer = json.loads(response.text)
    except ValueError:
        return None, "No draft: the LLM did not return valid JSON."
    if not (isinstance(answer, dict) and isinstance(answer.get("can_answer"), bool)
            and isinstance(answer.get("reply"), str) and isinstance(answer.get("reason"), str)):
        return None, "No draft: the LLM JSON did not match the expected shape."

    if not answer["can_answer"]:
        return None, f"No draft: {answer['reason'].strip() or 'the evidence does not answer the question'}."
    reply = answer["reply"].strip()
    if not reply:
        return None, "No draft: the LLM said it could answer but returned an empty reply."
    broken = _DRAFT_RULES.violations(reply)
    if broken:
        return None, "Draft discarded: " + "; ".join(broken) + "."
    ungrounded = _ungrounded_numbers(reply, user)
    if ungrounded:
        return None, "Draft discarded: numbers not found in the evidence or post: " + ", ".join(ungrounded) + "."
    return reply, None


_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _ungrounded_numbers(reply, source_text):
    """Numbers in the reply that appear nowhere in the supplied data (to the reply's own precision)."""
    available = [float(n) for n in _NUMBER.findall(source_text)]
    bad = []
    for token in _NUMBER.findall(reply):
        places = len(token.split(".")[1]) if "." in token else 0
        value = float(token)
        if not any(round(a, places) == value for a in available):
            bad.append(token)
    return bad


# ── Output ────────────────────────────────────────────────────────────────────

def _render_mention(m, projections):
    relation = "" if m.relation == "mentioned" else f" ({m.relation})"
    if m.status in (MentionStatus.RESOLVED, MentionStatus.SELECTED):
        how = "unique match" if m.status is MentionStatus.RESOLVED else f"selected by operator, id {m.player_id}"
        lines = [f"  {m.text} → {m.candidate.label()}  [{how}]{relation}"]
        p = projections.get(m.player_id)
        if p:
            lines.append(f"      xPts {p['xPts']:.1f} conf {p['confidence']}")
        return lines
    if m.status is MentionStatus.AMBIGUOUS:
        lines = [f"  {m.text} → AMBIGUOUS: matches {len(m.candidates)} players{relation}"]
        return lines + [f"      id {c.player_id}  {c.label()}" for c in m.candidates]
    if m.status is MentionStatus.UNRESOLVED:
        return [f"  {m.text} → not found{relation}"]
    return [f"  {m.text} → skipped by operator"]


def render(analysis):
    c, ev = analysis.conversation, analysis.squad_evidence
    # The Reddit URL is workflow metadata for the human; it is never part of the reply.
    lines = ["Opportunity", f"Title: {c.title}", f"Reddit: {c.url}",
             f"Image: {c.media[0].url if c.media else 'n/a'}", "",
             "Question:", c.body or "(no text in the post, image only)", "",
             "Evidence:", f"scanner: {analysis.scanner_status}   evidence: {analysis.evidence_level.value}"]
    if ev:
        lines.append(f"gameweek: {ev['gameweek']}")
        for p in sorted(ev["players"], key=lambda p: -p["xPts"]):
            slot = {True: "XI", False: "bench", None: "?"}[p["is_starting"]]
            lines.append(f"  {p['web_name']:<14} {p['player_api']['team_name']:<4} pos{p['position']} "
                         f"{slot:<5} xPts {p['xPts']:.1f} conf {p['confidence']}")
        if ev["xi_known"]:
            lines.append(f"bench check: {ev['message']}")
    if analysis.mentions:
        projections = {p["player_id"]: p for p in (ev or {}).get("mentioned_players", [])}
        lines += ["", "Mentioned players (not in the squad):"]
        for m in analysis.mentions:
            lines += _render_mention(m, projections)
    lines.append("")
    if analysis.needs_selection:
        waiting = pending(analysis.mentions)
        picks = " ".join(f'--pick "{m.text}=<id>"' for m in waiting)
        lines += [f"NEEDS SELECTION: {len(waiting)} ambiguous mention(s). No draft generated.",
                  f"Re-run with: python3 -m fpl.hustler.analyse {c.post_id} {picks}",
                  "(use the id from the list above; =none skips a mention; --skip-ambiguous continues without them)"]
    elif analysis.human_required:
        lines.append(f"HUMAN REQUIRED: {analysis.human_reason}")
    else:
        lines += ["DRAFT (edit before posting):", analysis.draft_reply]
    return "\n".join(lines)


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="python3 -m fpl.hustler.analyse")
    parser.add_argument("post", help="post id or post URL from the feed")
    parser.add_argument("--pick", action="append", default=[], metavar='"Name=player_id"',
                        help="choose the player for an ambiguous mention (=none to skip it)")
    parser.add_argument("--skip-ambiguous", action="store_true",
                        help="continue without resolving ambiguous mentions")
    args = parser.parse_args(argv)
    picks = {}
    for raw in args.pick:
        text, player_id = parse_pick(raw)
        if key(text) in {key(t) for t in picks}:
            raise PickError(f'--pick given twice for "{text}"')
        picks[text] = player_id
    return args.post, picks, args.skip_ambiguous


def main(argv):
    try:
        target_id, picks, skip_ambiguous = _parse_args(argv)
    except PickError as exc:
        print(f"Invalid pick: {exc}")
        return 2
    from .feed import SOURCE
    adapter = RedditRmtAdapter(SOURCE)
    try:
        posts = adapter.fetch_posts()
    except SourceUnavailable as exc:
        print(f"Feed unavailable: {exc}")
        return 1
    target = next((p for p in posts if target_id in (p.post_id, p.url) or p.post_id in target_id), None)
    if target is None:
        print("Post not found in the current feed.")
        return 1
    result = analyse(adapter, target, FplApiData(), picks=picks, skip_ambiguous=skip_ambiguous)
    print(render(result))
    return 3 if result.needs_selection else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
