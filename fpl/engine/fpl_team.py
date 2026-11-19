"""
FPL ID lookup: POST /api/fpl-team  {"fpl_id": 123456}

Reads the public FPL entry and picks endpoints (no login) and returns the team in the same
"manual_squad" shape /api/squad-check already accepts, so the rest of the flow is unchanged.

Limits that the caller must show to the user:
  * FPL only publishes picks once a deadline has passed, so this is the team as of the last
    deadline. Transfers made since then are not visible.
  * There is no public team-name search, so the lookup is by FPL ID only; the team name is
    returned so the user can confirm it is theirs.
"""

import json
import logging
import urllib.error
from typing import Callable, Tuple

from .transfer_adapter import ApiError, FplDataSource, MAX_BODY_BYTES, _http_fetch, error_payload

log = logging.getLogger("squadcheck.fpl_team")

_ENTRY_URL = "https://fantasy.premierleague.com/api/entry/{}/"
_PICKS_URL = "https://fantasy.premierleague.com/api/entry/{}/event/{}/picks/"
MAX_FPL_ID = 50_000_000


def parse_request(raw) -> int:
    if not isinstance(raw, dict) or set(raw) != {"fpl_id"}:
        raise ApiError(400, "BAD_REQUEST", "Send {\"fpl_id\": <number>}")
    v = raw["fpl_id"]
    if isinstance(v, str) and v.strip().isdigit():
        v = int(v.strip())
    if isinstance(v, bool) or not isinstance(v, int) or not (1 <= v <= MAX_FPL_ID):
        raise ApiError(400, "BAD_REQUEST", "FPL ID must be a whole number, for example 1234567")
    return v


def build_team(fpl_id: int, bootstrap: dict, fetch: Callable = _http_fetch) -> dict:
    try:
        entry = fetch(_ENTRY_URL.format(fpl_id))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ApiError(404, "NOT_FOUND", "No FPL team found with that ID. Check the number and try again.")
        raise ApiError(502, "UPSTREAM_ERROR", "FPL is not responding right now. Try again in a moment.")
    except Exception:
        raise ApiError(502, "UPSTREAM_ERROR", "FPL is not responding right now. Try again in a moment.")
    gw = entry.get("current_event") if isinstance(entry, dict) else None
    if not isinstance(gw, int) or gw < 1:
        raise ApiError(409, "NO_PICKS_YET", "That team has no gameweek picks yet, so there is nothing to check.")
    try:
        data = fetch(_PICKS_URL.format(fpl_id, gw))
    except urllib.error.HTTPError as e:
        code = 409 if e.code in (403, 404) else 502
        raise ApiError(code, "NO_PICKS_YET" if code == 409 else "UPSTREAM_ERROR",
                       "We couldn't read that team's picks from FPL. Try again, or upload a screenshot.")
    except Exception:
        raise ApiError(502, "UPSTREAM_ERROR", "FPL is not responding right now. Try again in a moment.")

    picks = sorted((data.get("picks") or []), key=lambda p: p.get("position", 99))
    by_id = {e.get("id"): e for e in bootstrap.get("elements", []) if isinstance(e, dict)}
    if len(picks) != 15 or any(p.get("element") not in by_id for p in picks):
        raise ApiError(422, "BAD_TEAM", "We couldn't read a full 15-player squad for that team. Try a screenshot instead.")
    name = lambda p: by_id[p["element"]].get("web_name")
    names = [name(p) for p in picks]
    if len(set(names)) != 15 or not all(isinstance(n, str) and n for n in names):
        raise ApiError(422, "BAD_TEAM", "Two of your players share a name, so we can't match them. Try a screenshot instead.")
    cap = next((name(p) for p in picks[:11] if p.get("is_captain")), None)
    vice = next((name(p) for p in picks[:11] if p.get("is_vice_captain")), None)
    hist = data.get("entry_history") or {}
    bank = hist.get("bank")
    return {
        "status": "OK",
        "team_name": entry.get("name") if isinstance(entry.get("name"), str) else None,
        "picks_gameweek": gw,
        "manual_squad": {"names": names, "bench": names[11:], "captain": cap,
                         "vice": vice if cap is not None else None},
        "bank_m": round(bank / 10.0, 1) if isinstance(bank, int) and bank >= 0 else None,
        "note": "This is your team as of the GW%d deadline. Transfers made since are not included." % gw,
    }


def handle(body: bytes, source: FplDataSource, fetch: Callable = _http_fetch) -> Tuple[int, dict]:
    try:
        if len(body) > MAX_BODY_BYTES:
            raise ApiError(413, "BAD_REQUEST", "Request body too large")
        try:
            raw = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "BAD_REQUEST", "Request body is not valid JSON")
        fpl_id = parse_request(raw)
        try:
            bootstrap = source.bootstrap()
        except Exception:
            raise ApiError(502, "UPSTREAM_ERROR", "FPL is not responding right now. Try again in a moment.")
        return 200, build_team(fpl_id, bootstrap, fetch)
    except ApiError as err:
        return err.http_status, error_payload(err)
    except Exception:
        log.exception("fpl-team failed unexpectedly")
        return 500, {"status": "ERROR", "error": "Internal error", "details": []}
