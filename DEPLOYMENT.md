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
9. [Production Deployment — Fly.io (Planned)](#production-deployment--flyio-planned)
10. [Local Kubernetes — When to Use It](#local-kubernetes--when-to-use-it)


---

## Architecture Overview

The backend runs locally via Docker Compose. All three frontend clients (web, desktop, mobile) connect to the same local backend during development and demos.

```
┌─────────────────────────────────────────────────────────┐
│                     Local Machine                       │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │   Web App    │  │ Desktop App  │  │  Mobile App  │   │
│  │   (React)    │  │   (Tauri)    │  │    (Expo)    │   │
│  │    :5173     │  │  native win  │  │ phone / emu  │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         │                 │                 │           │
│         └─────────────────┼─────────────────┘           │
│                           │                             │
│                    HTTP / WebSocket                     │
│                           │                             │
│  ┌────────────────────────▼──────────────────────────┐  │
│  │              FastAPI Backend  :8000               │  │
│  │    REST API · WebSocket · Prometheus /metrics     │  │
│  └──────┬──────────────────────────┬─────────────────┘  │
│         │                          │                    │
│  ┌──────▼──────┐          ┌────────▼────────┐           │
│  │    Redis    │          │   PostgreSQL    │           │
│  │    :6379    │          │      :5432      │           │
│  │ cache · pub │          │  laps · users   │           │
│  └──────┬──────┘          └────────┬────────┘           │
│         │                          │                    │
│  ┌──────▼──────────────────────────▼─────────────────┐  │
│  │        Celery worker  +  Celery beat              │  │
│  │    XGBoost · LightGBM · Monte Carlo · SHAP        │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
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
# From repo root. Always pass --env-file .env, or secrets are silently blank.
docker compose -f infra/docker/docker-compose.yml --env-file .env up -d
# (or simply: make dev)

# Verify all containers are healthy
docker compose -f infra/docker/docker-compose.yml ps
```

Expected running containers:

| Container | Service | Port |
|---|---|---|
| docker-backend-1 | FastAPI API | 8000 |
| docker-worker-1 | Celery worker | — |
| docker-beat-1 | Celery beat (schedules automatic race detection) | — |
| docker-postgres-1 | PostgreSQL (TimescaleDB extension installed) | 5432 |
| docker-redis-1 | Redis | 6379 |
| docker-prometheus-1 | Prometheus | 9090 |
| docker-grafana-1 | Grafana | 3000 |
| docker-alertmanager-1 | Alertmanager | 9093 |
| docker-redis-exporter-1 | Redis metrics | 9121 |
| docker-postgres-exporter-1 | Postgres metrics | 9187 |

### Verify Everything Works

```bash
# API health check
curl http://localhost:8000/health

# API docs (Swagger UI)
open http://localhost:8000/docs

# Grafana dashboard (user admin; password is GRAFANA_ADMIN_PASSWORD from .env, default "admin")
open http://localhost:3000

# Prometheus targets
open http://localhost:9090/targets
```

### Stopping the Stack

```bash
docker compose -f infra/docker/docker-compose.yml down
# (or: make dev-down)
```

### What Each Service Provides

| Service | What It Does |
|---|---|
| FastAPI backend | REST API, WebSocket telemetry, Prometheus /metrics; also launches Demo Replay runs |
| Celery worker | Per-lap processing, ML inference, Monte Carlo simulation, alert dispatch |
| Celery beat | Every 5 minutes, checks the F1 calendar and starts live ingestion when a race is about to begin |
| PostgreSQL | Persistent storage — lap data, predictions, users, alerts |
| Redis | Cache (TTL keys), Celery broker, pub/sub channels |
| Prometheus | Scrapes metrics from backend and worker |
| Grafana | 9-panel dashboard — latency, cache hit rate, ML inference time, WS connections, Celery queue depth |
| Alertmanager | Routes alerts to Slack (#alerts-critical, #alerts-warning) |

---

## Using the App — Quick Start

This section covers exactly what to do to start using the full system day-to-day: backend, web app, desktop app and mobile app.

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

The backend takes a minute or two to become healthy: importing the ML libraries is slow on a cold start. You only need to do this once per session; containers stay running until you stop them.

### Step 2 — Open the Web App

```bash
cd web
npm run dev
```

The web app needs `web/.env.local` with `VITE_API_URL=http://localhost:8000` and `VITE_WS_URL=ws://localhost:8000` (see [web/README.md](web/README.md)).

Open your browser at `http://localhost:5173`. Log in or register a new account. You now have access to:
- Race page with timing tower, circuit map, lap and sector charts, pit windows and undercut threats
- Demo Replay (replay a real 2026 race through the full pipeline)
- Strategy simulator (Monte Carlo, compare up to 4 pit strategies)
- Driver analytics and alerts

See [docs/features.md](docs/features.md) for the full tour.

### Step 3 — Open the Desktop App

```bash
cd desktop
npm run tauri dev
```

A native Windows application window opens, with the same core features as the web app plus the always-on-top overlay. See [desktop/README.md](desktop/README.md) for prerequisites (Rust, Visual Studio Build Tools).

### Step 4 — Open the Mobile App (Optional)

The mobile app runs on an Android emulator (free) or a physical device via an Expo development build. Full setup, including which paths need paid accounts, is in [mobile/README.md](mobile/README.md). In short:

1. Create `mobile/.env` with your backend's address:
   ```
   EXPO_PUBLIC_API_URL=http://<address>:8000
   EXPO_PUBLIC_WS_URL=ws://<address>:8000
   ```
   Use `10.0.2.2` on the Android emulator (its alias for your machine), or your laptop's LAN IP (`ipconfig`) on a physical device on the same WiFi. `localhost` won't work, because it points at the phone itself.
2. Start Metro:
   ```bash
   cd mobile
   npx expo start
   ```

> The mobile app has not yet been run on a physical device; it has been verified with type checks and Metro builds only.

### Step 5 — Watch a Race Play Through the System (Optional)

On the web app's race page, use the **Watch a Replay** panel to start one of the three curated 2026 races. The replay feeds real laps through the same workers, cache and WebSocket a live race uses, so the timing tower, circuit map, pit windows, undercut threats and alerts all update lap by lap across every connected client. Stop it from the same panel.

(`backend/tests/load/replay_publisher.py` also exists, but it is a load-testing helper: it only re-publishes lap events onto the WebSocket channel and doesn't run any predictions.)

### Step 6 — View Monitoring Dashboard (Optional)

Open Grafana at `http://localhost:3000` (user `admin`; the password is `GRAFANA_ADMIN_PASSWORD` from `.env`, default `admin`).

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

# Stop the web / desktop / Expo dev servers: Ctrl+C in each terminal
```

### Troubleshooting

| Problem | Fix |
|---|---|
| Container not starting | Check Docker Desktop is running, then `docker compose down` and `up -d` again |
| Features fail with auth or S3 errors after a restart | The stack was started without `--env-file .env`; recreate it with the flag |
| Mobile app can't connect | Check `EXPO_PUBLIC_API_URL` in `mobile/.env` (`10.0.2.2` on the emulator, current LAN IP on a device) |
| Strategy predictions failing | Check AWS credentials in `.env` — S3 model download may be failing |
| WebSocket not updating | Restart the backend container: `docker compose restart backend` |
| Worker still running old code | Workers don't hot-reload: `docker compose restart worker` |
| Grafana shows no data | Wait 30s after starting — Prometheus needs time to scrape first metrics |

---

## Demo Videos

No demo videos have been recorded yet. Screenshots and short clips of each
feature will live in [docs/features.md](docs/features.md), which lists
exactly what to capture. Until then, the quickest way to see the system
working is to run it locally and start a **Demo Replay** from the race page.

---

## Mobile App — Development Build

The mobile app uses an **Expo development build**: a custom build of the app, installed once, that then live-reloads from your laptop like Expo Go. It's needed because the app uses native modules (Skia, Reanimated, react-native-svg) that the stock Expo Go app doesn't include.

[mobile/README.md](mobile/README.md) is the full guide. The key facts:

| Target | Cost | Notes |
|---|---|---|
| Android emulator | Free | Recommended way to try the app; no device or paid account needed |
| Android phone | Free | Only a free Expo account |
| iPhone | Apple Developer Program, $99/year | Apple requires it to install a development build on a real device |

### One-Time Setup

```bash
# Install EAS CLI and log in (free Expo account)
npm install -g eas-cli
eas login

cd mobile
eas build --profile development --platform android   # free
eas build --profile development --platform ios       # needs Apple Developer account
```

Each build runs in Expo's cloud and gives you a link to install the result.

### Daily Development Workflow

```bash
# 1. Start the backend stack (repo root)
make dev

# 2. Point the app at your backend in mobile/.env
#    EXPO_PUBLIC_API_URL=http://<address>:8000
#    EXPO_PUBLIC_WS_URL=ws://<address>:8000
#    (10.0.2.2 on the Android emulator, your LAN IP on a physical device)

# 3. Start Metro
cd mobile
npx expo start

# 4. Open the development build on the emulator or device
#    Code changes appear instantly (live reload)
```

### When to Rebuild

A new EAS build is only needed when:
- Adding a new native Expo module (e.g. expo-camera, expo-notifications)
- Updating the Expo SDK version

Regular development (new screens, UI changes, API calls, new components) uses live reload — no rebuild needed. EAS's free plan includes a limited number of cloud builds per month; check Expo's pricing page for the current limit.

---

## Desktop App Build & Distribution

Covers producing a distributable Windows installer for the Tauri desktop
app and getting it into reviewers' hands, as opposed to `npm run tauri dev`
(development, live-reload, used elsewhere in this doc).

### Prerequisites

Already set up on the development machine (see CLAUDE.md's Desktop Sync Protocol /
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
anything changed since the last sync and re-sync — a stale copy won't fail the
build, it'll just ship desktop with outdated types/API calls silently.

### Before Building — Point at the Real Backend

`desktop/.env.production` currently holds a placeholder:

```
VITE_API_URL=https://placeholder.fly.dev
```

Replace it with the real Fly.io backend URL once that's deployed (see
[Production Deployment — Fly.io](#production-deployment--flyio-planned)):

```
VITE_API_URL=https://f1-strategy.fly.dev
```

A release build bundles whatever this file says at build time — there's no
runtime override once the installer is built, so get this right first.

### Production Build

```bash
cd desktop
npm run tauri build
```

First build: 10-15 minutes (full Rust dependency compile, release
profile). Subsequent builds are much faster (incremental).

### Output

```
desktop/src-tauri/target/release/bundle/
├── nsis/
│   └── F1 Strategy Engine_x.x.x_x64-setup.exe   ← NSIS installer
└── msi/
    └── F1 Strategy Engine_x.x.x_x64_en-US.msi    ← MSI installer
```

`x.x.x` is the `version` field in `desktop/src-tauri/tauri.conf.json`
(currently `1.0.0`). Both installers are produced by default
(`bundle.targets: "all"` in that same file) — either is fine to ship; NSIS
is the more common choice for a portfolio project since it doesn't require
elevated install permissions.

### Distributing via GitHub Releases

This is automated: pushing a version tag (e.g. `v1.0.0`) runs
`.github/workflows/cd-desktop.yml`, which builds on Windows and attaches both
installers to a GitHub Release, using `docs/release-notes-v1.0.0.md` as the
release text. To do it by hand instead:

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
  app instead of the installer — same content, no SmartScreen prompt to
  explain.

---

## General Development Workflow

### Starting a Development Session

```bash
# 1. Start backend stack
docker compose -f infra/docker/docker-compose.yml --env-file .env up -d

# 2. Start whichever client you're working on:

# Web app
cd web && npm run dev                 # http://localhost:5173

# Desktop app
cd desktop && npm run tauri dev       # opens native window

# Mobile app
cd mobile && npx expo start           # open the dev build on emulator/device
```

### Making a Demo Recording

**Web app / Desktop app:**
- Use OBS Studio (free) or Windows Game Bar (Win+G) to record the screen
- Start a **Demo Replay** from the race page so every panel has live, moving data
- For short GIFs, ScreenToGif (free) works well; see the screenshot checklist in [docs/features.md](docs/features.md)

**Mobile app:**
- Android emulator: the emulator's own screen-record button, or `adb shell screenrecord`
- iPhone (if you have a development build installed): Settings → Control Center → Screen Recording

### Sharing the System Temporarily (Remote Demo)

For a live demo over video call without cloud deployment:

```bash
# Install ngrok (free) from ngrok.com, then tunnel to your backend
ngrok http 8000
# → provides a public URL like https://abc123.ngrok-free.app
```

Then point the client at that URL:

- **Web / desktop:** `VITE_API_URL=https://abc123.ngrok-free.app` and `VITE_WS_URL=wss://abc123.ngrok-free.app`
- **Mobile:** `EXPO_PUBLIC_API_URL` and `EXPO_PUBLIC_WS_URL`, same values
- If the web app is served from another origin (e.g. Vercel), add that origin to the backend's `ALLOWED_ORIGINS`

Otherwise, screen share the desktop or mobile app directly. The ngrok URL is active as long as your laptop is on and ngrok is running.

---

## Local Kubernetes Deployment (Docker Desktop)

This runs the backend + worker on a real local Kubernetes cluster (Docker
Desktop's built-in Kubernetes) via the Helm chart in `infra/helm-chart/` —
proving the deployment path works before any cloud cluster exists. The chart
doesn't include Postgres or Redis: the pods connect to the cloud databases
(Supabase and Upstash) using the URLs in `.env`. Keep docker-compose running
anyway if you use KEDA (step 5), whose local trigger reads docker-compose's
Redis.

### Prerequisites

- Docker Desktop with Kubernetes enabled (Settings → Kubernetes → Enable
  Kubernetes). Verify: `kubectl cluster-info` should show a `127.0.0.1:<port>`
  control plane and `kubectl config current-context` should read
  `docker-desktop`.
- Helm installed (`helm version`). Install via `winget install Helm.Helm`
  if missing.
- `.env` contains `SUPABASE_DATABASE_URL` and `UPSTASH_REDIS_URL` (the pods
  use these, not `.env`'s local `DATABASE_URL`/`REDIS_URL`).

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

# Builds DATABASE_URL/TIMESCALE_URL from SUPABASE_DATABASE_URL (with the
# +asyncpg driver) and REDIS_URL from UPSTASH_REDIS_URL, plus SECRET_KEY,
# SENTRY_DSN and AWS credentials, all read from .env at repo root.
./infra/k8s/create-secrets.sh local
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
| Backend pods not `Ready` | Allow up to 5 minutes: the ML imports make startup slow, and the startup probe waits for it. Then `kubectl logs -n local deploy/f1-strategy-engine-backend` — usually a DB/Redis connectivity issue; check `SUPABASE_DATABASE_URL`/`UPSTASH_REDIS_URL` in `.env` and re-run `create-secrets.sh` |
| Pods keep running old code after `docker build` | The cluster doesn't re-resolve an image tag it has already cached. Tag each build uniquely (e.g. `f1-backend:<git-sha>`) and pass it with `--set backend.image.tag=... --set worker.image.tag=...` |
| Crash loop with completely empty logs | The container was killed before it logged anything — usually the liveness probe firing during the slow ML import. Check the chart still has its `startupProbe` |
| `create-secrets.sh` fails "namespace does not exist" | Run `kubectl create namespace local` first |
| `helm upgrade` fails on an existing release in a bad state | `helm status f1-strategy-engine -n local` to see what's wrong before retrying |

---

## Production Deployment — Fly.io (Planned)

> **Not live yet.** This section is the deployment *strategy*: what to run,
> where, and what it costs. It was revised on 2026-09-25 and replaces the
> earlier "hybrid" plan. The step-by-step commands (secrets, deploy, rollback)
> are in [docs/runbook.md's Fly.io deployment section](docs/runbook.md#flyio-deployment).
> Do **not** run `fly launch`, which can overwrite the committed `fly.toml`.

### Why the hybrid plan was dropped

The original plan kept only `web` running all the time and scaled `worker` and
`beat` to zero except on race weekends, for about $7/month. That made sense
when the worker was only needed during live races.

Demo Replay changed that. A visitor can start a replay of a real race at any
time, and a replay needs the worker: every replayed lap is processed and
predicted by Celery tasks. The What-If Simulator also runs on the worker. With
the worker scaled to zero, the two features that best show off the system
would be broken most of the month. So the worker has to be available at all
times.

### What was measured (2026-09-25)

Running the worker all the time isn't just "scale it to 1". These findings come
from the local Docker stack and Fly's and Upstash's pricing pages.

**1. Region is the biggest cost lever.** Fly prices regions differently.
`fly.toml` currently uses Singapore (`sin`), which costs 2x.

| Region | Price multiplier |
|---|---|
| Amsterdam (`ams`), Ashburn (`iad`) | 1.0x |
| Frankfurt, London, Paris, Stockholm, Chicago, Dallas and others | 1.21x |
| Singapore (`sin`), Tokyo, Sydney | 2.0x |
| Mumbai (`bom`), Johannesburg | 3.0x |

| shared-cpu-1x machine, running 24/7 | at 1.0x | in `sin` (2.0x) |
|---|---|---|
| 512 MB | $3.69/month | $7.38/month |
| 1 GB | $6.57/month | $13.14/month |
| 2 GB | $12.36/month | $24.72/month |

Singapore was chosen because Supabase and the S3 model bucket are in Mumbai
(`ap-south-1`). Production Supabase holds only the 3 curated Demo Replay races
plus reference data (circuits, teams, drivers), so moving it to another region
is a small job.

**2. An idle worker would cost more in Redis than in compute.** Thirty seconds
of Redis traffic from an idle local worker showed about 2 commands a second,
roughly 5.5 million a month:

| Source | Rate | Per month |
|---|---|---|
| Celery polling for new tasks (`BRPOP`, 1 s timeout) | ~1/s | ~2.6M |
| Our queue-depth metrics poller (`LLEN` on 3 queues every 5 s, `celery_app._poll_queue_depth`) | ~0.6/s | ~1.6M |
| Celery worker heartbeats (`PUBLISH`, every 2 s) | ~0.5/s | ~1.3M |

Fly's health check adds more: it calls `/health` every 10 seconds, and
`/health` pings Redis, which is about 260K commands a month.

Upstash's free tier allows 500K commands a month, then charges $0.20 per 100K.
Untuned, that's about $10/month for a worker doing nothing.

**3. The web machine would likely run out of memory during a replay.** Demo
Replay runs `replay_pipeline.py` as a subprocess on the **web** machine, not on
the worker. Measured locally: the web process uses about 560 MB once models
are loaded, and the replay process needs about 390 MB just to start. That's
roughly 950 MB on the 1 GB machine `fly.toml` currently specifies.

**4. Startup could take around 12 minutes on a shared CPU.** A Fly shared vCPU
is guaranteed 6.25% of a core. It can burst to 100% using saved-up credit, but
a new machine starts with only 5 seconds of credit (the maximum is 500 seconds,
earned while idle). Importing the app measured about 50 CPU-seconds locally: 5
seconds at full speed, then about 45 seconds of work at 6.25%, which is roughly
12 minutes. That is far past the 300-second health-check grace in `fly.toml`.
About 20 of those 50 seconds come from one import chain,
`backend.services.ml.driver_style` → `umap` → `pynndescent`, which recompiles
numba code on every start. It's loaded at startup even though only the driver
style page uses it.

**5. The separate beat machine is wasteful.** `beat` uses about 310 MB to send
one task (`check_for_live_session`) every 5 minutes. Celery can run the
scheduler inside the worker process (`celery worker -B`), which is safe with a
single worker.

### Options compared

| Option | Monthly cost | Verdict |
|---|---|---|
| Hybrid (web always on; worker and beat only on race weekends), `sin` | ~$13 + race weekends | Replay and simulator broken most of the time. Rejected. |
| Current `fly.toml` run always-on (web 1 GB, worker 1 GB, beat 512 MB), `sin`, untuned | ~$34 compute + ~$10 Upstash ≈ **$44** | Works (after the startup and memory fixes) but pays twice for region and Redis. |
| **Recommended:** web 1 GB + worker 1 GB with beat built in, Redis tuned, 1.0x region | $13.14 + ~$1 S3 ≈ **$14** | Everything available 24/7 at the lowest cost that doesn't hurt the experience. |
| Same as recommended, but stay in `sin` with Mumbai data stores | $26.28 + ~$1 ≈ **$27** | No database move, but twice the compute price. |
| Everything on one 2 GB machine (web + worker + beat) | $12.36 + ~$1 ≈ $13 | Saves $0.78 but shares one CPU allowance between the site and heavy ML work, so the site slows during every replay or simulation. Needs a process manager. Not worth it. |
| Scale to zero with Fly auto-stop/auto-start | a few dollars | A stopped machine restarts with 5 seconds of CPU credit, so a visitor's first page waits minutes. The worker can't auto-start at all (no public port for Fly's proxy to wake it on). Not suitable. |

### Recommended setup

| Component | Where | Size | $/month |
|---|---|---|---|
| `web` (FastAPI, plus replays as a subprocess) | Fly.io, 1.0x region | shared-cpu-1x, 1 GB + 512 MB swap | $6.57 |
| `worker` (Celery, scheduler built in with `-B`) | Fly.io, same region | shared-cpu-1x, 1 GB | $6.57 |
| Redis (cache, broker, pub/sub) | Upstash free tier, same region | — | $0 |
| PostgreSQL | Supabase free tier, same region | — | $0 |
| ML models | AWS S3 (`ap-south-1`, read once per process start) | — | ~$1 |
| Web frontend | Vercel | — | $0 |
| **Total** | | | **~$14** |

Two separate machines cost only $0.78 more than one shared machine and give
the site and the ML work separate CPU allowances. Keep the shared IPv4 that
Fly assigns; a dedicated IPv4 ($2/month) isn't needed for HTTP.

**Region decision (open).**

- **Recommended: Amsterdam (`ams`), with Supabase and Upstash moved to
  Frankfurt (`eu-central-1`).** Cheapest price tier, about 8 ms from the
  databases, and a reasonable middle ground for visitors (~120 ms from India,
  ~85 ms from the US East Coast).
- **Alternative: Ashburn (`iad`), with Supabase and Upstash in `us-east-1`.**
  Same price, and the better choice if most visitors are in the US.
- **Staying in `sin`** avoids the database move but costs about $27/month and
  still pays ~60 ms per database round trip to Mumbai.

### What needs to be done

**Code and config changes (before the first deploy)**

1. **Merge beat into the worker.** Add `-B --schedule=/tmp/celerybeat-schedule`
   to the `worker` command in `fly.toml` and remove the `beat` process group and
   its `[[vm]]` block.
2. **Cut idle Redis traffic to fit Upstash's free tier:**
   - Start the worker with `--without-heartbeat --without-gossip --without-mingle`.
   - Set `broker_transport_options={"polling_interval": 30}` in
     `backend/workers/celery_app.py`. This only makes the idle `BRPOP` loop
     re-poll less often; a new task is still picked up immediately.
   - Make the queue-depth poller configurable and turn it off on Fly, where no
     Prometheus scrapes it.
   - Raise the Fly health-check `interval` from 10 s to 60 s.
   - Estimated result: about 150–400K commands a month before visitor and
     replay traffic. If real usage goes over the free tier, the overage is a
     few dollars; the fallback is a self-hosted Redis on a 256 MB Fly machine
     ($2.25/month, no per-command billing).
3. **Give web enough memory for replays.** Add 512 MB of swap to the web
   machine. If swap proves too slow, move web to 2 GB (+$5.79/month).
4. **Cut startup CPU:**
   - Import `umap` inside the driver-style fitting function instead of at module
     level, so the site doesn't pay ~20 CPU-seconds at every boot.
   - Set `NUMBA_CACHE_DIR` to a directory baked into the Docker image, so
     numba-compiled code isn't rebuilt on every start.
5. **Update `primary_region` in `fly.toml`** to the chosen region, and update
   `fly.toml`'s header comment, which still describes the hybrid plan.

**Infrastructure (only if moving region)**

6. Create a new free Supabase project in the chosen region. Run the Alembic
   migrations, the circuit/team/outline seeds, the 3 curated race ingests and
   the backfills (`session_elapsed_seconds`, tyre degradation, session total
   laps). Confirm the Demo Replay position data (`driver_positions`) is present
   too, not only locally.
7. Create a new free Upstash database in the same region.
8. Update the `SUPABASE_DIRECT_URL` GitHub secret (used by CI migrations and
   the keep-alive job) and the Fly secrets.

**Deploy and verify**

9. Follow the runbook's Fly.io procedure, with the changes above.
10. Re-measure on Fly, because the numbers above come from Docker Desktop on
    Windows:
    - time from deploy to a healthy `/health`
    - web memory while a replay runs
    - Upstash's command count after a few idle days
11. Point the frontend at the new backend: update the `VITE_API_URL_PROD`
    GitHub secret, `desktop/.env.production`, and the backend's
    `ALLOWED_ORIGINS`.
12. Retire the race-weekend scaling steps in the runbook (`make fly-race-up` /
    `fly-race-down`), which only existed for the hybrid plan.

Pricing sources: [Fly.io pricing](https://docs.fly.io/about/pricing/),
[Fly.io shared CPU quotas](https://docs.fly.io/machines/cpu-performance/),
[Upstash Redis pricing](https://upstash.com/pricing/redis). Prices checked
2026-09-25.

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
- The Helm chart in `infra/helm-chart/` is a starting point for a managed
  cluster (GKE/EKS) if real traffic ever outgrows Fly.io, using the same
  Docker images

```bash
# Resume local Kubernetes deployment any time
docker compose -f infra/docker/docker-compose.yml --env-file .env up -d
helm upgrade --install f1-strategy-engine ./infra/helm-chart \
  --values infra/helm-chart/values.local.yaml --namespace local
kubectl get pods -n local
```

The chart and manifests have only been validated against this local cluster.
Before a real cloud cluster, they would need:
- images pushed to a registry, instead of the local `:local` tags
- a production namespace
- Redis authentication restored in `infra/k8s/worker-scaledobject.yaml`
- secrets managed properly (e.g. Sealed Secrets) instead of `create-secrets.sh`
- the missing `backend/scripts/prescale_for_session.py`, if the race-weekend
  CronJob is kept

Each manifest's header comment lists its local-only overrides.