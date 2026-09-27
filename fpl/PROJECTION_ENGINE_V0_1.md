# SquadCheck Projection Engine v0.1
**Milestone 2 Design Document · September 2026**

---

## 1. Objective

Produce a per-player **expected FPL points (xPts)** for the upcoming Gameweek.

xPts is not the final product verdict. It is the analytical foundation that feeds squad diagnosis, captaincy logic, and transfer recommendations. Those layers are built on top of xPts — they are not the same thing as xPts.

---

## 2. Model Philosophy

**Current-season-first.** Football is non-stationary. Managers change, roles change, squads evolve. A five-game current-season sample is almost always more predictive than three seasons of historical data for a player who now plays a different role, at a different club, under a different manager.

**Transparent by design.** Every xPts value must decompose into auditable components. No black boxes. A developer without a data science background must be able to read the formula and trace any output back to specific input values.

**Minimum viable, not maximum coverage.** The goal is the smallest reliable set of current-season signals capable of producing a useful projection. Every input must earn its place. Complexity is a liability at this stage.

**Not FPL's ep_next.** The `ep_next` field in the FPL API equals `form` (trailing 5-GW points-per-game average) for the majority of players. For unavailable players, it approaches zero. It has no fixture adjustment, no xG component, no minutes model, and no position-specific logic. It is a useful sanity benchmark, not a projection model. SquadCheck's xPts must be different from and better than ep_next.

---

## 3. Data Sources

All data is from the official FPL API. No third-party data is required for V0.1.

| Source | Endpoint | Fetch Frequency |
|--------|---------|----------------|
| Bootstrap-static | `/api/bootstrap-static/` | Once per session (cache for ~30 min) |
| Player history | `/api/element-summary/{id}/` | Per player in user's squad (15 requests max) |
| Fixtures | `/api/bootstrap-static/` (via elements[].fixtures lookup) or `/api/fixtures/` | Once per session |

For a single user's squad of 15 players: 1 bootstrap-static call + 15 element-summary calls = 16 API requests total. All are unauthenticated GET requests.

---

## 4. Data Availability Summary

See `PROJECTION_DATA_AUDIT.md` for the full field-by-field audit. Summary:

