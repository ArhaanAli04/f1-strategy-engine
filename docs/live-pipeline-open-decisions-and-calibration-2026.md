# Live Pipeline — Open Decisions, Calibration and Next-Race Verification (follow-on to the Monza doc)

> **Status (2026-09-23, end of the V2 re-run / item-14 session).** This document picks up
> where `docs/live-race-ingestion-and-strategy-gaps-monza-2026.md` ends. That document
> holds the five Monza issues (A-E, all fixed), the verification tooling (V1, V3, V4, V5)
> and the full evidence. This one holds **only what is still open**, the proof already
> in hand for each item, and how to close it.
>
> **What happened in the 2026-09-23 session, in one paragraph.** S3 `production/` was
> confirmed still byte-identical to the restored 2026-09-11 model set (section 0a, all 14
> objects). V2 and Option 1 were then re-run on those models, and **the undercut score came
> out worse than it had on the reverted ones** — Brier 0.289 → 0.332, saturated scores 22 of
> 104 → 47 of 104, and the one slice that used to defend the score (where T really does pit
> the next lap) inverted from beating the gap to losing badly to it (section 3f). CP1 parts
> C-E were redone and tell the complementary story: **the parts now carry real information**
> (the tyre model's event-level gain correlation went 0.05 → 0.33, `pit_predictor` rolled
> forward stopped flatlining), but every part's contribution beyond the gap is at most
> 0.0024 Brier — **too small to demonstrate on 104 held-out events, so Option B cannot pass
> its own CP2 gate** (section 3g). Recommendation on the table: ship a calibrated gap-based
> probability instead (section 3h). Separately, **item 14 was measured and fixed** (section
> 6b): it moves 27.7% of competitor pit laps but with **no measurable accuracy effect**, so it
> shipped as a consistency fix only — and measuring it surfaced a bigger finding, three of
> `pit_predictor`'s eight inputs being pinned to constants (new item 19). Unit suite 652.
>
> **What happened in the 2026-09-21/22 session, in one paragraph.** Item 3b was decided
> (keep the bounded retry, section 2). V2 was run end to end, with V6 folded in: the undercut
> score came out badly calibrated and *worse than the gap alone* on held-out 2025 (section 3),
> and a gap-based recalibration (Option 1) worked only because of the gap. The owner chose
> Option B (rebuild the simulation, section 3d). Its first checkpoint (CP1) then uncovered
> the most important finding of the session. **On 2026-09-14 the weekly `train-models.yml`
> cron, running `main`'s pre-fix code, silently replaced four production models with their
> pre-2026-09-11 versions** (section 0a). This means the V2 score numbers below were
> computed on the broken models and **must be re-run**. The four models were restored from S3
> (verified byte for byte, worker and backend restarted), and the owner merged `develop`
> into `main` (PR #126) so the next cron runs the fixed code. **No repository code changed in
> this session**; the analysis scripts live in the gitignored `recordings/v2_analysis/`
> (section 7).

---

## 0. Where things stand

| Item | What it is | State | Needs |
|---|---|---|---|
| **Model rollback** | Weekly retrain on `main` reverted 4 production models on 2026-09-14 | **✅ restored 2026-09-21; re-verified byte-identical 2026-09-23** (section 0a) | confirm the 2026-09-28 cron does not regress it |
| **3a** | `alert_worker._dispatch` skipped `dispose()` when the query raised | **✅ fixed and committed** (`e5f4868`, PR #124) | — |
| **3b** | Keep the bounded retry, or chain `process_lap` into the prediction | **✅ decided 2026-09-21: keep the retry (Option A)** (section 2) | check at Azerbaijan for any exhausted retry |
| **V2** | Does the undercut score mean what it says? (V6 folded in) | **✅ answered 2026-09-23 on the restored models: no. Worse than it looked, and worse than the gap alone** (section 3f) | nothing further; the answer feeds the decision below |
| **V2 Option 1** | Recalibrate the score with the gap (logistic) | **✅ re-run 2026-09-23; beats the raw score by −0.138, still does not beat gap alone (+0.001)** (section 3f) | — |
| **V2 Option B** | Rebuild the undercut simulation (rival pit timing, pit-loss spread, realistic noise) | **⛔ CP1 complete 2026-09-23; recommended NOT to proceed to CP2 — its gate is unreachable at this sample size** (section 3g) | **owner decision** (section 3h) |
| **Undercut redesign** | Ship a calibrated gap-based probability instead | **recommended, not started** (section 3h) | owner decision; do it AFTER Azerbaijan (live path, needs a shadow-race re-run) |
| **V5** | Which of F1's fields stream on the live socket; is the recorder sound | **built, awaiting a real race** (section 4) | Azerbaijan R15 (2026-09-26 in the local `races` table) with the stack up |
| **Lead-lap fallback** | Improve gap-only ranking from 92.2% | **conditional on V5** (section 5) | V5's answer |
| **Item 14** | Zero `fuel_load_penalty` in `_first_pit_laps_over_threshold_batch` | **✅ measured and fixed 2026-09-23; no accuracy effect, consistency only** (section 6b) | owner commit; CLAUDE.md Phase block |
| **Item 19 (new)** | Three of `pit_predictor`'s eight inputs pinned to constants behind `/overview` | **found 2026-09-23, measured baseline in hand** (section 6c) | measure with the harness first, then scope; after Azerbaijan |
| Others | Small leftovers and known limits | **listed** (section 6) | triage |

Order for the next session: (1) **Azerbaijan R15 (2026-09-26) — V5's checklist and commands
plus the 3b retry check** (section 4); this is the only calendar-bound item and everything
else can wait behind it; (2) after Monday 2026-09-28 02:00 UTC, confirm the cron did not
regress `production/` (section 0a); (3) the undercut decision — gap-based probability or
Option B under a changed criterion (section 3h); (4) item 19's read-only measurement
(section 6c); (5) lead-lap work only if V5 says `Position` does not stream.

---

## 0a. Production model rollback by the weekly retrain (found and fixed 2026-09-21)

### What happened
- `train-models.yml` runs every Monday at 02:00 UTC (`cron: "0 2 * * 1"`) and executes
  `backend/scripts/retrain_incremental.py` from the **default branch, `main`**.
- The 2026-09-04 pit-label fix (`60baf93`) and the 2026-09-11 tire-deg fixes (`2368de8`: fuel
  sign and leakage, `training_schema_version` promotion guard, safety-car training input) were
  on `develop` only. `origin/main` stopped at 2026-09-02 (`98d8aa7`).
- **On Monday 2026-09-14** (S3 folder `20260914-074521`) the old code retrained. Its models
  have artificially good error scores, because of the leaky `fuel_adjusted_time` feature, the
  same-lap pit label and the always-zero safety-car target. `main`'s guard only compared
  holdout MAE plus the feature count, so four of the old models replaced the fixed ones in
  S3 `production/`. The 2026-09-21 run (`20260921-074846`) trained the same old models again and
  promoted nothing new.

### What production held from 2026-09-14 to 2026-09-21

| Model | Production (before restore) | What was wrong |
|---|---|---|
| `tire_deg_medium`, `tire_deg_hard` | 2026-09-14 | Leaky `fuel_adjusted_time` feature. Inference feeds `fuel_load_penalty` into that slot, so the model gets an off-distribution input (the exact CP1 bug of the tire-deg doc). |
| `pit_predictor` | 2026-09-14 | Old same-lap label (`positive_rate` 0.0276). It gives no advance warning: about 0 until the lap itself. |
| `safety_car_model` | 2026-09-14 | Always-zero model again (`holdout_mae` 0.0). |
| `tire_deg_soft`, `_inter`, `_wet` | 2026-09-11 | Correct; not replaced. |

### Impact on evidence gathered in that window
- **The live stack:** every worker/backend start from 2026-09-14 to 2026-09-21 loaded the reverted models.
- **V2 and Option 1 (section 3):** their score numbers used the reverted MEDIUM/HARD tyre
  models. The undercut score only uses the tyre models, not `pit_predictor` or the safety-car
  model, and most events are on MEDIUM or HARD, so **the score-side results must be re-run**.
  Step 0's counts, the labels and the gap-only baseline do not depend on the models and stand.
- **CP1 parts C-E (section 3d):** invalid (reverted tyre models and the same-lap `pit_predictor`).
  Parts A and B do not use models and stand.
- **The shadow race of 2026-09-20 (Monza doc section 7d):** ran on the reverted models. Its 14
  checks are about wiring, persistence, positions, counters and gates, so they stand; its
  undercut score values do not.
- **The Monza doc's CP4/CP5 offline re-scores (2026-09-19)** also ran after 2026-09-14. The gap
  and target corrections they measured stand. The absolute score distributions (e.g. "scores
  ≥ 0.999: 185 → 74") mix in the reverted MEDIUM/HARD models and should not be quoted as final.
- The Monza race itself (predictions stored on race day) predates 2026-09-11 and is unaffected
  by this window.

### The restore (2026-09-21), all verified
- **Backup:** the eight affected `production/` objects (4 models + 4 sidecars) were
  downloaded first. They are now in `recordings/v2_analysis/production_backup_20260914/`
  (gitignored). The IAM user lacks `s3:ListBucketVersions`, so this local copy is the backup.
- **Copy:** server-side copy `20260911-065536/<obj>` → `production/<obj>` for
  `tire_deg_medium`, `tire_deg_hard`, `pit_predictor`, `safety_car_model` and their
  `.metrics.json`. Promotion writes the same file to both places (`train_models.py`'s
  `upload_model`, line ~602), so this restores production exactly as the 2026-09-11 run left
  it. **All 8 objects byte-identical to the source (sha256).**
- **Restored sidecars:** all `training_schema_version = 2`:
  - `tire_deg_medium`: `holdout_mae` 0.561, `fuel_load_penalty` feature.
  - `tire_deg_hard`: `holdout_mae` 0.585, `fuel_load_penalty` feature.
  - `pit_predictor`: `holdout_mae` 0.325, `positive_rate` 0.087.
  - `safety_car_model`: `holdout_mae` 0.00365.
- **Restart:** `docker restart docker-worker-1 docker-backend-1`. The worker logged `ready`;
  the backend is healthy and `/health` returns 200.
- **Probe inside the worker:**
  - All five tyre models have 6 features, and WET is no longer aliased to INTER.
  - The safety-car model is real again (default rate 0.00188, 24 circuits).
  - `pit_predictor` gives real probabilities again (0.15-0.37 on a probe car, against ≈0.00 before).
  - Script: `recordings/v2_analysis/probe_models.py`. A copy sits in the worker at
    `/tmp/probe_models.py`; it could not be deleted as the container user and is harmless.
- **Recurrence closed by the owner:** `develop` was merged into `main` on 2026-09-22 (PR #126,
  merge commit `10bbbfc`). Checked on `origin/main`:
  - `PIT_LABEL_HORIZON_LAPS = 3` and `TRAINING_SCHEMA_VERSION = 2` are present.
  - `fuel_adjusted_time` appears only in comments; `FEATURE_COLUMNS` uses `fuel_load_penalty`.
  - `develop` shows "1 behind main": that is only the merge commit, and the file trees are
    identical. The owner may fast-forward `develop`.

### Re-verified 2026-09-23 (read-only), before the V2 re-run
Everything below is measured, not assumed — the whole point of the re-run depended on it.
- **`probe_models.py` inside the worker** (container started minutes earlier, so it had
  loaded `production/` fresh): all five tyre models report 6 features, WET is **not** aliased
  to INTER, the safety-car model is real (`default_rate` 0.00188, 24 circuits), and
  `pit_predictor` gives real probabilities on a MEDIUM probe (**0.371 / 0.158 / 0.145** at
  ages 14/20/26), matching the 0.15-0.37 range recorded on 2026-09-21 rather than the ≈0.00
  of the same-lap detector.
- **All 7 sidecars carry `training_schema_version = 2`**, and no sidecar mentions
  `fuel_adjusted_time` anywhere: `tire_deg_soft` 0.6356, `_medium` **0.5609**, `_hard`
  **0.5849**, `_inter` 1.7695, `_wet` 3.9233 (all six-feature, `fuel_load_penalty` present);
  `pit_predictor` `holdout_mae` **0.3250**, `positive_rate` **0.0874**, `cv_auc` 0.7730;
  `safety_car_model` **0.0036531**.
- **Nothing has written to `production/` since the restore.** Timestamps form exactly two
  clusters: `2026-09-21T18:45:16-17Z` (the four restored models + sidecars) and
  `2026-09-11T06:55-56Z` (SOFT/INTER/WET). In particular **the 2026-09-21 cron run
  (`20260921-074846`) promoted nothing** — no `production/` object bears that timestamp,
  confirming section 0a's note that it retrained the same old models.
- **All 14 objects are byte-identical to `20260911-065536/`** by ETag comparison (models and
  sidecars), independently re-confirming the restore's sha256 check and ruling out a partial
  overwrite.

### Still to check
- **After Monday 2026-09-28 02:00 UTC,** check S3 `production/*.metrics.json`. Every sidecar
  should still carry `training_schema_version = 2`, and any model promoted that day must have
  been trained by the fixed code. Read-only check: the sidecar loop in
  `recordings/v2_analysis/` (see section 7). Not yet due as of 2026-09-23.
- **A second, previously-unnoticed consequence of the 2026-09-11 retrain**, found 2026-09-23
  while investigating item 14: `get_competitor_predicted_strategy` pins
  `safety_car_probability` to `0.0`. That was *exactly equivalent* to calling the model while
  `safety_car_model.pkl` was the always-zero artifact, and silently became wrong the moment
  CP3 promoted a real one. See item 19 (section 6c) — worth remembering that fixing a model
  can turn a harmless hardcoded constant into a defect elsewhere.
- **Worth considering (not started):** a guard so a scheduled retrain cannot promote from a
  branch that lacks the current schema versions. For example, the workflow could refuse to
  run when `main` is behind `develop`, or the sidecar could record the git SHA of the code
  that trained it.

---

## 1. Item 3a — `alert_worker._dispatch` dispose flaw — ✅ FIXED 2026-09-20

### What was wrong
`_dispatch` (`backend/workers/alert_worker.py`) opens a DB session to read the
subscriptions and then called `await get_engine().dispose()` **after** the `async with`
block. If the query raised, `dispose()` never ran. The task runs under `asyncio.run`, which
closes its event loop on the way out, so the pooled asyncpg connection was left bound to a
closed loop and the **next** task that reused the pool failed with `RuntimeError: Event
loop is closed`. This is the same shape as the bugs already fixed in
`prediction_worker._run_simulation`, `telemetry_worker._persist_lap` /
`_persist_tire_stint`, and `prediction_worker._persist_and_publish` (the last found by the V3
shadow race).

### The fix
The session block now sits in a `try` and `dispose()` is in the `finally`, with a comment
saying why. The FCM loop stays after it (no DB is needed there). The early return for a
below-threshold prediction is unchanged: no session is opened, so nothing needs disposing.
Two files changed: `backend/workers/alert_worker.py` and the new
`backend/tests/unit/test_alert_worker.py` (there was no test file for this module before).

### Evidence, all measured
- **3 new unit tests:** dispose runs and no push is sent when the query raises; dispose runs
  and one push is sent for a matching subscriber; a below-threshold prediction never opens
  a session or touches the engine.
- **Mutation check:** with the old shape put back temporarily, the "query raises" test
  **fails** (`dispose` was never awaited); with the fix it passes; the file was restored
  and compared byte for byte with a backup.
- `ruff check .` clean; `ruff format --check .` clean (159 files); `mypy backend/ --strict`
  clean (159 files); **unit suite 651 passed** (648 before, plus these 3); integration
  `test_live_prediction_pipeline`, `test_race_simulation_serialization`,
  `test_strategy_endpoint` **14 passed**; `test_alerts.py` (the only integration file that
  references the alert worker) **3 passed**.
- The worker container was restarted (`docker restart docker-worker-1`; `backend/` is
  bind-mounted) and the `finally` is present in the running file.

### What this does NOT show
The alert worker's real job is sending FCM pushes, and Firebase is **not configured** in
this environment (`_send_fcm` logs "Skipping FCM push" and returns). The fix is proven by unit
tests on the engine handling only; no real push was sent and none can be from here. The
shadow race does not exercise this worker either (it publishes predictions, but
`alert_worker` is a separate process that has to be started on its own).

---

## 2. Item 3b — chain `process_lap` into the prediction, or keep the retry — ✅ decided 2026-09-21: keep the retry

### The situation
The live ingestor dispatches two Celery tasks back to back for every lap completion
(`backend/scripts/ingest_live_session.py`, lines ~830-831):

```
process_lap.delay(raw_lap)               # telemetry_queue: writes the lap to lap_data
run_strategy_prediction.delay(raw_lap)   # prediction_queue: reads that lap, predicts
```

One `--pool=solo` worker consumes all queues and **nothing orders the two tasks**. At a lap
boundary every driver completes together (queue peaks of 21-23 tasks), so some predictions
start before their own lap exists and raise `NotFoundError: No lap data for driver ...`.

### The proof it is real, and that the current fix works
| | Smoke run 1 (no retry) | Smoke run 2 (retry) | Full race (retry) |
|---|---|---|---|
| Scope | start to lap 12 | start to lap 12 | all 53 laps |
| Predictions per dispatched lap | 218 of 233 (93.6%) | 233 of 233 | **1052 of 1052** |
| `NotFoundError` lines / retries | 15 failures | 0 | **17 retries, none exhausted** |
| Prediction lag median / p95 / max | 3.7 / 13.7 / 18.7 s | 4.0 / 30.9 / 37.5 s | 5.1 / 25.9 / 70.4 s |
| Peak queue depth | 21 | 21 | 23 |

Sources: `recordings/shadow/smoke_run1.{json,log}` (local, gitignored) and
`recordings/shadow/full{.json,.log,_verify.log}`; the 17 retries are the
`run_strategy_prediction ... retry: Retry in 3s: NotFoundError(...)` lines in the worker
log of the full run. Before the retry, 6.4% of laps had **no prediction and no alert**.

### The current fix (Option A)
`run_strategy_prediction` is a bound task (`backend/workers/prediction_worker.py`, ~line 1180)
that retries on `NotFoundError` only, `_LAP_NOT_YET_PERSISTED_RETRIES = 4` times,
`_LAP_NOT_YET_PERSISTED_DELAY_SECONDS = 3` seconds apart. When the retries are spent the
error propagates and the task fails, so a lap that never arrives still shows up as a failure.

### The two options

**Option A — keep the retry (current).**
- Already built, tested (3 unit tests: retries on `NotFoundError`, not on other errors, not
  on success) and shown across a full race.
- It is a mitigation, not a guarantee: it assumes a lap is written within about 12 s
  (4 x 3 s). A slower lap write, for example a backlog during a red-flag restart when
  every car crosses the line at once, would exhaust it, and that prediction would fail.
- It costs a few seconds of lag for the retried laps (the p95 rose 13.7 s -> 25.9-30.9 s).

**Option B — chain the tasks.**
- `process_lap` triggers the prediction itself once the lap is saved (for example
  `process_lap(...).apply_async(link=...)` or a call at the end of its own success path),
  so the prediction can never run before its lap. The race disappears by design and the
  retry becomes unnecessary.
- **Files it touches:** `ingest_live_session.py` (stop dispatching the prediction
  separately), `workers/telemetry_worker.py` (dispatch after commit), and every tool that
  dispatches the pair itself: `replay_pipeline.py`, `verify_live_feed_parity.py`, and
  `shadow_race.py`'s path through the ingestor. It also moves the prediction's queue
  behaviour: if it hangs off the telemetry task, a slow prediction could no longer be
  scaled or prioritised independently on `prediction_queue`.
- **Risks:** a bigger change to a pipeline that just passed a full race; a failure in
  `process_lap` would now also silently drop the prediction unless handled; the "two
  queues, independent scaling" reasoning recorded in CLAUDE.md (Celery + Redis for
  predictions) would need revisiting.
- **Effort:** medium. Verify by re-running the full shadow race and requiring 14 of 14 with
  zero retries in the worker log.

### Decision (2026-09-21): Option A kept
The owner chose to keep the bounded retry. No code change was needed; it was already in
place. Verified that day:
- `run_strategy_prediction` (`prediction_worker.py:1177-1204`) and the running worker hold
  the same retry constants.
- The 3 retry tests pass.
- `ruff check`/`ruff format --check`/`mypy backend/ --strict` are clean (159 files).
- Unit suite: 651 passed.
- Integration tests `test_live_prediction_pipeline`, `test_race_simulation_serialization`,
  `test_strategy_endpoint` and `test_alerts`: 17 passed.

**Remaining 3b check:** at Azerbaijan, look for any `NotFoundError` retry not followed by a
success (section 4). If one appears, revisit Option B.

### Recommendation and how to decide (as written before the decision)
Keep Option A. The measured outcome is 1052 of 1052 with no exhausted retries, and B is a
structural change to something working. Move to B if the **real** Azerbaijan race shows any
`run_strategy_prediction` task failing after its retries (check the worker log for
`NotFoundError` that is not followed by a success), or if the p95 lag matters to a user-facing
feature. Either way, re-run the shadow race after touching this path.

---

## 3. V2 — historical calibration of the undercut score (V6 folded in) — ✅ ANSWERED 2026-09-23 (sections 3a-3h)

> **The answer, up front.** The undercut score is not a calibrated probability, and a single
> number — the raw gap in seconds — predicts the outcome better than the whole simulation.
> Restoring the correct models made the score *worse*, not better, which is the strongest
> evidence yet that the fault is the simulation's **structure**, not its inputs. CP1 shows the
> individual parts now carry real signal but contribute too little to be demonstrable at this
> sample size, so Option B cannot pass its own gate. Read 3f (the re-run), 3g (CP1) and 3h
> (the decision); 3a-3e are the method and the superseded first pass, kept for the record.

### What the question is
`strategy_service.get_undercut_score` returns `probability_pit_now_gains_position`
(`schemas/strategy_schema.py`): the chance that pitting now puts the driver ahead of the car
in front. **Nothing has ever compared that number with what happened.** Every fix so far
(B, C, the live-standings work) corrected the score's *inputs* (starting gap, rival, ranking).
Whether the output is a calibrated probability is untested, so a "0.8" may not mean 80%.

### The constants that were chosen without outcome data
| Constant | Value | Where |
|---|---|---|
| `UNDERCUT_ALERT_THRESHOLD` | 0.5 | `services/alert_service.py:72` |
| `UNDERCUT_ALERT_MIN_TYRE_AGE_LAPS` | 4 | `alert_service.py:85` (from measured Monza buckets) |
| `UNDERCUT_ALERT_MIN_LAPS_REMAINING` | 15 | `alert_service.py:86` (from measured Monza buckets) |
| `UNDERCUT_ALERT_DEDUP_TTL_SECONDS` | 60 | `alert_service.py:94` |
| `UNDERCUT_MONTE_CARLO_SIMS` | 200 | `services/strategy_service.py:159` |
| `LAP_TIME_NOISE_STD_SECONDS` | 0.35 | `services/ml/race_simulator.py:87` |

Only the gates were checked against data, and that data was one race (Monza): the 29-alert
count is stable across neighbouring gate values (26-35), so they are not on a knife edge,
but that shows stability, not correctness.

### Method (unchanged from the Monza doc, made concrete)
1. **Step 0, count first.** From the 2018-2025 laps (163,623 rows; historical gaps are right
   through `session_elapsed_seconds`), find situations where driver D is directly behind T
   within a few seconds, D pits on lap L while T stays out and pits later. Report how many
   exist in the held-out season (2025). If too few for meaningful confidence intervals, widen
   the window and say so before going further.
2. **Label** each event: is D ahead of T after both have stopped?
3. **Score** each event as of lap L with the promoted models, using the same code path the
   live pipeline uses (the read-only `evaluate_undercut_live_gaps.py` shows how to call it
   offline; it needs the DB and S3).
4. **Compare:** reliability curve, Brier score and AUC, against a **gap-only baseline** (a
   plain "is the gap smaller than the pit loss" rule). Evaluate on 2025, which the models did
   not train on (2018-2024 train, 2025 holdout).
5. Report outcome rates for the alert gate conditions (tyre age, laps remaining) too.
6. **V6 inside it:** review the Monza alerts that survive the gates (`VER on RUS` x12,
   `VER on ANT` x7) against the race's real pit stops in `tire_stints`.

### Caveats to state up front
- **Selection bias:** teams pit when they expect it to work, so only attempted undercuts are
  observed; the score is being tested on a self-selected sample.
- The score assumes a "pit now vs next lap" framing that real stops only approximate.
- The models were retrained on 2026-09-11 (after Monza), so Monza-based checks mix a model
  change with any other change.

### Decision rule
If calibration is poor, or no better than the gap-only baseline, then the score and the 0.5
alert threshold need redesign. That is a finding about the score, not a failure of the input
fixes. Effort: medium-high. Read-only analysis; no production code changes unless the result
calls for them. **Needs owner approval before starting.** (Approved and run 2026-09-21; results below.)

> **Read this first.** Sections 3b-3d below were produced between 2026-09-21 and 2026-09-22
> while S3 `production/` held the **reverted** MEDIUM/HARD tyre models and the old
> `pit_predictor` (section 0a). They are **superseded** by the 2026-09-23 re-run in sections
> 3f-3g and are kept only to show what changed. The following do not depend on models and
> were reproduced exactly by the re-run, so they stand: the event definition and counts (3a),
> the labels, the gap-only baseline, and CP1 parts A-B.

### 3a. Step 0: eligible events (model-independent, stands)
**Definition (agreed with the owner):**
- At the end of lap L−1, driver D is directly behind T (position T+1, same lap), and the gap
  from `session_elapsed_seconds` is below a threshold.
- D pits on lap L. The in-lap is the lap before a later stint's `start_lap`, the same rule
  as `pit_predictor.label_pit_laps`.
- T's first stop at or after L falls on L+1..L+8.
- The lap-1 order is excluded.
- "Clean" means: both cars on the lead lap, no SC/VSC/red flag (`track_status` containing
  4/5/6/7) on D's in-lap, neither car on INTERMEDIATE/WET, and both cars have a lap after
  T's out-lap (so the outcome can be labelled).
- The **main set is gap < 5 s** (owner's choice), with gap < 3 s as a subset. **2026 is
  excluded** (owner's choice; whether the 2026-09-11 retrain saw 2026 laps was not checked).

Data: 2018-2025 race sessions, 166,453 laps, 5,251 pit stops.

| Gap under | 2018-2024 all / clean | 2025 (held out) all / clean | 2025 races |
|---|---|---|---|
| 2 s | 360 / 301 | 67 / 52 | 18 |
| 3 s | 548 / 463 | 94 / 75 | 22 |
| 4 s | 693 / 590 | 111 / 92 | 23 |
| **5 s** | **818 / 696** | **125 / 104** | 23 |

- **T's delay after D (clean, gap < 3 s):** +1: 187, +2: 84, +3: 60, +4: 52, +5: 44, +6: 41,
  +7: 32, +8: 38. So 40% are "rival covers on the next lap".
- **Spot check:** three random 2025 events against the raw laps were all correct.
- **Caveats:** a red-flag tyre change appears as a new stint (mostly removed by the in-lap
  filter); about 100 held-out events give roughly ±0.1 on AUC.

### 3b. Calibration of the current score (2026-09-21; ⚠ computed on the reverted models — SUPERSEDED by 3f)
**Method:**
- **Score:** the real `strategy_service._undercut_overcut_probability`, run as of lap L−1
  with `_current_state`/`_cumulative_race_time` swapped for as-of-lap values, no live payload
  (the historical path), and `_bump_pipeline_stat` stubbed. This is the same pattern as
  `evaluate_undercut_live_gaps.py`.
- **Label:** D is ahead of T at the end of T's out-lap (by `session_elapsed_seconds`). It
  agrees with "two laps later" 94.6% of the time.
- **Gap-only baseline:** a logistic on the gap, fitted on 2018-2024. The doc's "gap vs pit
  loss" rule was dropped because both cars pay the pit loss, so it cancels.

**Results on 2025 held out, gap < 5 s (104 events, 38.5% worked):**

| | Brier | AUC |
|---|---|---|
| Current score | 0.289 (CI 0.22-0.36) | 0.64 (CI 0.53-0.74) |
| Gap only (logistic) | **0.193** (CI 0.16-0.23) | **0.77** (CI 0.67-0.85) |
| Always the 39% base rate | 0.237 | — |

**Reliability:**

| Score bucket | Events | Mean score | Actually worked |
|---|---|---|---|
| 0.0-0.2 | 61 | 0.05 | **31%** |
| 0.2-0.4 | 16 | 0.28 | 44% |
| 0.4-0.6 | 12 | 0.48 | 50% |
| 0.6-0.8 | 5 | 0.65 | 60% |
| 0.8-1.0 | 10 | 0.94 | **50%** |

**Further findings:**
- 22 of 104 scores were saturated (≤0.001 or ≥0.999).
- With the gap under 1 s, undercuts worked 75% of the time, but the score averaged about 40%.
- At the 0.5 threshold: events scored ≥0.5 worked 50% of the time, against 36% below it.
- 2018-2024 (in-sample for the tyre models, 696 events) showed the same pattern: Brier 0.278
  against 0.196 for gap only.
- **Where T really pits the next lap** (the score's own framing, 42 events), the score did
  better: Brier 0.153 against 0.186 for gap only, with wide CIs.
- **Success rises with T's delay:** +1: 24%, +2: 32%, +3: 45%, +4: 54%, +5: 58%, +6: 62%,
  +7: 59%, +8: 52%.
- **The alert gates cannot be judged from history:**
  - Only 2 of 800 attempts were made on tyres 3 laps old or less (teams do not try it).
  - Attempts with fewer than 15 laps left worked 42% of the time (24 events), against 39% otherwise.
  - The gates are harmless but have no evidence behind them.
- **Hypothesis (untested):** the simulation's uncertainty is too narrow. It has only
  0.35 s/lap of noise, no pit-time spread and no out-lap cost, and it always assumes T pits
  exactly one lap later.

**V6 (Monza), on the post-fix offline alert replay (29 gated alerts):**
- **VER on RUS ×12 (laps 7-21):** RUS never stopped again after lap 3, so the premise "T pits
  next lap" was false throughout.
- **VER on ANT ×7 (laps 15-22):** ANT pitted on the same lap as VER (lap 28) and stayed ahead.
  VER finished P3, behind both.
- None was a real opportunity. Scores of 0.94-1.00 on 0.3-1.8 s gaps showed the same
  overconfidence.
- (The pair assessment against the real stops stands; the scores need the re-run.)
- **Note:** Monza's `tire_stints` are unreliable (live-ingested; everyone shows a stop on lap 4
  and VER's stint 2 is "UNKNOWN"). Stops were read from `lap_data` compound/tyre-age changes
  instead: VER laps 4 and 29, RUS lap 4 only, ANT laps 4 and 29.

### 3c. Option 1: recalibrate the score with the gap (2026-09-21; ⚠ SUPERSEDED by 3f — but its Option A is now the recommendation, see 3h)
**Method:**
- Model form chosen by 5-fold race-grouped CV on 2018-2024 only: gap only 0.1974, raw score
  only 0.2152, raw score + gap 0.1942 (chosen).
- Fit on 2018-2024, scored once on 2025. The fitted formula was
  `p = sigmoid(1.249 + 0.091·logit(score) − 0.638·gap_s)`. The weight on the raw score is tiny.

**Results on 2025:**
- The corrected value (raw score + gap) scored Brier **0.196**, AUC 0.75, and is calibrated:

  | Predicted | Actually worked |
  |---|---|
  | 0.13 | 11% |
  | 0.30 | 25% |
  | 0.50 | 42% |
  | 0.66 | 63% |

- It clearly beat the current score: paired Brier difference −0.093 (CI −0.149 to −0.038).
- It did **not** beat gap alone: difference +0.004 (CI −0.006 to +0.013).
- **Not shipped**, as agreed: it had to beat both.

**Thresholds on the corrected value (2025):**

| Alert when | Alerts | Worked | Successes caught |
|---|---|---|---|
| ≥ 0.5 | 45 | 60% | 68% |
| ≥ 0.6 | 31 | 65% | 50% |
| ≥ 0.7 | 9 | 89% | 20% |

**Monza through the correction:** VER on RUS 1.00 → 0.72-0.81; VER on ANT 0.65 → 0.60-0.74.
Still alerts, because the correction cannot know RUS would not stop again.

**The owner then chose Option B** (fix the simulation) over "A: ship a gap-based probability now".

### 3d. Option B: rebuild the undercut simulation (plan approved 2026-09-21; CP1 completed 2026-09-23 — parts C-E SUPERSEDED by 3g, and CP2 is NOT recommended, see 3h)
**Goal:** a new undercut probability that beats gap alone on held-out 2025 (paired Brier CI
excluding zero) and is well calibrated. If it cannot, stop and fall back to a gap-based
probability. Everything is measured before any production change.

**What changes in the simulation:**
1. **Rival pit timing.** Roll `pit_predictor` forward lap by lap for T, batched like
   `_first_pit_laps_over_threshold_batch`, to get a probability of stopping on each of the
   next 1-8 laps plus a "no stop soon" share.
2. **Pit-loss mean and spread per circuit,** measured from lap data (`pit_events` is empty).
3. **Realistic noise over the right window.** Compare the cars at the end of T's out-lap, with
   the per-lap noise fitted on 2018-2024.

**Checkpoints:**
- **CP1:** measure the parts (read-only).
- **CP2:** prototype and test with the V2 harness. **Gate: must beat gap alone on 2025.**
- **CP3:** production code. Expected files: a new `backend/services/ml/undercut_model.py`,
  `strategy_service._undercut_overcut_probability`, a parameter-fitting script, unit tests
  and the V4 property tests, and the threshold in `alert_service.py`. Checks plus a shadow-race
  re-run. **Re-confirm the file list with the owner first; do not deploy before Azerbaijan
  unless finished and shadow-tested.**
- **CP4:** docs.

**Side effects to remember:** `get_overcut_score` shares the helper, so overcut changes too.
Rolling `pit_predictor` forward adds about 8 batched calls per prediction (measure it in CP2).
No new dependency.

**CP1 results (2026-09-21):**
- **A. Pit loss** (stands; model-independent): in-lap + out-lap minus 2× the driver's median
  clean lap within ±2-6 laps, over 3,016 green-flag stops (2018-2024).
  - Median **22.7 s** (the code uses a fixed 22 s for both cars, with no spread).
  - Robust sd 3.1 s overall, and a median of **1.8 s within a circuit** (range 1.3-9.3 s).
  - Circuit medians run from 18.6 s (Spa) to 29.7 s (Imola).
- **B. Relative noise between two adjacent, non-pitting cars** (stands; model-independent):
  robust sd of the gap change.

  | Laps | Measured | Current code implies |
  |---|---|---|
  | 1 | 0.42 s | 0.49 s |
  | 3 | 1.00 s | 0.86 s |
  | 5 | 1.53 s | 1.11 s |
  | 8 | 2.20 s | 1.40 s |

  The gap spreads faster than √n noise over several laps, so it contains pace drift.
  Incidents make the plain sd huge, so use the robust figures.
- **C. Fresh-tyre gain while T stays out:** ⚠ INVALID (reverted MEDIUM/HARD models). Measured
  that day:
  - Real gain 1.20 s/lap against a tyre-model prediction of 0.86 s/lap.
  - **Correlation 0.05**: the model's event-level gain carried no information.
  - Re-run before believing it: it is the main candidate explanation for "the score adds
    nothing beyond the gap".
- **D. `pit_predictor` rolled forward for T:** ⚠ INVALID. The production model was the
  same-lap detector, so P(no stop within 8 laps) ≈ 1.00. This is what exposed the rollback.
- **E. Does each part add to gap alone** (CV on 2018-2024): ⚠ INVALID. No part improved on
  gap only (0.1974) that day.

### 3e. What the next session must do before continuing V2 / Option B — ✅ all done 2026-09-23
Steps 1-3 were executed; results in section 0a (re-verified), 3f (V2 + Option 1) and 3g
(CP1 C-E). Step 4's decision is section 3h, awaiting the owner. Step 5's list stays relevant
only if Option B is revived — note its second bullet (the zero `fuel_load_penalty`) is now
fixed at the production call site too, see section 6b.

### 3f. V2 and Option 1 re-run on the restored models (2026-09-23) — the score got WORSE
**The comparison is clean.** Every model-independent control reproduced exactly, and
`v2_calibrate.py` seeds its RNG per event (`crc32` of session+driver+lap), so there is no
Monte Carlo noise in the difference either — **the only variable that changed is the models.**

| Control | 2026-09-21 (reverted) | 2026-09-23 (restored) |
|---|---|---|
| Events scored / train / held-out 2025 | 800 / 696 / 104 | identical |
| 2025 base rate | 38.5% | 38.5% |
| Label agreement (T out-lap vs +2 laps) | 94.6% | 94.6% |
| Gap-only baseline, 2025 Brier / AUC | 0.193 / 0.77 | 0.193 / 0.766 |
| Gap-only CV Brier (2018-2024) | 0.1974 | 0.1974 |
| Gap-only logistic fit | — | coef −0.750, intercept 1.239 |

**Results, 2025 held out, gap < 5 s (n=104, 40 worked, 38.5%):**

| | Reverted | **Restored** | Gap only |
|---|---|---|---|
| Brier | 0.289 | **0.332** (CI 0.254-0.412) | 0.193 (0.162-0.225) |
| AUC | 0.64 | **0.595** (CI 0.484-0.698) | 0.766 (0.665-0.854) |
| Saturated (≤0.001 or ≥0.999) | 22 of 104 | **47 of 104** | — |

Constant base rate scores 0.237, so **the score is worse than predicting 38.5% every time.**

**Reliability (restored), 2025 gap < 5 s:**

| Score bucket | Events | Mean score | Actually worked |
|---|---|---|---|
| 0.0-0.2 | 67 | 0.03 | 33% |
| 0.2-0.4 | 13 | 0.30 | 46% |
| 0.4-0.6 | 5 | 0.54 | 60% |
| 0.6-0.8 | 3 | 0.69 | 100% |
| 0.8-1.0 | 16 | **0.97** | **38%** |

At the 0.5 threshold: ≥0.5 (n=23) worked 52%, <0.5 (n=81) worked 35% — some discrimination
survives, but no more than before.

**The slice that used to defend the score has inverted.** Section 3b's one encouraging result
was that where T really does pit the next lap — the score's own framing — it beat the gap
(Brier 0.153 vs 0.186). On the restored models:

| 2025, T pits the very next lap (n=42, 21.4% worked) | Reverted | **Restored** | Gap only |
|---|---|---|---|
| Brier | 0.153 | **0.310** | 0.186 |
| AUC | — | **0.515** (coin flip) | 0.761 |
| 0.8-1.0 bucket | — | n=6, mean score 0.96, **actual 0.00** | — |

Six events scored ≈0.96 and **every one failed.** At the 0.5 threshold in this slice, ≥0.5
worked 12% against 24% below it — i.e. inverted.

Other slices move the same way: gap < 3 s (n=75) Brier 0.387 / AUC 0.566 against gap-only
0.227 / 0.690, 30 of 75 saturated; T pits 2-8 laps later (n=62) 0.346 / 0.649 against
0.197 / 0.795; in-sample 2018-2024 (n=696) 0.310 / 0.636 against 0.196 / 0.748, with **304 of
696 saturated**.

**Option 1 re-fit puts even less weight on the score.** CV on 2018-2024: gap only 0.1974,
raw score only 0.2284 (was 0.2152), raw score + gap 0.1958 (was 0.1942, so marginally worse
and barely under gap-only). The fit became
`p = sigmoid(1.263 + 0.053·logit(score) − 0.703·gap_s)` — the score's coefficient fell
0.091 → **0.053** while the gap's strengthened −0.638 → **−0.703**. On 2025:
- beats the raw score by **−0.138** Brier (CI −0.202 to −0.073), a wider margin than before
  only because the raw score deteriorated;
- **still does not beat gap alone: +0.001** (CI −0.007 to +0.010), the same straddle as
  section 3c's +0.004. Not shipped, as agreed.
- Calibration of the corrected value is good: predicted 0.13 → 17% actual (n=18), 0.29 → 24%
  (n=33), 0.51 → 38% (n=29), 0.68 → 75% (n=24); range on 2025 is 0.07-0.79.
- Thresholds on the corrected value (2025): ≥0.4 → 53 alerts, 55% worked, 72% of successes
  caught; **≥0.5 → 40 alerts, 62% worked, 62% caught; ≥0.6 → 24 alerts, 75% worked, 45%
  caught;** ≥0.7 → 11 alerts, 82% worked, 22% caught.
- Monza through the correction: VER on RUS lap 10 1.00 → 0.76, lap 20 1.00 → 0.65; VER on ANT
  lap 16 0.65 → 0.73, lap 22 0.65 → 0.58; and a third pair now visible, VER on HAM lap 35
  0.78 → 0.58. The V6 verdict is unchanged — none was a real opportunity.

**Why better models made the output worse (the mechanism, consistent with all of the above).**
The 2026-09-11 fuel-sign fix gave the tyre models physically correct positive degradation
slopes, so `_project_stint_delta`'s deterministic term is now larger and more confident.
Almost all of the simulation's *uncertainty* comes from a single 0.35 s/lap noise term, which
is too small to create real doubt around a confident deterministic term — so the answer
collapses to 0.999 or 0.001. The score did not become more right, it became more **sure**.
That is section 3b's own untested hypothesis, now with evidence, and CP1 parts A-B quantify
exactly how wrong the surrounding assumptions are: the code charges a fixed 22 s pit loss
when real loss ranges 18.6-29.7 s by circuit, and implies 1.40 s of gap drift over 8 laps
when the real figure is 2.20 s. It also always assumes T pits exactly one lap later, which
only ~40% of rivals do.

### 3g. CP1 parts C-E redone on the restored models (2026-09-23) — real signal, too little of it
**Parts A and B reproduced exactly** (model-independent, so this also confirms the harness
did not drift): pit loss median **22.7 s**, robust sd 3.11 s over 3,016 green-flag stops,
within-circuit spread median **1.80 s** (range 1.31-9.33), circuit medians 18.6 s (Spa) to
29.7 s (Imola); relative noise **0.42 / 0.73 / 1.00 / 1.53 / 2.20 s** at 1/2/3/5/8 laps
against the current code's implied 0.49 / 0.70 / 0.86 / 1.11 / 1.40 s.

**Part C — the tyre model now carries information. This is the headline.**

| | Reverted | **Restored** |
|---|---|---|
| Real fresh-tyre gain (310 events) | 1.20 s/lap | 1.20 s/lap (model-independent) |
| Tyre model's predicted gain | 0.86 s/lap | **0.72 s/lap** (sd 0.50) |
| **Correlation, real vs predicted** | **0.05** | **0.33** |
| Fit | — | real = **0.90 × predicted + 0.55** |
| Prediction error | — | **+0.48 s/lap** bias, robust sd 1.06 |

Correlation 0.05 → 0.33 over 310 events: the reverted model's event-level gain was noise, the
restored one genuinely ranks events. The 0.90 slope means it is nearly correctly **scaled** and
its remaining fault is a consistent low bias of about half a second per lap — interpretable and
cheaply correctable. Note the mean prediction moved *further* from reality (0.86 → 0.72) while
the correlation improved sixfold: ranking got much better, average bias slightly worse.

**Part D — fires correctly now, but does not beat the historical mix.**

| | Reverted | **Restored** | Bar (3e) |
|---|---|---|---|
| Mean P(T makes no stop within 8 laps) | ≈1.00 | **0.06** | all events have a stop by construction |
| Log-likelihood of T's real pit lap | — | **−1.864** | must exceed **−1.838** ✗ |
| Correlation, predicted vs real delay | — | **0.29** | — |

The same-lap detector's flatline is gone. P(T pits the very next lap) is **well calibrated**
across 696 events — 0.13 → 0.06 actual (n=47), 0.29 → 0.34 (n=237), 0.42 → 0.41 (n=360),
0.53 → 0.54 (n=52) — but **not sharp**: predictions cluster in 0.29-0.42, so on *which* of the
next eight laps T stops it carries no more information than the historical delay distribution.
Calibrated but uninformative is a real distinction and the bar in 3e was not met.

**Part E — every part now helps, by an amount too small to ship.**

| Model (5-fold race-grouped CV, 2018-2024) | Reverted | **Restored** | Δ vs gap |
|---|---|---|---|
| gap only | 0.1974 | 0.1974 | — |
| gap + P(T pits next lap) | no improvement | 0.1967 | −0.0007 |
| gap + expected T delay | no improvement | 0.1970 | −0.0004 |
| gap + tyre-model gain/lap | no improvement | **0.1951** | **−0.0023** |
| gap + tyre gain × expected delay | no improvement | **0.1950** | **−0.0024** |
| gap + expected delay + tyre gain | no improvement | 0.1954 | −0.0020 |

Section 3d recorded "no part improved on gap only." That has flipped — all six now do. The
best gain is **0.0024 Brier, about 1.2% relative**, in-sample CV.

**Why that settles Option B: its CP2 gate is unreachable.** The gate is "beat gap alone on
held-out 2025 with the paired Brier CI excluding zero." That CI's half-width on 104 events is
about **±0.009**, measured twice (+0.004 ±0.010 on 2026-09-21, +0.001 ±0.008 today).
Resolving a true effect of 0.0024 needs roughly (0.009/0.0024)² ≈ **14× more held-out data —
about 1,460 events. Only 800 exist in total** across 2018-2025, and most must be training
data; spending every event as holdout still leaves a half-width near 0.0032, wider than the
effect. So the parts carry real information, the effect is real, and it is **structurally
unmeasurable at this sample size.**

**The honest counterargument, recorded:** part E combines the parts with a *linear logistic*,
and a full Monte Carlo might extract more than that. But a rebuild would have to produce
several times what a linear model gets from the same inputs, and nothing measured here
suggests it would.

### 3h. The decision on the table (owner's, not taken as of 2026-09-23)
**Recommendation: do not proceed to CP2. Replace the score's internals with a calibrated
gap-based probability** (section 3c's Option A), keeping the endpoint, field name, storage and
alert plumbing unchanged.

Why:
- Gap-only is calibrated, scores **Brier 0.193 / AUC 0.766** on held-out 2025, and beats the
  current score by **−0.138 Brier with a CI nowhere near zero** — the only improvement here
  that is statistically solid rather than hopeful.
- A **≥0.6** threshold gives 24 alerts on 2025 at **75% precision** (45% of successes caught),
  against today's 0.5 on an uncalibrated score. `UNDERCUT_ALERT_THRESHOLD` would move with it.
- It is a contained change, not a rebuild.

Known consequences and caveats to handle when it is done:
- **`get_overcut_score` shares the same helper**, so overcut changes too. Needs its own
  check, not an assumption.
- A bare logistic gives a number without a story, where the simulation could at least explain
  itself. **If that explanation surface matters for the UI, the honest move is to keep a
  simulation for interpretability and drop the claim that it predicts better** — a legitimate
  product argument, but not a calibration win and it must not be presented as one. That is the
  only form in which "continue Option B" is defensible, and it requires changing CP2's success
  criterion from "beat gap alone" to "match gap alone while staying interpretable."
- Worth keeping from CP1 either way: part C's +0.48 s/lap bias with a 0.90 slope is cheaply
  correctable, and part D's per-lap pit calibration is good enough to be useful elsewhere —
  it is the mechanism `race_simulator` needs for rivals to pit organically.
- **Do this AFTER Azerbaijan.** It sits in the live per-lap path, so it needs a full
  shadow-race re-run (~44 min at 2x plus verify), and that cannot run while a real race is live.
- The alert gates still have no outcome evidence behind them and cannot get any from history:
  only **2 of 800** real attempts were made on tyres ≤3 laps old (teams do not try it), and
  attempts with <15 laps left worked 42% (n=24) against 39% otherwise. Harmless, unevidenced.
- Success rises steadily with T's delay (+1: 24%, +2: 32%, +3: 45%, +4: 54%, +5: 58%, +6: 62%,
  +7: 59%, +8: 52%, all 2018-2025), while the score barely tracks it (mean 0.20-0.40). Any
  replacement should be checked against this gradient.

---

## 4. V5 — what only the next real race can answer

Two questions cannot be answered from an archive or a replay; both are answered by the next
race with the stack running (Azerbaijan GP, Round 15, **2026-09-26** in the local `races`
table). Nothing needs to be started by hand: `docker-compose.yml` defaults `RECORD_RAW_FEED`
to true, beat polls every 5 minutes, and the worker launches the ingestor about 30 minutes
before the race.

1. **Does F1's race-order `Position` field stream on the live socket?** The archive
   showed it complete (1052 of 1052 laps), but the 2026 Dutch GP live run saw it only in the
   snapshot. The ingestor logs when it first streams and writes
   `position_first_message_seq` to `f1:{season}:{round}:ingest_stats`; it logs a warning if it
   never did.
2. **Does the recorder behave on a real socket?** It is proven on replayed data only.

### Before the race (checklist)
- Stack up with `--env-file .env` (CLAUDE.md: secrets silently blank otherwise); worker on
  the current code (`docker restart docker-worker-1`; confirmed this session).
- **Do not run `shadow_race run` while a real race is live** (it refuses if it detects one).
- No leftover season-2098 race or `auto_ingestion_triggered` lock in Redis (both checked clean
  on 2026-09-20).
- Free disk for `recordings/` (a few MB per race).
- **Production models are the restored 2026-09-11 set** (section 0a), re-verified
  byte-identical on **2026-09-23** (all 14 objects, `etag_compare.py`), and the worker was
  started after the restore (2026-09-21 18:45 UTC); a later restart is fine. Check with
  `recordings/v2_analysis/probe_models.py`, as run in section 0a.
- **Uncommitted at the end of 2026-09-23:** item 14's two-file fix (section 6b). It is not on
  the live per-lap path and needs no shadow-race re-run, but the backend live-reloads from the
  mount, so `/overview` is already serving the fixed path locally. Decide before the race
  whether to keep or revert it so the race runs on known code.

### After the race (same day; the Redis keys last 24 h)
```
docker exec docker-redis-1 redis-cli GET f1:2026:15:ingest_stats
docker exec docker-redis-1 redis-cli HGETALL f1:2026:15:pipeline_stats
docker logs docker-worker-1 2>&1 | grep -E "Position field|Live ingest summary|never streamed"
python -m backend.scripts.verify_live_feed_archive --recording recordings/<file> --season 2026 --round 15
```
Record: whether and when `Position` first streamed; the share of rankings by F1 position vs
gaps; the socket-vs-archive message counts per topic; and the pipeline counters (live vs
summed gaps, alerts suppressed by each gate). Also count any `run_strategy_prediction` retry
that was **not** followed by a success (input to item 3b).

---

## 5. Lead-lap gap-only fallback — conditional on V5

**Trigger.** Only if the race shows `Position` does **not** stream live, so the whole race
runs on the gap-based ranking. If it does stream, the fallback is a rarely-used safety net
and this effort should go elsewhere.

**Where it stands (measured, V1 over the 14 completed 2026 races):** gap-only position match
**92.2%** overall (range 86.3%-96.4%; weakest Dutch 86.3%, Spanish 87.4%) against **99.8%**
with `Position`. On the Dutch GP, 4.7% of adjacent pairs are still in the wrong order, mostly two
lead-lap cars (42,268 wrong pairs, 4.4% of that pair type): about 34% had held gaps under 1 s,
about 26% involved a car in or just out of the pits, about 35% involved a gap update more than 40 s
old. **No hypothesis has been tested yet.**

**Hypotheses, one at a time, each measured on the 14 races:**
1. *Stale gaps (older than 40 s):* find what produces them (message patterns, pit lane,
   red-flag or safety-car periods) and whether to discount or extrapolate.
2. *Pit lane / pit exit:* a car's gap through the pits may not track its position order;
   test special handling around `InPit` / `PitOut`.
3. *Small-gap noise (under 1 s):* hysteresis on adjacent swaps, weighed against the lag it adds
   to genuine overtakes.

**Method.** Promote the scratch diagnosis (classifying adjacent inversions by pair type, gap
size, staleness, pit involvement) into the harness as `--diagnose`; keep a change only if
gap-only accuracy improves without hurting the `Position` path or the lapped-car result; add unit
and property tests for what stays. **Proposed targets (to be agreed):** gap-only >= 95%
overall and >= 90% on the worst race. Reproduce the baseline with:
```
python -m backend.scripts.verify_live_feed_archive --season 2026 --rounds 1-14 --no-position-diffs
```
Effort: medium.

---

## 6. Other leftovers, with the evidence for each

| # | Item | Evidence / why it matters | Suggested handling |
|---|---|---|---|
| 1 | ✅ **Explained 2026-09-20 (section 6a):** the 116 summed-path gap computations are all pairs involving a **lapped car** (PER, BOT, ALB, OCO), which has no seconds-to-leader gap by design | Reproduced exactly (116 of 2002 in the replay vs 116 of 2012 in the run); zero on laps 1-5, so the "first laps" guess was wrong | Optional: two lapped cars fighting each other still fall back to the DB sum (section 6a) |
| 2 | ✅ **Explained 2026-09-20 (section 6a):** the 70.4 s max is a queueing effect at the start of the race, made worse by the 2x speed-up; not a defect | Lap 1 dispatches 22 tasks at once; the single solo worker takes ~1.6 s per prediction; lap 2 arrives 41 s later (2x) and queues behind it | None needed; a 1x run would confirm the smaller real-time figure |
| 3 | **Cold worker start not exercised** (~88 s of imports, CLAUDE.md) | Every shadow run used a warm worker | Restart the worker just before a shadow run and measure the first laps |
| 4 | **Shadow race ran at 2x**, not 1x | Speed-up changes worker lag relative to real time | Optional 1x full run (about 90 min) if lag ever matters |
| 5 | **Existing Monza rows keep their pre-fix values** (`lap_data.position`, `strategy_predictions`, alerts) | Nothing re-scored them in the DB; only offline evaluations did | Leave, or re-ingest if a clean demo session is wanted |
| 6 | **Replays and historical sessions still form alert pairs from stored rows**, where retirees linger | Only live payloads use the live standings (`source == "live"`) | Accepted limit; document if replay alerts look odd |
| 7 | **The alert target is not stored with the prediction** | The alert pairs the trailing car with the live-adjacent one, normally the worker's target too | Store the target if V6 finds mismatches |
| 8 | **`total_laps` gate off for any session without it** | Live sessions get it from the lap count; older sessions do not | Backfill if a replayed session should exercise the gate |
| 9 | **scikit-learn / XGBoost version-mismatch warnings** (saved 1.9.1, loaded 1.9.0) | Evaluation numbers were produced under it | Align versions when convenient; not a correctness finding |
| 10 | **`SessionInfo` is subscribed with no handler**; the `SectorTime` table is unused; `isReplayActive` is misnamed; the `resilience` test marker is selected by no CI workflow | Listed in the Monza doc, section 7c | Housekeeping |
| 11 | **CLAUDE.md "Current Project Phase" block needs this session added** (model rollback, V2 results, Option B) | The owner updates it; not touched by the session | Owner |
| 12 | **The FCM push path is untestable here** (Firebase not configured) | `_send_fcm` skips when no app/token | Needs Firebase setup (External Services checklist) |
| 13 | **Nothing here has met a real live socket** | Everything is archive, replay and shadow evidence | V5, section 4 |
| 14 | ✅ **Measured and fixed 2026-09-23 (section 6b):** `strategy_service._first_pit_laps_over_threshold_batch` passed `np.zeros(...)` as `fuel_load_penalty` | Moves the predicted competitor pit lap for **27.7%** of driver-snapshots (13.7% by ≥5 laps), with **no measurable accuracy effect** (5.78 → 6.12 mean laps error; fixed closer 84 vs production 91 — a coin flip) | Shipped as a consistency fix only, not an improvement. Owner to commit |
| 15 | **New 2026-09-21:** `pit_predictor.add_gap_features` (training) still builds gaps from a running sum of `lap_time_seconds`, i.e. the NULL-lap bug fixed for inference on 2026-09-02 (item A) | On 2024 laps: median error 0, but **13.3% of gaps off by more than 2 s** and 4.4% capped at 120 s (true: 0.1%) | Use `session_elapsed_seconds` in training; needs a retrain and schema-version bump (owner decision) |
| 16 | **New 2026-09-21:** Monza 2026 `tire_stints` rows are unreliable (live-ingested: every car shows a stop on lap 4; VER stint 2 compound "UNKNOWN") | Found during V6 | Read stops from `lap_data` for live-ingested sessions; possibly related to Issue D (lap 4) |
| 17 | **New 2026-09-21:** the S3 IAM user cannot `ListBucketVersions` | The restore could not use S3 version history; the local backup in `recordings/v2_analysis/` is the only copy of the 2026-09-14 objects outside their `20260914-074521/` folder | Grant it if versioned rollback is wanted |
| 18 | **New 2026-09-21:** nothing stops a scheduled retrain from running code that lacks the current schema versions | Root cause of section 0a | Consider a workflow guard or recording the code's git SHA in each sidecar (section 0a) |
| 19 | **New 2026-09-23 (section 6c):** `get_competitor_predicted_strategy` pins **three of `pit_predictor`'s eight features** to constants — both gaps at `MAX_GAP_SECONDS` (120 s) and `safety_car_probability` at 0.0 | Competitor pit laps are off by a **mean 5.78 laps** against real stops, only **34.5% within 3 laps**, on a 15-lap horizon. This, not the fuel input, is what limits the feature | Measure with the item-14 harness first (read-only), then scope. The SC half is a small fix; the gap half needs a **batched** helper. After Azerbaijan |
| 20 | **New 2026-09-23:** `test_get_competitor_predicted_strategy_prefers_stored_total_laps`'s docstring claimed the horizon math was "exercised via `_first_pit_laps_over_threshold_batch`'s own dedicated tests below" — no such test existed | Harmless stale prose, but it asserted coverage that was absent | Now true: item 14's fix added the first direct test of that function (section 6b) |

### 6a. Leftovers 1 and 2 investigated (2026-09-20) — findings and proof

Read-only work: two scratch scripts (kept outside the repo, in the session scratchpad) and the
worker log. No production code changed. The shadow race's database rows had already been cleaned
up, so both used what survives: the raw-feed recording
(`recordings/2098_R01_R_20260920T162828Z.jsonl.gz`), the manifest (`recordings/shadow/full.json`,
which holds every lap's dispatch time) and `docker logs docker-worker-1`.

**Leftover 1 — why 116 undercut/overcut computations used the summed lap-time gap.**
- **Where the choice is made:** `strategy_service._undercut_overcut_probability` asks
  `_live_gap_deficit` for F1's live gap between the two cars and only falls back to the DB sum
  when that returns `None`. `None` is returned when there is no live payload for the session,
  either car is missing from it, or **either car has no seconds gap to the leader**, which is the
  case for a lapped car (its docstring says so).
- **Method:** replayed the recording through the unmodified ingestor
  (`verify_live_feed_archive.replay` with its `on_publish` hook); at every lap dispatch, took the
  driver's standings neighbours ahead and behind (the two pairs the worker scores) and checked
  which pairs would have no live deficit.
- **Result:** 1886 live + **116 summed** = 2002 pairs, against 1896 + 116 = 2012 in the real run.
  **The summed count matches exactly** (67 pairs where both cars are lapped, 26 neighbour only,
  23 requester only); the 10-pair difference in the live count is from replaying the state at
  dispatch time rather than at the worker's later read.
- **Who:** the cars with no seconds gap were **PER 99, BOT 49, ALB 25, OCO 10**, exactly the four
  lapped cars the Monza doc's section 0c names.
- **When:** none on laps 1-5. They start around lap 28 (PER and BOT go a lap down) and grow at the
  end (6-8 per lap on laps 47-52). **The "first laps" guess was wrong.**
- **Verdict:** working as designed, not a bug. A lapped car has no meaningful seconds-to-leader gap.
- **The one real limit:** a pair of two lapped cars fighting each other (PER vs BOT, 67 of the 116)
  falls back to the DB sum, which for a live-ingested session omits lap 1 (the exact defect of Issue
  B). The gap between two lapped cars could be taken from their difference in gap-to-leader plus
  laps down, or from F1's per-car interval field. Small (5.8% of pairs, back-markers), not urgent;
  it belongs with V2's review of what an alert should mean for lapped cars.

**Leftover 2 — why the max prediction lag was 70.4 s.**
- **Method:** reconstructed each prediction's finish time from the worker log
  (`run_strategy_prediction ... succeeded`) and its dispatch time from the manifest, pairing the
  i-th dispatch with the i-th finish. **Check that the method is sound:** it gives median 5.3 s,
  p95 26.3 s, max 70.4 s against the harness's own 5.1 / 25.9 / 70.4 (the database timestamps).
- **Where the max is:** the five worst are all **lap 1** (ALO, BOT, STR, LAW, ALB: 62-70 s), and
  lap 2 is next (61.9 s). Every later lap is 32 s or less (worst after that: lap 6, 32.3 s).
- **Why:** the worker is one `--pool=solo` process. `run_strategy_prediction` takes a median
  **1.63 s** (p95 2.08, max 2.70; sum 1729 s) and `process_lap` 0.12 s (sum 130 s). Lap 1 dispatches
  **22 predictions in one burst**, about 36 s of work, plus the lap-1 retries. At 2x, lap 2 is
  dispatched only **41 s** after lap 1 (lap 3 at +101 s), so it queues behind lap 1's unfinished
  backlog and the spike carries over. Burst size shows the same thing: dispatches in a burst of 13+
  had median lag 25.5 s, against 3.9 s for isolated ones.
- **The 17 retries** (item 3b) were all in lap 1's window, +21 s to +39 s after the first dispatch,
  i.e. the start-of-race burst is where the ordering race shows up; there were none on any later lap.
- **Load:** these two tasks kept the worker **73% busy over the 2x feed** (1859 s of 2536 s). At real
  speed that is about **37%**, so the backlog would drain between laps; the 70 s figure is largely an
  artefact of the speed-up. **This last step is an estimate, not a measurement:** a 1x full run
  (about 90 minutes) would confirm it.
- **Verdict:** not a defect. It does say the single worker's real capacity is about one 22-car burst
  per ~36 s of prediction work, so anything that bunches the field (a restart after a red flag, a
  safety-car queue) will produce a similar start-of-burst lag.

### 6b. Item 14 measured and fixed (2026-09-23) — a real bug with no accuracy consequence

**The defect.** `strategy_service._first_pit_laps_over_threshold_batch` passed
`np.zeros(len(group_idx))` as the `fuel_load_penalty` argument to
`tire_deg_model.predict_life_remaining_batch`, telling every tyre model the tank was empty on
every lap. That argument's own docstring forbids it explicitly ("must come from
`fuel_load_penalty_seconds` — see this module's docstring on why building it any other way is
what broke these predictions before 2026-09-09"). `prediction_worker`'s live path (line ~731)
and `race_simulator` both comply; this was the last call site that did not.

**Measured impact — it moves the output a lot.** 945 driver-snapshots across six real races
(2026 R5/R9/R10, 2025 R22/R23/R24), calling the real production function twice with only that
one input differing. `get_competitor_predicted_strategy` reads each driver's *latest* lap, which
on a finished race is the final lap where the penalty is ≈0 and the bug is invisible — so the
measurement uses as-of-lap snapshots (laps 5-40), as a live race would see it.

| Metric | Result |
|---|---|
| Predicted pit lap changes | **262 of 945 (27.7%)** |
| \|shift\| ≥ 2 / ≥ 3 / ≥ 5 laps | 209 (22.1%) / 161 (17.0%) / **129 (13.7%)** |
| Mean shift | +0.78 laps (median 0, range **−14 to +14**) |
| Pit probability, mean \|shift\| | 0.0615 (**max 0.659**) |

The effect tracks the penalty's size and vanishes as it decays — Spa R10 is the cleanest case:
16/21 drivers change at lap 5 (real fuel 2.92 s), 13/20 at lap 15, then **0 changes at laps 25,
30, 35, 40**. It is lumpy rather than smooth because the output only moves when a prediction
crosses a threshold, which is also where ±14 comes from (a driver flipping between crossing
`ALERT_THRESHOLD` inside the horizon and falling back to the horizon-final lap).

**Direction, corrected.** The session's first prediction — that the buggy version biased pit
laps *late* — was **wrong**. Production (zeros) predicts pit laps **~0.8 laps earlier** and with
**~0.035 higher** probability. The reasoning that failed assumed "no fuel → lighter → faster →
less degradation," but the training target already has the fuel effect subtracted out, so the
feature's learned role is residual and runs the other way (a high penalty partly proxies "early
race, tyres still fresh"). The bug makes competitor pit predictions slightly too **eager**.

**Measured accuracy — the fix changes nothing.** Each prediction scored against the driver's
real next pit in-lap (`tire_stints`, `start_lap - 1`, the `pit_predictor.label_pit_laps` rule):

| Population (a): real pit inside the 15-lap horizon | Production (zeros) | Fixed (real fuel) |
|---|---|---|
| Mean \|error\|, all 444 rows | **5.78 laps** | **6.12 laps** (+0.34) |
| Mean \|error\|, the 182 rows where they differ | 4.78 laps | 5.62 laps (+0.84) |
| Within 3 laps | 34.5% | 32.9% |
| Which was closer (non-ties) | **91** | **84** |

Population (b), any later pit (n=627): 6.23 vs 6.45 laps, 114 vs 110 closer. **Read this as "no
effect," not "the fix hurts":** 84 of 175 non-ties is 48%, a coin flip (binomial p ≈ 0.65), and
the +0.34 mean-error gap sits inside that noise. The implementation choice was checked before
concluding this — the roll-forward asks "if this driver were at lap *current+offset*," so the
penalty for that hypothetical lap is correct, and CP1 part D uses the same convention.

**Shipped anyway, on stated grounds: consistency, not accuracy.** Training builds this feature
one way, so inference must match; every other call site already does; leaving one outlier is how
this bug class returned once already; and anything copied from this function would inherit it.
The commit message and any summary should say **"closes a documented train/inference skew;
changes 27.7% of predicted competitor pit laps with no measurable accuracy effect"** — not that
it improves anything.

**Files (2):** `backend/services/strategy_service.py` (the argument, with a comment saying why)
and `backend/tests/unit/test_strategy_service.py` (`test_first_pit_laps_batch_passes_the_real_
fuel_penalty_not_zeros` — the **first direct test** of `_first_pit_laps_over_threshold_batch`;
it captures the argument at every offset and asserts it equals the helper's output, is strictly
positive, and strictly decreases as the lap advances). Zeros are a plausible value inside the
feature's real range, so nothing downstream would ever raise on them — only asserting on the
argument itself can catch a regression.

**Verified.** Mutation check: with `np.zeros(...)` restored the new test fails on
`assert np.allclose(fuel, expected)`; the file was then restored and confirmed byte-identical by
sha256. `ruff check` clean; `ruff format --check` clean (159 files); `mypy backend/ --strict`
clean (159 files); **unit suite 652 passed** (651 + 1); integration
`test_live_prediction_pipeline` / `test_race_simulation_serialization` / `test_strategy_endpoint`
/ `test_alerts` **17 passed**.

**Blast radius checked before touching it:** the function has exactly one production caller,
`get_competitor_predicted_strategy` (`/overview` and `warm_strategy_cache.py`). It is **not** on
the per-lap live prediction path — `prediction_worker` has its own inline single-driver code — so
it cannot affect live ingestion, stored predictions or alerts, and **needs no shadow-race
re-run.** That is what made it safe to do in the pre-race window.

### 6c. Item 19 — three of `pit_predictor`'s eight inputs are pinned to constants (found 2026-09-23)

Surfaced by item 14's accuracy measurement, which showed **both** versions are weak: mean
absolute error ~6 laps against a 15-lap horizon, only a third of predictions within 3 laps. The
cause is three lines above the fuel bug. `get_competitor_predicted_strategy` passes:

```python
np.full(n, pit_predictor.MAX_GAP_SECONDS),   # gap_to_car_ahead
np.full(n, pit_predictor.MAX_GAP_SECONDS),   # gap_to_car_behind
np.zeros(n),                                  # safety_car_probability
```

`MAX_GAP_SECONDS` is **120.0** — not merely "nobody near," but the clamp *ceiling*, far outside
the realistic distribution (real adjacent-car gaps are typically 0.5-10 s). So every driver is
scored at an extreme, out-of-distribution point in two of eight features, and those two are the
actual reason teams pit reactively. Against that, a 3.3 s fuel-feature error is a rounding
detail — **which is why fixing item 14 changed nothing.**

**The module docstring's justification is half right.** It says a real forward gap/SC model
"would need `telemetry_service` (forbidden import) or the full `race_simulator.py` multi-driver
simulation, which is out of scope." Checked against the code:
- **Safety car: the rationale does not apply at all.** `safety_car_model.pkl` is *already in the
  `models` dict this function receives*, and `prediction_worker` (line ~776) gets a real value
  with `sc_model.probability_within(circuit_name, lap_number, is_wet, 1)` — circuit name and lap
  number are both already in hand. No forbidden import, no simulation. **Why it was never
  revisited:** until 2026-09-11 the model was the always-zero artifact, so pinning the input to
  0.0 was *exactly equivalent* to calling it; CP3's real model made this silently wrong (see
  section 0a's "Still to check"). Small fix, one line plus a test — but note SC probabilities are
  small (0.002-0.02), so expect little movement. Free correctness, not a promised win.
- **Current gaps: already solved in this same module.** `_resolve_field_neighbors`
  (`strategy_service.py:1113`) returns real `gap_to_car_ahead` / `gap_to_car_behind`,
  live-standings-first via Redis, falling back to a `current_lap`-bounded DB query differencing
  `_cumulative_race_time` (which prefers `session_elapsed_seconds` since the 2026-09-02 fix).
  `get_competitor_predicted_strategy` already holds both `client` and `db`. The achievable change
  is to pass each driver's **real current** gaps and hold *those* constant across the horizon
  instead of holding 120 s constant.
- **Forward gaps: the docstring is right.** How the gap evolves across the 15-lap horizon does
  need the multi-driver simulation. Out of scope; holding the current value is the honest
  approximation.

**The catch is cost.** `_resolve_field_neighbors` is per-driver and makes three
`_cumulative_race_time` awaits each — ~60 extra queries per call across a 20-car field, on the
endpoint CLAUDE.md already documents as the 16-17 s cold-compute floor. It needs a **batched**
variant (the field is one query; the gaps are differences within it) — the same lesson as
`_first_pit_laps_over_threshold_batch` itself. Medium effort, and it must be batched, not looped.

**How to approach it — measure before building.** The item-14 accuracy harness already scores
predicted pit laps against real stops. **Baseline to beat: 5.78 laps mean error, 34.5% within 3
laps** (population (a), 444 rows). Feed real current gaps and a real SC probability through the
same read-only harness first (~15 min, no production code). If the error drops meaningfully,
scope the production change properly, including a `/overview` latency re-check. If it does not,
the feature is limited by the horizon-constant assumption rather than the input values — which
would point the same way as the tire-deg doc's CP5: **pit timing is partly strategic in ways a
degradation-plus-gap model structurally cannot see.**

**Stated prior (a prior, not a measurement):** the gaps should help somewhat, the SC almost not
at all, and neither is likely to bring a ~6-lap error down to something a race decision could
rest on. Cheap to check either way. **Do not start before Azerbaijan** — the batched gap work
touches the most latency-sensitive endpoint in the project.

### 6d. The pattern worth carrying forward (2026-09-23)

Twice in one session, fixing an input did not improve the output: the undercut score got
**worse** when its models were corrected (section 3f), and item 14's genuinely-wrong fuel input
moved predictions substantially without moving them **toward reality** (section 6b). Both have
the same explanation, and CP5 of `docs/tire-deg-model-quality-and-rival-pit-behavior.md` reached
it independently for pit timing: **these features are limited by the structure of their
calculation, not by the quality of what goes into them.** Useful as a prioritisation rule —
further input fixes are unlikely to pay; structural work (or an honestly simpler model) might.

---

## 7. Reproducing what is already proven

```
python -m backend.scripts.verify_live_feed_archive --season 2026 --round 13                  # Monza replay
python -m backend.scripts.verify_live_feed_archive --season 2026 --rounds 1-14               # V1 table (~7 min)
python -m backend.scripts.shadow_race cleanup                                                # remove any shadow race first
python -m backend.scripts.shadow_race run --speed 2 --manifest recordings/shadow/full.json   # ~44 min
python -m backend.scripts.shadow_race verify --manifest recordings/shadow/full.json          # 14 checks
python -m backend.scripts.shadow_race cleanup                                                # and again afterwards
python -m backend.scripts.evaluate_undercut_live_gaps                                        # offline re-score (~90 s; DB + S3)
```
**V2 / Option B / restore scripts (2026-09-21).** These are local only, in the gitignored
`recordings/v2_analysis/`. They are not part of the repo, so copy them into `backend/scripts/`
if they should be kept for good. All are read-only unless noted; run from the repo root with
`PYTHONPATH=.` and `-W ignore` (sklearn/xgboost version warnings).

| File | What it does |
|---|---|
| `v2_step0_count.py` | Step 0 event counts (section 3a); imported by the others for the event definition |
| `v2_step0_spot.py` | Prints three random 2025 events against the raw laps |
| `v2_calibrate.py <out.csv>` | Rebuilds the gap < 5 s clean events, scores them with the live code path, labels them, writes the CSV and prints section 3b's tables |
| `v2_recalibrate.py <events.csv>` | Option 1 fit and test (section 3c) |
| `v6_monza.py` | Monza gated alert replay with laps (section 3b, V6); uses `evaluate_undercut_live_gaps` and downloads F1's archive |
| `cp1_measure.py <events.csv> <out.csv>` | CP1 parts A-E (sections 3d, 3g) |
| `restore_models.py` | **Writes to S3 `production/`.** The restore of section 0a (backup, copy, sha256 verify). Do not re-run casually |
| `probe_models.py` | Loads production models and prints feature counts, the WET alias, safety-car rates and a `pit_predictor` probe. Run inside the worker with `docker cp` + `docker exec -w /app` |
| `check_production_sidecars.py` | **New 2026-09-23.** Lists every `production/` object with `LastModified`/size/ETag and prints each sidecar's `training_schema_version`, `holdout_mae`, `feature_names`, `positive_rate`. This is the section 0a "Still to check" loop — **run it after the 2026-09-28 cron.** Read-only; run inside the worker |
| `etag_compare.py` | **New 2026-09-23.** Compares all 14 `production/` ETags against `20260911-065536/` and prints IDENTICAL/DIFFERS per object. The cheapest proof nothing has overwritten the restored set. Read-only; run inside the worker |
| `measure_item14.py` | **New 2026-09-23.** Item 14's impact measurement (section 6b): calls the real `_first_pit_laps_over_threshold_batch` twice per as-of-lap snapshot, zeros vs real fuel, only that input differing. Now that the fix has shipped it measures ~nothing — **repurpose it by changing which input is varied** (that is exactly what item 19 needs) |
| `accuracy_item14.py` | **New 2026-09-23.** Scores predicted competitor pit laps against the real next pit in-lap from `tire_stints` (section 6b). **This is item 19's harness: baseline 5.78 laps mean error, 34.5% within 3 laps** (population (a), 444 rows). Read-only |

Also kept there:
- The 2026-09-21 outputs (`v2_events.csv`, `cp1_frame.csv`, `*.log`), computed on the reverted
  models — superseded, kept only for the before/after comparison.
- The 2026-09-23 outputs on the restored models: `v2_events_restored.csv`,
  `v2_run_restored.log`, `v2_recal_restored.log`, `cp1_frame_restored.csv`, `cp1_restored.log`.
- `production_backup_20260914/`, the replaced S3 objects.

Note all of `recordings/` is gitignored (`.gitignore:223`), so none of this is in the repo —
copy anything worth keeping into `backend/scripts/` deliberately.

Checks to run before calling any change done: `ruff check .`, `ruff format --check .`,
`mypy backend/ --strict`, `pytest backend/tests/unit -m unit` (**652** at the end of the
2026-09-23 session; 651 before item 14's test) and the integration tests that touch the
worker/prediction pipeline (`test_live_prediction_pipeline`,
`test_race_simulation_serialization`, `test_strategy_endpoint`, `test_alerts` = **17**).

---

## 8. Anchor prompt for resumption — paste into the new session

### Current anchor (2026-09-23, end of the V2 re-run / item-14 session)

```
Read docs/live-pipeline-open-decisions-and-calibration-2026.md before anything else: the
status note, sections 0 and 0a, 2 (the 3b decision), 3 with 3f/3g/3h (the V2 answer, CP1's
re-run, and the open decision), 4 (V5 at the next race), 5, 6 including 6b/6c/6d, and 7.
Then read docs/live-race-ingestion-and-strategy-gaps-monza-2026.md sections 0c, 7c and 7d
for the history, then CLAUDE.md as usual.

Context. Monza (R13) issues A-E are fixed and verified (V1 replay, V4 property tests, V5
counters + raw-feed recorder, V3 shadow race 14/14). Item 3a is fixed and committed; item
3b is decided (keep the bounded retry). The 2026-09-14 model rollback was restored on
2026-09-21 and re-verified byte-identical on 2026-09-23 (all 14 production/ objects vs
20260911-065536/; the 2026-09-21 cron promoted nothing).

What the 2026-09-23 session established, all measured, no assumptions:
 - V2 is ANSWERED. On the restored models the undercut score is WORSE than it looked on
   the reverted ones: 2025 held-out Brier 0.289 -> 0.332 against gap-only 0.193, AUC 0.64
   -> 0.595 against 0.766, saturated scores 22/104 -> 47/104. The one slice that used to
   defend it (T pits the very next lap) inverted: Brier 0.153 -> 0.310 vs gap-only 0.186,
   AUC 0.515, and 6 events scored ~0.96 with 0.00 actual. Option 1's refit puts even less
   weight on the score (logit coefficient 0.091 -> 0.053) and still does not beat gap alone
   (+0.001, CI -0.007 to +0.010). Section 3f.
 - CP1 parts C-E redone. The parts NOW carry real signal: the tyre model's event-level gain
   correlation went 0.05 -> 0.33 (slope 0.90, a correctable +0.48 s/lap low bias), and
   pit_predictor rolled forward stopped flatlining (P(no stop in 8 laps) 1.00 -> 0.06, and
   P(T pits next lap) is well calibrated across 696 events). But it MISSES 3e's bar on
   which lap (log-likelihood -1.864 vs the historical mix's -1.838), and in part E every
   part now beats gap-only by at most 0.0024 Brier. Section 3g.
 - Therefore Option B cannot pass its own CP2 gate: the paired Brier CI half-width on 104
   held-out events is about +/-0.009, so a 0.0024 effect needs ~14x more data (~1460
   events) and only 800 exist in total. Not a wrong idea - an unmeasurable one.
 - Recommendation on the table (section 3h, owner has NOT decided): replace the score's
   internals with a calibrated gap-based probability (Brier 0.193, AUC 0.766, beats the raw
   score by -0.138 with a CI clear of zero; a >=0.6 threshold gives 24 alerts at 75%
   precision on 2025). Keep endpoint/field/storage/alert plumbing. get_overcut_score shares
   the helper so overcut moves too. DO THIS AFTER the race - it is in the live per-lap path
   and needs a full shadow-race re-run. The only defensible form of "continue Option B" is
   keeping a simulation for interpretability with CP2's criterion changed from "beat gap
   alone" to "match gap alone" - and saying so honestly, not as a calibration win.
 - Item 14 fixed (section 6b), 2 files, uncommitted: strategy_service.py's
   _first_pit_laps_over_threshold_batch now passes fuel_load_penalty_seconds instead of
   np.zeros, plus the first direct unit test of that function. Measured: it moves 27.7% of
   predicted competitor pit laps (13.7% by >=5 laps) with NO measurable accuracy effect
   (5.78 -> 6.12 mean laps error; 84 vs 91 closer, a coin flip). Shipped as a CONSISTENCY
   fix - do not describe it as an improvement. ruff/format/mypy clean, unit 652,
   integration 17, mutation-checked.
 - New item 19 (section 6c): get_competitor_predicted_strategy pins three of
   pit_predictor's eight features - both gaps at MAX_GAP_SECONDS (120 s) and
   safety_car_probability at 0.0. That, not the fuel input, is why competitor pit laps are
   ~6 laps off. The safety-car zero became wrong only when CP3 promoted a real
   safety_car_model on 2026-09-11 (it was equivalent to calling the always-zero artifact
   before). Real current gaps are already available in-module via _resolve_field_neighbors;
   only FORWARD gaps need the simulation.
 - Section 6d: twice in one session a correct input failed to improve the output. These
   features are limited by the structure of their calculation, not their inputs. Treat that
   as a prioritisation rule.

What to do, in order (stop and report after each; wait for approval before the next):
 1. Azerbaijan R15 (2026-09-26 in the local races table) is the only calendar-bound item:
    run section 4's before-the-race checklist and, after the race, its four commands. Record
    whether and when F1's Position field first streamed, the share of rankings by position
    vs gaps, the socket-vs-archive per-topic counts, the pipeline counters, and any
    run_strategy_prediction NotFoundError retry NOT followed by a success (the remaining 3b
    check). NEVER run the shadow race while a real race is live.
 2. After Monday 2026-09-28 02:00 UTC, confirm the cron did not regress production/: run
    recordings/v2_analysis/check_production_sidecars.py and etag_compare.py inside the
    worker. Every sidecar must still show training_schema_version = 2.
 3. The undercut decision (section 3h) - gap-based probability, or Option B under a changed
    criterion. Agree it with me before writing any code; it touches the live path.
 4. Item 19 (section 6c): measure FIRST with recordings/v2_analysis/accuracy_item14.py
    (baseline 5.78 laps mean error, 34.5% within 3 laps) by feeding real current gaps and a
    real safety-car probability. Read-only, ~15 min. Only scope production code if the error
    actually drops - and the gap resolution must be BATCHED, since /overview is the
    documented 16-17 s cold-compute endpoint.
 5. The lead-lap gap-only fallback (section 5) only if V5 shows Position does not stream.
 6. Remaining section 6 leftovers: item 15 (pit_predictor training gaps from summed lap
    times - needs a retrain and a schema-version bump, keep it well clear of the pre-race
    window), items 16-18, item 20.

Code to read before proposing anything:
 - backend/services/strategy_service.py: _undercut_overcut_probability, _live_gap_deficit,
   get_undercut_score/get_overcut_score, _project_stint_delta,
   _first_pit_laps_over_threshold_batch and its caller get_competitor_predicted_strategy
   (~line 2141, where the three constants are passed), _resolve_field_neighbors (~line 1113,
   which already returns real gaps), and the module docstring's own note on why the
   constants were pinned (it is half wrong - see section 6c).
 - backend/services/alert_service.py (UNDERCUT_ALERT_* constants, ~lines 72-94).
 - backend/services/ml/pit_predictor.py (FEATURE_COLUMNS, MAX_GAP_SECONDS = 120.0,
   ALERT_THRESHOLD, label_pit_laps) and backend/services/ml/tire_deg_model.py
   (fuel_load_penalty_seconds, predict_life_remaining_batch and its contract docstring,
   project_stint_delta).
 - backend/services/ml/race_simulator.py (PIT_STOP_SECONDS, LAP_TIME_NOISE_STD_SECONDS -
   CP1 part A/B measured both to be wrong: real pit loss 18.6-29.7 s by circuit, real
   8-lap gap drift 2.20 s vs the code's implied 1.40 s).
 - backend/workers/prediction_worker.py (~lines 722-790: the live path that does the fuel
   penalty and the safety-car probability correctly - the reference for item 19).
 - backend/scripts/train_models.py (serialize_evaluate_and_upload, upload_model) and
   .github/workflows/train-models.yml (the Monday 02:00 UTC cron).
 - backend/scripts/evaluate_undercut_live_gaps.py, and recordings/v2_analysis/*.py
   (gitignored; see section 7's table for what each one does).
Tests to keep green: backend/tests/unit/test_strategy_service.py, test_alert_service.py,
test_prediction_worker.py, test_alert_worker.py. Unit suite 652 at the end of the session.

Working rules (also in the user's CLAUDE.md files):
 - Do not run any git command that changes anything. The owner commits, pushes, merges and
   opens PRs; read-only git (status/log/diff/show) is fine when asked. The owner also
   updates CLAUDE.md's "Current Project Phase" block.
 - State a brief plan and wait for approval before code touching more than 2 files, and
   confirm between checkpoints.
 - Ask before adding any dependency, and before any write to S3 production/.
 - No debug print (use logging), no TODO comments, no bare `except Exception`, type hints
   everywhere.
 - Measure before claiming a fix helps. Two "obvious" fixes this session moved nothing;
   both were only caught by scoring against real outcomes first.
 - If an error is not resolved in 2 attempts, stop and show what was tried, the exact error,
   the likely cause and two options.

Environment notes:
 - Windows + Git Bash. The Docker stack is docker-compose in infra/docker (containers
   docker-worker-1, docker-backend-1, docker-redis-1, docker-postgres-1, docker-beat-1;
   Postgres user f1user, db f1db). Bring it up with --env-file .env or secrets are silently
   blank.
 - The worker and backend mount backend/, so `docker restart docker-worker-1` picks up code
   changes and the backend live-reloads. Models load from S3 production/ at process start,
   so restart both after any model change.
 - Host venv is .venv/Scripts/python.exe; run the analysis scripts from the repo root with
   PYTHONPATH=. and -W ignore (sklearn/xgboost version warnings).
 - Celery runs --pool=solo. Avoid backslashes in inline heredocs; use MSYS_NO_PATHCONV=1 for
   docker exec/cp paths.
 - Checks before saying anything is done: ruff check, ruff format --check, mypy backend/
   --strict, pytest backend/tests/unit -m unit, and the integration tests that touch the
   worker/prediction pipeline.

First, report back in a few lines what you understood the state to be and what you propose
to do first, and wait for approval.
```

### Previous anchor (2026-09-22), superseded, kept as history

```
Read docs/live-pipeline-open-decisions-and-calibration-2026.md before anything else: the
status note, sections 0 and 0a, 2 (decision), 3 including 3a-3e, 4, 6 and 7. Then read
docs/live-race-ingestion-and-strategy-gaps-monza-2026.md sections 0c, 7c and 7d for the
history, then CLAUDE.md as usual.

Context. The Italian GP 2026 (Monza, R13) issues A-E are fixed and verified (replay V1,
property tests V4, counters and raw-feed recorder V5, shadow race V3 at 14 of 14). In the
2026-09-21/22 session:
 - Item 3b was decided: keep the bounded retry in run_strategy_prediction.
 - V2 found the undercut score badly calibrated and worse than the gap alone on held-out
   2025. A gap-based recalibration (Option 1) worked only because of the gap. The owner
   chose Option B (rebuild the undercut simulation: rival pit timing from pit_predictor,
   pit-loss spread, realistic noise), and CP1 was half done.
 - CP1 exposed that on 2026-09-14 the weekly train-models.yml cron, running main's pre-fix
   code, had replaced four production models (tire_deg_medium, tire_deg_hard,
   pit_predictor, safety_car_model) with pre-2026-09-11 versions. They were restored from
   S3 folder 20260911-065536/ (byte-identical, worker and backend restarted), and the
   owner merged develop into main (PR #126). The V2 score numbers and CP1 parts C-E were
   computed on the broken models and must be re-run.
 - No repository code changed. The analysis scripts are in the gitignored
   recordings/v2_analysis/ (section 7).

What to do, in order (stop and report after each; wait for approval before the next):
 1. Read-only check that S3 production/ still holds the 2026-09-11 models (section 3e step
    1; probe_models.py inside the worker). If today is after Monday 2026-09-28 02:00 UTC,
    also check that the cron did not regress it (section 0a "Still to check").
 2. Re-run V2 and Option 1 on the restored models (section 3e step 2) and compare with
    sections 3b/3c.
 3. Redo CP1 parts C-E (section 3e step 3). Report whether the tyre model's gain and the
    rolled-forward pit_predictor now carry information beyond the gap. Then decide with me
    whether Option B continues to CP2 or we fall back to a gap-based probability.
 4. Around Azerbaijan R15 (2026-09-26 in the local races table): the V5 checklist and
    commands (section 4), plus the 3b check (any NotFoundError retry not followed by a
    success). Never run the shadow race while a real race is live.
 5. The lead-lap gap-only fallback (section 5) only if V5 shows Position does not stream live.
 6. Leftovers in section 6. New ones: item 14 (zero fuel input in
    _first_pit_laps_over_threshold_batch), item 15 (pit_predictor training gaps from summed
    lap times), items 16-18.

Code to read before proposing anything:
 - backend/services/strategy_service.py (_undercut_overcut_probability, _live_gap_deficit,
   get_undercut_score, _first_pit_laps_over_threshold_batch, _project_stint_delta)
 - backend/services/alert_service.py (UNDERCUT_ALERT_* constants)
 - backend/services/ml/pit_predictor.py, backend/services/ml/tire_deg_model.py
   (fuel_load_penalty_seconds, predict_life_remaining_batch, project_stint_delta)
 - backend/services/ml/race_simulator.py (PIT_STOP_SECONDS, LAP_TIME_NOISE_STD_SECONDS)
 - backend/scripts/train_models.py (serialize_evaluate_and_upload, upload_model) and
   .github/workflows/train-models.yml (the Monday cron)
 - backend/scripts/evaluate_undercut_live_gaps.py
 - recordings/v2_analysis/*.py
Tests to keep green: backend/tests/unit/test_strategy_service.py, test_alert_service.py,
test_prediction_worker.py, test_alert_worker.py (unit suite 651 at the end of the session).

Working rules (also in the user's CLAUDE.md files):
 - Do not run any git command that changes anything. The owner commits, pushes, merges and
   opens PRs; read-only git (status/log/diff/show) is fine when asked.
 - State a brief plan and wait for approval before code touching more than 2 files, and
   confirm between checkpoints.
 - Ask before adding any dependency, and before any write to S3 production/.
 - No debug print (use logging), no TODO comments, no bare `except Exception`, type hints
   everywhere.
 - If an error is not resolved in 2 attempts, stop and show what was tried, the exact error,
   the likely cause and two options.

Environment notes:
 - Windows + Git Bash. The Docker stack is docker-compose in infra/docker (containers
   docker-worker-1, docker-backend-1, docker-redis-1, docker-postgres-1; Postgres user
   f1user, db f1db).
 - The worker mounts backend/, so `docker restart docker-worker-1` picks up code changes.
   Models load from S3 production/ at process start, so restart the worker and backend after
   any model change.
 - Celery runs --pool=solo. Avoid backslashes in inline heredocs; use MSYS_NO_PATHCONV=1 for
   docker exec paths.
 - Checks before saying anything is done: ruff check, ruff format --check, mypy backend/
   --strict, pytest backend/tests/unit -m unit, and the integration tests that touch the
   worker/prediction pipeline.

First, report back in a few lines what you understood the state to be and what you propose
to do first, and wait for approval.
```

### Previous anchor (2026-09-20), superseded, kept as history

```
Read docs/live-pipeline-open-decisions-and-calibration-2026.md before anything else
(status note, sections 0-6), then docs/live-race-ingestion-and-strategy-gaps-monza-2026.md
sections 0c, 7b, 7c and 7d for the full history, then CLAUDE.md as usual.

Context. The Italian GP 2026 (Monza, R13) was the first full live-ingested race. Its five
issues (A-E) are all fixed. Verification tooling exists: a replay harness over F1's
archived feed (verify_live_feed_archive.py, V1), property tests (V4), next-race counters and
a raw-feed recorder that is on by default in Docker (V5), and a shadow-race harness
(backend/scripts/shadow_race.py, V3). V3's full race (all 53 laps, 1052 lap completions, 2x)
passed 14 of 14 checks on 2026-09-20 and was cleaned up. Two production bugs it found in
prediction_worker.py (skipped engine dispose; a prediction running before its lap was
persisted, mitigated by a bounded retry) are fixed, and the same dispose flaw in
alert_worker._dispatch was fixed on 2026-09-20 (unit suite 651 passed).

What is NOT done:
 1. Item 3b, owner decision: keep the bounded retry in run_strategy_prediction (current;
    17 retries in the full race, none exhausted) or chain process_lap -> prediction.
    My recommendation was to keep the retry unless the real race shows exhausted retries.
 2. V2: historical calibration of probability_pit_now_gains_position (2018-2025, held-out
    2025, against a gap-only baseline), with V6 (review the Monza VER-on-RUS / VER-on-ANT
    alerts against tire_stints) folded in. Not started; needs owner approval. Step 0 is
    counting eligible events. The 0.5 alert threshold and the gate constants are not
    derived from outcomes.
 3. V5 verification at the next real race (Azerbaijan R15, 2026-09-26) with the stack
    running: does F1's Position field stream live; does the recorder work on a real socket.
    Section 4 of the new doc has the checklist and the exact commands.
 4. The lead-lap gap-only fallback work, only if V5 shows Position does not stream live.
 5. Small leftovers, section 6. The 116 summed-gap computations and the 70.4 s max lag
    were investigated and explained on 2026-09-20 (section 6a: lapped cars have no
    seconds gap by design; lap-1 burst queueing at the single worker, amplified by 2x).
    Still open there: cold start not exercised, a 1x run to confirm the lag estimate, and
    the rest of the list.

Code to read before proposing anything (all paths are real; the new doc's sections say why):
 - 3b: backend/scripts/ingest_live_session.py (~lines 830-831, the two back-to-back .delay
   calls), backend/workers/prediction_worker.py (run_strategy_prediction, ~line 1180, and
   _LAP_NOT_YET_PERSISTED_*), backend/workers/telemetry_worker.py (process_lap).
 - V2: backend/services/strategy_service.py (_undercut_overcut_probability, _live_gap_deficit,
   get_undercut_score), backend/services/alert_service.py (UNDERCUT_ALERT_* constants, ~lines
   72-94), backend/services/ml/race_simulator.py (LAP_TIME_NOISE_STD_SECONDS),
   backend/scripts/evaluate_undercut_live_gaps.py (how to score offline),
   backend/scripts/evaluate_pit_predictor_label_fix.py (an existing offline-evaluation pattern).
 - V5 / lead-lap: backend/scripts/verify_live_feed_archive.py (replay, --recording,
   --no-position-diffs), backend/scripts/_raw_feed_recorder.py, backend/scripts/shadow_race.py.
 - Tests to keep green: backend/tests/unit/test_prediction_worker.py, test_alert_worker.py,
   test_strategy_service.py, test_alert_service.py.
Also read section 6a of the new doc (the explained lag and lapped-car findings).

Working rules (also in the user's CLAUDE.md files): do not run any git command - the owner
commits, pushes and opens PRs; state a brief plan and wait for approval before code touching
more than 2 files, and confirm between checkpoints when a task is long; ask before adding
any dependency; no debug print (use logging), no TODO comments, no bare `except Exception`,
type hints everywhere; if an error is not resolved in 2 attempts, stop and show what was
tried, the exact error, the likely cause and two options. Never run the shadow race while a
real live race is running.

Environment notes: Windows + Git Bash; Docker stack is docker-compose in infra/docker
(containers docker-worker-1, docker-redis-1, docker-postgres-1); the worker mounts backend/,
so `docker restart docker-worker-1` picks up code changes (recreate with `--env-file .env`
only when compose settings change); Celery runs --pool=solo on three queues; avoid
backslashes in inline shell/Python heredocs; use MSYS_NO_PATHCONV=1 for docker exec paths.
Checks before saying anything is done: ruff check, ruff format --check, mypy backend/ --strict,
pytest backend/tests/unit -m unit, and the integration tests that touch the worker/prediction
pipeline.

First, report back in a few lines what you understood the state to be and what you propose
to do first, and wait for approval.
```
