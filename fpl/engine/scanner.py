"""
SquadCheck FPL Scanner

Accepts a squad description (image bytes or pre-structured dict) and returns
a structured ScanResult that the pipeline can consume.

Image input path
----------------
Uses Apple Vision (VNRecognizeTextRequest) via a compiled Swift helper.
On first call the Swift source (_vision_ocr.swift, same directory) is compiled
with `swiftc`; the resulting binary is cached at _vision_ocr in the same
directory.  Requires macOS + Xcode Command Line Tools.

Structured dict input path
--------------------------
Accepts the squad dict format already used by tests and the demo server path.
Validates structure (15 players, 11 starters, no duplicates) and returns the
appropriate status code without any OCR.

Status codes
------------
VALID         15 players found, 11 starters, no duplicate names.
PARTIAL       Fewer than 15 players, or wrong starter count.
AMBIGUOUS     Duplicate player names.
UNSUPPORTED   Input type not handled, or OCR unavailable / failed.

View types
----------
PITCH   FPL Pitch/Formation view  (detected from "Substitutes" marker)
LIST    FPL List/Squad tab view    (detected from positional column markers)
UNKNOWN Could not be determined
"""

import os
import re
import base64
from itertools import combinations as _combinations
import json
import subprocess
import tempfile
import unicodedata
import urllib.request
from pathlib import Path

# ── Status / view constants ───────────────────────────────────────────────────
VALID       = "VALID"
PARTIAL     = "PARTIAL"
AMBIGUOUS   = "AMBIGUOUS"
UNSUPPORTED = "UNSUPPORTED"

VIEW_PITCH   = "PITCH"
VIEW_LIST    = "LIST"
VIEW_UNKNOWN = "UNKNOWN"

_SQUAD_SIZE   = 15
_STARTER_SIZE = 11
_VALID_POSITIONS = frozenset({1, 2, 3, 4})

# ── OCR binary paths ──────────────────────────────────────────────────────────
_HERE   = Path(__file__).resolve().parent
_SWIFT  = _HERE / "_vision_ocr.swift"
_BIN    = _HERE / "_vision_ocr"          # compiled binary

# ── Pitch View: adaptive parser constants ─────────────────────────────────────
# The Pitch View parser no longer uses hardcoded Y-bands.  Instead it:
#   1. Locates bench labels ("GKP", "1. FWD", "2. DEF", …) as a separator anchor.
#   2. Clusters candidate player-name blocks dynamically by Y proximity.
#   3. Assigns positions (GKP/DEF/MID/FWD) by cluster order (top → bottom).
# This is robust to different formations, image crops, and device sizes.

_CLUSTER_TOLERANCE  = 0.03   # blocks within this Y-distance belong to the same row
_SEPARATOR_GAP      = 0.04   # exclusion zone either side of bench-label separator

# ── Text-filter heuristics ────────────────────────────────────────────────────
# Words that are never player names even though they pass the case/length checks
# ── Player name extraction ────────────────────────────────────────────────────
# The FPL screenshot only needs to give us player names and C/VC badges.
# Everything else (team, fixture, home/away, points, price) comes from the
# FPL API which we already call. We extract the name by scanning tokens
# left-to-right and stopping at the first token that cannot be part of a
# player name:
#   • All-uppercase 2+ chars  → team code / sponsor (HUL, BOU, MAREX, CMC…)
#   • Starts with digit       → score / price / number
#   • Starts with '('         → bracket in  "( A )", "(H)", "(A)"
#   • Exactly 'vs'            → opponent prefix
# This is format-agnostic: it handles "Pickford HUL ( A )", "Haaland 12",
# "Saka vs NOR", "Ødegaard LEE ( H )" and any future FPL display mode
# without needing to enumerate stat formats.

_ALL_CAPS_TOKEN = re.compile(r'^[A-Z]{2,}$')   # team codes, sponsors


