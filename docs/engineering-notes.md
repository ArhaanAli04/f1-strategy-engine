# Engineering Notes

Eight problems from building this project that were more interesting than they
first looked. Each follows the same shape: what went wrong, why, what changed,
and what the numbers were afterwards.

They're grouped by the kind of mistake, because the lessons are usually more
reusable than the specific bugs:

| # | Story | Kind of problem |
|---|---|---|
| 1 | [A 343-second gap that didn't exist](#1-a-343-second-gap-that-didnt-exist) | Data correctness |
| 2 | [A tyre model that looked better than it was](#2-a-tyre-model-that-looked-better-than-it-was) | ML: leakage |
| 3 | [A safety car model that could never be beaten](#3-a-safety-car-model-that-could-never-be-beaten) | ML: training data |
| 4 | [42x faster by noticing what doesn't change](#4-42x-faster-by-noticing-what-doesnt-change) | Performance |
| 5 | [One WebSocket listener per viewer](#5-one-websocket-listener-per-viewer) | Performance |
| 6 | [Kubernetes killing a healthy container](#6-kubernetes-killing-a-healthy-container) | Infrastructure |
| 7 | [Restarting didn't load the new models](#7-restarting-didnt-load-the-new-models) | Infrastructure |
| 8 | [Two bugs only a full race could find](#8-two-bugs-only-a-full-race-could-find) | Testing |

---

## 1. A 343-second gap that didn't exist

**Symptom.** Replaying the 2026 British Grand Prix, the timing tower showed P1
leading P2 by 2 minutes 55 seconds, and P4 ahead of P5 by 5 minutes 43. Real
gaps at that point were a few seconds.

**Root cause.** Gaps were computed by adding up each driver's lap times and
comparing the totals. The query only summed laps that had a recorded time, and
laps around pit stops or under a safety car often don't. Different drivers
have different numbers of those laps, so their totals weren't comparable. The
P1–P2 gap was almost exactly one missing lap: the leader had three untimed
laps, P2 had two.

**Fix.** Stop reconstructing the race clock and store it instead. FastF1
already provides the session time at the end of every lap, including untimed
ones. A new `session_elapsed_seconds` column captures it at ingestion. A
backfill script (`backend/scripts/backfill_lap_session_time.py`) filled in
169,709 existing rows. All four places that computed race time now use it.

**Result.** Computed gaps match F1's official classification to within 0.18
seconds.

**Lesson.** A sum over rows with a `WHERE value IS NOT NULL` filter quietly
changes meaning when different groups have different numbers of nulls. If a
source system already has the number you're reconstructing, store theirs.

---

## 2. A tyre model that looked better than it was

**Symptom.** The simulator kept saying fresh tyres would make a driver
*slower*. Separately, the tyre models' predicted remaining tyre life was stuck
at the 40-lap maximum for almost every realistic tyre age.

**Root cause.** Two bugs in how the training data was prepared.

1. **The fuel correction had the wrong sign.** Cars get faster as they burn
   fuel, which hides tyre wear, so lap times are corrected for fuel. The
   correction used fuel already *burned* (which grows during the race)
   instead of fuel still *on board* (which shrinks). That roughly doubled the
   fuel effect instead of removing it. After correction, the training target
   got *faster* as tyres aged, for every dry compound.
2. **One input leaked the answer.** A feature called `fuel_adjusted_time` was
   built from the lap time itself, and correlated with the target at +0.91 to
   +0.96. The model leaned on it heavily. At prediction time there is no lap
   time yet, so the feature was filled with a value far outside anything seen
   in training.

**Fix.** One shared function now defines the fuel effect (fuel on board). It
is used for training and for every prediction, so the two can't drift apart
again. The leaked feature was replaced by `fuel_load_penalty`, the fuel effect
alone.

A second problem appeared straight away: the automatic promotion step compared
error scores only, and the honest model scored *worse* than the leaky one. So
each model now records a training-schema version and its feature names, and a
model that isn't comparable to production is promoted regardless of score.

**Result.** The training target now slows as tyres age on SOFT, MEDIUM and HARD
(about +0.02 to +0.04 s per lap, up from negative). Predicted tyre life varies
with age again. In the simulator, the gain from fresh tyres flipped from
negative to positive in every scenario tested. Combined with a pit predictor
relabelled to "pits within 3 laps", the pit probability now climbs over the
laps before a real stop and drops after it, for each driver checked.

**Lesson.** A suspiciously good score is a bug report. And a promotion rule
based only on score will defend a broken model against its own fix.

---

## 3. A safety car model that could never be beaten

**Symptom.** The production safety car model predicted a 0% chance of a safety
car at every circuit. Its recorded error was exactly 0.0, which no properly
trained replacement could ever beat.

**Root cause.** It was trained on the same "valid laps only" data as the tyre
models. But FastF1 marks a lap run behind a safety car as invalid, because its
lap time is unusual. Filtering to valid laps removed the very events the model
was supposed to learn: 3,832 safety-car laps before the filter, 0 after. With
no events left, both the predictions and the actuals were always zero, so the
error was a perfect zero.

**Fix.** Train it on all laps, the same data the pit predictor already used
for the same reason. Give it a training-schema version so the promotion step
knows the old and new models aren't comparable.

**Result.** 286 safety car starts in training and 33 in holdout. 24 circuits
get their own rate. The ranking matches experience: Jeddah and Baku, both
street circuits known for incidents, come out highest, and Spa among the lowest.

**Lesson.** A data filter that suits one model can remove the signal another
one needs. A perfect score is as suspicious as a terrible one.

---

## 4. 42x faster by noticing what doesn't change

**Symptom.** Each per-lap strategy prediction took 28 to 87 seconds in the
background worker, when it should take a second or two.

**Root cause.** Profiling showed 36.6 of 38.2 seconds were spent in the
undercut calculation. It ran 200 simulations, and each simulation called the
tyre model three times, one lap-projection at a time: 600 separate prediction
calls at about 17.5 ms of fixed overhead each. A micro-benchmark confirmed it:
600 tiny calls took 10.5 s, while the same work in one call took 0.067 s.

The key observation was that in each simulation, the tyre model's projection
was *identical*. Only the random noise added on top changed.

**Fix.** Project each stint once (3 calls instead of 600) and draw the 200
noise samples in a single NumPy operation. The maths is unchanged. Running old
and new side by side 30 times at a close 50/50 scenario gave the same average
probability, within normal sampling noise.

**Result.** The undercut calculation went from 36.6 s to 0.87 s. The whole
prediction task went from 38.2 s to 2.1 s.

**Lesson.** Per-call overhead dominates when the calls are tiny. Before
vectorising a loop, check whether most of it is repeating the same work.

---

## 5. One WebSocket listener per viewer

**Symptom.** In a load test with 200 viewers on one race, each viewer received
only about 24 of 50 lap updates, with a median delay of 2.2 seconds. The same
test also made an unrelated endpoint slow: queuing a strategy simulation went
from about 2 s to 12 s.

**Root cause.** Every WebSocket connection ran its own Redis subscription. For
every lap event, every connection separately fetched the same car data from
Redis and built the same message. With 200 viewers, each event cost 200
identical Redis reads. Redis processes commands one at a time, so that queue
also delayed everything else using Redis, including the task broker.

**Fix.** One shared broadcaster per race session (`backend/apis/v1/telemetry.py`).
It is created when the first viewer connects and removed when the last one
leaves. It reads each event once, builds one message, and sends it to every
connected viewer. A failed send to one viewer can't interrupt the others.

**Result.** With 200 connections, all 50 of 50 messages arrived, with a median
delay of 31 ms (p99 63 ms). Simulation queuing returned to about 1.9 s.

**Lesson.** Per-connection work that doesn't depend on the connection is a
fan-out bug waiting for enough users. With a single-threaded dependency like
Redis, it also slows down everything else that shares it.

---

## 6. Kubernetes killing a healthy container

**Symptom.** On a local Kubernetes cluster, backend pods restarted forever.
`kubectl logs` and `kubectl logs --previous` were both completely empty. Worker
pods built from the same code started fine.

**Root cause.** Importing the ML libraries (XGBoost, LightGBM, SHAP, SciPy)
takes a long time on a cold start. A standalone `docker run` measured 88
seconds before the first successful health check, all of it before the web
server even opened its port. The liveness probe allowed 75 seconds, then killed
the container. It never got far enough to log a single line. The worker pods
had no liveness probe, which is why they survived.

**Fix.** A `startupProbe` on the same health endpoint with a 300-second budget
(`infra/helm-chart/templates/backend-deployment.yaml`). Liveness and readiness
checks only begin once startup succeeds.

**Result.** Pods start reliably.

**Lesson.** Empty logs on a crash loop usually mean the process was killed
before it started, not that it failed. A slow start needs a startup probe, not
a bigger liveness delay.

---

## 7. Restarting didn't load the new models

**Symptom.** After retraining and promoting new models, a check of their
behaviour gave exactly the same numbers as before the fix, even after
restarting the services.

**Root cause.** Downloaded models were cached on the container's disk, and the
download step skipped S3 whenever the file already existed. `docker compose
restart` keeps a container's filesystem, so the cache survived every restart.
The documented "restart to pick up a new model" step had never reliably worked.
File dates confirmed it: the host's cached models were over a month old.

**Fix.** Always download from S3 when a process starts
(`backend/workers/prediction_worker.py`, `backend/services/strategy_service.py`).
Each process still keeps models in memory after the first load, so there's no
extra cost per prediction.

**Result.** After clearing the old caches and restarting, every model showed
the expected new version, in both the backend and the worker.

**Lesson.** Know which layer a cache lives in, and what actually clears it. A
cache with no expiry and no version check will eventually serve something
stale.

---

## 8. Two bugs only a full race could find

**Symptom.** None visible. Unit tests passed and the live pipeline looked
fine, but races are rare, so a way to test the whole pipeline between them was
needed.

**What was built.** A "shadow race" (`backend/scripts/shadow_race.py`): it
replays a recorded live F1 feed through the real ingestor, Redis, background
workers and Postgres, under a throwaway race record, then runs 14 automatic
checks. The first short run failed two of them, revealing two real bugs.

1. **A failed prediction could break the ones after it.** A database cleanup
   step sat after the error handling instead of inside a `finally`. So any
   error left a pooled connection tied to an event loop that no longer
   existed, which then broke the next task that got that connection.
2. **Predictions could run before their lap was saved.** The ingestor queues
   "save this lap" and "predict for this lap" back to back on two different
   queues, and nothing guarantees their order. At lap boundaries, when many
   drivers finish at once, a prediction sometimes ran first and failed: 15 of
   233 laps (6.4%) got no prediction. Bug 1 had been hiding this.

**Fix.** The cleanup moved into a `finally` block. The prediction task now
retries (up to 4 times, 3 seconds apart) only when its lap isn't saved yet.
Any other error still fails the task.

**Result.** A full 53-lap race at double speed: 1,052 of 1,052 laps saved and
predicted, 0 worker errors, all 14 checks passing. The retry fired 17 times and
never ran out.

**Lesson.** Two queues give no ordering guarantee. Race conditions like this
only show up at realistic concurrency, so it's worth building a test that
recreates it.
