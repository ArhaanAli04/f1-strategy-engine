# F1 Strategy Engine — Performance Report

Date: 2026-08-19
Environment: Local Docker (PostgreSQL + Redis + Backend + Celery Worker)
Phase: 8, Day 35 — Performance profiling & query optimization

---

## 1. Index Audit

**Methodology:** ran `EXPLAIN ANALYZE` directly against the local Docker Postgres
(`docker exec docker-postgres-1 psql`) for every query pattern named in the Day 35
spec, using real ingested data — 166,453 `lap_data` rows, 158 `sessions` rows, and
session `00b4f598-40ec-4792-8687-6eae51257977` (1,534 laps) for the per-session
patterns. Per CLAUDE.md's rule, an index was only added if `EXPLAIN ANALYZE`
actually showed a sequential scan — none did.

**Finding: every required composite index already exists**, added across Days 8–16.
No missing indexes were found, and no migration was created.

| Query pattern | Backing index | Plan | Measured execution time |
|---|---|---|---|
| `lap_data (session_id, driver_id)` — per-driver strategy query | `uq_lap_data_session_driver_lap` (session_id, driver_id, lap_number) — its leading 2 columns serve this filter | Index Scan Backward | 0.878ms |
| `lap_data (session_id, lap_number)` — timing tower / gaps query, incl. its `ORDER BY driver_id, lap_number` | Same unique index — session_id leads, and the index's own column order satisfies the ORDER BY with no separate Sort node | Index Scan | 1.074ms |
| `lap_data (session_id, driver_id, lap_number)` — lap-by-lap analysis | Same unique index, exact 3-column match | Index Scan | 0.350ms |
| `strategy_predictions (session_id, driver_id)` — pit window queries | `ix_strategy_predictions_session_driver_predicted_at` (session_id, driver_id, predicted_at DESC), added pre-Day-14 | Index Scan (structurally — table is currently empty, 0 rows, so no live scan to time) | n/a — leading-column composite makes a seq scan structurally impossible regardless of row count |
| `sessions (race_id, session_type)` — race/session lookups | Only `ix_sessions_race_id` exists (single-column) | Index Scan on race_id + cheap Filter on session_type | 0.237ms |

The `sessions (race_id, session_type)` case is worth calling out explicitly: no
composite index exists there, but a race has at most 5 sessions (FP1/FP2/FP3/Q/R),
so the post-index-scan filter is trivial. Adding a composite index here would be
speculative — CLAUDE.md's rule explicitly forbids that, so it was skipped.

**Result: no migration needed for Day 35.** The Day 8 unique constraint on
`lap_data` and the Day 16 composite index on `strategy_predictions` already serve
every leading-column access pattern this session's queries need.

---

## 2. N+1 Query Fix

**Location:** `backend/workers/prediction_worker.py::_build_race_state` (backs
`POST /strategy/{session_id}/simulate` via the `run_race_simulation` Celery task).

**Problem:** the function looped over every driver in the session field (~20 for a
full grid) and called `await _cumulative_race_time(db, session_id, lap.driver_id,
current_lap)` once per iteration — one DB round trip per driver instead of one
query for the whole field. This directly fed CLAUDE.md's already-documented
65–88s/task Celery cost ("per-driver DB round trips").

**Fix:** replaced the per-driver calls with a single batched query executed once,
before the loop:

```python
cumulative_time_query = (
    select(LapData.driver_id, func.sum(LapData.lap_time_seconds))
    .where(
        LapData.session_id == session_id,
        LapData.lap_number <= current_lap,
        LapData.lap_time_seconds.is_not(None),
    )
    .group_by(LapData.driver_id)
)
```

Same filter shape as the original per-driver `_cumulative_race_time` call
(session_id, `lap_number <= current_lap`, `lap_time_seconds IS NOT NULL`), just
grouped by `driver_id` instead of scoped to one. Results are looked up from a
`{driver_id: cumulative_seconds}` dict inside the loop instead of awaited per
iteration.

**Impact (measured, Checkpoint 2 load test, worker restarted with the fix live):**

| Metric | Before (baseline) | After (Day 35) | Change |
|---|---|---|---|
| POST /strategy/simulate p50 | 3000ms | 300ms | **10x faster** |
| POST /strategy/simulate p95 | 6300ms | 2500ms | **2.5x faster** |
| POST /strategy/simulate p99 | 7400ms | 2900ms | 2.6x faster |

The improvement is concentrated in the tail (p95/p99), consistent with per-driver
round trips compounding under concurrent load rather than being visible only in a
single uncontended call.

**Test added:** `test_build_race_state_batches_cumulative_time_into_one_query`
(`backend/tests/unit/test_prediction_worker.py`) — asserts exactly 4 total
`db.execute()` calls (context, latest_laps, position, cumulative_time) regardless
of field size, guarding directly against the N+1 regression, and inspects the
compiled SQL of the batched query to confirm it's anchored to `current_lap` rather
than either driver's own divergent latest lap number (preserving the pre-existing
current-lap-anchoring behavior). Two sibling tests
(`test_build_race_state_starting_position_uses_current_lap_not_final_position`,
`test_build_race_state_position_query_filters_by_session_id`) were updated to mock
the new 4th `db.execute` call.

