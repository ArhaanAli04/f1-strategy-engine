# Live Pipeline — Open Decisions, Calibration and Next-Race Verification (follow-on to the Monza doc)

> **Status (2026-09-20).** This document picks up where
> `docs/live-race-ingestion-and-strategy-gaps-monza-2026.md` ends. That document
> holds the five Monza issues (A-E, all fixed), the verification tooling (V1, V3, V4, V5)
> and the full evidence. This one holds **only what is still open**, the proof already
> in hand for each item, and how to close it. Nothing here is a bug that is currently
> breaking a race; each item is a decision, a measurement, or something that needs a real
> race. **Nothing has been committed by the session.**
>
> **What changed at the end of the last session, in one paragraph.** The full-race shadow
> run (V3, all 53 laps, 1052 lap completions, 2x) passed 14 of 14 checks and its throwaway
> race was cleaned up. `alert_worker._dispatch`'s engine-dispose flaw (item 3a) was then
> fixed and tested (section 1). Everything else below is still open.

---

## 0. Where things stand

| Item | What it is | State | Needs |
|---|---|---|---|
| **3a** | `alert_worker._dispatch` skipped `dispose()` when the query raised | **✅ fixed 2026-09-20** (section 1) | commit |
| **3b** | Keep the bounded retry, or chain `process_lap` into the prediction | **open, owner decision** (section 2) | owner decision; shadow race after any change |
| **V2** | Does the undercut score mean what it says? (historical calibration, V6 folded in) | **not started** (section 3) | owner approval |
| **V5** | Which of F1's fields stream on the live socket; is the recorder sound | **built, awaiting a real race** (section 4) | the next race (Azerbaijan R15, 2026-09-26) with the stack up |
| **Lead-lap fallback** | Improve gap-only ranking from 92.2% | **conditional on V5** (section 5) | V5's answer |
| Others | Small leftovers and known limits | **listed** (section 6) | triage |

Order I would take them in: commit 3a; decide 3b (my recommendation: keep the retry);
V2 while the race is awaited; V5 at the race; the lead-lap work only if V5 says so.

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

## 2. Item 3b — chain `process_lap` into the prediction, or keep the retry

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

### Recommendation and how to decide
Keep Option A. The measured outcome is 1052 of 1052 with no exhausted retries, and B is a
structural change to something working. Move to B if the **real** Azerbaijan race shows any
`run_strategy_prediction` task failing after its retries (check the worker log for
`NotFoundError` that is not followed by a success), or if the p95 lag matters to a user-facing
feature. Either way, re-run the shadow race after touching this path.

---

## 3. V2 — historical calibration of the undercut score (V6 folded in) — not started

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
calls for them. **Needs owner approval before starting.**

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
| 11 | **CLAUDE.md "Current Project Phase" block is stale** (still describes the 2026-09-11 tire-deg session) | The owner updates it; I did not touch it | Owner |
| 12 | **The FCM push path is untestable here** (Firebase not configured) | `_send_fcm` skips when no app/token | Needs Firebase setup (External Services checklist) |
| 13 | **Nothing here has met a real live socket** | Everything is archive, replay and shadow evidence | V5, section 4 |

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
Checks to run before calling any change done: `ruff check .`, `ruff format --check .`,
`mypy backend/ --strict`, `pytest backend/tests/unit -m unit` (**651** at the end of this
session) and the integration tests that touch the worker/prediction pipeline
(`test_live_prediction_pipeline`, `test_race_simulation_serialization`,
`test_strategy_endpoint` = 14; `test_alerts` = 3).

---

## 8. Anchor prompt for resumption — paste into the new session

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