def _extract_player_name(text: str) -> str:
    """
    Extract just the player name from an OCR text block that may have
    fixture/stat data appended by Google Vision.

    'Pickford HUL ( A )'    → 'Pickford'
    'Szoboszlai MCI ( H )'  → 'Szoboszlai'
    'Haaland 12'             → 'Haaland'
    'Ødegaard LEE ( H )'    → 'Ødegaard'
    'Van Hecke'              → 'Van Hecke'   (no stat — unchanged)
    'M.Sangaré'              → 'M.Sangaré'  (no stat — unchanged)
    '1. FWD'                 → ''            (digit-first → bench label, filtered)
    """
    name_parts = []
    for tok in text.split():
        if _ALL_CAPS_TOKEN.match(tok):   # HUL, BOU, MAREX, ETIHAD …
            break
        if tok[0].isdigit():              # 12, 8, 5.9, 5.5m …
            break
        if tok[0] == '(':                 # ( A ), (H), (A) …
            break
        if tok.lower() == 'vs':           # vs SUN, vs ARS …
            break
        name_parts.append(tok)
    return ' '.join(name_parts).strip()


_NAME_BLOCKLIST = frozenset({
    "fantasy", "substitutes", "substitute",
    "sta",     "dard",        "artered",    # "Standard Chartered" fragments
    "fly",     "better",      "indeed",
    "emirates","standard",    "chartered",
    "express", "american",    "airways",
    "etihad",  "marex",       "usdc",       # kit sponsors (sometimes mixed-case OCR)
    "halo",    "hyundai",     "cazoo",      # other common FPL sponsor names
    # Google Vision paragraph-level additions:
    "knox",    "illcmc",     "hiicmc",      # Brentford/CMC Markets kit text in mixed case
    "hicmc",   "iiicmc",                   # CMC Markets OCR variants
    "vitality",                             # Bournemouth/Brighton kit sponsor
})


def _is_player_name(text: str) -> bool:
    """
    Heuristic: does this OCR token look like an FPL player name?

    Rules (all must pass):
      - Length ≥ 3
      - Not all-uppercase  (filters "GKP", "ETIHAD", "HALO", "USDC", …)
      - Not all-lowercase  (filters sponsors like "indeed", "dard")
      - First character is a letter, not a digit (filters "1. MID", "90", …)
      - Not a section header unique to List View
      - Not a combined team+position token ("Man City MID", "Arsenal DEF", …)
      - First word not in the blocklist
    """
    t = text.strip()
    if len(t) < 3:
        return False

    letters = [c for c in t if c.isalpha()]
    if not letters:
        return False
    if all(c.isupper() for c in letters):
        return False
    if all(c.islower() for c in letters):
        return False
    if not t[0].isalpha():   # blocks "(Vitality)", "1. FWD", "[...]" etc.
        return False

    # List View section headers and bench label — never player names.
    # Prefix check on "substitut" catches OCR variants like "Substitutoa",
    # "Substituto", "Substitution", etc.
    tl = t.lower()
    if tl in {"goalkeeper", "defenders", "midfielders", "forwards", "player"}:
        return False
    if tl.startswith("substitut"):
        return False

    # Combined "Team Name + POS" tokens ("Man City MID", "Arsenal DEF", …)
    # These end with a space-separated position abbreviation.
    parts = t.split()
    if parts and parts[-1].upper() in {"GKP", "DEF", "MID", "FWD"}:
        return False

    # Reject non-Latin scripts — OCR sometimes reads jersey logos (e.g. "KNOX")
    # as Cyrillic ("КлОХ"). FPL player names are always Latin-script; anything
    # above U+03FF (start of Cyrillic block) is an OCR artefact.
    if any(c.isalpha() and ord(c) > 0x03FF for c in t):
        return False

    first_word = parts[0].lower().rstrip(".,:")
    if first_word in _NAME_BLOCKLIST:
        return False

    return True


