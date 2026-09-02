# F1 Real-Time Strategy & Telemetry Engine
# CLAUDE.md — Project Memory & Conventions

> Read this entire file before touching any code in any session.
> This is the single source of truth for how this project is built.

---

## What This System Is

A full-stack, production-grade F1 race strategy platform that ingests live telemetry
from the FastF1 API, runs ML models to predict optimal pit windows and undercut
probabilities in real time, and delivers results to a React web app, Tauri desktop
app, and React Native mobile app — all with proper CI/CD, Docker/Kubernetes,
Alembic migrations, Redis caching, Prometheus monitoring, and a full test suite.

---

## Project Structure (memorise this)

```
f1-strategy-engine/
├── backend/
│   ├── apis/v1/          ← route handlers ONLY, no logic
│   ├── services/         ← ALL business logic lives here
│   │   └── ml/           ← ML sub-services
│   ├── models/           ← SQLAlchemy ORM table definitions
│   ├── schemas/          ← Pydantic v2 request/response contracts
│   ├── scripts/          ← ingestion, seeding, training scripts
│   ├── migrations/       ← Alembic migrations (versions/ subfolder)
│   ├── workers/          ← Celery task definitions
│   ├── core/             ← config, db session, redis, security, exceptions
│   └── tests/
│       ├── unit/         ← @pytest.mark.unit — no DB, no network
│       ├── integration/  ← @pytest.mark.integration — testcontainers
│       ├── e2e/          ← @pytest.mark.e2e — Playwright full stack
│       └── load/         ← Locust load test scripts
├── infra/
│   ├── docker/           ← Dockerfiles + docker-compose files
│   ├── k8s/              ← Kubernetes manifests + Helm chart
│   └── monitoring/       ← Prometheus, Grafana, Alertmanager configs
├── web/                  ← React + Vite + TypeScript
├── desktop/              ← Tauri + React
├── mobile/               ← React Native + Expo
└── .github/workflows/    ← CI/CD pipeline YAMLs
```

---

## Tech Stack (never deviate from these)

| Layer            | Technology                                              |
|------------------|---------------------------------------------------------|
| Backend API      | FastAPI, SQLAlchemy 2.0 async, Pydantic v2              |
| Task Queue       | Celery + Redis Streams as broker                        |
| ML               | XGBoost, LightGBM, scikit-learn, NumPy, SciPy, Numba   |
| Explainability   | SHAP (TreeExplainer for XGBoost/LightGBM)               |
| Data Ingestion   | FastF1, httpx (async), websockets, APScheduler          |
| Primary DB       | PostgreSQL (Supabase)                                   |
| Cache            | Redis (Upstash cloud), in-memory fallback on Redis down |
| Migrations       | Alembic (async engine, autogenerate)                    |
| Tests            | pytest, testcontainers, Playwright, Locust              |
| Containers       | Docker (multi-stage), Kubernetes, Helm                  |
| CI/CD            | GitHub Actions                                          |
| Monitoring       | Prometheus, Grafana, Sentry, Alertmanager               |
| Web              | React + Vite, TanStack Query, Zustand, Recharts         |
| Desktop          | Tauri + React (native system tray, always-on-top)       |
| Mobile           | React Native + Expo Router, expo-notifications          |
| Model Storage    | AWS S3 (versioned, encrypted)                           |

---

## Non-Negotiable Coding Rules

### Python / Backend
- ALL database queries must use async SQLAlchemy (AsyncSession). Never use
  synchronous Session or blocking calls inside async functions.
- NEVER write raw SQL with f-strings or string concatenation. Always use
  SQLAlchemy ORM or parameterised text() with bound params.
- ALL service methods must check Redis cache before computing:
  cache hit → return immediately; cache miss → compute → write to Redis → return.
- Route handlers (apis/v1/) contain ZERO business logic. They call one service
  method, validate the response schema, and return. That is all.
- ALL models import Base from core/database.py — never declare their own Base.
- ALL schemas use model_config = ConfigDict(from_attributes=True) for ORM compat.
- NEVER import directly from services in other services. All cross-service
  communication goes through the dependency injection system or Celery tasks.
- Every new public function in services/ must have a docstring with Args and Returns.
- Use Python type hints everywhere. mypy --strict must pass with zero errors.

### Database / Migrations
- NEVER modify an existing migration file. Always create a new revision.
- Every DB schema change = one Alembic revision. Run autogenerate first,
  review the output carefully, then run upgrade head.
- The migration naming convention: YYYYMMDD_short_description
  e.g. 20240315_add_confidence_score_to_strategy_prediction
- After every migration: verify with `SELECT table_name FROM information_schema.tables`
  that expected tables exist and columns are correct.
- TimescaleDB hypertables: lap_data uses created_at as the time dimension.
  Never query lap_data without a time range filter in production queries.

### Testing Rules
- Every new service method needs a corresponding unit test in tests/unit/.
- Every new API endpoint needs a corresponding integration test in tests/integration/.
- Unit tests NEVER touch a real database or Redis. Use mock_db_session and
  fakeredis fixtures from conftest.py.
- Integration tests use testcontainers (real Postgres + real Redis spun up fresh).
- Test file naming: test_{module_name}.py mirrors the source file it tests.
- All tests must pass before committing. Run `make test` before every commit.
- Target: > 80% line coverage on backend/services/, 100% on core/security.py.

### Git / Workflow
- Branch naming: feature/day-XX-description, bugfix/short-description
- Commit message format: "Day X: [what was built]"
  e.g. "Day 3: SQLAlchemy models + first Alembic migration"
- Never commit directly to main. Always PR from feature branch.
- Never commit: .env files, *.pkl model files, __pycache__, .venv, models/ directory.
- The .gitignore must cover all of the above before first commit.

### Secrets
- NEVER hardcode any secret, token, password, or connection string in any file.
- All secrets come from environment variables via core/config.py (pydantic-settings).
- In production: secrets live in Kubernetes Sealed Secrets. Never in plaintext YAML.

---

## Key Commands (use these, not raw commands)

```bash
make install      # fresh machine setup: install deps + pre-commit hooks
make dev          # docker compose up — starts postgres, redis, backend, worker
make test         # full pytest suite (unit + integration + e2e)
make test-unit    # pytest tests/unit/ -m unit -v
make test-int     # pytest tests/integration/ -m integration -v
make test-e2e     # pytest tests/e2e/ -m e2e -v
make lint         # ruff check . && ruff format --check . && mypy backend/ --strict
make migrate      # alembic upgrade head (with correct env vars loaded)
make new-migration MSG="description"  # alembic revision --autogenerate -m MSG
make train        # python scripts/train_models.py
make seed         # python scripts/seed_circuits.py
make ingest SEASON=2023  # python scripts/ingest_historical.py --season 2023
```

---

## Environment Variables (all required)

```
DATABASE_URL          postgresql+asyncpg://user:pass@host:5432/f1db
TIMESCALE_URL         postgresql+asyncpg://user:pass@host:5432/f1db  (same DB, TimescaleDB ext)
REDIS_URL             redis://default:pass@host:6379
SECRET_KEY            [256-bit random string — never commit]
FASTF1_CACHE_DIR      /tmp/fastf1_cache
SENTRY_DSN            [from Sentry project settings]
AWS_BUCKET_NAME       f1-strategy-models
AWS_ACCESS_KEY_ID     [from AWS IAM]
AWS_SECRET_ACCESS_KEY [from AWS IAM]
AWS_REGION            ap-south-1
FCM_SERVER_KEY        [from Firebase Console]
ENVIRONMENT           development | staging | production
```

---

## Architecture Decisions (understand these before proposing alternatives)

**Backend cold start is ~88s (xgboost/lightgbm/shap imports) — Kubernetes
startupProbe required:**
Kubernetes startupProbe set to 30×10s=300s budget in
`infra/helm-chart/templates/backend-deployment.yaml` — do not reduce this
without re-timing the cold start. Standard livenessProbe budget of 75s is
insufficient and causes CrashLoopBackOff. Confirmed empirically Day 22 via
a standalone `docker run` (no other pods competing for CPU): the container
took 88s from start to first successful `/health` response, all spent
before uvicorn ever binds the port (importing xgboost/lightgbm/shap/numpy/
scipy). The original `livenessProbe` (`initialDelaySeconds: 15,
periodSeconds: 20, failureThreshold: 3` = 75s budget) killed the container
via SIGKILL every time, before it ever logged a single line — `kubectl
logs` and `--previous` were both completely empty, which is the tell for
this class of bug (not a DB/Redis connectivity failure, not an app crash).
The worker Deployment has no `livenessProbe` at all, which is exactly why
worker pods reached `1/1 Ready` on the same node while backend pods
crash-looped forever — slow startup there only delayed readiness, nothing
killed the container mid-import. Fix: `startupProbe` (same `/health`
endpoint, `failureThreshold: 30, periodSeconds: 10`) gates liveness/
readiness until the app is actually up — the standard Kubernetes pattern
for slow-starting containers, rather than inflating livenessProbe's own
initialDelaySeconds.

