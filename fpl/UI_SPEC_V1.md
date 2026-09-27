# SquadCheck V1 — Mobile UI/UX Specification
**Status: Design review draft · Not implemented**

---

## 1. Product principle

The experience is a weekly story about the user's team — not a tool they operate.

The emotional order is:

> "How does my team look?" → "Tell me about each player" → "What should I do?"

This means:
- **The projected team score comes first**, not the recommendation.
- The recommendation comes last, after the user has been shown the evidence.
- HOLD is a successful, confident outcome. It should feel like reassurance, not failure.
- No model terminology visible at the surface level ("xPts" appears as "projected points"; "lambda" never appears).
- Every statement is traceable to actual squad/projection data. No generic claims.

---

## 2. Screen hierarchy

```
App entry
│
├── UPLOAD FLOW
│   ├── S0: Upload prompt (existing fpl/index.html)
│   ├── S0a: Loading / Scanning
│   ├── S0b: Scan failed (UNSUPPORTED / PARTIAL / AMBIGUOUS)
│   └── S0c: Projecting (spinner between scan ✓ and payload ready)
│
└── RESULT FEED (single vertical scroll)
    ├── S1: Gameweek Hero
    ├── S2: Your Players (goalkeeper → defence → midfield → forwards → bench)
    │   └── S2p: Player detail panel (progressive disclosure, 3 levels)
    ├── S3: What's Awesome
    ├── S4: What Needs Attention
    │   └── S4a: SWAP variant (recommendation with full explanation)
    │   └── S4b: HOLD variant ("Nothing needs changing")
    ├── S5: Final Call
    │   ├── S5a: HOLD card
    │   └── S5b: CHANGE card
    └── S6: Feedback
```

---

## 3. Visual language

**Palette**

| Token | Value | Role |
|---|---|---|
| Lime | `#CCFF00` | Primary accent, positive numbers, HIGH confidence |
| Dark | `#0A0A0C` | Background |
| Dark-2 | `#111114` | Card surfaces |
| Dark-3 | `#1A1A1F` | Nested sections |
| White | `#F0F0F2` | Body text |
| Gray | `#8A8A96` | Secondary text, labels |
| Amber | `#FFB547` | MEDIUM confidence |
| Coral | `#FF6B4A` | LOW confidence, risk indicators |
| Border | `rgba(240,240,242,0.08)` | Card edges |

**Typography** — Outfit (matches existing brand)

| Use | Weight | Size |
|---|---|---|
| Hero number | 900 | 64–72px |
| Section headline | 800 | 22px |
| Player name | 700 | 16px |
| Body / narrative | 500 | 15px |
| Labels / caps | 700 | 11px, 0.07em tracking |

**Confidence badge colours**

```
HIGH    → lime background, black text
MEDIUM  → amber background, black text
LOW     → coral background, black text
```

---

## 4. Wireframes

All wireframes are shown at 375px width (iPhone SE baseline). Content adapts upward.

---

### S0 — Upload prompt (existing screen, minor addition)

The current `fpl/index.html` is the entry point. The only required addition is an analytics event on screenshot selection.

```
┌────────────────────────────────────────┐
│  SquadCheck                      [FPL] │
├────────────────────────────────────────┤
│                                        │
│  [pill: FANTASY PREMIER LEAGUE · GW6]  │
│                                        │
│  Is your team                          │
│  actually any good?                    │
│                                        │
│  See what your squad is projecting     │
│  this Gameweek — and find out what     │
│  actually needs fixing.                │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │  ↑  Check My FPL Squad          │  │  ← triggers file input
│  └──────────────────────────────────┘  │
│                                        │
│  [EXAMPLE SQUAD image]                 │
│  ┌──────────────────────────────────┐  │
│  │  61.8 pts  │ You're in the mix  │  │
│  │  Midfield needs a look          │  │
│  └──────────────────────────────────┘  │
│  * Illustrative example                │
│                                        │
│  No login · No FPL ID · No setup       │
└────────────────────────────────────────┘
```

