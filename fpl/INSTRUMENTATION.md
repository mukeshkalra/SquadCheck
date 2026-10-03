# SquadCheck Instrumentation

Analytics provider: **PostHog** (EU endpoint — `eu.i.posthog.com`)  
Tracking file: `fpl/index.html` — replace `__POSTHOG_KEY__` with your PostHog project API key.  
Fallback: when the key is the placeholder string, all events log to `console.log('[SC]', …)`.

---

## Super Properties

Attached to **every** event automatically via `posthog.register()` and merged manually in the `track()` wrapper.

| Property | Type | Notes |
|---|---|---|
| `session_id` | string | UUID, per browser-tab session (`sessionStorage`) |
| `is_return` | boolean | `true` if user has loaded the app before (keyed by `localStorage`) |
| `sc_version` | string | Schema version, currently `'2.0'` |
| `utm_source` | string? | From URL `?utm_source=` — persisted to `sessionStorage` for the session |
| `utm_medium` | string? | From URL `?utm_medium=` |
| `utm_campaign` | string? | From URL `?utm_campaign=` |
| `utm_content` | string? | From URL `?utm_content=` |
| `utm_term` | string? | From URL `?utm_term=` |
| `experiment_id` | string? | From URL `?experiment_id=` — for A/B test attribution |
| `creative_id` | string? | From URL `?creative_id=` — for ad creative attribution |
| `gw` | number? | Gameweek — registered as super property after first `scan_valid` |

---

## Event Schema

### Funnel order

```
page_viewed
  └─ screenshot_uploaded
       ├─ scan_ambiguous → disambig_resolved → projection_viewed → decision_reached
       ├─ scan_failed
       └─ scan_valid → projection_viewed → decision_reached
```

---

### `page_viewed`
Fires once at script load, after super properties are registered.

| Property | Type | Notes |
|---|---|---|
| `referrer` | string\|null | `document.referrer`, empty string → `null` |
| *(super props)* | | `is_return`, `utm_*`, `session_id` |

**Use for:** unique visitor counts, acquisition channel breakdown, return-visit rate.

---

### `screenshot_uploaded`
Fires when the user selects a file (file-picker change event = upload initiated + submitted in one step).

| Property | Type | Notes |
|---|---|---|
| `file_size_kb` | number | Rounded to nearest KB |
| `file_type` | string | MIME type, e.g. `image/jpeg` |

**Known gap:** "upload initiated" (user tapped the button) and "upload submitted" (file chosen) are not distinguishable — the file-picker opens synchronously so there is no separate prior event.

---

### `scan_ambiguous`
Fires when the API returns `status: DISAMBIG` — player names are ambiguous and need user resolution.

| Property | Type | Notes |
|---|---|---|
| `count` | number | Number of ambiguous player name groups |

---

### `scan_failed`
Fires when the API returns a non-OK, non-DISAMBIG status, or when a JS/network error occurs in the upload flow.

| Property | Type | Notes |
|---|---|---|
| `status` | string | `SCAN_FAIL` / `PARTIAL` / `UNSUPPORTED` / `DUPLICATE_NAMES` / `APP_ERROR` / `default` |
| `message` | string | Human-readable error message from API or `err.message` |

**Reliability note:** `APP_ERROR` fires from the JS catch block — only covers errors that throw (network down, JSON parse fail, render crash). Silent server 4xx/5xx errors that return valid JSON are captured with the API's `status` field instead.

---

### `scan_valid`
Fires when OCR and pipeline succeed and a result is ready to render.

| Property | Type | Notes |
|---|---|---|
| `gw` | number | Gameweek number |
| `player_count` | number | Total players in payload (expect 15) |
| `view_type` | string\|null | `PITCH` / `LIST` / `PARTIAL` / `UNKNOWN` |
| `captain_name` | string\|null | `web_name` of detected captain |

---

### `scan_ambiguous` → `disambig_resolved`
Fires when the user confirms their player choices on the disambiguation screen.

| Property | Type | Notes |
|---|---|---|
| `choices` | number | Number of name groups resolved |

---

### `projection_viewed`
Fires when the result screen is shown. Also fires after disambiguation resolves (with `via_disambig: true`).

| Property | Type | Notes |
|---|---|---|
| `gw` | number | Gameweek |
| `xPts` | number | Projected xPts for the submitted XI (before captain bonus) |
| `action` | string | Pipeline recommendation: `HOLD` or `SWAP` |
| `view_type` | string\|null | Screenshot view type |
| `captain_name` | string\|null | Captain's `web_name` |
| `captain_xpts` | number\|null | Captain's xPts |
| `via_disambig` | boolean? | `true` only on the disambiguation path |

