# Strategy Simulator fix session — handoff (2026-08-30, session 2)

> **Status:** WET-model crash and current_lap validation are ✅ fixed and verified
> (unit + integration tests, plus a live worker repro). Several real, distinct
> issues were found along the way and are documented below as still open —
> read the relevant section before touching any of them, don't re-derive from
> scratch.
>
> This session followed on from an earlier same-day session (Opus 5,
> investigation-and-planning) that produced
> `docs/simulator-issues-wet-model-and-position-context.md` — read that file
> first if you haven't; this document assumes its Part A/B1/B2 analysis as
> background and only re-summarizes what actually changed since then.

---

## TL;DR — what changed today

| # | Item | Status | Where |
|---|---|---|---|
| 1 | WET tyre model 8-vs-6 feature schema mismatch (crashes any WET-compound sim) | ✅ fixed | `tire_deg_model.py`, `race_simulator.py`, both `_load_models` copies |
| 2 | `current_lap` had zero validation anywhere — a 68-lap what-if silently ran against a 44-lap race | ✅ fixed | `simulate_schema.py`, `strategy_service.py`, `apis/v1/strategy.py`, `prediction_worker.py` |
| 3 | `_run_simulation` skipped `get_engine().dispose()` on any exception (found while testing #2) | ✅ fixed | `prediction_worker.py` |
| 4 | `predicted_finish_time` isn't a real elapsed time, just relative deltas | 🔴 deferred | `race_simulator.py` |
| 5 | `driver_id_encoded` has no real skill signal (hash only) | 🔴 deferred | `race_simulator.py`, `tire_deg_model.py`, `pit_predictor.py` |
| 6 | No cross-driver reactive/strategic adaptation in the Monte Carlo | 🔴 deferred, long-term | `race_simulator.py` |
| 7 | NULL-lap cumulative-sum bug (4 call sites) | 🔴 deferred (pre-existing, CLAUDE.md item A) | `telemetry_service.py`, `strategy_service.py`, `prediction_worker.py` (x2) |
| 8 | `tire_deg_wet.pkl` needs a real 6-feature retrain | 🔴 deferred (pre-existing) | `scripts/train_models.py` |
| 9 | Promotion guard has no feature-schema compatibility check | 🔴 deferred (pre-existing) | `scripts/train_models.py` |
| 10 | Tyre models have no track-condition input (dry-INTER/WET modelled as wet) | 🔴 deferred (pre-existing) | `tire_deg_model.py`, `race_simulator.py` |
| 11 | `telemetry_worker._persist_lap` has the same dispose-on-exception bug as #3, unfixed | 🔴 deferred | `telemetry_worker.py` |
| 12 | Frontend never surfaces the new validation errors to a user | 🔴 deferred | `web/src/pages/SimulatorPage.tsx`, `desktop/` copy |

Items 7-10 pre-date this session (from
`docs/simulator-issues-wet-model-and-position-context.md` / existing CLAUDE.md
Deferred Wiring) and are only re-listed here because item 5's/6's discussion
directly built on them — see each item's own section for what's actually new
today vs. what's carried forward.

---

## Part 1 — What was fixed today

### 1a. WET tyre model schema mismatch (Part A of the prior session's doc)

Production `tire_deg_wet.pkl` was an 8-feature model (a 2026-07-10
weather-experiment leftover) while soft/medium/hard/inter are all 6-feature —
any simulation touching WET crashed the whole Monte Carlo task with
`ValueError: X has 6 features, but StandardScaler is expecting 8 features`.

**Fix (2b + 2a from the prior doc):**
- `backend/services/ml/tire_deg_model.py` — new `INCOMPATIBLE_TYRE_MODEL_FALLBACKS
  = {"tire_deg_wet.pkl": "tire_deg_inter.pkl"}`, `pipeline_feature_count(pipeline)
  -> int | None` (reads `n_features_in_` safely), `apply_incompatible_model_
  fallbacks(models: dict)` (aliases any mismatched entry to its fallback,
  logs a warning).
- `backend/services/strategy_service.py` and `backend/workers/prediction_worker.py`
  — both `_load_models()` copies call `tire_deg_model.apply_incompatible_model_
  fallbacks(_model_cache)` once, at the end, before returning.
- `backend/services/ml/race_simulator.py::_tire_deg_predictions` — permanent
  backstop, independent of the alias: per compound group, checks
  `pipeline_feature_count(pipeline)` against the built feature vector's width
  and wraps the `predict()`/`predict_life_remaining_batch()` call in
  `try/except Exception`. Either failure degrades just that compound group
  (`delta=0`, `life=MAX_LOOKAHEAD_LAPS`) instead of raising out of
  `simulate_race` — protects against *future* schema drift on *any* compound,
  not just today's known WET case.

**Verified live** (not just in tests): restarted the real `docker-worker-1`
container, ran the exact NOR/lap-68/HARD→WET repro from the prior doc through
the actual Celery queue. Task succeeded; worker log shows:
```
WARNING backend.services.ml.tire_deg_model: tire_deg_wet.pkl has 8 features (expected 6) — aliasing to tire_deg_inter.pkl for this process
```
logged exactly once per worker process (cached in `_model_cache` after).

**Tests:** `test_tire_deg_model.py` (7 new — `pipeline_feature_count`,
`apply_incompatible_model_fallbacks`), `test_race_simulator.py` (3 new fast +
1 new `@slow` mixed-field `simulate_race` test), `test_strategy_service.py` /
`test_prediction_worker.py` (1 wiring test each, asserting the real
`_load_models()` body aliases correctly).

### 1b. B1 mitigation — Simulator no longer auto-picks a partial live-ingest

`strategy_service._fetch_last_ingested_session` (backing `GET /strategy/
last-ingested-session`) had no `status` filter, so it could resolve to a
`status="scheduled"` session from a partial live-ingestion dry run (Zandvoort
2026 R12 — NULL `position` for every row, unevenly-missing laps) instead of a
real completed race.

**Fix:** added `Race.status == "completed"` to that query's `.where(...)`.
Locally this moves the resolved session from Zandvoort R12 →
**Belgian GP 2026 R10** (`da57b9fd-4976-4fce-91a1-c7d0aac9c619`, 44 laps,
verified via a direct call to `strategy_service.get_last_ingested_session`
inside the worker container). The stale Redis key
`f1:strategy:last_ingested_session` (TTL 86400s) was manually deleted from
`docker-redis-1` so this took effect immediately rather than after the TTL.

**Note:** this is a mitigation, not the real fix — Deferred Wiring item A
(the NULL-lap cumulative-sum bug, item 7 below) is the actual root cause and
remains open. A genuinely completed session with its own missing-lap gaps
could still surface the same class of symptom.

### 1c. `current_lap` had no validation anywhere

Full root-cause chain (confirmed by direct code read, not assumed): no
`Field` bounds on `SimulateStrategyRequest.current_lap`/`remaining_laps`/
`current_tyre_age`; `POST /simulate` had no `db` dependency and did zero
lookups before enqueueing; `prediction_worker._build_race_state` only ever
used `current_lap` as a `<= current_lap` filter bound, which is a silent
no-op once `current_lap` exceeds a session's real progress. A repro with
`current_lap=68` against the (real, 44-lap) Belgian GP session ran to
completion with no error, simulating 24 phantom laps.

**Fix, four layers:**

1. **`backend/schemas/simulate_schema.py`** — `current_lap`/`remaining_laps`
   gained `Field(ge=1)`, `current_tyre_age` gained `Field(ge=0)` (0 = fresh
   tyre, legitimately allowed). `_validate_pit_plan` extended: every
   `pit_laps` entry must satisfy `current_lap < lap <= current_lap +
   remaining_laps` — previously an out-of-range forced pit was silently
   never triggered inside `simulate_race`'s loop.
2. **`backend/services/strategy_service.py`** — new public
   `validate_current_lap(db, session_id, current_lap) -> None`. Two checks,
   folded into one function (both callers always want both together): (a)
   session must exist (`NotFoundError` otherwise — was previously a raw
   `NoResultFound` surfacing deep inside `_build_race_state`'s unrelated
   context query), then (b) `current_lap <= (MAX(LapData.lap_number) or 0) +
   1` (`ValidationError` otherwise). The `+1` is deliberate and load-bearing:
   it's what lets `test_simulate_returns_task_id`'s existing zero-lap-data
   pre-race scenario (`current_lap=1`) keep working — see that test before
   changing this ceiling.
3. **`backend/apis/v1/strategy.py::simulate_strategy`** — gained a `db`
   dependency (previously entirely absent) and calls `validate_current_lap`
   before building `task_payload`/calling `.delay()`, so a bad request costs
   no Celery round trip.
4. **`backend/workers/prediction_worker.py::_run_simulation`** — calls the
   *same* `validate_current_lap`, right after opening its own DB session,
   before `_build_race_state` — defense in depth for any caller that
   dispatches `run_race_simulation` directly (`.delay()`/`.run()`), bypassing
   the route entirely (e.g. `replay_pipeline.py`, or a future backfill
   script).

**Tests:** `test_schemas.py` (7 new), `test_strategy_service.py` (5 new —
unknown session, pre-race allowance, one-past-progress allowance, and the
exact reported bug), `test_strategy_endpoint.py` (3 new integration tests —
reject beyond progress + reject unknown session, both asserting
`run_race_simulation.delay` is never called; one new test that calls
`run_race_simulation.run()` **directly**, bypassing the route, to prove the
worker-level check independently catches what the route never got a chance
to).

### 1d. Connection-pool leak found while testing 1c

`_run_simulation`'s `await get_engine().dispose()` sat *after* its
`try/finally` block, so it was silently skipped whenever an exception
propagated out of `async with session_factory() as db:`. This was a latent,
pre-existing gap (the only prior failure mode there was a rare
`NoResultFound`), but `validate_current_lap` raising is now a routine,
*expected* rejection path — so the skip became reliably observable: the new
"call `.run()` directly" integration test passed its own assertion, then
crashed the test fixture's teardown with `RuntimeError: Event loop is closed`
(a stale pooled asyncpg connection, bound to the crashed call's event loop,
colliding with the next `asyncio.run()` in the same test process).

**Fix:** moved `await get_engine().dispose()` into the same `finally` block
as the Redis client cleanup in `_run_simulation`, so it now always runs.

**The identical shape exists in `telemetry_worker._persist_lap`, left
unfixed** — see item 11 below.

### Full verification for 1a-1d

- `pytest backend/tests/unit/ -m unit` → **206 passed**
- `pytest backend/tests/integration/test_strategy_endpoint.py backend/tests/
  integration/test_race_simulation_serialization.py backend/tests/
  integration/test_live_prediction_pipeline.py -m integration` → **9 passed**
- `ruff check` / `mypy --strict` clean on every changed file

---

## Part 2 — What was tested (real-data validation, not synthetic)

Both tests ran against **Belgian GP 2026 R10** (`da57b9fd-4976-4fce-91a1-
c7d0aac9c619`), 44 real laps, via the actual restarted worker process
(`run_race_simulation.delay(...)`, not a mock).

### Test 1 — race leader, forced pit 2 laps from the end

Real leader at lap 42: **ANT** (`6e7ae7bf-40a0-4bf4-8fb3-f3839f10906d`), SOFT,
tyre age 27. Two forced-pit what-ifs at lap 43, `remaining_laps=2`:

| | compound | `position_gain_loss` | `predicted_finish_time` |
|---|---|---|---|
| 1a | MEDIUM (dry, sensible) | -6 | 4390.85s |
| 1b | INTERMEDIATE (wet, on a dry track) | **-5** | **4386.82s** |

**Finding: 1b is not worse than 1a — it's marginally *better* by the model's
own numbers.** This is **item 10** (no track-condition input) manifesting
exactly as `docs/simulator-issues-wet-model-and-position-context.md` Part B2
predicted: INTER's degradation curve is learned entirely from historical
*wet* laps and applied unconditionally regardless of real track state. Not a
new bug — confirms the existing deferred item is real and still unaddressed.

### Test 2 — real pit stop replayed as a what-if

Identified VER's real pit stop: lap 17 (MEDIUM, tyre age 17) → lap 18 (HARD,
fresh). Real lap-18 time minus lap-17 time ≈ 21.4s — lines up almost exactly
with the model's own `PIT_STOP_SECONDS=22.0` constant (a nice independent
sanity check that the pit-cost constant is realistic).