**Event:** `screenshot_uploaded` fired on file selection.

---

### S0a — Loading / Scanning

Shown between file selection and payload ready. Two micro-states: scanning → projecting.

```
┌────────────────────────────────────────┐
│  SquadCheck                      [FPL] │
├────────────────────────────────────────┤
│                                        │
│  [Player card skeleton × 3]            │
│  ┌──────────────────────────────────┐  │
│  │  ░░░░░░░░░  ░░  ░░░░            │  │
│  └──────────────────────────────────┘  │
│  ┌──────────────────────────────────┐  │
│  │  ░░░░░░░░░░░  ░░  ░░░░          │  │
│  └──────────────────────────────────┘  │
│                                        │
│  Scanning your squad...                │  ← or "Projecting GW6..."
│                                        │
└────────────────────────────────────────┘
```

Two label states:
- `"Scanning your squad..."` (scanner running)
- `"Projecting GW6..."` (projection engine running)

No spinner. Use animated skeleton cards (CSS pulse).

---

### S0b — Scanner failure

```
┌────────────────────────────────────────┐
│  SquadCheck                      [FPL] │
├────────────────────────────────────────┤
│                                        │
│  ┌──────────────────────────────────┐  │
│  │                                  │  │
│  │   Couldn't read this screenshot  │  │
│  │                                  │  │
│  │   [state-specific reason]        │  │
│  │                                  │  │
│  │   [Try another screenshot]       │  │  ← primary CTA
│  │                                  │  │
│  └──────────────────────────────────┘  │
│                                        │
│  Make sure you're uploading:           │
│  · Your FPL squad from the app         │
│  · Pitch view or List view             │
│  · All 15 players visible              │
│                                        │
└────────────────────────────────────────┘
```

**State-specific messages by scanner status:**

| Status | Headline | Body |
|---|---|---|
| PARTIAL | "Couldn't see all your players" | "We found {n}/15 players. Try uploading your Pitch or List view with all players visible." |
| AMBIGUOUS | "Two players look the same" | "We found a duplicate name. Double-check the screenshot includes your full squad." |
| UNSUPPORTED | "We couldn't read this screenshot" | "Try using your FPL app Pitch view or List view screenshot." |

**Event:** `scan_failed` with `{reason: status}`.

---

### S1 — Gameweek Hero

The first thing the user sees when the result loads. Full-width, no scrolling required for this section.

```
┌────────────────────────────────────────┐
│  SquadCheck                      [FPL] │
│  ─────────────────────────────────── ← thin border
│                                        │
│  [pill: GW 6 · Your team]             │
│                                        │
│              61.5                      │  ← xPts, large (64px, lime)
│        PROJECTED POINTS                │  ← label (11px, gray, caps)
│                                        │
│  Your starting XI this Gameweek        │  ← 15px body
│                                        │
│  ┌────────────┐  ┌────────────┐        │
│  │ HIGH       │  │ 11         │        │  ← confidence badge + starters
│  │ confidence │  │ starters   │        │
│  └────────────┘  └────────────┘        │
│                                        │
│  ──────── Scroll to see your team ──── │  ← subtle fade cue
└────────────────────────────────────────┘
```

**Confidence badge logic (aggregate):**
- All 11 starters HIGH → `HIGH confidence`
- Any starter LOW → `Mixed confidence`
- Otherwise → `Good confidence`

**Data consumed:** `recommended_xi.xPts` (or `submitted_xi.xPts` if HOLD), `confidence` of starters.

**Event:** `projection_viewed` with `{gw, total_xPts, action}`.

---

### S2 — Your Players (position sections)

A continuous vertical feed grouped by position. Each position group has a sub-headline showing the group's combined projected contribution.

#### Position group header

```
┌────────────────────────────────────────┐
│  DEFENCE                   11.3 pts    │  ← pos label (caps) + sum
│  ──────────────────────────────────    │
└────────────────────────────────────────┘
```

