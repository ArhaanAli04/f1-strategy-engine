# Architecture Decisions

This document explains *why* the stack looks the way it does — what was
chosen, what else was on the table, and what was given up. It describes what
was actually built and, where a decision differs from what was originally
planned (the `TimescaleDB` entry below is the clearest example), says so
directly rather than quietly rewriting history. `CLAUDE.md`'s Architecture
Decisions section is the source of truth this document was written from —
consult it for the full incident-level detail behind any decision here.

## 1. FastAPI over Django/Flask

**Decision:** FastAPI as the backend web framework.

**Alternatives considered:** Django (batteries-included: ORM, admin site,
auth) and Flask (minimal, unopinionated, closer to what FastAPI itself
resembles).

**Reasoning:** Async-native request handling (matters here — the backend
holds a DB connection pool, a Redis client, and dispatches to Celery, all of
which are I/O-bound); Pydantic v2 request/response validation built in;
automatic OpenAPI docs generation (`/docs`) with no separate spec to
maintain; and first-class typing that plugs directly into `mypy --strict`.

**Tradeoffs:** No built-in admin site or ORM the way Django has — SQLAlchemy
was chosen and wired up separately. FastAPI is also more of a toolkit than a
framework: the `apis/v1/` (routes) / `services/` (logic) / `models/` (ORM) /
`schemas/` (Pydantic) separation documented in `CLAUDE.md` is a convention
this project enforces by hand, not something the framework imposes on you.

---

## 2. Celery over FastAPI BackgroundTasks

**Decision:** Celery workers, with Redis Streams as the broker.

**Alternatives considered:** FastAPI's built-in `BackgroundTasks` (runs
in-process, same event loop as the web server).

