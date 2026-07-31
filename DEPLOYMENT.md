# Deployment & Demo Strategy

This document covers how the F1 Strategy Engine is run, demonstrated, and developed across all three client platforms. It is intended for developers, interviewers, and contributors.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Local Backend — Full Stack](#local-backend--full-stack)
3. [Using the App — Quick Start](#using-the-app--quick-start)
4. [Demo Videos](#demo-videos)
5. [Mobile App — Development Build](#mobile-app--development-build)
6. [General Development Workflow](#general-development-workflow)
7. [Local Kubernetes Deployment (Docker Desktop)](#local-kubernetes-deployment-docker-desktop)
8. [Future Cloud Deployment](#future-cloud-deployment)

---

## Architecture Overview

The backend runs locally via Docker Compose. All three frontend clients (web, desktop, mobile) connect to the same local backend during development and demos.

```
┌─────────────────────────────────────────────────────────┐
│                    Local Machine                         │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  Web App     │  │ Desktop App  │  │  Mobile App  │  │
│  │  (React)     │  │  (Tauri)     │  │(React Native)│  │
│  │  :3000       │  │  native win  │  │  iPhone      │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                 │                  │           │
│         └─────────────────┴──────────────────┘           │
│                           │                              │
│                    HTTP / WebSocket                       │
│                           │                              │
│  ┌────────────────────────▼─────────────────────────┐   │
│  │              FastAPI Backend  :8000               │   │
│  │   REST API · WebSocket · Prometheus /metrics      │   │
│  └──────┬──────────────────────────┬────────────────┘   │
│         │                          │                     │
│  ┌──────▼──────┐          ┌────────▼────────┐           │
│  │    Redis    │          │   PostgreSQL     │           │
│  │   :6379     │          │   :5432          │           │
│  │  cache·pub  │          │  TimescaleDB     │           │
│  └──────┬──────┘          └────────┬────────┘           │
│         │                          │                     │
│  ┌──────▼──────────────────────────▼────────────────┐   │
│  │           Celery Worker (prediction_queue)        │   │
│  │   XGBoost · LightGBM · Monte Carlo · SHAP        │   │
│  └───────────────────────────────────────────────────┘   │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐                     │
│  │  Prometheus  │  │   Grafana    │                     │
│  │    :9090     │  │    :3000     │                     │
│  └──────────────┘  └──────────────┘                     │
└─────────────────────────────────────────────────────────┘
```

---

## Local Backend — Full Stack

### Prerequisites

- Docker Desktop installed and running
- `.env` file at repo root (copy from `.env.example` and fill in values)
- AWS credentials in `.env` for S3 model downloads

### Starting the Full Stack

```bash
# From repo root
docker compose -f infra/docker/docker-compose.yml --env-file .env up -d

# Verify all containers are healthy
docker compose -f infra/docker/docker-compose.yml ps
```

Expected running containers:

| Container | Service | Port |
|---|---|---|
| docker-backend-1 | FastAPI API | 8000 |
| docker-worker-1 | Celery worker | — |
| docker-postgres-1 | PostgreSQL/TimescaleDB | 5432 |
| docker-redis-1 | Redis | 6379 |
| docker-prometheus-1 | Prometheus | 9090 |
| docker-grafana-1 | Grafana | 3001 |
| docker-alertmanager-1 | Alertmanager | 9093 |
| docker-redis-exporter-1 | Redis metrics | 9121 |
| docker-postgres-exporter-1 | Postgres metrics | 9187 |

### Verify Everything Works

```bash
# API health check
curl http://localhost:8000/health

# API docs (Swagger UI)
open http://localhost:8000/docs

# Grafana dashboard (admin / admin-dev)
open http://localhost:3001

# Prometheus targets
open http://localhost:9090/targets
```

### Stopping the Stack

```bash
docker compose -f infra/docker/docker-compose.yml down
```

### What Each Service Provides

| Service | What It Does |
|---|---|
| FastAPI backend | REST API, WebSocket telemetry, Prometheus /metrics |
| Celery worker | ML inference, Monte Carlo simulation, alert dispatch |
| PostgreSQL | Persistent storage — lap data, predictions, users, alerts |
| Redis | Cache (TTL keys), Celery broker, pub/sub channels |
| Prometheus | Scrapes metrics every 10s from backend and worker |
| Grafana | 9-panel dashboard — latency, cache hit rate, ML inference time, WS connections, Celery queue depth |
| Alertmanager | Routes alerts to Slack (#alerts-critical, #alerts-warning) |

---

## Using the App — Quick Start

This section covers exactly what to do to start using the full system day-to-day — backend, web app, and mobile app on your iPhone.

### Step 1 — Start the Backend (always first)

Open a terminal at the repo root:

```bash
# Activate virtual environment
.venv\Scripts\activate

# Start all services
docker compose -f infra/docker/docker-compose.yml --env-file .env up -d

# Confirm everything is healthy (all containers should show "healthy" or "running")
docker compose -f infra/docker/docker-compose.yml ps
```

Takes about 30-60 seconds for all containers to be ready. You only need to do this once per session — containers stay running until you stop them.

### Step 2 — Open the Web App

```bash
cd clients/web
npm run dev
```

Open your browser at `http://localhost:3000`. Log in or register a new account. You now have access to:
- Live race dashboard with timing tower and circuit map
- Strategy simulator (pit window recommendations, Monte Carlo)
- Alert notifications panel
- Historical race analysis

### Step 3 — Use the Mobile App on Your iPhone

**Prerequisite: iPhone and laptop must be on the same WiFi network.**

```bash
# Find your laptop's local IP address
ipconfig
# Look for "IPv4 Address" under your WiFi adapter
# Example: 192.168.1.105
```

Make sure `clients/mobile/.env` has:
```
API_BASE_URL=http://192.168.1.105:8000
WS_BASE_URL=ws://192.168.1.105:8000
```
Replace `192.168.1.105` with your actual laptop IP.

Then start the Expo dev server:
```bash
cd clients/mobile
npx expo start
```

On your iPhone — tap the **F1 Strategy** app icon (installed via EAS Development Build). It connects automatically to your laptop and opens the app. Live reload is active — any code changes appear on your iPhone instantly.

> **Note:** If the app shows a connection error, your laptop's IP may have changed since you last connected. Run `ipconfig` again and update the `.env` file.

### Step 4 — Open the Desktop App

```bash
cd clients/desktop
npm run tauri dev
```

A native Windows application window opens — same functionality as the web app but as a standalone desktop application.

### Step 5 — Watch Live Telemetry (Optional)

To simulate live race data flowing through the system:

```bash
# In a separate terminal — replay a real 2025 race session
python backend/tests/load/replay_publisher.py \
  --session-id 00b4f598-40ec-4792-8687-6eae51257977 \
  --rate 5
```

This publishes real lap data through Redis pub/sub. The timing tower, circuit map, and strategy alerts all update in real time across all three clients simultaneously.

### Step 6 — View Monitoring Dashboard (Optional)

Open Grafana at `http://localhost:3001` (login: `admin` / `admin-dev`).

The F1 Strategy Engine dashboard shows:
- Request rate and latency per endpoint
- Redis cache hit rate
- Celery queue depth
- ML inference time per model
- Active WebSocket connections

### Stopping Everything

```bash
# Stop all Docker containers
docker compose -f infra/docker/docker-compose.yml down

# Stop the web app dev server: Ctrl+C in that terminal
# Stop the Expo dev server: Ctrl+C in that terminal
# Stop the replay publisher: Ctrl+C in that terminal
```

### Troubleshooting

| Problem | Fix |
|---|---|
| Container not starting | Check Docker Desktop is running, then `docker compose down` and `up -d` again |
| Mobile app can't connect | Run `ipconfig`, update `API_BASE_URL` in `clients/mobile/.env` with new IP |
| Strategy predictions failing | Check AWS credentials in `.env` — S3 model download may be failing |
| WebSocket not updating | Restart the backend container: `docker compose restart backend` |
| Grafana shows no data | Wait 30s after starting — Prometheus needs time to scrape first metrics |

---

## Demo Videos

All demo recordings are in the `demos/` directory and linked below. Videos show the system running locally with live F1 data from the 2025 season.

### Web App

| Video | Description | Duration |
|---|---|---|
| [Live Race Dashboard](demos/web-app/01-live-race-dashboard.mp4) | Circuit map with moving driver dots, timing tower updating via WebSocket | ~3 min |
| [Strategy Simulator](demos/web-app/02-strategy-simulator.mp4) | Monte Carlo simulation form → result with position probability distributions | ~2 min |
| [Pit Window with SHAP](demos/web-app/03-pit-window-shap.mp4) | Pit window recommendation with SHAP explanation showing top contributing features | ~2 min |
| [Alert System](demos/web-app/04-alerts.mp4) | Undercut threat alert appearing in notification panel, full alert pipeline | ~1 min |
| [Grafana Monitoring](demos/web-app/05-grafana-monitoring.mp4) | Live Grafana dashboard during load test — cache hit rate, Celery queue depth, ML inference time | ~2 min |

### Desktop App (Tauri)

| Video | Description | Duration |
|---|---|---|
| [Desktop Overview](demos/desktop-app/01-overview.mp4) | Native desktop app — same features as web but as a standalone application | ~2 min |
| [Offline Analysis](demos/desktop-app/02-offline-analysis.mp4) | Historical race analysis using locally cached data | ~2 min |

### Mobile App (React Native — iOS)

| Video | Description | Duration |
|---|---|---|
| [Live Race View](demos/mobile-app/01-live-race.mp4) | Mobile timing tower and circuit map during a race session | ~2 min |
| [Push Notification](demos/mobile-app/02-push-notification.mp4) | Undercut alert push notification arriving on iPhone, opening to strategy detail | ~1 min |
| [Strategy Cards](demos/mobile-app/03-strategy-cards.mp4) | Per-driver strategy cards with pit window recommendations | ~1 min |

### System & Infrastructure

| Video | Description | Duration |
|---|---|---|
| [CI Pipeline](demos/system/01-ci-pipeline.mp4) | GitHub Actions CI running all 5 jobs on a commit | ~1 min |
| [Load Test](demos/system/02-load-test.mp4) | Locust load test at 100 users with Grafana panels updating live | ~3 min |
| [Kubernetes Deploy](demos/system/03-kubernetes.mp4) | Helm deploy to local Kubernetes, pods scaling, rolling update | ~2 min |

---

## Mobile App — Development Build

The mobile app uses **Expo Development Build** — a custom version of Expo Go installed once on your iPhone. It does not require an Apple Developer account ($99/year) and does not need to be on the App Store.

### One-Time Setup

```bash
# Install EAS CLI
npm install -g eas-cli

# Login to Expo account (free at expo.dev)
eas login

# From the mobile app directory
cd clients/mobile

# Build the development client (runs in EAS cloud, ~15 minutes)
eas build --profile development --platform ios
```

When the build completes, EAS provides a QR code. Scan it with your iPhone camera to install the development build. The app icon appears on your home screen and stays there permanently.

### Daily Development Workflow

```bash
# 1. Start the backend stack
docker compose -f infra/docker/docker-compose.yml --env-file .env up -d

# 2. Find your laptop's local IP
ipconfig  # Windows — look for IPv4 Address under WiFi adapter
# Example: 192.168.1.105

# 3. Set the API URL in mobile app .env
# API_BASE_URL=http://192.168.1.105:8000
# WS_BASE_URL=ws://192.168.1.105:8000

# 4. Start Expo dev server
cd clients/mobile
npx expo start

# 5. Open the development build app on your iPhone
# Tap the app icon → it auto-connects to your laptop
# Code changes appear instantly (live reload)
```

### Requirements

- iPhone and laptop on the **same WiFi network**
- Backend Docker stack running on laptop
- Expo dev server running (`npx expo start`)

### When to Rebuild

A new EAS build is only needed when:
- Adding a new native Expo module (e.g. expo-camera, expo-notifications)
- Updating the Expo SDK version

Regular development (new screens, UI changes, API calls, new components) uses live reload — no rebuild needed.

### EAS Free Tier Limits

- 30 builds per month — more than sufficient for a 4-day build sprint
- No Apple Developer account required for personal device installation

---

## General Development Workflow

### Starting a Development Session

```bash
# 1. Start backend stack
docker compose -f infra/docker/docker-compose.yml --env-file .env up -d

# 2. Start whichever client you're working on:

# Web app
cd clients/web && npm run dev          # http://localhost:3000

# Desktop app
cd clients/desktop && npm run tauri dev  # opens native window

# Mobile app
cd clients/mobile && npx expo start    # scan QR with iPhone
```

### Making a Demo Recording

**Web app / Desktop app:**
- Use OBS Studio (free) or Windows Game Bar (Win+G) to record the screen
- Run the live telemetry replay publisher for realistic data:
  ```bash
  python backend/tests/load/replay_publisher.py \
    --session-id <session_id> --rate 5
  ```

**Mobile app:**
- iPhone built-in screen recorder: Settings → Control Center → Screen Recording
- Or QuickTime on Mac with iPhone connected via USB

### Sharing the System Temporarily (Remote Demo)

For a live demo over video call without cloud deployment:

```bash
# Install ngrok (free)
# Download from ngrok.com

# Start ngrok tunnel to your backend
ngrok http 8000
# → provides a public URL like https://abc123.ngrok-free.app

# Update client .env with the ngrok URL
# API_BASE_URL=https://abc123.ngrok-free.app
# WS_BASE_URL=wss://abc123.ngrok-free.app

# Share the frontend URL (if web app is deployed on Vercel)
# or screen share for desktop/mobile
```

The ngrok URL is active as long as your laptop is on and ngrok is running.

---

## Local Kubernetes Deployment (Docker Desktop)

This runs the backend + worker on a real local Kubernetes cluster (Docker
Desktop's built-in Kubernetes) via the Helm chart in `infra/helm-chart/` —
proving the deployment path works before any cloud cluster exists. **It
runs alongside docker-compose, not instead of it** — docker-compose keeps
owning Postgres/Redis/monitoring, and the K8s backend/worker pods reach
those same containers over the host network. Do not stop docker-compose
before or during this.

### Prerequisites

- Docker Desktop with Kubernetes enabled (Settings → Kubernetes → Enable
  Kubernetes). Verify: `kubectl cluster-info` should show a `127.0.0.1:<port>`
  control plane and `kubectl config current-context` should read
  `docker-desktop`.
- Helm installed (`helm version`). Install via `winget install Helm.Helm`
  if missing.
- docker-compose stack already running (`docker compose -f
  infra/docker/docker-compose.yml ps` — postgres and redis must be healthy),
  since this deployment does not include its own Postgres/Redis.

### 1. Build and Verify Images

```bash
docker build -f infra/docker/Dockerfile.backend -t f1-backend:local .
docker build -f infra/docker/Dockerfile.worker -t f1-worker:local .
```

No `kind load` step is needed here — despite Docker Desktop's Kubernetes
node running on a kind-style provisioner internally, it is not a cluster
the `kind` CLI manages, and it shares its containerd image store directly
with the Docker daemon. Images built above are already visible to the
cluster; `values.local.yaml` sets `imagePullPolicy: IfNotPresent` so pods
use the local image instead of attempting a registry pull.

### 2. Create the Namespace and Secrets

```bash
kubectl create namespace local

# Reads DATABASE_URL/TIMESCALE_URL/REDIS_URL/SECRET_KEY/AWS credentials from
# .env at repo root, rewriting "localhost" to "host.docker.internal" so
# pods in the cluster can reach docker-compose's Postgres/Redis over the
# host network.
./infra/k8s/create-secrets.sh local --rewrite-localhost
```

`create-secrets.sh` is gitignored (never committed) — it's a local-only
helper, present on disk from setup but not tracked in git. Re-run it any
time `.env` changes; it's idempotent.

### 3. Deploy with Helm

```bash
helm lint infra/helm-chart
helm template infra/helm-chart --values infra/helm-chart/values.local.yaml

helm upgrade --install f1-strategy-engine ./infra/helm-chart \
  --values infra/helm-chart/values.local.yaml \
  --namespace local

kubectl rollout status deployment/f1-strategy-engine-backend -n local --timeout=300s
kubectl get pods -n local
```

All pods should show `Running` with readiness passing (backend's `/health`
probe, worker's `celery inspect ping` probe).

### 4. Access the Backend

Docker Desktop's Kubernetes is **local-only, not publicly accessible** —
use `kubectl port-forward` to reach it. Host port 8000 is already taken by
docker-compose's own backend container, so this uses 8080:

```bash
kubectl port-forward svc/f1-strategy-engine-backend 8080:8000 -n local

# In another terminal:
curl http://localhost:8080/health   # expect 200 OK
```

For a demo that needs a public URL, use ngrok against the docker-compose
backend as documented above (`ngrok http 8000`) — the K8s deployment here
is a deployment-path proof, not the demo path.

### 5. Worker Autoscaling — KEDA

```bash
helm repo add kedacore https://kedacore.github.io/charts
helm install keda kedacore/keda --namespace keda --create-namespace

kubectl apply -f infra/k8s/worker-scaledobject.yaml -n local
kubectl get scaledobject -n local
```

`worker-scaledobject.yaml` scales on `prediction_queue`'s Redis list length
(see CLAUDE.md's `--pool=solo` scaling note), not CPU. Its `namespace:
local` and `address: host.docker.internal:6379` are local-validation
overrides — the file notes production restores its own namespace and adds
Redis auth back once a real cluster exists.

### 6. Race Weekend Pre-Scaling CronJob

```bash
kubectl apply -f infra/k8s/race-weekend-cronjob.yaml -n local
kubectl get cronjob -n local
```

Applying this only proves the CronJob/RBAC objects register correctly —
`backend/scripts/prescale_for_session.py` (the command it runs) doesn't
exist yet, so a scheduled run will fail until that script is written.

### Troubleshooting

| Problem | Fix |
|---|---|
| Pods stuck `ImagePullBackOff` | Confirm `docker build` used the exact tag in `values.local.yaml` (`f1-backend:local` / `f1-worker:local`); rebuild if stale |
| Backend pods not `Ready` | `kubectl logs -n local deploy/f1-strategy-engine-backend` — usually a DB/Redis connectivity issue; confirm docker-compose's postgres/redis are healthy and reachable at `host.docker.internal` |
| `create-secrets.sh` fails "namespace does not exist" | Run `kubectl create namespace local` first |
| `helm upgrade` fails on an existing release in a bad state | `helm status f1-strategy-engine -n local` to see what's wrong before retrying |

---

## Future Cloud Deployment

When cloud deployment is needed (job demo, always-on access):

| Component | Platform | Cost |
|---|---|---|
| FastAPI backend | Render Starter | $7/month |
| Celery worker | Render Background Worker | $7/month |
| PostgreSQL | Supabase free tier | $0 |
| Redis | Upstash free tier | $0 |
| S3 models | AWS S3 (existing) | ~$1/month |
| Web frontend | Vercel (existing) | $0 |
| **Total** | | **~$15/month** |

The Kubernetes manifests and Helm charts in `infra/helm-chart/` are ready for GKE/EKS deployment when needed — same Docker images, same configuration, pointed at cloud infrastructure instead of local.

Migration steps are documented in `docs/runbook.md`.