**Group totals shown:**
- GKP: `xPts + xSavePts framing`: "Projected N.N pts · {N}% clean sheet chance"
- DEF: `xPts sum` + "N.N% clean sheet chance"
- MID: `xPts sum`
- FWD: `xPts sum`
- Bench: `xPts sum` + delta if swap available

**Event:** `position_section_viewed` with `{position, count, total_xPts}`.

#### Player card (collapsed — Level 1)

```
┌────────────────────────────────────────┐
│  ┌────┐                                │
│  │ GK │  Raya            [HIGH]        │  ← position tag + name + confidence
│  └────┘  Arsenal                       │  ← team (gray, 13px)
│          5.3 projected pts             │  ← xPts (lime if >5, white otherwise)
│                                        │
│                          Why? ›        │  ← progressive disclosure trigger
└────────────────────────────────────────┘
```

**Colour rules for projected points:**
- ≥ 8.0: lime text
- 5.0–7.9: white text
- < 5.0: gray text

**Captain / Vice indicator** (⚠ data gap — see Section 9):
Not available from current payload. Field is `TBD`.

**Event:** `player_opened` with `{player_id, position, xPts, confidence}`.

---

### S2p — Player detail panel (progressive disclosure)

Tapping "Why?" expands inline. Three levels.

#### Level 2 — Human-readable evidence

```
┌────────────────────────────────────────┐
│  ┌────┐                                │
│  │ GK │  Raya            [HIGH]        │
│  └────┘  Arsenal                       │
│          5.3 projected pts             │
│  ─────────────────────────────────── ← divider
│                                        │
│  WHY 5.3?                              │  ← 13px, gray, caps
│                                        │
│  ✓  Nailed starter — 5/5 games         │  ← from drivers[]
│  ✓  37% clean sheet chance             │  ← derived from p_cs
│  ✓  Home fixture this week             │  ← from is_home
│                                        │
│  ⚠  Moderate attacking fixture         │  ← from risks[] if present
│                                        │
│         See the numbers ›              │  ← Level 3 trigger
│                                        │
│                          Less ↑        │  ← collapse trigger
└────────────────────────────────────────┘
```

**Driver → human text mapping:**

| driver string (from API) | UI text |
|---|---|
| "Nailed starter — 5/5 starts…" | "Nailed starter — playing every minute" |
| "Strong xG rate — 0.767…" | "Elite goal threat (adjusted for fixtures)" |
| "Good CS prospect — P(CS)=0.37…" | "{N}% clean sheet chance" |
| "Confirmed penalty taker" | "Takes penalties" |
| "Favourable attacking fixture…" | "Favorable fixture this week" |
| "Strong attacking fixture…" | "Strong fixture — {opponent} struggle to defend" |

**Risk → human text mapping:**

| risk string (from API) | UI text |
|---|---|
| "Injury/doubt — 75%…" | "Fitness doubt — 75% chance of playing" |
| "Minutes uncertainty — P(60+)=…" | "May not play 60 minutes" |
| "Difficult attacking fixture…" | "Tough attacking fixture" |
| "Leaky defence — λ=…" | "Defensive risk — high conceding environment" |
| "Small sample — N GW…" | "Early season — {N} games of data" |

**Event:** `player_explanation_opened` with `{player_id}`.

#### Level 3 — The numbers (optional)