**Reasoning:** `BackgroundTasks` executes in the same process as the API —
a slow ML inference or a 1000-run Monte Carlo simulation would block that
worker from handling any other request while it runs. Celery tasks run in
separate worker processes, so they can be scaled independently of the API
(API pods are I/O-bound and cheap to run several of; worker pods are
CPU-bound and race-day scaling targets them specifically — see
`docs/runbook.md`'s race day scaling procedure).

**Tradeoffs:** Real operational cost, not just a diagram box. It means a
broker to run and monitor, a separate `Dockerfile.worker` deployment, and
task-serialization to reason about (see `CLAUDE.md`'s note on
`confidence_interval` tuple round-tripping through the JSON result backend).
The Day 18 500-user load test measured a single `--pool=solo` worker at
65-88 seconds per `run_race_simulation` task — a number that only exists
because Celery tasks are independently measurable and scalable in the first
place; a `BackgroundTasks` approach would have simply stalled the API
instead of surfacing a clean "add more workers" fix.

---

## 3. PostgreSQL (Supabase) over TimescaleDB

**Decision:** Plain PostgreSQL, not a TimescaleDB hypertable, for
`lap_data` — despite `TIMESCALE_URL` existing as an env var and the
TimescaleDB extension being installed (`migration b2e4f6a8c0d1`).

**Alternatives considered:** Converting `lap_data` into a TimescaleDB
hypertable, partitioned on `created_at` — the original plan, reflected in
the "PostgreSQL + TimescaleDB extension" line in the tech stack table this
project started from.

**Reasoning — this is the honest version, not the planned one:** Two
separate things are true at once here:

1. **It wasn't needed at the scale actually reached.** The Day 35 index
   audit ran `EXPLAIN ANALYZE` directly against 166,453 real `lap_data` rows
   using composite indexes already in place from Days 8-16 (no new index or
   migration was required):

   | Query pattern | Measured execution time |
   |---|---|
   | Per-driver strategy query (`session_id, driver_id`) | 0.878ms |
   | Timing tower / gaps query (`session_id, lap_number` + `ORDER BY`) | 1.074ms |
   | Lap-by-lap analysis (`session_id, driver_id, lap_number`) | 0.350ms |
   | Session lookup (`race_id, session_type`) | 0.237ms |

   Every measured query pattern returned in under 1.1ms on plain indexed
   Postgres. See `docs/performance-report.md`'s Index Audit section for the
   full methodology.

2. **Adopting it isn't actually free, schema-wise.** TimescaleDB requires
   every unique constraint on a hypertable to include the partition column.
   `sector_times.lap_data_id → lap_data.id` is currently backed by a
   single-column unique constraint on `lap_data.id`, which TimescaleDB
   forbids on a hypertable. Converting would first require adding a
   `lap_data_created_at` column to `sector_times` and changing that FK to a
   composite `(lap_data_id, lap_data_created_at)` reference — a real schema
   migration, not a config flag.

**Tradeoffs:** This is a "not yet, and not clearly worth it" decision, not
a permanent rejection. `docs/runbook.md`'s deferred-schema notes in
`CLAUDE.md` already lay out the exact migration path if/when volume grows
past what composite indexes comfortably serve — revisit before any
production-scale data load, using the audit above as the "is it still fine"
baseline to re-measure against.

---

## 4. Redis pub/sub for WebSocket fan-out

**Decision:** Redis pub/sub, not Redis Streams, to fan lap-completion
events out to WebSocket clients across backend pod replicas.

**Alternatives considered:** Redis Streams (consumer groups, message
persistence/replay); broadcasting directly in-process (ruled out
immediately — doesn't work once there's more than one backend replica,
since only the pod that processed the originating event would know about
it).

**Reasoning:** Pub/sub decouples ingestion (one writer, per lap completion)
from delivery (N pod readers, each pushing to its own connected WebSocket
clients) with the least moving parts. Streams was evaluated and explicitly
**deferred, not rejected** — pub/sub is sufficient at the connection counts
actually tested (`tests/load/ws_load_test.py` verified 200 concurrent
connections, 50/50 messages delivered per connection, p99=63ms, after the
Day 28 fan-out fix collapsed the design to one shared broadcaster per
session instead of one pubsub loop per connection).

**Tradeoffs:** Pub/sub has no message durability — a message published
while a client isn't connected is simply lost, with no replay on
reconnect. That's an acceptable fit for live telemetry (state is
effectively "current gap/lap," not an event log a client needs to catch up
on), but would be a real gap if reconnect-safe delivery became a
requirement. The threshold for revisiting Streams: **north of ~1000
concurrent WebSocket connections**, where a single pub/sub channel's
fan-out cost or Redis's single-threaded command queue becomes the
bottleneck (the same command-queue contention already showed up once, at
far lower load, as the cause of a `/simulate` enqueue-latency regression
under combined load — see `CLAUDE.md`'s WS fan-out redundancy fix). Watch
`f1_active_websocket_connections` in Grafana as the metric to trigger that
reassessment.

---

## 5. Monte Carlo simulation over a deterministic model

**Decision:** Monte Carlo race simulation — 1000 runs per `/simulate`
call, Numba-JIT-compiled for the hot loop.

**Alternatives considered:** A deterministic model that outputs a single
predicted race outcome per driver.

**Reasoning:** F1 strategy is genuinely probabilistic, not just noisy —
safety cars, mechanical failures, weather, and how rivals react to a pit
stop are all real sources of randomness, not measurement error to be
averaged away. A deterministic model would produce one number that looks
precise but encodes false confidence. Running 1000 simulations instead
produces a probability distribution — e.g. a confidence interval on final
position — which is the honest representation of what's actually knowable
about a race that hasn't happened yet.

**Tradeoffs:** 1000 runs per simulation is real compute cost, which is
exactly why this is a Celery task (`run_race_simulation`, on
`prediction_queue`) rather than something computed inline on the request
path, and why the hot loop is Numba-JIT-compiled rather than plain Python.
The output is also a distribution, not a single crisp answer — more honest,
but a harder thing for a UI to present simply than "pit on lap 32." The
`PlanExplanation` fields (`drivers_overtaken`, `pit_cost_seconds`,
`total_recoverable_seconds`, etc.) exist specifically to make that
distribution legible rather than just showing raw percentiles.

---

## 6. XGBoost + LightGBM over neural networks

**Decision:** XGBoost (5 per-compound tire degradation models),
LightGBM (`pit_predictor`), and a Poisson model (`safety_car_model`) — no
neural network anywhere in the ML pipeline.

**Alternatives considered:** A neural network (e.g. a small tabular MLP,
possibly multi-task across tire compounds).

**Reasoning:** This is tabular data with roughly 20 features and a training
corpus on the order of ~163k-166k laps across 7-8 seasons — not the data
volume or modality neural networks need to earn their complexity.
Gradient-boosted trees are also directly interpretable via SHAP's
`TreeExplainer`, which matters for a strategy tool where a driver/engineer
needs to see *why* a model recommends a pit lap, not just the
recommendation. Inference is also required to be fast (<10ms) for
real-time serving — tree ensembles hit that comfortably without a GPU.

**Tradeoffs:** Five independently trained tire-degradation models (one per
compound: SOFT/MEDIUM/HARD/INTER/WET) instead of one shared model — no
cross-compound representation sharing the way a multi-task neural net
embedding could offer, and five separate holdout-MAE promotion decisions to
track per retraining run (see `train_models.py`'s promotion guard). Tree
models also can't consume raw waveform telemetry the way a neural net
could — this project deliberately never ingested 100ms-frequency
Throttle/Brake channels (see `CLAUDE.md`'s Deferred Telemetry Features), so
`driver_style.py` uses four lap/stint-level statistical proxies instead of
the two originally-specced raw-telemetry features.

---

## 7. Tauri over Electron for desktop

**Decision:** Tauri v2 for the desktop app.

**Alternatives considered:** Electron.

**Reasoning:** Tauri uses the OS's native WebView (WebView2 on Windows)
instead of bundling a full Chromium runtime the way Electron does. The
measured difference is not subtle: **~5MB** for the Tauri binary versus a
**~150MB** typical Electron app for equivalent functionality. The same
React codebase from `web/` is reused, not rewritten.

**Tradeoffs:** No monorepo/symlink sharing with `web/` on Windows (symlinks
are unreliable there), so most of `desktop/src/` is a manually-maintained
copy of `web/src/` — kept in sync by hand, file by file, per the table in
`desktop/src/README.md`. That's ongoing maintenance, not a one-time cost:
every future `web/` change to a shared file needs a matching manual desktop
update. The build is also currently validated only on `windows-latest` in
CI — no macOS/Linux Tauri build exists yet — and the resulting binary isn't
code-signed, so it triggers a Windows SmartScreen warning on first run (see
the v1.0.0 release notes).

---

## 8. Expo / React Native for mobile

**Decision:** Expo (managed workflow) + Expo Router for the mobile app.

**Alternatives considered:** Bare React Native (no managed workflow, direct
access to native modules); fully native iOS (Swift) and Android (Kotlin)
apps.

**Reasoning:** Shares TypeScript/React knowledge and a large fraction of
business logic (types, API clients, formatters) with `web/`; one codebase
targets both iOS and Android instead of two separate native codebases; and
Expo's managed workflow avoids owning an Xcode/Android Studio native
project directly, which matters for a project without a dedicated mobile
toolchain already set up.

**Tradeoffs:** Same manual-sync burden as desktop (`mobile/src/README.md`'s
sync table), and further from a straight copy in places — hooks are
hand-written per-platform rather than copied, and several features are
disclosed simplifications rather than full parity with `web/` (e.g.
`TeamLogo` is swatch-only, `TelemetryGauge`'s arcs snap instead of sweep,
Driver Detail is a minimal stub). Push notifications, being mobile-only,
have been verified only via `tsc`/Metro export — there's no physical device
or Android emulator set up yet to test them end to end (no Apple Developer
account, no emulator configured this sprint — see `CLAUDE.md`'s Blockers).
