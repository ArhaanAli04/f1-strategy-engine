# Core Feature Rebuild — Real-Time Strategy Recommendations

> **Status:** Investigation only, 2026-09-04. Nothing in this document has
> been implemented or fixed. This is a scoping document for a future
> dedicated session (or several) to rebuild the project's originally
> planned core feature, which — as documented below — does not currently
> exist as a coherent thing, even though its individual pieces (tire_deg
> models, pit_predictor, SHAP explainability) are all real and working on
> their own terms.
>
> Produced investigating two follow-on questions from the item-5 encoding
> fix session (`docs/day-deferred-fixes-session2-handoff.md`): (1) could
> the crc32 encoding bug explain Day 43's near-universal low
> `pit_probability` observations — answer: no, and the bug's measured
> effect actually pushed the opposite direction; (2) does the *current*
> system actually deliver the project's original core-feature vision —
> answer: no, and not narrowly either. This document is the writeup of
> finding (2).

---

## 1. Original Vision

From early project planning, verbatim:

> **Real-Time Strategy Recommendations (The Core Feature)**
>
> What the user sees: During a live race, for any driver they're
> watching, they see:
> - "Optimal pit window: Lap 31-34 — confidence 71%"
> - "Recommended compound after pit: MEDIUM"
> - Why this recommendation: "Tyre age 24 laps, degradation accelerating,
>   gap to P4 behind is 8.2s — safe to pit"
>
> This updates automatically after every lap. No button press needed.
> The user just watches the race and the recommendations evolve in real
> time alongside it.

Five distinct elements to hold the current system against:

1. A **narrow lap-range** recommendation (a 4-lap window, not a search
   horizon).
2. A **confidence score** (a percentage).
3. A **compound recommendation** for the stint after the pit.
4. A **multi-factor plain-English explanation**, explicitly including
   gap-to-rival reasoning ("gap to P4 behind is 8.2s").
5. **Automatic per-lap updates**, no user action, during a live race.

---

## 2. What Currently Exists (honest inventory)

Two separate, never-reconciled mechanisms answer overlapping-sounding
questions. Neither one alone delivers the vision, and — critically — the
system picks the weaker of the two specifically during the scenario the
vision describes (a live race).

### 2a. `GET /strategy/{session_id}/{driver_id}/pit-window`

Backed by `strategy_service.get_optimal_pit_window` /
`get_pit_window_with_explanation` (`backend/services/strategy_service.py`),
schema `PitWindowResponse` (`backend/schemas/strategy_schema.py`), rendered
by `PitWindowCard.tsx` (`web/src/components/strategy/PitWindowCard.tsx`).
Uses the tire_deg models directly (`_project_stint_delta`), one candidate
per lap across a fixed 15-lap search horizon
(`PIT_WINDOW_LOOKAHEAD_LAPS = 15`), ranked by projected total time delta.

**Real evidence, gathered today** — LEC, Belgian GP 2026 R10, real pit was
lap 21 (MEDIUM→HARD). Queried as-of lap 17 (their real state at that
point: MEDIUM, tyre age 17) by exercising the actual unmodified
`get_pit_window_with_explanation` code (monkeypatching only the DB-bound
"driver's absolute latest lap" lookup to an as-of-lap-17 snapshot, since
this session is fully historically ingested and the real endpoint always
resolves to the driver's *final* race lap — itself a symptom of the same
disconnect from "live," see 2c below). Real response:

```json
[
  {
    "pit_lap": 32,
    "window_start": 18,
    "window_end": 32,
    "projected_total_delta_seconds": -19.925,
    "shap_explanation": [
      {"feature_name": "fuel_adjusted_time", "value": -2.4, "contribution": -1.004, "direction": "-"},
      {"feature_name": "circuit_id_encoded", "value": 11.0, "contribution": -0.775, "direction": "-"},
      {"feature_name": "driver_id_encoded", "value": 42.0, "contribution": 0.230, "direction": "+"},
      {"feature_name": "lap_number", "value": 32.0, "contribution": -0.155, "direction": "-"},
      {"feature_name": "tyre_age_laps", "value": 32.0, "contribution": -0.096, "direction": "-"}
    ]
  },
  {"pit_lap": 31, "window_start": 18, "window_end": 32, "projected_total_delta_seconds": -19.641, "shap_explanation": null},
  {"pit_lap": 30, "window_start": 18, "window_end": 32, "projected_total_delta_seconds": -17.556, "shap_explanation": null}
]
```