# ─────────────────────────────────────────────────────────────────────────────
# OCR BACKEND  (Apple Vision via Swift subprocess)
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_binary() -> bool:
    """Compile the Swift OCR helper if not already built. Returns True on success."""
    if _BIN.exists():
        return True
    if not _SWIFT.exists():
        return False
    swift = _find_swift()
    if not swift:
        return False
    try:
        r = subprocess.run(
            [swift + "c", str(_SWIFT), "-o", str(_BIN)],
            capture_output=True, timeout=120,
        )
        return r.returncode == 0
    except Exception:
        return False


def _find_swift() -> str:
    """Return path to the swift toolchain prefix, or empty string."""
    for candidate in ("/usr/bin/swift", "/usr/local/bin/swift"):
        if os.path.exists(candidate):
            return candidate.rstrip("c").rstrip("t").rstrip("f").rstrip("i").rstrip("w").rstrip("s")
            # We want the directory prefix, not the command name
    return ""


def _find_swiftc() -> str:
    """Return path to swiftc, or empty string."""
    for candidate in ("/usr/bin/swiftc", "/usr/local/bin/swiftc"):
        if os.path.exists(candidate):
            return candidate
    return ""


def _ensure_binary() -> bool:  # noqa: F811 — redefine cleanly
    """Compile the Swift OCR helper if not already built. Returns True on success."""
    if _BIN.exists():
        return True
    if not _SWIFT.exists():
        return False
    swiftc = _find_swiftc()
    if not swiftc:
        return False
    try:
        r = subprocess.run(
            [swiftc, str(_SWIFT), "-o", str(_BIN)],
            capture_output=True, timeout=120,
        )
        return r.returncode == 0 and _BIN.exists()
    except Exception:
        return False


_OCR_UNAVAILABLE = None    # sentinel: no OCR backend available
_OCR_IMAGE_ERROR  = []     # sentinel: backend ran but image could not be processed


# ── Backend 1: Apple Vision via Swift (macOS local dev) ──────────────────────

