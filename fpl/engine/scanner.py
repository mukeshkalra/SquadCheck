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
# ── FPL stat-suffix stripping (Google Vision paragraph-level fix) ─────────────
# Google Vision merges a player's name with the stat shown below it in the
# FPL app into one paragraph.  The FPL Pitch View can display:
#   points (8), form (5.0), price (£5.5m), price change (+0.1 / -0.1),
#   ownership (31.5%), opponent (vs SUN / vs LIV (H)), or just a team code.
# None of these patterns can appear at the end of a real player name.
# The regex is applied iteratively so combinations ("Pickford 8 (H)") work.
_FPL_STAT_SUFFIX = re.compile(
    r'\s+(?:'
    r'\d{1,3}'                              # integer score:    8, 12, 0
    r'|\d+\.\d+[%m]?'                       # decimal stat:     5.0  31.5%  5.5m
    r'|[+\-]\d+\.?\d*'                      # price change:     +0.1  -0.2
    r'|£\d+[\d.]*m?'                        # explicit price:   £5.5m  £5.5
    r'|\(H\)|\(A\)'                         # home/away marker: (H)  (A)
    r'|vs\s+[A-Z]{2,4}(?:\s+\([HA]\))?'    # vs OPP:           vs SUN  vs LIV (H)
    r'|[A-Z]{2,4}(?:\s+\([HA]\))?'         # bare team code:   SUN  MCI (A)
    r')$',
    re.IGNORECASE,
)


def _strip_fpl_stat(text: str) -> str:
    """
    Remove any FPL stat token(s) appended to a player name by Google Vision.

    Applied iteratively so multi-token suffixes resolve correctly:
        "Pickford vs SUN (H)"  →  "Pickford"   (2 iterations not needed here,
        "Haaland 12 (A)"       →  "Haaland"     but iterating is safe)
    """
    prev = None
    while text != prev:
        prev  = text
        text  = _FPL_STAT_SUFFIX.sub("", text).strip()
    return text


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
    if t[0].isdigit():
        return False

    # List View section headers — never player names
    if t.lower() in {"goalkeeper", "defenders", "midfielders", "forwards",
                     "substitutes", "substitute", "player"}:
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

    resp  = data.get("responses", [{}])[0]
    pages = resp.get("fullTextAnnotation", {}).get("pages", [])

    if not pages:
        return _OCR_IMAGE_ERROR

    img_w = pages[0].get("width",  0)
    img_h = pages[0].get("height", 0)
    if not img_w or not img_h:
        return _OCR_IMAGE_ERROR

    def _word_text(word: dict) -> str:
        """Concatenate symbol texts within one word (preserves hyphens, accents)."""
        return "".join(sym.get("text", "") for sym in word.get("symbols", []))

    def _para_coords(para: dict):
        """
        Return (x_left, y_bottom, width, height) in normalised Apple Vision coords,
        or None if the paragraph has no usable bounding box.
        """
        verts = para.get("boundingBox", {}).get("vertices", [])
        if not verts:
            return None
        xs = [v.get("x", 0) for v in verts]
        ys = [v.get("y", 0) for v in verts]
        x_min, x_max = min(xs) / img_w, max(xs) / img_w
        y_min, y_max = min(ys) / img_h, max(ys) / img_h
        # y_max is the visual bottom of the text in pixel coords (largest y).
        # After flipping it becomes the Apple Vision bottom edge (y=0 at bottom).
        return (
            x_min,          # left edge
            1.0 - y_max,    # bottom edge in Apple Vision convention
            x_max - x_min,  # width
            y_max - y_min,  # height
        )

    blocks = []
    for page in pages:
        for block in page.get("blocks", []):
            for para in block.get("paragraphs", []):
                # Reconstruct line text: join words separated by spaces.
                # Each word is formed by concatenating its symbol characters.
                words = [_word_text(w) for w in para.get("words", [])]
                text  = " ".join(words).strip()

                # Google Vision merges the player name with whatever stat is
                # shown below it on the FPL Pitch View card (points, opponent,
                # form, price, ownership, price change, home/away marker).
                # Strip those suffixes so only the player name remains.
                text = _strip_fpl_stat(text)

                if not text:
                    continue

                coords = _para_coords(para)
                if coords is None:
                    continue

                x_left, y_bottom, width, height = coords
                blocks.append({
                    "text": text,
                    "conf": 1.0,
                    "x":   x_left,    # left edge  — parsers add w/2 to get centre
                    "y":   y_bottom,  # bottom edge — matches Apple Vision b.origin.y
                    "w":   width,
                    "h":   height,
                })

    return blocks   # [] if image contains no text