What this shows, concretely:

- **`window_start`/`window_end` are identical across all 3 candidates** —
  they're `state["lap_number"] + 1` and `min(state["lap_number"] +
  PIT_WINDOW_LOOKAHEAD_LAPS, total_laps)`, i.e. the *search horizon*, not
  a per-candidate range. `PitWindowCard.tsx` renders this pair as the
  card's giant headline (`Lap {window_start}–{window_end}`, "Lap 18–32"
  here) with the *actual* per-candidate recommendation (`pit_lap`, "32")
  relegated to a small subtitle ("Recommended: Lap 32"). The visually
  dominant number is a fixed constant, not a recommendation.
- **No confidence field anywhere** — not in `PitWindowResponse`, not
  computed by `get_optimal_pit_window`. `projected_total_delta_seconds`
  is a time cost in seconds, not a probability.
- **The recommended compound is computed, then discarded.**
  `get_optimal_pit_window`'s stint-2 loop iterates `SOFT`/`MEDIUM`/`HARD`
  and tracks only `best_stint2_delta: float | None` — the winning
  compound string is never assigned to a variable that survives the loop.
  There is no field to add for this without first fixing the loop itself
  to remember which candidate won.
- **The SHAP explanation cannot express the vision's own example.**
  `tire_deg_model.FEATURE_COLUMNS` is `lap_number`, `compound_encoded`,
  `tyre_age_laps`, `fuel_adjusted_time`, `circuit_id_encoded`,
  `driver_id_encoded` — **no gap-to-rival feature exists in this vector at
  all**. "Gap to P4 behind is 8.2s" is a `pit_predictor.FEATURE_COLUMNS`
  concept (`gap_to_car_ahead`/`gap_to_car_behind`), structurally
  unreachable from this endpoint's explanation mechanism. In the real
  example above, the top-contributing feature was `fuel_adjusted_time` —
  not tyre age, not a rival gap. The SHAP vector is also built from the
  driver's *current* compound (`state["compound"]`), not whichever
  compound the (discarded) stint-2 winner actually was — so even what it
  does explain isn't about the recommended stint.

### 2b. `pit_probability` (persisted `StrategyPrediction` / live history)

Backed by `pit_predictor.pkl`, computed per-lap by
`prediction_worker._run_inference`/`_persist_and_publish` (the
`run_strategy_prediction` Celery task), persisted to `StrategyPrediction`,
surfaced via `GET /strategy/{session_id}/{driver_id}/history`
(`StrategyPredictionHistoryEntry`). This is the mechanism that actually
progresses automatically, lap by lap, without user action — the one piece
of the vision's "no button press" requirement that's genuinely met.

**Real evidence, gathered today** — same session, three real pit stops,
re-run fresh through the (already-fixed, post-encoding-fix)
`_run_inference` code path directly:

| Driver | Real pit lap N | N-4…N-1 | **N** | N+1…N+3 |
|---|---|---|---|---|
| LEC | 21 | 0.0000, 0.0000, 0.0000, 0.0000 | **0.9999** | 0.0008, 0.0004, 0.0083 |
| COL | 16 | 0.0001, 0.0002, 0.0001, 0.0001 | **0.9999** | 0.0103, 0.0032, 0.0106 |
| GAS | 15 | 0.0001, 0.0001, 0.0001, 0.0000 | **0.9999** | 0.0036, 0.0010, 0.0056 |
| NOR (no pit, laps 11-24) | — | uniformly 0.0000–0.0001 | — | — |

`pit_probability` is flat near-zero right up to and including the lap
*before* the real pit, spikes to ~0.9999 exactly *on* the pit lap, then
collapses back to near-zero the next lap. Zero advance warning. Root
cause: `pit_predictor.label_pit_laps` labels `did_pit_this_lap=True` on
the stint's `start_lap` — the out-lap, i.e. `current_tyre_age==1` by
construction — and `current_tyre_age` is directly a feature, so the model
has essentially learned "tyre age 1 → a pit just happened," not
"anticipate an upcoming pit." (Full writeup of this finding earlier in
this session's own transcript, not reproduced further here — this
document is the follow-on scoping artifact.) `optimal_pit_lap`
(`StrategyPrediction.optimal_pit_lap`, rendered as `predicted_pit_lap`) has
its own, separate, already-documented bug on top of this: it's computed
as `lap_number + max(int(tire_life_remaining), 1)`, and
`tire_life_remaining` is the raw tire_deg lap-time-delta prediction (a
small ±2-second float), not a laps-remaining count — see CLAUDE.md's own
Deferred Wiring entry, "`StrategyPrediction.tire_life_remaining` stores
the wrong value." In practice this collapses `predicted_pit_lap` to
`current_lap + 1` almost always.

### 2c. The two mechanisms are never reconciled — and the live-race UI picks the weaker one

`PitWindowCard.tsx` (`web/src/components/strategy/PitWindowCard.tsx`)
renders differently depending on `isReplayActive` (from
`useLiveTelemetry`'s `lapsByDriver`, i.e. "is live telemetry currently
flowing for this driver"):

- `usePitWindow(sessionId, driverId, !isReplayActive)` — **explicitly
  disabled** whenever a race is live/replaying.
- `web/src/hooks/useStrategy.ts`'s `usePitWindow` is a plain `useQuery`
  with no `refetchInterval` and a `queryKey` that does not include lap
  number — even when enabled, nothing makes it refetch as laps pass. Its
  own neighbor hook, `useCurrentLapHistoryEntry`, explicitly embeds
  `liveEvent?.lap_number` in its query key specifically *because*
  without that the query "would fetch once... and freeze" (the code
  comment's own words) — `usePitWindow` has no such mechanism.
- The component's *only* per-lap-updating branch is `if (compact &&
  isReplayActive)`, which renders `pit_probability`/`predicted_pit_lap`
  from §2b, not anything from §2a.
- If the full (non-compact) card is ever rendered while
  `isReplayActive` is true, `usePitWindow` stays disabled, `windows` is
  `undefined`, and the card falls through to "No pit window predicted."

**Net effect: during the exact scenario the original vision describes —
watching a live race — the rich, SHAP-explained, delta-ranked mechanism
(§2a) is never shown at all.** What's shown is the lagging-indicator
mechanism (§2b), which cannot give advance warning by construction.

### 2d. This must hold for BOTH live and replay — they already share one code path

`isReplayActive` is not "is this a real live race" — it's "is telemetry
currently flowing on the live WebSocket/lap-progression channel," which is
true identically for a genuinely live race (`ingest_live_session.py`) and
for Demo Replay (`replay_pipeline.py`, Day 43). Confirmed architecturally
elsewhere in this codebase: `replay_pipeline.py` deliberately republishes
to the *same* Redis keys (`f1:{season}:{round}:car:{car_number}:position`,
etc.) a real live ingestor would write, specifically so downstream
consumers (e.g. `CircuitMapPanel`) need no replay-specific code at all —
the same principle applies here. `PitWindowCard.tsx` itself makes no
live-vs-replay distinction; it only checks `isReplayActive`. This is
important for the rebuild, not just descriptive: **whatever mechanism
ends up serving live per-lap updates must be verified against both a real
live session and a replay session before being considered complete** —
not because they need separate handling, but because a fix that happens
to work against one code path (e.g. by accident of timing, caching, or a
DB-vs-Redis data-freshness quirk) is not proven to work against the other
just because today's code doesn't branch on it. See §4's own note on this.

---

## 3. Gap Analysis

| Vision element | Currently exists? | What's missing/broken |
|---|---|---|
| Narrow lap-range window ("Lap 31-34") | ❌ | `window_start`/`window_end` is the fixed 15-lap search horizon, identical across every candidate — not a per-recommendation range. The real per-lap answer (`pit_lap`) exists but is rendered as a subtitle, not the headline. |
| Confidence score ("71%") | ❌ | No field exists anywhere in `PitWindowResponse`, `get_optimal_pit_window`, or the frontend. `projected_total_delta_seconds` (a time cost) is the closest analog and is not a probability. |
| Compound recommendation ("MEDIUM") | ❌ | Computed internally (`best_stint2_delta`'s winning candidate) then discarded — the loop never stores which compound produced the best delta. |
| Multi-factor "why," including rival-gap reasoning | ⚠️ partial | Real SHAP explanation exists (`shap_explanation`), but (a) only the single top feature is rendered, not a narrative, (b) the underlying feature vector has no gap-to-rival term at all — structurally cannot produce the vision's own example, (c) it's built from the driver's *current* compound, not the recommended one. |
| Automatic per-lap updates during a live race, no button press | ⚠️ wrong mechanism | The one thing that *does* update per lap automatically (`pit_probability`/`predicted_pit_lap`, §2b) is a different, cruder mechanism than the one with the range/SHAP explanation (§2a) — and §2a is explicitly disabled during live/replay (`usePitWindow(..., !isReplayActive)`), so the rich mechanism never gets the "updates every lap" behavior at all. |
| Works during a genuinely live race | ⚠️ untested this way | Only ever exercised via `replay_pipeline.py` in this codebase's own history (Day 43 checkpoints) — no evidence of dedicated verification against a real `ingest_live_session.py` run for this specific feature. |
| Works during Demo Replay | ✅ (for §2b only) | `pit_probability`/`predicted_pit_lap` do progress correctly per lap during replay (confirmed via Day 43's own checkpoints) — but see the lagging-indicator and `predicted_pit_lap≈current_lap+1` findings in §2b; "progresses" is not the same as "correct." |

---

## 4. What Needs To Change

High-level only — this is scoping, not an implementation plan. The future
session should design the actual approach (see §5).

- **A confidence score needs to be added.** Doesn't exist in any form
  today. Needs a design decision: derived from the Monte Carlo /
  cross-validation spread already used elsewhere (`race_simulator.py`'s
  confidence intervals, `tire_deg_model`'s CV folds), from
  `pit_predictor`'s own classifier probability (if that mechanism is kept
  — see below), or something new.
- **The winning compound needs to be returned, not discarded.**
  `get_optimal_pit_window`'s stint-2 loop needs to track *which*
  candidate compound produced `best_stint2_delta`, and that needs a new
  response field.
- **"Window" needs to become a real narrow range, not the search
  horizon.** `window_start`/`window_end` need to mean something
  candidate-specific (e.g. a tolerance band around `pit_lap` where the
  delta cost is within some threshold of optimal), not the fixed lookahead
  bound.
- **The SHAP explanation needs rival-gap awareness, or a different
  explanation mechanism entirely.** As long as the explanation is drawn
  from `tire_deg_model.FEATURE_COLUMNS` alone, it cannot express the
  vision's own example. This may mean explaining `pit_predictor`'s
  decision instead/as well (which does have gap features), or building a
  combined explanation across both models, or something else — a real
  design question for the future session, not decided here.
- **The rich mechanism needs to actually run during live progression, not
  be disabled.** Whatever the rebuilt endpoint/hook looks like, it must be
  wired into the per-lap update cycle the way `useCurrentLapHistoryEntry`
  already is (lap number in the query key, or a push-based update via the
  existing WS/Redis pub-sub infrastructure), not gated off with
  `enabled: !isReplayActive`.
- **`pit_probability`'s lagging-indicator flaw needs a decision, not just
  a bugfix.** Two real options, not adjudicated here: (a) fix the label
  definition (`label_pit_laps` labeling the out-lap rather than some
  N-laps-before-out-lap window) and retrain, keeping `pit_probability` as
  a genuine advance-warning signal in its own right; or (b) stop routing
  live-race strategy recommendations through `pit_predictor` at all, and
  instead route the live per-lap update cycle through a fixed,
  properly-live-wired version of the §2a (tire_deg/SHAP) mechanism. Given
  §2a already has the richer explanation infrastructure, (b) may be the
  more coherent direction — but this needs real scoping, not a default
  assumption.
- **Must work identically in LIVE and REPLAY mode, verified against
  both, not just one.** Per §2d: `ingest_live_session.py` (real live race)
  and `replay_pipeline.py` (Day 43 Demo Replay) already share the same
  underlying WebSocket/lap-progression/Redis-key infrastructure, and the
  frontend's `isReplayActive` flag treats them identically by design — the
  rebuilt feature should not introduce a live-vs-replay branch that
  doesn't already exist. Any proposed fix must be tested against **both**
  a real live session and a replay session before being considered done;
  passing only in replay (the easier one to test, and the only one this
  investigation itself exercised) is not sufficient evidence the live path
  works.

---

## 5. Anchor Prompt — paste into a new session

```
Read docs/core-feature-rebuild-strategy-recommendations.md in full before
doing anything else. It documents a real, evidence-based gap between this
project's originally-planned core feature (a live, per-lap-updating pit
strategy recommendation with a narrow lap window, a confidence score, a
compound recommendation, and a multi-factor explanation) and what
currently exists (two disconnected mechanisms, neither of which alone
delivers the vision, with the live-race UI defaulting to the weaker one).