def _run_ocr_swift(image_bytes: bytes):
    """
    Run Apple Vision OCR via the compiled Swift binary.
    Returns blocks / _OCR_UNAVAILABLE / _OCR_IMAGE_ERROR.
    """
    if not _ensure_binary():
        return _OCR_UNAVAILABLE

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(image_bytes)
        tmp = f.name
    try:
        r = subprocess.run(
            [str(_BIN), tmp],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return _OCR_IMAGE_ERROR
        return json.loads(r.stdout)
    except Exception:
        return _OCR_IMAGE_ERROR
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ── Backend 2: Google Cloud Vision REST API (Linux / Vercel) ─────────────────

def _run_ocr_google_vision(image_bytes: bytes, api_key: str):
    """
    Call Google Cloud Vision DOCUMENT_TEXT_DETECTION and return blocks in the
    same format as _run_ocr_swift so the existing parsers need no changes.

    Output schema (matches Apple Vision _run_ocr_swift output exactly)
    ------------------------------------------------------------------
      text : str    line-level text (one paragraph ≈ one line)
      conf : float  1.0  (Google Vision does not expose per-paragraph confidence)
      x    : float  LEFT EDGE of bounding box, normalised [0, 1]
      y    : float  BOTTOM EDGE of bounding box, normalised [0, 1], y=0 at bottom
      w    : float  width,  normalised [0, 1]
      h    : float  height, normalised [0, 1]

    Granularity
    -----------
    Apple Vision VNRecognizeTextRequest returns ONE observation per LINE of text.
    Google Vision textAnnotations[1:] returns ONE annotation per WORD — wrong
    granularity; "De Cuyper" becomes ["De","Cuyper"] and team tokens like
    "Man City MID" become ["Man","City","MID"] which breaks the name filters.

    Google Vision fullTextAnnotation.pages[].blocks[].paragraphs[] gives ONE
    paragraph per LINE of text — the correct equivalent.  We join the words
    within each paragraph (space-separated) to reconstruct the line text.

    Coordinate conversion
    ---------------------
    Google Vision: integer pixel coords, origin TOP-LEFT (y increases downward)
    Apple Vision:  normalised [0,1],     origin BOTTOM-LEFT (y increases upward)
    Apple Vision stores the BOTTOM EDGE of the bounding box in the y field.

        x_left   = min_pixel_x / image_width          ← left edge (not centre)
        y_bottom = 1.0 - (max_pixel_y / image_height) ← flip: pixel bottom → AV bottom
        width    = (max_px_x - min_px_x) / image_width
        height   = (max_px_y - min_px_y) / image_height
    """
    payload = json.dumps({
        "requests": [{
            "image": {"content": base64.b64encode(image_bytes).decode()},
            "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
        }]
    }).encode()

    req = urllib.request.Request(
        f"https://vision.googleapis.com/v1/images:annotate?key={api_key}",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())

    resp = data.get("responses", [{}])[0]
    return _parse_gv_response(resp)


def _parse_gv_response(resp: dict):
    """
    Convert a single Google Vision API response object into the normalised
    block list used by the parsers.  Separated from the network call so
    tests can replay saved responses without hitting the API.
    """
    pages = resp.get("fullTextAnnotation", {}).get("pages", [])
    if not pages:
        return _OCR_IMAGE_ERROR

    img_w = pages[0].get("width",  0)
    img_h = pages[0].get("height", 0)
    if not img_w or not img_h:
        return _OCR_IMAGE_ERROR

    def _word_text(word):
        return "".join(sym.get("text", "") for sym in word.get("symbols", []))

    def _para_coords(para):
        verts = para.get("boundingBox", {}).get("vertices", [])
        if not verts:
            return None
        xs = [v.get("x", 0) for v in verts]
        ys = [v.get("y", 0) for v in verts]
        x_min, x_max = min(xs) / img_w, max(xs) / img_w
        y_min, y_max = min(ys) / img_h, max(ys) / img_h
        return (x_min, 1.0 - y_max, x_max - x_min, y_max - y_min)

    blocks = []
    for page in pages:
        for block in page.get("blocks", []):
            for para in block.get("paragraphs", []):
                words = [_word_text(w) for w in para.get("words", [])]
                text  = " ".join(words).strip()
                if not text:
                    continue
                coords = _para_coords(para)
                if coords is None:
                    continue
                x_left, y_bottom, width, height = coords
                blocks.append({
                    "text": text, "conf": 1.0,
                    "x": x_left, "y": y_bottom, "w": width, "h": height,
                })

    return blocks


# ── Dispatcher: Swift on macOS, Google Vision on Linux/cloud ─────────────────

def _run_ocr(image_bytes: bytes):
    """
    Run Google Cloud Vision OCR.  Single backend used everywhere —
    local dev and Vercel behave identically.

    Requires GOOGLE_VISION_API_KEY env var.
    Returns list[dict] | _OCR_UNAVAILABLE | _OCR_IMAGE_ERROR.
    """
    import sys

    api_key = os.environ.get("GOOGLE_VISION_API_KEY", "")
    print(f"[diag] image_bytes={len(image_bytes)}  api_key_present={bool(api_key)}  backend=google_vision",
          file=sys.stderr)

    if not api_key:
        return _OCR_UNAVAILABLE

    try:
        result = _run_ocr_google_vision(image_bytes, api_key)
        if isinstance(result, list):
            print(f"[diag] google_vision_blocks={len(result)}", file=sys.stderr)
        return result
    except Exception as exc:
        http_status = getattr(exc, "code", None)
        http_body   = None
        if http_status:
            try: http_body = exc.read(512).decode("utf-8", errors="replace")
            except Exception: pass
        print(f"[scanner] Google Vision error: {type(exc).__name__}: {exc}"
              + (f" | HTTP {http_status}" if http_status else "")
              + (f" | body: {http_body}"  if http_body   else ""),
              file=sys.stderr)
        return _OCR_IMAGE_ERROR


# ─────────────────────────────────────────────────────────────────────────────
# PITCH VIEW PARSER
# ─────────────────────────────────────────────────────────────────────────────

def _detect_view_type(blocks: list) -> str:
    """
    Distinguish Pitch from List view using landmark text.

    Pitch view: "Substitutes" text appears at y < 0.05 (very bottom of image,
                just above the bottom chrome bar).

    List view:  The squad tab uses named section headers — "Defenders",
                "Midfielders", "Forwards" — that never appear in the Pitch view.
                We look for any two of these three headers.  Standalone position
                abbreviations (GKP/DEF/MID/FWD) are a secondary signal; in the
                List View OCR they mostly appear embedded in combined "Team MID"
                tokens, so the original threshold of ≥ 3 standalone tags was
                almost never met.
    """
    section_headers = 0
    standalone_pos  = 0
    bench_labels    = 0   # "GKP", "1. FWD", "2. DEF" etc. in the bench-label band

    for b in blocks:
        t  = b["text"].strip()
        tl = t.lower()

        # Pitch View signal 1: "Substitutes" at the very bottom of the image
        if b["y"] < 0.05 and "substitutes" in tl:
            return VIEW_PITCH

        # Pitch View signal 2: bench role labels anywhere in the image.
        # "GKP", "1. FWD", "2. DEF", "3. DEF" are unique to the Pitch View
        # and never appear in the List View.  We search the whole image so
        # detection works even when the image is cropped or unusually sized.
        tu = t.upper()
        if tu == "GKP" or (t and t[0].isdigit() and "." in t and
                           any(p in tu for p in ("FWD", "DEF", "MID"))):
            bench_labels += 1

        # List View section headers (plural, never in Pitch View)
        if tl in {"defenders", "midfielders", "forwards"}:
            section_headers += 1

        # Standalone position tag (secondary signal)
        if t.upper() in {"GKP", "DEF", "MID", "FWD"}:
            standalone_pos += 1

    # Pitch View: bench labels are the most reliable cross-image signal
    if bench_labels >= 2:
        return VIEW_PITCH

    if section_headers >= 2 or standalone_pos >= 3:
        return VIEW_LIST

    return VIEW_UNKNOWN


# ── Pitch View helpers ────────────────────────────────────────────────────────

def _find_bench_labels(blocks: list) -> list:
    """
    Locate bench role labels anywhere in the image.
    Labels: standalone "GKP", or "N. FWD / N. DEF / N. MID" (N = digit).
    Returns list of {x, pos, y}.
    These are unique to the Pitch View and always present when the bench section
    is visible, regardless of image size, crop, or formation.
    """
    labels = []
    for b in blocks:
        t  = b["text"].strip()
        tu = t.upper()
        cx = b["x"] + b.get("w", 0) / 2

        if tu == "GKP":
            labels.append({"x": cx, "pos": 1, "y": b["y"]})
        elif t and t[0].isdigit() and "." in t:
            if   "FWD" in tu: labels.append({"x": cx, "pos": 4, "y": b["y"]})
            elif "DEF" in tu: labels.append({"x": cx, "pos": 2, "y": b["y"]})
            elif "MID" in tu: labels.append({"x": cx, "pos": 3, "y": b["y"]})
    return labels


def _nearest_player_block(badge: dict, player_blocks: list):
    """Return the player block whose centre is closest to the badge block."""
    if not player_blocks:
        return None
    bx = badge["x"] + badge.get("w", 0) / 2
    by = badge["y"] + badge.get("h", 0) / 2
    return min(
        player_blocks,
        key=lambda b: (b["x"] + b.get("w", 0) / 2 - bx) ** 2
                    + (b["y"] + b.get("h", 0) / 2 - by) ** 2,
    )


def _detect_captain_badges(blocks: list, player_blocks: list):
    """
    Detect captain ("C") and vice-captain ("VC") badges from OCR blocks.

    Handles two cases:
    1. Standalone badge blocks: text is exactly "C" or "VC" — associate with
       the nearest player name block by centre-to-centre Euclidean distance.
    2. Suffix on player name: handled upstream in _run_ocr_google_vision by
       stripping " C" / " VC" before blocks reach the parser.

    Returns (captain_name, vice_name) — either may be None.
    """
    captain_name: str | None = None
    vice_name:    str | None = None

    for b in blocks:
        t = b["text"].strip()
        if t == "C" and captain_name is None:
            nearest = _nearest_player_block(b, player_blocks)
            if nearest:
                captain_name = nearest["text"].strip()
        elif t == "VC" and vice_name is None:
            nearest = _nearest_player_block(b, player_blocks)
            if nearest:
                vice_name = nearest["text"].strip()

    return captain_name, vice_name


def _cluster_by_y(blocks: list, tolerance: float = 0.03) -> list:
    """
    Group blocks into horizontal clusters where consecutive items are within
    `tolerance` Y of the current cluster's running mean.
    Returns a list of lists (clusters), unsorted.
    """
    if not blocks:
        return []
    sorted_b = sorted(blocks, key=lambda b: b["y"])
    clusters  = [[sorted_b[0]]]
    for b in sorted_b[1:]:
        mean_y = sum(x["y"] for x in clusters[-1]) / len(clusters[-1])
        if abs(b["y"] - mean_y) <= tolerance:
            clusters[-1].append(b)
        else:
            clusters.append([b])
    return clusters


def _parse_pitch_view(blocks: list) -> dict:
    """
    Extract players from a Pitch View screenshot.

    Only three things come from the image:
      - Player names   (extracted by _extract_player_name)
      - Starting / bench  (above or below the bench-label separator)
      - Captain / vice-captain  (C/VC badge proximity)

    Position, team, fixtures — all resolved from the FPL bootstrap API
    after name matching, so we don't attempt to assign them here.
    """
    warnings = []

    # ── Locate bench-label separator ─────────────────────────────────────────
    bench_labels = _find_bench_labels(blocks)
    if bench_labels:
        separator_y = sum(lb["y"] for lb in bench_labels) / len(bench_labels)
    else:
        separator_y = 0.18
        warnings.append("Bench labels not visible; separator estimated at y=0.18")

    gap = _SEPARATOR_GAP

    # ── Collect player names, classify as starting or bench ──────────────────
    seen: set = set()
    players_out = []
    player_blocks = []   # for C/VC proximity detection

    for b in blocks:
        name = _extract_player_name(b["text"])   # strip fixture/stat suffix
        if not name or not _is_player_name(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        if b["y"] > separator_y + gap:
            is_starting = True
        elif b["y"] < separator_y - gap:
            is_starting = False
        else:
            continue
        seen.add(key)
        players_out.append({"name": name, "is_starting": is_starting})
        player_blocks.append(b)

    if not players_out:
        return _unsupported(
            "No players detected. Ensure the screenshot shows a complete FPL Pitch View."
        )

    captain_name, vice_name = _detect_captain_badges(blocks, player_blocks)

    result = _validate({"view_type": VIEW_PITCH, "players": players_out, "warnings": warnings})
    result["captain_name"]      = captain_name
    result["vice_captain_name"] = vice_name
    return result


# ─────────────────────────────────────────────────────────────────────────────
# LIST VIEW PARSER
# ─────────────────────────────────────────────────────────────────────────────

_LIST_SECTION_TO_POS = {
    "goalkeeper":  1,
    "defenders":   2,
    "midfielders": 3,
    "forwards":    4,
}
_LIST_TAG_TO_POS = {"GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}


def _pos_from_combined_token(text: str):
    """
    Extract position int from a combined 'Team Name POS' token, e.g.
    'Man City MID' → 3, 'Brighton GKP' → 1, 'Arsenal' → None.
    """
    last = text.strip().split()[-1].upper()
    return _LIST_TAG_TO_POS.get(last)


def _parse_list_view(blocks: list) -> dict:
    """
    Extract players from a List View screenshot.

    Only two things come from the image:
      - Player names  (above "Substitutes" header = starting, below = bench)
      - Captain / vice-captain  (C/VC badge proximity)

    Position comes from the FPL bootstrap after name resolution.
    """
    warnings           = []
    player_name_blocks = []
    sorted_blocks      = sorted(blocks, key=lambda b: -b["y"])   # top → bottom

    # Y-values of standalone position tags ("GKP", "DEF", "MID", "FWD").
    # When OCR splits "Arsenal DEF" into two blocks at the same y, the lone
    # team name sits at the same y as the tag — skip it to avoid false names.
    _split_pos_y = set()
    for b in sorted_blocks:
        if b["text"].strip().upper() in _LIST_TAG_TO_POS:
            _split_pos_y.add(round(b["y"] * 100))

    is_bench = False
    seen: set = set()
    players_out = []

    for b in sorted_blocks:
        t  = b["text"].strip()
        tl = t.lower()

        # Section headers — not players
        if tl in _LIST_SECTION_TO_POS:
            continue

        # "Substitutes" marks the bench boundary
        if tl.startswith("substitut"):
            is_bench = True
            continue

        # Skip combined team+position tokens ("Arsenal DEF", "Everton GKP")
        if _pos_from_combined_token(t) is not None:
            continue

        # Skip team name sitting at same y as a standalone position tag
        if round(b["y"] * 100) in _split_pos_y:
            continue

        name = _extract_player_name(t)
        if not name or not _is_player_name(name):
            continue
        if name.lower() in seen:
            continue

        seen.add(name.lower())
        players_out.append({"name": name, "is_starting": not is_bench})
        player_name_blocks.append(b)

    if not players_out:
        return _unsupported(
            "No players detected. Ensure the screenshot shows a complete FPL List View."
        )

    captain_name, vice_name = _detect_captain_badges(blocks, player_name_blocks)

    result = _validate({"view_type": VIEW_LIST, "players": players_out, "warnings": warnings})
    result["captain_name"]      = captain_name
    result["vice_captain_name"] = vice_name
    return result


# ─────────────────────────────────────────────────────────────────────────────
# IMAGE SCAN DISPATCH
# ─────────────────────────────────────────────────────────────────────────────

def _dispatch_blocks(blocks: list) -> dict:
    """Route a block list to the correct parser. Used by both _scan_image and tests."""
    import sys

    sample = [
        {"text": b["text"], "x": round(b["x"], 4), "y": round(b["y"], 4),
         "w": round(b.get("w", 0), 4), "h": round(b.get("h", 0), 4)}
        for b in sorted(blocks, key=lambda b: -b["y"])[:15]
    ]
    print(f"[diag] top15_blocks={sample}", file=sys.stderr)

    view_type = _detect_view_type(blocks)
    print(f"[diag] view_type={view_type}", file=sys.stderr)

    if view_type == VIEW_PITCH:
        result = _parse_pitch_view(blocks)
    elif view_type == VIEW_LIST:
        result = _parse_list_view(blocks)
    else:
        result = _unsupported(
            "Screenshot does not appear to be an FPL Pitch View or List View. "
            "Use the FPL app squad tab (Pitch or List)."
        )

    print(
        f"[diag] scanner_status={result['status']}  "
        f"players={len(result.get('players', []))}  "
        f"msg={result.get('message', '')[:120]}",
        file=sys.stderr,
    )
    return result


def _scan_image(image_bytes: bytes) -> dict:
    import sys

    blocks = _run_ocr(image_bytes)
    if blocks is _OCR_UNAVAILABLE:
        result = _unsupported("OCR unavailable — set GOOGLE_VISION_API_KEY.")
        print(f"[diag] scanner_status={result['status']} reason=ocr_unavailable", file=sys.stderr)
        return result
    if blocks is _OCR_IMAGE_ERROR or not blocks:
        result = _unsupported(
            "Image could not be decoded or contains no recognisable text."
        )
        print(f"[diag] scanner_status={result['status']} reason=ocr_error", file=sys.stderr)
        return result

    print(f"[diag] google_vision_blocks={len(blocks)}", file=sys.stderr)
    return _dispatch_blocks(blocks)


# ─────────────────────────────────────────────────────────────────────────────
# STRUCTURED DICT VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def _validate(data: dict) -> dict:
    """
    Validate a pre-structured scan result dict.

    Expected dict shape::

        {
            "view_type": "PITCH" | "LIST" | "UNKNOWN",   # optional
            "players": [
                {
                    "name":        str,
                    "position":    int,   # 1=GKP 2=DEF 3=MID 4=FWD
                    "is_starting": bool,
                },
                ...
            ]
        }
    """
    view_type   = data.get("view_type", VIEW_UNKNOWN)
    raw_players = data.get("players", [])
    warnings    = list(data.get("warnings", []))

    valid_players = []
    for i, p in enumerate(raw_players):
        label = p.get("name", "player %d" % i)
        if not isinstance(p.get("name"), str) or not p["name"].strip():
            warnings.append("Skipped player %d: missing name" % i)
            continue
        if not isinstance(p.get("is_starting"), bool):
            warnings.append("Skipped %r: is_starting must be bool" % label)
            continue
        # Position is optional for image scans (resolved from bootstrap later).
        # If explicitly provided (demo/structured path) it must be valid.
        pos = p.get("position")
        if pos is not None and pos not in _VALID_POSITIONS:
            warnings.append("Skipped %r: invalid position %r" % (label, pos))
            continue
        player: dict = {"name": p["name"].strip(), "is_starting": p["is_starting"]}
        if pos is not None:
            player["position"] = pos
        valid_players.append(player)

    total    = len(valid_players)
    starters = sum(1 for p in valid_players if p["is_starting"])

    if total < _SQUAD_SIZE:
        return _result(PARTIAL, view_type, valid_players, warnings,
                       "Only %d/%d players found" % (total, _SQUAD_SIZE))

    if starters < _STARTER_SIZE:
        return _result(PARTIAL, view_type, valid_players, warnings,
                       "Only %d starters found, expected %d" % (starters, _STARTER_SIZE))

    names_lower = [p["name"].lower() for p in valid_players]
    if len(names_lower) != len(set(names_lower)):
        dupes = [n for n in set(names_lower) if names_lower.count(n) > 1]
        return _result(AMBIGUOUS, view_type, valid_players, warnings,
                       "Duplicate names: %s" % ", ".join(dupes))

    return _result(VALID, view_type, valid_players, warnings,
                   "Squad validated: %d players, %d starters" % (total, starters))


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def scan_squad(source) -> dict:
    """
    Main entry point.

    Parameters
    ----------
    source : bytes | bytearray | memoryview | dict
        Image data  → OCR via Apple Vision, then layout parser.
        Structured dict → validated directly (test / demo path).

    Returns
    -------
    ScanResult dict with keys: status, view_type, players, message, warnings.
    """
    if isinstance(source, (bytes, bytearray, memoryview)):
        return _scan_image(bytes(source))
    if isinstance(source, dict):
        return _validate(source)
    return _unsupported("Unsupported input type: %s" % type(source).__name__)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _result(status, view_type, players, warnings, message):
    return {
        "status":    status,
        "view_type": view_type,
        "players":   players,
        "message":   message,
        "warnings":  warnings,
    }


def _unsupported(message):
    return _result(UNSUPPORTED, VIEW_UNKNOWN, [], [], message)