**Confirmed available and reliable:**
- Player status, availability flags, chance-of-playing percentages
- Season xG, xA, xGI, xGC (totals and per-90 rates)
- Per-GW history: minutes, starts, xG, xA, xGI, xGC, bonus, BPS, total_points
- Upcoming fixture: difficulty (1–5, player's team perspective), is_home, kickoff
- Set-piece order: penalties_order, direct_freekicks_order, corners_order
- Team overall strength (1–5 scale, home and away)
- Saves, clean sheets, goals conceded

**Confirmed absent / unusable:**
- `strength_attack_home/away` and `strength_defence_home/away` — field exists but is **0 for all 20 teams**
- Team standings, league position, current form — all null/0
- Lineup data before deadline
- Fixture-level xG for teams
- Opponent-specific attack/defence split

**Key constraint acknowledged:** With only 5 completed GWs at GW6, all per-90 rates and per-start averages are based on a small sample. The model must handle early-season noise explicitly via minimum thresholds and confidence dampening.

---

## 5. Core Inputs

These are the minimum fields required to run V0.1. All confirmed available from the FPL API.

| Input | Source Field(s) | Why It Earns Its Place |
|-------|----------------|----------------------|
| Player status | `status`, `chance_of_playing_next_round` | Gating condition. A player who cannot play has xPts = 0. Ignoring availability is the single biggest source of projection error. |
| Recent minutes | Per-GW `history[].minutes` (last 3 GWs) | Most predictive signal for minutes expectation. A player who played 45, 60, 72 minutes recently is almost certainly not a guaranteed 90. |
| Season starts | `starts` (BS) | Distinguishes nailed starters from rotation players. |
| xG per 90 | `expected_goals_per_90` (BS) | Underlying attacking quality, stripped of luck. Preferred over raw goals at low N. |
| xA per 90 | `expected_assists_per_90` (BS) | Underlying creative contribution. |
| xGC per 90 | `expected_goals_conceded_per_90` (BS) | Proxy for team defensive quality. Determines clean sheet probability. |
| Position | `element_type` (BS) | Controls FPL scoring rules: goal points, CS points, GC deduction. |
| Fixture difficulty | `difficulty` from element-summary fixtures | Directional fixture adjustment for both attacking and defensive expectations. |
| Home/away | `is_home` from element-summary fixtures | Home advantage is real and persistent. |
| Season bonus | `bonus` / `starts` (BS) | Reasonable bonus estimate for players with a meaningful sample. |
| Saves per 90 | `saves_per_90` (BS) | GKP-only. Save points (1 per 3 saves) are a reliable component. |
| Penalties order | `penalties_order` (BS) | A confirmed penalty taker gets a non-trivial xPts uplift (~1–2 pts per game for forwards). |

---

## 6. Optional Inputs (Not Required for V0.1)

| Input | Source | Condition for Use |
|-------|--------|-----------------|
| Prior-season xG/xA per 90 | `history_past[]` in element-summary | Only for players with fewer than 2 current-season GW appearances. Must be flagged explicitly as prior-season data. |
| Direct FK order | `direct_freekicks_order` | Minor xA signal. Complicated to calibrate. Defer to V0.2. |
| Corners order | `corners_and_indirect_freekicks_order` | Minor xA signal. Defer to V0.2. |
| Yellow card rate | `yellow_cards / starts` | Small negative contribution (-1 pt). Include if trivial to add; otherwise defer. |
| BPS per game | `bps / starts` | Alternative bonus estimate. Redundant with `bonus / starts` for V0.1. |
| Transfers in/out event | `transfers_in_event`, `transfers_out_event` | Transfer momentum. Useful for differential recommendation layer, not raw xPts. |

---

## 7. Inputs Rejected for V0.1

| Input | Reason for Rejection |
|-------|---------------------|
| `ep_next` as xPts | It is a simple rolling PPG average with minimal injury logic. Not a projection. Use as a sanity benchmark only. |
| `form` as xAtk proxy | Form reflects past points including lucky goals and blanks. xG-based rates are more stable. |
| `strength_attack_home/away` | All 0 in live API. Cannot use. |
| `strength_defence_home/away` | All 0 in live API. Cannot use. |
| ICT index (influence/creativity/threat) | Composite FPL metric less transparent than raw xG/xA. Adds nothing that xG/xA don't already capture. |
| Historical seasons as primary input | Football non-stationarity means 2023/24 Haaland data is poor evidence for 2026/27 Haaland projection. Useful only as fallback for tiny current-season samples. |
| `value_form`, `value_season` | Price-efficiency metrics, not performance metrics. Not relevant to xPts. |
| `dreamteam_count`, `in_dreamteam` | Reflects past outcomes, not future expectation. |
| Press conference / news text parsing | Free text. Not reliably parseable at V0.1. The `chance_of_playing_next_round` field already encodes the availability signal from this. |
| `defensive_contribution`, `clearances_blocks_interceptions`, `recoveries`, `tackles` | Defensive contribution metrics exist but their FPL scoring impact is indirect at best. Not worth the complexity at V0.1. |

---

## 8. xMins Model

Expected minutes is the most important single input. All other components are scaled by it.

### 8a. Availability Gate (applied first)

```
if status == 'u' or status == 's':
    xMins = 0   // unavailable (departed) or suspended
elif status == 'i' and chance_of_playing_next_round == 0:
    xMins = 0   // confirmed out
elif status == 'd' and chance_of_playing_next_round == 0:
    xMins = 0   // confirmed out
```

### 8b. Injury / Doubt Scaling

When a player has a doubt flag with a non-zero cop:

```
cop_fraction = chance_of_playing_next_round / 100.0
// e.g. 75% → 0.75, 50% → 0.50

// Compute base expected minutes (as if available):
recent_avg_mins = mean of last min(3, n_gws_played) GW minutes
season_avg_mins = season_minutes / n_gws_played

// Blend: weight recent GWs more heavily
base_xMins = 0.65 × recent_avg_mins + 0.35 × season_avg_mins
base_xMins = min(base_xMins, 90)

// Apply cop scaling
xMins = cop_fraction × base_xMins
```

### 8c. Available Player (status = 'a', no cop flag)

```
if n_gws_played >= 3:
    recent_avg = mean of last 3 GW minutes
    season_avg = season_minutes / n_gws_played
    xMins = 0.65 × recent_avg + 0.35 × season_avg

elif n_gws_played >= 1:
    xMins = mean of all available GW minutes
    // Confidence is MEDIUM regardless

else:
    // New player: no current-season data
    // See Section 17: New Player Handling
    xMins = positional_default[position]
    // GKP: 60, DEF: 55, MID: 50, FWD: 45
    // Confidence = LOW

xMins = min(max(xMins, 0), 90)  // clamp to [0, 90]
```

### 8d. Recent-Start Test

Apply a non-start penalty when a player has been coming off the bench recently:

```
recent_starts = sum of starts in last 3 GW history records
if recent_starts == 0:
    xMins = min(xMins, 30)   // likely bench
    confidence = LOW
elif recent_starts == 1:
    xMins = min(xMins, 65)   // rotation risk
    // confidence degraded
```

### 8e. xMins Output

`xMins`: float, 0–90  
`xMins_confidence`: HIGH | MEDIUM | LOW (see Section 13)

---

## 9. Attacking Model (xAtk)

### 9a. Expected Goals and Assists

Scale season per-90 rates to xMins:

```
scale = xMins / 90.0

xGoals = expected_goals_per_90 × scale
xAssists = expected_assists_per_90 × scale
```

### 9b. Minimum Sample Floor

With only 5 GWs at GW6, per-90 rates can be extreme (a player with 1 goal in 1 game has xG_p90 = 0.74 from that one match). Apply a damping weight:

```
// Full confidence in the rate requires at least MIN_GWS games
MIN_GWS = 8  // adjust as season progresses

if n_gws_played >= MIN_GWS:
    damped_xG_p90 = expected_goals_per_90
else:
    // Dampen toward positional mean
    positional_xG_baseline = {1: 0.01, 2: 0.04, 3: 0.10, 4: 0.20}
    w = n_gws_played / MIN_GWS   // 0.0 to <1.0
    damped_xG_p90 = w × expected_goals_per_90 + (1 - w) × positional_xG_baseline[position]
```

Apply same logic to xA_p90 with baselines: {1: 0.01, 2: 0.04, 3: 0.10, 4: 0.06}.

### 9c. FPL Goal Points by Position

```
goal_pts = {1: 6, 2: 6, 3: 5, 4: 4}
assist_pts = 3   // same for all positions
```

### 9d. Penalty Taker Bonus

```
if penalties_order == 1:
    // ~0.27 penalties per game across PL (roughly 1 every 3.7 games)
    pen_freq = 0.27
    xPenGoals = pen_freq × 0.76  // ~76% conversion rate
    // Note: xG from open play already includes some penalty xG in the API
    // Add incremental boost only if it's not already reflected:
    // For V0.1: add a flat 0.15 xGoals bonus for penalty takers
    // as most per-90 xG doesn't capture full PK probability
    xGoals += 0.15
```

### 9e. Fixture Multiplier (Attacking)

```
atk_difficulty_mult = {1: 1.25, 2: 1.10, 3: 1.00, 4: 0.85, 5: 0.65}
atk_mult = atk_difficulty_mult[fixture_difficulty]

if is_home:
    atk_mult *= 1.05  // ~5% home advantage for attacking

xAtk = (xGoals × goal_pts[position] + xAssists × assist_pts) × atk_mult
```

---

## 10. Defensive Model (xDef)

Applies to GKP (position 1) and DEF (position 2) for clean sheet points. MID earns 1 pt per CS. FWD earns nothing defensively.

### 10a. Clean Sheet Probability

The player's `expected_goals_conceded_per_90` reflects their team's defensive quality based on current-season xGC. Use it as the primary signal.

```
xGC_p90 = expected_goals_conceded_per_90

// Base clean sheet probability derived from xGC_p90
// Calibration: xGC_p90 of 0.8 corresponds to ~42% CS rate (Arsenal ~GW6 2026)
//              xGC_p90 of 1.0 → ~35% CS rate
//              xGC_p90 of 1.3 → ~22% CS rate
//              xGC_p90 of 1.6 → ~13% CS rate
// Linear approximation: p_cs_base = max(0.05, 0.75 - (xGC_p90 × 0.40))

p_cs_base = max(0.05, min(0.80, 0.75 - (xGC_p90 × 0.40)))
```

### 10b. Fixture Adjustment for Defence

```
// Same difficulty scale, but for defence:
// An easy fixture (low difficulty) = weak opponent attack = higher CS probability
def_difficulty_mult = {1: 1.30, 2: 1.10, 3: 1.00, 4: 0.80, 5: 0.60}
p_cs_adj = min(0.85, p_cs_base × def_difficulty_mult[fixture_difficulty])
```

### 10c. Minutes Eligibility for Clean Sheet

FPL requires 60+ minutes to earn CS points:

```
if xMins >= 70:
    cs_eligibility = 1.0
elif xMins >= 45:
    cs_eligibility = (xMins - 30) / 45.0  // rough scaling
else:
    cs_eligibility = 0.0
```

### 10d. CS Points by Position

```
cs_pts = {1: 6, 2: 6, 3: 1, 4: 0}
xCS = p_cs_adj × cs_pts[position] × cs_eligibility
```

### 10e. Goals Conceded Deduction (GKP and DEF only)

```
if position in (1, 2):
    // Expected goals conceded in this game:
    xGC_game = xGC_p90 × (xMins / 90) × (2.0 - def_difficulty_mult[fixture_difficulty])
    // Note: (2.0 - def_mult) inverts the fixture adjustment:
    //   easy fixture (def_mult=1.30) → gc_mult=0.70 → fewer expected GC
    //   hard fixture (def_mult=0.60) → gc_mult=1.40 → more expected GC
    xGCpts = -(xGC_game / 2.0)  // -1 pt per 2 goals conceded
else:
    xGCpts = 0.0
```

### 10f. Save Points (GKP only)

```
if position == 1:
    expected_saves = saves_per_90 × (xMins / 90)
    xSavePts = expected_saves / 3.0  // 1 pt per 3 saves
else:
    xSavePts = 0.0
```

### 10g. Total Defensive Contribution

```
xDef = xCS + xGCpts + xSavePts
```

---

## 11. Bonus Model (xBonus)

Bonus is the noisiest FPL component. BPS varies significantly game to game. At GW6 (5 data points), the bonus per start is highly volatile.

### 11a. Simple Per-Start Average

```
if starts == 0:
    xBonus = 0.0
else:
    bonus_per_start = season_bonus / starts
    xBonus = bonus_per_start × min(xMins / 90, 1.0)
    xBonus = min(xBonus, 3.0)  // hard cap: 3 is FPL maximum per game
```

### 11b. Early-Season Damping

With < 5 starts, extreme bonus averages (e.g. 3.0 bonus/game from two huge hauls) should be dampened:

```
if starts < 5:
    positional_bonus_baseline = {1: 0.3, 2: 0.5, 3: 0.7, 4: 0.9}
    w = starts / 5.0
    xBonus = w × xBonus + (1 - w) × positional_bonus_baseline[position]
```

### 11c. Assessment

The bonus model will be the least accurate component of xPts at GW6. That is acceptable for V0.1. The key is that it does not systematically bias results and is capped. Flag bonus as LOW confidence component in output.

---

## 12. Fixture Treatment

### 12a. What We Have

The FPL API provides a per-game difficulty rating (1–5) for each upcoming fixture, from the player's team's perspective. This is available in `element-summary/{id}/fixtures[].difficulty`.

- Difficulty 1 = easiest possible opponent (e.g. promoted side away)
- Difficulty 5 = hardest possible opponent (e.g. current top-4 away)

### 12b. Known Limitations of the Difficulty Rating

1. It is assigned pre-season based on historical team strength, not current-season form.
2. It does not distinguish opponent attack from opponent defence.
3. Teams with good defences but weak attacks could have an inflated difficulty rating for attackers, and vice versa.

### 12c. V0.1 Approach: Use With Muted Weights

For V0.1, use the difficulty rating as a directional adjustment with muted multipliers. Do not apply aggressive adjustments. A 5x difficulty game should not produce 0 xPts for an elite player.

```
// Attacking multipliers (applied to xAtk)
{1: 1.25, 2: 1.10, 3: 1.00, 4: 0.85, 5: 0.65}

// Defensive multipliers (applied to CS probability)
{1: 1.30, 2: 1.10, 3: 1.00, 4: 0.80, 5: 0.60}
```

Maximum swing: a difficulty-1 home fixture produces 1.25 × 1.05 = ~1.31× attacking multiplier. A difficulty-5 away fixture produces 0.65× attacking multiplier. The range is approximately 2× from easiest to hardest. This is intentionally conservative.

### 12d. Double and Blank Gameweeks

**Not handled by the current difficulty approach.** If a player has 0 fixtures in a GW → xPts = 0. If a player has 2 fixtures in a GW (double GW) → compute xPts for each fixture separately and sum. Check `element-summary/{id}/fixtures` and count entries for the target GW.

### 12e. Home Advantage

Apply a +5% multiplier to attacking expectation for home games. This is conservative but directionally correct.

---

## 13. Confidence Model

Confidence is a first-class output, not an afterthought. It determines how strongly the xPts value should be acted upon and how prominently uncertainty should be displayed.

### 13a. Confidence Levels

**HIGH**
- status = 'a' (available), no cop flag
- n_gws_played >= 4
- recent_starts (last 3 GWs) = 3 (started every game)
- Recent 3-GW average minutes >= 75
- Coefficient of variation of recent minutes <= 0.15 (consistent minutes, not erratic)

**MEDIUM**
- status = 'a' but cop_next = 75
- OR n_gws_played = 2–3
- OR recent_starts = 2 out of 3 (some rotation risk)
- OR recent minute variance is moderate (CV 0.15–0.35)

**LOW**
- status = 'd' (doubt) with any cop value
- OR n_gws_played <= 1
- OR recent_starts = 0 or 1 out of last 3 (rotation/bench player)
- OR new player (team_join_date within last 28 days)
- OR cop_next <= 50
- OR recent minute variance is high (CV > 0.35)

### 13b. Confidence Inheritance

The overall confidence is the worst of the component confidences:
- xMins confidence (see 8c/8d above)
- Data adequacy confidence (n_gws < 4 → MEDIUM floor)
- Availability confidence (cop_next < 100 → at most MEDIUM)

If any component is LOW, overall confidence is LOW.

### 13c. Effect of Confidence on Display (not on the number itself)

Confidence does NOT change the xPts calculation. It annotates the output to indicate reliability. The product layer uses confidence to:
- Suppress weak recommendations for LOW confidence players
- Surface uncertainty in explanations ("uncertain minutes" risk)
- Affect captaincy logic (never recommend LOW confidence captain)

---

## 14. Proposed xPts Formula

### Full Pseudocode

```
function compute_xPts(player, gw_target):

  // STEP 1: Availability gate
  if is_unavailable(player):
      return {xMins: 0, xPts: 0, confidence: LOW, ...}

  // STEP 2: xMins
  xMins, mins_confidence = compute_xMins(player)

  // STEP 3: Fixture
  fixture = get_fixture(player, gw_target)
  if fixture is null:
      return {xMins: 0, xPts: 0, confidence: LOW, drivers: ["No fixture this GW"]}

  difficulty = fixture.difficulty         // 1–5
  is_home = fixture.is_home               // bool

  atk_mult = ATK_DIFFICULTY_MULT[difficulty] × (1.05 if is_home else 1.0)
  def_mult = DEF_DIFFICULTY_MULT[difficulty]

  // STEP 4: Appearance points
  p_60plus = max(0, min(1, (xMins - 15) / 55))
  xApp = 1.0 + p_60plus × 1.0     // 1.0 base (for playing at all) + up to 1.0 more
  if xMins == 0: xApp = 0.0

  // STEP 5: Attacking
  n = player.n_gws_played
  MIN_GWS = 8
  pos_xG_base = {1: 0.01, 2: 0.04, 3: 0.10, 4: 0.20}
  pos_xA_base = {1: 0.01, 2: 0.04, 3: 0.10, 4: 0.06}
  w = min(n / MIN_GWS, 1.0)

  xG_p90 = w × player.expected_goals_per_90 + (1-w) × pos_xG_base[player.position]
  xA_p90 = w × player.expected_assists_per_90 + (1-w) × pos_xA_base[player.position]

  scale = xMins / 90.0
  xGoals = xG_p90 × scale
  xAssists = xA_p90 × scale

  if player.penalties_order == 1:
      xGoals += 0.15  // incremental pen taker bonus

  GOAL_PTS = {1: 6, 2: 6, 3: 5, 4: 4}
  xAtk = (xGoals × GOAL_PTS[player.position] + xAssists × 3) × atk_mult

  // STEP 6: Defensive
  xGC_p90 = player.expected_goals_conceded_per_90
  p_cs_base = max(0.05, min(0.80, 0.75 - xGC_p90 × 0.40))
  p_cs_adj = min(0.85, p_cs_base × def_mult)
  cs_elig = max(0, min(1, (xMins - 30) / 45))

  CS_PTS = {1: 6, 2: 6, 3: 1, 4: 0}
  xCS = p_cs_adj × CS_PTS[player.position] × cs_elig

  if player.position in (1, 2):
      gc_mult = 2.0 - def_mult
      xGC_game = xGC_p90 × scale × gc_mult
      xGCpts = -(xGC_game / 2.0)
  else:
      xGCpts = 0.0

  if player.position == 1:
      xSavePts = player.saves_per_90 × scale / 3.0
  else:
      xSavePts = 0.0

  xDef = xCS + xGCpts + xSavePts

  // STEP 7: Bonus
  if player.starts > 0:
      bps = player.bonus / player.starts
      w_bonus = min(player.starts / 5.0, 1.0)
      pos_bonus_base = {1: 0.3, 2: 0.5, 3: 0.7, 4: 0.9}
      xBonus = w_bonus × bps + (1-w_bonus) × pos_bonus_base[player.position]
      xBonus = xBonus × min(scale, 1.0)
      xBonus = min(xBonus, 3.0)
  else:
      xBonus = 0.0

  // STEP 8: Total
  xPts = xApp + xAtk + xDef + xBonus
  xPts = max(0.0, round(xPts, 1))

  // STEP 9: Confidence
  confidence = compute_confidence(player, mins_confidence, n)

  return {
      player_id: player.id,
      xMins: xMins,
      xPts: xPts,
      confidence: confidence,
      components: {
          xApp: xApp,
          xAtk: xAtk,
          xDef: xDef,
          xBonus: xBonus,
          fixture_mult: atk_mult
      },
      drivers: derive_drivers(player, xMins, xAtk, xDef, fixture),
      risks: derive_risks(player, xMins, confidence, fixture)
  }
```

### Weighting Rationale

| Component | Typical range for nailed starter | Dominates for |
|-----------|--------------------------------|--------------|
| xApp | 1.7–1.95 | Players with minutes risk |
| xAtk | 0.0–8.0+ | Attackers and premium midfielders |
| xDef | -0.5–5.0 | GKP and DEF in solid teams |
| xBonus | 0.0–2.5 | Consistent contributors |

For a reliable FWD (Haaland, GW6, away vs difficulty-4 opponent):
- xApp ≈ 1.90 (90 mins confident)
- xAtk ≈ 4.3 (0.89 xG_p90 × 0.85 atk_mult × 4 goal_pts + 0.15 pen_bonus × 4)
- xDef ≈ 0.0 (FWD, no CS points)
- xBonus ≈ 1.8 (avg 2.4 bonus/start, damped slightly)
- **xPts ≈ 8.0**

Actual ep_next for Haaland = 7.8. Our model produces a similar aggregate from transparent components rather than a rolling average. The model also provides fixture context (4 = hard) and the penalty taker signal, which ep_next ignores.

---

## 15. Output Schema

```json
{
  "player_id": 411,
  "web_name": "Haaland",
  "position": 4,
  "team_id": 15,
  "gw": 6,

  "xMins": 90.0,
  "xPts": 8.0,
  "confidence": "HIGH",

  "components": {
    "xApp": 1.90,
    "xAtk": 4.35,
    "xDef": 0.00,
    "xBonus": 1.75,
    "fixture_atk_mult": 0.893
  },

  "inputs_used": {
    "status": "a",
    "chance_of_playing_next_round": null,
    "n_gws_played": 5,
    "starts": 5,
    "recent_3gw_mins": [90, 90, 90],
    "xG_p90": 0.89,
    "xA_p90": 0.01,
    "xGC_p90": 1.36,
    "bonus_per_start": 1.80,
    "saves_per_90": 0.0,
    "penalties_order": 1,
    "fixture_difficulty": 4,
    "is_home": false
  },

  "drivers": [
    "Nailed starter — 5/5 starts, 450 minutes",
    "Elite xGI rate — 0.99 per 90",
    "Confirmed penalty taker"
  ],

  "risks": [
    "Difficult fixture — away, difficulty 4",
    "Poor clean sheet odds — high-scoring team context"
  ]
}
```

**Drivers logic:** A driver is surfaced when a component meaningfully contributes to a HIGHER xPts than the positional average. A risk is surfaced when a component meaningfully reduces xPts or increases uncertainty.

Driver/risk thresholds (to implement):
- xG_p90 > 0.40 → "strong xG rate"
- xA_p90 > 0.20 → "strong xA rate"
- p_cs_adj > 0.40 → "good CS prospect"
- xMins < 70 → "minutes uncertainty"
- confidence == LOW → "uncertain availability"
- fixture_difficulty >= 4 → "difficult fixture"
- fixture_difficulty <= 2 → "favourable fixture"
- penalties_order == 1 → "penalty taker"
- cop_next <= 75 → "injury doubt"

---

## 16. Missing Data Handling

| Scenario | Handling |
|----------|---------|
| `expected_goals_per_90` is 0 for a player who has played | Use damped positional baseline. A GKP with 0 xG is normal; a FWD with 0 xG after 5 starts is a real signal — do not replace it with a large baseline. |
| `expected_goals_conceded_per_90` is 0 | Defensive data not yet populated. Use positional midpoint: {1: 1.0, 2: 1.0}. Flag as LOW confidence. |
| `chance_of_playing_next_round` is null | Treat as 100% available. Null = no doubt, not unknown. Confirmed by API inspection. |
| `saves_per_90` is 0 for a GKP | Not yet played (or transferred in). Fall back to league-average saves rate: ~2.5 saves per 90. |
| `bonus` is 0 and `starts` > 0 | Player has genuinely earned 0 bonus. xBonus uses damped baseline. Do not artificially inflate. |
| `penalties_order` is null | Not a penalty taker. No bonus applied. |
| GW fixture not found | Player has no game (blank GW). xPts = 0. |
| `history` has no records | New player with no current-season data. See Section 17. |

---

## 17. New Player Handling

A "new player" is defined as: `team_join_date` within the last 28 days OR `history` in element-summary contains 0 records.

```
// New player protocol
if is_new_player:
    // Step 1: Check history_past for prior season data (OPTIONAL)
    if history_past is not empty:
        prior = most_recent entry in history_past
        prior_xG_p90 = prior.expected_goals / (prior.minutes / 90)
        prior_xA_p90 = prior.expected_assists / (prior.minutes / 90)
        // Use prior rates, heavily damped (w = 0.2 max)
        xG_p90 = 0.2 × prior_xG_p90 + 0.8 × pos_xG_base[position]
        xA_p90 = 0.2 × prior_xA_p90 + 0.8 × pos_xA_base[position]
    else:
        // No prior data at all
        xG_p90 = pos_xG_base[position]
        xA_p90 = pos_xA_base[position]

    // Minutes: use positional default, heavily penalised
    xMins = positional_default_new[position]  // {1: 60, 2: 55, 3: 50, 4: 45}
    confidence = LOW
```

Do NOT use a new player's first 1–2 GW stats directly. Wait for 3+ GW appearances before their current-season data dominates.

A player who transferred from another club mid-season: their `history` will contain their new club's GW records. If `team_join_date` is within 3 GWs, treat them as a new player with prior-season context.

---

## 18. Sanity Checks

Automated checks to run before returning any xPts value.

### Minutes Checks
- A player with `starts = 5, minutes = 450` (nailed) should have `xMins >= 80`. If not, check the recent-minute computation.
- A player with `status = 'i', chance_of_playing_next_round = 0` must have `xMins = 0`.
- `xMins` must never exceed 90.
- A player with 0 recent starts (last 3 GWs) must have `xMins <= 65`.

### Fixture Checks
- A player with `fixture_difficulty = 1` (easiest fixture) should have a higher `xAtk` than the same player with `fixture_difficulty = 5`, all else equal.
- Blank GW (0 fixtures) must produce `xPts = 0`.
- Double GW (2 fixtures) must produce `xPts > single GW xPts` for the same player.

### Position Checks
- A GKP with `xGC_p90 = 0.8` should have `xDef > 0` (CS + save contribution).
- A FWD should have `xDef == 0` (no CS contribution).
- A MID's `goal_pts = 5` while FWD's = 4. A MID with identical xG to a FWD should produce slightly higher xAtk from goal points.

### Sample Size Checks
- A player with `starts = 1, goals_scored = 2` in that single game should NOT produce an extreme xAtk. The damping formula must reduce the xG rate toward the positional baseline.
- A player who has played 8 GWs should have higher-weight actual rates and lower damping than a 2-GW player.

### Range Checks
- `xPts` floor is 0.0 (never negative in output, even if xDef is large negative for bad defensive record).
- `xBonus` must not exceed 3.0.
- `p_cs_adj` must not exceed 0.85 (some residual uncertainty always exists).

### Consistency Checks
- A player with `confidence = HIGH` must have `status = 'a'`, `chance_of_playing_next_round = null`, and `n_gws_played >= 4`.
- A player with `xMins = 0` must have `xPts = 0`.

---

## 19. Known Limitations

### Early Season (GWs 1–8)
The model degrades gracefully as `n_gws_played` decreases. Confidence levels correctly flag uncertainty. Do not expect accurate absolute xPts values at GW6 — the strength is in relative rankings and component transparency.

### Defensive xGC Calibration
The clean sheet probability formula (`p_cs = 0.75 - xGC_p90 × 0.40`) is a reasonable approximation but uncalibrated. It needs validation against actual CS rates across the season. Treat the absolute xDef values as illustrative until validated at GW10+.

### Fixture Difficulty Reliability
The FPL difficulty rating is assigned pre-season and does not update dynamically. A team that improves significantly (or collapses) mid-season will have stale difficulty ratings. At V0.1, this is acceptable. Mitigation: consider using a team's actual current-season xGC and xGI (derived from player data) as a secondary fixture signal in V0.2.

### Bonus Noise
At 5 GWs, bonus per start has enormous variance. The model's damped bonus estimate will be systematically imprecise. Accept this and flag bonus as the weakest component. Post-GW validation will reveal whether systematic bias exists.

### Attack/Defence Strength Split
The FPL API provides no split between team attack and defence strength. A team with a dominant attack but poor defence (e.g. a team that wins 3-2 regularly) will have ambiguous overall strength. This affects both the attacking and defensive adjustments. V0.2 should derive team attack/defence ratings from aggregated player xGI and xGC.

### Lineups Not Known Before Deadline
The model assumes a player's recent starts are predictive of their next start. This assumption breaks when managers rotate aggressively, rest players for European games, or return an injured player to a different role. The `news` and `chance_of_playing_next_round` fields help but do not cover tactical rotation.

### Transfers In (New to Current Team)
A player who has transferred clubs within the last 28 days has limited current-team data. Prior-season data is at a different club under a different manager. Confidence will correctly be LOW but the xPts estimate may be unreliable in either direction.

---

## 20. What to Build Next

### Immediate (enables V0.1 to run)

1. **Data fetcher** (`fpl/engine/fetch.py`)
   - Fetch bootstrap-static: 1 request
   - For each player in user's squad: fetch element-summary: 15 requests
   - Return structured dicts with exactly the fields needed by the model
   - Cache bootstrap-static for 30 minutes (avoids hammering the API)

2. **xPts calculator** (`fpl/engine/projection.py`)
   - Implement the pseudocode from Section 14
   - Returns the output schema from Section 15
   - Pure functions, no side effects, fully testable

3. **Sanity check runner** (`fpl/engine/checks.py`)
   - Implement the automated checks from Section 18
   - Raises warnings (not errors) for violated checks
   - Log violations to console during development

4. **Unit test cases** (`fpl/engine/tests/`)
   - Haaland (FWD, nailed, penalty taker, difficult fixture) → expect xPts ~7–9
   - Raya (GKP, nailed, easy fixture) → expect xPts ~5–8
   - Injured player with cop=0 → expect xPts = 0
   - New player → expect LOW confidence, positional baseline output

### Next (V0.2, after validation)

5. **xPts vs actual validation** — After each GW, compare `xPts` to `total_points` from the live endpoint. Track RMSE per position. This replaces the need for historical backtesting.

6. **Derived team attack/defence ratings** — Aggregate player xGI and xGC by team to replace the missing `strength_attack/defence` fields.

7. **Fixture difficulty recalibration** — Replace pre-season difficulty ratings with current-season derived strength when > 8 GWs have been played.

8. **Squad-level xPts** — Sum individual player xPts for a user's expected XI. This feeds the diagnosis layer (strengths, weaknesses, captain recommendation).

---

## Summary

### What We Can Reliably Get from the FPL API
- Player availability (status, cop_next, news) — **immediately reliable**
- Season-to-date xG, xA, xGI, xGC (all per-90) — **reliable, 5 GWs at GW6**
- Per-GW history (minutes, starts, goals, xG, xA, bonus) — **5 records at GW6**
- Upcoming fixture difficulty and home/away — **reliable**
- Set-piece order (especially penalties_order) — **reliable**
- Team overall strength (1–5 scale only) — **available but coarse**

### What We Cannot Reliably Get
- Team attack/defence strength split — **all 0 in live API**
- Opponent-specific fixture xG — **not in API**
- Lineup confirmation before deadline — **not available**
- Manager rotation patterns programmatically — **must infer from minutes history**

### Proposed V0.1 Model
A transparent, rules-based model with five components: **xApp + xAtk + xDef + xBonus** with fixture adjustment. All inputs from the FPL API. Damped xG/xA rates to account for early-season small samples. Confidence (HIGH/MEDIUM/LOW) as a first-class output. Output schema preserves all components for explanation and future recommendation layer.

### Minimum Data Needed to Build
16 API calls per user squad check: 1 × bootstrap-static + 15 × element-summary. No database, no auth, no third-party data. A working prototype can be built in a single Python script.

### Major Risks
1. **Small sample distortion** — At GW6 (5 games), one explosive GW can inflate per-90 rates significantly. Mitigated by damping toward positional baseline, but the calibration of that damping needs empirical validation.
2. **Clean sheet probability calibration** — The CS model is unvalidated. Early results may show systematic over/under-estimation for specific teams. Needs GW-by-GW comparison to actuals.
3. **Fixture difficulty staleness** — The pre-season difficulty ratings may not reflect current team form. Teams that have improved or declined significantly will generate incorrect fixture adjustments. Watch for systematic patterns after 8+ GWs.
4. **No lineup data** — Rotation risk is the biggest single source of xPts error in FPL. A player projected for 85 xMins who gets rested contributes 0 points. The model cannot eliminate this risk; it can only communicate it via LOW confidence when rotation signals are present.

### Exact Next Implementation Step
Build `fpl/engine/fetch.py` and `fpl/engine/projection.py`:

```python
# fpl/engine/fetch.py
def fetch_squad_data(player_ids: list[int]) -> dict:
    # 1. GET /api/bootstrap-static/ → cache as bootstrap
    # 2. For each id: GET /api/element-summary/{id}/ → player_summaries[id]
    # 3. Return {bootstrap, player_summaries}

# fpl/engine/projection.py
def compute_xPts(player_id: int, gw: int, data: dict) -> dict:
    # Implements Section 14 pseudocode exactly
    # Returns the schema from Section 15
```

This is a 200–300 line pure Python implementation with no dependencies beyond the standard library and `requests`. It can be tested against the live API immediately and its outputs compared to ep_next and actual results as GWs progress.
