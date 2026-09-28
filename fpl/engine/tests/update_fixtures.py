"""
Update Google Vision OCR fixtures for screenshot tests.

Run this whenever test images change or you want to refresh fixtures:

    GOOGLE_VISION_API_KEY=<key> python3 fpl/engine/tests/update_fixtures.py

Saves raw GV responses to fpl/test_images/fixtures/<stem>.json.
Tests load these fixtures instead of calling the API, so they run
offline and reflect real GV behaviour (not Apple Vision).
"""

import json
import os
import sys
import pathlib
import base64
import urllib.request

_IMG_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "test_images"
_FIX_DIR = _IMG_DIR / "fixtures"
_GV_URL  = "https://vision.googleapis.com/v1/images:annotate?key={key}"

IMAGES = [
    "V1 Test Pitch View.jpg",
    "fpl test prod 2.jpg",
    "list-view-example.png",
    "pitch-view-example.jpg",
    "screenshot fpl team .jpg",
    "fpl pitch 3.jpeg",
]


def _call_gv(image_bytes: bytes, api_key: str) -> dict:
    payload = json.dumps({
        "requests": [{
            "image":    {"content": base64.b64encode(image_bytes).decode()},
            "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
        }]
    }).encode()
    req = urllib.request.Request(
        _GV_URL.format(key=api_key),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    api_key = os.environ.get("GOOGLE_VISION_API_KEY", "").strip().strip("‘’“”'\"")
    if not api_key:
        print("ERROR: GOOGLE_VISION_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    _FIX_DIR.mkdir(exist_ok=True)

    for name in IMAGES:
        img = _IMG_DIR / name
        if not img.exists():
            print(f"  SKIP  {name} (not found)")
            continue
        fix = _FIX_DIR / (img.stem + ".json")
        print(f"  CALL  {name} ...", end="", flush=True)
        resp = _call_gv(img.read_bytes(), api_key)
        fix.write_text(json.dumps(resp, ensure_ascii=False, indent=2))
        blocks = len(resp.get("responses", [{}])[0]
                     .get("fullTextAnnotation", {})
                     .get("pages", [{}])[0]
                     .get("blocks", []))
        print(f" saved ({blocks} blocks) → {fix.name}")

    print("Done.")


if __name__ == "__main__":
    main()