---

### `decision_reached`
Fires immediately after `projection_viewed`. Records the pipeline's recommendation — this is the app's decision, not the user's (user decision is captured in `action_*_xi`).

| Property | Type | Notes |
|---|---|---|
| `gw` | number | Gameweek |
| `action` | string | `HOLD` or `SWAP` |
| `xPts` | number | Submitted XI xPts |
| `delta` | number\|null | Bench-swap xPts gain (`SWAP` only); `null` for `HOLD` |

---

### Engagement events

| Event | When | Key Properties |
|---|---|---|
| `position_section_viewed` | User scrolls a position row into view | `position` (GK/DEF/MID/FWD), `xPts` |
| `player_card_opened` | Tap on a horizontal player card | `player_id` |
| `player_opened` | Old vertical card open (may be dead code) | `player_id` |
| `player_explanation_opened` | "Why?" expanded | `player_id` |
| `player_numbers_opened` | "Full numbers" expanded | `player_id` |
| `bench_viewed` | Bench section expanded | — |
| `captain_switch_shown` | App suggests captain change | `from` (player_id), `to` (player_id), `gain` (xPts delta) |
| `captain_card_viewed` | Captain card rendered (no switch needed) | `player_id`, `xPts` |
| `recommendation_viewed` | "Changes to make" section scrolled into view (IntersectionObserver, 30% threshold) | `action`, `delta` |
| `recommended_change_viewed` | Final Call section rendered | `action`, `delta` |

---

### Decision / feedback events

| Event | When | Key Properties |
|---|---|---|
| `feedback_loved` / `_useful` / `_ok` / `_bad` | User taps a rating button | `rating` (same as suffix), `gw`, `pipeline_action` |
| `action_changed_xi` / `_kept_xi` / `_deciding_xi` | User taps "What did you do?" | `user_action`, `pipeline_action`, `gw`, `delta` |
| `feedback_reason` | Reason checkbox ticked | `reason` (`explanation`/`wrong`/`captain`/`transfer`), `gw` |

**Key cross-reference:** join `action_*_xi.pipeline_action` with `action_*_xi.user_action` to measure how often users follow the app's recommendation.

---

### Share events

| Event | When |
|---|---|
| `share_native_image` | Web Share API triggered with image |
| `share_native` | Web Share API triggered without image |
| `share_download` | Image downloaded (fallback) |

---

### Utility events

| Event | When | Key Properties |
|---|---|---|
| `add_to_home_screen` | A2HS prompt answered | `outcome` (`accepted`/`dismissed`) |

---

## Funnel Queries (PostHog)

Build these funnels in PostHog → Funnels:

**Core conversion funnel:**
```
page_viewed → screenshot_uploaded → scan_valid → projection_viewed → action_changed_xi OR action_kept_xi
```

**Recommendation follow-through:**
```
decision_reached (action=SWAP) → action_changed_xi
decision_reached (action=HOLD) → action_kept_xi
```

**Engagement depth:**
```
projection_viewed → player_card_opened → recommendation_viewed → feedback_*
```

---

## Known Gaps & Reliability Notes

| Gap | Severity | Notes |
|---|---|---|
| No `upload_started` distinct from `screenshot_uploaded` | Low | File picker opens synchronously — no gap between tap and file-chosen |
| `player_opened` may be dead code | Low | Kept for backwards compat; verify in PostHog after launch |
| `position_section_viewed` fires on scroll, not on first render | Medium | May under-count if user doesn't scroll; captures genuine attention |
| No explicit `scan_partial` event | Low | Covered by `scan_failed` with `status: PARTIAL` |
| No server-side events | Medium | All events are client-side; server crashes or timeouts only appear as `APP_ERROR` in scan_failed |
| Return visit detection is device/browser-scoped | Medium | Clearing localStorage resets `is_return` to false; incognito always shows `is_return: false` |
| No user identity | Low | Anonymous sessions only — deliberate. Add `posthog.identify()` if auth is added. |

---

## Setup

1. Create a PostHog project at [posthog.com](https://posthog.com) — EU cloud recommended for GDPR.
2. Copy your project API key (write-only, safe to commit).
3. In `fpl/index.html`, replace `__POSTHOG_KEY__` with your key.
4. Deploy. Verify in PostHog Live Events that `page_viewed` appears.
5. Build the funnel queries above in PostHog → Funnels.
