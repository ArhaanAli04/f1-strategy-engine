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

Models are loaded lazily on first request and cached in memory per worker process.
New model versions are uploaded to S3 with a version tag. Workers check S3 for a
newer :production tag every 60 seconds during a live session.

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
f1:race:{race_id}:detail                                     TTL: 86400s   (race metadata)
f1:circuit:{circuit_id}:detail                               TTL: infinity (static data)
f1:alerts:{session_id}                                       pub/sub       (no TTL — alert delivery channel)
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
Phase:    3
Day:      9 (improvement pass between Day 9 and Day 10)
Status:   Weather features improvement pass complete. Added track_temp/air_temp 
          to lap_data (migration + 166,453 row backfill, 100% coverage). 
          Live weather wired in ingest_live_session.py (Redis TTL 60s). 
          strategy_service._resolve_weather() reads live weather with DB fallback.
          race_simulator.RaceSimulationInput now weather-aware.
          tire_deg_model retrained — holdout MAE regressed 30-40%, promotion 
          guard refused replacement. Previous production models retained.
          Infrastructure committed, model finding documented in CLAUDE.md
Next:     Day 10 — User auth service + REST API endpoints Part 1
Blockers: None
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
| AWS S3 (f1-strategy-models) | ML model storage | ✅ Not set up | Day 7 |
| AWS IAM credentials | S3 read/write access | ✅ Not set up | Day 7 |
| Supabase (production DB) | Cloud PostgreSQL + TimescaleDB | ⬜ Not set up | Day 23 |
| Upstash Redis (production) | Cloud Redis cache + broker | ⬜ Not set up | Day 23 |
| Kubernetes cluster (EKS/GKE) | Production container orchestration | ⬜ Not set up | Day 22 |
| Sentry | Exception tracking + performance | ⬜ Not set up | Day 12 |
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

- **prediction_worker → strategy_service:** undercut_score/overcut_score in 
  StrategyPrediction hardcoded 0.0. prediction_worker.py needs to call 
  strategy_service.get_undercut_score() to populate real scores. Without this, 
  evaluate_threats never fires real alerts.

- **prediction_worker.py tire_deg feature vector shape:** `_run_inference()` builds
  `features = [[tyre_age_laps, lap_number]]` — 2 values — against
  tire_deg_model.FEATURE_COLUMNS, which now has 8 columns (lap_number,
  compound_encoded, tyre_age_laps, fuel_adjusted_time, circuit_id_encoded,
  driver_id_encoded, track_temp, air_temp) after the weather-features pass. This
  predates that pass — it was already a 2-vs-6 mismatch before track_temp/air_temp
  existed — so it is not a new regression, just a pre-existing bug whose correct
  target shape changed. Needs the same real feature construction strategy_service.py's
  `_project_stint_delta`/`_resolve_weather` already use when this gets fixed
  alongside the undercut_score/overcut_score wiring above.

- **WeatherData live stream:** now wired (was previously subscribed but discarded).
  ingest_live_session.py parses AirTemp/TrackTemp from the WeatherData topic and
  writes them to f1:{season}:{round}:weather:latest (see Redis Cache Key Schema).
  Weather features are active in both training (train_models.py / tire_deg_model.py)
  and live inference (strategy_service.py's _resolve_weather, with a circuit+compound
  DB-average fallback when the live key is absent).

-  teams and driver_contracts tables are empty — no ingestion script 
populates them. Need a seed_teams.py script that:
    - Seeds current teams (McLaren, Ferrari, Red Bull, etc.)
    - Seeds driver_contracts linking current drivers to their teams
    - Can use FastF1's session.get_driver() data or a hardcoded 
    current-season roster
Add alongside a future ingestion improvement pass.

### Notes

**users.fcm_token (✅ completed Day 10):**
- Migration added: `20260711_add_fcm_token_to_users.py`
- User model updated with `fcm_token: Mapped[str | None]`
- `PUT /auth/fcm-token` endpoint added for mobile clients
- Note: fcm_token intentionally NOT included in UserResponse —
  no need to echo device token back in every user payload

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