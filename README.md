# F1 Strategy & Telemetry Engine
[![CI](https://github.com/ArhaanAli04/f1-strategy-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/ArhaanAli04/f1-strategy-engine/actions/workflows/ci.yml)

A full-stack F1 race strategy platform that ingests lap-by-lap telemetry from
the FastF1 API, runs XGBoost/LightGBM models and a Monte Carlo race simulator
to predict pit windows and undercut probabilities, and streams the results to
a web, desktop, and mobile client over a live WebSocket feed. It exists as a
from-scratch build-in-public project to work through a production-shaped
stack — async FastAPI, Celery, Kubernetes, real ML — end to end rather than
as a pre-existing tool.

## Architecture

```
FastF1 API ──▶ ingestion scripts ──▶ PostgreSQL (Supabase)
                                          │
                                          ├──▶ Celery workers ──▶ ML models (XGBoost/LightGBM)
                                          │         │                  │
                                          │         └──── Redis (Upstash) ◀──┘
                                          │                  │  cache + pub/sub + broker
                                          ▼                  ▼
                                    FastAPI backend ──▶ WebSocket / REST
                                                              │
                                       ┌──────────────────────┼──────────────────────┐
                                       ▼                      ▼                      ▼
                                  React (web)           Tauri (desktop)       Expo (mobile)
```

See [docs/architecture.md](docs/architecture.md) for the reasoning behind
each of these choices.

Key components:
- **FastAPI backend** — async REST + WebSocket API, zero business logic in
  route handlers (`backend/apis/v1/`), all logic in `backend/services/`.
- **Celery workers** — run ML inference and the Monte Carlo race simulator
  off the request path, on Redis Streams as the broker.
- **PostgreSQL (Supabase)** — primary datastore for laps, stints, races,
  strategy predictions.
- **Redis (Upstash)** — cache-aside for predictions, pub/sub for WebSocket
  fan-out, and the Celery broker/result backend.
- **React web app** — the primary client, deployed to Vercel.
- **Tauri desktop app** — same React codebase, native Windows build.
- **Expo mobile app** — React Native client for iOS/Android.

## Prerequisites

**Required:**
- Python 3.12+
- Node 20+
- Docker Desktop

**Required only if you're building the desktop app:**
- Rust (via [rustup](https://rustup.rs/))
- Visual Studio Build Tools (MSVC — Tauri's Windows build target)

**Optional:**
- kubectl + Helm — only needed for the local Kubernetes demo (`infra/k8s/`,
  `infra/helm-chart/`); not required for normal development, which runs on
  Docker Compose.

## Quick Start (local development)

```bash
git clone https://github.com/ArhaanAli04/f1-strategy-engine
cd f1-strategy-engine
cp .env.example .env
# Edit .env with your own credentials (DB, Redis, secrets — see .env.example
# for what each variable is for; local dev works with the file's defaults
# for DATABASE_URL/REDIS_URL as-is)

make dev              # starts Postgres, Redis, backend, worker, monitoring stack
cd web && npm ci && npm run dev   # starts the web app
# Open http://localhost:5173
```

The backend API is at `http://localhost:8000` (interactive docs at
`http://localhost:8000/docs`). `make dev-down` stops the Docker stack.

## Project Structure

| Directory | Contents |
|---|---|
| `backend/` | FastAPI app — API routes, services (business logic), SQLAlchemy models, Pydantic schemas, Alembic migrations, Celery workers, ingestion/training scripts, and the full test suite (unit/integration/e2e/load). |
| `web/` | React + Vite + TypeScript web app. Deployed to Vercel. |
| `desktop/` | Tauri v2 + React desktop app. Shares most of its source with `web/` via manual sync — see `desktop/src/README.md`. |
| `mobile/` | Expo (React Native) mobile app. Same manual-sync relationship to `web/` — see `mobile/src/README.md`. |
| `infra/` | Docker Compose files, Kubernetes manifests + Helm chart, and Prometheus/Grafana/Alertmanager monitoring config. |
| `docs/` | Architecture decisions, ML model documentation, load test results, and the operational runbook. |

## Available Make Targets

| Target | Description |
|---|---|
| `make install` | Install backend deps (editable, with dev extras) + web/desktop/mobile npm deps + pre-commit hooks. |
| `make dev` | Start the full Docker Compose stack (Postgres, Redis, backend, worker, monitoring). |
| `make dev-down` | Stop the Docker Compose stack. |
| `make test` | Run the full backend pytest suite. |
| `make test-unit` | Run unit tests only (`-m unit`, no DB/Redis/network). |
| `make test-int` / `make test-integration` | Run integration tests (`-m integration`, real Postgres + Redis via testcontainers). |
| `make test-e2e` | Run Playwright end-to-end tests (`-m e2e`). |
| `make lint` | `ruff check backend/ --fix`, `ruff format --check .`, `npm run lint` in `web/`. |
| `make type-check` | `mypy backend/ --strict` + `npx tsc -b` in `web/`. |
| `make migrate` | Apply Alembic migrations (`alembic upgrade head`). |
| `make new-migration MSG="..."` | Autogenerate a new Alembic revision. |
| `make train` | Run the ML training pipeline (`backend/scripts/train_models.py`). |
| `make seed-circuits` / `make seed-teams` | Seed circuit and team/driver-contract reference data. |
| `make ingest SEASON=... ROUND=... SESSION_TYPE=...` | Ingest one historical session via FastF1. |
| `make ingest-season SEASON=...` | Ingest every race session for a full season. |
| `make ingest-live SEASON=... [ROUND=... SESSION_TYPE=...]` | Ingest a live session (or poll for the next one). |
| `make warm-cache SESSION_ID=...` | Pre-warm strategy prediction caches ahead of a live session. |

## Documentation

| Doc | Covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Why each major technology was chosen, including tradeoffs that were reconsidered along the way. |
| [docs/ml-models.md](docs/ml-models.md) | All 7 ML models — type, features, training data, performance, retraining. |
| [docs/performance-report.md](docs/performance-report.md) | Day 35 load test results and the N+1 fix that produced them. |
| [docs/load_test_results.md](docs/load_test_results.md) | Running log of load test runs across the project (Day 13 onward). |
| [docs/runbook.md](docs/runbook.md) | Rollback, scaling, and secret rotation procedures. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Branching strategy, PR process, and local dev setup. |

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Backend API | FastAPI, SQLAlchemy 2.0 (async), Pydantic v2 | Async-native, auto-generated OpenAPI docs, request/response validation, and `mypy --strict` type safety throughout. |
| Task Queue | Celery + Redis Streams | ML inference and race simulation run in separate worker processes so they never block the API event loop, and scale independently of it. |
| ML | XGBoost, LightGBM, scikit-learn, NumPy, SciPy, Numba | Tabular data (~20 features, ~166k training rows) — gradient-boosted trees outperform and are far more interpretable than a neural net at this scale. Numba JIT-compiles the Monte Carlo simulation's hot loop. |
| Explainability | SHAP (TreeExplainer) | Native support for XGBoost/LightGBM; used to explain individual pit/tire-degradation predictions. |
| Data Ingestion | FastF1, httpx (async), websockets, APScheduler | FastF1 is the standard open-source F1 timing/telemetry library; async httpx and APScheduler drive both historical backfills and live-session polling. |
| Database | PostgreSQL (Supabase) | TimescaleDB was evaluated for `lap_data` but dropped — composite indexes on plain Postgres already return <1ms on 166k rows at this project's scale, so the hypertable's operational complexity wasn't justified. See docs/architecture.md. |
| Cache | Redis (Upstash), in-memory fallback | Cache-aside for predictions, pub/sub for WebSocket fan-out across API instances, and the Celery broker/result backend. |
| Migrations | Alembic | Async-engine, autogenerate-driven schema migrations. |
| Tests | pytest, testcontainers, Playwright, Locust | Unit (no DB/network) / integration (real Postgres+Redis) / e2e (real browser) / load, run as separate marked suites. |
| Containers | Docker (multi-stage), Kubernetes, Helm | Validated locally against Docker Desktop Kubernetes; production target is Fly.io, not Kubernetes (see docs/runbook.md). |
| CI/CD | GitHub Actions | Lint/type-check/test on every PR; separate CD workflows per client (web → Vercel, desktop → GitHub Releases, backend → TBD Day 40). |
| Web | React + Vite, TanStack Query, Zustand, Recharts | Fast dev server, server-state caching that matches the API's cache-aside model, minimal client state. |
| Desktop | Tauri v2 + React | Native OS WebView instead of bundled Chromium — same React source as `web/`, ~5MB binary instead of Electron's ~150MB. |
| Mobile | Expo (React Native) + Expo Router | Shared TypeScript/React knowledge with `web/`, single codebase for iOS and Android, managed workflow avoids native build setup. |
| Monitoring | Prometheus, Grafana, Sentry, Alertmanager | Metrics scraping + dashboards + exception tracking + Slack/PagerDuty alerting, running locally via Docker Compose today. |

## Live Demo

The web app deploys to Vercel automatically on every push to `main`
(`.github/workflows/cd-web.yml`), but its URL isn't published here yet: it's
currently built against a placeholder `VITE_API_URL_PROD`, because the
backend itself has no cloud deployment yet — that's planned for Day 40
(Fly.io). Once the backend is live, both the web app URL and interactive API
docs (`/docs`) will be linked here.

Until then, run everything locally via [Quick Start](#quick-start-local-development) above.