Ran the simulation with VER's exact real inputs: `current_lap=17,
current_compound=MEDIUM, current_tyre_age=17, remaining_laps=27,
pit_laps=[18], compounds=[HARD]` (real position at lap 17: **P4**).

**Result:** `position_gain_loss=-4`, `predicted_finish_time=1581.93s`.
**Reality:** VER finished the real race **P3** (gained a position, not lost
four) with a real final cumulative time of 4604.0s.

**Two distinct findings came out of digging into this gap** — do not conflate
them, they're different bugs with different fixes:

1. **`predicted_finish_time` is not a real elapsed time — item 4 below.**
   Verified directly: ANT's real cumulative time through lap 42 was 4370.54s;
   the two real remaining laps added 219.85s (109.9s/lap) to reach 4590.39s.
   But the simulator's `cumulative_race_time_seconds` only ever adds the tyre
   model's *delta-from-session-median* per simulated lap (small, often
   near-zero), not a full absolute lap time — confirmed by
   `race_simulator.py`'s own module docstring. That's why `predicted_finish_
   time` (1581.93s for Test 2) looks nothing like VER's real total (4604.0s):
   only ~7.6s of relative deltas plus the 22s pit cost were added to the
   1574.35s starting point, not 27 real laps' worth of time (~2900s).

2. **`position_gain_loss` itself is mechanically sound** (traced precisely,
   see below) — its miss on VER is a genuine forecast-accuracy limitation
   (items 5 and 6 below), not a units bug like #1.

**Traced precisely, on direct request, exactly what `position_gain_loss`
represents** (see `race_simulator.py:417-542`):
- `starting_position` is never read inside `race_simulator.py` — it's
  `prediction_worker._build_race_state`'s real DB position at `current_lap`.
- `mean_position` comes from `finishing_positions`, computed **after** the
  entire `for lap_number in range(current_lap+1, total_laps+1)` loop
  finishes — for Test 2, `total_laps = 17+27 = 44`, the real race's actual
  final lap. All 1000 Monte Carlo runs go the full remaining distance before
  this is computed — there is no early/next-lap-only comparison anywhere.
- **All other drivers are dynamically simulated too, not frozen**: every
  compound group's tyre degradation, every driver's own pit-probability
  score (autonomous, model-decided per simulation — only the requester's pit
  lap is forced), and independent per-(sim, driver, lap) Gaussian noise all
  apply to the whole field, confirmed by the `(n_sims, n_drivers)` array
  shapes used throughout `simulate_race`'s loop. The Monte Carlo's
  uncertainty modelling covers the whole field, not just the requester.
- What limits real-world accuracy instead: `driver_id_encoded` has no real
  skill signal (item 5), no rival ever reacts to the requester's what-if
  (item 6), and 27 laps is a long, high-variance horizon for any Monte Carlo
  forecast to hit one specific real outcome — none of this was introduced by
  today's fixes.

---

## Part 3 — Deferred items, one section each

Each section below is written to be picked up independently — you shouldn't
need to re-read this whole document to work on just one.

### 4. `predicted_finish_time` is not a real elapsed race time (units/naming gap)

**Core issue:** the API response field `predicted_finish_time` (and
`confidence_interval`, both sourced from `DriverPositionDistribution
.mean_finish_time_seconds`/`finish_time_p5/p95_seconds` in `race_simulator.py`)
looks like "the driver's predicted total time to finish the race" but is
actually `(real cumulative time through current_lap) + (sum of tyre-model
lap_time_delta values for the simulated remainder) + (pit costs) + (SC
bunching)`. `lap_time_delta` is defined at training time as deviation from a
driver's own session-median lap time (see `tire_deg_model.add_engineered_
features`), not an absolute lap time — so for anything beyond a couple of
remaining laps, this field diverges hugely from a real finish time (confirmed
empirically: off by ~3000s over 27 remaining laps in Test 2 above).

**Why it wasn't a problem before today:** nothing previously compared this
field against real historical data — it's only used for *relative* comparison
between alternative what-if plans for the same driver at the same
`current_lap`, where the systematic understatement mostly cancels out between
plans. Test 2 above is the first time (documented, anyway) it's been checked
against a real number.

**Relevant files:** `backend/services/ml/race_simulator.py` (module docstring
already documents the *design intent* — "does not attempt to model absolute
per-driver pace" — but doesn't flag that the *field name* on the response
schema oversells what it delivers), `backend/schemas/simulate_schema.py`
(`SimulatedRaceOutcome.predicted_finish_time`/`confidence_interval`),
`web/src/pages/SimulatorPage.tsx` (renders this as "Predicted Finish Time" —
check exact wording before assuming what a user sees).

**Starting point for a fix — pick one, don't assume which is right without
asking the person who owns the product decision:**
- (a) **Rename, don't change the math** — e.g. `predicted_time_delta_seconds`
  or similar, and update the frontend label — cheapest, honest about what
  the number already is.
- (b) **Make it real** — add each driver's actual median/mean lap time (or a
  circuit-specific baseline) to every simulated lap's delta before
  accumulating, so `cumulative_race_time_seconds` becomes a genuine absolute
  time. More work, changes `race_simulator.py`'s core loop, needs its own
  round of unit tests re-verifying `test_race_simulator.py`'s existing
  position-sum/reproducibility assertions still hold.
- Either way, `position_gain_loss` (the actually-reliable field) is
  unaffected by whichever option is chosen — don't let (b)'s implementation
  accidentally touch the ranking logic.

### 5. `driver_id_encoded` has no real skill signal

See **CLAUDE.md's own Deferred Wiring entry** (added today, session 2) for
the full writeup — reproduced in brief here so this document is
self-contained: the tyre/pit models' only per-driver feature is
`zlib.crc32(str(driver_id).encode()) % 1000`, a hash with zero relationship
to driving ability. `driver_service._performance_vs_team_avg` already
computes a real, session-relative skill proxy (mean valid lap time vs. season
teammates, same session) — it would need aggregating across sessions into a
stable per-driver number before it could become a training feature, then
wiring into `tire_deg_model.FEATURE_COLUMNS`/`pit_predictor.FEATURE_COLUMNS`
alongside (not replacing) `driver_id_encoded`, then a full retrain +
promotion pass. **Moderate effort** — real accuracy win, some of the data
work is already done. Full detail: CLAUDE.md Deferred Wiring, entry titled
"driver_id_encoded ... has no relationship to actual driving ability."

### 6. No strategic/reactive adaptation between drivers in the Monte Carlo

See **CLAUDE.md's own Deferred Wiring entry** (added today, session 2) for
the full writeup. In brief: every driver's simulated pit decision in
`race_simulator.simulate_race` depends only on their own state that lap —
there is no cross-driver term anywhere in `pit_predictor.FEATURE_COLUMNS` or
in the loop body, so no rival ever reacts to the requester's simulated pit
lap (or to each other). Fixing this needs a fundamentally different,
sequential/reactive simulation architecture instead of today's fully
vectorized batch-across-drivers-and-sims approach (which is exactly what
makes it fast today), likely at a real performance cost. **High effort — a
dedicated session on this would need to start with research** (how real
teams' undercut/overcut game theory is actually modeled, what a tractable
reactive-Monte-Carlo formulation looks like at this scale, whether a
heuristic layered on the existing vectorized core gets most of the value for
a fraction of the cost) **before any implementation** — this borders on its
own research project, not a normal feature-build day. Long-term "nice to
have." Full detail: CLAUDE.md Deferred Wiring, entry titled "The Monte Carlo
simulator has no strategic/reactive adaptation."

### 7. NULL-lap cumulative-sum bug (4 call sites) — pre-existing, still open

Not new today — this is **CLAUDE.md's existing Deferred Wiring item A**.
Re-flagging here only because item 1b (the B1 mitigation) directly depends on
understanding it: `SUM(lap_time_seconds) ... WHERE lap_time_seconds IS NOT
NULL` silently drops out-lap/in-lap/SC laps with no recorded time, so
different drivers' cumulative sums become non-comparable whenever they have
different NULL-lap counts. Four call sites: `telemetry_service
._compute_session_gaps`'s `_GAPS_QUERY`, `strategy_service
._cumulative_race_time`, `prediction_worker._cumulative_race_time`, and
`prediction_worker._build_race_state`'s batched query (the one that matters
for the Strategy Simulator specifically). No easy fix — needs either an
ingestion-time absolute `Lap.Time` capture (FastF1 exposes this separately
from the per-lap delta `LapTime` this codebase currently stores) or
query-time interpolation. **Read CLAUDE.md's own item A in full before
touching this** — it has much more detail (exact numbers from a real
British GP investigation, the four call sites' exact line context) than is
worth duplicating here.

### 8. `tire_deg_wet.pkl` needs a real 6-feature retrain — pre-existing, still deferred

Today's fix (item 1a above) aliases WET to INTER at model-load time as a
working stopgap — a real, dedicated 6-feature WET model has never existed
since the 2026-07-16 feature-set revert. To do this properly: run
`scripts/train_models.py` against the local corpus (produces a 6-feature WET
candidate), then delete `production/tire_deg_wet.pkl.metrics.json` from S3
**first** — the MAE-only promotion guard compares against the stale
8-feature incumbent's `cv_mae=5.7906`, and a 319-lap WET corpus (see
`docs/simulator-issues-wet-model-and-position-context.md`'s Part A data) is
very unlikely to beat that on a fair basis either, so without deleting the
sidecar the guard will just reject the new candidate again. **Low priority**
— 319 valid WET laps across the whole 2018-2025 corpus (2025 holdout has
zero) means any retrained model stays high-variance regardless; this removes
the INTER-alias fudge, it doesn't produce a genuinely accurate WET model.
Full detail: `docs/simulator-issues-wet-model-and-position-context.md`
Part A, Option 1; CLAUDE.md's own Deferred Wiring entry (added session 2
today, titled "Retrain a real 6-feature `tire_deg_wet.pkl`").

### 9. Promotion guard needs a feature-schema-compatibility check — pre-existing, still deferred

Root cause of item 1a/8: `train_models.py::serialize_evaluate_and_upload`'s
`should_promote = current_holdout_mae is None or holdout_mae < current_
holdout_mae` compares MAE only — it has no idea the kept incumbent could be a
different feature schema than what current inference code builds, so it
"correctly" kept a model that crashes in production. Proposed fix: write each
model's `n_features`/`feature_names` into its `.pkl.metrics.json` sidecar at
`upload_model` time, and reject/force-promote based on a schema match/
mismatch, not MAE alone. `tire_deg_model.pipeline_feature_count`/`apply_
incompatible_model_fallbacks` (added today) are a **runtime symptom-guard**,
not a substitute for this — the guard could still promote a new incompatible
model in the future and this item would still apply. Full detail:
CLAUDE.md's own Deferred Wiring entry (added session 2 today).

### 10. Tyre models have no track-condition input — pre-existing, still deferred

Confirmed still real by Test 1 above (item 1b's INTER-on-dry result). Full
analysis already exists: `docs/simulator-issues-wet-model-and-position-
context.md` Part B.3, and CLAUDE.md's own Deferred Wiring entry (added the
session before this one, titled "The tyre-degradation models have no
track-condition input"). Nothing new to add here except: today's real-data
test is a second, independent confirmation this is a live, observable
problem, not a theoretical one.

### 11. `telemetry_worker._persist_lap` has the same dispose-on-exception bug as item 1d, unfixed

`backend/workers/telemetry_worker.py::_persist_lap` has `async with
session_factory() as db: ...` with **no** enclosing `try/finally`, then an
unconditional `await get_engine().dispose()` right before
`_publish_lap_completed(lap)` — if the block ever raises (a DB constraint
violation, anything `LapDataCreate.model_validate` didn't already catch), the
pooled asyncpg connection leaks into whatever `asyncio.run()` call happens
next in that worker process. Not yet observed to bite in production (that
failure mode there would look like slow connection-pool exhaustion over many
failed lap-persists, not an immediate crash — different symptom shape than
the test-fixture-teardown crash that surfaced item 1d).

**Starting point:** the exact fix already applied to `_run_simulation` —
move `await get_engine().dispose()` into a `finally` block wrapping the
`async with session_factory() as db:` block. Should be a small, low-risk
change; write a regression test first that forces an exception inside the
`async with` block (e.g. monkeypatch something to raise) and asserts
`dispose()` still gets called, mirroring how item 1d's bug was actually
*discovered* (a real integration test hit it) rather than reasoned about
abstractly. Full detail: CLAUDE.md's own Deferred Wiring entry (added session
2 today, titled "`telemetry_worker._persist_lap` skips
`get_engine().dispose()`").

### 12. Frontend never surfaces the new validation errors

Deferred explicitly, by the user's own decision mid-session-2, to a later
day — the backend correctness fix (item 1c) stands on its own regardless.
Two gaps, confirmed by reading `web/src/pages/SimulatorPage.tsx` directly:

- `handleRunSimulation`'s `await simulateMutation.mutateAsync(payload)` (the
  initial `POST /simulate` call) has no error handling around it at all — a
  new `404`/`422` from `validate_current_lap` would currently just be an
  unhandled promise rejection in the browser.
- The async-task-`FAILURE` UI path (step 3's status card) renders a fixed
  string `"Simulation failed."` regardless of what actually went wrong — it
  never reads the underlying error message.
- `web/src/utils/errors.ts::getApiErrorMessage` already exists and correctly
  parses this exact backend error shape (`{message, detail}` from
  `f1_strategy_error_handler`) — it's just not called anywhere in
  `SimulatorPage.tsx` today. Wiring it in is likely most of the fix for the
  first gap; the second (task `FAILURE`) needs a bit more thought since
  `GET /simulate/{task_id}`'s `SimulateTaskStatusResponse` doesn't currently
  carry the failure's error message at all — check whether `AsyncResult
  .result` (an exception instance on failure) is worth surfacing through
  that schema, or whether that's out of scope and only the synchronous
  route-level rejection is worth fixing.
- Per the Desktop Sync Protocol (see `desktop/src/README.md`), whatever
  changes here also need porting to `desktop/src/pages/SimulatorPage.tsx`
  (copied-and-adapted, not shared) — check that file too, don't assume it's
  identical.

---

## Key files touched today (for quick navigation)

| Path | What changed |
|---|---|
| `backend/services/ml/tire_deg_model.py` | WET alias helpers (item 1a) |
| `backend/services/ml/race_simulator.py` | `_tire_deg_predictions` backstop guard (item 1a) |
| `backend/services/strategy_service.py` | `_load_models` alias call, `Race.status` filter, `validate_current_lap` (items 1a/1b/1c) |
| `backend/workers/prediction_worker.py` | `_load_models` alias call, `validate_current_lap` call, dispose fix (items 1a/1c/1d) |
| `backend/apis/v1/strategy.py` | `db` dependency + `validate_current_lap` call on `POST /simulate` (item 1c) |
| `backend/schemas/simulate_schema.py` | `Field` bounds + `pit_laps` horizon validator (item 1c) |
| `backend/tests/unit/test_tire_deg_model.py` | 7 new tests (item 1a) |
| `backend/tests/unit/test_race_simulator.py` | 4 new tests (item 1a) |
| `backend/tests/unit/test_strategy_service.py` | 6 new tests (items 1a/1b/1c) |
| `backend/tests/unit/test_prediction_worker.py` | 1 new test (item 1a) |
| `backend/tests/unit/test_schemas.py` | 7 new tests (item 1c) |
| `backend/tests/integration/test_strategy_endpoint.py` | 4 new tests (items 1c/1d) |
| `CLAUDE.md` | 5 new Deferred Wiring entries, 2 new Notes entries — see its own diff for exact wording |
| `docs/simulator-issues-wet-model-and-position-context.md` | Status header updated to record what landed |
| `docs/day-deferred-fixes-session2-handoff.md` | this file |

---

# ANCHOR PROMPT — paste into a new session

```
Read docs/day-deferred-fixes-session2-handoff.md in full before doing
anything else — it documents a completed fix session (WET tyre model alias,
current_lap validation, a connection-dispose bug fix) and a set of distinct
deferred items found while validating those fixes against real Belgian GP
2026 R10 data.