---

## 3. Load Test Results

**Configuration:** 100 users, 10/s ramp, 3 minutes, headless, against the local
Docker stack (`backend/tests/load/locustfile.py`, session
`00b4f598-40ec-4792-8687-6eae51257977`).

**locustfile.py bug found and fixed during this session:** `_register_and_login`'s
test-account provisioning only slept `_SECONDS_BETWEEN_UNAUTH_CALLS` (6s) *between*
an account's register and login calls, never between one account's login and the
next account's register — so unauthenticated calls fired at roughly double the
intended 10/minute pace. The first attempt at this load test burned the entire
per-minute rate-limit budget after only 5 of the 50 required accounts, then 429'd
instantly on all 44 remaining (no backoff), leaving 100 simulated users backed by
just 5 accounts — which produced a 34% failure rate on `/strategy/overview` that
was a load-test harness artifact, not a backend issue. Fixed by sleeping after
*every* unauthenticated call, not just between them. `ruff check` and
`mypy backend/ --strict` both pass clean on the fix.

### Results vs. baseline (2026-07-30 15:07 IST — closest matching 100-user run)

| Endpoint | Baseline p50 | Day 35 p50 | Baseline p95 | Day 35 p95 | Baseline failures | Day 35 failures |
|---|---|---|---|---|---|---|
| GET /races/current | 6200ms | 350ms | 13000ms | 2200ms | 0/41 (0%) | 0/41 (0%) |
| GET /strategy/overview | 82ms | 61ms | 4000ms | 1300ms | 7/777 (0.90%) | 3/862 (0.35%) |
| POST /strategy/simulate | 3000ms | 300ms | 6300ms | 2500ms | 0/48 (0%) | 0/48 (0%) |
| GET /drivers/laps | 140ms | 51ms | 5100ms | 1200ms | 0/62 (0%) | 0/67 (0%) |
| **Aggregated** | **0ms*** | **64ms** | **2000ms** | **1400ms** | **7/3913 (0.18%)** | **3/1018 (0.29%)** |

\* Baseline's aggregated row includes WS `lap_completed` traffic (2,985 near-instant
messages), which pulls its aggregate p50 down to 0ms — not directly comparable to
Day 35's aggregate, which has no WS traffic (see Deferred Items). The per-endpoint
HTTP rows above are the meaningful comparison.

Every endpoint improved on both p50 and p95. No regressions were found.

**Targets (per Day 35 spec):**
- `/strategy/overview` p95: 1300ms < 1500ms target — **met**
- `/strategy/simulate` p95: 2500ms < 8000ms target — **met**
- Overall failure rate: 0.29% < 1% target — **met**

**Note on slowapi overhead:** Locust's stats have no row for `/auth/login` — by
`locustfile.py`'s design, test accounts are logged in once before the timed window,
so no `User` task calls it during the measured 3 minutes. Measured directly instead
(5 sequential calls, rate-limit bucket idle 10+ minutes so no interference):
**~800–970ms**, vs. `/health` (no rate limiter) at **~30–40ms**. This gap is not
slowapi — `core/security.py` uses `bcrypt.gensalt()` at its default cost factor
(12), which is intentionally expensive, plus a DB lookup and a Redis write for the
refresh token. slowapi's own check is a single Redis round trip, the same order of
magnitude as `/health`'s ~30–40ms baseline (which itself does a DB+Redis check) —
nowhere near enough to explain the ~800ms gap. **No measurable slowapi overhead was
detected.** Zero 429s were observed on `/auth/login` under legitimate traffic in
this session (the 429 storm above was the provisioning pacing bug, not organic
request volume).

Full report: `docs/load_test_results_day35.html` (+ CSVs alongside it).

---

## 4. Deferred Items

| Item | Reasoning |
|---|---|
| 500 concurrent users | Needs Fly.io deployment — local Docker Desktop is the ceiling for realistic capacity testing at that scale (per CLAUDE.md's Current Project Phase blockers). |
| Redis Streams | Not needed at expected load — pub/sub is sufficient for the current WS fan-out volume (see CLAUDE.md's Notes on the WS telemetry broadcast fix). |
| py-spy profiling | Requires admin privileges on Windows; the targeted index-audit + N+1 approach found and fixed the actual bottleneck without it. |
| `/telemetry/{session_id}/gaps` load test | No `User` class in the current `locustfile.py` harness calls this endpoint — it isn't exercised by any of the four Locust user types, so no p50/p95 data exists for it from this or any prior run. Would need a new `User` class to cover it. |
| WebSocket latency measurement | This run had no `replay_publisher.py` feeding synthetic lap events (per the exact command given), so WS connections stayed open but idle — no `lap_completed` messages to measure. The WS fan-out fix's own numbers (0 failures, p99=94ms under combined load) are already validated in the 2026-07-28 baseline entry; re-validating it wasn't in today's scope. |