```
┌────────────────────────────────────────┐
│  THE NUMBERS                           │
│  ─────────────────────────────────── ← divider
│                                        │
│  Minutes         87 expected           │  ← inputs.xMins
│  Minutes conf.   HIGH                  │  ← _minutes_conf
│                                        │
│  Attacking rate  0.77 xG / 90          │  ← inputs.xG_d (damped)
│  Raw stat        0.88 xG / 90          │  ← player_api.xG_p90
│  Data quality    5 games (72%)         │  ← player_api.starts, inputs.w
│                                        │
│  Fixture mult.   1.10× (home)          │  ← inputs.atk_scale + is_home
│  Opponent def.   1.30 xGC / game       │  ← inputs.opp_xgc_d
│                                        │
│  CS probability  37%                   │  ← inputs.p_cs
│  Expected GC     1.4 / game            │  ← inputs.lam
│                                        │
│  BREAKDOWN                             │
│  Appearance      +1.9                  │  ← components.xAppPts
│  Goals / assists +3.4                  │  ← xGoalPts + xAssistPts
│  Clean sheet     +0.0                  │  ← components.xCSPts
│  Bonus           +1.3                  │  ← components.xBonus
│  ─────────────────────                 │
│  Total           6.3 projected pts     │  ← xPts
│                                        │
└────────────────────────────────────────┘
```

This level is for users who want to verify the model. It should feel like opening the bonnet, not the default view.

**Data format rules:**
- Decimals: 1dp for xPts, 2dp for rates
- Probabilities: shown as percentages, not decimals
- Framing: "X expected" not "E[X]", "N% chance" not "P=0.N"

**Bench players** follow the same card structure but the entire bench section is collapsed behind a single `Bench (4 players)` heading that expands.

**Event:** (no separate event — covered by `player_explanation_opened` depth tracking)

---

### S3 — What's Awesome

2–4 highlights derived from the actual projection data. Never generic.

```
┌────────────────────────────────────────┐
│  WHAT'S LOOKING GOOD                   │
│  ─────────────────────────────────── ← divider
│                                        │
│  ┌──────────────────────────────────┐  │
│  │  ⚡  Haaland's fixture           │  │
│  │  City are home vs a leaky        │  │
│  │  defence. His best match of      │  │
│  │  the GW on paper.                │  │
│  └──────────────────────────────────┘  │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │  🔒  Strong defensive block      │  │
│  │  Your backline projects 36%      │  │
│  │  clean sheet odds. Above average │  │
│  │  for this round.                 │  │
│  └──────────────────────────────────┘  │
│                                        │
│  ┌──────────────────────────────────┐  │
│  │  📈  Midfield strength           │  │
│  │  3 midfielders project 6+ pts    │  │
│  │  this week. Consistent floor.    │  │
│  └──────────────────────────────────┘  │
└────────────────────────────────────────┘
```

**Callout generation rules (priority order — show top 4):**

| Condition | Callout text |
|---|---|
| Any starter `xPts ≥ 8.0` | "{player}'s fixture — {team context}" |
| `p_cs ≥ 0.40` for DEF starters (avg) | "Strong defensive block — {N}% CS odds" |
| 3+ MID starters with `xPts ≥ 6.0` | "Midfield strength — {N} players projecting 6+" |
| Starter has `atk_scale ≥ 1.20` | "Elite attacking fixture — {player} vs {opponent}" |
| Overall confidence all HIGH | "High confidence lineup — all starters rated HIGH" |
| Delta = 0 (HOLD) | "Optimal lineup — no improvements available" |

All text uses actual numbers pulled from the payload. Template engine fills them.

**Event:** none (passive section — position_section_viewed covers navigation).

---

### S4a — What Needs Attention (SWAP variant)

Shown when `action == "SWAP"` and `delta >= threshold`.

```
┌────────────────────────────────────────┐
│  SOMETHING TO CONSIDER                 │
│  ─────────────────────────────────── ← divider
│                                        │
│  ┌──────────────────────────────────┐  │
│  │  Mendy → Rogers                  │  │  ← out.web_name → in.web_name
│  │  +2.5 projected points           │  │  ← delta (lime)
│  └──────────────────────────────────┘  │
│                                        │
│  WHY MENDY COMES OUT                   │
│  ─────────────────                     │
│                                        │
│  Expected minutes    50                │  ← out player inputs.xMins
│  Projection conf.    MEDIUM            │  ← out player _projection_conf
│  Team fixture        1.05×             │  ← out player inputs.atk_scale
│  Opponent defense    Average           │  ← out player opp_xgc_d vs avg
│                                        │
│  WHY ROGERS COMES IN                   │
│  ─────────────────                     │
│                                        │
│  Expected minutes    87                │  ← in player inputs.xMins
│  Projection conf.    HIGH              │
│  Team fixture        1.22×             │  ← favorable
│  Opponent defense    Leaky             │  ← opp_xgc_d > league_avg
│                                        │
│  The difference isn't just player      │
│  quality — Rogers has a significantly  │
│  better fixture environment this week. │  ← model principle in plain language
│                                        │
└────────────────────────────────────────┘
```

