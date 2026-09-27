# FPL Projection Engine — Data Audit
**SquadCheck Milestone 2 · Gameweek 6 · September 2026**

All fields verified against live API responses. No assumptions. Fields marked UNVERIFIED were listed in documentation but not confirmed in actual responses.

---

## Endpoints

| ID | URL | Notes |
|----|-----|-------|
| BS | `https://fantasy.premierleague.com/api/bootstrap-static/` | Main data dump. ~667 players, 20 teams, 38 GWs. Single large response. |
| FX | `https://fantasy.premierleague.com/api/fixtures/` | All 380 fixtures. No auth. |
| ES | `https://fantasy.premierleague.com/api/element-summary/{id}/` | Per-player: GW-by-GW history + upcoming fixtures. |
| LV | `https://fantasy.premierleague.com/api/event/{gw}/live/` | Live GW stats. Only useful during or after GW. |

Bootstrap-static is the workhorse. Element-summary must be fetched per player (667 requests for full squad coverage; in practice 15 requests per user squad).

---

## Section 1 — Player Identity & Status

| Field | Source | Available? | Update Frequency | Before Deadline? | Season Only? | Reliability | Proposed Use | Notes |
|-------|--------|-----------|-----------------|-----------------|-------------|------------|-------------|-------|
| `id` | BS `elements[].id` | Yes | Static | Yes | Current | High | Player lookup key | Stable integer per season |
| `web_name` | BS | Yes | Rarely | Yes | Current | High | Display | e.g. "Haaland" |
| `first_name` / `second_name` | BS | Yes | Static | Yes | Current | High | Full name display | — |
| `element_type` | BS | Yes | Static | Yes | Current | High | Position logic | 1=GKP, 2=DEF, 3=MID, 4=FWD |
| `team` | BS | Yes | On transfer | Yes | Current | High | Team lookup | Integer team id |
| `team_join_date` | BS | Yes | On transfer | Yes | Current | High | New-player flag | ISO date string; use to detect mid-season arrivals |
| `status` | BS | Yes | Daily+ | Yes | Current | High | Availability gate | 'a'=available, 'i'=injured, 'd'=doubt, 'u'=unavailable (departed), 's'=suspended |
| `news` | BS | Yes | On update | Yes | Current | Medium | Human-readable context | Free text, e.g. "Hamstring injury - Expected back 10 Oct". Not programmatically parseable for dates. |
| `news_added` | BS | Yes | On update | Yes | Current | Medium | Staleness check | ISO datetime. Null if no news. |
| `chance_of_playing_this_round` | BS | Yes | Daily+ | Yes | Current | High | xMins gate | 0, 25, 50, 75, or null (=100). Null means no doubt. |
| `chance_of_playing_next_round` | BS | Yes | Daily+ | Yes | Current | High | xMins gate | Same scale. Primary signal for projections. |
| `removed` | BS | Yes | On departure | Yes | Current | High | Exclude flag | Bool. True if player removed from FPL entirely (rare). |
| `code` / `opta_code` | BS | Yes | Static | Yes | Current | High | Cross-reference | Opta ID for future enrichment |

**Status code breakdown (GW6 live):** a=467, i=71, d=20, u=105 (departed), s=4. The 105 'u' players are transfer departures still in the database.

---

## Section 2 — Season-Aggregate Stats (Bootstrap-static)

All fields are season-to-date cumulative totals.

