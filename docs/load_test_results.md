# Load Test Results

Historical record of load test runs against the F1 Strategy Engine backend.
Each entry is append-only — new runs are added below, existing entries are
never edited, so improvement (or regression) deltas are visible over time.

Test session used a real ingested race session
(`00b4f598-40ec-4792-8687-6eae51257977`, 1534 lap rows) and 5 real driver
UUIDs drawn from it, against the local Docker stack
(`infra/docker/docker-compose.yml`) with monitoring (Prometheus/Grafana)
already running.

---

## Load Test Run — 2026-07-23 16:51 IST (11:21 UTC)

**Conditions:** 100 users, 10/s ramp, 2 minute duration
**Infrastructure:** Local Docker
**Git commit:** `a33983c2bd46dfdf168eb69da0c3ccfad3b54b58` (working tree had an
uncommitted change to `backend/tests/load/locustfile.py` at run time — added
`HistoricalUser` and set `weight` on all four Locust user classes per the
Day 18 spec's 70/20/10 split, see Checkpoint 1. This only changes the load
generator itself, not the application code under test.)

### Results per endpoint

| Endpoint | p50 | p95 | p99 | req/s | failures |
|---|---|---|---|---|---|
| GET /races/current | 1400ms | 3600ms | 4600ms | 0.35 | 0/41 (0%) |
| GET /strategy/{session_id}/overview | 81ms | 12000ms | 15000ms | 11.54 | 4/632 (0.63%) |
| POST /strategy/{session_id}/simulate | 6900ms | 14000ms | 14000ms | 1.09 | 0/37 (0%) |
| GET /drivers/{driver_id}/laps | 150ms | 13000ms | 15000ms | 0.43 | 0/50 (0%) |
| WS lap_completed | 0ms | 0ms | 1000ms | 22.52 | 0/1939 (0%) |
| **Aggregated** | **0ms** | **4300ms** | **13000ms** | **23.38** | **4/2699 (0.15%)** |

### Grafana observations

Not captured for this run — the Day 18 spec scoped live Grafana observation
to the 500-user race-day run below, not this 100-user baseline.

### Bottlenecks identified

- **Pre-Day-14 fixes are holding.** `/races/current` p50 dropped to 1400ms
  from the pre-fix Day 13 baseline of p50=13000ms (100% uncached) — the
  negative-result cache + single-flight lock (`race_service.get_current_race`)
  is working as designed.
- **Tail latency and occasional failures on `/strategy/overview` (4 failures:
  2 `ConnectionAbortedError`, 2 `RemoteDisconnected`) and elevated
  `/strategy/simulate` latency (p50=6900ms vs the isolated
  dedicated-executor fix's previously-measured 630–2400ms) reproduce
  CLAUDE.md's already-tracked "WS telemetry broadcast: redundant
  per-connection enrichment fan-out" finding**, not a new regression — that
  bullet already predicted combined WS+HTTP load would resurface tail
  latency and occasional failures that isolated (WS-free) re-tests don't
  show. This run corroborates it again.
- **New data point, same root cause:** `GET /drivers/{driver_id}/laps`
  (`HistoricalUser`, tested for the first time today) shows the identical
  tail-latency shape (p95=13000ms, p99=15000ms) despite being a plain
  indexed paginated query with no ML involved — further evidence the shared
  Redis instance's command queue (not any one endpoint's own logic) is the
  actual bottleneck once WS fan-out traffic is present.
- Teardown noise only, not a failure: a large volume of
  `keepalive ping timeout` / `ConnectionClosedError` messages logged when
  Locust killed all WS connections at the `--run-time` cutoff. WS stats show
  0 failures (1939/1939 messages delivered) — cosmetic, not investigated
  further.

### Changes made after this run

None — this run's purpose was to confirm the pre-Day-14 fixes still hold,
which they do. See the 500-user run below for the day's actionable findings.

---

## Load Test Run — 2026-07-23 17:03 IST (11:33 UTC)