Do NOT trust this document's findings blindly — independently verify
everything in it against the current codebase before proposing anything.
It was written 2026-09-04; deferred items referenced in it (e.g.
CLAUDE.md's "StrategyPrediction.tire_life_remaining stores the wrong
value") may have been fixed by a later session — re-read CLAUDE.md's
Deferred Wiring and Notes sections fresh, and re-check the actual current
state of every file this document cites (backend/services/
strategy_service.py, backend/schemas/strategy_schema.py, backend/services/
ml/pit_predictor.py, backend/services/ml/tire_deg_model.py, backend/
workers/prediction_worker.py, web/src/components/strategy/
PitWindowCard.tsx, web/src/hooks/useStrategy.ts) rather than assuming this
document's line-level claims still hold.

This is a significant feature rebuild, not a quick fix. Do not jump to
implementation. Before writing any code:

1. Re-investigate the current state of both mechanisms (§2 of the
   document) and confirm or correct this document's Gap Analysis (§3)
   against what you find.
2. Propose a complete end-to-end plan for what the rebuilt feature should
   look like — backend schema changes, whether/how the ML models
   (tire_deg, pit_predictor) need to change (including whether
   pit_predictor's label definition needs fixing, or whether live-race
   strategy recommendations should route through a different mechanism
   entirely — see §4's two options, not pre-decided), and how the
   frontend should consume and display it (including how it updates
   per-lap without a button press, using the existing WS/lap-progression
   infrastructure rather than inventing new plumbing).
3. Present that plan and wait for approval before implementing anything —
   same checkpoint-based convention used throughout this project's other
   deferred-item sessions (propose, get approval, implement checkpoint by
   checkpoint, report + wait between each). Expect this to span multiple
   checkpoints, quite possibly multiple sessions — do not try to compress
   it into one pass.

Whatever solution you propose and build MUST work correctly and
identically in both of this project's live-progression scenarios:
  (a) a genuinely live race, via ingest_live_session.py, and
  (b) Demo Replay, via replay_pipeline.py (Day 43).
These already share the same underlying WebSocket/Redis lap-progression
mechanism, and the frontend's `isReplayActive` flag treats them
identically by design — do not special-case them. Before considering any
part of this work complete, verify it against BOTH a live scenario and a
replay scenario, not just whichever is more convenient to test locally
(replay is easier to trigger and was the only one exercised by this
investigation itself — that is a gap in the evidence, not a green light
to skip live verification in the rebuild).

Do not run git commands unless explicitly asked.
```