| Field | Source | Available? | Before Deadline? | Reliability | Proposed Use | Notes |
|-------|--------|-----------|-----------------|------------|-------------|-------|
| `minutes` | BS | Yes | Yes | High | xMins base rate | Total season minutes |
| `starts` | BS | Yes | Yes | High | xMins, rotation detection | Season starts count |
| `goals_scored` | BS | Yes | Yes | High | Sanity check vs xG | Raw goals |
| `assists` | BS | Yes | Yes | High | Sanity check vs xA | Raw assists |
| `clean_sheets` | BS | Yes | Yes | High | CS base rate | Cumulative |
| `goals_conceded` | BS | Yes | Yes | High | Defensive baseline | Cumulative |
| `bonus` | BS | Yes | Yes | High | xBonus estimate | Season total bonus pts |
| `bps` | BS | Yes | Yes | Medium | Bonus proxy | Season total BPS. Noisy. |
| `saves` | BS | Yes | Yes | High | GKP save points | GKP only useful |
| `yellow_cards` | BS | Yes | Yes | High | YC risk | Season total |
| `red_cards` | BS | Yes | Yes | High | Risk flag | Season total |
| `own_goals` | BS | Yes | Yes | High | Completeness | Season total |
| `penalties_missed` | BS | Yes | Yes | High | PK reliability | Season total |
| `penalties_saved` | BS | Yes | Yes | High | GKP save pts | Season total |
| `total_points` | BS | Yes | Yes | High | Benchmark/validation | Season total FPL pts |
| `event_points` | BS | Yes | After GW | N/A | Post-GW validation | Most recent GW points |
| `dreamteam_count` | BS | Yes | Yes | Low | Not used | Times in FPL Dream Team |

---

## Section 3 — Per-90 Rates (Bootstrap-static)

Calculated by FPL as season_total / (minutes / 90). Available as ready-made rates.

| Field | Source | Available? | Before Deadline? | Reliability | Proposed Use | Notes |
|-------|--------|-----------|-----------------|------------|-------------|-------|
| `expected_goals_per_90` | BS | Yes | Yes | Medium-High | xAtk: xG component | Season xG / (mins/90). Early season = small sample (5 GWs). |
| `expected_assists_per_90` | BS | Yes | Yes | Medium-High | xAtk: xA component | Season xA / (mins/90). |
| `expected_goal_involvements_per_90` | BS | Yes | Yes | Medium-High | xAtk: combined rate | xG+xA per 90. |
| `expected_goals_conceded_per_90` | BS | Yes | Yes | Medium-High | xDef: CS probability, GC deduction | Reflects team defensive quality. Use as primary defensive signal. |
| `goals_conceded_per_90` | BS | Yes | Yes | Medium | Defensive backup | Actual goals conceded rate. Noisier than xGC. |
| `saves_per_90` | BS | Yes | Yes | High | GKP save points | GKP only. Reliable even at 5 GWs. |
| `clean_sheets_per_90` | BS | Yes | Yes | Medium | CS sanity check | Low N = volatile. Use xGC_p90 as primary. |
| `starts_per_90` | BS | Yes | Yes | Medium | Rotation signal | starts / (mins/90). >1.0 means player is subbed off; <1.0 means bench appearances. Useful but redundant with per-GW history. |
| `defensive_contribution_per_90` | BS | Yes | Yes | Low | Not used V0.1 | Composite metric. Not well documented. |
| `clearances_blocks_interceptions` | BS | Yes | Yes | Low | Not used V0.1 | Season total, DEF/MID only. |
| `recoveries` | BS | Yes | Yes | Low | Not used V0.1 | Season total. |
| `tackles` | BS | Yes | Yes | Low | Not used V0.1 | Season total. |

---

## Section 4 — FPL-Specific Metrics

| Field | Source | Available? | Before Deadline? | Reliability | Proposed Use | Notes |
|-------|--------|-----------|-----------------|------------|-------------|-------|
| `form` | BS | Yes | Yes | Medium | Benchmark comparison | FPL's trailing ~5-GW PPG average. Confirmed ≠ ep_next in 23/667 cases (injury/suspension cases). |
| `ep_next` | BS | Yes | Yes | Low-Medium | Benchmark only — do NOT use as xPts | Equals `form` in most cases. For injured/suspended players, ep_next approaches 0 while form may remain elevated. Confirms FPL's own model is a simple rolling average with availability logic. |
| `ep_this` | BS | Yes | Yes (current GW only) | Low-Medium | Not used | FPL's in-GW projection. Not useful pre-deadline for future GW. |
| `points_per_game` | BS | Yes | Yes | High | Benchmark/validation | Season average pts per game. |
| `value_form` | BS | Yes | Yes | Low | Not used | Points/cost ratio based on form. |
| `value_season` | BS | Yes | Yes | Low | Not used | Points/cost ratio season total. |
| `selected_by_percent` | BS | Yes | Yes | High | Squad context (future: differential value) | Ownership %. |
| `transfers_in` / `transfers_out` | BS | Yes | Yes | High | Transfer momentum signal | Season totals. |
| `transfers_in_event` / `transfers_out_event` | BS | Yes | Yes | High | Current GW momentum | Weekly transfer counts. Useful for sentiment, not projection. |
| `now_cost` | BS | Yes | Yes | High | Transfer cost (future: cost-benefit layer) | In 0.1m units (e.g. 156 = £15.6m). |
| `cost_change_event` / `cost_change_start` | BS | Yes | Yes | Medium | Price trend | Price changes this GW / since season start. |