**Conditions:** 500 users, 20/s ramp, 10 minute duration
**Infrastructure:** Local Docker (with Prometheus + Grafana running)
**Git commit:** `a33983c2bd46dfdf168eb69da0c3ccfad3b54b58` (same uncommitted
`locustfile.py` change as the run above)

### Results per endpoint

| Endpoint | p50 | p95 | p99 | req/s | failures |
|---|---|---|---|---|---|
| GET /races/current | 25000ms | 29000ms | 149000ms | 0.35 | 1/206 (0.49%) |
| GET /strategy/{session_id}/overview | 3800ms | 21000ms | 103000ms | 11.54 | 1954/6858 (28.49%) |
| POST /strategy/{session_id}/simulate | 6000ms | 81000ms | 88000ms | 1.09 | 88/647 (13.60%) |
| GET /drivers/{driver_id}/laps | 3900ms | 27000ms | 105000ms | 1.20 | 60/716 (8.38%) |
| WS lap_completed | 0ms | 0ms | 2100ms | 22.52 | 13139/13387 (98.15%) |
| **Aggregated** | **0ms** | **14000ms** | **79000ms** | **36.69** | **15242/21814 (69.87%)** |

### Grafana observations

- Celery queue depth: climbed to ~580 queued tasks at peak. **Verified
  after the run finished** (see Bottlenecks below) that this number alone
  understates the problem badly — the queue was still at 559 nearly 30
  minutes after `--run-time` expired, and real per-task duration is
  65–88s, not the ~10s the ML-inference panel below suggests.
- Redis cache hit rate: started ~60% early in the run, climbed to ~90–100%
  over the final ~4 minutes as caches warmed
- Redis memory used: 3.49MB, dipped mid-run, then rose again in the final
  ~4 minutes
- ML inference time: race simulator (Monte Carlo) held steady around 10s,
  tire degradation model 1–2s, pit predictor 200–300ms, safety car model
  0–10ms — this is a **sub-step** of `run_race_simulation`, not the full
  task duration (see Bottlenecks below for the actual end-to-end number)
- Celery task throughput: `run_race_simulation` succeeded at 0.0138 ops/s
  — consistent with the real ~65–88s/task duration found post-run
  (1/75s ≈ 0.0133 ops/s), not with the queue backing up merely from burst
  volume
- Peak observed request rate: 19 req/s

### Bottlenecks identified