# ── Dispatcher: Swift on macOS, Google Vision on Linux/cloud ─────────────────

def _run_ocr(image_bytes: bytes):
    """
    Select OCR backend automatically:
      - macOS dev:  Swift binary (fast, free, high accuracy)
      - Cloud/Linux: Google Cloud Vision REST API (requires GOOGLE_VISION_API_KEY)

    Returns list[dict] | _OCR_UNAVAILABLE | _OCR_IMAGE_ERROR.
    """
    import sys

    # ── Diagnostic 1 & 2 & 3 ─────────────────────────────────────────────────
    api_key    = os.environ.get("GOOGLE_VISION_API_KEY", "")
    key_present = bool(api_key)
    swift_avail = _BIN.exists()
    backend     = "swift" if swift_avail else ("google_vision" if key_present else "none")
    print(
        f"[diag] image_bytes={len(image_bytes)}  "
        f"api_key_present={key_present}  "
        f"backend={backend}",
        file=sys.stderr,
    )

    # Swift binary is only present on macOS (excluded from git via .gitignore)
    if swift_avail:
        return _run_ocr_swift(image_bytes)

    # Cloud path — requires env var set in Vercel (or locally for testing)
    if not key_present:
        return _OCR_UNAVAILABLE

    try:
        result = _run_ocr_google_vision(image_bytes, api_key)

        # ── Diagnostic 4 ─────────────────────────────────────────────────────
        if isinstance(result, list):
            print(f"[diag] google_vision_blocks={len(result)}", file=sys.stderr)
        else:
            sentinel = "_OCR_IMAGE_ERROR" if result is _OCR_IMAGE_ERROR else "_OCR_UNAVAILABLE"
            print(f"[diag] google_vision_result={sentinel}", file=sys.stderr)

        return result

    except Exception as exc:
        http_status = None
        http_body   = None
        if hasattr(exc, "code"):           # urllib.error.HTTPError
            http_status = exc.code
            try:
                http_body = exc.read(512).decode("utf-8", errors="replace")
            except Exception:
                pass
        print(
            f"[scanner] Google Vision error: {type(exc).__name__}: {exc}"
            + (f" | HTTP {http_status}" if http_status else "")
            + (f" | body: {http_body}"  if http_body   else ""),
            file=sys.stderr,
        )
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
    Extract 15 players from a Pitch View OCR block list.

    Adaptive strategy — no hardcoded Y-bands:

    1.  Find bench labels ("GKP", "1. FWD", "2. DEF", …) anywhere in the image.
        Their median Y becomes the separator between starters and bench.
        If none found, fall back to estimating the separator from image structure.

    2.  Collect candidate player names above the separator → starters.
        Collect candidate player names below the separator → bench.

    3.  Cluster starters by Y proximity (tolerance ±0.03).
        Sort clusters top-to-bottom.
        Assign positions by order: cluster 0 = GKP, 1 = DEF, 2 = MID, 3 = FWD.
        (In any FPL Pitch View the rows always appear in this top-to-bottom order
        regardless of formation or image dimensions.)

    4.  Assign bench player positions by X-proximity to the bench labels.

    This works for any formation (3-4-3, 4-4-2, 5-3-2, …), any image size,
    and partially cropped screenshots.
    """
    warnings = []

    # ── Step 1: Locate bench labels as separator anchor ───────────────────────
    bench_labels = _find_bench_labels(blocks)

    if bench_labels:
        separator_y = sum(lb["y"] for lb in bench_labels) / len(bench_labels)
    else:
        # Bench section not visible (cropped?). Estimate from image structure:
        # name blocks near the bottom tend to be bench.
        name_ys = sorted(b["y"] for b in blocks if _is_player_name(b["text"]))
        if name_ys and name_ys[0] < 0.20:
            # There are name-like blocks near the bottom — use 0.18 as separator
            separator_y = 0.18
        else:
            separator_y = 0.15
        warnings.append(
            "Bench labels not visible; estimated separator at y=%.2f" % separator_y
        )

    gap = _SEPARATOR_GAP   # exclusion zone around the separator row

    # ── Step 2: Split candidate names into starter / bench areas ─────────────
    starter_names = [
        b for b in blocks
        if b["y"] > separator_y + gap and _is_player_name(b["text"])
    ]
    bench_names = [
        b for b in blocks
        if b["y"] < separator_y - gap and _is_player_name(b["text"])
    ]

    # ── Step 3: Cluster starters into position rows ───────────────────────────
    clusters = _cluster_by_y(starter_names, tolerance=_CLUSTER_TOLERANCE)
    # Sort top-to-bottom (highest Vision Y = top of image)
    clusters.sort(key=lambda c: -(sum(b["y"] for b in c) / len(c)))

    # If >4 clusters (kit/logo fragments created extra), slide a window of 4
    # and pick the window whose total player count is closest to 11 starters.
    if len(clusters) > 4:
        best_start, best_diff = 0, 9999
        for s in range(len(clusters) - 3):
            diff = abs(sum(len(c) for c in clusters[s:s + 4]) - 11)
            if diff < best_diff:
                best_diff, best_start = diff, s
        clusters = clusters[best_start:best_start + 4]

    # Assign positions by cluster order (always GKP→DEF→MID→FWD top-to-bottom)
    POS_ORDER = [1, 2, 3, 4]
    starters  = []
    for cluster, pos in zip(clusters, POS_ORDER):
        for b in cluster:
            starters.append({
                "name":        b["text"].strip(),
                "position":    pos,
                "is_starting": True,
            })

    # ── Step 4: Bench players — position from nearest label by X ─────────────
    bench_players = []
    for b in bench_names:
        bx = b["x"] + b.get("w", 0) / 2
        if bench_labels:
            nearest = min(bench_labels, key=lambda lb: abs(lb["x"] - bx))
            pos = nearest["pos"]
        else:
            pos = 2   # fallback: DEF
        bench_players.append({
            "name":        b["text"].strip(),
            "position":    pos,
            "is_starting": False,
        })

    players_out = [
        {"name": p["name"], "position": p["position"], "is_starting": p["is_starting"]}
        for p in starters + bench_players
    ]

    if not players_out:
        return _unsupported(
            "No players detected. Ensure the screenshot shows a complete FPL Pitch View."
        )

    return _validate({
        "view_type": VIEW_PITCH,
        "players":   players_out,
        "warnings":  warnings,
    })


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
    Extract 15 players from a List View OCR block list.

    The FPL List View structure (top → bottom, Vision y decreasing):

        Goalkeeper                        ← section header  → pos=1
          Pickford                        ← player name
          Everton GKP                     ← team+pos token (skip as name)
        Defenders                         ← section header  → pos=2
          Calafiori
          Arsenal DEF
          Diop
          Ipswich Town DEF
          …
        Midfielders                       → pos=3
          …
        Forwards                          → pos=4
          …
        Substitutes                       ← bench separator
          Verbruggen
          Brighton GKP                    ← pos=1 for this bench slot
          …

    Strategy
    --------
    1. Walk blocks top-to-bottom (y descending).
    2. When a section header ("Goalkeeper", "Defenders", …) is seen, update
       current_pos.
    3. When "Substitutes" is seen, switch to bench mode (is_bench = True).
    4. For starters: any _is_player_name block inherits current_pos.
    5. For bench players: position comes from the team+pos combined token
       that appears immediately below the player name in the same row.
    """
    warnings = []
    sorted_blocks = sorted(blocks, key=lambda b: -b["y"])   # top → bottom

    # When OCR splits "Arsenal DEF" into two separate blocks at the same Y-level,
    # the team-name token ("Arsenal") appears at the same y as the standalone "DEF".
    # Collect those Y-values so we can skip the accompanying team-name token.
    _split_pos_y = set()
    for b in sorted_blocks:
        if b["text"].strip().upper() in _LIST_TAG_TO_POS:
            _split_pos_y.add(round(b["y"] * 100))

    current_pos = None
    is_bench    = False
    players_out = []

    for i, b in enumerate(sorted_blocks):
        t  = b["text"].strip()
        tl = t.lower()

        # ── Section header → update position context ──────────────────────────
        if tl in _LIST_SECTION_TO_POS:
            current_pos = _LIST_SECTION_TO_POS[tl]
            continue

        # ── Substitutes header → switch to bench mode ─────────────────────────
        if "substitutes" in tl and len(t) <= 12:   # avoid matching player names
            is_bench    = True
            current_pos = None   # position determined per-player from team+pos token
            continue

        # ── Skip non-player tokens (numbers, sponsors, team+pos, section words) ──
        if not _is_player_name(t):
            continue

        # ── Skip split team-name companions (e.g. "Arsenal" next to "DEF") ──
        # When OCR splits "Arsenal DEF" into two blocks at the same y-level,
        # the lone team name has no position suffix and appears at the same Y
        # as a standalone position tag.  Drop it.
        if round(b["y"] * 100) in _split_pos_y:
            continue

        # ── Determine position ────────────────────────────────────────────────
        if not is_bench:
            # Starter: inherits the active section context
            pos = current_pos
        else:
            # Bench: find the team+pos token just below this player name
            # It appears a small distance lower (y decreases downward).
            pos = None
            player_y = b["y"]
            for below in sorted_blocks:
                dy = player_y - below["y"]
                if dy < 0.005 or dy > 0.06:     # must be 0.005–0.06 below
                    continue
                if abs(below["x"] - b["x"]) > 0.15:   # must be in same column
                    continue
                pos = _pos_from_combined_token(below["text"])
                if pos is not None:
                    break

            if pos is None:
                warnings.append("Could not determine position for bench player %r" % t)
                pos = 2   # safe fallback: DEF

        if pos is None:
            warnings.append("Player %r found outside any position section; skipped" % t)
            continue

        players_out.append({
            "name":        t,
            "position":    pos,
            "is_starting": not is_bench,
        })

    if not players_out:
        return _unsupported(
            "No players detected. Ensure the screenshot shows a complete FPL List View."
        )

    return _validate({
        "view_type": VIEW_LIST,
        "players":   players_out,
        "warnings":  warnings,
    })