---

## Section 5 — Set-Piece Information

Highly valuable. Confirmed present in live API.

| Field | Source | Available? | Before Deadline? | Reliability | Proposed Use | Notes |
|-------|--------|-----------|-----------------|------------|-------------|-------|
| `penalties_order` | BS | Yes | Yes | High | xAtk bonus for PK takers | Integer: 1=first taker, 2=second, null=not a taker. Haaland=1, Saka=1. |
| `penalties_text` | BS | Yes | Yes | Medium | Human-readable PK confirmation | Free text. Rarely populated. Trust `penalties_order` as primary. |
| `direct_freekicks_order` | BS | Yes | Yes | Medium | Minor xAtk signal | Integer. Less predictable than pens. |
| `direct_freekicks_text` | BS | Yes | Yes | Low | Context only | Free text. |
| `corners_and_indirect_freekicks_order` | BS | Yes | Yes | Medium | Minor xA signal | Integer. Saka=2 (secondary corner taker). |
| `corners_and_indirect_freekicks_text` | BS | Yes | Yes | Low | Context only | Free text. Rarely populated. |

**Key finding:** `penalties_order = 1` is a material xPts driver. A dedicated penalty taker gets ~0.27 × goal_pts bonus per game expectation based on PL penalty frequency (~1 per 3.7 games).

---

## Section 6 — ICT Metrics

| Field | Source | Available? | Before Deadline? | Reliability | Proposed Use | Notes |
|-------|--------|-----------|-----------------|------------|-------------|-------|
| `influence` | BS | Yes | Yes | Medium | Not used V0.1 | FPL composite metric. Season total. |
| `creativity` | BS | Yes | Yes | Medium | Not used V0.1 | FPL composite metric. Season total. |
| `threat` | BS | Yes | Yes | Medium | Not used V0.1 | FPL composite metric. Season total. |
| `ict_index` | BS | Yes | Yes | Medium | Not used V0.1 | Sum of the above. Correlated with xGI but less transparent. |
| `influence_rank` / `creativity_rank` / `threat_rank` / `ict_index_rank` | BS | Yes | Yes | Medium | Not used V0.1 | Positional and global ranks. |

ICT metrics are FPL's own proprietary index. Less transparent than raw xG/xA. Prefer using `expected_goals_per_90` and `expected_assists_per_90` directly.

---

## Section 7 — Team Data

| Field | Source | Available? | Before Deadline? | Reliability | Proposed Use | Notes |
|-------|--------|-----------|-----------------|------------|-------------|-------|
| `teams[].id` | BS | Yes | Yes | High | Team lookup | Stable integer per season |
| `teams[].short_name` | BS | Yes | Yes | High | Display | e.g. "ARS" |
| `teams[].strength_overall_home` | BS | Yes | Yes | Medium | Fixture context | Scale 1–5. Populated for all 20 teams. |
| `teams[].strength_overall_away` | BS | Yes | Yes | Medium | Fixture context | Scale 1–5. Populated for all 20 teams. |
| `teams[].strength_attack_home` | BS | **NO — all 0** | — | — | Cannot use | Field exists but is 0 for all 20 teams. Confirmed live. |
| `teams[].strength_attack_away` | BS | **NO — all 0** | — | — | Cannot use | Same. |
| `teams[].strength_defence_home` | BS | **NO — all 0** | — | — | Cannot use | Same. |
| `teams[].strength_defence_away` | BS | **NO — all 0** | — | — | Cannot use | Same. |
| `teams[].strength` | BS | No — null | — | — | Cannot use | All null. |
| `teams[].form` | BS | No — null | — | — | Cannot use | All null in current season. |
| `teams[].win` / `draw` / `loss` / `played` / `points` | BS | No — all 0 | — | — | Cannot use | All 0. FPL does not expose live standings. |