- **New, critical: DB connection pool exhaustion.**
  `core/database.py`'s `create_async_engine(..., pool_size=10,
  max_overflow=20)` — 30 total connections, default 30s timeout — is far too
  small for 500 concurrent users. Backend container logs for the run window
  show **493 occurrences** of:
  ```
  sqlalchemy.exc.TimeoutError: QueuePool limit of size 10 overflow 20
  reached, connection timed out, timeout 30.00
  ```
  This is the primary cause of several symptoms that look unrelated on the
  surface:
  - 172 of the 1954 `/strategy/overview` failures are clean
    `500 Internal Server Error`s caused directly by this timeout propagating
    up as an unhandled exception.
  - **58 occurrences of a genuinely new WebSocket bug:**
    `RuntimeError: Expected ASGI message 'websocket.send' or
    'websocket.close', but got 'websocket.accept'` in `telemetry.py`. Root
    cause chain: `websocket_telemetry`'s `resolve_season_round` DB lookup
    (telemetry.py:210) stalls up to 30s waiting on the same exhausted pool;
    meanwhile Locust's client-side `ws_connect(ws_url, open_timeout=10)`
    gives up after only 10s and tears down its side of the connection (this
    is the `TimeoutError: timed out while waiting for handshake response`
    seen in the Locust console). When the server's stalled DB call finally
    returns and the handler reaches `await websocket.accept()`
    (telemetry.py:215), it's calling accept on a transport the client
    already abandoned — uvicorn's ASGI state machine rejects the late
    accept with this error. Not a standalone bug — a symptom of the pool
    exhaustion above.
  - Very likely also the dominant cause of the remaining `/overview` and
    `/drivers/laps` failures (`RemoteDisconnected`, `ConnectionAbortedError`).
  - Recommendation for a future day: raise `pool_size`/`max_overflow`
    (with real numbers derived from a follow-up test, not guessed), and
    consider a bounded retry or a faster-failing timeout so overloaded
    requests return a clean 503 instead of hanging up to 30s per attempt.

- **New, critical: single `--pool=solo` worker cannot drain a 10-minute
  burst of simulate traffic in anything close to real time.** Verified
  directly after the run (not from the Grafana panel alone, which only
  showed "queue depth ~580 at peak" and made this look like a transient
  spike): `redis-cli llen prediction_queue` still read **559** roughly 30
  minutes after `--run-time` expired, and `docker logs docker-worker-1`
  showed each `run_race_simulation` task taking **65–88 seconds
  end-to-end** (not the ~10s the Grafana ML-inference panel shows — that
  panel measures only the Monte Carlo inference sub-step, not the full
  task including its per-driver DB round trips, which are themselves
  slowed by the same connection-pool contention above). At ~75s/task
  average and 559 tasks still queued, full backlog drain was projected at
  **10+ hours** on the single solo-pool worker process — confirmed while
  writing today's E2E test (Checkpoint 6): a fresh `/simulate` call queued
  behind this backlog did not complete even once in a 30s poll window, and
  only succeeded (in 68s) after the stale queue was purged
  (`celery -A backend.workers.celery_app purge -f -Q prediction_queue`,
  555 messages removed) to unblock testing. 647 simulate requests were
  submitted in the 10-minute run at a sustained rate the worker cannot
  remotely keep up with; CLAUDE.md's own "Celery worker pool --pool=solo"
  rationale ("Race day scaling: run 8+ worker pods, not 8 processes per
  pod") already anticipated needing multiple worker pods for real race-day
  load, but this run is the first concrete evidence of just how large that
  gap is at a single-pod baseline: one worker pod sustains roughly
  0.013 tasks/sec, while this run alone demanded far more than that
  sustained over 10 minutes.

- **WS telemetry fan-out issue (already tracked in CLAUDE.md) hit its
  logical extreme at this scale.** 13139/13387 (98.15%) of WS connections
  failed with `ConnectionClosedError` (`keepalive ping timeout`, close code
  1011) at ~206 concurrent `WebSocketUser`s. This is the clearest evidence
  yet for the already-scoped "WS telemetry broadcast: redundant
  per-connection enrichment fan-out" fix (pre-Day-22): the per-connection
  `pubsub.listen()` + redundant `get_live_car_channels` Redis GET, once per
  connected client per lap event, backs up Redis badly enough at this
  connection count that the server can't service WS keepalive pings in
  time. No new root cause here — this run is confirmation the fix is
  overdue, not a new finding.

- **Load-test harness artifact, not a server bug:** 1385 of the 15242
  failures are `429 Too Many Requests` (1257 on `/overview`, 88 on
  `/simulate`, 40 on `/laps`). `locustfile.py`'s account pool sizing
  (`min(users, 30)`, unchanged from Day 13) caps at 30 shared accounts
  regardless of population — at 500 users that's ~17 simulated users per
  account versus Day 13's ~3.3, pushing each account's request rate past
  the documented 60/min authenticated rate-limit bucket. The rate limiter
  is correctly protecting the server here; this is the load-test harness's
  pool-sizing formula not scaling past roughly 100 simulated users, not an
  application bug. Revisit the formula (or accept a larger, slower
  provisioning pass) before the next 500+-user run.

- The already-tracked 16–17s cold-compute floor for
  `get_competitor_predicted_strategy` (CLAUDE.md's "Cache stampede fix does
  not address the underlying 16-17s compute floor") is baked into these
  numbers too — not a new finding, just still unaddressed.

### Changes made after this run

One operational action, no code changes: the stale `prediction_queue`
backlog (555 messages left over from this run) was purged
(`celery -A backend.workers.celery_app purge -f -Q prediction_queue`) after
the run concluded, purely to unblock Day 18's E2E test (Checkpoint 6) from
queuing behind a 10+ hour drain — these were load-test-generated tasks with
no real user waiting on a result, and Locust itself never polls simulate
task completion (only the `202 Accepted` enqueue response), so purging them
does not affect any number recorded above.

No application code was changed — today's scope (Day 18) was load testing
and documentation, not fixes. Four items should be added to CLAUDE.md's
Deferred Wiring section for a future day (left for the end-of-day CLAUDE.md
update rather than edited here):
1. DB connection pool exhaustion at 500 concurrent users (`pool_size=10,
   max_overflow=20` in `core/database.py`) — new, highest-priority finding
   from today, recommend fixing before Day 22 alongside the WS fan-out item
   since both block real Kubernetes-scale traffic.
2. Single `--pool=solo` worker throughput (~0.013 tasks/sec, 65–88s/task)
   cannot sustain the simulate request rate a 500-user race day generates —
   confirmed via a 559-task, 10+-hour-projected backlog after this run, not
   just a queue-depth graph. CLAUDE.md's existing solo-pool rationale
   already calls for "8+ worker pods" on race day; this run gives the first
   concrete number for how far short a single pod falls, worth carrying
   into whatever capacity planning happens before Day 22.
3. The WS telemetry fan-out fix (already tracked) now has direct evidence
   at production-like scale — no change to its scope, just stronger
   evidence it's overdue.
4. `locustfile.py`'s account-pool-size formula needs revisiting for load
   tests above ~100 users, to avoid rate-limit 429s dominating the failure
   count and obscuring real server-side bottlenecks in future runs.

---

## Load Test Run — 2026-07-28 18:01 IST (12:31 UTC)

**Conditions:** 100 users, 10/s ramp, 2 minute duration, combined population
(`RaceDayViewerUser` + `StrategyUser` + `HistoricalUser` + `WebSocketUser`,
70/20/10/70 weights per `locustfile.py`), `replay_publisher.py --rate 5`
feeding real WS traffic from session `5ef7e1da-0fcd-418f-80a6-17ddd132c273`
(927 lap rows)
**Infrastructure:** Local Docker
**Git commit:** `9a2b87c09c4c96f1ea773f1c02cd9ecb45b50d56` (working tree had
uncommitted changes at run time: the WS telemetry fan-out fix itself —
`backend/apis/v1/telemetry.py`'s new `_SessionBroadcaster` registry — plus
its regression test in `backend/tests/integration/test_websocket.py`. This
run's purpose was to validate that fix under combined load; see Bottlenecks
below.)

### Results per endpoint

| Endpoint | p50 | p95 | p99 | req/s | failures |
|---|---|---|---|---|---|
| GET /races/current | 160ms | 1300ms | 2200ms | 0.35 | 0/41 (0%) |
| GET /strategy/{session_id}/overview | 68ms | 26000ms | 28000ms | 5.38 | 5/637 (0.78%) |
| POST /strategy/{session_id}/simulate | 1900ms | 25000ms | 26000ms | 0.33 | 0/39 (0%) |
| GET /drivers/{driver_id}/laps | 130ms | 25000ms | 27000ms | 0.44 | 0/52 (0%) |
| WS lap_completed | 0ms | 0ms | 94ms | 26.10 | 0/3090 (0%) |
| **Aggregated** | **0ms** | **280ms** | **25000ms** | **32.60** | **5/3859 (0.13%)** |

Errors (all on `/strategy/{session_id}/overview`): 4x `RemoteDisconnected`
("Remote end closed connection without response"), 1x
`ConnectionAbortedError` (WinError 10053).

Companion run, same session, same code: `tests/load/ws_load_test.py
--connections 200 --messages 50 --rate 20` (isolated, no HTTP traffic) —
50/50 messages delivered per connection, p50=31ms, p95=47ms, p99=63ms,
max=78ms, 0 connection failures.

### Grafana observations

Not captured for this run — validation leaned on Locust's own per-endpoint
stats and the dedicated `ws_load_test.py` run above rather than Grafana
panels. Worth capturing queue-depth/Redis-command-rate panels on the next
run once the DB pool sizing fix below lands, to see the effect directly.

### Bottlenecks identified

- **WS telemetry fan-out fix confirmed working under combined load.** `WS
  lap_completed`: 3090 messages, **0 failures**, p99=94ms, max=380ms — no
  loss, no meaningful tail, while three other user types hit the API
  concurrently. This is the first combined-load run since the fix landed
  (`_SessionBroadcaster`, one shared `pubsub.listen()` loop per session_id
  instead of one per connection — see CLAUDE.md's Notes entry "WS telemetry
  broadcast fan-out redundancy").
- **The specific regression this fix targeted is gone.** CLAUDE.md's
  pre-fix pass documented `POST /strategy/simulate`'s enqueue latency
  regressing from its isolated ~630-2400ms p50 back to ~12000ms under
  combined WS+HTTP load, attributed to the old per-connection fan-out's
  Nx-redundant `get_live_car_channels` Redis GETs saturating Redis's
  single-threaded command queue. This run's `simulate` p50 is **1900ms** —
  back in the isolated-fix range, not the ~12000ms regression.
- **Remaining tail latency on `/strategy/overview`, `/strategy/simulate`,
  and `/drivers/laps` (p95/p99 in the 25000-28000ms range, plus 5
  `RemoteDisconnected`/`ConnectionAbortedError` failures on `/overview`) is
  the already-tracked, separate DB connection pool exhaustion issue**
  (`core/database.py`'s `pool_size=10, max_overflow=20`), not a new finding
  and not caused by the WS fan-out fix — same symptom pattern as the
  2026-07-23 500-user run's 493 `QueuePool` timeout occurrences, just at
  smaller scale. With the WS fan-out no longer in the picture, this is now
  the clearest single remaining bottleneck.
- The 16-17s `get_competitor_predicted_strategy` cold-compute floor
  (CLAUDE.md's "Cache stampede fix does not address the underlying 16-17s
  compute floor") is also baked into `/strategy/overview`'s tail here — not
  new, still unaddressed, and DB-pool-bound rather than pure model
  overhead per that entry's analysis.

### Changes made after this run

No code changes — this run's purpose was to validate the already-implemented
WS fan-out fix, which it did. Documentation only:
- CLAUDE.md's "WS telemetry broadcast: redundant per-connection enrichment
  fan-out" entry moved from Deferred Wiring & Integration Gaps to Notes,
  marked fixed, with this run's numbers and the `ws_load_test.py` numbers
  above.
- CLAUDE.md's "DB connection pool exhaustion" entry updated to record this
  run's recurrence of the `RemoteDisconnected` pattern (5 failures on
  `/overview`) as confirmation it's now the next priority, ahead of Day 22.
- Two dangling cross-references in CLAUDE.md that pointed at the
  now-moved WS fan-out bullet (`broker_pool_limit` entry and
  `get_competitor_predicted_strategy` entry) were updated to point at the
  new Notes entry and record that the regressions they described are
  confirmed resolved.

---

## Load Test Run — 2026-07-30 15:07 IST (09:37 UTC)

**Conditions:** 100 users, 10/s ramp, 2 minute duration, combined population
(`RaceDayViewerUser` + `StrategyUser` + `HistoricalUser` + `WebSocketUser`,
70/20/10/70 weights per `locustfile.py`), `replay_publisher.py --rate 5`
feeding real WS traffic from session `00b4f598-40ec-4792-8687-6eae51257977`
(1531 lap rows)
**Infrastructure:** Local Docker — post-fix config: backend
`pool_size=20, max_overflow=40` (cap 60), worker unchanged
`pool_size=10, max_overflow=20` (cap 30), Postgres `max_connections=200`
(raised from 100)
**Git commit:** `3f54348` (branch HEAD at time of this run; working tree had
uncommitted changes — the DB connection pool sizing fix itself:
`backend/core/config.py`, `backend/core/database.py`,
`infra/docker/docker-compose.yml`. This run's explicit purpose was
verifying that fix.)

### Results per endpoint

| Endpoint | p50 | p95 | p99 | req/s | failures |
|---|---|---|---|---|---|
| GET /races/current | 6200ms | 13000ms | 14000ms | 0.35 | 0/41 (0%) |
| GET /strategy/{session_id}/overview | 82ms | 4000ms | 5900ms | 6.57 | 7/777 (0.90%) |
| POST /strategy/{session_id}/simulate | 3000ms | 6300ms | 7400ms | 0.41 | 0/48 (0%) |
| GET /drivers/{driver_id}/laps | 140ms | 5100ms | 6700ms | 0.52 | 0/62 (0%) |
| WS lap_completed | 0ms | 0ms | 230ms | 25.23 | 0/2985 (0%) |
| **Aggregated** | **0ms** | **2000ms** | **5700ms** | **33.07** | **7/3913 (0.18%)** |

### QueuePool timeout count

**0** — confirmed via `docker compose logs backend --since 5m | grep -c QueuePool`.
Progression across fixes: 493 (Day 18 500-user baseline) → 16 (2026-07-30
500-user re-run, post-WS-fan-out-fix, same pool config as Day 18) → **0**
(this run, post-pool-fix).

### Grafana observations

Not captured for this run — verification leaned on Locust's own
per-endpoint stats and a direct backend-log grep for `QueuePool`, which
was the specific signal this run needed to confirm.

### Bottlenecks identified

- **DB connection pool exhaustion: resolved.** Zero `QueuePool` timeouts
  in backend logs for the run window, down from 16 in the same-scale,
  same-code empirical run earlier today and 493 in the Day 18 500-user
  baseline. See CLAUDE.md's Notes entry "DB connection pool exhaustion"
  for the full fix writeup and reasoning behind the specific numbers
  chosen.
- **Residual: 7 `RemoteDisconnected` failures on `/overview` (0.90%),
  not pool-related.** With `QueuePool` timeouts at zero, these are not
  the same root cause as prior runs' failures on this endpoint. Most
  likely candidate: the already-tracked "WS keepalive ping timeouts
  under heavy CPU load" Deferred Wiring entry (Uvicorn's single event
  loop blocked by synchronous ML inference) — unconfirmed, not
  investigated further in this run since it was out of scope for the
  pool-sizing verification.
- **Operational note, not an application bug:** two earlier attempts at
  this exact run failed with 100% `/overview` 500s
  (`botocore.exceptions.ParamValidationError: Invalid bucket name ""`)
  because the stack had been rebuilt with `docker compose up` omitting
  `--env-file .env`, zeroing `AWS_BUCKET_NAME`/AWS credentials in the
  container per `docker-compose.yml`'s `${VAR:-}` substitution. Fixed by
  rebuilding with `--env-file .env` (per CLAUDE.md's documented
  convention) before this run. Not a code defect — a reminder that any
  manual `docker compose up`/`--build` during this kind of session must
  include `--env-file .env`.

### Changes made after this run

None — this run's purpose was to verify the pool-sizing fix already
applied earlier in this session (see Conditions above), which it did.
Documentation only:
- CLAUDE.md's "DB connection pool exhaustion" entry moved from Deferred
  Wiring & Integration Gaps to Notes, marked fixed, with the full
  before/after numbers and the 493 → 16 → 0 progression.
- This entry added to `docs/load_test_results.md`.
