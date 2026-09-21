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
| 4 | `predicted_finish_time` isn't a real elapsed time, just relative deltas | ✅ fixed 2026-09-03 | `race_simulator.py`, `prediction_worker.py`, `web`/`desktop` formatters + `SimulatorPage.tsx` |
| 5 | `driver_id_encoded` has no real skill signal (hash only) | ⚠️ investigated 2026-09-04, skill feature deferred (not a clean win) — but surfaced and fixed a bigger separate bug: crc32 never matched the real training-time code | `tire_deg_model.py`, `train_models.py`, `retrain_incremental.py`, `strategy_service.py`, `prediction_worker.py`, new `scripts/evaluate_driver_features.py` |
| 6 | No cross-driver reactive/strategic adaptation in the Monte Carlo | 🔴 deferred, long-term | `race_simulator.py` |
| 7 | NULL-lap cumulative-sum bug (4 call sites) | ✅ fixed 2026-09-02 | `telemetry_service.py`, `strategy_service.py`, `prediction_worker.py` (x2), new migration + `backfill_lap_session_time.py` |
| 8 | `tire_deg_wet.pkl` needs a real 6-feature retrain | 🔴 deferred (pre-existing) | `scripts/train_models.py` |
| 9 | Promotion guard has no feature-schema compatibility check | ✅ fixed 2026-09-02 | `scripts/train_models.py`, `scripts/retrain_incremental.py`, `.github/workflows/train-models.yml` |
| 10 | Tyre models have no track-condition input (dry-INTER/WET modelled as wet) | 🔴 deferred (pre-existing) | `tire_deg_model.py`, `race_simulator.py` |
| 11 | `telemetry_worker._persist_lap` has the same dispose-on-exception bug as #3 | ✅ fixed 2026-09-01 | `telemetry_worker.py` (also `_persist_tire_stint` — identical bug shape, found and fixed in the same session, not originally scoped) |
| 12 | Frontend never surfaces the new validation errors to a user | ✅ fixed 2026-09-01 | `web/src/pages/SimulatorPage.tsx`, `desktop/` copy, `mobile/app/simulator.tsx` (not originally scoped — same gap found while reading the file) |

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

**The identical shape existed in `telemetry_worker._persist_lap` (and
`_persist_tire_stint`), left unfixed at the time** — see item 11 below,
fixed in a 2026-09-01 follow-up session.

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

