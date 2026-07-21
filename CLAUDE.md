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
Phase:    4
Day:      16
Status:   13 integration tests passing. test_alembic_migrations (3), 
          test_race_api (4), test_strategy_endpoint (2), 
          test_telemetry_ingestion (2), test_live_prediction_pipeline (1), 
          test_race_simulation_serialization (1). Fixed pre-existing 
          production bug: prometheus-fastapi-instrumentator 8.0.0 
          incompatible with FastAPI 0.138/Starlette 1.3.1 — bumped to 
          8.0.2. Fixed two Celery singleton-caching issues in eager mode. 
          TimescaleDB image now used in testcontainers. 104 unit tests 
          still passing. ruff+mypy clean.
Next:     Integration tests — auth, WebSocket & user flows
Blockers: Strategy endpoints missing auth (noted in deferred wiring)
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

- Strategy endpoints missing authentication: POST /strategy/simulate 
and GET /strategy/{session_id}/{driver_id}/pit-window should require 
Depends(get_current_user) before production deployment. Currently 
public — rate limiting (10 req/min unauth) provides minimal protection 
but compute-heavy endpoints are exploitable. Fix before Day 22 
Kubernetes deployment.

- **WebSocket JWT in query param (?token=):** access token appears in 
  server logs and browser history. Acceptable for now. Production fix: 
  short-lived WebSocket ticket — exchange via REST before connection, 
  use one-time token for WS auth instead of the full JWT.

- **WS telemetry broadcast: redundant per-connection enrichment fan-out —
  fix before Day 22 Kubernetes deployment.** `_forward_lap_events` in
  `backend/apis/v1/telemetry.py` runs one `pubsub.listen()` loop per WS
  connection, and on every lap-completion message each loop independently
  calls `telemetry_service.get_live_car_channels(...)` — the same Redis GET,
  for the same cache key, once per connected client, per event. At N
  concurrent viewers that's Nx redundant Redis round trips per event instead
  of one. Measured via `tests/load/ws_load_test.py --connections 200
  --messages 50 --rate 20` (2026-07-16, after the two pool fixes below were
  already applied): only ~24/50 messages delivered per connection on average
  before the test's drain window closed, p50 latency ~2.2s, p95 ~3.6s, max
  ~4.0s — versus a clean 20-connection run at the same rate (p50 16ms, p99
  47ms, 0 loss). Bumping Redis `max_connections` further would only raise the
  ceiling, not remove the redundancy, since every extra viewer still adds
  another full copy of the same lookup work per event.

  Required fix: replace the one-pubsub-per-connection model with a single
  shared listener per session — one `pubsub.listen()` task (keyed by
  `session_id`) that receives each lap-completion message once, calls
  `get_live_car_channels` once, builds one `TelemetryStreamMessage`, and
  fans it out by iterating the set of WebSocket connections currently
  attached to that session (a session_id -> set[WebSocket] registry, started
  on first subscriber and torn down when the last one disconnects — same
  lifecycle shape as a per-session singleton, not a per-request dependency).
  `_watch_for_disconnect` per-connection can stay as-is for detecting client
  disconnects; only the listen+enrich+send path needs to move from
  per-connection to per-session. Re-run `ws_load_test.py` at
  `--connections 200` after the change and confirm delivery returns to ~0
  loss with sub-100ms p99, matching the 20-connection baseline above.

  Two related pool-sizing fixes already landed alongside this finding
  (2026-07-16, pre-Day-14 load testing pass) and should NOT be re-litigated
  when the fan-out fix lands — they're independent, already-verified issues:
  - `websocket_telemetry` used to take `db: Annotated[AsyncSession,
    Depends(get_db)]`, which FastAPI keeps open for the WS connection's
    entire lifetime even though the DB is only needed once at connect time
    (`resolve_season_round`). With `pool_size=10 + max_overflow=20` (30
    total, `core/database.py`), this capped concurrent viewers at ~30
    regardless of pod scaling. Fixed by resolving season/round through a
    short-lived module-local session factory (same pattern as
    `workers/*.py`'s `_get_session_factory`) before entering the streaming
    loop, instead of a request-scoped dependency held for the connection.
  - Redis pub/sub subscriptions pin a dedicated connection from the shared
    pool for the subscription's lifetime (a redis-py constraint, not a bug).
    `core/redis_client.py`'s pool was `max_connections=50`, capping
    concurrent viewers there too. Raised to 250 to give the 200-connection
    load test headroom on top of ordinary REST-route command traffic. This
    number will need revisiting once the fan-out fix above lands and
    replaces N pubsub connections with 1 per session.

  **Corroborating evidence, pre-Day-14 fix pass (2026-07-18):** a combined
  Locust run (`-u 100 -r 10 --run-time 2m`, `RaceDayViewerUser` +
  `StrategyUser` + `WebSocketUser` together, `replay_publisher.py --rate 5`
  feeding real WS traffic) — run to verify the `/races/current` single-flight
  lock and the `/strategy/simulate` dedicated-executor fix below — showed
  `POST /strategy/{session_id}/simulate`'s enqueue latency regress back to
  ~12s p50, even though both of those fixes were independently confirmed
  working in isolated (WS-free) runs on the same day (p50 630-2400ms).
  `redis-cli slowlog get` during the combined run showed the rate limiter's
  own `EVALSHA` check — normally sub-millisecond — taking 12-16ms, consistent
  with Redis's single-threaded command queue backing up under load rather
  than any one code path being newly slow. Given `redis-1` is the same
  instance serving cache reads/writes, the Celery broker, rate-limit checks,
  and every WS pub/sub subscription, this fan-out's Nx-per-event redundant
  `get_live_car_channels` GETs (up to ~33 concurrent `WebSocketUser`s at
  5 events/sec in this run) is the most likely source of the extra command
  volume dragging down unrelated Redis-adjacent paths — not a new bottleneck,
  the same one already scoped above, now visible because this was the first
  load test to run WS + overview + simulate + races/current simultaneously
  (Day 13's baseline and this session's earlier re-tests each isolated a
  subset). Supports doing this fix before Day 22 as planned, rather than
  deferring further — its blast radius already reaches beyond `/ws/telemetry`
  itself once real WS traffic is in the mix.

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
  Nx-redundant-per-event `get_live_car_channels` GETs (see the "WS
  telemetry broadcast: redundant per-connection enrichment fan-out" bullet
  above for the full analysis and required fix). No separate action needed
  on this bullet — fixing the fan-out should resolve this regression too.

- **get_competitor_predicted_strategy 16-17s cold compute floor:**
  `/strategy/{session_id}/overview` has p50=55ms (cache hits) but 
  p99=17,000ms (cold misses). The cold path iterates all 20 drivers 
  sequentially with ML inference per driver — no batching, no parallelism. 
  Candidate fixes: parallelise with asyncio.gather() across drivers, 
  or batch the tire_deg/pit_predictor calls across all 20 drivers 
  simultaneously. Profile _first_pit_lap_over_threshold first — 
  redundant per-lap looping may be the dominant cost. Fix before Day 22.

### Dependency version drift — prometheus-fastapi-instrumentator

pyproject.toml lower-bound-only pins caused a silent compatibility 
break: prometheus-fastapi-instrumentator 8.0.0 crashed on every HTTP 
request with AttributeError: '_IncludedRouter' object has no attribute 
'path' under FastAPI 0.138/Starlette 1.3.1. Fixed Day 16 by bumping 
to >=8.0.2 (GitHub issue #370, fixed in 8.0.1). For middleware/monitoring 
libraries that hook into framework internals, consider upper bounds to 
prevent silent breaks during pip install --upgrade.

### Notes

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