# ─────────────────────────────────────────────────────────────────────────────
# IMAGE SCAN DISPATCH
# ─────────────────────────────────────────────────────────────────────────────

def _scan_image(image_bytes: bytes) -> dict:
    """
    Entry point for image bytes.  Runs OCR then dispatches to the appropriate
    layout parser.  Returns UNSUPPORTED if OCR is unavailable.
    """
    import sys

    blocks = _run_ocr(image_bytes)
    if blocks is _OCR_UNAVAILABLE:
        result = _unsupported(
            "OCR unavailable. Requires macOS with Xcode Command Line Tools "
            "(swiftc must be on PATH)."
        )
        print(f"[diag] scanner_status={result['status']} reason=ocr_unavailable", file=sys.stderr)
        return result
    if blocks is _OCR_IMAGE_ERROR or not blocks:
        result = _unsupported(
            "Image could not be decoded or contains no recognisable text. "
            "Ensure the screenshot is a valid JPEG or PNG from the FPL app."
        )
        print(f"[diag] scanner_status={result['status']} reason=ocr_image_error_or_empty", file=sys.stderr)
        return result

    # ── Diagnostic 5: first 15 normalised blocks ─────────────────────────────
    sample = [
        {"text": b["text"], "x": round(b["x"], 4), "y": round(b["y"], 4),
         "w": round(b.get("w", 0), 4), "h": round(b.get("h", 0), 4)}
        for b in sorted(blocks, key=lambda b: -b["y"])[:15]
    ]
    print(f"[diag] top15_blocks={sample}", file=sys.stderr)

    # ── Diagnostic 6: view detection ─────────────────────────────────────────
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

    # ── Diagnostic 7: final scanner status ───────────────────────────────────
    print(
        f"[diag] scanner_status={result['status']}  "
        f"players={len(result.get('players', []))}  "
        f"msg={result.get('message', '')[:120]}",
        file=sys.stderr,
    )
    return result


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
        if p.get("position") not in _VALID_POSITIONS:
            warnings.append("Skipped %r: invalid position %r" % (label, p.get("position")))
            continue
        if not isinstance(p.get("is_starting"), bool):
            warnings.append("Skipped %r: is_starting must be bool" % label)
            continue
        valid_players.append({
            "name":        p["name"].strip(),
            "position":    p["position"],
            "is_starting": p["is_starting"],
        })

    total    = len(valid_players)
    starters = sum(1 for p in valid_players if p["is_starting"])

    if total < _SQUAD_SIZE:
        return _result(PARTIAL, view_type, valid_players, warnings,
                       "Only %d/%d players found" % (total, _SQUAD_SIZE))

    if starters != _STARTER_SIZE:
        return _result(PARTIAL, view_type, valid_players, warnings,
                       "%d starters found, expected %d" % (starters, _STARTER_SIZE))

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
