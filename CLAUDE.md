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
| Primary DB       | PostgreSQL + TimescaleDB extension (Supabase cloud)     |
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
```

When adding a new cache key: add it to this list with TTL and justification.

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
Phase:    5
Day:      21
Status:   Model retraining pipeline complete. export_training_data.py 
          (one-time S3 parquet export), retrain_incremental.py (CI 
          entrypoint — base parquet + live FastF1 2026 fetch, no DB 
          needed), train-models.yml (workflow_dispatch live, cron TODO 
          Day 23), per-model promotion to :production tag. docs/runbook.md 
          written. K8s manifests written but not applied: hpa.yaml, 
          worker-scaledobject.yaml, race-weekend-cronjob.yaml. 
          pyarrow added to deps. 104 tests passing, 113 files mypy clean.
Next:     Day 22 — Kubernetes manifests, Helm chart, Docker Desktop K8s
Blockers: Strategy endpoints missing auth (noted in deferred wiring),DB pool           exhaustion (fix before Day 22), WS fan-out 
          (fix before Day 22), production environment needs ArhaanAli04 as required reviewer 
          before Day 24, Run export_training_data.py once locally before triggering 
          train-models.yml (see CLAUDE.md deferred wiring)
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
| Supabase (production DB) | Cloud PostgreSQL + TimescaleDB | ⬜ Not set up | Day 23 |
| Upstash Redis (production) | Cloud Redis cache + broker | ⬜ Not set up | Day 23 |
| Kubernetes cluster (EKS/GKE) | Production container orchestration | ⬜ Not set up | Day 22 |
| Sentry | Exception tracking + performance | ✅ set up | Day 12 |
| Slack (F1 Strategy Engine workspace) | Alertmanager notifications | ✅ Set up | Day 12 |
| Vercel | Web frontend deployment | ⬜ Not set up | Day 33 |
| GitHub Secrets | CD pipeline credentials | ⬜ Not set up | Day 19 |

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

**GitHub Secrets (add before Day 19):**
- AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
- DATABASE_URL (production Supabase)
- REDIS_URL (production Upstash)
- SECRET_KEY (fresh 64-char random string for production)
- SENTRY_DSN
- KUBECONFIG (base64-encoded kubectl config)


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

These are not schema changes but known integration gaps to fix on future days.

- prometheus.yml Basic Auth credentials are hardcoded as dev defaults 
(metrics/metrics-dev). Fix on Day 19 when setting up GitHub Secrets — 
use an entrypoint script to substitute ${METRICS_USER}/${METRICS_PASSWORD} 
into prometheus.yml at container startup, same pattern as alertmanager.yml's 
Slack webhook handling.

- **WebSocket JWT in query param (?token=):** access token appears in 
  server logs and browser history. Acceptable for now. Production fix: 
  short-lived WebSocket ticket — exchange via REST before connection, 
  use one-time token for WS auth instead of the full JWT.

- **Cache stampede fix does not address the underlying 16-17s compute
  floor:** the single-flight lock added to `cache_service.cacheable` (see
  Notes: "Cache stampede single-flight lock") removes the *redundant*
  computation cost, but does nothing about the *underlying* ~16-17s
  single-computation cost itself (`get_competitor_predicted_strategy`'s
  nested per-driver ML inference loop in `strategy_service.py`) — confirmed
  in the Day 13 re-run: `/strategy/overview`'s p95/p99/max were *unchanged*
  (16000/19000/20070ms) even with the stampede fixed, because a request
  that's unlucky enough to need a fresh computation (or wait behind one)
  still pays close to that full 16-17s floor. This is now the single
  highest-leverage remaining target — a future day should profile why ~20
  drivers' worth of pit_predictor + tire_deg calls costs 16-17s (candidate
  causes: no batching across drivers, redundant per-lap looping inside
  `_first_pit_lap_over_threshold`) before reaching for anything more
  drastic.

- **`broker_pool_limit` 10->50 — applied 2026-07-16, confirmed live, did NOT
  fix POST `/strategy/{session_id}/simulate`'s enqueue latency on its own.**
  Original hypothesis: Celery's producer-side `broker_pool_limit` (default
  10, confirmed via `app.conf.broker_pool_limit` before the change) capped
  concurrent `.delay()` calls from the API process, explaining why a call
  that should be a near-instant broker publish was taking ~12s median at 100
  concurrent users. Raised to 50 in `workers/celery_app.py`'s
  `app.conf.update(...)`; confirmed live post-restart. Re-ran the identical
  baseline: p50 went from 12000ms to 14000ms — unchanged at best.
  **Real cause identified and fixed (pre-Day-14):** `apis/v1/strategy.py`'s
  `simulate_strategy` was wrapping `.delay()` in
  `loop.run_in_executor(None, run_race_simulation.delay, task_payload)` —
  passing `None` uses asyncio's *default* `ThreadPoolExecutor`, capped at
  `min(32, cpu_count+4)` (= 20 on this container's 16 CPUs). Fixed with a
  dedicated `_SIMULATE_ENQUEUE_EXECUTOR` (50 workers, matching
  `broker_pool_limit=50`) — confirmed working in isolated (WS-free) load
  test runs: p50 630-2400ms, down from ~12-14s.
  **Regression under combined load is not a new bug — it's the WS fan-out
  issue tracked above:** a combined Locust run (`RaceDayViewerUser` +
  `StrategyUser` + `WebSocketUser` together, real WS traffic) showed this
  same enqueue latency regress back to ~12s p50 even with the
  dedicated-executor fix in place, traced to Redis's single-threaded
  command queue backing up under the WS telemetry fan-out's
  Nx-redundant-per-event `get_live_car_channels` GETs (see Notes: "WS
  telemetry broadcast fan-out redundancy" for the fix). **Confirmed
  resolved:** the 2026-07-28 100-user combined-load re-run (after the
  fan-out fix landed) showed this enqueue latency back to p50=1900ms,
  matching the isolated dedicated-executor fix's 630-2400ms range — fixing
  the fan-out did resolve this regression as predicted.

- **get_competitor_predicted_strategy 16-17s cold compute floor:**
  `/strategy/{session_id}/overview` has p50=55ms (cache hits) but 
  p99=17,000ms (cold misses). The cold path iterates all 20 drivers 
  sequentially with ML inference per driver — no batching, no parallelism. 
  Candidate fixes: parallelise with asyncio.gather() across drivers, 
  or batch the tire_deg/pit_predictor calls across all 20 drivers 
  simultaneously. Profile _first_pit_lap_over_threshold first — 
  redundant per-lap looping may be the dominant cost.

  **Update, pre-Day-22 fix pass:** batching applied.
  `get_competitor_predicted_strategy` (via the new
  `_first_pit_laps_over_threshold_batch`) now calls
  `pit_model.predict_proba` and `tire_deg_model.predict_life_remaining_batch`
  once per lap-offset across all still-active drivers (grouped by compound
  for the tire_deg call, since that pipeline is compound-specific), instead
  of once per driver per offset. Verified 35% per-call improvement
  (0.937s → 0.612s, models-warm) via a git-stash A/B against a real
  20-driver session (`00b4f598-40ec-4792-8687-6eae51257977`). This is a
  real, verified improvement — but does not reproduce or explain the full
  16-17s concurrent-load floor above: an isolated single call finishes in
  under 1s even on the pre-refactor code, so that floor is DB-pool/
  concurrency-bound (queuing under ~100 concurrent Locust users), not pure
  per-call model overhead. The WS fan-out fix has since landed (see Notes:
  "WS telemetry broadcast fan-out redundancy") — the remaining floor is DB
  connection pool sizing (below), still open.

- **Single `--pool=solo` Celery worker cannot sustain race-day simulate
  traffic — fix via multiple worker pods on Day 22.** This project's
  existing `--pool=solo` rationale already anticipated needing "8+ worker
  pods" on race day, but the Day 18 500-user load test (2026-07-23) gives
  the first concrete number for how large that gap actually is. Grafana's
  Celery queue-depth panel showed ~580 queued tasks at peak during the run,
  which reads like a transient spike — it isn't. Verified directly after
  the run: `redis-cli llen prediction_queue` still read 559 roughly 30
  minutes after `--run-time` expired, and `docker logs docker-worker-1`
  showed each `run_race_simulation` task taking 65-88 seconds end-to-end
  (not the ~10s Grafana's ML-inference panel shows — that panel measures
  only the Monte Carlo inference sub-step, not the full task including its
  per-driver DB round trips, themselves slowed by the connection-pool
  exhaustion above). At ~75s/task average and 559 tasks still queued, full
  backlog drain was projected at 10+ hours on the single solo-pool worker
  process — confirmed while writing the Day 18 E2E test
  (`tests/e2e/test_api_flows.py`): a fresh `/simulate` call queued behind
  this backlog did not complete even once in a 30s poll window, and only
  succeeded (in 68s) after the stale queue was purged
  (`celery -A backend.workers.celery_app purge -f -Q prediction_queue`,
  555 messages removed) to unblock testing. 647 simulate requests were
  submitted in the 10-minute run — far more than one worker pod at
  ~0.013 tasks/sec (1/75s) can remotely keep up with.

  Proposed fix: scale to multiple worker pods for race day, as already
  planned in the `--pool=solo` rationale — this run supplies the real
  per-task cost (65-88s) needed to size that pool count properly instead of
  guessing. Fix on Day 22 Kubernetes deployment alongside the DB pool
  sizing fix above, since both block real race-day-scale traffic.

- **kubectl apply --dry-run=client not yet validated:**
  infra/k8s/hpa.yaml, worker-scaledobject.yaml, and
  race-weekend-cronjob.yaml were validated with a YAML parser only —
  kubectl dry-run requires an API server connection which needs a running
  cluster. Full validation with kubectl apply --dry-run=client will happen
  on Day 22 when Docker Desktop Kubernetes is enabled. At that point also
  confirm Deployment names match what the Helm chart generates and update
  placeholder names if needed.

- **WS keepalive ping timeouts under heavy CPU load:** 28,603 closures
  (85.7% of WS traffic) in 500-user run. Likely cause: Uvicorn's single
  event loop blocked by synchronous CPU-bound ML inference in `/overview`
  cold path, starving asyncio ping/pong. Investigate after DB pool fix and
  K8s deployment — may resolve naturally with multiple backend pods (each
  with its own event loop, less contention per pod).

### Dependency version drift — prometheus-fastapi-instrumentator

pyproject.toml lower-bound-only pins caused a silent compatibility 
break: prometheus-fastapi-instrumentator 8.0.0 crashed on every HTTP 
request with AttributeError: '_IncludedRouter' object has no attribute 
'path' under FastAPI 0.138/Starlette 1.3.1. Fixed Day 16 by bumping 
to >=8.0.2 (GitHub issue #370, fixed in 8.0.1). For middleware/monitoring 
libraries that hook into framework internals, consider upper bounds to 
prevent silent breaks during pip install --upgrade.

### Notes

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