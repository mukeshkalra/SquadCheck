# SquadCheck — V2+ Idea Log

Ideas recorded here are explicitly **not part of the current V1 implementation**.
V1 scope is frozen. Do not expand it without deliberate decision.

---

## V2 Priority Ideas

### Chip Optimizer — HIGH PRIORITY
The major V2 decision engine. FPL chips (Wildcard, Free Hit, Bench Boost, Triple Captain) are
high-stakes, irreversible decisions that most managers get wrong. A chip optimizer would:

- Use the manager's actual chip inventory and history (FPL API: `game_settings`, manager endpoint)
- Model opportunity cost across multi-GW fixture runs
- Factor in chip sequencing (e.g., BB before Triple Captain, Wildcard timing)
- Account for squad state — chips are less valuable when the squad is already strong
- Identify fixture swings across GW windows
- Return a concrete "use chip / hold chip" recommendation with reasoning

**Why high priority:** chip decisions have the highest single-GW variance in FPL. A manager who
correctly times a Bench Boost or Triple Captain vs a manager who wastes one can differ by
40–60 pts in rank. No existing tool does this well. The projection engine already produces
per-player xPts that feed directly into chip value calculation.

**Data needed:** manager history endpoint (`/api/entry/{id}/history/`), upcoming fixtures
(already available), chip inventory per manager.

---

### Multi-Gameweek Outlook
After the current-GW story, optionally surface how the existing squad looks over the next
2–4 Gameweeks — fixtures, injury risk, rotation patterns.

**Principle:** surface meaningful changes, do not create more homework. Use real V1 engagement
data to decide how many GWs users actually want to see before building.

**Why not V1:** we do not yet know if users want multi-GW depth or if it adds cognitive load
without adding value. Measure first.

---

### Viral / Distribution Loops — HIGH PRIORITY
FPL has ~10M managers globally. SquadCheck needs to reach them where they already spend time.
This is a major product workstream, not a single feature.

Ideas in scope for future prioritization:
- **Shareable result cards** — generated image cards users can post on social media.
  Format: "My FPL team / GW{N} / {X} pts projected / SquadCheck badge"
- **Referral mechanics** — "Check a friend's team" with simple share link
- **Reddit / Discord / WhatsApp integration** — distribution through existing FPL communities
- **FPL Mini-League context** — "vs your league average" framing for results

**V1 share is a start:** V1 ships native share (text + URL). Full social card generation
and referral mechanics are V2.

**Why not V1:** build the core value first. Distribution loops without a product worth sharing
produce nothing. Get the product right, then amplify.

---

### User-Feedback-Driven Iteration
V1 ships with in-product feedback (❤️/👍/😐/👎) and behavioural analytics. Before any V2
feature gets built, evaluate:

- What are users doing after seeing the result? (kept XI / changed XI / still deciding)
- Where do users drop off in the feed? (scroll depth per section)
- What negative feedback patterns emerge?
- Which positive signals are most reliable predictors of "this felt useful"?

**Principle:** the V1 feedback and analytics instrumentation exists specifically to answer these
questions. Do not prioritize features on instinct when data is available.

---

## Explicitly Out of Scope (V1 and V2 unless deliberately decided)

- Transfer recommendations (the model cannot know the user's bank balance, transfer cost, or bench value)
- Captain optimizer (requires understanding manager preferences and risk tolerance)
- Injury tracker / fitness news aggregation
- User accounts / login
- Social network / manager profiles
- Multi-manager comparisons
- Draft FPL support
- Historical season data or backtesting features

---

*Last updated: V1 launch preparation*