**Monte Carlo simulator fixes (feature/monte-carlo-fix):**
1. cumulative_race_time_seconds anchored to current_lap 
   for all drivers (not each driver's own latest DB lap)
2. starting_position anchored to current_lap + scoped 
   to session_id (was doing cross-session join)
3. PlanExplanation added to SimulatedRaceOutcome — 
   drivers_overtaken, pit_cost_seconds, remaining_laps, 
   fresh_tyre_gain_per_lap, total_recoverable_seconds
Validated: STR lap 55 → -8/-9, NOR → -1, OCO → -10"

**Driver style metrics fix (feature/style-radar-improvements):**
sector_time_variance and lap_time_consistency were previously computed
across all circuits and session types in a season, conflating
cross-circuit pace differences with driver skill. Fixed in driver_style.py
to use per-circuit z-scoring against peers (race sessions only,
is_valid=True), matching tyre_management_index's existing approach. All 4
style metrics are now z-scores on a comparable scale (~-2 to +2). Frontend
normalization bounds updated in StyleRadar.tsx (both web and desktop).

**Docker Desktop Kubernetes shares its image store directly — no `kind
load` needed:**
Despite Docker Desktop's Kubernetes node (`desktop-control-plane`) running
on a kind-style provisioner internally, it is not a cluster the `kind` CLI
manages (confirmed Day 22: `kind` isn't even installed, and `kind load
docker-image` only works on clusters the kind CLI itself created). The
node uses containerd directly and shares that image store with the Docker
daemon, so a plain `docker build -t f1-backend:local .` is immediately
visible to the cluster — confirmed via `kubectl describe pod` showing
`Successfully pulled image "f1-backend:local" in 2.166s` (a local
containerd-store hit, not a network pull). `values.local.yaml` sets
`imagePullPolicy: IfNotPresent` accordingly.

**Correction (Day 24): the above only holds for a tag's *first* build —
rebuilding an already-cached tag does not get picked up.** Under
`IfNotPresent`, once containerd has resolved `f1-backend:local`/
`f1-worker:local` to a real image at least once, it treats that tag as
"already present" and never re-resolves it, even after `docker build -t
f1-backend:local .` retags the same name to genuinely new content on the
host. `docker images` on the host correctly shows the new digest — the
node just doesn't look again. Confirmed Day 24: after rebuilding
`f1-worker:local` to pick up a real code fix, `kubectl get pod ... -o
jsonpath='{.status.containerStatuses[0].imageID}'` still showed the
pre-rebuild digest on a pod created *after* the rebuild, and the pod kept
failing with the pre-fix bug — restarting/recreating the pod did not help,
since pod recreation still resolves the same stale tag. Workaround used:
retag to a build-specific name (`docker tag f1-backend:local
f1-backend:day24fix`, same for worker) and point the Helm release at it
(`helm upgrade ... --set backend.image.tag=day24fix --set
worker.image.tag=day24fix`) — a name the node has never cached forces a
real re-resolution. The alternative, `imagePullPolicy: Always`, forces a
fresh resolution on every pod restart (always current, no manual retagging
needed) but is slower per pod start and was not adopted here since
`values.local.yaml` already documents why `Always` is wrong for a
local-only tag never pushed to a registry — a real fix would need a
per-build unique tag (e.g. a git SHA or timestamp) wired into the local
dev workflow, not just a one-off manual retag.

**Local Kubernetes deployment reaches docker-compose's Postgres/Redis via
`host.docker.internal`, not its own DB/cache:**
`infra/helm-chart/` deliberately does not template Postgres/Redis — the
Day 22 local deploy runs *alongside* docker-compose (see Deployment
Strategy), not instead of it. `infra/k8s/create-secrets.sh
--rewrite-localhost` rewrites `.env`'s `DATABASE_URL`/`TIMESCALE_URL`/
`REDIS_URL` from `localhost` to `host.docker.internal` before creating the
cluster Secret, since the K8s pods are not on docker-compose's Docker
network but can reach the host's exposed ports. `worker-scaledobject.yaml`
follows the same pattern for KEDA's Redis trigger address. A real cloud
cluster (Supabase/Upstash) needs no such rewriting — real hostnames go in
directly.

**`worker-scaledobject.yaml` / `race-weekend-cronjob.yaml` carry
local-validation overrides, not their final production shape:**
Both files were written Day 21 targeting `namespace: production`; Day 22
changed them to `namespace: local` (plus `f1-backend:local` image and
Redis address) so they could actually be applied and verified against the
local Docker Desktop cluster. Each file's own header comment states what
production must restore: `worker-scaledobject.yaml` needs its
`TriggerAuthentication`/Redis-password `authenticationRef` added back
(dropped locally since docker-compose's Redis has no password);
`race-weekend-cronjob.yaml` needs its ECR image reference restored and
still requires `backend/scripts/prescale_for_session.py` to be implemented
— applying it today only proves the CronJob/RBAC objects register
correctly, not that a scheduled run succeeds.

**WebSocket pubsub cleanup — fire-and-forget aclose():**
redis-py 6.4.0's `PubSub.aclose()` hangs indefinitely on disconnect 
when `forward_task` is cancelled mid-read inside `conn.read_response()` 
— the connection is left in a non-cancellable state. Even 
`asyncio.wait_for(..., timeout=2.0)` cannot rescue this because the 
inner cancellation itself never completes (it waits for the stuck 
connection to acknowledge the cancel). Fixed in `telemetry.py` by 
scheduling `pubsub.aclose()` as a detached `asyncio.create_task()` 
with a logged done-callback, so `.dec()` and the route's return are 
never blocked by a wedged connection. Without this fix, every WS 
disconnect leaks one Redis connection from the pool permanently — 
critical to preserve given pool exhaustion was already a documented 
load-test finding.

**Why Celery + Redis for predictions, not FastAPI BackgroundTasks?**
BackgroundTasks run in the same process as the web server. A slow ML inference
(500ms+) would block that worker from handling other requests. Celery tasks run
in completely separate worker processes and can be scaled independently on race day.

**Why TimescaleDB for telemetry, not plain Postgres?**
lap_data will have 17,000+ rows per season. Time-series queries (e.g. "get last
5 laps for driver X in session Y") are 10–100x faster with a hypertable and
time_bucket() than with a regular indexed table at this volume.

**Why Redis Streams for pub/sub, not WebSocket broadcast directly?**
Multiple API pod replicas need to all receive the same lap completion event and
push it to their connected WebSocket clients. Redis Streams decouples ingestion
(one writer) from delivery (N pod readers). Without it, only the pod that
processed the Celery task would know about the new lap.

**Why separate Dockerfile.backend and Dockerfile.worker?**
Same codebase, different process entrypoints. This allows you to scale worker
pods (ML-heavy, CPU-intensive) independently from API pods (IO-heavy, low CPU).
On race day you might run 2 API pods and 8 worker pods.

**Why Monte Carlo for race simulation, not a deterministic model?**
F1 strategy is inherently probabilistic. Safety cars, reliability failures, rain,
and opponent reactions are random. A deterministic model gives false confidence.
Monte Carlo with 1000 simulations returns a probability distribution over outcomes
which is the honest representation of uncertainty.

**Why is lap_data not yet a TimescaleDB hypertable?**
TimescaleDB requires every unique constraint on a hypertable to include the partition
column (created_at). The current schema has `sector_times.lap_data_id → lap_data.id`
backed by a single-column unique constraint on `lap_data.id` — which TimescaleDB
forbids. A future migration (before any production data load) must first add a
`lap_data_created_at TIMESTAMPTZ NOT NULL` column to `sector_times` and change the
FK to a composite reference: `(lap_data_id, lap_data_created_at) → lap_data(id, created_at)`.
Once that schema change lands, a follow-up migration can call `create_hypertable`.
Until then, `lap_data` is a regular indexed Postgres table. The TimescaleDB extension
is already installed (migration b2e4f6a8c0d1).

**Celery worker pool — `--pool=solo`:**
Single process, no forking. Rationale: scaling strategy is multiple worker 
pods, not intra-process forking — solo pool enables prometheus_client metrics 
(start_http_server, counters, histograms) to work correctly without multiprocess 
mode complexity. Race day scaling: run 8+ worker pods, not 8 processes per pod.
---

## ML Model Registry

| Model File              | Type        | Target Variable          | Compounds  |
|-------------------------|-------------|--------------------------|------------|
| tire_deg_soft.pkl       | XGBRegressor| lap_time_delta           | Soft       |
| tire_deg_medium.pkl     | XGBRegressor| lap_time_delta           | Medium     |
| tire_deg_hard.pkl       | XGBRegressor| lap_time_delta           | Hard       |
| tire_deg_inter.pkl      | XGBRegressor| lap_time_delta           | Inter      |
| tire_deg_wet.pkl        | XGBRegressor| lap_time_delta           | Wet        |
| pit_predictor.pkl       | LGBMClassifier| did_pit (binary)       | All        |
| safety_car_model.pkl    | Poisson/scipy | P(SC in N laps)        | —          |

Models are loaded lazily on first use per worker process (checking
local disk cache, then S3's :production tag) and cached in memory
for the process's lifetime — restart the worker to pick up a newly
promoted model version. race_simulator.py is wired as of Day 11 via
the run_race_simulation Celery task (prediction_queue), called by
POST /strategy/{session_id}/simulate.

---

## Redis Cache Key Schema

```
f1:{season}:{round}:car:{driver_num}:latest                  TTL: 8s       (live telemetry per car)
f1:{season}:{round}:gaps                                     TTL: 8s       (all driver gaps)
f1:{season}:{round}:strategy:{driver_id}:pit_window          TTL: 30s      (optimal pit window prediction)
f1:{season}:{round}:strategy:{driver_id}:undercut:{target}   TTL: 30s      (undercut score vs target driver)
f1:{season}:{round}:strategy:{driver_id}:overcut:{target}    TTL: 30s      (overcut score vs target driver)
f1:{season}:{round}:strategy:competitors                     TTL: 30s      (all drivers predicted pit windows)
f1:{season}:{round}:telemetry:{driver_id}:history:{last_n}   TTL: 15s      (lap history sector data)
f1:{season}:{round}:driver:{driver_id}:car_number            TTL: session  (driver_id → car_number mapping)
f1:{season}:{round}:weather:latest                            TTL: 60s      (live track_temp/air_temp, written by ingest_live_session.py's WeatherData handler)
f1:driver:{driver_id}:fingerprint                            TTL: 3600s    (driver style profile — season-level archetype/cluster/UMAP; written as a side effect of the population fit below, see driver_service.get_driver_analysis)
f1:driver_style:fit:{season}                                  TTL: 3600s    (cached population-level PCA(4)->KMeans(5)->UMAP(2D) fit for driver_service.py's driver-style analysis endpoint — avoids refitting for every driver requested in the same season, see services/driver_service.py)
f1:race:{race_id}:detail                                          TTL: 86400s   (race + circuit + sessions, now wired Day 13)
f1:race:{race_id}:session:{session_id}:detail                     TTL: 86400s   (single session lookup)
f1:race:by_session:{session_id}:detail                            TTL: 86400s   (Day 43: resolves a session_id to its own race+circuit, for Circuit Map Panel — see race_service.get_race_by_session)
f1:races:list:{season}:{round_number}:{page}:{page_size}          TTL: 86400s   (paginated race listing)
f1:current_race:{season}                                          TTL: 300s     (Ergast-resolved current race, insulates external API)
f1:drivers:all                                                    TTL: infinity (driver roster, manual invalidation only)
f1:driver:{driver_id}:session:{session_id}:laps:{page}:{page_size} TTL: 86400s (paginated per-driver lap history)
f1:circuit:{circuit_id}:detail                               TTL: infinity (static data)
f1:alerts:{session_id}                                       pub/sub       (no TTL — alert delivery channel)
f1:telemetry:{session_id}:laps    pub/sub    (lap completion broadcast channel, Checkpoint E Day 11)
f1:{season}:{round}:R:auto_ingestion_triggered                TTL: 14400s   (Day 39B dedup lock, not cached data — SETNX guard so a re-poll of check_for_live_session doesn't double-launch the live ingestor for the same race; see Auto Race Detection below)
f1:demo:replay:state                                          TTL: 7200s    (Day 43 Part 4 — single global Demo Replay state, not cached data. JSON: replay_id/session_id/race_name/start_lap/end_lap/pid/started_at. Written by demo_service.start_replay (NX claim then full payload), read by GET /demo/replay/status, deleted by stop_replay / the race_detection_worker kill-switch. TTL is a safety net well above a curated window's ~20-min playout.)
f1:strategy:last_ingested_session                            TTL: 86400s   (newest-race_date COMPLETED R session that has lap_data — GET /strategy/last-ingested-session, the Strategy Simulator's session source when no race is live. Race.status == "completed" filter added 2026-08-30 to exclude partially live-ingested sessions, see Deferred Wiring/Notes. Not written by ingestion, so a newer ingest surfaces after this expires or a manual cache_service delete. Constant key — resolved per-environment from that DB.)
```

When adding a new cache key: add it to this list with TTL and justification.

---

## Auto Race Detection (Day 39B)

`backend/workers/race_detection_worker.py`'s `check_for_live_session` Celery
Beat task polls Ergast's race schedule every 5 minutes
(`celery_app.py`'s `beat_schedule`) and auto-launches
`ingest_live_session.py` as a detached subprocess when a Race (`R`) session's
scheduled start is within a 30-minute grace window
(`race_detection_worker._GRACE_WINDOW`) of "now".

**Why a subprocess, not an inline Celery task call:** the worker runs
`--pool=solo` (single process, single thread) across all three queues.
`run_live_ingestor()` blocks for up to 3 hours and itself dispatches
`process_lap.delay()`/`run_strategy_prediction.delay()` back onto that same
worker — calling it inline from a Celery task would deadlock the whole race
(the worker stuck inside the detection task, never picking up the lap/
prediction tasks that same task depends on). `check_for_live_session`
instead launches `python -m backend.scripts.ingest_live_session --season
... --round ... --session-type R` as a detached OS process
(`subprocess.Popen(..., start_new_session=True)`) and returns immediately —
same mechanism `make ingest-live` already uses manually, just auto-triggered.

**Dedup:** a Redis `SET key NX EX 14400` (`f1:{season}:{round}:R:
auto_ingestion_triggered`, see Redis Cache Key Schema above) claims the
race atomically on first trigger; later polls within the same race see the
key already set and no-op. TTL (4h) covers the ingestor's 3h default
`max_duration` plus buffer. Deliberately Redis-only, not a DB column — no
migration needed, and `get_or_create_session`/`get_or_create_race` (called
inside the subprocess's own `_resolve_context`) are already idempotent, so
a duplicate launch after a Redis flush is harmless (a second SignalR
connection for the same session), not corrupting.

**Enable/disable:** `LiveTimingSettings.auto_race_detection_enabled`
(`AUTO_RACE_DETECTION_ENABLED` env var, default `true`). Set to `false` to
disable without touching the beat schedule itself — the task checks this
flag first and no-ops. Toggling requires a worker restart (pydantic-settings
reads env once at process start), same as every other setting in this
project.

**Demo Replay kill-switch (Day 43 Part 4):** once the dedup claim above
succeeds (a real race is definitely launching), `check_for_live_session`
calls `_force_stop_demo_replay(client)` before `_launch_ingestion_
subprocess`. It reads `f1:demo:replay:state` (see Redis Cache Key Schema),
`os.kill(pid, SIGTERM)`s the replay subprocess (`replay_pipeline.py`'s
`_reraise_sigterm_as_interrupt` handler turns that into its graceful
KeyboardInterrupt shutdown — position thread stopped, keys left to TTL
out), and deletes the state key. A real live race always wins: a replay
and a live ingestor both write `f1:{season}:{round}:gaps` /
`:car:{n}:position`. A dead/exited pid (`ProcessLookupError`/
`PermissionError`) or a bare NX-claim sentinel with no pid is tolerated —
the key is cleared regardless. Covered by
`tests/unit/test_race_detection_worker.py`.

**Requires a running `celery beat` process** (`infra/docker/docker-
compose.yml`'s `beat` service, same image as `worker`, `celery -A
backend.workers.celery_app beat --loglevel=info`) in addition to the
worker — beat only schedules `check_for_live_session`, the worker
service actually executes it. Not yet wired into a Fly.io production
process (Day 40, out of scope today).

**Edge cases:** Ergast unreachable → caught, logged, task returns cleanly
(no beat-schedule disruption); no Race session in the grace window → no-op;
already-triggered → no-op (see dedup above). Covered by
`tests/unit/test_race_detection_worker.py`.

**Scope note:** detection is Race (`R`) sessions only, not FP1/FP2/FP3/Q —
`ingest_live_session.py --poll`'s existing APScheduler-based
`_run_scheduler`/`_find_upcoming_session` (hourly, all 5 session types, 10
min *before*-start window) is unchanged and still available as a separate,
manually-run all-sessions alternative; the two now share their Ergast
date/time parsing via `_ingest_common.py`'s `SESSION_TYPE_TO_ERGAST_COLUMNS`/
`combine_ergast_date_time` rather than duplicating it.

---

## API Versioning

All endpoints are under /api/v1/. When adding a breaking change, create /api/v2/
for those specific endpoints — never modify existing v1 response schemas.

Current endpoints overview:
- POST   /api/v1/auth/register
- POST   /api/v1/auth/login
- POST   /api/v1/auth/refresh
- POST   /api/v1/auth/logout
- GET    /api/v1/auth/me
- GET    /api/v1/races
- GET    /api/v1/races/{id}
- GET    /api/v1/races/current
- GET    /api/v1/races/upcoming
- GET    /api/v1/races/session/{session_id}
- GET    /api/v1/drivers
- GET    /api/v1/drivers/{id}/analysis
- GET    /api/v1/drivers/{id}/laps
- GET    /api/v1/telemetry/{session_id}/{driver_id}/live
- GET    /api/v1/telemetry/{session_id}/{driver_id}/history
- WS     /api/v1/ws/telemetry/{session_id}
- GET    /api/v1/telemetry/{session_id}/gaps
- GET    /api/v1/strategy/simulate/{task_id}
- GET    /api/v1/strategy/last-ingested-session
- GET    /api/v1/strategy/{session_id}/{driver_id}/pit-window
- GET    /api/v1/strategy/{session_id}/{driver_id}/undercut
- GET    /api/v1/strategy/{session_id}/{driver_id}/history
- GET    /api/v1/strategy/{session_id}/overview
- POST   /api/v1/strategy/{session_id}/simulate
- GET    /api/v1/alerts
- PUT    /api/v1/alerts/{id}/read
- GET    /api/v1/alerts/subscriptions
- PUT    /api/v1/alerts/subscriptions
- GET    /api/v1/demo/sessions
- GET    /api/v1/demo/replay/available
- GET    /api/v1/demo/replay/status
- POST   /api/v1/demo/replay/start
- POST   /api/v1/demo/replay/stop
- GET    /health

---

## Current Project Phase

Update this section at the start of each day's session:

```
Phase:    8
Day:      Deferred items — batch 3 (item 7)
Status:   Item 7 done: NULL-lap cumulative-sum gap/race-time
          reconstruction fixed via a new LapData.
          session_elapsed_seconds column (migration
          20260902_add_session_elapsed_seconds_to_lap_data),
          captured from FastF1's absolute Lap.Time at
          ingestion (ingest_historical.py) and backfilled
          for all existing local R sessions (backend/scripts/
          backfill_lap_session_time.py — 169,709 rows across
          155/158 sessions). All 4 affected call sites
          (telemetry_service._compute_session_gaps,
          strategy_service/prediction_worker's
          _cumulative_race_time, prediction_worker
          ._build_race_state) now prefer it, falling back to
          the original SUM(lap_time_seconds) reconstruction
          only for a live-ingested/never-backfilled session.
          Verified against real FastF1 final classifications
          for British GP 2026 R9 and Belgian GP 2026 R10 (not
          just unit tests) — exact position-order match,
          gaps accurate to ≤0.18s vs. the original bug's 343s
          error. Live verification surfaced one genuine,
          distinct, pre-existing gap (not introduced by this
          fix): no F1 penalty/post-race-classification data
          is ingested anywhere, so a penalized driver's
          on-track order can disagree with the official
          result — logged as its own new Deferred Wiring
          entry, not fixed today. 5 items remain in docs/day-
          deferred-fixes-session2-handoff.md: 4 (predicted_
          finish_time naming), 5 (driver skill signal), 6
          (strategic adaptation — research-first), 8 (WET
          model retrain, low value, unblocked since item 9),
          10 (track-condition input). Item 7's Supabase
          backfill is a manual post-merge step (see docs/
          runbook.md's new "One-time: backfill session_
          elapsed_seconds on Supabase" section) — cannot run
          until this branch merges and cd.yml's migrate job
          adds the column to Supabase.
Next:     Continue down the remaining 5 deferred items — no
          single one is recommended next; pick per session
          based on priority/scope. Fly.io deployment (Day 40
          A4) after deferred items are addressed. Note:
          train-models.yml currently fetches zero 2026
          laps (see the escalated GitHub-Actions/FastF1
          deferred item) — resolve or consciously accept
          the base-corpus-only outcome before triggering
          a real run that would exercise item 9's guard
          for the first time in production.
Blockers: No physical device for testing — Android emulator 
          setup planned after Day 32 (see mobile/src/README.md),Cloud deployment target undecided (Render/GKE) — cd.yml Jobs 3-5 remain placeholders, Sector boundaries (S1/S2/S3) deferred — see CLAUDE.md, VITE_API_URL_PROD placeholder until Fly.io deployed Day 40, ALLOWED_ORIGINS needs Vercel URL after Day 40 deployment. Note: always recreate local Docker stack with --env-file .env flag or secrets silently blank.
```

---

## What Claude Must Do at the Start of Every Session

1. Read this CLAUDE.md in full
2. Run `find backend/ -name "*.py" | head -40` to see current file state
3. Check `git log --oneline -10` to see what was last committed
4. Read the "Current Project Phase" section above
5. Then and only then begin implementing the day's tasks

Never assume file contents from memory. Always read the actual file before editing it.


## External Services & Credentials Checklist

Track which external services have been set up and which are pending.
Update this list as each service is configured.

| Service | Purpose | Status | Needed By |
|---|---|---|---|
| Firebase / FCM | Push notifications (mobile + web) | ⬜ Not set up | Day 31 |
| F1TV Subscription | Authenticated live timing feed | ⬜ Not set up | Live testing |
| AWS S3 (f1-strategy-models) | ML model storage | ✅ set up | Day 7 |
| AWS IAM credentials | S3 read/write access | ✅  set up | Day 7 |
| Supabase (production DB) | Cloud PostgreSQL | ✅ set up day 23 | Day 23 |
| Upstash Redis (production) | Cloud Redis cache + broker | ✅ set up day 23 | Day 23 |
| Kubernetes cluster (local Docker Desktop) | Local container orchestration | ✅ set up day 22 | Day 22 |
| Sentry | Exception tracking + performance | ✅ set up | Day 12 |
| Slack (F1 Strategy Engine workspace) | Alertmanager notifications | ✅ Set up | Day 12 |
| Vercel | Web frontend deployment | ⬜ Not set up | Day 33 |
| GitHub Secrets | CD pipeline credentials | ✅ set up day 19 | Day 19 |

### Setup Notes

**Firebase FCM:**
- console.firebase.google.com → New project → Cloud Messaging
- Project Settings → Service Accounts → Generate new private key → save JSON
- Add path to .env: FIREBASE_CREDENTIALS_PATH=/path/to/firebase-credentials.json
- Never commit the JSON file — add it to .gitignore

**F1TV Auth:**
- Requires active F1TV subscription
- Run get_auth_token() once to cache OAuth token locally
- ingest_live_session.py defaults to no_auth=True until this is configured

**AWS S3:**
- Create bucket: f1-strategy-models (private, versioning on, AES-256)
- IAM user with s3:GetObject, s3:PutObject on that bucket only
- Add to .env: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION=ap-south-1

**Sentry:**
- sentry.io → New Project → Python → FastAPI
- Add DSN to .env: SENTRY_DSN=https://...

**GitHub Secrets:** see the "GitHub Secrets Checklist" section below for the
full, verified-against-the-actual-workflow-files list — the original Day 19
list above (`DATABASE_URL`/`REDIS_URL`/`KUBECONFIG`) named secrets that no
current workflow actually references and is kept here only as history.

## GitHub Secrets Checklist

Audited Day 39 by reading every `.github/workflows/*.yml` file and listing
every `${{ secrets.X }}` actually referenced, then cross-checked Day 39
against the real GitHub Secrets page (repo Settings → Secrets and variables
→ Actions) — every secret below is **confirmed set as of 2026-08-20**.
Re-audit the "referenced in" column whenever a workflow file changes which
secrets it reads.

### Referenced by a workflow today

| Secret | Referenced in | Status | Notes |
|---|---|---|---|
| `AWS_ACCESS_KEY_ID` | `cd.yml`, `train-models.yml` | ✅ set, confirmed 2026-08-20 | S3 model storage + ECR push |
| `AWS_SECRET_ACCESS_KEY` | `cd.yml`, `train-models.yml` | ✅ set, confirmed 2026-08-20 | |
| `AWS_REGION` | `cd.yml`, `train-models.yml` | ✅ set, confirmed 2026-08-20 | |
| `ECR_REGISTRY` | `cd.yml` (`build-and-push`) | ✅ set, confirmed 2026-08-20 | `build-and-push`/`migrate` are NOT deferred — they run on every merge to `main` even though the K8s deploy stages after them (`deploy-staging`/`deploy-production`) are still placeholders. |
| `ECR_BACKEND_REPO` | `cd.yml` (`build-and-push`) | ✅ set, confirmed 2026-08-20 | Same as above. |
| `ECR_WORKER_REPO` | `cd.yml` (`build-and-push`) | ✅ set, confirmed 2026-08-20 | Same as above. Once Fly.io is live (Day 40) and `deploy-production` builds straight from the Dockerfile, ECR push may become dead weight worth removing — not done today, out of scope for this audit. |
| `SUPABASE_DIRECT_URL` | `cd.yml` (`migrate`), `keep-supabase-alive.yml` | ✅ set, confirmed 2026-08-20 | Session-mode pooler, port 5432 — the Supabase secret CI/CD actually uses for migrations + the keep-alive ping. |
| `SLACK_WEBHOOK_DEPLOY` | `cd.yml`, `train-models.yml`, `load-test.yml`, `keep-supabase-alive.yml` | ✅ set, confirmed 2026-08-20 | Distinct from `.env`'s `SLACK_WEBHOOK_CRITICAL`/`SLACK_WEBHOOK_WARNING` (Alertmanager, local `.env` only, never a GitHub Secret) — three differently-scoped Slack webhooks exist in this project; don't conflate them. |
| `VERCEL_TOKEN` | `cd-web.yml` | ✅ set, confirmed 2026-08-20 | |
| `VERCEL_ORG_ID` | `cd-web.yml` | ✅ set, confirmed 2026-08-20 | |
| `VERCEL_PROJECT_ID` | `cd-web.yml` | ✅ set, confirmed 2026-08-20 | |
| `VITE_API_URL_PROD` | `cd-web.yml` | ✅ set, confirmed 2026-08-20 | Even set, this points at nothing real until the Fly.io backend exists (Day 40) — see the "VITE_API_URL_PROD placeholder" blocker note in Current Project Phase. Revisit its value after Day 40. |
| `GITHUB_TOKEN` | `train-models.yml`, `cd-desktop.yml` | ✅ auto-provided | Injected automatically by GitHub Actions per run — not something to add manually, doesn't appear on the Secrets page. |

### Set on the Secrets page but not yet read by any workflow

Confirmed present 2026-08-20. These mirror `.env.example`'s app-runtime
vars — set proactively so they're ready for the Day 40 Fly.io
`deploy-production` job (via `fly secrets set` or an env-passthrough step)
rather than something to fix today; no current workflow file reads them via
`${{ secrets.X }}`.

- `AWS_BUCKET_NAME`, `ENVIRONMENT`, `FASTF1_CACHE_DIR`, `METRICS_PASSWORD`,
  `METRICS_USER`, `RELEASE_VERSION` — app runtime config, not yet wired into
  any deploy step.
- `SECRET_KEY` — every CI job that needs one (`unit-tests`/`integration-tests`/
  `e2e-tests` in `ci.yml`) hardcodes a non-secret placeholder value directly
  in the job `env:` block instead, by design (see each job's own comment) —
  this real secret is reserved for the Day 40 production app.
- `SENTRY_DSN` — same reasoning; needed once Day 40's real Fly.io deploy step
  lands and passes it through to the running app.
- `SUPABASE_DATABASE_URL` — the transaction-mode pooler URL (port 6543, app
  runtime), as opposed to `SUPABASE_DIRECT_URL` (session-mode pooler, port
  5432, migrations) above. Both are correctly present; `SUPABASE_DATABASE_URL`
  just isn't consumed by any workflow file yet — it's for the app's own
  `DATABASE_URL`/`TIMESCALE_URL` once a real deploy job sets them.
- `UPSTASH_REDIS_URL` — same category, for the app's own `REDIS_URL` once wired
  into a real deploy job.

`KUBECONFIG` is not on the Secrets page and none is needed — production
target is Fly.io (decided Day 24), not Kubernetes.


## Development Tooling Notes

### AWS Credentials

AWS credentials must be explicitly passed to backend and worker 
containers via docker-compose.yml env vars — boto3's default 
credential chain does not read pydantic-settings .env values. 
Both _download_from_s3 functions and the compose env passthrough 
were fixed on Day 13.

### Celery worker — restart required after code changes

Unlike the backend container (uvicorn --reload auto-reloads), Celery 
workers do not hot-reload. After any change to files in backend/workers/, 
run: docker compose restart worker
Otherwise the old worker process serves the old code indefinitely.

### libgomp1 — required in Docker final stage for LightGBM

`python:3.11-slim` strips system libraries including `libgomp1`, which 
LightGBM requires at import time (`dlopen` on OpenMP runtime). Both 
`Dockerfile.backend` and `Dockerfile.worker` final stages must include:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
```

Without this, any import of `lightgbm` (including transitively via 
`pit_predictor.py`) raises `OSError: libgomp.so.1: cannot open shared 
object file`. Added in Day 10 Checkpoint A.

### bcrypt — use directly, not via passlib

`passlib 1.7.4` is unmaintained and incompatible with `bcrypt>=4.1` 
(raises `ValueError` on its internal self-test instead of the old 
silent behavior passlib expects). Every `hash_password`/`verify_password` 
call crashes at runtime.

Fix applied Day 10: removed passlib from pyproject.toml, rewrote 
`core/security.py` to call `bcrypt.hashpw()` and `bcrypt.checkpw()` 
directly. `bcrypt>=4.0.0` is now a direct dependency.

### slowapi rate limiting — use per-route decorators, not middleware

SlowAPIMiddleware's default_limits cannot do dynamic per-request limits —
the request object is None in that code path (verified in slowapi 0.1.10 
source: _check_request_limit with in_middleware=True). Dynamic auth-vs-ip 
limits require the per-route @limiter.limit(callable) decorator pattern, 
which correctly binds the request object. All rate-limited routes must have 
request: Request as a parameter.

### Alembic — always run from host, never inside Docker

`alembic.ini` lives at the repo root and `DATABASE_URL` in `.env` points
to `localhost:5432` (Docker's exposed port). The container does not have
`alembic.ini` or the root `pyproject.toml` copied in, so running alembic
inside the container fails with "No script_location key found".

Always run migrations from the host venv with Docker postgres running:

```bash
# Generate a new migration
.venv/Scripts/python.exe -m alembic revision --autogenerate -m "description"

# Apply migrations
.venv/Scripts/python.exe -m alembic upgrade head

# Check for schema drift
.venv/Scripts/python.exe -m alembic check
```

## Deferred Schema Changes

Schema additions that were intentionally deferred from their discovery day
to avoid out-of-scope migrations. Add these on the specified day.

| Column | Table | Purpose | Add On |
|---|---|---|---|
| fcm_token | users | Device token for FCM push notifications |✅ Done Day 10 |


## Deferred Wiring & Integration Gaps

These are not schema changes but known integration gaps. Audited Day 39:
each item below is tagged genuinely deferred (real future work), out of
scope for this portfolio project (documented and closed, not going to
happen), or was found already fixed and moved into ### Notes below instead.

- **[✅ done 2026-09-02] Cumulative-sum gap/race-time reconstruction (`SUM(lap_time_
  seconds) ... WHERE lap_time_seconds IS NOT NULL`) silently produced
  non-comparable totals across drivers, corrupting the timing tower's gaps
  for any session ingested via `ingest_historical.py`.** Discovered Day 42
  investigating a user report on British GP 2026 Round 9: `GET /telemetry/
  {session_id}/gaps` showed P1→P2 = +2:55, P4→P5 = +5:43 — multi-minute
  gaps between adjacent cars, not realistic for a real race at that
  spacing.
  - **Root cause:** a lap with a NULL `lap_time_seconds` (out-lap, in-lap
    around a pit stop, a Safety Car lap, or any lap FastF1 didn't record a
    valid time for) is excluded entirely by the `WHERE lap_time_seconds IS
    NOT NULL` filter, so it silently drops out of that driver's cumulative
    `SUM()`. Different drivers naturally have different NULL-lap counts
    (different pit strategies, different SC timing relative to their pit
    windows), so their cumulative sums stop being directly comparable —
    the "gap" between two drivers ends up being (real gap) ± (however much
    time their differing NULL-lap counts hid), not the real gap.
  - **Confirmed with real numbers**, not just theory: for this session, the
    P1 leader had **3** NULL-time laps vs. P2/P3's **2** — one extra
    "missing" lap's worth of time (~175s) landed exactly on the phantom
    P1→P2 gap (`175.24099999999817` in the raw API response). Lap-number
    *row* coverage itself was confirmed contiguous per driver (checked via
    `MIN`/`MAX`/`COUNT(*)` — no missing rows, unlike Day 36's Dutch GP
    finding where laps 1-8 were never live-ingested at all) — this is a
    related but distinct manifestation of the same underlying class of bug
    (differential per-driver lap-time-data coverage breaking a raw
    cumulative-sum comparison), not the identical Day 36 mechanism.
  - **Affects 4 call sites**, all sharing the same `SUM(lap_time_seconds)
    ... IS NOT NULL` pattern: `telemetry_service._compute_session_gaps`'s
    `_GAPS_QUERY` (the timing tower gaps this was discovered through),
    `strategy_service._cumulative_race_time`, `prediction_worker
    ._cumulative_race_time`, and `prediction_worker._build_race_state`'s
    batched cumulative-time query. The latter three back undercut/overcut
    probability and Monte Carlo race-simulation starting state — both are
    almost certainly also silently wrong for this same session, not just
    the gaps display. This is a **second, independent** way undercut math
    can be wrong for a historically-ingested session, distinct from the
    already-logged stale-`undercut_score` alert-dedup issue above (that
    one is about reading a prediction that's simply old; this one is about
    the underlying cumulative-time inputs themselves being non-comparable
    across drivers even when fresh).
  - **Scope:** any session ingested via `ingest_historical.py` (not just
    British GP, not just replay-tested sessions) is affected — this is a
    property of the ingestion path (no live TimingData ever ran to capture
    F1's own authoritative gaps), not something specific to one race.
    `telemetry_service.get_session_gaps`'s own docstring already
    anticipated this exact scenario before today ("confirmed unreliable
    for a session with any gap in its recorded lap history... should only
    ever be reached for a session that was never live-ingested") — this
    investigation found the precise mechanism, not a new class of gap.
  - **Fixed via the ingestion-time change** this entry's own "no easy fix"
    note anticipated: `LapData.session_elapsed_seconds` (migration
    `20260902_add_session_elapsed_seconds_to_lap_data`) captures FastF1's
    absolute `Lap.Time` directly, anchored to the session's earliest
    `LapStartTime` (`ingest_historical.py`'s `resolve_session_start`/
    `compute_session_elapsed_seconds`) — confirmed populated on 100% of lap
    rows across a 2020-2026 sample, including every row with a NULL
    `lap_time_seconds`. `backend/scripts/backfill_lap_session_time.py`
    (`make backfill-lap-session-time`) backfilled all 158 local R sessions
    (169,709 rows updated across 155 sessions; the other 3 were either
    already done or genuinely empty). All 4 call sites now prefer
    `session_elapsed_seconds` and fall back to the original
    `SUM(lap_time_seconds)` reconstruction only for a session that was
    never backfilled (a live-ingested session — deliberately left NULL
    there, see below) — see the Notes entry below for the full write-up and
    live verification against real final classifications.
  - **`f1:{season}:{round}:gaps:last_good`** was checked directly against
    the local Redis before this fix landed — empty, nothing poisoned to
    invalidate at the time (no request had hit the broken path recently
    enough for a `:last_good` value to still be cached). A genuinely
    poisoned key from before this fix would still need a manual delete —
    this fix does not retroactively correct an existing cached value, only
    every compute going forward.
  - **Deliberately NOT extended to live ingestion:** `ingest_live_session.py`
    never populates `session_elapsed_seconds` — its TimingData stream
    carries no absolute session clock, and a live session already has its
    own authoritative Redis gaps via `_publish_live_gaps`, so this path is
    rarely reached for a live session anyway. Live-ingested sessions
    continue to use the original `SUM(lap_time_seconds)` fallback,
    unchanged from before this fix.

- **[deferred] No F1 penalty/post-race-classification data is ingested
  anywhere — `lap_data.position` (and everything derived from it:
  `_compute_session_gaps`'s ranking, `race_simulator`'s `starting_position`)
  reflects LIVE on-track order, not the final penalized classification, and
  can disagree with it for any driver who receives a post-race time
  penalty.** Discovered 2026-09-02 verifying item A's fix
  (`session_elapsed_seconds`, see the ✅-fixed item A entry above) end-to-end
  against British GP 2026 Round 9's real final classification: the computed
  field order matched FastF1's own `session.results` exactly for positions
  1-8, then diverged at position 9 — the reconstruction (and the DB's own
  `lap_data.position`, sourced from FastF1's raw per-lap `Position`
  telemetry) shows ANT running P9 for their entire final stint with a
  ~3.4s gap to COL, while `session.results` classifies ANT P15 with an
  8.005s gap. **Confirmed pre-existing, not introduced by item A's fix:**
  computed directly what the OLD `SUM(lap_time_seconds)` code would have
  produced for this exact pair — ANT's old-style cumulative sum (5070.97s)
  is also less than COL's (5071.94s), so the same mis-ranking existed
  before today's fix too, for the same underlying reason. **Root cause:**
  `ingest_historical.py`'s `Lap.Position`/`Lap.Time` come from FastF1's
  live/raw timing feed, which has no concept of a penalty applied after the
  checkered flag; there is no `penalties` table or any ingestion path for
  this anywhere in the codebase. **No easy fix** — would need a new
  ingestion path for FastF1's `session.results`/`Status`/penalty data
  (confirmed available via `session.results` directly, not currently read
  by any script) and a decision on how "current gaps" should reconcile live
  position against a not-yet-applied pending penalty for an in-progress
  session (a penalty is typically decided/announced after the race, so even
  a "final" classification pull mid-session wouldn't have it yet). Affects
  `_compute_session_gaps` (timing tower) for any completed session with a
  penalized driver, and by extension anything using `lap_data.position` as
  a position proxy (`race_simulator`'s `starting_position`,
  `prediction_worker._build_race_state`). Low urgency: only matters for the
  exact drivers/positions affected by a penalty, on an already-completed
  session's timing tower — mid-race live gaps are unaffected since a
  penalty isn't yet known at that point.

- **[deferred] `evaluate_threats`/`_latest_undercut_scores` reads each
  driver's most-recent `StrategyPrediction.undercut_score` with no
  recency check, so a stale row from a prior session/day can trigger a
  real alert before that driver has a fresh prediction today.** Observed
  Day 42 during `replay_pipeline.py` verification of the new alert
  wiring: the first alert of a fresh replay run fired off a driver's
  leftover Day 41 score before their own prediction had been recomputed.
  Not fixed today — a real fix would bound the lookup to predictions from
  the current session/recent window, not just "most recent regardless of
  age."

- **[deferred] `prediction_worker._resolve_position_context` has no
  `lap_number <= current_lap` bound, so for a fully-ingested historical
  session played back through `replay_pipeline.py` it computes every lap's
  undercut/overcut against the RACE-END field snapshot — `undercut_score`
  and `overcut_score` come out frozen (and mostly saturated to 0.0/1.0)
  for the whole replay.** This directly degrades the headline "Undercut
  Threat Detection" feature during Demo Replay. Discovered Day 43 Part 2
  during manual verification of a Belgian GP 2026 Round 10 replay (curated
  window laps 14-23): the Undercut panel showed 0% for every driver the
  user selected.
  - **Confirmed with real `strategy_predictions` rows** for that session
    (`da57b9fd-4976-4fce-91a1-c7d0aac9c619`): `undercut_score` is frozen
    across all 11 replayed laps per driver — ALB/HAM/LEC/VER `0.000` every
    lap, ALO/NOR `1.000` every lap; only LIN (~0.93) and PIA (~0.63) drift
    at all, and only because the deterministic stint projection reads the
    *incoming lap's* tyre age while the position/gap threshold stays
    static. HAM/LEC/VER are exactly the drivers a user clicks first, and
    they all sit at 0.000 — hence "always 0% regardless of driver".
    `UndercutThreatPanel`'s `ReplayThreatRow` renders
    `historyEntry.undercut_score` faithfully; the stored value is what's
    wrong, not the frontend.
  - **Root cause:** `_resolve_position_context(db, session_id, driver_id)`
    (no lap arg at all) does
    `SELECT driver_id, MAX(lap_number) ... WHERE session_id = X GROUP BY
    driver_id`, joins each driver's absolute-latest `LapData` row, and
    `.order_by(LapData.position)`. Belgian GP `lap_data` is fully ingested
    (laps 1-44), so "latest" is lap 44 for everyone → the field is ordered
    by *finishing* position (LEC P2, VER P3, HAM P4, ALB P15…) and the
    `target_ahead_driver_id`/`gap_to_car_ahead` it returns are the
    race-end neighbours and race-end cumulative-time gaps — identical on
    every `run_strategy_prediction` call the replay dispatches, so
    `_resolve_undercut_overcut` → `strategy_service.get_undercut_score`
    gets a constant input and returns a constant, usually-saturated
    probability. The `_cumulative_race_time` calls *inside*
    `_resolve_position_context` (lines ~248/254/262) are passed
    `driver_lap.lap_number` == 44, so they sum the whole race too.
  - **Known-good fix pattern already exists in this same file.**
    `prediction_worker._build_race_state` (the Monte Carlo `/simulate`
    path) had the identical class of bug and was fixed under
    `feature/pre-day30-monte-carlo-fix` (see "Monte Carlo simulator
    fixes" in Architecture Decisions). It takes `current_lap` as a
    parameter and, for field position, uses a second subquery
    (`position_subq` / `ref_lap`) scoped
    `WHERE LapData.session_id == session_id AND LapData.lap_number <=
    current_lap`, joining on `LapData.lap_number == position_subq.c.ref_lap`
    — "Field position as of current_lap specifically — NOT each driver's
    own absolute-latest DB row" (its own inline comment). Its batched
    cumulative-time query is likewise capped `LapData.lap_number <=
    current_lap`. The fix here is to thread `context["lap_number"]` from
    `_resolve_undercut_overcut` (it already has `lap_number` in `context`
    — see its call at line ~589 / `_run_inference`'s `context`) down into
    `_resolve_position_context` and apply exactly that `<= current_lap`
    bound to both its field-position subquery and the
    `_cumulative_race_time` calls it makes. No new pattern to design —
    just the second call site catching up to the first.
  - **Relationship to the two items above:**
    1. The **NULL-lap-sum `SUM(lap_time_seconds) ... IS NOT NULL` bug**
       (4-call-sites item above): `_resolve_position_context`'s gap
       numbers go through `_cumulative_race_time`, so they carry that
       corruption *on top of* the wrong-lap problem. Even after the
       `<= current_lap` bound lands, the gaps stay approximate until the
       NULL-lap issue is also addressed (ingestion-time absolute `Lap.Time`
       or query-time interpolation). The two are independent: this one is
       "reading the wrong lap's field state", that one is "the per-lap
       deltas being summed aren't comparable across drivers".
    2. The **stale-`undercut_score` alert-dedup item** (directly above):
       that is about `evaluate_threats` reading an *old* persisted
       prediction row; this is about the row being *wrong when freshly
       computed* because its position/gap inputs are anchored to the wrong
       lap. A replay can hit both at once.
  - **Not the same as the "near-zero `pit_probability` is genuine"
    finding** (British GP, Day 43 Checkpoints A-F; and Belgian GP's own
    `pit_probability`, which is genuinely low pre-VSC then correctly
    spikes ~lap 18-21 as the VSC opens the pit window). `pit_probability`
    is fine; `undercut_score`/`overcut_score` are not.
  - **Scope:** any `ingest_historical.py`-ingested session replayed via
    `replay_pipeline.py`. A genuinely live-ingested session accumulates
    `lap_data` lap-by-lap, so "latest lap" naturally tracks the current
    lap there and this bug does not bite — it is specific to replaying a
    session whose full race is already in the DB. Belgian GP was never
    directly tested in Day 41 (that was British GP only, and the Day 41
    undercut work was a 42x perf fix verified via a borderline-equivalence
    check, not against a fully-ingested session's position context) — this
    is genuinely newly-observed on Day 43 Part 2.
  - Deferred to a dedicated future session per explicit instruction — do
    NOT fold it into an unrelated change.

- **[deferred] `tire_deg_hard.pkl` mispredicts a fresh HARD tyre's first lap
  (`tyre_age_laps=1`), inflating `pit_probability` immediately after a real
  pit stop.** Discovered Day 41 via `replay_pipeline.py` against British GP
  2026 Round 9: two predictions (ALB lap 2, PIA lap 3 — both drivers' first
  lap on HARD right after pitting) came back with `pit_probability` 0.72 and
  0.999. Root-cause investigation (not a code bug): reconstructed the exact
  `pit_predictor` feature vector and found `predicted_life_remaining=0.0` for
  both — `tire_deg_model.predict_life_remaining_batch`'s threshold-crossing
  logic is working correctly, it's faithfully reporting that
  `tire_deg_hard.pkl` itself predicts an implausible **+1.71s** degradation
  delta at `tyre_age_laps=1` (a fresh tyre should show near-zero/negative
  delta, not already above `DEGRADATION_THRESHOLD_SECONDS=1.5`), then drops
  to sensible negative deltas (-1.5 to -1.7s) from `tyre_age_laps=2` onward.
  Characterized via a raw-prediction sweep: **HARD-compound-specific** (the
  same `(lap_number=2, tyre_age_laps=1)` input on MEDIUM/SOFT gives normal,
  small, mostly-negative deltas); **not early-race-specific** (HARD at
  `lap_number=20, tyre_age_laps=1` — a normal-timing pit — shows an even
  larger +5.29s spike, so this fires for ANY fresh HARD tyre, any time in
  the race). Most likely cause: a training-data gap specific to
  `tyre_age_laps=1` on HARD — real out-laps are commonly excluded from
  training data via lap-accuracy/`is_valid` filtering, so the model may have
  seen little or no genuine `tyre_age_laps=1` HARD coverage and is
  extrapolating badly into that unseen region (plausibly MEDIUM/SOFT have
  denser coverage there from being used more often). Real race-day risk:
  every HARD pit stop's very next lap would trigger this same false
  "pit again immediately" signal and could fire a spurious alert one lap
  after the driver just stopped. Fix requires retraining `tire_deg_hard.pkl`
  with better `tyre_age_laps=1` coverage (or auditing whether HARD out-laps
  are being systematically filtered from the training corpus) — real ML
  work, not attempted today; genuinely deferred to a future day.

- **[deferred] `StrategyPrediction.tire_life_remaining` stores the wrong
  value — the tire_deg model's raw `lap_time_delta` prediction instead of
  the intended laps-remaining estimate — and can be negative.** Found Day 43
  investigating a Demo Replay manual-verification report of near-zero
  `pit_probability` across most drivers at British GP 2026 Round 9 laps
  43-45 (that report turned out to be correct/expected behavior, not a bug —
  see this file's own commit history for the full investigation — but this
  distinct issue surfaced along the way while cross-checking DB rows).
  `prediction_worker._run_inference` sets `tire_life_remaining =
  float(deg_model.predict(tire_deg_features)[0])` — that's the SAME raw
  degradation-delta prediction `_project_stint_delta`/training use
  elsewhere (seconds of predicted lap-time delta from tyre wear, legitimately
  negative for a fresh tyre), not the laps-until-threshold-crossing value
  the field name implies. The correct value is computed two lines later as
  `predicted_life_remaining` (via `tire_deg_model.predict_life_remaining_
  batch`) and IS used correctly for the `pit_predictor` feature vector
  (`pit_features`) — it's just never the thing persisted to this column.
  Confirmed via direct DB query against real rows: e.g. ALB lap 43
  `tire_life_remaining=-1.749...`, ALO lap 43 `-0.990...` — negative values
  throughout, consistent with a raw (possibly-negative) delta rather than a
  remaining-laps count. **Does not affect `pit_probability` or anything Day
  43's Demo Replay work exposes** — `StrategyPredictionHistoryEntry` (the
  history endpoint's schema) never includes `tire_life_remaining` at all,
  and `pit_features` reads the correctly-computed `predicted_life_remaining`
  variable, not this column. The only consumer is `StrategyPredictionResponse`
  (a schema no current frontend hook renders), so this is currently a silent,
  harmless-in-production mislabeling — genuinely worth fixing (swap in
  `predicted_life_remaining`) but not attempted today since nothing depends
  on the wrong value yet.

- **[out of scope — documented and closed] `CarData.z`/`Position.z` (live
  telemetry gauges + circuit map dots) require F1TV authentication —
  unavailable in `no_auth` mode.** Confirmed live during the Day 40 Dutch GP
  dry run: `ingest_live_session.py` subscribes to both topics correctly and
  `_handle_car_data`/`_handle_position_data` write to exactly the Redis keys
  `telemetry_service.py` reads (`f1:{season}:{round}:car:{car_number}:latest`
  / `:position`, verified matching on both sides) — but with `no_auth=True`
  (the default; see F1TV Auth notes above, no subscription configured),
  zero feed messages for these two topics ever arrive, while `TimingData`/
  `TimingAppData`/`WeatherData`/`DriverList`/`TrackStatus` all stream
  normally on the same connection with zero errors logged. FastF1's own
  `get_auth_token()` docstring states the token requires "an active F1TV
  Access/Pro/Premium subscription," matching this being an entitlement gate
  on F1's side rather than a parsing/subscription bug. `GET /telemetry/
  {session_id}/{driver_id}/live` correctly 503s with "No live telemetry
  cached for car X" in this state — that response is the intended, working
  behavior for an unauthenticated connection, not a defect. Revisit only if
  F1TV Auth (see checklist above) is ever set up.

- **[deferred] extract_circuit_outlines.py must still be run manually
  against Supabase.** `backend/scripts/extract_circuit_outlines.py`'s and
  `seed_circuit_outlines.py`'s own docstrings still read "run locally
  first... then set DATABASE_URL to SUPABASE_DATABASE_URL and re-run
  against production" as an instruction, not a completed step — no
  completion note exists anywhere in this file for the production run, and
  this session has no way to query Supabase directly to confirm either way.
  **Action needed:** confirm whether `circuits.map_geometry` is actually
  populated in the production Supabase DB; if not, run both scripts against
  it (same pattern as `seed_circuits.py`/`seed_teams.py`) before relying on
  the live circuit map feature against production data.

- **[deferred] Circuit map sector boundaries (S1/S2/S3):** requires
  timestamp-correlation between `Lap.Sector1SessionTime`/
  `Sector2SessionTime` and position telemetry's `get_pos_data()`
  `SessionTime` column (~50ms accuracy at racing speed). Needs new logic in
  `extract_circuit_outlines.py` plus edge-case handling for missing sector
  timestamps. The circuit map feature itself (Days 25-28) is complete, so
  this is no longer gated on anything — genuinely still open, not started.

- **[deferred, escalated — superseded the old "403→mirror fallback" framing
  below, real cause is different and this is now urgent, not "before next
  season"] `retrain_incremental.py` fetches ZERO 2026 laps when run from
  GitHub Actions — confirmed via the actual 2026-08-24 `train-models.yml`
  run log (run id `32685243197`), Day 41.** The run's own final line reads
  `Fetched 0 lap row(s) for season 2026` — every model trained by that run
  used only the static 2018-2025 base corpus (`Train laps: 119984, holdout
  laps: 23043`), despite the workflow completing green. The original theory
  (a clean 403 from `livetiming.formula1.com` → mirror fallback →
  `SessionNotAvailableError`, "not urgent today") was wrong on both counts —
  the real failure and its urgency are both worse:
  - **Almost every round (3, 4, 5, 11, 13–23) fails with `fastf1.exceptions.
    DataNotLoadedError`** ("The data you are trying to access has not been
    loaded yet") raised on `retrain_incremental.py`'s own `laps =
    session.laps` line. Root cause: the underlying driver/session-info fetch
    itself comes back **completely empty** (`"Finished loading data for 0
    drivers: []"`) — not a 403, not a mirror-fallback `SessionNotAvailable
    Error`. Round 24 is correctly rejected as out-of-range
    (`Invalid round: 24` — 2026 has 23 rounds) and is not part of this bug.
  - **Round 6 (Monaco) is a subtler variant of the same root cause, not a
    separate bug.** Its `session.load()` call reports `"Finished loading
    data for 22 drivers"` — an apparent success — and along the way
    `fastf1/core.py`'s `_add_first_lap_time_from_ergast()` raises
    `AttributeError: 'Session' object has no attribute '_laps'` for 21 of 22
    drivers. That `AttributeError` is a **red herring**: it's already caught
    inside FastF1's own per-driver `try/except` (logged at `DEBUG`, doesn't
    propagate), and disabling Ergast enrichment (not possible via any public
    `session.load()` parameter anyway — confirmed by reading the installed
    fastf1 3.8.3 source, `load()`'s only args are `laps`/`telemetry`/
    `weather`/`messages`/`livedata`) would **not** have fixed anything. The
    real problem: `self._laps` genuinely never gets populated during this
    `load()` call despite the misleading "22 drivers" success message (which
    reflects driver/session metadata loading fine, not the laps data
    specifically) — so `retrain_incremental.py`'s own subsequent `session
    .laps` access fails with the identical `DataNotLoadedError` one line
    later (`"Skipping round 6"`), for the same underlying reason as every
    other skipped round.
  - **Not a FastF1 library bug, not fixed by a version pin.** FastF1 3.8.3
    (the current latest on PyPI, released 2026-04-29 — `pyproject.toml`'s
    unbounded `fastf1>=3.3.0` resolves to this both locally and in CI) was
    specifically checked: its own changelog already fixed a related
    first-lap/Ergast bug for the 2026 Chinese Grand Prix (round 2). A
    genuinely fresh-cache (not reusing this session's pre-warmed local
    cache) fetch of rounds 1 and 2 from this machine, same fastf1 3.8.3,
    succeeded with zero errors. Same version, same code, works cleanly from
    a residential IP and fails from GitHub's runner — strong evidence this
    is F1's backend serving degraded/incomplete responses specifically to
    GitHub Actions' (datacenter) IP range, not a code or version defect.
    Severity varies by round (total emptiness for most rounds, a partial/
    mild failure for Monaco specifically) rather than a uniform hard block,
    which reads more like throttling/rate-limiting than a permanent ban.
  - **Directly threatens `.github/workflows/ingest-historical.yml`
    (built Day 41, not yet triggered).** It calls the identical
    `load_session()`/`fastf1.get_session().load()` path from the same
    GitHub-hosted runner infrastructure and is very likely to hit this same
    failure — silently ingesting zero rows while still reporting success.
    **Do not trigger it until this is resolved or at least tested
    cautiously against a single round.**
  - **Next steps, in priority order (none attempted yet):** (1) add a delay/
    backoff between each round's fetch in both `retrain_incremental.py`'s
    and `ingest_historical.py`'s loops — cheapest to try, and the
    varying-severity pattern above is more consistent with rate-limiting
    than a hard IP ban; (2) a self-hosted GitHub Actions runner (non-
    datacenter IP) as a more robust fix if pacing doesn't help; (3) until
    resolved, manual local ingestion (`make ingest`, as done Day 41 for
    British GP 2026 Round 9) remains the only reliable path for getting real
    2026 data into Supabase or the training corpus.

- **[deferred, reworded] WS keepalive ping timeouts under heavy CPU load:**
  28,603 closures (85.7% of WS traffic) in the Day 18 500-user run. Likely
  cause: Uvicorn's single event loop blocked by synchronous CPU-bound ML
  inference in `/overview`'s cold path, starving asyncio ping/pong. The DB
  pool fix has since landed (see Notes below); the originally-proposed
  "may resolve naturally with multiple backend pods" fix assumed a
  Kubernetes deployment, which is now out of scope (see note at the top of
  this section) — if this is revisited, the equivalent lever on Fly.io
  would be running multiple machines, not K8s pods. Still open; not
  re-measured since the DB pool fix.

- **[deferred, reworded] Mobile Driver Detail charts (victory-native +
  @shopify/react-native-skia):** still not installed — the Mobile Sync
  Protocol section confirms Driver Detail remains "a minimal stub" with no
  ported charts. The original "install at the start of Day 32" framing is
  stale (Day 32 passed without this happening); the substance is unchanged
  — `npx expo install @shopify/react-native-skia && npm install
  victory-native` is still needed before porting
  `web/src/components/driver/{LapTimesChart,SectorComparison,
  StyleRadar}.tsx` onto `app/driver/[id].tsx`. Post-v1.0.0 polish, not
  blocking.

- **[deferred — desktop sync] Strategy Simulator "last ingested race"
  session source is web-only.** `web/` now replaces the manual "Session
  UUID" text input on `SimulatorPage` with a read-only display sourced from
  `GET /strategy/last-ingested-session` (`useLastIngestedSession`) when no
  race is live — auto-selecting the newest-race_date R session with lap
  data, resolved per-environment. `desktop/src/pages/SimulatorPage.tsx` is a
  copied-and-adapted file (see Desktop Sync Protocol) that already dropped
  web's live-mode detection and sources session/driver from
  `raceContextStore` instead, so this isn't a blind copy — port the
  read-only "last ingested race" display there as a follow-up (hand-write a
  `useLastIngestedSession` equivalent, same as desktop's other hooks). Same
  convention as the other deferred desktop-sync items.

- **[deferred, consolidated] `/strategy/{session_id}/overview`'s 16-17s
  cold-compute floor — partially addressed, not fully closed.** Originally
  two separate entries (per-driver ML inference loop cost, and the cache
  stampede fix not touching the underlying compute cost) describing the
  same root problem; merged here. Batching was applied pre-Day-22
  (`_first_pit_laps_over_threshold_batch`, verified 35% per-call
  improvement, 0.937s → 0.612s models-warm) and the DB connection pool
  fix landed 2026-07-30 (QueuePool timeouts: 493 → 16 → 0 across three
  re-runs — see Notes below), which was suspected to be the dominant
  remaining cause of the 16-17s *concurrent-load* tail (p95/p99). **Not
  yet verified:** no load test has re-measured `/overview`'s p95/p99/max
  specifically since the DB pool fix landed — the QueuePool-timeout-count
  evidence confirms the *symptom* (connection exhaustion) is gone, not
  that `/overview`'s tail latency itself dropped. A fresh load test
  against `/overview` would close this out definitively.

- **[out of scope — documented and closed] prometheus.yml Basic Auth
  hardcoded dev defaults (metrics/metrics-dev):** confirmed still hardcoded
  in `infra/monitoring/prometheus.yml` as of Day 39 — the planned Day 19
  entrypoint-substitution fix (mirroring `alertmanager.yml`'s
  `api_url_file` pattern) was never built, even though `METRICS_USER`/
  `METRICS_PASSWORD` are set as real GitHub Secrets. Closing as out of
  scope: `infra/monitoring/` is a local docker-compose-only stack (see
  Deployment Strategy) — Prometheus is never internet-exposed in this
  project's actual deployment plan (Fly.io hosts the backend/worker only),
  so the dev-default credential is a genuine no-op risk, not a production
  gap. Revisit only if Prometheus is ever deployed somewhere reachable.

- **[out of scope — documented and closed] WebSocket JWT in query param
  (`?token=`):** access token appears in server logs and browser history.
  The proper fix (short-lived WebSocket ticket, exchanged via REST before
  connection) is real production hardening beyond this portfolio's
  remaining scope — closing rather than leaving open-ended. Documented here
  as a known, accepted limitation rather than a TODO.

- **[out of scope — documented and closed] Single `--pool=solo` Celery
  worker cannot sustain race-day traffic; race-day scaling procedure
  (`docs/runbook.md`) assumes Kubernetes pod scaling.** The Day 18
  500-user load test measured 65-88s/task and a 10+ hour backlog-drain
  projection at that throughput — a real, measured limitation. The
  proposed fix (multiple K8s worker pods, `kubectl scale`) is now out of
  scope: production is Fly.io, not Kubernetes (decided Day 24). If this
  project ever needed real race-day-scale traffic, the equivalent lever
  would be `fly scale count worker=N`, itself unbuilt and out of scope for
  today. `infra/k8s/`, `infra/helm-chart/`, and `docs/runbook.md`'s
  Kubernetes-specific scaling/rollback procedures remain validated against
  local Docker Desktop only, as already documented at the top of
  `docs/runbook.md`.

- **[out of scope — documented and closed] `kubectl apply --dry-run=client`
  never validated against a real API server** for `infra/k8s/hpa.yaml`,
  `worker-scaledobject.yaml`, `race-weekend-cronjob.yaml` — only YAML-parser
  validated. No longer relevant: these manifests target a production
  Kubernetes cluster that isn't part of this project's deployment plan
  (Fly.io instead, see Deployment Strategy). Local Docker Desktop
  validation, if ever wanted, remains possible but isn't planned work.

- **[✅ done 2026-08-29] Tyre-degradation backfill applied to Supabase for
  the 2026 races.** Part of the Driver Style page fix (✅ Notes: "Driver
  Style page empty for season 2026"), which had only touched the local DB.
  Ran `backend/scripts/backfill_tire_data.py --season 2026` with
  `DATABASE_URL` overridden to `SUPABASE_DIRECT_URL` (session-mode pooler,
  port 5432; `postgresql://` → `postgresql+asyncpg://` conversion applied,
  same as `cd.yml`'s migrate job) — **141 stint(s) updated, 37 skipped**
  (<2 valid timed laps). The 141 updates match local exactly (Canada R5
  48/55, British R9 51/73, Belgian R10 42/50 → `avg_deg_per_lap` non-null);
  Supabase's skip count is lower than local's 87 only because it lacks
  Zandvoort R12 (50 live-ingested stints, all skipped locally too).
  Verified by re-querying Supabase post-run. Idempotent (plain UPDATE of a
  nullable column) — safe to re-run if 2026 races are re-ingested.

- **[deferred — hardening, only if this class of bug recurs] Driver-style
  fit is brittle to a single entirely-missing feature and to season
  selection.** Two optional improvements, both skipped when the 2026
  Driver Style bug was fixed (see Notes below — the fix was the missing
  `avg_deg_per_lap` backfill, not either of these):
  1. `driver_style.build_driver_style_features` inner-merges all four
     features, so if one is completely empty (as `tyre_management_index`
     was for 2026 — every stint's `avg_deg_per_lap` NULL) the whole result
     collapses to empty and `driver_service._fit_population` raises a
     misleading "Not enough lap/stint data" even though the other three
     features and 20+ drivers of laps are fine. Could fit on the surviving
     features (drop that PCA column) when exactly one is missing, and/or
     make the error name the actual gap.
  2. `useResolvedSession` picks the Driver Style page's season as "most
     recent completed race", inheriting live-timing-page semantics. A
     dedicated "most recent season with a usable style fit" resolver would
     stop a freshly-ingested, not-yet-backfilled season from breaking the
     page. Not needed once ingestion is self-sufficient (fix B below).

- **[deferred] Retrain a real 6-feature `tire_deg_wet.pkl`.** The ✅-fixed
  entry below ("WET tyre model schema mismatch") aliases `tire_deg_wet.pkl`
  to `tire_deg_inter.pkl` at model-load time as a working stopgap — a real
  WET-specific model has never existed at the 6-feature schema the rest of
  the tire_deg registry uses since the 2026-07-16 weather-feature revert. To
  retrain: run `train_models.py` against the local corpus (produces a
  6-feature WET candidate). **Manual sidecar deletion is no longer needed
  before this** — the promotion guard fix directly above (item 9, ✅ done
  2026-09-02) now force-promotes automatically on a detected schema mismatch
  against the stale 8-feature incumbent (`cv_mae=5.7906`), regardless of the
  candidate's own `holdout_mae`; the old workaround of deleting
  `production/tire_deg_wet.pkl.metrics.json` from S3 first was only ever
  needed because the guard used to compare MAE blindly. Low priority: only
  319 valid WET laps exist across the whole 2018-2025 corpus (2025 holdout
  has zero), so any retrained WET model will stay high-variance regardless —
  this removes the INTER-alias fudge, it doesn't produce a genuinely
  accurate WET model. Full analysis:
  `docs/simulator-issues-wet-model-and-position-context.md` Part A, Option 1.

- **[✅ done 2026-09-02] The model promotion guard (`train_models.py`'s
  `serialize_evaluate_and_upload`) needed a feature-schema-compatibility
  check, not just an MAE comparison.** Root cause of the WET schema-mismatch
  bug (✅-fixed entry above): `should_promote = current_holdout_mae is None
  or holdout_mae < current_holdout_mae` had no idea the kept incumbent could
  be a different feature schema than what current inference code builds —
  it "correctly" kept a model that crashed in production because nothing
  ever checked whether the two models were even comparable. Fixed: every
  sidecar now carries `n_features`/`feature_names`/`schema_source`, and a
  confirmed schema mismatch (including an unloadable incumbent `.pkl`)
  force-promotes the candidate regardless of `holdout_mae`. [[tire_deg_model]]'s
  `pipeline_feature_count`/`apply_incompatible_model_fallbacks` (item 1a) stay
  in effect as the runtime symptom-guard — this closes the promotion-time gap
  that let an incompatible model reach production in the first place. See the
  Notes entry below for the full writeup and verification. Full original
  analysis: `docs/simulator-issues-wet-model-and-position-context.md`
  Part A.6-A.7.

- **[deferred — model limitation] The tyre-degradation models have no
  track-condition input, so INTERMEDIATE/WET degradation is modelled
  identically on a dry or wet track.** `tire_deg_model.FEATURE_COLUMNS` has
  no weather / `track_status` / wet-dry feature; `compound_encoded` is the
  only compound signal. INTER/WET curves were learned from historical
  (wet-only) laps and are applied unconditionally, so a Simulator what-if
  that pits to INTER/WET on a dry track shows a fresh-tyre time *gain*
  instead of the real-world catastrophic loss (`RaceSimulationInput.wet_track`
  exists but only feeds the safety-car model, never the tyre model). No
  dry-INTER/dry-WET training data exists, so retraining alone can't fix it —
  needs either a track-condition feature (large, still extrapolating), a
  heuristic compound-vs-conditions penalty in `race_simulator`, or a
  Simulator UI guard blocking wet-compound-on-dry what-ifs. Discovered
  2026-08-29 via a NOR lap-68 dry-track pit-to-INTERMEDIATE sim that also
  surfaced the Zandvoort-R12 garbage `starting_position` / cumulative-time
  issue (see Deferred Wiring item A). Considered and deliberately left
  deferred during the 2026-08-30 fix session that closed Part A and
  mitigated B1 below (neither the heuristic penalty nor the frontend guard
  was implemented — both would need their own dedicated scoping, per that
  session's own decision). Full analysis:
  `docs/simulator-issues-wet-model-and-position-context.md` Part B.3.

- **[✅ done 2026-09-01] `telemetry_worker._persist_lap` (and
  `_persist_tire_stint`) skipped `get_engine().dispose()` whenever an
  exception propagated out of their own `async with session_factory() as
  db:` blocks — the identical shape as the bug fixed in
  `prediction_worker._run_simulation` (see the current_lap-validation
  ✅-fixed Notes entry below, and this fix's own Notes entry further down).**
  Fixed both functions the same way: moved `await get_engine().dispose()`
  into a `finally` wrapping the `async with` block. `_persist_tire_stint`
  was found to have the identical shape while fixing `_persist_lap` and
  fixed alongside on request, not left for a separate session. See the
  Notes entry below for the fix summary and verification.

- **[deferred] `driver_id_encoded` (the tyre-degradation and pit-predictor
  models' only per-driver signal) has no relationship to actual driving
  ability — it's `zlib.crc32(str(driver_id).encode()) % 1000`
  (`strategy_service._stable_code`, duplicated in `prediction_worker.py`
  and used identically inside `race_simulator.py`), a deterministic hash
  chosen only so the same driver gets the same code across calls. It
  carries zero skill/pace signal, so no model can ever learn "this specific
  driver is unusually fast/consistent" — confirmed as the likely explanation
  for Test 2's 2026-08-30 validation finding (VER's real pit stop at Spa
  2026 R10: simulator predicted `position_gain_loss=-4` over the remaining
  27 laps, VER actually gained a position in the real race — see the
  current_lap-validation ✅-fixed Notes entry below for the full trace of
  why `position_gain_loss` itself is mechanically sound but still can't
  see this).
  - **A real per-driver skill metric already exists, session-relative:**
    `driver_service._performance_vs_team_avg` (`services/driver_service.py`)
    computes each driver's mean valid lap time minus their season
    teammates' mean, same session — negative means faster than teammates.
    It's computed fresh per request (not cached long-term — see that
    module's own docstring on why), so using it as a model feature would
    need aggregating it into a stable per-driver number first (e.g. a
    rolling/season-average across many sessions), not wiring in the raw
    session-relative value directly.
  - **Fix requires, in order:** (1) a real per-driver skill feature —
    plausibly an aggregate of `_performance_vs_team_avg` across a driver's
    recent sessions/seasons, computed at training-data-export time
    (`scripts/export_training_data.py`/`scripts/train_models.py`'s
    `fetch_laps_from_db`), since it isn't currently part of any laps
    export; (2) adding that column to `tire_deg_model.FEATURE_COLUMNS` and
    `pit_predictor.FEATURE_COLUMNS` alongside (not instead of)
    `driver_id_encoded` — replacing the hash outright would remove even
    the current "same driver, same code" consistency contributed alongside
    a real feature; (3) a full retrain + promotion-guard pass for all 5
    tyre_deg models and `pit_predictor.pkl` (this is a training-corpus and
    feature-schema change, not a hot-swappable model like the WET alias
    fixed today).
  - **Effort: moderate.** Real accuracy improvement, and the underlying
    session-relative computation already exists — the work is aggregation
    + feature engineering + retrain, not inventing a new data signal from
    scratch. Good candidate for a future dedicated day, not attempted today.

- **[deferred — architectural, long-term] The Monte Carlo simulator has no
  strategic/reactive adaptation between drivers — every driver's simulated
  pit decision is independent of what any other driver (including the
  requester's own forced what-if) is doing that same lap.** Confirmed by
  code trace (2026-08-30, answering a direct question about
  `position_gain_loss`'s scoping — see the current_lap-validation ✅-fixed
  Notes entry below): `race_simulator.simulate_race`'s per-lap loop calls
  `_pit_scores` once per lap across the full `(n_sims, n_drivers)` array,
  and every driver's `pit_flags` entry is decided purely from **that
  driver's own** `tyre_age`/`predicted_life_remaining`/`gap_to_ahead`/
  `gap_to_behind`/`position` at that lap — there is no cross-driver term at
  all (no "rival X just pitted, does that change MY pit-now probability"
  signal exists in `pit_predictor.FEATURE_COLUMNS`, and nothing in
  `simulate_race`'s loop body reads one driver's `pit_flags`/compound
  decision when computing another's). This means the simulator cannot
  represent real undercut/overcut *decision-making* — it already computes
  `undercut_score`/`overcut_score` elsewhere (`strategy_service.py`,
  a genuinely-fixed 42x-vectorized feature per CLAUDE.md's Notes) as a
  static probability given a fixed pit lap, but nothing makes a *rival*
  dynamically react to the requester's simulated pit lap by pitting
  earlier/later themselves within the same Monte Carlo run.
  - **Fix requires a fundamentally different simulation architecture** —
    sequential/iterative per-lap reactive logic (each driver's pit decision
    conditioned on what other drivers did earlier THAT SAME lap or the lap
    before, within each of the 1000 simulations) instead of today's fully
    vectorized batch-across-all-drivers-and-sims approach, which is
    exactly what makes the current design fast (one batched `.predict()`
    call per model per lap, not per driver — see CLAUDE.md's "Monte Carlo
    for race simulation, not a deterministic model" and the `_undercut_
    overcut_probability` vectorization Notes entry for how much this
    codebase already leans on batching for performance). A reactive
    architecture would very likely cost real throughput, and even
    scoping it properly needs research beyond just this codebase — how
    real teams' undercut/overcut reasoning is actually modeled in the
    literature, what a tractable reactive-Monte-Carlo formulation looks
    like at this scale, and how much of that is even worth approximating
    versus a simpler heuristic layered on top of the existing vectorized
    core. **Effort: high — a dedicated session on this would need to start
    with that research, not straight to implementation; this borders on
    its own research project.** Most commercial F1 strategy tools don't
    fully solve this either. Long-term "nice to have," not a near-term
    priority — do not casually fold this into an otherwise-scoped session.

### Dependency version drift — prometheus-fastapi-instrumentator (✅ fixed Day 16)

pyproject.toml lower-bound-only pins caused a silent compatibility 
break: prometheus-fastapi-instrumentator 8.0.0 crashed on every HTTP 
request with AttributeError: '_IncludedRouter' object has no attribute 
'path' under FastAPI 0.138/Starlette 1.3.1. Fixed Day 16 by bumping 
to >=8.0.2 (GitHub issue #370, fixed in 8.0.1). For middleware/monitoring 
libraries that hook into framework internals, consider upper bounds to 
prevent silent breaks during pip install --upgrade.

### Notes

**Item A — NULL-lap cumulative-sum gap/race-time reconstruction fixed via
`LapData.session_elapsed_seconds` (✅ fixed 2026-09-02):** Full session
across 5 checkpoints (plan → schema/ingestion → backfill script → wire the
4 call sites → docs), verified against real Belgian GP and British GP 2026
data at every step, not just unit tests. See item A's own Deferred Wiring
entry above for the original bug; this Notes entry covers the fix.

- **Root cause confirmed directly, not assumed:** FastF1's `Laps.Time`
  (session clock at lap completion) is populated on 100% of lap rows across
  a 2020–2026 sample, including every row where `LapTime` (this codebase's
  `lap_time_seconds`) is NULL — `ingest_historical.py` was simply never
  capturing it. Reproduced the exact reported bug with real numbers before
  touching any code: British GP 2026 R9's `Time`-based gap between LEC and
  NOR is 1.2s (matching reality); the old `SUM(lap_time_seconds)` query
  reports 343s.
- **Schema:** migration `20260902_add_session_elapsed_seconds_to_lap_data`
  adds nullable `LapData.session_elapsed_seconds` (Float). `ingest_
  historical.py` gained `resolve_session_start` (anchors to the session's
  earliest `LapStartTime` — confirmed identical across all 22 drivers for a
  given session) and `compute_session_elapsed_seconds`, both shared,
  documented functions — `_upsert_lap_data` and the new backfill script
  call the exact same code, not duplicated logic.
- **Backfill:** `backend/scripts/backfill_lap_session_time.py`
  (`make backfill-lap-session-time`), R-sessions-only (the only sessions
  the gaps/simulator endpoints serve — FP/Q deliberately left for later),
  idempotent (skips a session with zero remaining NULL rows without even
  hitting FastF1), matches existing rows by `(driver code, lap number)`
  without creating new `Driver`/`LapData` rows. Hit and fixed a real
  SQLAlchemy 2.0 quirk along the way: a bulk `UPDATE` via bound params
  against an ORM-mapped class triggers its "bulk update by primary key"
  path and rejects a custom bindparam name — fixed by targeting
  `LapData.__table__` (Core-level) instead of the ORM entity. Ran against
  the full local corpus: **169,709 rows updated across 155 of 158 R
  sessions** (the other 3: one done manually during dev, two genuinely
  empty — `2018 R14`/`2026 R13` have zero `lap_data` rows). Verified in DB
  directly afterward: zero remaining NULL `session_elapsed_seconds` across
  all 170,822 R-session lap rows.
- **The four call sites** (`telemetry_service._compute_session_gaps`,
  `strategy_service._cumulative_race_time`, `prediction_worker
  ._cumulative_race_time`, `prediction_worker._build_race_state`) now
  prefer `session_elapsed_seconds`, falling back to the original
  `SUM(lap_time_seconds)` reconstruction only when it's NULL (a
  live-ingested/never-backfilled session, or a driver with no laps yet) —
  both cases collapse to the same `is None` check, no separate per-session
  mode flag needed. `_build_race_state` reuses its existing `position_subq`/
  `position_join` (already resolving "latest row ≤ current_lap per driver"
  for position) to also pull `session_elapsed_seconds` off the same row,
  rather than adding a third query. `_compute_session_gaps`'s `_GAPS_QUERY`
  also **dropped the `WHERE lap_time_seconds IS NOT NULL` filter entirely**
  — that filter was a second, related bug: it silently excluded a driver's
  most recent lap from consideration whenever that lap had no recorded
  time, understating their reported *current lap number*, not just their
  cumulative time.
- **Live-data verification, not just unit tests:** compared the fix's
  output against FastF1's own authoritative `session.results` for the full
  top-12 finishers of both British GP 2026 R9 and Belgian GP 2026 R10 —
  exact position-order match on both, gaps accurate to within ≤0.18s
  (versus the original bug's 343s error). Confirmed end-to-end through the
  real running `docker-backend-1` container's actual `GET /telemetry/
  {session_id}/gaps` route (not just direct Python calls) — verified the
  Redis cache write proved a fresh compute, not a stale hit.
- **A genuine, distinct limitation surfaced during this verification, not
  a defect in this fix** — see the new "No F1 penalty/post-race-
  classification data is ingested anywhere" Deferred Wiring entry above:
  `_compute_session_gaps`'s field order diverged from the true official
  classification starting at position 9 for British GP, because ANT
  received a post-race time penalty that no data source in this codebase
  captures. Confirmed the identical old `SUM(lap_time_seconds)` code would
  have produced the same mis-ranking for the same pair (computed directly:
  ANT 5070.97s vs COL 5071.94s) — not something this fix introduced.
- Verified: 9 new unit tests (prefers-elapsed / falls-back-to-sum /
  defaults-to-zero-on-double-null, per call site — chosen because the
  *existing* tests for these functions only asserted counts/membership,
  never actual gap values), 6 existing tests' mock fixtures updated to the
  new row shapes. Full `pytest backend/tests/unit/ -m unit`: **231 passed**.
  Full `pytest backend/tests/integration/ -m integration` (real
  testcontainers Postgres/Redis, including `test_alembic_migrations.py`
  exercising the new migration from base→head and downgrade/upgrade
  idempotency): **45 passed**. `ruff check`/`ruff format --check`/
  `mypy --strict` clean across the entire `backend/` tree.
- **Not done, and cannot be done yet:** Supabase (production) backfill —
  this session only ran against the local Docker Postgres.
  `session_elapsed_seconds` doesn't exist on Supabase until this branch
  merges to `main` and `cd.yml`'s `migrate` job applies the migration —
  running the backfill before that merges is not possible (the column
  isn't there to write to), not just premature. Correct sequence, a manual
  post-merge step: see `docs/runbook.md`'s "One-time: backfill
  session_elapsed_seconds on Supabase" section — (1) merge, confirm the
  migration job succeeded, (2) then run `backfill_lap_session_time.py`
  against `SUPABASE_DIRECT_URL` (same pattern as the tyre-degradation
  backfill above).

**Model promotion guard gained a feature-schema-compatibility check —
item 9 (✅ fixed 2026-09-02):** Root cause of the WET tyre-model
schema-mismatch bug (✅-fixed entry below) — `train_models.py`'s promotion
guard compared `holdout_mae` only, with no idea a kept incumbent could be a
different feature schema than what current inference code builds, so it
"correctly" kept a model that crashed in production because nothing ever
checked whether the two models were even comparable.

- **Every sidecar (`.pkl.metrics.json`) `serialize_evaluate_and_upload` writes
  now carries `n_features`/`feature_names`/`schema_source`** (`"declared"`
  for a freshly-trained candidate). New `fitted_feature_count` (in
  `train_models.py`) is the general form of [[tire_deg_model]]'s existing
  `pipeline_feature_count` — it also reads a bare `LGBMClassifier`'s
  `n_features_in_` directly (`pit_predictor` has no `named_steps`) and
  correctly returns `None` for `safety_car_model.SafetyCarModel` (no
  feature-vector concept at all — confirmed via a dedicated test asserting
  zero `.pkl` downloads for that model type even with an existing
  incumbent, since the schema check never applies to it).
- **New decision, replacing the bare MAE comparison:** no existing
  production model → promote (unchanged); the incumbent's feature schema —
  read from its sidecar, or (only when the sidecar predates this fix and
  has no schema recorded) recovered by downloading and introspecting the
  production `.pkl` directly — mismatches the candidate's, or that `.pkl`
  can't even be loaded → **force-promote regardless of `holdout_mae`**,
  logged loudly with both feature counts (new `PromotionOutcome` return
  type carries `reason="schema_mismatch"`, threaded into
  `retrain_summary.json` as `promotion_reason` and rendered in
  `train-models.yml`'s Slack/release-notes `jq` output); otherwise → the
  original MAE comparison, unchanged.
- **A legacy incumbent's `.pkl` is only ever downloaded once.** On a
  successful introspection, the recovered `n_features` is backfilled into
  its sidecar in place (`schema_source="introspected"`, no promotion, the
  model itself untouched), so the next comparison for that filename reads
  straight from the sidecar — verified directly by a two-call test
  (`test_backfills_legacy_sidecar_and_skips_second_download`) asserting
  `download_file` is called exactly once across both calls.
- **`train_all()` and `retrain_incremental.py::_promote_and_record`/
  `retrain()` both wire real `FEATURE_COLUMNS` through** —
  `tire_deg_model.FEATURE_COLUMNS` (×5), `pit_predictor.FEATURE_COLUMNS`;
  `safety_car_model.pkl` passes none, by design (no feature vector).
  `_promote_and_record`'s consumption of the new `PromotionOutcome` return
  type was itself a real fix, not just plumbing: it previously stored the
  return value directly under `summary[filename]["promoted"]`, which
  type-checks fine under that dict's `object`-typed values but crashes
  `json.dumps(retrain_summary.json)` at runtime now that the return type is
  a dataclass — confirmed via direct repro before fixing. Fixed by
  unpacking `.promoted`/`.reason` explicitly into the summary dict.
- **This is a runtime guard on the *next* promotion decision, not a
  backfill of existing production state.** `tire_deg_wet.pkl` stays aliased
  to `tire_deg_inter.pkl` in memory via [[tire_deg_model]]'s
  `apply_incompatible_model_fallbacks` (item 1a, unrelated code path, still
  in effect) until a real training run actually calls this guard and
  force-promotes a schema-correct WET candidate — see the "Retrain a real
  6-feature `tire_deg_wet.pkl`" entry above (item 8, still deferred) for
  that retrain; this fix removes the promotion-guard obstacle item 8
  previously required a manual sidecar deletion to work around, it doesn't
  perform the retrain itself. **`train-models.yml` currently fetches zero
  2026 laps** (see the escalated GitHub-Actions/FastF1 deferred item below)
  — the first real run under this guard should be triggered deliberately
  once that's resolved or the base-corpus-only outcome is consciously
  accepted, not casually.
- Verified: `pytest backend/tests/unit/test_train_models.py -m unit` (new
  file, 12 tests — 3 direct `fitted_feature_count` cases plus 9 covering
  the full decision table, including the exact 8-vs-6-feature WET shape
  force-promoting despite a *worse* `holdout_mae`) and a full `pytest
  backend/tests/unit/ -m unit` (222 passed, no regressions). `ruff check`/
  `mypy --strict` clean on all changed files (`train_models.py`,
  `retrain_incremental.py`, the new test file); `train-models.yml`'s `jq`
  change validated as well-formed YAML. Not yet verified against a real
  training run/S3 (deliberately deferred — see the `train-models.yml`
  zero-laps blocker above).

**Strategy Simulator accepted a current_lap far beyond a session's real
race distance (✅ fixed 2026-08-30):** Found during manual Checkpoint-6
verification of the WET-alias fix directly below — the repro script used
`current_lap=68` against Belgian GP 2026 R10, a 44-lap race, and the
simulation ran to completion with no error, silently simulating 24 phantom
laps that never happened. Root cause: no layer in the stack validated
`current_lap` against anything — `SimulateStrategyRequest` had no `Field`
bounds at all on `current_lap`/`remaining_laps`/`current_tyre_age`, the
`POST /simulate` route (`apis/v1/strategy.py::simulate_strategy`) had no
`db` dependency and did zero lookups before enqueueing, and
`prediction_worker._build_race_state` only ever used `current_lap` as a
`<= current_lap` filter bound — which is a no-op once `current_lap` exceeds
a session's real max ingested lap, so every other driver just silently
resolved to their real final state while the requester's own payload-forced
state drove `race_simulator.simulate_race` through `range(current_lap+1,
total_laps+1)`, laps 69-73, with no notion these laps never existed
(`safety_car_model.probability_within` only special-cases `lap_number ==
1`; the tyre models have no race-length awareness at all). Neither current
nor total laps are stored anywhere in the schema (`Race`/`Circuit`/`Session`
all lack a `total_laps` column) — the only ground truth is
`MAX(LapData.lap_number)` per session, the same proxy `_current_state`/
`_resolve_inference_context` already use elsewhere for "total laps".

- **Schema-level bounds** (`schemas/simulate_schema.py`): `current_lap`/
  `remaining_laps` gained `Field(ge=1)`, `current_tyre_age` gained
  `Field(ge=0)` (0 = fresh tyre, legitimately not `ge=1`). `_validate_pit_plan`
  extended to reject any `pit_laps` entry outside `(current_lap,
  current_lap + remaining_laps]` — previously a forced pit stop outside that
  range was silently never triggered inside `simulate_race`'s loop, with no
  error to say so; same class of "no bounds checking" gap, fixed alongside
  since it's the same validator.
- **`strategy_service.validate_current_lap(db, session_id, current_lap)`**
  (new, public): two checks — session must exist (`NotFoundError` if not,
  so a bad `session_id` fails cleanly instead of surfacing as a raw
  `NoResultFound` deep inside `_build_race_state`'s own unrelated context
  query), then `current_lap` must be at most **one lap past** the session's
  real progress (`MAX(LapData.lap_number)`, or 0 if no lap_data exists yet —
  a genuine pre-race what-if; see `test_simulate_returns_task_id`, which
  seeds zero `LapData` rows and expects `current_lap=1` to succeed, and
  which this fix's design deliberately preserves) — `ValidationError`
  (422) otherwise. "One past" allows "currently completing the very next
  lap after the last one anyone's finished"; anything further is either
  stale client state or a fabricated race length. Called from **two** places
  (folded into one function rather than two separate checks — both callers
  always want both checks together, so one call site can't forget the other):
  - `apis/v1/strategy.py::simulate_strategy` — added the route's previously
    entirely-missing `db` dependency, calls this before ever building
    `task_payload`/calling `.delay()`, so a bad request costs no Celery
    round trip at all.
  - `prediction_worker._run_simulation` — defense in depth: a caller that
    dispatches `run_race_simulation` directly (`.delay()`/`.run()`),
    bypassing the route entirely (e.g. a future replay/backfill script),
    must not be able to skip this check just by not going through the API.
- **Found and fixed along the way:** `_run_simulation`'s `await
  get_engine().dispose()` sat *after* its `try/finally` block, so it was
  silently **skipped** whenever an exception propagated out of the
  `async with session_factory() as db:` block — a latent, pre-existing gap
  that rarely mattered before (the only prior failure mode was a rare
  `NoResultFound`), but `validate_current_lap` raising is now a routine,
  expected rejection path, so the skip became reliably observable: a new
  integration test calling `run_race_simulation.run()` directly passed its
  own assertion but then crashed the test fixture's teardown with
  `RuntimeError: Event loop is closed` (a stale pooled asyncpg connection,
  bound to the crashed call's event loop, colliding with the next
  `asyncio.run()` in the same test process). Fixed by moving the `dispose()`
  call into the same `finally` block as the Redis client cleanup, so it now
  always runs regardless of how the block exits. The identical shape existed
  in `telemetry_worker._persist_lap` and `_persist_tire_stint` too — left as
  a Deferred Wiring item rather than fixed opportunistically as part of this
  unrelated change; fixed in a 2026-09-01 follow-up session (see that Notes
  entry further down).
- Verified: `pytest backend/tests/unit/test_schemas.py backend/tests/unit/
  test_strategy_service.py -m unit` (new tests: schema bounds,
  `validate_current_lap`'s 4 branches) plus `pytest backend/tests/
  integration/test_strategy_endpoint.py backend/tests/integration/
  test_race_simulation_serialization.py backend/tests/integration/
  test_live_prediction_pipeline.py -m integration` (9 passed, including two
  new route-level tests — reject beyond progress + reject unknown session,
  both asserting `run_race_simulation.delay` is never called — and one new
  worker-level bypass test calling `.run()` directly) plus a full `make
  test-unit` (206 passed). Frontend surfacing (`SimulatorPage.tsx` web +
  desktop) deferred to a later day — neither the initial-POST error path
  nor the async-task-`FAILURE` UI currently shows the new message to a
  user; the backend correctness fix stands on its own regardless.

**Frontend never surfaced validate_current_lap's rejection or a task
FAILURE's reason — SimulatorPage.tsx, all three clients (✅ fixed
2026-09-01):** The frontend gap flagged above (handoff doc
`docs/day-deferred-fixes-session2-handoff.md` item 12) — two distinct
holes, both closed:
- **Synchronous `POST /simulate` rejection:** `handleRunSimulation`'s
  `await simulateMutation.mutateAsync(payload)` had no error handling at
  all, and `setStep(3)` ran *before* the await — a 404/422 from
  `validate_current_lap` was an unhandled promise rejection, and the user
  was stranded on step 3's spinner forever (no task ever created, so
  neither `FAILURE` nor `timedOut` would ever fire to show "Try Again").
  Fixed: `mutateAsync` is now try/caught, `setStep(3)` only runs on
  success, and the rejection renders inline on step 2 via the existing
  `getApiErrorMessage` util (`role="alert"`, `text-destructive` — same
  pattern as `LoginPage.tsx`/`login.tsx`'s `serverError`).
  `handleReset` now also calls `simulateMutation.reset()`.
- **Async task `FAILURE`:** `SimulateTaskStatusResponse` carried no
  failure reason at all — step 3's `FAILURE` card always rendered a fixed
  `"Simulation failed."` regardless of why. Fixed with a new `error: str |
  None` field, populated by `apis/v1/strategy.py::get_simulation_result`:
  Celery's result backend reconstructs the real exception instance on
  `FAILURE` (confirmed against a real `celery.backends.redis.RedisBackend`,
  not assumed — `task_serializer="json"` still round-trips a known,
  importable exception class faithfully, including an `F1StrategyError`
  subclass's `.message`), so `isinstance(exc, F1StrategyError)` gates what
  gets echoed: a known rejection's own `.message` passes through verbatim,
  anything else becomes a fixed generic string with the real exception
  logged server-side — this route is unauthenticated (unguessable task
  UUID), so it must never leak an arbitrary internal exception's text, same
  policy as `unhandled_error_handler` for every other route.
- **Ported to all three clients**, not just web + desktop as the handoff
  doc scoped: `mobile/app/simulator.tsx` had the identical gap (bare
  `mutateAsync`, hardcoded `"Simulation failed."`) — found while reading
  the file, not in the original handoff doc, and fixed alongside using the
  same `getApiErrorMessage`/`role="alert"` pattern already established in
  `mobile/app/(auth)/login.tsx`.
- Verified: `backend/tests/integration/test_strategy_endpoint.py` gained
  two new tests (`F1StrategyError` pass-through, generic-exception
  safe-message) against a real eager-Celery + real Redis result backend —
  full file (9 tests) and `ruff`/`mypy --strict` clean. New
  `web/src/__tests__/SimulatorPage.test.tsx` (2 tests) covers both frontend
  paths; full web suite (28 tests across 9 files) and `tsc`/`oxlint` clean.
  Along the way, `web/src/test/setup.ts` gained a `scrollIntoView` stub —
  jsdom has none, and Radix `Select` calls it internally on mount; this is
  the first test in the codebase to interact with one, so the stub is
  reusable infrastructure, not scoped to this fix alone. `desktop`/`mobile`
  have no test runner (see their own sync-protocol docs) — verified via
  `tsc --noEmit` only, clean on both, consistent with how the rest of each
  file is already verified.

**`telemetry_worker._persist_lap` (and `_persist_tire_stint`) skipped
`get_engine().dispose()` on exception (✅ fixed 2026-09-01):** The sibling
gap flagged when `prediction_worker._run_simulation`'s identical bug was
fixed (item 1d above) — deferred at the time, picked up in the same
2026-09-01 follow-up session as the frontend Notes entry directly above.
- **`_persist_lap`:** same fix as `_run_simulation`'s — `await
  get_engine().dispose()` moved into a `finally` block wrapping the `async
  with session_factory() as db:` block, so it now always runs regardless of
  how the block exits. `_publish_lap_completed(lap)` stays outside the
  `try/finally`, unreachable on any exception, unchanged from before.
- **`_persist_tire_stint`:** the identical bug shape in the same file
  (`record_tire_stint`'s persist function) — not originally scoped, found
  while fixing `_persist_lap`, fixed alongside on request with the same
  pattern, same session.
- New tests: `backend/tests/unit/test_telemetry_worker.py` (new file), 4
  tests — one raise/success pair per function. Each "raise" test forces an
  exception inside the `async with` block via a minimal `_FakeSession`
  stand-in (a purpose-built async-context-manager fake, since no existing
  fixture stood in for `session_factory() as db` itself) and asserts
  `dispose()` still runs and the original exception still propagates —
  mirroring how item 1d's bug was originally *discovered* (a real
  integration test hitting a crashed fixture teardown), rather than
  reasoned about abstractly.
- Verified: `ruff check`/`mypy --strict` clean on both changed files; new
  test file 4/4 passed; full `backend/tests/unit/ -m unit` 210 passed (206
  pre-existing before this item + 4 new), no regressions.

**WET tyre model schema mismatch — Strategy Simulator crash on any WET
compound (✅ fixed 2026-08-30):** `docs/simulator-issues-wet-model-and-
position-context.md` Part A. Production `tire_deg_wet.pkl` was an 8-feature
model (a 2026-07-10 weather-experiment leftover — see Data Quality Notes)
while soft/medium/hard/inter are all 6-feature; the 2026-08-03 retrain's
6-feature WET candidate never beat the stale incumbent's `cv_mae=5.7906`
(the MAE-only promotion guard has no schema awareness — see the new
deferred entry above), so the incompatible model stayed in production and
crashed `race_simulator._tire_deg_predictions` with `ValueError: X has 6
features, but StandardScaler is expecting 8 features` whenever a WET
compound appeared, killing the entire Monte Carlo task. Fixed with the
doc's recommended 2b+2a:
- **2b (alias):** `tire_deg_model.py` gained
  `INCOMPATIBLE_TYRE_MODEL_FALLBACKS = {"tire_deg_wet.pkl":
  "tire_deg_inter.pkl"}`, `pipeline_feature_count()` (reads a fitted
  pipeline's `StandardScaler.n_features_in_`), and
  `apply_incompatible_model_fallbacks()` (aliases any registry entry whose
  fitted feature count doesn't match `len(FEATURE_COLUMNS)` to its fallback,
  logging a warning). Called once at the end of both `_load_models()`
  copies (`strategy_service.py`, `prediction_worker.py`) — every consumer
  (`_pipeline_for_compound`, `_run_inference`, `_run_simulation`'s
  `tire_deg_pipelines` dict) now gets the INTER pipeline for WET requests.
- **2a (backstop):** `race_simulator._tire_deg_predictions` independently
  checks each compound group's pipeline feature count before predicting,
  and wraps the `predict()`/`predict_life_remaining_batch()` call in
  `try/except`; either failure mode now skips just that compound group
  (`delta=0`, `life=MAX_LOOKAHEAD_LAPS`) with a logged warning instead of
  raising out of `simulate_race` — a permanent guard against any *future*
  schema drift on any compound, independent of the alias above.
Verified via `pytest backend/tests/unit/test_tire_deg_model.py
backend/tests/unit/test_race_simulator.py backend/tests/unit/
test_strategy_service.py backend/tests/unit/test_prediction_worker.py -m
unit` plus a full `make test-unit` run (193 passed) — new coverage includes
direct `_tire_deg_predictions` tests (mismatched shape, raising `predict()`,
compatible pipeline), a mixed-field `simulate_race` test, and a wiring test
per `_load_models` copy asserting the alias actually applies. `ruff` +
`mypy --strict` clean on all changed files. Not yet verified against a real
`docker compose restart worker` + live repro (Checkpoint 6, pending).

**Strategy Simulator auto-picking a partially-live-ingested session (B1
mitigation, ✅ fixed 2026-08-30 — deep fix still deferred):**
`docs/simulator-issues-wet-model-and-position-context.md` Part B1.
`GET /strategy/last-ingested-session` (`strategy_service
._fetch_last_ingested_session`) picked the newest-`race_date` R session
with any `lap_data`, with no `status` filter — on a local DB this resolved
to Dutch GP 2026 Round 12 (Zandvoort, `status="scheduled"`, a partial Day
36 live-ingestion dry run) instead of a real completed race. That
session's `lap_data.position` is NULL for every row and different drivers
are missing different numbers of laps, so the Simulator's `_build_race_
state` fell through to a meaningless `starting_position` fallback
(`len(latest_laps)` for every driver) and a non-comparable cumulative-time
ranking — producing nonsense like "+16 positions" for the actual race
leader. Root cause is Deferred Wiring item A (the NULL-lap cumulative-sum
bug) — **not fixed here**, per explicit scope decision; this is the
doc's cheap targeted mitigation only. Fix: added `Race.status ==
"completed"` to `_fetch_last_ingested_session`'s query — the picker now
only ever resolves to a real `ingest_historical.py` ingest. On this local
DB the effective default moves from Zandvoort R12 → Belgian GP 2026 R10;
on Supabase (all 3 curated 2026 races already `status="completed"`) this
is a no-op. Verified via a new unit test capturing the actual query object
and asserting its compiled SQL carries `status = 'completed'` (a mocked
return value alone can't prove the filter is real). The stale Redis key
`f1:strategy:last_ingested_session` (TTL 86400s, written before this fix)
was manually deleted from the local `docker-redis-1` container so the new
query takes effect immediately rather than after a 24h TTL — confirmed via
`EXISTS` returning `0` post-delete.

**Driver Style page empty for season 2026 (✅ fixed 2026-08-29):** The
Driver Style radar (`StyleRadar.tsx` → `GET /drivers/{id}/analysis` →
`driver_service._fit_population` → `driver_style.build_driver_style_features`)
started returning `404 "Not enough lap/stint data to build driver-style
profiles for season 2026"`. Season is not hardcoded — it follows the
`session_id` `useResolvedSession` passes, which is "most recent *completed*
race"; once British GP 2026 R9 was ingested (Day 41, `status="completed"`)
that became a 2026 race, and Day 43's curated ingestion (Spa R10, Canada
R5) added more. 2026 had ~4.4k laps / 228 stints / 20+ drivers — plenty —
but **every 2026 `tire_stints` row had `avg_deg_per_lap = NULL`**, because
`ingest_historical.py._upsert_tire_stints` always wrote `None` and relied
on a separate manual pass, `backend/scripts/backfill_tire_data.py`
(`make backfill-tire-data`), which had been run for 2018-2025 but never for
2026. Empty `avg_deg_per_lap` → `compute_tyre_management_index` returns 0
rows → the four-feature `inner` merge in `build_driver_style_features`
collapses the whole result to empty. The other three style features were
fine. Not a regression from that day's dot-animation work (unrelated
files). Fixes:
- **A (data):** ran `backfill_tire_data.py --season 2026` against the local
  DB — 141/228 stints updated, 87 skipped (<2 valid timed laps, normal).
  `GET /drivers/{id}/analysis` for the Belgian GP 2026 session now returns
  a full profile. Also applied to production Supabase the same day (141
  updated, matches local for the 3 curated races) — see the "[✅ done
  2026-08-29] Tyre-degradation backfill applied to Supabase" entry in
  Deferred Wiring above.
- **B (recurrence):** `_upsert_tire_stints` now computes `avg_deg_per_lap`
  inline from each stint's valid (`IsAccurate`) timed laps, reusing
  `backfill_tire_data._regression_slope` (same filter, same `<2 laps →
  None` guard). `backfill_tire_data.py` stays as the repair tool for
  already-ingested data.
- **C / D skipped** (partial-feature fitting; dedicated season resolver) —
  see the Deferred Wiring "[deferred — hardening]" entry.
Round 12 (Zandvoort) 2026 stints stay NULL — it was live-ingested Day 36
(laps 1-8 missing, `is_valid` unreliable) and is `status="scheduled"`, so
`useResolvedSession` never lands on it and the 2026 fit succeeds without
it. Separate pre-existing data-quality issue, out of scope.

**Real DB alerts wired + StrategyPrediction history endpoint (✅ fixed
2026-08-26, Day 42):** Two Day 41 findings closed together.

- **Alert system disconnect.** Two independent alert pipelines existed:
  `workers/alert_worker.py` (pubsub-triggered off `f1:predictions:*`,
  `pit_probability >= 0.5`, FCM push only — running, but untestable in this
  env since Firebase was never configured) and `services/alert_service.py`'s
  `evaluate_threats`/`dispatch_alert` (writes real `Alert` DB rows +
  `f1:alerts:{session_id}` publish, exactly what `GET /alerts` and the web
  `RecentAlertsFeed`/`AlertsPage` read) — but nothing called `evaluate_threats`
  anywhere outside its own unit test. Fixed by calling
  `alert_service.evaluate_threats(db, async_redis_client, session_id)` from
  `workers/prediction_worker.py`'s `_persist_and_publish`, right after each
  `StrategyPrediction` commits — fires identically for a real live session and
  a `replay_pipeline.py` run, no replay-specific code. `alert_worker.py` was
  deliberately left untouched and the two paths were NOT merged — different
  signal (`pit_probability` vs. `undercut_score`), different delivery (FCM vs.
  DB+WS), forcing a shared code path would have meant touching the
  still-unconfigured FCM path just to reuse a one-line threshold check.
  Because `evaluate_threats` re-evaluates every track-position-adjacent pair
  in the whole session on every call (not just the driver whose prediction
  just committed), and one Celery task fires per driver per lap, the naive
  wiring would re-dispatch the same alert ~20x per lap round. Added a Redis
  `SETNX`/`EX` dedup guard (`UNDERCUT_ALERT_DEDUP_TTL_SECONDS = 60`, keyed per
  `session_id:trailing_driver:ahead_driver:alert_type` — same pattern as Auto
  Race Detection's dedup lock) directly in `evaluate_threats`, short enough
  (well under a real F1 lap) that a genuinely-persisting threat still re-fires
  on the next lap rather than being suppressed forever. Verified live via
  `replay_pipeline.py` against British GP 2026 Round 9 (110 events, real
  `Alert` rows written, `GET /alerts` returns them, dedup confirmed holding
  ~11 redundant re-checks per 60s window while still re-firing once the TTL
  expired on a persisting threat). See the stale-score dedup entry above
  (still open) for a related gap this did NOT fix.
- **StrategyPrediction history gap.** `StrategyPrediction` had no
  `lap_number` column, so there was no way to serve "predictions over time"
  for a driver — `/overview` only ever computed live/current state. Added
  migration `20260826_add_lap_number_to_strategy_predictions` (nullable
  `lap_number` + composite index `(session_id, driver_id, lap_number)`;
  existing pre-migration rows stay permanently NULL — no way to backfill
  which lap they were predicted for), populated going forward by
  `prediction_worker._persist_and_publish` from the same lap-completion
  context already driving the rest of the prediction. New
  `GET /strategy/{session_id}/{driver_id}/history` (see API Versioning list)
  returns every persisted prediction for one driver, ordered `lap_number ASC
  NULLS LAST, predicted_at ASC` (so pre-migration rows sort after all
  lap_number-having rows, oldest-first within each group) — supplementary to
  `/overview`, not a replacement; deliberately uncached (a cache-aside TTL
  would show a stale, non-growing list during exactly the live-progression
  use case this endpoint exists for). Response field `predicted_pit_lap` is
  renamed from the model's `optimal_pit_lap` at the API boundary only — no
  model/column rename. Verified live: fresh replayed laps (`lap_number` 1, 2)
  sort first; all of Checkpoint 1's earlier NULL-`lap_number` rows correctly
  sort after via `NULLS LAST`.

**`_undercut_overcut_probability` vectorized (✅ fixed 2026-08-25):**
Discovered during Day 41 full-pipeline replay testing (`replay_pipeline.py`
against British GP 2026 Round 9): `run_strategy_prediction` Celery tasks were
taking 28-87s each — 10-100x slower than expected. Profiled directly inside
the worker container: `_resolve_undercut_overcut` (calls both
`get_undercut_score` and `get_overcut_score` per task) accounted for 36.563s
of a 38.162s task total — model loading (in-memory cache hit), DB context
resolution, and ML inference combined were ~1.5s. Root cause:
`strategy_service._undercut_overcut_probability`'s `UNDERCUT_MONTE_CARLO_
SIMS=200`-iteration Python loop called `pipeline.predict()` 3 times per
iteration — 600 unbatched calls per invocation, ~17.5ms fixed overhead each
(confirmed via micro-benchmark: 600 individual tiny `predict()` calls took
10.5s versus 0.067s for the identical total workload batched into one call —
157x, same hardware, same load — ruling out S3/model-loading cost,
`@cacheable` lock contention (impossible here regardless: `--pool=solo`
never runs tasks concurrently, so there is no real lock contention to begin
with), and Docker/WSL2 resource pressure as causes; `docker stats` showed
0.10% CPU at idle with no container CPU/memory limits set).

The deterministic `_project_stint_delta` term for each of the three stint
segments (now/stay_out/fresh) does not vary across the 200 simulations at
all — only the Gaussian noise term does — so the fix projects each segment
**once** (3 total `predict()` calls, not 200) and vectorizes only the noise
draws with plain numpy (`_sampled_noise`, replacing the old per-draw
`_sampled_stint_delta`, removed as dead code — no other callers).
Mathematically equivalent to the original: same noise distribution (mean 0,
`LAP_TIME_NOISE_STD_SECONDS * sqrt(n_laps)`), verified by running both
implementations 30 times each at a near-50/50 borderline scenario (the
original `rng = np.random.default_rng()` was never seeded/reproducible
call-to-call to begin with, so exact bit-for-bit equality was never a
property to preserve) — old mean=0.5023 vs new mean=0.5165 probability, old
mean=0.0039s vs new mean=0.0251s projected gap, both differences well within
expected Monte Carlo sampling noise between two independently-drawn
200-sample runs.

Measured end-to-end on the same real driver/lap/session profiled before the
fix: `_resolve_undercut_overcut` **36.563s → 0.871s** (42x), task total
**38.162s → 2.101s**. `make test-unit`: all 133 tests pass, including
`test_undercut_returns_positive_when_gap_favourable`.

**DB connection pool exhaustion (✅ fixed 2026-07-30):**
`core/database.py`'s hardcoded `pool_size=10, max_overflow=20` (30 total
connections) was sized far too small for race-day concurrency — see the
Day 18 500-user load test that originally surfaced this (493 QueuePool
timeouts, cascading into `/overview` 500s and a WS `websocket.accept`
bug — full history in the removed Deferred Wiring entry this replaces).
Fixed via a targeted fix pass, not guessed: `pool_size`/`max_overflow` are
now configurable via new `db_pool_size`/`db_max_overflow` fields on
`DatabaseSettings` (`core/config.py`), read by `core/database.py`'s
`get_engine()` instead of hardcoded literals. Defaults stay at `10`/`20`
(worker's behavior is unchanged — no evidence of worker-side DB
contention in any run); `docker-compose.yml`'s `backend` service alone
sets `DB_POOL_SIZE=20`/`DB_MAX_OVERFLOW=40` (cap 60), since the load-test
evidence (all logged `QueuePool` timeouts were from `backend-1`, zero
`/simulate` failures) implicated only the backend's pool, not the
worker's. Postgres's `max_connections` was also bumped `100 → 200`
(`docker-compose.yml`'s `postgres` service `command`), confirmed via
`SHOW max_connections;`, giving headroom for backend(60) + worker(30) +
exporter/admin(~10) ≈ 100 of 200.

QueuePool timeout progression across fixes: **493** (Day 18 500-user
baseline, pre-WS-fix) → **16** (2026-07-30 500-user re-run, post-WS-fan-out-fix,
same pool config) → **0** (2026-07-30 100-user verification run, post-pool-fix
— see `docs/load_test_results.md`'s 2026-07-30 entry). The 16→0 drop is
this fix; the 493→16 drop was the WS fan-out fix's side effect (faster
session turnover meant DB sessions weren't held hostage behind Redis
backpressure).

**`/strategy/simulate` enqueue latency — `broker_pool_limit` was a red
herring, real cause fixed (✅ resolved, confirmed 2026-07-28):** Raising
Celery's `broker_pool_limit` 10→50 (`workers/celery_app.py`) did not fix
~12s median enqueue latency at 100 concurrent users — a re-run showed no
improvement. Real cause: `apis/v1/strategy.py`'s `simulate_strategy` passed
`None` as the executor to `loop.run_in_executor(None, run_race_simulation.delay,
...)`, using asyncio's default `ThreadPoolExecutor` (capped at
`min(32, cpu_count+4)` = 20). Fixed with a dedicated
`_SIMULATE_ENQUEUE_EXECUTOR` (50 workers) — isolated (WS-free) tests showed
p50 630-2400ms, down from ~12-14s. A combined-load re-run then showed the
same symptom recur (~12s p50) via a different mechanism — Redis's
single-threaded command queue backing up under the WS telemetry fan-out's
Nx-redundant-per-event GETs (see "WS telemetry broadcast fan-out
redundancy" below). Confirmed resolved once that landed: the 2026-07-28
100-user combined-load re-run showed enqueue latency back to p50=1900ms,
matching the isolated fix's range.

**export_training_data.py one-time base corpus export (✅ completed 2026-07-30):**
Ran once against the local Docker Postgres per the Deferred Wiring action item:
exported 163,623 lap rows and 8,271 stint rows (2018-2025) and uploaded to S3:
- `s3://f1-strategy-models/training-data/base/laps.parquet` (1.0 MB)
- `s3://f1-strategy-models/training-data/base/stints.parquet` (27 KB)

Upload confirmed via a direct `list_objects_v2` check against the bucket (AWS
CLI isn't installed in this environment). `train-models.yml`'s
`workflow_dispatch` is now unblocked — the CI workflow reads this base corpus
from S3 on every training run. Re-run only if the base corpus changes (new
historical seasons added).

**users.fcm_token (✅ completed Day 10):**
- Migration added: `20260711_add_fcm_token_to_users.py`
- User model updated with `fcm_token: Mapped[str | None]`
- `PUT /auth/fcm-token` endpoint added for mobile clients
- Note: fcm_token intentionally NOT included in UserResponse —
  no need to echo device token back in every user payload

**WeatherData live stream (✅ wired, weather improvement pass):**
ingest_live_session.py now parses AirTemp/TrackTemp → Redis 
f1:{season}:{round}:weather:latest (TTL 60s). strategy_service 
_resolve_weather reads live key with DB fallback.

**`/races/current` negative-result caching + single-flight (✅ fixed pre-Day-14):**
`race_service._fetch_current_race` raised `NotFoundError` when the
Ergast-resolved season/round hadn't been ingested yet (true for 2026 right
now — ingestion stops at the 2025 holdout set). Since the old `@cacheable`
wrapper only wrote to cache on a successful return, a raised exception
meant this negative result was never cached — every request paid the full
external Ergast round trip (confirmed as the root cause of
`/races/current`'s p50=13000ms, 100% uncached, in the Day 13 baseline).
Fixed: `_fetch_current_race` is no longer wrapped in `@cacheable`;
`get_current_race` hand-rolls cache-aside with two TTLs —
`CURRENT_RACE_TTL_SECONDS` (300s) for a real race,
`CURRENT_RACE_NOT_FOUND_TTL_SECONDS` (60s) for "no current race". The Day
13 re-run showed this alone wasn't enough — `RaceDayViewerUser.on_start()`
calls this once per user and all ~34 users ramped up within the same ~10s
window, hitting the cold key before any one of them finished its ~13s
Ergast call — the same stampede shape `cacheable()`'s lock already guards
against, just on a hand-rolled path. Fixed by adding a
`cache_service.cache_lock` single-flight lock (same tuning as `cacheable`)
directly into `get_current_race`'s manual cache-aside. Confirmed in code:
`race_service.py`'s `get_current_race` now acquires the lock, re-checks
cache after acquiring (in case another caller populated it while waiting),
and falls back to computing independently if `blocking_timeout` elapses.

**Cache stampede single-flight lock (✅ fixed Day 13):**
`cache_service.cacheable`'s cache-aside decorator had no single-flight
protection — on a miss, every concurrent caller independently re-ran the
full decorated function instead of one computing and the rest reusing the
result. Confirmed via Day 13 baseline load test (`-u 100 -r 10 --run-time
2m`) on `/strategy/{session_id}/overview`: p50=55ms (clean hits) alongside
p95=15000ms/p99=17000ms/max=19000ms and 2 `RemoteDisconnected` failures
(clustered recomputation at each TTL rollover). Fixed by adding a
Redis-lock single-flight (`cache_service.cache_lock`) around the miss
path — losers block on the lock (not busy-polling) and re-read cache once
it releases. Verified directly: 5 concurrent requests against a cold key
now produce exactly one Redis write instead of 5 redundant computations,
and the 2 `RemoteDisconnected` failures are gone in the post-fix re-run
(0/478). Lock timeouts tuned to 40.0s/40.0s — the first attempt
(`20.0`/`20.0`) was barely above `get_competitor_predicted_strategy`'s own
~16-17s uncontended runtime, so under a concurrent burst every waiter's
`blocking_timeout` elapsed at essentially the same moment the winner
finished and they fell through to recomputing independently anyway (no
benefit at all). **Does not address the underlying 16-17s
single-computation cost** — see Deferred Wiring's "Cache stampede fix does
not address the underlying 16-17s compute floor" bullet.

**Redis cache hit rate under burst ramp-up (accepted operational
trade-off, not a pending code fix):**
Even with the single-flight lock, cache hit rate drops from ~88% to ~5%
when all users arrive within the same 10-second ramp window (before any
cache is populated). `warm_strategy_cache.py` addresses strategy
predictions but not `/races/current` or other endpoints. Mitigation: run
`warm_strategy_cache.py` before load test/race day start, and consider
increasing TTLs for endpoints whose data changes infrequently (races list
at 86400s is good; `/races/current` at 300s could be higher during an
active race weekend). This is an operational/deployment concern, not a
code bug — no fix is planned against it.

**DRS decoding (✅ fixed pre-Day-14B):**
_decode_car_channels now maps DRS channel values to proper status 
strings: {0: "off", 8: "available", 10: "enabled", 14: "open"}, 
fallback "unknown" for unrecognized codes. LapCompletedEvent.drs 
changed from bool | None to Literal["off","available","enabled",
"open","unknown"] | None.

**run_race_simulation Celery serialization (✅ completed pre-Day-14C):**
confidence_interval's Python tuple was suspected to become a JSON array
through Celery's result backend (task_serializer/result_serializer="json",
workers/celery_app.py) with the Pydantic v2 round-trip coercion back to
tuple untested against a real ML pipeline. Verified via
tests/integration/test_race_simulation_serialization.py: runs the real
run_race_simulation task body (stubbed ML models, real race_simulator.py
Monte Carlo loop) to get a genuine confidence_interval tuple, round-trips it
through a real celery.backends.redis.RedisBackend (store_result/
get_task_meta, not eager mode, which would skip serialization entirely),
confirms the tuple becomes a JSON list on the wire as expected, then
confirms SimulateStrategyResponse.model_validate(...) — the same call
apis/v1/strategy.py's get_simulation_result makes — coerces it back to a
tuple with the original values intact. No schema change was needed;
Pydantic v2's tuple validator accepts any sequence.

**teams/driver_contracts seeding (✅ completed pre-Day-14C):**
Both tables were empty — no ingestion script populated them, so `GET
/drivers` never returned team or contract info. Fixed via
`backend/scripts/seed_teams.py` (`make seed-teams`): a hardcoded, confirmed
2026 grid (11 teams including the new Cadillac entry, 22 drivers).
Upsert-or-create on both upstream tables, not just the join: any roster
driver code missing from `drivers` (e.g. Arvid Lindblad, a 2026 rookie with
no prior FastF1 session to have been ingested from) is inserted before its
`DriverContract` row, same for any missing `Team`. `driver_contracts` has no
DB-level unique constraint on `(driver_id, season)`, so duplicate-avoidance
is done at the application level (existing-pairs set checked before insert),
same convention as `seed_circuits.py`'s skip-by-name set — confirmed
idempotent via a second run (0 inserts). Verified live against the local
Docker Postgres: `GET /drivers` returns correct `team`/`contracts` for all
22 rostered drivers.

**prediction_worker.py pit_predictor feature array + undercut/overcut wiring (✅ completed, pre-Day-13 fix pass):**
- tire_deg feature vector was confirmed already fixed (8 columns, done prior to
  this pass) — no change needed there.
- pit_predictor now gets its real 8-column vector
  (`pit_predictor.FEATURE_COLUMNS`): `predicted_life_remaining` via
  `tire_deg_model.predict_life_remaining_batch`, `safety_car_probability` via
  the loaded `safety_car_model.pkl`'s `.probability_within(...)`, and
  `gap_to_car_ahead`/`gap_to_car_behind`/`position` from a new
  `_resolve_position_context` helper (latest-`LapData`-per-driver query,
  ordered by position, same pattern as `_build_race_state`/
  `alert_service._latest_positions`).
- `undercut_score`/`overcut_score` now call `strategy_service.get_undercut_score`/
  `get_overcut_score` for real, against the car immediately ahead/behind in track
  position respectively (matching `alert_service.evaluate_threats`' existing
  assumption about what `undercut_score` means). `ModelNotLoadedError` is caught
  per-call and falls back to `0.0` with a logged warning; leader/last-car have no
  target and also fall back to `0.0`.
- Worker → service import (`from backend.services import strategy_service` in
  `prediction_worker.py`) is intentional and was checked for cycles: nothing
  under `backend/services/` imports `backend/workers/`, so this is a one-way
  dependency, not a violation of the "services must not import other services"
  rule (that rule is about services importing services).

**Strategy endpoint authentication (✅ fixed pre-Day-22 fix pass):**
All four business-logic strategy routes — `POST /simulate`, `GET pit-window`,
`GET undercut`, `GET overview` — now require `Depends(get_current_user)`,
matching the pattern already used in `alerts.py`/`auth.py`. Originally scoped
to just `POST /simulate` and `GET pit-window`; extended to all four during
the fix pass since `/overview` is the single most compute-expensive endpoint
measured (16-17s cold) and leaving it public while locking the other three
would have been an inconsistent gap in the same file. `GET
/simulate/{task_id}` stays unauthenticated — it's a cheap Celery result
lookup keyed by an unguessable task UUID, not a computation itself.
`tests/integration/test_strategy_endpoint.py` updated to use the
`authenticated_client` fixture.

**Load-test harness account-pool-size formula (✅ fixed pre-Day-22 fix pass):**
`tests/load/locustfile.py`'s `_target_pool_size` no longer caps at a flat 30
regardless of population. Pool size now scales with `num_users`, sized so
that even an account unlucky enough to back only the heaviest user type
(RaceDayViewerUser, up to ~15/min) stays under half of `core/rate_limit.py`'s
60/min authenticated bucket — see `_MAX_SIMULATED_USERS_PER_ACCOUNT`'s
derivation in the module for the exact math.

**WS telemetry broadcast fan-out redundancy (✅ fixed, 2026-07-28):**
Replaced the one-`pubsub.listen()`-loop-per-connection model in
`backend/apis/v1/telemetry.py` with a single shared `_SessionBroadcaster`
per `session_id`: one instance is created on the first subscriber and torn
down on the last disconnect (a module-level `_broadcasters` registry
guarded by one `asyncio.Lock` — the same per-session-singleton lifecycle
originally scoped, not a per-request dependency). Its one `pubsub.listen()`
loop calls `get_live_car_channels` exactly once per lap-completion event,
builds one `TelemetryStreamMessage`, and fans it out to every connection
currently registered on that broadcaster — eliminating the Nx-redundant-
per-event Redis GET this entry originally tracked. The broadcaster owns its
own Redis client, built from the connecting request's `connection_pool`
rather than its DI-scoped client, since that specific client can be
`aclose()`'d by a *different* viewer's disconnect while the broadcaster
itself is still serving other connections. Per-connection disconnect
detection (`_watch_for_disconnect`) is unchanged — only the
listen+enrich+send path moved from per-connection to per-session. A
per-connection `websocket.send_text()` inside the shared loop is wrapped in
its own try/except so one dead connection can't break delivery to the rest.

Verified:
- `tests/load/ws_load_test.py --connections 200 --messages 50 --rate 20`:
  50/50 messages delivered per connection (was ~24/50), p50=31ms (was
  ~2200ms), p95=47ms, p99=63ms, max=78ms, 0 connection failures — beats the
  original clean 20-connection baseline (p50 16ms, p99 47ms).
- Confirmed under combined load: 100-user Locust run (`-u 100 -r 10
  --run-time 2m`, `replay_publisher.py --rate 5`) — 3090 WS messages
  delivered, 0 failures, p99=94ms. `POST /strategy/{session_id}/simulate`
  enqueue latency was back to p50=1900ms (matching the isolated
  dedicated-executor fix's 630-2400ms range), not the ~12,000ms
  combined-load regression this same redundancy previously caused via
  Redis's single-threaded command queue backing up. See
  `docs/load_test_results.md`'s 2026-07-28 entry for the full breakdown.
- New regression test: `test_ws_fans_out_from_one_shared_broadcaster` in
  `tests/integration/test_websocket.py` — two simultaneous connections to
  one session_id, one publish, asserts both receive the identical envelope
  and `get_live_car_channels` is called exactly once.
- `core/redis_client.py`'s `max_connections=250` was sized for the old
  N-pubsub-connections-per-session model; now only one pubsub connection is
  pinned per active session regardless of viewer count, so this ceiling has
  headroom to spare. Left unchanged — no evidence yet it needs lowering,
  and lowering it isn't necessary for correctness.

**SENTRY_DSN never reached a running process (✅ fixed Day 24):**
`.env` had a real `SENTRY_DSN` since Day 12, but neither
`infra/docker/docker-compose.yml`'s `backend`/`worker` `environment:`
blocks nor the Helm chart's Secret/ConfigMap ever passed it through — so
`AppSettings.sentry_dsn` was always `""` and `main.py`'s lifespan never
called `sentry_sdk.init()`, in both docker-compose and every Kubernetes
deployment to date. Only surfaced when a Day 24 smoke test tried to
verify Sentry actually receives errors. Fixed: `SENTRY_DSN:
"${SENTRY_DSN:-}"` added to both compose services;
`infra/k8s/create-secrets.sh` now includes `SENTRY_DSN` in the Secret it
creates (flows to both Deployments automatically via their existing
`envFrom.secretRef`, no template changes needed). Added a permanent
ops-only `GET /api/v1/debug/trigger-error` route (`main.py`) — 404s when
`ENVIRONMENT=production` — for verifying Sentry wiring after any future
deploy without needing a real bug. Verified live: event landed in
`arhaanali.sentry.io` within 3 seconds, correct project, correct
`environment` tag.

**`create-secrets.sh` was sourcing the wrong DB/Redis URLs for a
Kubernetes Secret (✅ fixed Day 24):** `.env`'s own `DATABASE_URL`/
`TIMESCALE_URL`/`REDIS_URL` point at docker-compose's local Postgres/Redis
(`localhost`) — they're for docker-compose's own `backend`/`worker`
services, not for anything reaching Supabase/Upstash. The real cloud
endpoints live in separate `SUPABASE_DATABASE_URL`/`SUPABASE_DIRECT_URL`/
`UPSTASH_REDIS_URL` vars (added Day 23). `create-secrets.sh` read the
former, so re-running it against the current `.env` silently wrote
`localhost` URLs into the Kubernetes Secret — invisible until a pod
actually restarted and picked them up (secrets aren't hot-reloaded),
at which point it failed with `Connection refused` to `localhost:5432`/
`6379` from inside the cluster network. Fixed: the script now builds
`DATABASE_URL`/`TIMESCALE_URL` from `SUPABASE_DATABASE_URL` (with the
`postgresql://` → `postgresql+asyncpg://` conversion `cd.yml`'s migrate
job already does) and `REDIS_URL` from `UPSTASH_REDIS_URL` directly. The
now-obsolete `--rewrite-localhost` flag (for reaching docker-compose's
Postgres/Redis via `host.docker.internal`) was removed along with it,
since a Kubernetes pod now always talks to the real Supabase/Upstash
endpoints — there is no longer a "local-only" DB/cache target for it to
reach. See [[docs/runbook.md]]'s Secret rotation procedure, corrected to
match.

**asyncpg + Supabase's transaction-mode pooler → intermittent
`DuplicatePreparedStatementError` (✅ fixed Day 24):** Once
`create-secrets.sh` above was fixed to actually point Kubernetes pods at
Supabase, backend pods started failing `/health` — some only at startup
(self-recovered on retry), one persistently on every request. Root cause:
`SUPABASE_DATABASE_URL` (port 6543) is PgBouncer in transaction-pooling
mode, which is incompatible with asyncpg's default prepared-statement
caching — a connection can be handed to a different session mid-cache,
producing `asyncpg.exceptions.DuplicatePreparedStatementError`. This is
asyncpg's own documented PgBouncer caveat, not app-specific. Fixed:
`core/database.py`'s `get_engine()` now passes
`connect_args={"statement_cache_size": 0}` to `create_async_engine()`,
unconditionally (harmless against docker-compose's local Postgres too,
which has no pooler in front of it). Verified: 5 consecutive `/races`
calls with zero errors post-fix, versus reproducing on ~1 in 3 fresh pods
pre-fix.

**Celery + Upstash's `rediss://` → hard crash at worker boot (✅ fixed Day
24):** Once real Upstash traffic hit the worker (same Day 24 fix pass
above), every worker pod crash-looped immediately:
`ValueError: A rediss:// URL must have parameter ssl_cert_reqs...`.
Celery's redis transport (`celery/backends/redis.py`) requires this
stated explicitly for a `rediss://` broker/backend URL — unlike the
FastAPI side's plain `redis.asyncio` client, which merely logs a warning
and falls back to an unverified TLS connection. `workers/celery_app.py`
now sets `broker_use_ssl`/`redis_backend_use_ssl` to
`{"ssl_cert_reqs": ssl.CERT_REQUIRED}` whenever `REDIS_URL` starts with
`rediss://` (a no-op against docker-compose's plain `redis://`). Also
tightened `_poll_queue_depth`'s own `redis.Redis.from_url(...)` call and
`core/redis_client.py`'s `aioredis.ConnectionPool.from_url(...)` to pass
`ssl_cert_reqs="required"` under the same condition, closing the same
unverified-TLS gap everywhere `rediss://` is used, not just where it was
fatal. Verified: worker pods reach `celery@... ready.` and mingle with
their peers against real Upstash.

**Docker Desktop Kubernetes does not pick up a rebuilt image under an
already-cached tag (⚠️ workaround only, not fixed — see Architecture
Decisions' correction to the Day 22 image-caching note):** hit while
verifying the two fixes above — rebuilding `f1-backend:local`/
`f1-worker:local` and restarting pods kept reproducing the pre-fix
behavior, traced to the node running a stale image digest under the same
tag. Worked around Day 24 by retagging to a build-specific name
(`f1-backend:day24fix`) and pointing the Helm release at it via `--set
backend.image.tag=...`; not a lasting fix — local iteration on `:local`
images should move to a per-build unique tag (git SHA or timestamp)
rather than reusing one tag repeatedly.

## Deferred Telemetry Features

Raw high-frequency telemetry (100ms Throttle/Brake/Speed channels from FastF1) was
never ingested — Day 5's ingest_historical.py deliberately skips these channels to
avoid tens of millions of rows (only lap/sector/stint-level aggregates are stored).

| Feature | Original Spec Source | Purpose | Add On |
|---|---|---|---|
| braking_consistency | 100ms Brake channel, std of brake points per corner | driver_style.py fingerprinting | TBD |
| throttle_application_smoothness | 100ms Throttle channel | driver_style.py fingerprinting | TBD |

### Notes

**driver_style.py braking/throttle features (Day 8):**
- Discovered on Day 8: the original driver_style.py spec called for
  braking_consistency and throttle_application_smoothness, both computable only
  from 100ms Throttle/Brake telemetry that Day 5 never ingested.
- Decision on Day 8: ship driver_style.py with 4 lap/stint-level proxies instead —
  sector_time_variance, tyre_management_index, lap_time_consistency,
  stint_length_tendency (all derivable from lap_data/tire_stints already stored).
  The PCA(4) -> KMeans(5) -> UMAP(2D) pipeline itself is unchanged from spec, just
  fed these 4 features instead of the original 4.
- When this lands: ingest_live_session.py / ingest_historical.py would need a new
  high-frequency telemetry table (partitioned/hypertable — this is exactly the
  volume TimescaleDB was chosen for), a backfill script for historical sessions,
  and driver_style.py's FEATURE_COLUMNS would gain the 2 original features
  alongside (not instead of) the 4 current proxies.

## Deferred Test Coverage

Per pre-commit.md convention, `backend/tests/unit/` collecting 0 tests
(pytest exit code 5) is the expected, accepted result before Day 14 —
CLAUDE.md's unit-test coverage rule applies starting Day 14, not to
services written earlier.

| Test File | Covers | Add On |
|---|---|---|
| tests/unit/test_tire_deg_model.py | backend/services/ml/tire_deg_model.py | Day 14 |
| tests/unit/test_pit_predictor.py | backend/services/ml/pit_predictor.py | Day 14 |
| tests/unit/test_safety_car_model.py | backend/services/ml/safety_car_model.py | Day 14 |

## Planned Feature — Live Circuit Map (Days 25-28)

Display live race on circuit layout with drivers as moving dots 
alongside the timing tower on the same page.

Backend prep needed (before Day 25):
- Extract circuit map X/Y coordinates from FastF1 pos_data for 
  all 24 circuits, store as JSON
- Capture LapDistance from TimingData SignalR stream in 
  ingest_live_session.py
- New Redis key: f1:{season}:{round}:car:{driver}:lap_distance TTL 2s
- Endpoint to serve circuit coordinates

Frontend (Days 25-28):
- SVG circuit outline from stored coordinates
- 20 team-colored dots interpolated along circuit path from LapDistance
- Updates via WebSocket or polling
- Timing tower on same page, selecting driver syncs both panels

Reference: https://github.com/TiE23/sab-f1-ui for timing tower 
animation CSS patterns

## Data Quality Notes

**Historical ingestion coverage (2018-2024 training corpus):**
- Total: ~139,764 lap records across 7 seasons
- Missing circuits due to FastF1 location name mismatches:
  Le Castellet (French GP 2018-2021), Yas Marina (Abu Dhabi 2018),
  Portimão (Portuguese GP 2021), Istanbul Park (Turkish GP 2020-2021),
  Mugello (Tuscany GP 2020), Nürburgring (Eifel GP 2020)
- Decision: not fixing — missing circuits are either off-calendar or 
  represented by more recent season data. 139k laps is sufficient 
  training corpus for all 7 ML models.
- 2025 holdout set: 26,689 laps, all 24 rounds complete

**Weather features (track_temp, air_temp) training result (2026-07-11):**
Weather features (track_temp, air_temp) added to tire_deg_model feature
set but regressed holdout MAE by 30-40% across all compounds
(SOFT: 0.644→0.909, MEDIUM: 0.504→0.665, HARD: 0.521→0.696).
Promotion guard correctly refused to replace production models.
Hypothesis: circuit_id_encoded already captures average temperature
signal implicitly; explicit temperature adds race-specific noise that
doesn't generalize across seasons. Revisit when 2+ additional holdout
seasons available, or try temperature deviation from circuit historical
mean as engineered feature instead of raw temperature.


## Deployment Strategy

**Local-first deployment** — all three clients (web/desktop/mobile) 
connect to the locally hosted Docker stack during development and demos.

- Backend: Docker Compose (all services on localhost)
- Web app: Vercel (frontend only, points to local backend via ngrok for demos)
- Desktop app: Tauri native build, connects to local backend
- Mobile app: Expo Development Build on iPhone, connects to local backend 
  via LAN IP (same WiFi network required)
- Demo videos: recorded from each client, stored in demos/ directory

No cloud VM deployment during the build — see DEPLOYMENT.md for full 
strategy including ngrok demo workflow and future Render deployment plan.

## Desktop Sync Protocol

`desktop/` (Tauri v2 + React, Day 30) has no monorepo/symlink sharing with
`web/` — symlinks are unreliable on Windows. Several `desktop/src/` files
are manual copies of `web/src/` files and must be updated by hand whenever
the `web/` source changes. Full file-by-file list, including which files
are verbatim copies vs. copied-and-adapted vs. hand-written-but-logic-
mirrored: **`desktop/src/README.md`**.

Summary of what's copied: `types/*.ts`, `api/*.ts`, `utils/{constants,
errors, formatters, drivers}.ts`, `stores/authStore.ts`, `lib/utils.ts`,
`components/ui/*.tsx` (shadcn), `components/shared/ErrorBoundary.tsx`,
`index.css`, `tailwind.config.js`, `postcss.config.js`, the self-hosted
Titillium Web font files, and `favicon.svg`. `pages/SimulatorPage.tsx` is
copied-and-adapted (live-mode detection removed, session/driver source
switched to `raceContextStore`, desktop-only CSV export button added) —
diff against web rather than blind-overwriting on sync. Hooks are
deliberately **not** copied (window-management/native-API concerns differ
per platform) — `desktop/src/hooks/{useDrivers,useSessionGaps,
useDriverLaps,useStrategy,useAuth}.ts` are hand-written re-implementations
of the same react-query logic and can drift independently; check them too
when the corresponding web hook changes.

## Mobile Sync Protocol

`mobile/` (Expo SDK 57 + Expo Router + NativeWind v4, Day 31) has no
monorepo/symlink sharing with `web/` — same reasoning as desktop (symlinks
unreliable on Windows). Full file-by-file sync table — verbatim copies vs.
copied-and-adapted vs. hand-written-but-logic-mirrored vs. hand-ported
components — lives in **`mobile/src/README.md`**, same convention as
`desktop/src/README.md`.

Summary of what's copied verbatim: `types/*.ts` (10 files), 8 of 9
`api/*.ts` files (all except `client.ts`), `utils/{errors,formatters,
drivers}.ts`, `stores/{sessionStore,alertStore}.ts`. Copied-and-adapted:
`api/client.ts` (SecureStore-backed token reads via the store, not the
interceptor itself), `stores/authStore.ts` (SecureStore `StateStorage`
persist adapter, token-slice-only — `user` is never persisted, ~2048-byte
iOS SecureStore item limit), `utils/constants.ts` (Expo Router paths for
`ROUTES`, `EXPO_PUBLIC_*` env vars instead of Vite's `import.meta.env`).

Hooks are **not** copied (hand-written per-platform re-implementations,
same as desktop) — 17 hooks total, mirroring web's react-query logic
1:1 where web's own hook has no browser API involved. One is a genuine
rewrite, not a mirror: `hooks/useWebSocket.ts` wraps React Native's
built-in `WebSocket` (hand-rolled fixed-delay reconnect) instead of the
browser-only `reconnecting-websocket` package web uses.

Components are hand-built or ported (not copied) — 15 components across
`shared/`, `circuit/`, `telemetry/`, `strategy/`, `dashboard/`,
`settings/`. Notably: `CircuitMapPanel.tsx` + `TelemetryGauge.tsx` +
`CircuitOutlineSvg.tsx` are full react-native-svg ports of the web
originals (same geometry/math), with live dot movement via a new
`AnimatedDriverDot.tsx` (Reanimated `useAnimatedProps`) replacing web's
CSS `transform` transition. `CircuitMapPanel` sits at the top of the
**Live** tab, not Home — web's Home-equivalent only ever got the static
outline (`UpcomingRaceCard`), the full live panel belongs where web
mounts it (`RacePage`). Several simplifications are disclosed inline in
`mobile/src/README.md` (`TeamLogo` swatch-only, no Ergast-standings sort
on the Drivers tab, no FLIP row-reorder animation, Driver Detail is a
minimal stub, `TelemetryGauge`'s arcs snap instead of sweep) — check that
file before assuming full parity with any given web component.

Push notifications (`src/notifications/notificationHandler.ts`,
`src/hooks/{usePushNotifications,useNotificationResponseListener}.ts`)
have no web equivalent — mobile-only capability. Written and verified via
`tsc`/Metro export only; untestable without a physical device + dev
build (no Apple Developer account or Android emulator set up this
sprint — see `mobile/src/README.md`'s Testing Options, including a full
Android-emulator setup procedure verified against Expo's current docs).