**Opponent defensive quality → plain language:**

| `opp_xgc_d` vs `league_avg_xgc` | Label |
|---|---|
| > 1.35 (>= +20%) | "Leaky" |
| 1.15–1.35 (+7–20%) | "Below average" |
| 0.95–1.15 (±7%) | "Average" |
| 0.80–0.95 (-7–20%) | "Solid" |
| < 0.80 (<-20%) | "Elite defence" |

**For multi-player swaps:** Show each substitution as a separate card in sequence.

**Event:** `recommendation_viewed` with `{action, delta, out_id, in_id}`.

---

### S4b — What Needs Attention (HOLD variant)

Shown when `action == "HOLD"`.

```
┌────────────────────────────────────────┐
│  NOTHING NEEDS CHANGING                │
│  ─────────────────────────────────── ← divider
│                                        │
│  SquadCheck can't find a bench swap    │
│  that improves your starting XI        │
│  this Gameweek.                        │
│                                        │
│  Your bench:                           │
│  ┌──────────────────────────────────┐  │
│  │  Kelleher   GK   4.1 pts        │  │
│  │  White      DEF  3.8 pts        │  │
│  │  EpsilonMID MID  3.4 pts        │  │
│  │  GammaFWD   FWD  3.1 pts        │  │
│  └──────────────────────────────────┘  │
│                                        │
│  Each bench player projects lower      │
│  than the weakest starter they could   │
│  replace.                              │
│                                        │
└────────────────────────────────────────┘
```

This section exists so the user understands WHY it's HOLD — not just that it is. The bench xPts are shown explicitly so the user can validate the logic.

**Event:** `recommended_change_viewed` fires regardless of HOLD/SWAP when user reaches this section (scroll depth tracking).

---

### S5a — Final Call: HOLD

```
┌────────────────────────────────────────┐
│                                        │
│  ┌──────────────────────────────────┐  │
│  │                                  │  │
│  │          HOLD YOUR XI            │  │  ← large, white, Outfit 900
│  │                                  │  │
│  │  Your starting lineup is         │  │
│  │  already the strongest bench     │  │
│  │  combination available.          │  │
│  │                                  │  │
│  │  61.5 projected points           │  │  ← lime
│  │                                  │  │
│  └──────────────────────────────────┘  │
│                                        │
│  Good luck this Gameweek.              │
│                                        │
└────────────────────────────────────────┘
```

---

### S5b — Final Call: CHANGE

```
┌────────────────────────────────────────┐
│                                        │
│  ┌──────────────────────────────────┐  │
│  │                                  │  │
│  │    CONSIDER A BENCH CHANGE       │  │  ← large, white, Outfit 900
│  │                                  │  │
│  │  Mendy → Rogers                  │  │
│  │  +2.5 projected points           │  │  ← lime
│  │                                  │  │
│  │  Your XI       61.5              │  │
│  │  SquadCheck XI  64.0             │  │
│  │                                  │  │
│  └──────────────────────────────────┘  │
│                                        │
│  This is a bench swap, not a transfer. │  ← clarification (important)
│  No changes to your registered squad. │
│                                        │
└────────────────────────────────────────┘
```

The clarifying note ("This is a bench swap, not a transfer") is mandatory. Users must not confuse bench optimisation with FPL transfer advice.

