# Live Race Ingestion & Strategy-Feature Gaps — Italian GP 2026 (Monza)

> **Status: investigation only, NOT fixed.** Discovered 2026-09-11 reviewing
> the local Docker DB after a real live-ingested race — Italian GP 2026,
> Round 13, Monza, 53 laps — that the stack ingested end-to-end while the
> user watched the UI during the actual race. This is the first real-world,
> full-race live-ingestion run this project's UI has been directly observed
> against (as opposed to a replayed/synthetic session or a post-hoc
> historical ingest), and it surfaced five distinct, real problems the user
> noticed live, all independently confirmed here against the real DB and the
> real source code — not assumed from reading code alone. (Issues D and E
> started life as one conflated issue; a user follow-up after the first
> draft surfaced a fact that split it into two genuinely separate bugs —
> see Issue D's own correction note.)
>
> Every fact in this document was captured via a live query against the
> local Postgres/TimescaleDB stack on 2026-09-11, or a direct read of the
> current source files named. No fixes have been attempted — this is a
> discovery/root-cause document only, for a future session to plan and
> implement against. Read the Anchor Prompt at the end before starting that
> work.
>
> **Re-verified 2026-09-18** against the same local DB (all Monza rows still
> present) and the current source. All five issues confirmed real; four
> changed materially, and several "needs investigating" questions are now
> answered — including Issue A's `total_laps` source and Issue D's
> red-flag confirmation. **Read section 0b next** for what changed, then
> each issue's own dated re-verification note. Issue B's framing in
> particular was found to be wrong and has been re-scoped; Issue C was found
> to be blocked on evidence that no longer exists. Still no fixes attempted.

---

## 0. Session & Data Reference

Real identifiers, so any future session can reproduce every query in this
document exactly:

| Field | Value |
|---|---|
| Race | Italian GP 2026, Round 13 |
| Circuit | Autodromo Nazionale Monza |
| Race.id | `22b873c3-3b85-4395-83bb-7a299f1f45ce` |
| Race.status | `scheduled` (never flipped to `completed` — expected for a live-ingested race that was never re-processed by `ingest_historical.py`; same pattern as the already-documented Zandvoort R12 case in CLAUDE.md) |
| race_date | 2026-09-06 |
| R session.id | `3ddc84bd-f10e-4870-9e98-631d79695beb` |
| Real total race distance | **53 laps**, confirmed via `MAX(LapData.lap_number)` across the whole field post-race |

The stack was up and live-ingesting for the entire real race. All findings
below come from querying `lap_data`/`tire_stints` directly against this
session, plus reading the exact ingestion/service/frontend code paths that
produced what's in the DB and what the UI rendered from it.

---

## 0b. Re-verification pass (2026-09-18) — what changed

Every finding above was independently re-verified against the same local
Postgres (the Monza session's rows are all still present: 1052 `lap_data`
rows, 1052 `strategy_predictions` rows, 33 `alerts` rows) and against the
current source. **All five issues are real.** Four of the five changed in
some material way, and several of this document's own "what needs
investigating" questions are now answered — including two that were listed
as needing a future live race or external research.

| Issue | Re-verification outcome |
|---|---|
| A | **Confirmed, materially worse than written.** Layer 2's frequency claim is now measured, not reasoned: 94.6% null. Also: the open `total_laps` design question now has an authoritative answer (see A.1 below). |
| B | **Confirmed, but the framing was wrong.** The real defect is score *bistability*, not primarily race-state blindness. The specific alert cited (ANT/VER at lap 38) is not what the DB shows. |
| C | **Confirmed. The #1 research step is impossible** — no raw feed sample exists or can be recovered. |
| D | **Confirmed and extended.** The anomaly spans laps 3-6, not just lap 4; the red-flag hypothesis is now confirmed from the DB alone; and the signal needed to fix it is *already being received and discarded*. |
| E | **Mechanism upgraded from hypothesis to documented-recurring**, via a precedent found in this codebase's own comments. One research question closed for free. |

Each issue's section below carries its own dated re-verification note with
the real numbers. Where a number in the original text is superseded, the
note says so explicitly rather than editing the original claim away — same
provenance convention this project's CLAUDE.md uses.

**One caveat that applies to A and B together, and was not available to the
original investigation:** this race ran **2026-09-06**, which is *before*
the 2026-09-11 CP1-CP3 tire-deg/pit_predictor retrain
(`docs/tire-deg-model-quality-and-rival-pit-behavior.md`). Every model
output captured in this session's `strategy_predictions` rows — including
the `tire_life_remaining = 40.00` pegging that drives Issue A's arithmetic
— comes from the **pre-retrain** models. The structural bugs are real and
independent of model quality, but the observed *magnitudes* should not be
assumed to still hold on current models without re-measuring.

---

## 1. Issue A — Pit-window recommendation exceeded the real race length (predicted lap 78 on a 53-lap race) — ✅ FIXED 2026-09-19

> **✅ Fixed 2026-09-19, four checkpoints.** `Session.total_laps` now
> stores the real scheduled race distance (migration
> `20260918_add_total_laps_to_sessions`), populated historically from
> FastF1's own `session.total_laps` and live from the feed's `LapCount`
> topic (previously subscribed to nothing that carried this). Every real
> consumer of the old `MAX(lap_number)`-so-far proxy — including a THIRD
> instance the original investigation didn't find, in
> `get_competitor_predicted_strategy` — now prefers the real value, and
> `optimal_pit_lap` is clamped to it once known. See "Fix summary
> (2026-09-19)" at the end of this section for the full implementation,
> the real-data verification (all 155 backfillable completed R sessions
> confirmed populated, live-endpoint checks against real Belgian GP data),
> and a genuine pre-existing test bug found and fixed along the way
> (unrelated to Issue A, confirmed via `git stash` against the
> pre-this-session code).

### What the feature is supposed to do

`PitWindowCard` ("Recommended: Lap N") should show a pit lap that is, at
worst, the last lap of the race — recommending a stop that can't physically
happen (25 laps past the chequered flag) is never correct, live or
historical.

### What it currently does

At some point around/after lap 38 (based on the exact arithmetic below),
the UI showed **"Recommended: Lap 78"** for a 53-lap race.

### Root cause — two independent layers, both real

**Layer 1: two different "pit lap" fields exist, and the live UI path
silently prefers the unbounded one.**

- `recommended_pit_lap` — from `strategy_service.compute_pit_recommendation`
  (`backend/services/strategy_service.py`), which **is correctly capped**:
  `max_pit_lap = min(state["lap_number"] + PIT_WINDOW_LOOKAHEAD_LAPS, state["total_laps"])`
  (`PIT_WINDOW_LOOKAHEAD_LAPS = 15`).
- `predicted_pit_lap` — the API-renamed form of `StrategyPrediction.
  optimal_pit_lap`, computed in `prediction_worker._run_inference`
  (`backend/workers/prediction_worker.py:731`):
  ```python
  "optimal_pit_lap": lap_number + max(int(predicted_life_remaining), 1),
  ```
  **This has no bound against race length anywhere.**
  `predicted_life_remaining` caps at `tire_deg_model.MAX_LOOKAHEAD_LAPS = 40`.
  At `lap_number=38`, a pegged `predicted_life_remaining=40` gives exactly
  `38 + 40 = 78` — matching the observed value exactly. (Whether the driver
  in question was actually at lap 38 specifically wasn't independently
  re-confirmed against the exact UI screenshot/timestamp — the arithmetic
  match is strong circumstantial evidence, not a re-run of the literal
  original request, since the live moment can't be replayed after the fact.)

  Predicted-life-remaining pegging at the 40-lap cap is itself a
  well-documented, pre-existing model-quality characteristic — see
  `docs/tire-deg-model-quality-and-rival-pit-behavior.md` (the CP1-CP5
  investigation earlier this same day) for the full history; this document
  does not re-litigate that model-quality question, only how its output
  reaches the UI completely unbounded here.

  The frontend (`web/src/hooks/useStrategy.ts`, `viewFromHistoryEntry`)
  picks between the two: `pitLap: entry.recommended_pit_lap ??
  entry.predicted_pit_lap` — it only falls back to the unbounded field when
  the capped one is `null`.

**Layer 2 (the more important one): `recommended_pit_lap` is null far more
often during a LIVE race than its own docstring implies, making the
"rare" fallback actually common.**

`_compute_recommendation_fields`'s own docstring says `recommended_pit_lap`
is `None` when "`current_lap >= total_laps`, race essentially over" — framed
as an end-of-race edge case. But `total_laps` itself, throughout the entire
live per-lap pipeline (`_current_state` in `strategy_service.py`,
`_resolve_inference_context` in `prediction_worker.py`), is computed as:

```python
total_laps_query = select(func.max(LapData.lap_number)).where(LapData.session_id == session_id)
total_laps = (await db.execute(total_laps_query)).scalar_one() or lap.lap_number
```

i.e. **"the highest lap number recorded for this session so far."** During a
race that is still in progress, this is not an estimate of the real race
distance — it's essentially a restatement of "how far has the race gotten,"
which tracks within a lap or two of `current_lap` for every driver, all the
time. That means `max_pit_lap = min(current_lap + 15, total_laps)` collapses
to **≈ `current_lap`** for almost the entire live race, not just near the
end — so `pit_laps = np.arange(current_lap + 1, max_pit_lap + 1)` is empty
(`n_candidates == 0`) far more often than the "race essentially over"
framing suggests. This was reasoned from the code, not independently
measured against this exact race's Redis/DB timeline — see below.

**Also relevant, discovered along the way, not yet connected with certainty:**
`useCurrentLapHistoryEntry`'s `isReplayActive` flag (which gates whether
`PitWindowCard` reads the persisted-history path at all) is driven by
`useLiveTelemetry`'s WebSocket feed (`liveEvent !== undefined`) — it is
`true` for a genuinely live race exactly the same way it's `true` during
Demo Replay, despite the name suggesting it means Demo Replay specifically.
This isn't itself a bug, but it means every "isReplayActive"-gated code path
in the frontend needs to be read as "replay OR live," not "replay only,"
when reasoning about live-race behavior — worth keeping in mind for the rest
of this document too.

### Re-verified 2026-09-18 — measured, and worse than written

All 1052 `strategy_predictions` rows for this session were queried directly.

**Layer 1 confirmed, with a literal match rather than circumstantial
arithmetic.** The original text hedged that "whether the driver in question
was actually at lap 38 specifically wasn't independently re-confirmed." It
now is — and it wasn't one driver, it was the whole field:

```
SELECT d.code, sp.lap_number, sp.optimal_pit_lap, sp.recommended_pit_lap, sp.tire_life_remaining
  FROM strategy_predictions sp JOIN drivers d ON d.id = sp.driver_id
 WHERE sp.session_id = '3ddc84bd-f10e-4870-9e98-631d79695beb' AND sp.lap_number = 38;
```

returns **19 rows, every single one with `optimal_pit_lap = 78` and
`tire_life_remaining = 40.00`** (the `MAX_LOOKAHEAD_LAPS` peg), and 17 of
the 19 with `recommended_pit_lap` NULL (only BOT and PER got 39). So "Lap
78" was on screen for essentially any driver the user clicked at that
moment, not a one-off.

Session-wide bounds:

| Measure | Value |
|---|---|
| `optimal_pit_lap` range | **41 to 93** (race is 53 laps) |
| rows with `optimal_pit_lap > 53` | **778 / 1052 = 74.0%** |
| rows with `recommended_pit_lap` NULL | **995 / 1052 = 94.6%** |
| rows both NULL-recommendation *and* beyond-race-length | 721 |
| `recommended_pit_lap` / `window_end` max | **53 / 53** — 0 rows beyond race length |

That last row is the clean confirmation of Layer 1 as originally stated:
the capped field is genuinely capped and never misbehaved; **only** the
unbounded `optimal_pit_lap` escapes.

**Layer 2's frequency claim is now measured, and the mechanism is proven,
not inferred.** The original text explicitly flagged this as "reasoned from
the code, not independently measured." Computing, for every row, the
headroom `field_max_lap_at_predict_time − that driver's own lap_number`
(where `field_max_lap_at_predict_time` is `MAX(lap_data.lap_number)` among
rows whose `created_at <= sp.predicted_at`):

| `recommended_pit_lap` | rows | min headroom | avg headroom | max headroom |
|---|---|---|---|---|
| NULL | 995 | −1 | **0.00** | **0** |
| present | 57 | **1** | 1.05 | 2 |

**Perfect separation across all 1052 rows**, with no overlap. A
recommendation exists if and only if the field had already moved at least
one lap past the lap being predicted for — i.e. only when the Celery worker
was running *behind* the race. Null-rate by lap bucket confirms the same
shape from the other direction: laps 1-19 are **100% null** (0 of 400),
rising only to 23 of 190 in the lap 40-49 bucket as worker backlog grows.

**This is a stronger conclusion than the original section drew.** Even in
the 57 rows where a recommendation *did* appear, max headroom is 2 — so
`pit_laps = np.arange(current_lap + 1, max_pit_lap + 1)` was searching a
**1-2 lap window, never the intended 15**. The pit-window recommendation
engine is not "degrading to a fallback occasionally" during a live race; it
is **functionally dead for the entire race**, and on the rare occasions it
produces a number at all, that number is the output of a search over one or
two candidate laps. Fixing the unbounded-clamp alone (Layer 1) would
therefore replace a wrong number with a *capped* wrong number — it does not
restore the feature.

### `total_laps` has an authoritative source — research question 2 is answered

The original research question 2 listed only speculative options ("(a) add
a real `total_laps` column, populated from Ergast/FastF1's own
scheduled-laps data — needs a data source audit, does Ergast reliably carry
this pre-race?; (b) a live-only fallback heuristic; (c) something else").
Verified answer: **F1's own live timing feed broadcasts it directly**, and
this project is simply not subscribed to it.

- `LapCount` is a real live-timing topic — FastF1's own reference SignalR
  client subscribes to it (`fastf1/livetiming/client.py:103`, alongside
  `TopThree`/`RcmSeries`), and it is archived per session as
  `LapCount.jsonStream` (`fastf1/_api.py:53`, "Lap counter").
- Its `TotalLaps` field is explicitly **the originally scheduled lap
  count** — FastF1's `Session._load_total_lap_count` (`fastf1/core.py`)
  reads exactly this to populate its own public `session.total_laps`
  property, with the comment *"'TotalLaps' is intended lap count, use last
  value that is not None in case of wrong data being corrected later.
  Shouldn't usually change."*
- `ingest_live_session.py`'s `_TOPICS` (line 70) does **not** include
  `LapCount`.

So both ingestion paths have a real, authoritative source with no heuristic
and no Ergast dependency: **live** → subscribe to `LapCount`, read
`TotalLaps`; **historical** → FastF1's `session.total_laps`. What remains a
genuine open decision is only *where to store it* (a `total_laps` column on
`sessions` vs. on `races` — `sessions` looks right, since a race's R/Q/FP
sessions have different lap counts and the column is a session property),
and the fallback behaviour when the feed never sends it.

### Files involved

- `backend/workers/prediction_worker.py` — `_run_inference` (the unbounded
  `optimal_pit_lap`), `_compute_recommendation_fields` (where
  `recommended_pit_lap` gets set to `None`)
- `backend/services/strategy_service.py` — `_current_state` (the `total_laps`
  proxy), `compute_pit_recommendation` (the correctly-capped search that
  still depends on that proxy being right)
- `web/src/hooks/useStrategy.ts` — `usePitRecommendation`,
  `useCurrentLapHistoryEntry`, `viewFromHistoryEntry`'s fallback
- `web/src/components/strategy/PitWindowCard.tsx` — renders `view.pitLap`

### What needs investigating / researching before fixing

1. ~~**Independently confirm Layer 2's frequency claim with real data**~~ —
   **✅ answered 2026-09-18, see the re-verification section above.** 94.6%
   null, and the mechanism proved out with perfect separation on all 1052
   rows. The follow-on finding (the search window is only ever 1-2 laps
   wide even when it *does* return a value, so a clamp alone doesn't restore
   the feature) is new and should drive the fix's scope.
2. ~~**Design a real `total_laps` source.**~~ — **✅ source identified
   2026-09-18 (`LapCount.TotalLaps` live / `session.total_laps` historical,
   see above); only the storage location and no-feed fallback remain open.**
   Original framing kept below for context: CLAUDE.md already documents that
   no table stores race distance anywhere (`Race`/`Circuit`/`Session` all
   lack a `total_laps` column) — this has been worked around before by using
   `MAX(lap_number)` as a proxy for a *completed* session, where it's
   trivially correct. For a *live* session it fundamentally isn't. Real
   options, not pre-decided: (a) add a real `total_laps` column, populated
   from Ergast/FastF1's own scheduled-laps data at race-weekend setup time
   (most correct, but needs a data source audit — does Ergast reliably carry
   this pre-race?); (b) a live-only fallback heuristic (e.g. a per-circuit
   typical lap count table); (c) something else. This decision affects more
   than just the pit-window feature — anything computing "laps remaining"
   live inherits the same proxy problem (worth auditing for other call
   sites of this exact query pattern while in there).
3. ~~**Decide whether `optimal_pit_lap`/`predicted_pit_lap` should be
   clamped independently**~~ — **✅ done 2026-09-19 (CP4).** Clamped to
   `Session.total_laps` specifically when known — see the fix summary
   below for why the naive version of this (clamping against
   `resolved["total_laps"]`, which is real-value-OR-still-the-old-proxy)
   was a real mistake caught during implementation, not shipped.
4. **`isReplayActive`'s naming/semantics** — not renamed as part of this
   fix; still genuinely open, tracked in the Cross-cutting Observations
   section below rather than re-litigated here.

### Fix summary (2026-09-19)

Four checkpoints, approved and implemented in sequence.

- **CP1 (schema):** `Session.total_laps: int | None`, migration
  `20260918_add_total_laps_to_sessions` — nullable, non-regressive by
  construction. Generated and applied via the project's `/migrate` skill
  (steps 1-8; commit deliberately skipped per instruction). `alembic
  check` confirmed zero drift between the model and the migration.
- **CP2 (write path):**
  - `_ingest_common.get_or_create_session` gained an optional `total_laps`
    param: sets it on a new row, backfills an existing NULL row, **never**
    overwrites an existing value.
  - `ingest_historical.py` passes `fastf1_session.total_laps` through
    (already loaded via its existing `load(laps=True, ...)` call).
  - `ingest_live_session.py` subscribed to the `LapCount` topic (was in
    `_TOPICS` for no functional reason before this — no handler existed)
    and added `_handle_lap_count`, dispatching a new
    `update_session_total_laps` Celery task (`telemetry_worker.py`) once
    per distinct value seen.
  - `backend/scripts/backfill_session_total_laps.py` (new, `make
    backfill-session-total-laps`): backfills completed R sessions from
    `MAX(lap_number)` — correct by construction for a genuinely finished
    race, no FastF1 fetch needed. Deliberately scoped to `Race.status ==
    "completed"` only, per this section's own research-question-2
    decision — a partially-live-ingested session (Monza itself) is left
    `NULL` rather than backfilled with a value indistinguishable from a
    real one. **Run for real against the local DB: 155 of 158 R sessions
    backfilled** (the 3 not backfilled: Monza itself, still
    `status="scheduled"` as designed, plus 2 completed races with zero
    `lap_data` rows — one handled gracefully with a warning, the other
    likewise).
- **CP3 (read path):** `strategy_service._current_state` and
  `prediction_worker._resolve_inference_context` now prefer the stored
  value, falling back to the old `MAX(lap_number)` proxy only when it's
  genuinely unknown. **A third, previously-undiscovered instance of the
  identical bug was found and fixed in the same checkpoint:**
  `strategy_service.get_competitor_predicted_strategy`'s own `total_laps =
  max(lap.lap_number for lap in latest_laps)` — the same "how far has the
  race gotten" proxy, computed in Python instead of SQL, feeding
  `_first_pit_laps_over_threshold_batch`'s horizon calculation directly
  and silently shrinking every competitor's pit-lap search horizon to 1
  lap mid-race. This backs `/strategy/{session_id}/overview` — CLAUDE.md's
  own "single most compute-expensive endpoint" — not a minor code path.
  This also closes the layer-1 corruption the original investigation
  flagged but didn't fully trace: `total_laps` collapsing toward
  `current_lap` mid-race was feeding wrong values into three ML features
  (`fuel_load_penalty`, `fuel_load_est`, `laps_to_race_end`) on every
  single live prediction, not just the displayed pit lap — the visible
  "Lap 78" was the symptom, this was the larger defect underneath.
- **CP4 (clamp + frontend):** `_run_inference`'s `optimal_pit_lap` is
  clamped to the real total_laps once known. **A real design mistake was
  caught mid-implementation, not shipped:** the first draft clamped
  against `resolved["total_laps"]` — which is *also* true for the
  meaningless mid-race proxy value, not just a genuinely-known real one.
  That would have silently replaced one wrong number (obviously
  implausible, e.g. lap 78 of 53) with a *different* wrong number that
  looks plausible (≈ current lap, since the proxy tracks progress) —
  exactly the trap this section's own research-question-3 discussion
  warned against. Fixed by threading a separate `stored_total_laps` field
  (the real value specifically, `None` otherwise) through
  `_resolve_inference_context`'s return and clamping against *that*, not
  the coalesced value — caught by a fixture in the existing integration
  test suite (`test_live_prediction_pipeline.py`, proxy=40) before it
  reached any real verification. On the frontend,
  `web/src/hooks/useStrategy.ts`'s `PitRecommendationView` gained
  `isFallbackEstimate: boolean` (true only when
  `viewFromHistoryEntry` falls back to the raw, potentially-still-unbounded
  `predicted_pit_lap` — a pre-fix persisted row is never retroactively
  corrected, and a live session before its first `LapCount` message
  legitimately still has no real total_laps to clamp against). The
  frontend has no race-length context of its own to sanity-check the
  number, so `PitWindowCard.tsx` surfaces the uncertainty honestly instead
  (a `~` headline prefix, "Estimated" instead of "Recommended," an
  "Unconfirmed estimate" caption) rather than attempting a client-side
  clamp. Desktop/mobile were checked directly and confirmed **not**
  affected — both already, deliberately, only ever render the REST
  `/pit-window` source (always bounded), never the history-based fallback.

**Verified, not just implemented:**
- `ruff`/`ruff format`/`mypy --strict` clean on every changed file across
  all four checkpoints; `tsc -b`/`oxlint` clean on the frontend changes.
- New/updated unit tests at every checkpoint (schema-adjacent
  `get_or_create_session` tests, `_handle_lap_count`/Celery task tests,
  `_current_state`/`_resolve_inference_context`/
  `get_competitor_predicted_strategy` prefer-vs-fallback tests with
  captured-argument assertions, `_run_inference` clamp tests using the
  exact real Monza numbers, frontend `isFallbackEstimate` rendering
  tests). Full backend unit suite: **429 passed, 0 failed** by CP4
  (341 baseline + 88 new across the whole fix); full web vitest suite:
  **56 passed, 0 failed**.
- **Real integration-test runs against a genuine testcontainer Postgres**
  at both CP3 (11 passed) and CP4 (4 passed after the bug below was
  fixed) — not just mocks.
- **Real-DB/live-API verification**: hit the real running
  `/strategy/{session_id}/overview` endpoint against Belgian GP R10 (now
  carrying a real backfilled `total_laps=44`) — responds correctly.
  Restarted the worker container (Celery doesn't hot-reload) and
  confirmed clean startup with `update_session_total_laps` correctly
  registered — no import errors.
- **A genuine pre-existing test bug found and fixed, unrelated to Issue
  A:** `test_resilience.py::test_prediction_worker_continues_on_model_exception`
  asserted `tire_life_remaining == 0.0` on a model-exception fallback, but
  `_run_inference`'s real, documented, long-standing fallback is
  `tire_deg_model.MAX_LOOKAHEAD_LAPS` (40.0) — confirmed via `git stash`
  that this assertion failed identically against the code as it stood
  *before* any of Issue A's fixes, so this predates and is unrelated to
  this session's work. Fixed the test's expected values (`40.0`/
  `lap_number + 40`) to match real production behavior, not the other way
  around. Not currently wired into any CI workflow (`resilience` isn't a
  marker any `.github/workflows/*.yml` selects) — a separate, real gap,
  noted here rather than silently left.

**Honest limitation:** no live race occurred during this fix. The
real-data checks above (backfill against 155 real historical sessions,
live-endpoint checks, exact-Monza-number unit tests) are the strongest
verification available without one — they are not a substitute for
watching a genuinely live race with the fix in place.

---

## 2. Issue B — Undercut threat alert ignored overall race state

### What the feature is supposed to do

An undercut-threat alert should represent a *realistic* strategic threat —
not just "if both cars hypothetically pit right now, who wins the next few
laps on tyre freshness," independent of whether that hypothetical pit stop
is something either car would actually make at that point in the race.

### What it currently does

At lap 38 of 53 (15 laps remaining), an alert fired for ANT showing **100%**
undercut-threat probability from VER (the car behind) — despite there being
little realistic likelihood of another stop being made by either car that
late, per the user's own read of the race situation.

### Root cause

`_undercut_overcut_probability` (`backend/services/strategy_service.py`,
~line 1635) computes a fixed, race-state-blind comparison every time it's
called:

- `pitting_now_driver_id` pits **this lap**, runs `UNDERCUT_PROJECTION_LAPS
  = 5` laps on a fresh tyre.
- `pitting_next_lap_driver_id` stays out **one more lap**, then pits and
  runs the remaining 4 laps on a fresh tyre.
- `UNDERCUT_MONTE_CARLO_SIMS = 200` Gaussian-noise draws turn the
  deterministic tyre-delta projection into a probability.

This is a pure, symmetric tyre-physics question — "does 5 laps of fresher
rubber beat 5 laps of older rubber" — computed **identically regardless of
what lap of the race it is**. Nothing here asks:

- whether there's enough remaining race distance for a pit-and-recover
  strategy to make sense at all (a fresh-tyre time advantage over a 5-lap
  window is real regardless of how many laps are left, but *whether a team
  would actually pit for it* depends heavily on how many laps remain to
  cash in that advantage — the model has no notion of this trade-off);
- whether the "threatening" driver's own `pit_probability` (already computed
  elsewhere, by `pit_predictor`) suggests they're likely to pit again at
  all;
- `Circuit`/session-level total-laps context is simply never passed into
  this calculation's *strategic* reasoning, only into the tyre-delta
  projection's own lookahead math (via `_project_stint_delta`'s
  `now_state["total_laps"]`/`next_state["total_laps"]` arguments, which
  only prevent projecting past the field-max-so-far, not "is this
  realistic").

`evaluate_threats` (`backend/services/alert_service.py`) then just checks
`undercut_score > UNDERCUT_ALERT_THRESHOLD` (`0.5`) with no additional
race-state gate before dispatching — so a "100%, but physically
meaningless at this point in the race" score reaches the user exactly as
confidently as a genuinely actionable one earlier in the race would.

### Re-verified 2026-09-18 — real, but this section's framing is wrong

The **alerts are real and were persisted**: 33 `UNDERCUT_THREAT` rows exist
for this session, spanning `13:05:06` to `14:40:16` UTC — i.e. the whole
race, right up to the closing laps. The code reads exactly as described
(`_undercut_overcut_probability` takes no race-state input;
`evaluate_threats` gates only on `score > UNDERCUT_ALERT_THRESHOLD = 0.5`).
So the feature-level complaint stands. Two corrections, though, one of them
significant.

**Correction 1 — the specific alert cited above is not what the DB shows.**
The real late-race alert messages are:

```
14:40:16  VER  Undercut threat: VER on PIA (55%)
14:39:12  VER  Undercut threat: VER on PIA (55%)
14:38:01  VER  Undercut threat: VER on PIA (55%)
14:23:34  VER  Undercut threat: VER on GAS (100%)
14:22:26  VER  Undercut threat: VER on GAS (100%)
14:10:11  ALO  Undercut threat: ALO on PER (100%)
```

There is no ANT/VER alert. The **100%** alerts were `VER on GAS`, and
correlating `predicted_at` back to `lap_number` puts them at **lap 30 of
53** — not lap 38, and not "15 laps remaining." The alerts that *were*
firing at ~15 laps to go read **55%**, comfortably above the 0.5 threshold
but not the "100% confidence" the section describes. The user's recollection
of the pairing and the lap was approximate; the underlying observation (a
100% undercut alert that didn't reflect a realistic threat) is still real.

**Correction 2 — and this is the important one: the primary defect is score
*bistability*, not race-state blindness.** Lap 30 of 53 is a perfectly
ordinary pit window — "there's no realistic chance either car pits again"
simply does not describe the moment the 100% alert actually fired. What the
data does show is that the score is numerically unstable lap to lap. VER's
`undercut_score` across consecutive laps:

```
lap 29  0.070      lap 34  0.000      lap 39  0.000
lap 30  1.000  ←   lap 35  0.000      lap 40  0.000
lap 31  0.010      lap 36  0.035      lap 41  0.550
lap 32  0.005      lap 37  0.200      lap 42  0.550
lap 33  0.000      lap 38  0.465      lap 43  0.000
```

A jump from 0.070 to **1.000** and back to 0.010 on three consecutive laps
is not a credible probability estimate under *any* race-state policy.
Session-wide, the distribution is heavily saturated rather than spread:
**131 of 1052 rows sit at ≥0.999 and 547 at ≤0.001** — 64% of all rows are
pinned at one extreme or the other, with only ~36% anywhere in between.
297 rows exceed the 0.5 alert threshold.

**Why this changes the fix.** The originally-proposed remedies (a
remaining-laps cutoff, a confidence discount that scales with laps
remaining, a `pit_probability` cross-check) would all have suppressed the
*late* alerts while leaving the lap-30 100%-then-1% swing completely
untouched — because that one fires in the middle of the race, where every
proposed race-state gate would pass it. Any real fix has to address
calibration/stability first; a race-state gate is a reasonable second layer
on top, not the fix.

Worth noting what this is **not**: CLAUDE.md documents a separate
saturation cause in `_resolve_position_context` (no `lap_number <=
current_lap` bound, producing frozen race-end-anchored scores). That
mechanism is explicitly scoped to *replaying a fully-ingested historical
session* — Monza was genuinely live-ingested lap by lap, so "latest lap"
tracked the real current lap throughout and that bug should not bite here.
The saturation seen above therefore appears intrinsic to
`_undercut_overcut_probability`'s own deterministic-delta-vs-noise balance
(the deterministic stint-delta difference swamping
`_sampled_noise`'s spread, driving the win fraction to 0 or 200 of 200),
not inherited from the position-context bug. **This was reasoned from the
two mechanisms' scopes, not measured** — quantifying the delta/noise ratio
directly is the right first step of a future fix session.

### Files involved

- `backend/services/strategy_service.py` — `_undercut_overcut_probability`,
  `UNDERCUT_PROJECTION_LAPS`/`UNDERCUT_MONTE_CARLO_SIMS` constants,
  `_sampled_noise` (the noise term whose scale relative to the
  deterministic delta is the likely saturation driver)
- `backend/services/alert_service.py` — `evaluate_threats`,
  `UNDERCUT_ALERT_THRESHOLD`

### What needs investigating / researching before fixing

1. **Define what "race state" should mean here, concretely** — this is a
   real design decision, not pre-made: a hard remaining-laps cutoff below
   which undercut alerts simply don't fire? A confidence discount that
   scales down as remaining laps shrink? Cross-referencing the threatening
   driver's own `pit_probability` (if it's low, an undercut "threat" from
   them is not credible regardless of the tyre-physics number)? Some
   combination?
2. **Check whether this is a `race_simulator`-adjacent problem too.**
   `docs/tire-deg-model-quality-and-rival-pit-behavior.md`'s CP6 (not yet
   built) already considers a "resilience layer independent of
   pit_predictor's own calibration" for the Monte Carlo simulator — this
   undercut-alert gap may be a related, possibly-shared piece of the same
   underlying "the system has no sense of overall race-strategy plausibility"
   limitation. Worth reading that document before scoping a fix here, to
   avoid solving overlapping problems twice with two different mechanisms.
3. **Real-world validation data needed.** Before/after this fix, the same
   kind of real-stint measurement used throughout the tire-deg-model
   investigation (real historical races, real undercut attempts, did they
   actually happen and succeed) would be the right acceptance test — not
   currently built for this specific question.
4. Confirm whether `alert_worker.py`'s separate FCM-push threshold path
   (`_PIT_PROBABILITY_ALERT_THRESHOLD = 0.5`, a different mechanism
   entirely per CLAUDE.md) has any related gap — not checked in this
   session, flagged only as a "look here too" note.

---

## 3. Issue C — Retired driver (LEC) never left the timing tower

### What the feature is supposed to do

Once a driver retires, they should stop appearing as an active competitor
in the live timing tower (and anywhere else driven by the same live
position/gap state) for the remainder of the race — not continue to occupy
a position/gap slot based on stale pre-retirement data.

### What it currently does, confirmed against the real DB

LEC's entire footprint in `lap_data` for this session is **one row**:

```
lap_number=1, lap_time_seconds=NULL, position=3, track_status=NULL,
compound=SOFT, is_valid=True, created_at=2026-09-06 13:05:04 UTC
```

`tire_stints` shows one stint, never closed out: `(stint_number=1,
compound=SOFT, start_lap=1, end_lap=NULL)`.

This confirms LEC's real retirement happened very early (consistent with
"retired at lap 4" — the DB simply has no further completed-lap events to
record after that, which is expected/correct: you cannot record "lap 2, 3,
4" for a car that never crosses those finish lines). What's *not* expected
is that the user says LEC kept appearing in the timing tower for the rest
of the 53-lap race.

### Root cause

`ingest_live_session.py`'s `_update_gap_state` evicts a car from the live
gap-tracking state (`_car_live_gap_state`) **only** when F1's feed sends the
exact string `"RETIRED"` in that car's `GapToLeader` field:

```python
_RETIRED_MARKER = "RETIRED"
...
gap_to_leader_raw = _extract_string_field(entry.get("GapToLeader"))
if gap_to_leader_raw is not None and gap_to_leader_raw.strip().upper() == _RETIRED_MARKER:
    was_present = self._car_live_gap_state.pop(car_number, None) is not None
    return was_present
```

If that exact marker never arrives for a given retirement — the car's feed
entry simply stops updating some other way, or F1 signals retirement
through a different field this ingestor doesn't check — this eviction never
fires, and the car's last real state (LEC's `position=3` snapshot) stays in
`_car_live_gap_state` indefinitely. `_publish_live_gaps()` then republishes
that frozen entry to Redis on **every single TimingData message for the
rest of the race**, unconditionally (this "publish every message regardless
of whether anything changed" behavior is itself a deliberate, documented
fix for a *different* bug — a Redis-TTL staleness issue — so it isn't
itself wrong, it's just what makes a frozen retiree's entry visible
continuously rather than only briefly).

This exact eviction mechanism is documented as having been validated
before, against a real session with 3 retirements
(`verify_live_feed_parity.py`, referenced in this function's own
docstring) — so this isn't a "the mechanism has never worked" situation,
more likely "F1's real feed doesn't always signal a retirement via this one
specific string," which the existing validation didn't happen to exercise.

### Re-verified 2026-09-18 — confirmed, but research step 1 is impossible

The code is exactly as described: `_RETIRED_MARKER = "RETIRED"` (line 146),
and `_update_gap_state` (line 623) evicts only on
`gap_to_leader_raw.strip().upper() == _RETIRED_MARKER`. LEC's DB footprint
re-confirmed: **1 row**, `lap_number=1`, `position=3`. The other two real
DNFs are also confirmed (STR 26 laps, ALO 23 laps) — all three stop short
of the field's 53.

**Research step 1 ("get a real recorded feed sample of LEC's actual
retirement") cannot be done.** Checked directly:

- `ingest_live_session.py` never persists raw feed messages anywhere — it
  parses each message and discards it. There is no raw-message log, no
  `.jsonStream` capture, no replay dump.
- `FASTF1_CACHE_DIR` (`/tmp/fastf1_cache`) is FastF1's own HTTP cache for
  its *historical* API, not a live-feed recorder — it holds nothing from
  this live SignalR session.
- Race-day container logs are gone; the compose stack has been recreated
  since (containers are minutes old at re-verification time).

So there is no path to seeing what F1 actually sent for LEC. **This changes
the fix's sequencing rather than its substance**: either ship a
marker-independent backstop on reasoning alone (risky — a staleness-based
eviction heuristic tuned without ground truth can evict healthy cars, e.g.
a car genuinely stationary in a long pit stop or under a red flag, which
this very race had), or add raw-feed recording behind a flag *first* so the
next live race produces the evidence, then fix against real data. The
latter is slower but is the only option that can actually answer research
step 2 ("what signals real F1 retirements can look like") rather than
guessing at it.

Research step 3 (did STR/ALO show the same frozen-tower symptom?) is
likewise unanswerable retroactively — it depends on what the UI displayed,
which was never captured — but it is worth explicitly watching for at the
next live race with a retirement, since "only LEC" vs. "all three" still
discriminates sharply between a feed-specific quirk and a broken mechanism.

### Files involved

- `backend/scripts/ingest_live_session.py` — `_update_gap_state`,
  `_publish_live_gaps`, `_RETIRED_MARKER`

### What needs investigating / researching before fixing

1. **Get a real recorded feed sample of LEC's actual retirement** from this
   race (if the raw feed was logged/cached anywhere — check
   `FASTF1_CACHE_DIR` or equivalent) to see exactly what F1 *did* send for
   LEC around lap 1-4, instead of guessing. This is the single most
   valuable next step — it would either confirm "a different retirement
   signal exists and needs handling" or reveal something else entirely
   (e.g. a genuinely missing/dropped message).
2. **Consider a robustness backstop independent of any specific marker
   string** — e.g. evicting a car whose `GapToLeader`/position hasn't
   updated in N real seconds/messages while every other car keeps
   advancing, as a fallback for whatever signal(s) F1's feed uses for
   retirement that aren't the literal `"RETIRED"` string. Needs research
   into what signals real F1 retirements can look like (there may be
   several: `"RETIRED"`, a `Status`/`KnockedOut`-type field elsewhere in
   the payload, `Position.z`'s own `Status` field noted-but-unused in
   `_handle_position_data`'s docstring, or simply silence).
3. **Check whether STR and ALO — who also stopped short in this same race**
   (STR: 26 laps, ALO: 23 laps, both real DNFs per the lap coverage table
   below) **showed the same frozen-timing-tower symptom**, or whether only
   LEC did — if only LEC, that's a strong hint the feed genuinely behaved
   differently for LEC's specific retirement (e.g. a mechanical failure vs.
   a collision vs. a driver-retired-car-on-track scenario might send
   different signals), which would focus the investigation considerably.
4. Confirm whether this same gap affects the **sector times chart** the
   user also mentioned for LEC — that chart reads real `lap_data` rows
   (confirmed in Issue D below), so LEC's actual plotted data should be
   just the one lap-1 point, not a continuously-updating stream; if the
   user saw LEC's sector chart look "live" throughout the race, that may
   point to a *different*, frontend-side stale-selection bug rather than
   this backend eviction gap — worth clarifying/re-observing next race
   before assuming this is the same root cause for both UI surfaces.

---

## 4. Issue D — Field-wide bogus lap-4 time (real, but NOT why VER's charts were empty — see Issue E) — ✅ FIXED 2026-09-18

> **✅ Fixed 2026-09-18.** `TrackStatus` now has a real handler
> (`_handle_track_status`), accumulated per-car per-lap the same way sectors
> already were, and a new `_is_plausible_lap` (mirroring FastF1's own
> `Session._check_lap_accuracy`) now derives `is_valid`/`track_status`
> instead of the old hardcoded `True`/never-set. See "Fix summary
> (2026-09-18)" at the end of this section for the full implementation,
> real-data verification (all 21 real lap-4 AND lap-5 rows from this exact
> race now correctly reject, using only real recorded values), and one
> honestly-documented residual gap (lap 3 cannot be proven fixed
> retroactively against this already-ingested race — see that section).
> Section 0b's re-verification findings below (the lap 3/5/6 extension, the
> `TrackStatus`-already-subscribed finding) are what this fix is built on
> and remain accurate.

> **Correction:** this section originally conflated two separate things.
> The user clarified afterward that during the race, **every other
> driver's** sector heatmap and lap-times chart updated correctly, lap by
> lap — only VER's showed no data, all race. That rules out this section's
> original theory (a shared, field-wide outlier visually flattening every
> chart) as the explanation for the reported symptom, since it would have
> broken every driver's chart equally, not just one. The bogus lap-4 value
> below is still real and still worth fixing, but it is a **separate,
> lower-severity issue** from what the user actually observed — see Issue E
> for the finding that actually explains the reported symptom.

### What the feature is supposed to do

A driver's lap-times and sector-times charts should plot their real,
physically-plausible lap-by-lap data — an implausible value (if one is ever
ingested) shouldn't be trusted/plotted raw alongside real ones.

### What it currently does, confirmed against the real DB

VER's data is actually complete: 53 rows, 52 real (non-null) lap times —
same as everyone else. **Every single driver in the field** shows an
obviously-bogus lap time on lap 4, all clustered in the same narrow range:

```
RUS 1958.319   COL 1957.224   VER 1956.913   HAM 1956.22   HUL 1956.009
ANT 1955.709   GAS 1955.174   BOR 1954.83    TSU 1954.517  PIA 1954.257
OCO 1954.058   SAI 1953.793   LIN 1953.462   BEA 1953.349  NOR 1953.175
LAW 1952.849   PER 1952.583   ALB 1952.319   STR 1952.267  BOT 1952.109
ALO 1948.683
```

~1955 seconds ≈ 32.6 minutes — for every driver, on the same lap, within a
~10-second spread of each other. That pattern (whole-field, same lap,
tightly clustered) is the signature of a real session stoppage (most likely
a red flag) rather than a per-driver data error. `track_status` is `NULL`
for all 1052 `lap_data` rows in this session, so it can't be used to
independently confirm a red flag was flagged — that's itself a related gap,
noted below.

**Confirmed by direct user observation, correcting this section's own
earlier draft(s):** during the race, every driver's lap-times chart *other
than VER's* rendered correctly — including the lap-4 anomaly itself, which
showed as a real, visible spike to ~30 minutes on the chart, before the
chart correctly resumed showing normal lap times for the rest of the race.
So the chart handled this outlier exactly as it should given the (bad) data
it was fed — a visible but non-breaking spike, not a squashed/unreadable
chart. (An earlier draft of this section guessed it would cause "a visual
glitch" without specifying what that would look like; this is the actual,
now-confirmed, correct behavior of the chart given bad input — the chart
itself is not at fault here.) This remains a real, worth-fixing data-quality
issue on its own terms (an implausible value silently marked
`is_valid=True` is a genuine data-integrity problem — see the `is_valid`
concern in "What needs investigating" below, which has value independent of
any UI symptom), but it should not be framed as a charting bug — it isn't
one, as far as this race's real behavior shows.

### Root cause

`ingest_live_session.py`'s `_handle_timing_data` builds `lap_time_seconds`
directly from F1's own `LastLapTime.Value` field, via `_parse_lap_time`,
with **no plausibility/sanity check of any kind**:

```python
"lap_time_seconds": _parse_lap_time(last_lap.get("Value")),
...
"is_valid": True,
```

`is_valid` is **hardcoded to `True` for every live-ingested lap**,
regardless of the value — there is no equivalent here of
`ingest_historical.py`'s reliance on FastF1's own `IsAccurate` flag. F1's
feed most likely genuinely reported this value (a red-flag-inflated
session-clock delta, not a parsing artifact of this codebase) — the parser
itself is working correctly; the gap is that nothing downstream ever
questions an implausible result before storing and serving it.

**Separately, minor, not the cause of either chart symptom:** the
standalone `SectorTime` table (`backend/models/telemetry.py`) has **zero
rows for this session, and zero rows in the entire database, for any
session, ever**. Confirmed this is genuinely dead/unused, not merely sparse
— `driver_service.py`'s laps endpoint (the one the sector chart actually
reads) queries `lap_data.sector1_seconds`/`sector2_seconds`/
`sector3_seconds` directly, never `SectorTime`. Worth flagging as a
separate, real observation (either finish wiring it or remove it), but not
part of either chart incident's root cause.

### Re-verified 2026-09-18 — confirmed, extended, and cheaper to fix than assumed

**Extension 1: the anomaly is not one lap, it's four.** The original section
found lap 4 only. Aggregating lap time by lap number across the field shows
a clear multi-lap stoppage window:

| lap | rows | avg | min | max |
|---|---|---|---|---|
| 2 | 21 | 88.5 | 86.7 | 92.3 |
| **3** | 21 | **144.7** | 119.1 | 170.0 |
| **4** | 21 | **1954.2** | 1948.7 | 1958.3 |
| **5** | 21 | **197.5** | 194.6 | 200.7 |
| **6** | 21 | **146.7** | 125.5 | 167.8 |
| 7 | 21 | 88.1 | 86.3 | 90.8 |
| 8 | 21 | 87.5 | 85.9 | 89.7 |

Laps 3, 5 and 6 are 1.4x-2.3x a normal Monza lap — individually *plausible*
(they look like safety-car / slow-down / restart laps, so no simple
magnitude threshold catches them), but they are just as unrepresentative as
lap 4 for anything that consumes lap times as real racing pace. A fix that
only excludes the ~1955s outlier still leaves three distorted laps per
driver marked `is_valid = True`.

**Extension 2: the red-flag hypothesis is confirmed from the DB alone — no
external lookup needed.** Research step 1 asked for real-world race-result
or news confirmation. The DB settles it: counting per-driver compound
changes between consecutive laps,

```
lap  4 → 20 compound changes     lap 28 → 5     lap 29 → 2
lap 13 → 1                       lap 33 → 1     lap 47 → 1
```

**20 of the 21 running drivers changed tyre compound on the same lap.** A
simultaneous whole-field tyre change is the unmistakable signature of a red
flag (a stopped race permits a free tyre change); it is not something that
can happen under green-flag racing or even under a safety car. Combined
with a ~32-minute "lap" and the slow laps either side, this is conclusive.
VER's own rows show it plainly: `SOFT` on laps 1-3, `MEDIUM` from lap 4.

**Extension 3 — the most actionable finding: `TrackStatus` is already
subscribed and then silently discarded.** The original section's option (b)
was scoped as "subscribing to `RaceControlMessages`… more correct, more
work." That's only half right. `_TOPICS` (line 70) already contains
`TrackStatus` — but the `_on_feed` dispatch has handlers for only six
topics (`_handle_car_data`, `_handle_position_data`, `_handle_timing_data`,
`_handle_timing_app_data`, `_handle_weather_data`, `_handle_driver_list`).
**There is no `_handle_track_status` anywhere in the file.** `TrackStatus`
(and `SessionInfo`) are requested from F1, received, and dropped on the
floor. That is also the direct answer to research step 4: `track_status`
is NULL on all 1052 rows not because the signal is unavailable, but because
the handler was never written. The red/yellow-flag signal needed to fix
this issue was arriving on the wire during the whole race.

**Threshold sensitivity, for designing option (a):**

| predicate | rows |
|---|---|
| `lap_time_seconds > 600` | 21 |
| `lap_time_seconds > 300` | **21** |
| `lap_time_seconds > 200` | 23 |
| `120 <= lap_time_seconds <= 200` | 65 |

A `> 300s` cut isolates exactly the 21 bogus rows and nothing else, so a
crude magnitude guard is viable as a backstop — but per Extension 1 it
provably does *not* catch the 65 distorted-but-plausible laps, which is the
argument for deriving `is_valid` from `TrackStatus` rather than from
magnitude alone.

**Other counts re-confirmed unchanged:** 1052 `lap_data` rows total,
`track_status` non-null on **0**, `is_valid` true on **1052**,
`session_elapsed_seconds` non-null on **0** (expected — CLAUDE.md documents
this is deliberately not populated on the live path), and `sector_times`
holds **0 rows across the entire database**, confirming the dead-table
observation.

### Files involved

- `backend/scripts/ingest_live_session.py` — `_handle_timing_data`,
  `_parse_lap_time`, `_TOPICS` (already lists `TrackStatus`), `_on_feed`
  (the handler dispatch with no `TrackStatus` branch)
- `backend/models/telemetry.py` — `SectorTime` (the unused table, tangential)
- `backend/services/driver_service.py` — confirmed as the real data source
  for the laps/sector endpoint (reads `lap_data`'s inline columns)

### What needs investigating / researching before fixing

1. **Confirm the red-flag hypothesis directly**, don't assume it — check
   whether Monza 2026 R actually had a red flag (real-world race result/
   news, or F1's `RaceControlMessages` topic if this ingestor ever
   subscribes to it — currently it does not, per the topic list in
   `_on_feed`) around lap 4. If confirmed, this is expected/correct
   *session* behavior that the *ingestion* needs to handle gracefully, not
   a data-source error to chase further.
2. **Design the actual fix, once the cause is confirmed** — options, not
   pre-decided: (a) a plausibility cap on `lap_time_seconds` at ingestion
   time (e.g. anything over some threshold gets marked `is_valid=False`
   rather than silently trusted); (b) subscribing to `RaceControlMessages`
   to detect red-flag periods explicitly and handle affected laps
   specially (more correct, more work); (c) computing `is_valid` from some
   real signal instead of the current hardcoded `True`, more generally —
   this last one has broader value beyond just this incident, since
   `is_valid` being meaningless for every live-ingested lap likely affects
   more than charts (e.g. any downstream training-data export that trusts
   `is_valid` as a real filter would silently include every live-ingested
   session's anomalies too).
3. **Check whether the frontend charts have their own defensive outlier
   handling at all** (a reasonable second layer of defense regardless of
   the ingestion-side fix) — not reviewed in this session; only the backend
   data path was traced.
4. **`track_status` being NULL for the whole live session** is worth its
   own small investigation — does `ingest_live_session.py` simply never
   populate it (a live-ingestion-wide gap, separate from this specific
   incident), and if so, does anything downstream depend on it being real
   for a live session (e.g. `safety_car_model`'s live inference path, or
   the wet-tyre `wet_track` flag)?

### Fix summary (2026-09-18)

Implemented options (b) and (c) from research step 2 together — a real
`TrackStatus` handler plus deriving `is_valid` from a real signal, close to
the full FastF1 `_check_lap_accuracy` standard rather than a bare magnitude
cap (option (a) alone).

- **`_handle_track_status`** (new) — wired into `_on_feed`'s existing
  `TrackStatus` dispatch branch (the topic was already subscribed, see
  section 0b; only the handler was missing). Tracks one session-wide
  `_current_track_status`, defaulting to `"1"` (AllClear) so laps ingested
  before this ingestor's first-ever `TrackStatus` message aren't
  incorrectly treated as under an incident.
- **Per-car status accumulation** — `_handle_timing_data`'s existing pass 1
  (where sector accumulation and gap-state updates already happen
  unconditionally per message) now also records `_current_track_status`
  into a per-car `set[str]`, cleared into that lap's `track_status` string
  at completion, mirroring the existing `_sector_accumulator` pattern
  exactly. A car's previous completed lap's codes are kept separately for
  the check below.
- **`_is_plausible_lap`** (new, pure function) — the real replacement for
  the hardcoded `is_valid = True`. Checks, all must hold: all three sectors
  and lap time present; lap time under a 300s magnitude backstop (covers
  laps ingested before any `TrackStatus` message ever arrives); the three
  sectors sum to the lap time within 0.05s; every status code observed
  during the lap is green/yellow only (`{"1", "2"}`); and the *previous*
  lap's codes were clean too (FastF1's own check_3: "first lap after a
  safety car often has timing issues"). Deliberately does **not** attempt
  pit in/out-lap exclusion (FastF1's `PitInTime`/`PitOutTime`/
  `FastF1Generated` checks) — no live equivalent of those fields exists yet;
  left as a known, narrower residual gap, not attempted here.
- **`LapDataCreate.track_status`** (new optional field) — the one schema
  change needed so the derived value actually reaches the `lap_data` table;
  `LapData.track_status` already existed as a column, just never populated
  live.

**Real-data verification, not just synthetic fixtures.** All 21 drivers'
literal, actual `lap_data` values for laps 3, 4, and 5 of this exact race
were pulled from the local DB and fed through `_is_plausible_lap` directly
in a new permanent regression test
(`backend/tests/unit/test_ingest_live_session.py`):

- **Lap 4 (all 21 drivers) and lap 5 (all 21 drivers): confirmed rejected**,
  using only the real recorded values with the *most charitable possible*
  status assumption (`{"1"}`, no incident in the previous lap — i.e.
  assuming nothing at all is known about track status). A query run
  specifically to verify this found something the original investigation
  didn't check: **sector1 is `NULL` for every one of the 21 real rows on
  both lap 4 and lap 5** — so both laps are independently caught by the
  missing-sector check alone, with no dependency on `TrackStatus` data ever
  having existed for this race. (Lap 4's ~1955s magnitude also independently
  fails the 300s backstop — belt and suspenders, not a single point of
  failure.)
- **Lap 3: confirmed NOT closeable retroactively for this race, and the
  test says so rather than hiding it.** A query across all 21 drivers found
  every field present and self-consistent (0 of 21 rows missing a sector),
  and lap-3 times (120-170s) are well under the 300s magnitude backstop —
  the *only* signal that could have caught it is `TrackStatus`, which does
  not exist as ground truth for this already-ingested session
  (`track_status` is `NULL` for all 1052 rows — it was ingested before this
  fix). A dedicated test
  (`test_is_plausible_lap_real_monza_lap3_is_a_known_uncloseable_gap`)
  asserts the current, honest behavior (still plausible under a
  "nothing known" assumption) and documents explicitly that this is a
  structural gap the fix cannot close for Monza specifically — only for a
  genuinely live-ingested *future* race, where real `TrackStatus` messages
  would actually arrive. Lap 6 (partially sector-complete, per section 0b's
  table) has the same residual gap for its sector-complete rows.

**Checks run:** `ruff check`/`ruff format --check`, `mypy --strict` (all
three changed files), and the backend unit suite — `test_ingest_live_session
.py` alone: 78 passed (31 handler/derivation tests + 21 lap-4 + 21 lap-5 +
5 lap-3 real-data cases); full `backend/tests/unit/` suite: 377 passed, 0
failed, 0 regressions.

**Explicitly out of scope / not attempted:** pit in/out-lap exclusion (noted
above); a backfill of the existing Monza rows (`track_status` stays `NULL`
and `is_valid` stays `True` for this race's 1052 already-ingested rows — a
partial backfill using only magnitude/sector heuristics would silently miss
lap 3, which was judged worse than leaving the data as-is and documented,
matching this document's own earlier "CP5" recommendation); end-to-end
verification against a real live race (no live race occurred during this
fix — the real-data verification above is the strongest verification
available without one, but it is not a substitute for one).

---

## 5. Issue E — VER's lap/sector charts showed no data all race, while every other driver's updated correctly — ✅ FIXED 2026-09-18

> **✅ Fixed 2026-09-18.** `get_driver_laps`'s long (86400s) cache TTL is now
> reachable only when the result is non-empty, `_is_session_live` reads
> `False`, AND the session's `Race.status` is genuinely `"completed"` — a
> new `_resolve_race_status` check that doesn't depend on the same 30s Redis
> key `_is_session_live` reads, so a lapsed `gaps` key during a real
> ingestor reconnect gap can no longer poison the cache for 24h. Verified
> live against the real Monza session on the running stack (its actual
> state — `status = "scheduled"`, no `gaps` key — is exactly this bug's
> precondition): the TTL written for a real `GET /drivers/{id}/laps` call
> was ~3s, not 86400s. See "Fix summary (2026-09-18)" at the end of this
> section for the full implementation and test coverage.

### What the feature is supposed to do

Every driver's lap-times chart and sector heatmap should update every lap
during a live race, identically in mechanism regardless of which driver is
selected.

### What it currently does

Per the user's direct observation: VER's sector heatmap and lap-times chart
showed **nothing at all** for the entire race — not even the lap-4 spike
every other driver's chart correctly showed (see Issue D). Every other
driver's chart updated correctly, lap by lap, including plotting that same
anomalous value as a real spike before resuming normal data. VER's
underlying DB data is confirmed complete and correct (Issue D above) — this
is a **display/caching** problem, not a data problem, and it is specific to
VER (or, more precisely, to whichever driver's cache-population request
happened to land at the wrong moment — see below).

"Nothing at all" (not a partial/degraded chart, not even the one point
every other driver showed) is itself a meaningful clue: it's consistent
with the frontend receiving a genuinely **empty** dataset for VER, not a
malformed or partially-missing one — which lines up exactly with the known
failure mode described below (a cached `{"items": [], "total": 0}`
response), rather than, say, a rendering bug that would more plausibly
produce a partial or garbled chart instead of a total blank.

### Root cause — a known bug class, already documented once, with a real gap still open in it

`driver_service.get_driver_laps` (the function backing the lap-times/sector
data the charts render) already has a hand-rolled fix for exactly this
shape of bug, and its own docstring documents having hit it before:

```python
DRIVER_LAPS_TTL_SECONDS = 86400
# Live session laps change every ~1-2 minutes (one new lap per driver per
# lap). Confirmed live (2026 Dutch GP dry run): the 86400s TTL above cached
# an EMPTY result (queried before any laps existed yet) and then served that
# stale {"items": [], "total": 0} for the rest of the race, silently masking
# every real lap ingested afterward — 24h is only safe for a session that
# will never change again.
DRIVER_LAPS_LIVE_TTL_SECONDS = 30
```

```python
ttl = (
    DRIVER_LAPS_LIVE_TTL_SECONDS
    if await _is_session_live(client, db, session_id)
    else DRIVER_LAPS_TTL_SECONDS
)
```

So a genuinely live session is *supposed* to always get the short 30s TTL,
self-correcting within half a minute even if the first-ever request for a
given driver/page caught an early/incomplete snapshot. The gap: **this
entirely depends on `_is_session_live()` answering correctly at the exact
moment each cache entry is populated.**

```python
async def _is_session_live(client, db, session_id) -> bool:
    """Whether ingest_live_session.py is actively publishing gaps for this session.
    f1:{season}:{round}:gaps is written by the live ingestor's
    _publish_live_gaps (30s TTL, refreshed on every relevant TimingData
    update — see ingest_live_session.py) — its mere presence is a reliable
    live-vs-historical signal, cheaper than checking session status.
    """
    ...
    return await cache_get(client, f"f1:{season}:{round_number}:gaps") is not None
```

`f1:{season}:{round}:gaps` carries only a 30-second TTL, refreshed by
`_publish_live_gaps()` on every TimingData message the ingestor processes
— which normally keeps it warm continuously. But `ingest_live_session.py`
is confirmed (its own code comments, `_on_close`/reconnect handling) to
experience real connection drops and reconnect loops during live operation
("observed live: a reconnect loop every ~2s" during a rough patch, per an
existing comment in that file) — any such gap lasting more than 30 seconds
would let the `gaps` key expire, making `_is_session_live()` return `False`
for that window even though the race is very much still live.

**The failure mode this produces:** if a driver's lap-data cache entry
happens to get (re-)populated during exactly one of these transient gaps,
`get_driver_laps` applies the **86400-second** TTL instead of 30 —
freezing whatever was true at that single unlucky moment (plausibly an
early, incomplete, or even empty page) for the rest of the race, with no
further self-correction — while every other driver, whose own cache
population happened to land outside such a gap, kept refreshing normally
every 30 seconds all race. This would explain the reported symptom
precisely: nothing about VER specifically, just which driver's request(s)
happened to be unlucky enough to land inside a connectivity gap. VER being
a prominent driver people check first/often (more page loads = more chances
to be the one caught in an unlucky window) is a very plausible reason it
was VER and not someone else, without there being anything VER-specific in
the code.

**Not yet confirmed directly** — Redis was not in a state to inspect the
actual cache entries from the real race by the time this was investigated
(the stack had since restarted), so this is the leading, well-evidenced
hypothesis based on the exact documented failure mode of this exact
function, not a confirmed-via-live-inspection root cause. See below for how
a future session could confirm it more directly.

**Confirmed in the frontend, and it explains why BOTH surfaces broke
together for VER, as one bug rather than two:** `LapTimeChart` and
`SectorHeatmap` (`web/src/components/telemetry/`) both consume
`useDriverLaps`/`driverLapsQueryOptions` (`web/src/hooks/useDriverLaps.ts`)
with the **identical** react-query cache key —
`["driver", "laps", sessionId, driverId]` — explicitly so, per that file's
own comment ("Shared query options so LapTimeChart ... and SectorHeatmap
... hit the same react-query cache entry per driver instead of fetching
independently"). Both ultimately call the same `GET /drivers/{id}/laps`
endpoint, backed by the same single `get_driver_laps` Redis cache entry per
driver traced above. So a single poisoned backend cache entry for VER is
sufficient, on its own, to explain both the empty lap-times chart AND the
empty sector heatmap simultaneously — this is one root cause producing two
visible symptoms, not two separate bugs to investigate.

Also confirmed: the frontend was not at fault for failing to re-poll.
`driverLapsQueryOptions` sets `refetchInterval: 10_000` — react-query was
issuing a fresh request roughly every 10 seconds for the whole race,
including for VER. The staleness lived entirely in the **backend's** Redis
cache (serving the same poisoned response to every one of those refetches),
not in any frontend polling failure.

### Re-verified 2026-09-18 — mechanism upgraded from hypothesis to documented-recurring

The code is exactly as quoted (`driver_service.py` lines 60/67 for the two
TTLs, 456-461 for the selection, 373-385 for `_is_session_live`), and the
frontend claim holds: `driverLapsQueryOptions` uses the shared key
`["driver", "laps", sessionId, driverId]`, `refetchInterval: 10_000`, and
`page_size: 100` — one page covers a full race distance, so there is
**exactly one backend cache entry per driver per session** to poison. That
is what lets a single poisoned entry blank both charts at once.

**New corroboration — this exact false negative is already documented as
having happened for real, in this codebase's own comments.** The original
section inferred the `gaps`-key-expiry risk from the reconnect handling.
Stronger evidence exists: `ingest_live_session.py`'s `_publish_live_gaps`
call site carries an inline comment explaining why it republishes on
*every* message rather than only on change —

> "confirmed live (2026 Dutch GP): gating on 'did anything change' let the
> 30s Redis TTL lapse for a minute-plus at a time whenever F1 resent
> identical gap strings for a stretch (two cars holding a stable gap to 3
> decimal places), which is common enough that **driver_service's
> live-session detection (checking whether this key exists) intermittently
> and incorrectly read as 'not live'**."

So `_is_session_live` transiently returning `False` mid-race is not a
theoretical failure mode — it is a *previously observed, previously
fixed-once* one, and the earlier fix addressed only one of its causes
(identical-gap-string suppression). A connection drop longer than 30s is a
second, independent way to produce the identical lapse, and nothing in
`get_driver_laps` is resilient to it. This moves Issue E from "leading
hypothesis" to "known failure mode with a known precedent, one unconfirmed
instance." It still is **not** directly confirmed for Monza specifically —
Redis was flushed and the containers recreated long before re-verification,
so the actual poisoned entry can never be inspected.

**Research step 3 is closed at zero cost:** `_is_session_live` has exactly
**one call site** in the entire backend — `get_driver_laps` (grep returns
only its own definition, that one call, and two test references). There are
no other consumers to audit, so the blast radius of a transient false
negative is precisely this one cache.

This also means the fix is well-contained: nothing else depends on
`_is_session_live`'s correctness, so hardening `get_driver_laps` against it
cannot regress another feature.

### Files involved

- `backend/services/driver_service.py` — `get_driver_laps`, `_is_session_live`,
  `DRIVER_LAPS_TTL_SECONDS`/`DRIVER_LAPS_LIVE_TTL_SECONDS`
- `backend/scripts/ingest_live_session.py` — `_publish_live_gaps` (the 30s
  `gaps` key `_is_session_live` depends on), the connection/reconnect
  handling (`_on_close`, the reconnect loop mentioned in this file's own
  comments)
- `web/src/hooks/useDriverLaps.ts` — `driverLapsQueryOptions` (the shared
  query key/cache both charts key off)
- `web/src/components/telemetry/LapTimeChart.tsx`,
  `web/src/components/telemetry/SectorHeatmap.tsx` — confirmed consumers of
  that same shared hook

### What needs investigating / researching before fixing

1. **Confirm this actually happened**, don't just trust the plausible
   mechanism — if the live worker/backend container logs from race day are
   still available anywhere (they likely aren't, given a restart since),
   or if Sentry captured anything relevant, check them. Otherwise, the next
   real live race is the only way to directly confirm: watch
   `f1:{season}:{round}:gaps`'s TTL during a live session and correlate any
   observed lapse with which driver's chart (if any) goes stale afterward.
2. **The real fix likely isn't `_is_session_live` itself** (its mechanism
   is reasonable) **but making `get_driver_laps` resilient to
   `_is_session_live` being transiently wrong** — options, not pre-decided:
   (a) a much shorter "unknown/live" default TTL applied whenever
   `_is_session_live` returns `False` but the session's `Race.status` isn't
   `completed` (cheap, likely sufficient); (b) don't cache an empty/near-empty
   result at all regardless of the computed TTL (an empty page is exactly
   the highest-risk case to freeze); (c) something else. Needs a real
   design decision, not just picking (a) reflexively.
3. **Audit other `_is_session_live`-gated call sites** for the same class of
   risk — this function may be reused elsewhere in `driver_service.py` or
   beyond; a transient false-negative here could plausibly affect more than
   just this one cache.
4. **Consider whether the underlying connection-drop frequency itself is
   worth improving** (separate from the caching fix) — if reconnects are
   frequent enough in real operation to matter this much, that's worth its
   own look, though this document doesn't establish how often that really
   happens in practice (the "~2s reconnect loop" comment describes one
   observed bad patch, not a general rate).

### Fix summary (2026-09-18)

Implemented research step 2's option (a) (a status-based TTL floor) plus
part of (b) (never long-cache an empty result), chosen over touching
`_is_session_live` itself — its own mechanism is reasonable, per the
research note; the gap was `get_driver_laps` trusting it alone.

- **`_resolve_race_status`** (new) — a direct `Race.status` query, scoped to
  the session, independent of the Redis `gaps` key `_is_session_live` reads.
  A live-ingested race stays `"scheduled"` for its entire life unless
  separately re-processed by `ingest_historical.py` (confirmed true for
  Monza itself — `status` is still `"scheduled"` at verification time), so
  this is a reliable signal a transient Redis gap can't fool.
- **`get_driver_laps`** rewritten so the 86400s TTL is reachable only when
  **all three** hold: the result is non-empty, `_is_session_live()` reads
  `False`, **and** `_resolve_race_status()` returns `"completed"`. An empty
  result always gets the short TTL regardless of status (research step 2's
  option (b) — the original Dutch GP dry-run finding this module's own
  `DRIVER_LAPS_TTL_SECONDS` comment already documents, an empty page is the
  highest-cost thing to freeze). The extra DB query only runs on the
  already-uncommon path (cache miss, not currently live-flagged, non-empty)
  — the common live-polling and genuinely-historical paths cost the same as
  before.

**Verified two ways**, not just unit tests:

- **Real-DB verification against the actual Monza session on the running
  stack** — its real state (`Race.status = "scheduled"`, no `gaps` key in
  Redis) is exactly this bug's precondition. A real authenticated
  `GET /drivers/{id}/laps?session_id=<monza>` call was made against the live
  backend; the Redis key written for it showed **TTL ≈ 3s**, not the 86400s
  the old logic would have written for this exact combination.
  - **Unit tests** (`backend/tests/unit/test_driver_service.py`): the
  not-live/not-completed case (short TTL — the Monza-shaped bug),
  not-live/completed (long TTL — non-regression), empty result never gets
  the long TTL regardless of status, and a genuinely-live session (`gaps`
  key present) short-circuits before `_resolve_race_status` is ever
  called (confirmed via call-count assertion, not just the resulting TTL).

**Checks run:** `ruff check`/`ruff format --check`, `mypy --strict`, and the
backend unit suite — `test_driver_service.py` alone: 11 passed (4 new); full
`backend/tests/unit/` suite: 345 passed at the time this fix landed (0
regressions against the 341 baseline CLAUDE.md's Notes recorded before this
session).

**Not directly confirmed:** whether this exact mechanism (a transient
`_is_session_live` false negative poisoning one driver's cache entry) is
what actually happened to VER's charts at Monza — Redis had already been
reset by the time of the original investigation, so that remains the
leading, well-evidenced hypothesis rather than a confirmed root cause (see
this section's own re-verification note above). What's now fixed is the
mechanism itself, regardless of whether it's specifically what hit VER that
race.

---

## 6. Cross-cutting observations (not full issues, just worth carrying forward)

- **Real DNFs this race, for reference:** LEC (1 lap), STR (26 laps), ALO
  (23 laps) all stopped short of 53 — three real retirements in one race is
  a good, realistic test case for Issue C's eventual fix and its
  verification.
- **`track_status` is entirely NULL across this whole live session** — a
  standing gap in `ingest_live_session.py`, surfaced while investigating
  Issue D but not itself chased further here. **Re-verified 2026-09-18:
  root cause found — the `TrackStatus` topic is subscribed but has no
  handler at all, so it is received and discarded. See Issue D's
  re-verification note.**
- **`SessionInfo` is likewise subscribed with no handler** (found alongside
  the `TrackStatus` finding) — same shape, unexamined consequences. Worth a
  look whenever anyone next touches `_on_feed`'s dispatch.
- **`LapCount` is NOT subscribed**, and it is the authoritative source of
  scheduled race distance F1 broadcasts live (see Issue A's re-verification
  note). Adding it is the cleanest fix for Issue A's `total_laps` gap.
- **No raw feed message is ever recorded anywhere**, which is what makes
  Issue C unfixable retroactively. A flag-gated raw-message recorder would
  make every future live-ingestion bug of this class diagnosable after the
  fact, rather than requiring the bug to recur while someone is watching.
- **The `SectorTime` table is dead code** (zero rows, ever, for any
  session) — confirmed unused by the one place that would plausibly read
  it. Not urgent, but worth a decision (finish wiring it, or remove it)
  next time anyone is in this part of the schema.
- **`isReplayActive`'s naming is misleading** (`web/src/hooks/useStrategy.ts`)
  — it's actually "is live telemetry currently flowing" (true for both a
  genuine live race and Demo Replay), not "is a Demo Replay active"
  specifically. This isn't a bug by itself, but caused real confusion while
  tracing Issue A and is worth a rename or at least a clarifying comment
  for whoever works on this file next.

---

## 7. Gap Summary

Updated 2026-09-18 to reflect the re-verification pass (section 0b).

| Issue | Correct behavior | Current behavior | Confirmed root cause | Fix complexity (revised 2026-09-18) |
|---|---|---|---|---|
| A: pit window > race length | Never recommend past the last lap | 74% of all predictions exceeded race length (max lap 93); at lap 38 **every** driver showed lap 78 | Two layers: unbounded `optimal_pit_lap` + `total_laps` proxy that collapses to ≈`current_lap` mid-race, making the capped path return NULL 94.6% of the time | **✅ Fixed 2026-09-19 (4 checkpoints)** — real `Session.total_laps` (schema + both ingestion paths), 3 consumers switched to prefer it (a 3rd found beyond the original 2), `optimal_pit_lap` clamped, frontend surfaces uncertainty honestly. 155/158 real sessions backfilled; 429 backend + 56 web tests passing |
| B: undercut score is bistable and race-state-blind | Alert reflects a realistic, stable strategic threat | Score swings 0.07 → **1.00** → 0.01 on consecutive laps; 64% of rows pinned at 0 or 1; 33 alerts fired | Primarily calibration: the deterministic stint-delta appears to swamp `_sampled_noise`, saturating the win fraction. Race-state blindness is a real but **secondary** layer | High — **re-scoped**: a remaining-laps gate would not have suppressed the observed 100% alert (it fired at lap 30 of 53). Needs calibration measurement first |
| C: retired driver frozen in timing tower | Retiree drops out of active standings | LEC shown all race after a lap-1 retirement | `_update_gap_state`'s eviction only fires on the exact string `"RETIRED"` | **Blocked on evidence** — no raw feed was recorded and race-day logs are gone, so this cannot be root-caused retroactively. Either guess at a backstop, or add feed recording and fix after the next live race |
| D: bogus lap times around a red flag | Implausible/unrepresentative laps flagged, not stored as valid racing laps | Laps **3-6** distorted (lap 4 ≈1955s), all marked `is_valid=True`; `track_status` NULL everywhere | No plausibility check; `is_valid` hardcoded `True`; **and `TrackStatus` is subscribed but has no handler**, so the flag signal is received and discarded | **✅ Fixed 2026-09-18** — `_handle_track_status` + `_is_plausible_lap` (FastF1-equivalent checks). All 21 real lap-4 AND lap-5 rows confirmed rejected using real DB values; lap 3 confirmed NOT closeable retroactively for this race (no real `TrackStatus` ground truth exists), documented rather than hidden |
| E: one driver's charts showed no data all race | Every driver's live chart updates identically | Only VER's lap/sector charts never updated; every other driver's did | `get_driver_laps` picks its TTL from `_is_session_live`, which is **documented to have transiently read `False` mid-race before** (2026 Dutch GP); an unlucky cache population then gets an 86400s TTL | **✅ Fixed 2026-09-18** — `get_driver_laps` now also floors on real `Race.status == "completed"`, independent of the Redis key `_is_session_live` reads. Verified live against the real Monza session (its exact precondition): TTL written dropped from would-be 86400s to ~3s |

None of these affect historically-ingested (post-race, `ingest_historical.py`)
sessions — all five are specific to the live path
(`ingest_live_session.py` and the live-serving branches of
`strategy_service.py`/`prediction_worker.py`/`driver_service.py`/the
frontend hooks that key off live telemetry).

---

## 8. Anchor Prompt for Resumption — paste into the new session

> **Note (2026-09-18):** the prompt below is the ORIGINAL anchor, written
> before the re-verification pass. It is still broadly accurate, but its
> summaries of issues B, C and D are now superseded — B's framing was wrong
> (the defect is score bistability, and the cited ANT/VER lap-38 alert is
> not what the DB shows), C is blocked on evidence that no longer exists,
> and D spans laps 3-6 with its red flag now confirmed. A session resuming
> this work should read section 0b and each issue's dated re-verification
> note rather than relying on the summary below.

```
Read docs/live-race-ingestion-and-strategy-gaps-monza-2026.md in full before
doing anything else. It documents five distinct, real problems discovered
2026-09-11 by reviewing the local DB after a real live-ingested race
(Italian GP 2026, Round 13, Monza, 53 laps) that the user watched live in
the UI. Note issue D was originally written as a single issue conflating
two things, then split into D+E once the user clarified a key fact after
the first draft — read D's own correction note at the top of its section
before trusting anything else in this summary about it.

A. Pit-window recommendation exceeded the real race length (predicted lap
   78 on a 53-lap race) — two-layer bug: an unbounded StrategyPrediction.
   optimal_pit_lap field, and a total_laps proxy (MAX(lap_number) so far)
   that's meaningless while a race is still in progress.
B. An undercut-threat alert fired at 100% confidence with only 15 laps
   remaining, with no realistic chance either driver pits again —
   strategy_service._undercut_overcut_probability has no race-state or
   remaining-laps awareness at all.
C. A driver (LEC) who retired after lap 1 stayed visible in the live
   timing tower for the entire rest of the race — ingest_live_session.py's
   retirement eviction only fires on the exact string "RETIRED" in F1's
   GapToLeader field, which apparently didn't arrive for this specific
   retirement.
D. Every driver's lap-4 time was ingested as ~1955 seconds (a likely
   red-flag/session-stoppage artifact from F1's own feed), stored with no
   plausibility check and is_valid hardcoded True. Confirmed field-wide
   (every driver, not just one) — the user confirmed every driver's chart
   OTHER than VER's plotted this correctly as a real, visible spike, then
   resumed normal data — so this is real and worth fixing on data-integrity
   grounds, but it is NOT a charting bug and does NOT explain VER's own
   symptom. See E for that.
E. Only VER's lap-times/sector charts showed NOTHING at all for the whole
   race (not even the lap-4 spike everyone else showed) — every other
   driver's updated correctly, lap by lap. VER's DB data is confirmed
   complete; this is a caching bug, not a data bug. LapTimeChart and
   SectorHeatmap are confirmed (web/src/hooks/useDriverLaps.ts) to share the
   exact same query key/cache entry per driver, so one root cause explains
   both symptoms breaking together for VER specifically, not two bugs.
   driver_service.get_driver_laps applies a short 30s cache TTL only when
   _is_session_live() reads True; that check depends on a 30s-TTL Redis key
   the live ingestor refreshes continuously EXCEPT during a real,
   documented class of connection-drop/reconnect gap. If a driver's cache
   entry happens to get populated during exactly such a gap, it gets
   poisoned with an 86400s TTL instead of 30s and never self-corrects for
   the rest of the race — explaining why it was one driver, not a
   mechanism specific to VER (VER being a driver people check often is a
   plausible reason it was them and not someone else). Not yet confirmed
   via direct evidence from race day (Redis had already been reset by
   investigation time) — this is the leading, well-evidenced hypothesis,
   not a confirmed root cause.

This document is investigation-only — nothing has been fixed, and no
specific fix has been chosen for any of the five. Each issue's own section
lists concrete "what needs investigating/researching" next steps — several
require real data this session didn't have time to pull (e.g. the raw F1
feed around LEC's actual retirement, confirmation of a real red flag at
Monza 2026, direct confirmation of E's caching hypothesis from a future live
race, a direct count of how often recommended_pit_lap was null during this
real race). Do NOT skip straight to implementing a fix for any of these
without first doing that issue's own listed research — several of the
"fixes" have real, undecided design questions (what should total_laps be
sourced from? what does "race-state-aware" mean for the undercut alert?),
not just a bug to patch.

Independently re-verify this document's findings against the current
codebase and, where possible, the real DB (the Monza session's data should
still be present locally unless the stack has been reset) before proposing
anything — same rigor as every other investigation in this project
(docs/tire-deg-model-quality-and-rival-pit-behavior.md is the reference for
the expected standard of evidence: real queries, real code reads, no
assumptions stated as fact). Issue D/E's own history in this document is a
live example of why: an initial, plausible-sounding theory (D) turned out
to be wrong about the actual reported symptom once a follow-up question
from the user surfaced a fact that contradicted it — treat every finding
here as provisional in the same way until independently re-confirmed.

Propose a plan — which issue(s) to tackle, in what order, and how — and
wait for approval before implementing anything. These five issues are
independent of each other (different files, different mechanisms) and can
be picked up in any order or split across sessions; they don't need to be
fixed together. Do not run git commands unless explicitly asked.
```
