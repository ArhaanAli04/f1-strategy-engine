# Deployment & Demo Strategy

This document covers how the F1 Strategy Engine is run, demonstrated, and developed across all three client platforms. It is intended for developers, interviewers, and contributors.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Local Backend — Full Stack](#local-backend--full-stack)
3. [Using the App — Quick Start](#using-the-app--quick-start)
4. [Demo Videos](#demo-videos)
5. [Mobile App — Development Build](#mobile-app--development-build)
6. [Desktop App Build & Distribution](#desktop-app-build--distribution)
7. [General Development Workflow](#general-development-workflow)
8. [Local Kubernetes Deployment (Docker Desktop)](#local-kubernetes-deployment-docker-desktop)
9. [Production Deployment — Fly.io (After Day 40)](#production-deployment--flyio-after-day-40)
10. [Local Kubernetes — When to Use It](#local-kubernetes--when-to-use-it)


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
cd mobile

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
cd mobile
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

## Desktop App Build & Distribution

Covers producing a distributable Windows installer for the Tauri desktop
app and getting it into reviewers' hands, as opposed to `cargo tauri dev`
(development, live-reload, used elsewhere in this doc).

### Prerequisites

Already set up as part of Day 30 (see CLAUDE.md's Desktop Sync Protocol /
Architecture Decisions for how these were verified):

- rustup + cargo (`stable-x86_64-pc-windows-msvc` toolchain)
- Visual Studio Build Tools 2022 with the "Desktop development with C++"
  workload (provides the MSVC linker Rust needs on Windows)
- WebView2 runtime — pre-installed on Windows 10/11, no separate step

### Before Building — Sync Check

`desktop/src/` contains manual copies of several `web/src/` files (types,
api client, shared UI components — see CLAUDE.md's **Desktop Sync
Protocol** section and `desktop/src/README.md` for the exact file list).
Before cutting a release build, diff `desktop/src/` against `web/src/` for
anything changed since Day 30 and re-sync — a stale copy won't fail the
build, it'll just ship desktop with outdated types/API calls silently.

### Before Building — Point at the Real Backend

`desktop/.env.production` currently holds a placeholder:

```
VITE_API_URL=https://placeholder.fly.dev
```

Replace it with the real Fly.io backend URL once that's deployed (see
[Production Deployment — Fly.io](#production-deployment--flyio-after-day-40)):

```
VITE_API_URL=https://f1-strategy-engine-backend.fly.dev
```

A release build bundles whatever this file says at build time — there's no
runtime override once the installer is built, so get this right first.

### Production Build

```bash
cd desktop
cargo tauri build
```

First build: 10-15 minutes (full Rust dependency compile, release
profile). Subsequent builds are much faster (incremental). This is
separate from — and slower than — the `cargo tauri dev` 15-20 min *first*
compile mentioned earlier in this repo's setup notes; that one is a debug
build, this one is release + bundling.

### Output

```
desktop/src-tauri/target/release/bundle/
├── nsis/
│   └── f1-strategy-engine_x.x.x_x64-setup.exe   ← NSIS installer
└── msi/
    └── f1-strategy-engine_x.x.x_x64.msi          ← MSI installer
```

`x.x.x` is the `version` field in `desktop/src-tauri/tauri.conf.json`
(currently `0.1.0`). Both installers are produced by default
(`bundle.targets: "all"` in that same file) — either is fine to ship; NSIS
is the more common choice for a portfolio project since it doesn't require
elevated install permissions.

### Distributing via GitHub Releases

1. Upload the `.exe` (or `.msi`) installer as a release asset on the same
   GitHub Releases page already used for ML model releases.
2. Include a note in the release description warning about the Windows
   SmartScreen prompt (see below) so reviewers aren't caught off guard —
   an unexplained "Windows protected your PC" screen reads as broken
   software, not an unsigned binary.

### Windows SmartScreen Warning

Expected, not a bug. Windows SmartScreen flags any executable that isn't
code-signed with a certificate from a recognized CA, regardless of what
the app actually does.

- **Cause:** the installer has no EV code-signing certificate.
- **Cost to fix:** $300-500/year for an EV cert — out of scope for a
  portfolio project.
- **Workaround for reviewers:** on the SmartScreen dialog, click **"More
  info"** → **"Run anyway"**.
- **Alternative:** if asking a reviewer to click through a security
  warning feels like too much friction, share a screen recording of the
  app instead of the installer (see [Demo Videos](#demo-videos)) — same
  content, no SmartScreen prompt to explain.

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

## Production Deployment — Fly.io (After Day 40)

The recommended production deployment after all three clients are built. 
Fly.io runs the backend and worker containers publicly at $0-4/month. 
All features work including WebSockets — no compromises.

### Production Stack

| Component | Platform | Cost |
|---|---|---|
| FastAPI backend | Fly.io | $0-2/month |
| Celery worker | Fly.io | $0-2/month |
| PostgreSQL | Supabase (Day 23) | $0 |
| Redis | Upstash (Day 23) | $0 |
| ML Models | AWS S3 (Day 7) | ~$1/month |
| Web frontend | Vercel | $0 |
| **Total** | | **~$1-5/month** |

### One-Time Setup (After Day 40)

```bash
# Install Fly CLI
curl -L https://fly.io/install.sh | sh

# Login
fly auth login

# Deploy backend
fly launch --dockerfile infra/docker/Dockerfile.backend \
  --name f1-strategy-engine-backend \
  --region sin  # Singapore — closest to Mumbai
fly deploy

# Deploy worker
fly launch --dockerfile infra/docker/Dockerfile.worker \
  --name f1-strategy-engine-worker \
  --region sin
fly deploy
```

### Environment Variables to Set on Fly.io

```bash
# Set for both backend and worker apps
fly secrets set \
  DATABASE_URL=<supabase-pooler-url> \
  REDIS_URL=<upstash-rediss-url> \
  SECRET_KEY=<your-secret-key> \
  AWS_ACCESS_KEY_ID=<key> \
  AWS_SECRET_ACCESS_KEY=<secret> \
  AWS_BUCKET_NAME=f1-strategy-models \
  AWS_REGION=ap-south-1 \
  SENTRY_DSN=<your-dsn> \
  ENVIRONMENT=production \
  --app f1-strategy-engine-backend
```

### After Deployment

```bash
# Verify backend is healthy
curl https://f1-strategy-engine-backend.fly.dev/health

# Update frontend .env to point at Fly.io
# VITE_API_URL=https://f1-strategy-engine-backend.fly.dev
# VITE_WS_URL=wss://f1-strategy-engine-backend.fly.dev

# Deploy frontend to Vercel (points at Fly.io backend)
vercel deploy
```

### RAM Note

Fly.io free VMs are 256MB shared. Your backend loads XGBoost + LightGBM + 
SHAP at startup (~512MB needed). Upgrade to 512MB if needed:

```bash
fly scale memory 512 --app f1-strategy-engine-backend
# Cost: ~$1.94/month — still nearly free
```

### What Works on Fly.io

- ✅ All REST API endpoints
- ✅ WebSocket live telemetry
- ✅ ML inference (XGBoost, LightGBM, Monte Carlo)
- ✅ Celery background tasks
- ✅ SHAP explanations
- ✅ Push notifications
- ✅ Authentication (JWT)
- ✅ Always-on (no spin-down)
- ✅ Public permanent URL

---

## Local Kubernetes — When to Use It

Local Kubernetes (Docker Desktop) remains available for:

**Portfolio demonstrations:**
- Show Helm chart deployment to interviewers
- Demonstrate HPA auto-scaling behavior
- Show KEDA worker scaling based on Redis queue depth
- Prove zero-downtime rolling deployments

**Load testing with auto-scaling:**
- Run Locust at 100-500 users against local K8s
- Watch KEDA scale workers in real time
- Compare single-instance vs multi-replica performance

**Future cloud migration:**
- The same Helm chart in `infra/helm-chart/` deploys to GKE/EKS unchanged
- If real traffic ever demands it, migrate from Fly.io to GKE in one afternoon
- Same Docker images, same configuration, different cluster endpoint

```bash
# Resume local Kubernetes deployment any time
docker compose -f infra/docker/docker-compose.yml --env-file .env up -d
helm upgrade --install f1-strategy-engine ./infra/helm-chart \
  --values infra/helm-chart/values.local.yaml --namespace local
kubectl get pods -n local
```

The Kubernetes manifests and Helm charts in `infra/helm-chart/` are ready for GKE/EKS deployment when needed — same Docker images, same configuration, pointed at cloud infrastructure instead of local.

Migration steps are documented in `docs/runbook.md`.