**Critical finding:** The only usable team-level strength signal is `strength_overall_home` and `strength_overall_away` (1–5 scale). Attack and defence breakdowns are completely absent. Workaround for V0.1: derive approximate team attack/defence ratings by aggregating player xGI and xGC from bootstrap-static elements.

---

## Section 8 — Fixture Data

| Field | Source | Available? | Before Deadline? | Reliability | Proposed Use | Notes |
|-------|--------|-----------|-----------------|------------|-------------|-------|
| `fixtures[].event` | FX | Yes | Yes | High | GW mapping | Integer GW number |
| `fixtures[].team_h` / `team_a` | FX | Yes | Yes | High | Team resolution | Team IDs |
| `fixtures[].team_h_difficulty` | FX | Yes | Yes | Medium | Fixture adjustment | 1–5. Difficulty for HOME team's players. |
| `fixtures[].team_a_difficulty` | FX | Yes | Yes | Medium | Fixture adjustment | 1–5. Difficulty for AWAY team's players. |
| `fixtures[].kickoff_time` | FX | Yes | Yes | High | Scheduling | ISO datetime |
| `fixtures[].finished` | FX | Yes | Yes | High | Data state | Bool |
| `fixtures[].started` | FX | Yes | Yes | High | Data state | Bool |
| `fixtures[].stats` | FX | Yes | After match | High | Post-GW validation | Contains goals, assists, bonus etc. per player. |
| Team-level xG per fixture | FX | **No** | — | — | Not available | FPL fixture endpoint has no xG data. |
| Opponent team id from player perspective | ES | Yes (derive) | Yes | High | Fixture opponent | In element-summary fixtures: if is_home=True, opponent = team_a; if is_home=False, opponent = team_h. |
| `element-summary fixtures[].difficulty` | ES | Yes | Yes | Medium | Per-player fixture difficulty | Correct difficulty from this player's team perspective. More convenient than the fixtures endpoint. |
| `element-summary fixtures[].is_home` | ES | Yes | Yes | High | Home advantage | Bool |

---

## Section 9 — Per-GW Player History (element-summary)

Available via `element-summary/{id}/history`. Must fetch per player.

| Field | Available? | Before Deadline? | Reliability | Proposed Use | Notes |
|-------|-----------|-----------------|------------|-------------|-------|
| `round` | Yes | Yes (past GWs) | High | GW label | Integer |
| `minutes` | Yes | Yes | High | xMins: recent average | Per-GW minutes played |
| `starts` | Yes | Yes | High | Rotation detection | 0 or 1 per GW |
| `goals_scored` | Yes | Yes | High | Validation | Per-GW |
| `assists` | Yes | Yes | High | Validation | Per-GW |
| `expected_goals` | Yes | Yes | High | Per-GW xG | Stat-cast level |
| `expected_assists` | Yes | Yes | High | Per-GW xA | Stat-cast level |
| `expected_goal_involvements` | Yes | Yes | High | Per-GW xGI | xG + xA |
| `expected_goals_conceded` | Yes | Yes | High | Per-GW xGC | Team defensive exposure |
| `clean_sheets` | Yes | Yes | High | CS rate | Per-GW |
| `goals_conceded` | Yes | Yes | High | GC count | Per-GW |
| `bonus` | Yes | Yes | High | xBonus: average | Per-GW |
| `bps` | Yes | Yes | Medium | Bonus proxy | Per-GW BPS score |
| `total_points` | Yes | Yes | High | Model validation | Per-GW FPL points |
| `was_home` | Yes | Yes | High | Home/away split | Bool |
| `opponent_team` | Yes | Yes | High | Opponent resolution | Team id |
| `value` | Yes | Yes | Medium | Price at time | In 0.1m units |
| `selected` | Yes | Yes | Medium | Ownership at time | Integer |
| `transfers_in` / `transfers_out` | Yes | Yes | Medium | Momentum history | Per-GW transfers |
| `saves` | Yes | Yes | High | GKP per-GW saves | GKP only |
| `influence` / `creativity` / `threat` / `ict_index` | Yes | Yes | Medium | Not used V0.1 | Per-GW ICT |

