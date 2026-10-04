"""PostHog HogQL query client — read-only analytics access.

Credentials required (server-side environment variables only):
  POSTHOG_PERSONAL_API_KEY  — Personal API key (phx_...) from PostHog → Settings → Personal API Keys
  POSTHOG_PROJECT_ID        — Numeric project ID from PostHog → Settings → Project
"""

import json
import os
import urllib.request
import urllib.error

_API_HOST = "https://eu.posthog.com"


def run_query(hogql: str) -> dict:
    """POST a HogQL query to PostHog and return the raw response dict."""
    key = os.environ.get("POSTHOG_PERSONAL_API_KEY", "").strip()
    project_id = os.environ.get("POSTHOG_PROJECT_ID", "").strip()
    if not key or not project_id:
        raise EnvironmentError(
            "POSTHOG_PERSONAL_API_KEY and POSTHOG_PROJECT_ID must be set"
        )
    url = f"{_API_HOST}/api/projects/{project_id}/query/"
    payload = json.dumps({
        "query": {"kind": "HogQLQuery", "query": hogql}
    }).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"PostHog query failed ({e.code}): {body}") from e


def rows_to_dicts(response: dict) -> list:
    """Convert a PostHog query response into a list of dicts keyed by column name."""
    # HogQL returns plain column-name strings; tolerate {"name": ...} dicts too
    cols = [c["name"] if isinstance(c, dict) else c for c in response.get("columns", [])]
    return [dict(zip(cols, row)) for row in response.get("results", [])]