**Event:** `recommended_change_viewed` with `{action: "SWAP", delta}`.

---

### S6 — Feedback

After the Final Call card. Not a modal. Part of the feed.

```
┌────────────────────────────────────────┐
│  HOW DID THIS FEEL?                    │
│  ─────────────────────────────────── ← divider
│                                        │
│  [ ❤️ Loved it ] [ 👍 Useful ]          │
│  [ 😐 Just okay ] [ 👎 Not useful ]     │
│                                        │
│  ─────────────────────────────────── ← appears after tap
│                                        │
│  WHAT DID YOU DO?                      │
│                                        │
│  [ Changed my XI ]                     │
│  [ Kept my XI ]                        │
│  [ Still deciding ]                    │
│                                        │
│  ─────────────────────────────────── ← appears after tap
│                                        │
│  Any reason? (optional)                │
│                                        │
│  [ ] Wanted more explanation           │
│  [ ] Projection felt wrong             │
│  [ ] Recommendation wasn't useful      │
│  [ ] Wanted transfer advice            │
│  [ ] Wanted captain advice             │
│  [ ] Something else                    │
│                                        │
│             [  Done  ]                 │
│                                        │
└────────────────────────────────────────┘
```

The feedback section never blocks navigation. "Done" is not required — scrolling past it is enough.

**Events:** `feedback_loved`, `feedback_useful`, `feedback_ok`, `feedback_bad`; `action_changed_xi`, `action_kept_xi`, `action_still_deciding`.

---

## 5. States summary

| State | Trigger | Screen path |
|---|---|---|
| Normal HOLD | action=HOLD, no improvement | S0→S0a→S1→S2→S3→S4b→S5a→S6 |
| CHANGE | action=SWAP, delta≥threshold | S0→S0a→S1→S2→S3→S4a→S5b→S6 |
| No bench improvement | delta=0 exactly | Same as HOLD; S4b shows "no better option exists" |
| Partial screenshot | scanner PARTIAL | S0→S0b (partial message) |
| Ambiguous screenshot | scanner AMBIGUOUS | S0→S0b (duplicate message) |
| Unsupported screenshot | scanner UNSUPPORTED | S0→S0b (format message) |
| Loading — scanning | between upload and scan | S0a ("Scanning your squad...") |
| Loading — projecting | between scan OK and payload | S0a ("Projecting GW6...") |
| Player card collapsed | default | S2 player card Level 1 |
| Player card expanded — Why | user taps "Why?" | S2 card Level 2 |
| Player card expanded — Numbers | user taps "See the numbers" | S2 card Level 3 |
| Bench collapsed | default | Single "Bench (4 players)" row |
| Bench expanded | user taps bench row | S2 bench section |

---

## 6. Analytics events

All events fire to a simple event log. No PII. No raw model data.

| Event | Fires when | Properties |
|---|---|---|
| `screenshot_uploaded` | File selected in upload input | — |
| `scan_valid` | Pipeline returns status=OK | `{gw}` |
| `scan_failed` | Pipeline returns SCAN_FAIL or RESOLVE_FAIL | `{reason: scanner_status}` |
| `projection_viewed` | S1 renders | `{gw, total_xPts, action, delta}` |
| `player_opened` | Player card Level 1 tapped | `{player_id, position, xPts, confidence, is_starting}` |
| `player_explanation_opened` | Player "Why?" tapped (Level 2) | `{player_id, position}` |
| `player_numbers_opened` | Player "See the numbers" tapped (Level 3) | `{player_id, position}` |
| `position_section_viewed` | Position group enters viewport | `{position, total_xPts}` |
| `bench_viewed` | Bench section expanded | `{bench_total_xPts}` |
| `recommendation_viewed` | S4a/S4b enters viewport | `{action, delta}` |
| `recommended_change_viewed` | S5 enters viewport | `{action, delta}` |
| `feedback_loved` | ❤️ tapped | — |
| `feedback_useful` | 👍 tapped | — |
| `feedback_ok` | 😐 tapped | — |
| `feedback_bad` | 👎 tapped | — |
| `action_changed_xi` | "Changed my XI" selected | — |
| `action_kept_xi` | "Kept my XI" selected | — |
| `action_still_deciding` | "Still deciding" selected | — |
| `feedback_reason` | optional reason selected | `{reason: string}` |