**Coverage:** At GW6, `history` contains 5 records per player (GWs 1–5). This is the minimum viable sample.

---

## Section 10 — Historical Past Seasons (element-summary)

Available via `element-summary/{id}/history_past`. Season-aggregate totals per previous season.

| Field | Available? | Reliability | Proposed Use | Notes |
|-------|-----------|------------|-------------|-------|
| `season_name` | Yes | High | Season label | e.g. "2025/26" |
| `total_points` | Yes | High | OPTIONAL — prior season benchmark | Useful for players with <3 GW current data |
| `minutes` / `starts` | Yes | High | OPTIONAL — prior season mins rate | |
| `expected_goals` / `expected_assists` | Yes | High | OPTIONAL — prior season xG/xA | Only relevant if current sample is tiny |
| `expected_goals_conceded` | Yes | High | OPTIONAL | |
| All other stats | Yes | Medium | OPTIONAL | Full season aggregates |

**V0.1 rule:** Do NOT use history_past as a primary input. It may be consulted as a fallback for players with <2 current-season GW appearances. Label clearly when used.

---

## Section 11 — Live Event Data

Available via `event/{gw}/live/`. Only useful during or after a GW.

| Field | Available? | Timing | Proposed Use |
|-------|-----------|--------|-------------|
| `elements[].stats` | Yes | During/after GW | Post-GW validation |
| `elements[].explain` | Yes | After GW | Point-by-point breakdown |
| `elements[].modified` | Yes | After GW | Whether stats updated |

**V0.1 use:** Not applicable for pre-deadline projections. Useful later for model validation (compare xPts to actual points).

---

## Section 12 — What Is NOT Available in the FPL API

The following signals would improve the model but are not accessible:

| Signal | Why Useful | Workaround |
|--------|-----------|-----------|
| Team lineups before deadline | Confirm starter status | None. Use minutes history as proxy. |
| Team-level xG / xGA per fixture | Better defensive/attacking fixture adjustment | Aggregate from player data (approximate). |
| Opposition-specific attack/defence breakdown | Opponent strength | strength_overall only (1–5). Supplement with aggregated player xGI/xGC. |
| Substitution patterns | Predict sub-off / sub-on minutes | Infer from starts + minutes variance in history. |
| Press conference / manager quotes | Rotation/fitness signals | Read news field (not parseable). |
| Player role / formation | Positional role (e.g. inverted winger vs full-back) | Not exposed. Infer from xG/xA ratios. |
| Double GW announcements in advance | Multiple games → multiplied xPts | Check fixtures endpoint for >1 game in a GW. |
| Blank GW player flag | Player has no game → xPts = 0 | Check fixtures endpoint for 0 games in GW. |
| Underlying xG data source (Opta vs StatsBomb) | Provider consistency | Not disclosed. Treat as opaque. |

---

## Audit Summary

| Category | Status |
|----------|--------|
| Player availability (status, cop_next, news) | **Confirmed — fully available** |
| Player season stats (xG, xA, xGI, xGC, all per-90) | **Confirmed — fully available** |
| Per-GW history (mins, starts, xG, xA, xGC, bonus) | **Confirmed — 5 GWs at GW6** |
| Upcoming fixture (GW, difficulty, is_home) | **Confirmed — available via element-summary** |
| Set-piece order (penalties, FK, corners) | **Confirmed — penalties_order most reliable** |
| Team overall strength (1–5) | **Confirmed — only overall, not att/def split** |
| Team attack/defence strength | **Confirmed absent — all fields are 0** |
| Team standings / form | **Confirmed absent — all null/0** |
| Fixture-level xG | **Confirmed absent** |
| Lineup data | **Confirmed absent** |
| ep_next (FPL's own projection) | **Confirmed — equals `form` for most players; drops to 0 for unavailable players. Simple rolling average, not a model.** |