**⚠️ Correction (2026-09-03): the "real final cumulative time of 4604.0s"
above is wrong — it predates item 7's fix and was itself computed via the
exact `SUM(lap_time_seconds)`-drops-NULL-laps bug item 7 fixed.** VER's pit
in/out laps around lap 18 are exactly the kind of NULL-time lap that method
silently drops, undercounting his real total. The validated ground truth
(via `LapData.session_elapsed_seconds`, accurate to ≤0.18s against FastF1's
own classification — see CLAUDE.md's item-A Notes entry) is **5094.285s**,
not 4604.0s — a ~490s difference. This does not change finding 2 above
(`position_gain_loss` is still mechanically sound and still misses VER's
real +1 gain for the same items-5/6 reasons); it only means the *magnitude*
of finding 1's gap was itself unreliable. See item 4's own section below for
the real comparison against the corrected 5094.285s figure.

---

## Part 3 — Deferred items, one section each

Each section below is written to be picked up independently — you shouldn't
need to re-read this whole document to work on just one.

### 4. `predicted_finish_time` is not a real elapsed race time (units/naming gap) — ✅ fixed 2026-09-03

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

**Fixed via option (b) ("make it real"), picked after asking the product
owner directly** (per this section's own "don't assume" guidance). See
CLAUDE.md's own Notes entry ("`predicted_finish_time` made a real elapsed
time") for the full writeup — summarized here for this doc's own
completeness:

- New `DriverRaceState.baseline_lap_time_seconds` (each driver's own real
  median lap time through `current_lap`, computed by `prediction_worker
  ._build_race_state` via `percentile_cont(0.5)` — the same definition
  `tire_deg_model.add_engineered_features` uses for `lap_time_delta`'s own
  training baseline) is added to every simulated lap alongside the model's
  delta, so `cumulative_race_time_seconds` becomes a genuine absolute time.
  A driver with no median of their own falls back to the field's median
  (not 0.0, which would falsely rank them P1). The SC lap-time constant now
  scales off the field's median baseline (`SC_LAP_TIME_MULTIPLIER = 1.4`)
  instead of a small fixed value, for the same real-absolute-time reason.
- `web`/`desktop` gained `formatRaceTime` (h:mm:ss.sss) and now use it for
  the Finish Time columns/tooltip, replacing `formatLapTime` (which has no
  hours segment). Mobile renders neither field — no change needed there.
- **Validated against real data, not just tests:** re-ran this doc's own
  Test 2 (VER's real pit stop replayed) against the real worker — landed
  within **21.5s (0.4%)** of VER's real finish time (see the ⚠️ Correction
  above Part 3 for the corrected 5094.285s ground truth), versus the old
  value being off by ~3400s (67x). Re-ran Test 1 (ANT) too: `position_gain_
  loss` shifted by exactly -1 on both variants (-6→-7, -5→-6) — the
  expected, flagged side effect of real pace now propagating over the
  remaining laps instead of the starting gap staying frozen, not a
  regression. Item 10 (no track-condition input) confirmed still
  unaffected: INTER (1b) still shows a lower cost than MEDIUM (1a),
  unchanged in direction.
- Tests: 3 new in `test_race_simulator.py` (direct `_advance_lap` baseline
  arithmetic, SC-lap-time derivation end-to-end via `simulate_race`), 2 new
  in `test_prediction_worker.py` (field-median fallback, zero-when-field-
  has-none), 4 new in `web/src/__tests__/formatters.test.ts`. Full `pytest
  backend/tests/unit/ -m unit`: 236 passed. Full `pytest backend/tests/
  integration/ -m integration` (real testcontainers): 45 passed. `ruff`/
  `ruff format --check`/`mypy --strict` clean across `backend/`. `web`:
  `vitest run` 32 passed, `tsc --noEmit` clean, `oxlint` clean. `desktop`/
  `mobile`: `tsc --noEmit` clean.

### 5. `driver_id_encoded` has no real skill signal — investigated 2026-09-04, evidence-corrected, deferred

Picked up 2026-09-04. See CLAUDE.md's own ✅-fixed Notes entry ("Driver/circuit
encoding persisted") and its corrected Deferred Wiring entry for the full
writeup — summarized here:

- **Before touching any code**, an offline evaluation harness
  (`scripts/evaluate_driver_features.py`, new — kept as a reusable dev tool
  for future feature questions, not deleted after use) measured two things
  against real holdout data: (a) whether a genuine per-driver skill feature
  (per-driver tyre-degradation slope / lap-time consistency, computed
  prior-sessions-only so neither leaks the training target) actually helps,
  and (b) what the training-vs-inference encoding mismatch this item's
  original write-up flagged as a side note actually costs.
- **(a) was NOT a clean win**: tire_deg holdout MAE improved on HARD/
  INTERMEDIATE (-2% to -10.5%) but got WORSE on SOFT/MEDIUM (+2% to +6%) —
  the two highest-volume compounds. `pit_predictor` showed a small,
  consistent improvement (-1% to -2.8% MAE) across all variants. Given the
  mixed/negative tire_deg result, the feature was deliberately NOT added —
  correcting this document's and CLAUDE.md's prior "moderate effort, real
  accuracy win" framing.
- **(b) was much larger than expected**: scoring the same fitted model on
  holdout with the real training-time `pd.Categorical` code vs. the
  `crc32` stand-in inflates tire_deg MAE by **50-265%** depending on
  compound (MEDIUM, the highest-volume compound, was the worst: +265%).
  This was live in production, undetected by item 9's schema guard (which
  only checks feature *count*, not whether encoded values are
  in-distribution). **This was fixed**, as the session's actual
  deliverable in place of the driver-skill feature — see CLAUDE.md's Notes
  entry for the full mechanism (per-model sidecar maps, resolved
  per-pipeline-call, graceful crc32 fallback).
- **Validated against a real local retrain**, not just the offline
  harness: 4 of 5 tire_deg models (MEDIUM/HARD/INTER/WET; SOFT's candidate
  didn't beat its incumbent, stays on crc32 fallback until a future
  retrain naturally promotes it) picked up real encoding maps; their
  holdout MAEs matched the offline evaluation to 4 decimal places.
  Confirmed live that VER's real trained code (19) differs from the old
  crc32 stand-in (320) and is genuinely what's used. Re-ran this doc's own
  Test 2 (VER's real Belgian GP pit stop) through the actual restarted
  worker as an honest single-anecdote check: `predicted_finish_time`
  landed 26.9s (0.53%) off the corrected 5094.285s ground truth —
  marginally *worse* than item 4's pre-encoding-fix 21.5s (0.4%), and
  `position_gain_loss` moved from -4 to -5 (real outcome: +1). Not a sign
  the fix is wrong — a single 27-lap Monte Carlo forecast isn't a valid
  isolation test (the retrain also naturally updated model weights against
  a grown corpus, and items 5's-skill-signal-gap/6 remain the dominant
  limiter on any one what-if's directional accuracy) — the CP1-equivalent
  same-model-different-codes comparison above is the scientifically valid
  measurement, and it reproduced almost exactly.
- **Incidental validation of item 9**: MEDIUM/HARD/INTER's incumbent
  `.pkl` files were found to be genuinely corrupted
  (`xgboost: input stream corrupted`) during this retrain — item 9's
  schema guard correctly treated the unrecoverable incumbent as
  incompatible and force-promoted working candidates. First real-world
  exercise of that guard; not something this session caused.
- **One documented, deliberate limitation**: `RaceSimulationInput.
  circuit_id_encoded` is a single value shared across every compound group
  in `race_simulator.py`, so it's resolved against the requesting driver's
  own compound — correct in the normal case (all compounds share one
  training run), an approximation otherwise. A fully general fix needs a
  `race_simulator.py` data-model change (per-compound circuit codes), out
  of scope for this session.
- Tests: 10 new unit tests (`test_tire_deg_model.py` — encoding-map
  build/parse/resolve + parallel-cache aliasing; `test_strategy_service.py`
  and `test_prediction_worker.py` — `_load_models()` sidecar-download and
  alias wiring). Full `pytest backend/tests/unit/ -m unit`: 252 passed (was
  236 before this session). Full `pytest backend/tests/integration/ -m
  integration`: 45 passed. `ruff`/`ruff format --check`/`mypy --strict`
  clean across `backend/`.

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

### 7. NULL-lap cumulative-sum bug (4 call sites) — ✅ fixed 2026-09-02

Was **CLAUDE.md's existing Deferred Wiring item A**. Picked up 2026-09-02;
see CLAUDE.md's own Notes entry ("Item A — NULL-lap cumulative-sum
gap/race-time reconstruction fixed via `LapData.session_elapsed_seconds`")
for the full write-up — summarized here for this doc's own completeness:

- **Fixed via the ingestion-time change this item's own analysis
  anticipated:** new `LapData.session_elapsed_seconds` column (migration
  `20260902_add_session_elapsed_seconds_to_lap_data`) captures FastF1's
  absolute `Lap.Time` directly at ingestion, anchored per-session to the
  earliest `LapStartTime` — confirmed populated on 100% of lap rows across
  a 2020-2026 sample, including every row with a NULL `lap_time_seconds`.
- **Backfilled** for all existing local data via new `backend/scripts/
  backfill_lap_session_time.py` (`make backfill-lap-session-time`),
  R-sessions-only: 169,709 rows updated across 155 of 158 sessions.
- **All four call sites** now prefer `session_elapsed_seconds`, falling
  back to the original `SUM(lap_time_seconds)` reconstruction only for a
  live-ingested/never-backfilled session. `_compute_session_gaps`'s
  `_GAPS_QUERY` also dropped its `WHERE lap_time_seconds IS NOT NULL`
  filter — a related bug that understated a driver's reported current lap
  number, not just their cumulative time.
- **Verified against real FastF1 final classifications** for British GP
  2026 R9 and Belgian GP 2026 R10 (not just unit tests) — exact
  position-order match for the top 12 in both, gaps accurate to ≤0.18s
  versus the original bug's 343s error. Confirmed end-to-end through the
  real running backend container's actual `GET /telemetry/{session_id}
  /gaps` route.
- **A genuine, distinct, pre-existing limitation surfaced during this
  verification, not a defect in this fix** — logged as a new CLAUDE.md
  Deferred Wiring entry ("No F1 penalty/post-race-classification data is
  ingested anywhere"): British GP's computed order diverged from the true
  official classification starting at position 9 because a driver (ANT)
  received a post-race time penalty no data source in this codebase
  captures. Confirmed the old `SUM(lap_time_seconds)` code would have
  produced the identical mis-ranking for the same pair — not introduced by
  this fix.
- New tests: 9 unit tests (prefers-elapsed / falls-back-to-sum /
  defaults-to-zero, per call site). Full `pytest backend/tests/unit/ -m
  unit`: 231 passed. Full `pytest backend/tests/integration/ -m
  integration` (real testcontainers): 45 passed. `ruff`/`ruff format
  --check`/`mypy --strict` clean across the entire `backend/` tree.
- **✅ Done 2026-09-02, in a follow-up after merge:** Supabase (production)
  backfill. Confirmed `cd.yml`'s `migrate` job had applied the migration
  first (queried `information_schema.columns` directly against
  `SUPABASE_DIRECT_URL` — column existed, 0/3,196 R-session rows
  populated), then ran the backfill against production. Result: 3,196 rows
  updated across the 3 curated sessions (Canadian GP R5: 1,211; British GP
  R9: 1,113; Belgian GP R10: 872) — an exact match to the local counts.
  Re-verified afterward: 3,196/3,196 populated. Spot-checked British GP
  R9's LEC/RUS/HAM gaps directly on Supabase (RUS +0.399s, HAM +0.806s) —
  bit-for-bit identical to the local values already verified against
  FastF1's own official classification. Full procedure now documented as
  a completed, repeatable-for-future-rounds runbook entry:
  `docs/runbook.md`'s "One-time: backfill session_elapsed_seconds on
  Supabase" section.

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

### 9. Promotion guard needs a feature-schema-compatibility check — ✅ fixed 2026-09-02

Was root cause of item 1a/8: `train_models.py::serialize_evaluate_and_upload`'s
`should_promote = current_holdout_mae is None or holdout_mae < current_
holdout_mae` compared MAE only — it had no idea the kept incumbent could be a
different feature schema than what current inference code builds, so it
"correctly" kept a model that crashes in production. Picked up 2026-09-02; see
CLAUDE.md's own Notes entry ("Model promotion guard gained a
feature-schema-compatibility check") for the full writeup — summarized here
for this doc's own completeness:

- Every sidecar `serialize_evaluate_and_upload` writes now carries
  `n_features`/`feature_names`/`schema_source`. A confirmed schema mismatch
  between the production incumbent and the new candidate — or an incumbent
  `.pkl` that can't even be loaded — now **force-promotes the candidate
  regardless of `holdout_mae`**, via a new `PromotionOutcome` return type
  (`reason="schema_mismatch"`), logged loudly and threaded into
  `retrain_summary.json`/`train-models.yml`'s Slack and release-notes `jq`
  output.
- A legacy incumbent's `.pkl` (one that predates this fix, no schema in its
  sidecar) is downloaded and introspected **once**, then backfilled into its
  sidecar — every later comparison for that filename reads the sidecar
  directly, no repeat download.
- `tire_deg_model.pipeline_feature_count`/`apply_incompatible_model_
  fallbacks` (item 1a, unchanged) stay in effect as the runtime
  symptom-guard — this fix closes the promotion-time gap that let an
  incompatible model reach production in the first place, it doesn't
  replace the runtime alias.
- **Item 8 (WET retrain) is now unblocked, not done** — its old "delete the
  sidecar manually first" workaround is obsolete (the guard force-promotes
  automatically on the schema mismatch now), but the actual retrain hasn't
  happened.
- New tests: `backend/tests/unit/test_train_models.py` (new file, 12 tests —
  3 direct `fitted_feature_count` cases plus 9 covering the full decision
  table, including the exact 8-vs-6-feature WET shape force-promoting
  despite a *worse* `holdout_mae`). Full `pytest backend/tests/unit/ -m
  unit`: 222 passed, no regressions. `ruff`/`mypy --strict` clean.
- **Not yet verified against a real training run/S3** — `train-models.yml`
  currently fetches zero 2026 laps (see CLAUDE.md's escalated
  GitHub-Actions/FastF1 deferred item), so the first real run exercising
  this guard should wait for that to be resolved or consciously accepted.

### 10. Tyre models have no track-condition input — pre-existing, still deferred

Confirmed still real by Test 1 above (item 1b's INTER-on-dry result). Full
analysis already exists: `docs/simulator-issues-wet-model-and-position-
context.md` Part B.3, and CLAUDE.md's own Deferred Wiring entry (added the
session before this one, titled "The tyre-degradation models have no
track-condition input"). Nothing new to add here except: today's real-data
test is a second, independent confirmation this is a live, observable
problem, not a theoretical one.

### 11. `telemetry_worker._persist_lap` has the same dispose-on-exception bug as item 1d — ✅ fixed 2026-09-01

Was deferred explicitly at end-of-session-2, picked up 2026-09-01. See
CLAUDE.md's own Notes entry ("`telemetry_worker._persist_lap` (and
`_persist_tire_stint`) skipped `get_engine().dispose()` on exception") for
the full writeup — summarized here for this doc's own completeness:

- **`_persist_lap` fixed as scoped:** `await get_engine().dispose()` moved
  into a `finally` block wrapping the `async with session_factory() as db:`
  block — the exact fix already applied to `prediction_worker
  ._run_simulation` under item 1d. `_publish_lap_completed(lap)` stays
  outside the `try/finally`, unreachable on any exception, unchanged from
  before.
- **`_persist_tire_stint` fixed alongside, not originally scoped:** found
  while fixing `_persist_lap` — the identical bug shape in the same file
  (`record_tire_stint`'s persist function), fixed with the same pattern on
  request, same session.
- New tests (`backend/tests/unit/test_telemetry_worker.py`, new file): 4
  tests, one pair per function — forces an exception inside the `async
  with` block via a minimal `_FakeSession` stand-in and asserts `dispose()`
  still runs (mirroring how item 1d's bug was originally *discovered*, a
  real integration test hitting it, rather than reasoned about abstractly),
  plus a happy-path sibling confirming unchanged success behavior.
- Verified: `ruff check`/`mypy --strict` clean on both changed files; new
  test file 4/4 passed; full `backend/tests/unit/ -m unit` 210 passed
  (206 pre-existing before this item + 4 new), no regressions.

### 12. Frontend never surfaces the new validation errors — ✅ fixed 2026-09-01

Was deferred explicitly, by the user's own decision mid-session-2, to a
later day. Picked up 2026-09-01; see CLAUDE.md's own Notes entry ("Frontend
never surfaced validate_current_lap's rejection or a task FAILURE's
reason") for the full writeup — summarized here for this doc's own
completeness:

- **Synchronous rejection:** `handleRunSimulation`'s `mutateAsync` call is
  now try/caught; `setStep(3)` only runs on success; the rejection renders
  inline on step 2 via `getApiErrorMessage` (already existed, just wasn't
  called here). `handleReset` calls `simulateMutation.reset()`.
- **Async task `FAILURE`:** `SimulateTaskStatusResponse` gained `error: str
  | None`, populated by `get_simulation_result` — an `F1StrategyError`'s own
  `.message` passes through verbatim (verified against a real Celery
  `RedisBackend`, `task_serializer="json"` faithfully reconstructs known
  exception classes), anything else becomes a fixed generic string (this
  route is unauthenticated, so no arbitrary exception text is ever echoed).
- **Ported to all three clients, not just web + desktop as originally
  scoped:** `mobile/app/simulator.tsx` had the identical gap (bare
  `mutateAsync`, hardcoded `"Simulation failed."`) — not mentioned above
  when this item was written, found while reading the file at the start of
  the follow-up session, fixed alongside using the same
  `getApiErrorMessage`/`role="alert"` pattern already established in
  `mobile/app/(auth)/login.tsx`.
- New tests: 2 backend integration tests (`test_strategy_endpoint.py`), 2
  web tests (`SimulatorPage.test.tsx`, new file). `desktop`/`mobile` have no
  test runner — verified via `tsc --noEmit` only, per their own existing
  sync-protocol convention.

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

## Key files touched — 2026-09-01 follow-up (items 11 & 12)

| Path | What changed |
|---|---|
| `backend/schemas/simulate_schema.py` | `SimulateTaskStatusResponse.error: str \| None` (item 12) |
| `backend/apis/v1/strategy.py` | `get_simulation_result` derives `error` on `FAILURE` — `F1StrategyError.message` pass-through, generic fallback otherwise (item 12) |
| `backend/tests/integration/test_strategy_endpoint.py` | 2 new tests — `F1StrategyError` pass-through, generic-exception safe-message (item 12) |
| `backend/workers/telemetry_worker.py` | `_persist_lap` **and** `_persist_tire_stint` — dispose moved into `finally` (item 11) |
| `backend/tests/unit/test_telemetry_worker.py` | new file — 4 tests, one raise/success pair per function (item 11) |
| `web/src/pages/SimulatorPage.tsx` | try/caught `mutateAsync`, inline error banner, `error`-aware `FAILURE` text, `reset()` on `handleReset` (item 12) |
| `web/src/types/simulate.ts` | `SimulateTaskStatusResponse.error` (item 12) |
| `web/src/test/setup.ts` | `scrollIntoView` jsdom stub — reusable infra, not scoped to item 12 alone (item 12) |
| `web/src/__tests__/SimulatorPage.test.tsx` | new file — 2 tests (item 12) |
| `desktop/src/pages/SimulatorPage.tsx` | same fix as web's copy (item 12) |
| `desktop/src/types/simulate.ts` | `SimulateTaskStatusResponse.error` (item 12) |
| `mobile/app/simulator.tsx` | same fix — not originally scoped, found while reading the file (item 12) |
| `mobile/src/types/simulate.ts` | `SimulateTaskStatusResponse.error` (item 12) |
| `CLAUDE.md` | 2 new ✅-fixed Notes entries (items 11 & 12), Phase Tracker updated |
| `docs/day-deferred-fixes-session2-handoff.md` | this file |

---

## Key files touched — 2026-09-02 follow-up (item 9)

| Path | What changed |
|---|---|
| `backend/scripts/train_models.py` | New `fitted_feature_count`, `_resolve_incumbent_schema`, `PromotionOutcome`; `serialize_evaluate_and_upload` rewritten with the schema-mismatch decision table; `train_all()`'s 3 call sites pass real `feature_names` (item 9) |
| `backend/scripts/retrain_incremental.py` | `_promote_and_record` widened `metrics` type, threads `feature_names` through, correctly unpacks `PromotionOutcome` into `summary` (fixes a `json.dumps` crash the old code would have hit); `retrain()`'s 3 call sites pass real `feature_names` (item 9) |
| `.github/workflows/train-models.yml` | Both `jq` summary lines render `promotion_reason` (item 9) |
| `backend/tests/unit/test_train_models.py` | new file — 12 tests (item 9) |
| `CLAUDE.md` | Deferred Wiring entry → ✅ done, 1 new Notes entry, item 8's entry updated (obsolete workaround note), Phase Tracker updated |
| `docs/day-deferred-fixes-session2-handoff.md` | this file |

---

## Key files touched — 2026-09-02 follow-up (item 7)

| Path | What changed |
|---|---|
| `backend/models/telemetry.py` | New `LapData.session_elapsed_seconds` column (item 7) |
| `backend/migrations/versions/20260902_add_session_elapsed_seconds_to_lap_data.py` | New migration (item 7) |
| `backend/scripts/ingest_historical.py` | New `resolve_session_start`/`compute_session_elapsed_seconds` (shared, also used by the backfill script); `_upsert_lap_data` populates the new column (item 7) |
| `backend/scripts/backfill_lap_session_time.py` | New file — R-sessions-only backfill script (item 7) |
| `Makefile` | New `backfill-lap-session-time` target (item 7) |
| `backend/services/telemetry_service.py` | `_GAPS_QUERY`/`_compute_session_gaps` prefer `session_elapsed_seconds`, dropped the `WHERE lap_time_seconds IS NOT NULL` filter, docstring updated (item 7) |
| `backend/services/strategy_service.py` | `_cumulative_race_time` prefers `session_elapsed_seconds` (item 7) |
| `backend/workers/prediction_worker.py` | `_cumulative_race_time` (same fix as strategy_service's copy) and `_build_race_state` (reuses its existing `position_subq`/`position_join` to also pull `session_elapsed_seconds`) (item 7) |
| `backend/tests/unit/test_telemetry_service.py` | 3 new tests, 3 existing tests' fixtures updated to the new row shape (item 7) |
| `backend/tests/unit/test_strategy_service.py` | 3 new tests (item 7) |
| `backend/tests/unit/test_prediction_worker.py` | 3 new tests, 3 existing tests' fixtures updated (item 7) |
| `CLAUDE.md` | Deferred Wiring item A → ✅ done, 1 new Notes entry, 1 new Deferred Wiring entry (post-race-penalty gap discovered during verification), Phase Tracker updated |
| `docs/day-deferred-fixes-session2-handoff.md` | this file |

---

## Key files touched — 2026-09-03 follow-up (item 4)

| Path | What changed |
|---|---|
| `backend/services/ml/race_simulator.py` | New `DriverRaceState.baseline_lap_time_seconds`, `_advance_lap` adds it per racing lap, `SC_LAP_TIME_MULTIPLIER`-derived SC lap time, module docstring updated (item 4) |
| `backend/workers/prediction_worker.py` | `_build_race_state`'s existing batched `cumulative_time_query` gained a `percentile_cont(0.5)` column (no new DB round trip); field-median fallback chain; wired into both `DriverRaceState` construction sites (item 4) |
| `web/src/utils/formatters.ts`, `desktop/src/utils/formatters.ts` | New `formatRaceTime` (h:mm:ss.sss), kept identical between web/desktop (item 4) |
| `web/src/pages/SimulatorPage.tsx`, `desktop/src/pages/SimulatorPage.tsx` | `formatLapTime` → `formatRaceTime` at all finish-time call sites (item 4) |
| `web/src/components/landing/FeatureTiles.tsx` | Landing mock's `SimulatorTile` finish-time render switched to `formatRaceTime` (item 4) |
| `backend/tests/unit/test_race_simulator.py` | 3 new tests (item 4) |
| `backend/tests/unit/test_prediction_worker.py` | 2 new tests (item 4) |
| `web/src/__tests__/formatters.test.ts` | 4 new tests (item 4) |
| `CLAUDE.md` | New ✅ Notes entry for item 4; condensed the item 7/9/11/12 Notes entries (bloat control — see this session's own request); Phase Tracker updated |
| `docs/day-deferred-fixes-session2-handoff.md` | This file — item 4 → ✅ fixed in the TL;DR table and its own Part 3 section, plus a ⚠️ Correction note on the stale 4604.0s Test-2 ground-truth figure (Part 2) |

---

## Key files touched — 2026-09-04 follow-up (item 5)

| Path | What changed |
|---|---|
| `backend/scripts/evaluate_driver_features.py` | New file — offline evaluation harness (no S3/DB writes): candidate skill features (prior-sessions-only expanding mean), the encoding-mismatch cost measurement, tire_deg/pit_predictor holdout comparisons across feature-set variants. Kept as a reusable dev tool (item 5) |
| `backend/services/ml/tire_deg_model.py` | New `CategoricalEncodingMaps`, `build_categorical_encoding_maps`, `encoding_maps_from_metrics`, `resolve_driver_code`/`resolve_circuit_code`, `_crc32_fallback_code`; `apply_incompatible_model_fallbacks` generalized to alias parallel caches (item 5) |
| `backend/scripts/train_models.py` | `train_all()` computes and embeds `driver_id_to_code`/`circuit_name_to_code` into each tire_deg model's own sidecar metrics (item 5) |
| `backend/scripts/retrain_incremental.py` | Same wiring in `retrain()`; flagged (not fixed) a separate pre-existing bug this surfaces — current-season rows key `driver_id` by FastF1 3-letter code, not DB UUID, so a driver active in both halves gets two unrelated codes (item 5) |
| `backend/services/strategy_service.py` | New `_local_metrics_path`/`_download_metrics_from_s3`/`_encoding_maps_for_compound`/`_load_encoding_maps`; `_load_models()` populates the new cache; `_current_state` widened to resolve `circuit_name`; all 10 `_stable_code` call sites (`get_optimal_pit_window`, `get_pit_window_with_explanation`, `_undercut_overcut_probability`, `get_competitor_predicted_strategy`/`_first_pit_laps_over_threshold_batch`) switched to the resolvers, each matched to its own pipeline call; dead `_stable_code` + `zlib` import removed (item 5) |
| `backend/workers/prediction_worker.py` | Same sidecar-loading wiring; `_run_inference`/`_build_race_state`/`_run_simulation` all gained a `maps_cache` parameter; all 6 `_stable_code` call sites switched — `_build_race_state`'s per-driver `driver_id_encoded` resolved per that driver's own current compound, `RaceSimulationInput.circuit_id_encoded` resolved against the requesting driver's compound (documented limitation — see item 5's own section); dead `_stable_code` + `zlib` import removed (item 5) |
| `backend/tests/unit/test_tire_deg_model.py` | 10 new tests — parallel-cache aliasing (2), build/parse/resolve encoding maps (8) (item 5) |
| `backend/tests/unit/test_strategy_service.py` | 2 new wiring tests, existing WET-alias test + `_current_state_side_effects` helper updated for the widened circuit query (item 5) |
| `backend/tests/unit/test_prediction_worker.py` | 2 new wiring tests, existing WET-alias test updated; 6 `_build_race_state(...)` call sites updated for the new `maps_cache` parameter (item 5) |
| `backend/tests/integration/test_resilience.py` | Updated direct `_run_inference(...)` call for the new `maps_cache` parameter (item 5) |
| `CLAUDE.md` | New ✅ Notes entry ("Driver/circuit encoding persisted"); Deferred Wiring entry for `driver_id_encoded` corrected with the offline-evaluation evidence (mixed/negative result, not a clean win — superseded the old "moderate effort, real accuracy win" framing); Phase Tracker updated |
| `docs/day-deferred-fixes-session2-handoff.md` | This file — item 5 → ⚠️ investigated/corrected in the TL;DR table and its own Part 3 section |

---

# ANCHOR PROMPT — paste into a new session

```
Read CLAUDE.md and docs/day-deferred-fixes-session2-handoff.md in full before doing
anything else — it documents a completed fix session (WET tyre model alias,
current_lap validation, a connection-dispose bug fix), a set of distinct
deferred items found while validating those fixes against real Belgian GP
2026 R10 data, and six of those items (4, 5, 7, 9, 11, 12) already closed
or resolved in follow-up sessions since — see the paragraph below for full
current status.

Items 11 (telemetry_worker dispose-on-exception bug, both _persist_lap and
_persist_tire_stint) and 12 (frontend never surfaced validate_current_lap's
rejection or a task FAILURE's reason, all three clients) are ✅ done as of a
2026-09-01 follow-up session; item 9 (promotion guard feature-schema check)
and item 7 (NULL-lap cumulative-sum bug, CLAUDE.md's own Deferred Wiring
item A) are both ✅ done as of 2026-09-02 follow-up sessions; item 4
(predicted_finish_time made a real elapsed time) is ✅ done as of a
2026-09-03 follow-up session; item 5 (driver_id_encoded skill signal) was
investigated as of a 2026-09-04 follow-up session — the skill-feature ask
itself was evaluated offline and found NOT a clean win (deliberately not
added), but the investigation surfaced and fixed a much bigger separate bug
(driver/circuit encoding never matched real training-time codes, inflating
tire_deg holdout MAE 50-265%) — see this file's own item 4/5/7/9/11/12
sections and CLAUDE.md's Notes entries for what landed on each. Item 7's
fix also surfaced a new, distinct, pre-existing CLAUDE.md Deferred Wiring
entry ("No F1 penalty/post-race-classification data is ingested anywhere")
— read that if working anywhere near `_compute_session_gaps`/
`lap_data.position`. Item 4's fix also caught a stale ground-truth figure
in this doc's own Part 2 Test 2 (the old "4604.0s" was itself computed via
the pre-item-7 SUM(lap_time_seconds) bug) — see the ⚠️ Correction note
there before citing that section's numbers. Item 5's own section has the
corrected Test 2 numbers from the real post-fix retrain.

3 items remain in the file's Part 3, independent of each other — pick ONE
to work on this session, don't try to fix several at once:

  6.  No strategic/reactive adaptation between drivers in the Monte Carlo
      (long-term, research-first — don't start here unless that's explicitly
      what's wanted)
  8.  tire_deg_wet.pkl needs a real 6-feature retrain (item 9's guard is now
      confirmed working against a real run, per item 5's follow-up — the old
      manual-sidecar-deletion workaround is obsolete, but the retrain itself
      still hasn't happened; train-models.yml (CI) currently fetches zero
      2026 laps — see CLAUDE.md's escalated GitHub-Actions/FastF1 item —
      resolve or consciously accept the base-corpus-only outcome before
      triggering a real CI run; a local run, as item 5's follow-up did, is
      unaffected by that CI-specific issue)
  10. Tyre models have no track-condition input (dry vs wet)

Item 7's Supabase (production) backfill is also done (2026-09-02, after
this branch merged) — 3,196 rows across the 3 curated sessions, verified
against both the local backfill and FastF1's own official classification.
See `docs/runbook.md`'s "One-time: backfill session_elapsed_seconds on
Supabase" section (now marked done, kept as the procedure for a future
round). Nothing outstanding on item 7. Item 5's model-registry fix has no
separate "production" copy to backfill — `aws_bucket_name` defaults to the
single `f1-strategy-models` S3 bucket everywhere (local, CI, and any future
Fly.io deploy; confirmed via `backend/core/config.py` — no per-environment
bucket scoping exists in this codebase, unlike Supabase/Upstash), so this
session's `make train` run already updated the one real production model
registry directly — MEDIUM/HARD/INTER/WET's fix is live wherever anything
reads `production/*.pkl` from S3, right now, not something that still
needs propagating anywhere. SOFT's incumbent still lacks a real map (its
candidate didn't beat it this run) — nothing to do about that beyond
letting a future retrain naturally promote it.

If the user hasn't already told you which item to pick, ask before starting
— several of these (6 especially) are moderate-to-large scope changes that
deserve a plan-first checkpoint discussion, same convention as the session
that produced this handoff doc (propose a checkpoint plan, wait for
approval, implement checkpoint by checkpoint, report + wait between each).

Do NOT run git commands unless explicitly asked.
```