---

## 7. Data fields required from pipeline payload

All fields are already available in the V1 integration payload.

### Per-player (from `players[]`)

| Field path | Used for |
|---|---|
| `player_id` | Identity, cross-referencing |
| `web_name` | Display name |
| `position` | Position tag (GKP/DEF/MID/FWD), grouping |
| `team_id` | Team label (requires team name lookup — see gaps) |
| `is_starting` | Starting XI vs bench |
| `xPts` | Projected points number |
| `confidence` | Confidence badge |
| `_minutes_conf` | Sub-score for Level 3 |
| `_projection_conf` | Sub-score for Level 3 |
| `inputs.xMins` | "Expected X minutes" |
| `inputs.P_app` | Availability context |
| `inputs.P_60plus` | Minutes confidence context |
| `inputs.w` | "X games of data" |
| `inputs.xG_d` | "Adjusted attacking rate" |
| `inputs.xA_d` | "Adjusted creative rate" |
| `inputs.atk_scale` | Fixture attacking multiplier |
| `inputs.home_factor` | Home/away context |
| `inputs.is_home` | "Home fixture" / "Away fixture" label |
| `inputs.opponent_id` | Opponent identity (needs team name — see gaps) |
| `inputs.opp_xgc_d` | Opponent defensive quality → plain language |
| `inputs.opp_atk_q` | Opponent attacking quality |
| `inputs.team_xGC` | Team defensive context |
| `inputs.lam` | Expected GC → CS calculation |
| `inputs.p_cs` | Clean sheet probability percentage |
| `components.xAppPts` | Appearance contribution |
| `components.xGoalPts` | Goal/assist contribution |
| `components.xAssistPts` | (already in xGoalPts for display) |
| `components.xCSPts` | CS contribution |
| `components.xGCDeductPts` | GC deduction (for DEF/GKP context) |
| `components.xSavePts` | Save points (GKP only) |
| `components.xBonus` | Bonus contribution |
| `player_api.xG_p90` | Raw rate for Level 3 |
| `player_api.xA_p90` | Raw rate for Level 3 |
| `player_api.starts` | "5/5 games" |
| `player_api.minutes` | Season minutes context |
| `player_api.status` | Availability gate display |
| `player_api.cop_next` | "75% chance of playing" |
| `drivers[]` | What's good → plain language mapping |
| `risks[]` | What needs attention → plain language mapping |

### Squad-level (from payload root)

| Field path | Used for |
|---|---|
| `submitted_xi.xPts` | "Your XI: X pts" |
| `submitted_xi.formation` | Formation display |
| `recommended_xi.xPts` | "SquadCheck XI: X pts" |
| `recommended_xi.formation` | Formation display |
| `delta` | "+X projected points" |
| `action` | HOLD / CHANGE |
| `substitutions[].out` | Player coming out |
| `substitutions[].in` | Player coming in |

---

## 8. Data gaps

The following are **missing from the current payload** and must be addressed before or during implementation.

### Gap 1 — Team name (critical for display)
**Missing:** `team_name` or `team_short_name` per player.
**Current:** Only `team_id` (integer) is in the payload.
**Impact:** Cannot display "Arsenal" under player name. Can display team ID only.
**Fix:** Add a `team_name_map` lookup (from bootstrap-static `teams[]`) to the pipeline, or resolve at render time from a bundled constant. The bootstrap-static `teams[].short_name` (e.g., "ARS") and `teams[].name` (e.g., "Arsenal") are available.