The deferred items, in the file's Part 3, are independent of each other —
pick ONE to work on this session, don't try to fix several at once:

  4.  predicted_finish_time isn't a real elapsed time (units/naming gap)
  5.  driver_id_encoded has no real driving-skill signal
  6.  No strategic/reactive adaptation between drivers in the Monte Carlo
      (long-term, research-first — don't start here unless that's explicitly
      what's wanted)
  7.  NULL-lap cumulative-sum bug, 4 call sites (this is CLAUDE.md's own
      pre-existing Deferred Wiring item A — read that entry directly, it has
      more detail than the handoff doc repeats)
  8.  tire_deg_wet.pkl needs a real 6-feature retrain
  9.  Promotion guard needs a feature-schema-compatibility check
  10. Tyre models have no track-condition input (dry vs wet)
  11. telemetry_worker._persist_lap has the same dispose-on-exception bug
      already fixed in prediction_worker._run_simulation
  12. Frontend (SimulatorPage.tsx, web + desktop) never surfaces the new
      current_lap validation errors to a user

If the user hasn't already told you which item to pick, ask before starting
— several of these (5, 6 especially) are moderate-to-large scope changes
that deserve a plan-first checkpoint discussion, same convention as the
session that produced this handoff doc (propose a checkpoint plan, wait for
approval, implement checkpoint by checkpoint, report + wait between each).

Do NOT run git commands unless explicitly asked.
```
