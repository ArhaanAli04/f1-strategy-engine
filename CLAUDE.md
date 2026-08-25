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
f1:races:list:{season}:{round_number}:{page}:{page_size}          TTL: 86400s   (paginated race listing)
f1:current_race:{season}                                          TTL: 300s     (Ergast-resolved current race, insulates external API)
f1:drivers:all                                                    TTL: infinity (driver roster, manual invalidation only)
f1:driver:{driver_id}:session:{session_id}:laps:{page}:{page_size} TTL: 86400s (paginated per-driver lap history)
f1:circuit:{circuit_id}:detail                               TTL: infinity (static data)
f1:alerts:{session_id}                                       pub/sub       (no TTL — alert delivery channel)
f1:telemetry:{session_id}:laps    pub/sub    (lap completion broadcast channel, Checkpoint E Day 11)
f1:{season}:{round}:R:auto_ingestion_triggered                TTL: 14400s   (Day 39B dedup lock, not cached data — SETNX guard so a re-poll of check_for_live_session doesn't double-launch the live ingestor for the same race; see Auto Race Detection below)
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
- GET    /api/v1/drivers
- GET    /api/v1/drivers/{id}/analysis
- GET    /api/v1/drivers/{id}/laps
- GET    /api/v1/telemetry/{session_id}/{driver_id}/live
- GET    /api/v1/telemetry/{session_id}/{driver_id}/history
- WS     /api/v1/ws/telemetry/{session_id}
- GET    /api/v1/telemetry/{session_id}/gaps
- GET    /api/v1/strategy/simulate/{task_id}
- GET    /api/v1/strategy/{session_id}/{driver_id}/pit-window
- GET    /api/v1/strategy/{session_id}/{driver_id}/undercut
- GET    /api/v1/strategy/{session_id}/overview
- POST   /api/v1/strategy/{session_id}/simulate
- GET    /api/v1/alerts
- PUT    /api/v1/alerts/{id}/read
- GET    /api/v1/alerts/subscriptions
- PUT    /api/v1/alerts/subscriptions
- GET    /health

---

## Current Project Phase

Update this section at the start of each day's session:

```
Phase:    8
Day:      41
Status:   British GP 2026 (Round 9) ingested and 
          validated end-to-end. Strategy overview, pit window, undercut, Monte Carlo simulator all tested against real 2026 data.Major fix: _undercut_overcut_probability vectorized — 600 unbatched XGBoost calls 
          → 3 batched calls per pipeline. 36.5s → 0.87s (42x speedup), full task 
          38s → 2.1s (18x). Fixes documented Day 18 "65-88s/task" load test finding root cause. replay_pipeline.py created — full pipeline 
          replay (process_lap + run_strategy_prediction + alerts) with --rate and --limit flags.Bounded replay validated: 110 events, 0 queue backlog, 2 real alerts fired correctly.ingest-historical.yml workflow created — 
          manual trigger to keep Supabase current with 2026 races post-race-weekend.
          Findings logged to Deferred Wiring: tire_deg_hard.pkl mispredicts fresh HARD tyre (tyre_age_laps=1) — needs retraining. StrategyPrediction history endpoint gap (no lap-by-lap prediction history view)
Next:     Day 40 A4 — Fly.io deployment (fly auth login → fly deploy → verify)
Blockers: No physical device for testing — Android emulator 
          setup planned after Day 32 (see mobile/src/README.md),Cloud deployment target undecided (Render/GKE) — cd.yml Jobs 3-5 remain placeholders, Sector boundaries (S1/S2/S3) deferred — see CLAUDE.md, VITE_API_URL_PROD placeholder until Fly.io deployed Day 40, ALLOWED_ORIGINS needs Vercel URL after Day 40 deployment
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

- **[deferred] No endpoint exists to view historical `StrategyPrediction`
  rows (what was predicted at a specific past lap).** Current `/overview`
  always computes live from `lap_data`'s latest state per driver (see
  `strategy_service._current_state`) — it has no notion of "as of lap N."
  `replay_pipeline.py` (Day 41) proved the write path persists real,
  varying per-lap `StrategyPrediction` rows correctly, but nothing reads
  them back as a lap-by-lap history. Would need a new endpoint like
  `GET /strategy/{session_id}/{driver_id}/history` to expose this. Not a
  bug — deferred, not needed for the live race use case `/overview` and
  `/pit-window` already serve.

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

- **[deferred] retrain_incremental.py FastF1 403→mirror fallback for 2026
  data:** FastF1 gets a 403 from `livetiming.formula1.com` and falls back
  to `livetiming-mirror.fastf1.dev`, which has no 2026 data (only patches a
  couple of corrupted 2021-2022 sessions), so 2026 rounds raise
  `SessionNotAvailableError`. Current behavior: rounds are skipped
  gracefully — non-blocking, the pipeline trains correctly on the
  2018-2025 base corpus. Real fix (retry loop + cache clear +
  `FASTF1_CACHE_DIR` setup in `train-models.yml`) needed before 2026 data
  becomes important for MAE improvement — i.e. before next season, not
  urgent today.

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

### Dependency version drift — prometheus-fastapi-instrumentator (✅ fixed Day 16)

pyproject.toml lower-bound-only pins caused a silent compatibility 
break: prometheus-fastapi-instrumentator 8.0.0 crashed on every HTTP 
request with AttributeError: '_IncludedRouter' object has no attribute 
'path' under FastAPI 0.138/Starlette 1.3.1. Fixed Day 16 by bumping 
to >=8.0.2 (GitHub issue #370, fixed in 8.0.1). For middleware/monitoring 
libraries that hook into framework internals, consider upper bounds to 
prevent silent breaks during pip install --upgrade.

### Notes

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