### Gap 2 — Opponent team name (important for explanation)
**Missing:** Opponent team name for the fixture context.
**Current:** `inputs.opponent_id` (integer) is in the payload.
**Impact:** "Your team faces ?" — cannot show "vs Leeds" without resolution.
**Fix:** Same as Gap 1. Resolve `opponent_id → team name` from bootstrap `teams[]`.

### Gap 3 — Captain / Vice-captain (design intention blocked)
**Missing:** Which player is captain / which is vice-captain.
**Current:** Not in scanner output. Not in pipeline payload.
**Impact:** Cannot show captain badge on player card. Cannot show captain points multiplier (2× on captain's score affects actual FPL score).
**Design decision:** Captain badge is listed as TBD in the wireframe. The projected points shown are **individual** (not doubled). The UI should note this, or captain selection should be a post-V1 feature.
**Fix options:** (a) Add captain/vice picker to the upload flow. (b) Infer highest-projected starter as suggested captain (display only, not in projection math). (c) Explicitly omit until V1.1.

### Gap 4 — GW number
**Missing:** The current Gameweek number is not in the root payload.
**Current:** Individual fixture records have `event` (GW number) but this isn't surfaced at the top level.
**Impact:** Cannot show "GW 6" in the hero or pill without additional context.
**Fix:** Add `gameweek` to the pipeline payload root from the fixture data of any player, or pass it as a parameter to `run_pipeline()`.

### Gap 5 — Player photo / kit colour (nice-to-have)
**Missing:** No player image or kit colour in the payload.
**Current:** Bootstrap-static has `photo` field (Opta image ID). Not in pipeline output.
**Impact:** Player cards show name only. No visual identity.
**Fix:** Optional for V1. Can be added from `player_api` at render time if bootstrap is available client-side.

### Gap 6 — Upcoming opponent name in "What Needs Attention"
**Missing:** When explaining a substitution, "better fixture vs ?" can't complete without opponent name.
**Impact:** Copy reads "Rogers has a better fixture environment" without specifying who Rogers faces.
**Fix:** Resolve from Gap 2 fix. Both require the same team name lookup.

---

## 9. Component inventory

| Component | States | Notes |
|---|---|---|
| `GWHero` | normal, loading | Big number |
| `PlayerCard` | collapsed, level2-open, level3-open, bench | Reused for all positions |
| `PositionSection` | collapsed, expanded | Contains N PlayerCards |
| `PositionGroupHeader` | — | Label + xPts sum |
| `AwesomeCard` | — | Icon + headline + body |
| `AttentionCard` | swap, hold | Conditional render |
| `SubstitutionDetail` | out-player, in-player | 2 sub-sections |
| `FinalCallCard` | hold, change | Green or lime tint |
| `FeedbackRow` | idle, rated, action-selected, reason-selected | Progressive |
| `ScanErrorCard` | partial, ambiguous, unsupported | 3 message variants |
| `LoadingSkeleton` | scanning, projecting | Animated shimmer |
| `ConfidenceBadge` | HIGH, MEDIUM, LOW | Colour-coded |
| `OpponentQualityLabel` | elite-defence … leaky | 5 text variants |

---

## 10. Interaction notes

**Scroll behaviour:** The result is a single scroll. No pagination, no tabs.

**Expanding player cards:** Expansion is inline (height animates). Only one player card can be at Level 3 at a time (Level 2 can be open on multiple).

**Bench section:** Collapsed by default behind a single expandable row. Expanding shows all 4 bench players in the same PlayerCard format.

**Section transitions:** Subtle section dividers. No heavy separators. Use negative space and typography weight to distinguish sections from each other.

**Error states:** Never show raw error codes or status strings to the user. Always show a human-readable reason with a recovery action.

**HOLD framing:** The word "HOLD" should be presented as a confident decision, not a failure state. Visual treatment for HOLD card is the same weight as CHANGE card — both are valid terminal states.

---

*Artifact path: `fpl/UI_SPEC_V1.md`*
