# Live Pipeline — Open Decisions, Calibration and Next-Race Verification (follow-on to the Monza doc)

> **Status (2026-09-22, end of the V2 / model-restore session).** This document picks up
> where `docs/live-race-ingestion-and-strategy-gaps-monza-2026.md` ends. That document
> holds the five Monza issues (A-E, all fixed), the verification tooling (V1, V3, V4, V5)
> and the full evidence. This one holds **only what is still open**, the proof already
> in hand for each item, and how to close it.
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
| **Model rollback (new)** | Weekly retrain on `main` reverted 4 production models on 2026-09-14 | **✅ restored 2026-09-21; `develop` merged to `main` (PR #126)** (section 0a) | confirm the 2026-09-28 cron does not regress it |
| **3a** | `alert_worker._dispatch` skipped `dispose()` when the query raised | **✅ fixed and committed** (`e5f4868`, PR #124) | — |
| **3b** | Keep the bounded retry, or chain `process_lap` into the prediction | **✅ decided 2026-09-21: keep the retry (Option A)** (section 2) | check at Azerbaijan for any exhausted retry |
| **V2** | Does the undercut score mean what it says? (V6 folded in) | **run 2026-09-21, but on the reverted models: re-run needed** (section 3) | re-run on the restored models |
| **V2 Option 1** | Recalibrate the score with the gap (logistic) | **run; did not beat gap alone, not shipped** (section 3c) | re-run with V2 |
| **V2 Option B** | Rebuild the undercut simulation (rival pit timing, pit-loss spread, realistic noise) | **chosen by the owner; CP1 half done** (section 3d) | redo CP1 parts C-E on the restored models, then CP2 |
| **V5** | Which of F1's fields stream on the live socket; is the recorder sound | **built, awaiting a real race** (section 4) | Azerbaijan R15 (2026-09-26 in the local `races` table) with the stack up |
| **Lead-lap fallback** | Improve gap-only ranking from 92.2% | **conditional on V5** (section 5) | V5's answer |
| Others | Small leftovers and known limits, two new ones from this session | **listed** (section 6) | triage |

Order for the next session: (1) confirm production still holds the 2026-09-11 models
(section 0a check); (2) re-run V2 + Option 1 on them (section 3e); (3) redo CP1 parts C-E and
decide whether Option B continues; (4) V5 and the 3b check at Azerbaijan; (5) after Monday
2026-09-28, confirm the cron did not regress production; (6) lead-lap work only if V5 says so.

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

### Still to check
- **After Monday 2026-09-28 02:00 UTC,** check S3 `production/*.metrics.json`. Every sidecar
  should still carry `training_schema_version = 2`, and any model promoted that day must have
  been trained by the fixed code. Read-only check: the sidecar loop in
  `recordings/v2_analysis/` (see section 7).
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

## 3. V2 — historical calibration of the undercut score (V6 folded in) — run 2026-09-21, re-run needed (sections 3a-3e)

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
> `pit_predictor` (section 0a). The following do not depend on models and stand: the event
> definition and counts (3a), the labels, the gap-only baseline, and CP1 parts A-B. Every
> number involving the undercut score (3b, 3c, V6 scores) or the models (CP1 parts C-E) must
> be re-run on the restored models (section 3e) before any decision is taken on it.

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

### 3b. Calibration of the current score (2026-09-21; ⚠ computed on the reverted models, re-run needed)
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

### 3c. Option 1: recalibrate the score with the gap (2026-09-21; ⚠ re-run needed)
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

### 3d. Option B: rebuild the undercut simulation (plan approved 2026-09-21; CP1 half done)
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

### 3e. What the next session must do before continuing V2 / Option B
1. **Confirm production still holds the 2026-09-11 models:** sidecars with
   `training_schema_version = 2`, pit `positive_rate` ≈ 0.087, MEDIUM/HARD with
   `fuel_load_penalty`. The worker/backend must have been started after 2026-09-21 18:45 UTC.
2. **Re-run V2 on the restored models,** from the repo root with `PYTHONPATH=.`:
   `python -W ignore recordings/v2_analysis/v2_calibrate.py recordings/v2_analysis/v2_events_restored.csv`
   (about 1-2 min; it rebuilds the events and re-scores them; DB + S3 read-only).
   Then run `v2_recalibrate.py` on that CSV. Compare with sections 3b/3c:
   - Does the score's calibration and AUC improve?
   - Does the raw score now add information beyond the gap?
3. **Redo CP1 parts C-E:**
   `python -W ignore recordings/v2_analysis/cp1_measure.py <events csv> recordings/v2_analysis/cp1_frame_restored.csv`
   (about 5 min). Questions to answer:
   - Does the tyre model's predicted gain now correlate with the real gain (part C)?
   - Does `pit_predictor` rolled forward predict T's real pit lap better than the fixed
     historical mix (part D; model log-likelihood above −1.838 on 2018-2024)?
   - Does any part improve on gap only in part E?
4. **Decide with the owner:** if the re-run shows the parts carry information, go to CP2. If
   not, discuss falling back to a gap-based probability (Option A of section 3c) before
   building anything.
5. **Things the prototype must handle:**
   - Turn `pit_predictor`'s "within 3 laps" label into per-lap odds. CP1 used
     `h = 1 − (1 − p3)^(1/3)`; check whether that is calibrated.
   - Pass the real `fuel_load_penalty_seconds` into `predict_life_remaining_batch`, not
     zeros (see section 6, item 14).
   - Handle lapped-pair and red-flag stints.
   - Keep `evaluate_undercut_live_gaps.py` in step with any signature change.

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
- **Production models are the restored 2026-09-11 set** (section 0a), and the worker was
  started after the restore (2026-09-21 18:45 UTC); a later restart is fine. Check with
  `recordings/v2_analysis/probe_models.py`, as run in section 0a.

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
| 14 | **New 2026-09-21:** `strategy_service._first_pit_laps_over_threshold_batch` (behind `/overview`'s competitor strategy) passes `np.zeros(...)` as `fuel_load_penalty` to `tire_deg_model.predict_life_remaining_batch`; the worker passes the real `fuel_load_penalty_seconds` | `predicted_life_remaining`, and so the competitor pit laps, are computed with the wrong fuel input (a train/inference skew of the CP1 kind). Not measured how much it moves the output | Fix with a test; Option B's prototype must not copy it |
| 15 | **New 2026-09-21:** `pit_predictor.add_gap_features` (training) still builds gaps from a running sum of `lap_time_seconds`, i.e. the NULL-lap bug fixed for inference on 2026-09-02 (item A) | On 2024 laps: median error 0, but **13.3% of gaps off by more than 2 s** and 4.4% capped at 120 s (true: 0.1%) | Use `session_elapsed_seconds` in training; needs a retrain and schema-version bump (owner decision) |
| 16 | **New 2026-09-21:** Monza 2026 `tire_stints` rows are unreliable (live-ingested: every car shows a stop on lap 4; VER stint 2 compound "UNKNOWN") | Found during V6 | Read stops from `lap_data` for live-ingested sessions; possibly related to Issue D (lap 4) |
| 17 | **New 2026-09-21:** the S3 IAM user cannot `ListBucketVersions` | The restore could not use S3 version history; the local backup in `recordings/v2_analysis/` is the only copy of the 2026-09-14 objects outside their `20260914-074521/` folder | Grant it if versioned rollback is wanted |
| 18 | **New 2026-09-21:** nothing stops a scheduled retrain from running code that lacks the current schema versions | Root cause of section 0a | Consider a workflow guard or recording the code's git SHA in each sidecar (section 0a) |

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
| `cp1_measure.py <events.csv> <out.csv>` | CP1 parts A-E (section 3d) |
| `restore_models.py` | **Writes to S3 `production/`.** The restore of section 0a (backup, copy, sha256 verify). Do not re-run casually |
| `probe_models.py` | Loads production models and prints feature counts, the WET alias, safety-car rates and a `pit_predictor` probe. Run inside the worker with `docker cp` + `docker exec -w /app` |

Also kept there:
- The 2026-09-21 outputs (`v2_events.csv`, `cp1_frame.csv`, `*.log`), computed on the reverted models.
- `production_backup_20260914/`, the replaced S3 objects.

Checks to run before calling any change done: `ruff check .`, `ruff format --check .`,
`mypy backend/ --strict`, `pytest backend/tests/unit -m unit` (**651** at the end of this
session) and the integration tests that touch the worker/prediction pipeline
(`test_live_prediction_pipeline`, `test_race_simulation_serialization`,
`test_strategy_endpoint` = 14; `test_alerts` = 3).

---

## 8. Anchor prompt for resumption — paste into the new session

### Current anchor (2026-09-22, end of the V2 / model-restore session)

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
