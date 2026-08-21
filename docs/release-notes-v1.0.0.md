# v1.0.0 — F1 Strategy & Telemetry Engine

A full-stack, production-grade F1 race strategy platform: live telemetry
ingestion, ML-driven pit window and undercut/overcut predictions, Monte
Carlo race simulation, and a real-time timing tower — delivered across web,
Windows desktop, and mobile clients.

## What's included

- **Web app** — React + Vite, deployed on Vercel. Timing tower, strategy
  simulator, driver style analysis, live circuit map.
- **Windows desktop app** — Tauri v2 native build (this release's `.exe`/
  `.msi` installers), with an always-on-top overlay window for race-day
  glanceable strategy info alongside the main window.
- **React Native mobile app** — Expo Router, iOS/Android. Built and verified
  this sprint; not yet published to an app store (see Known limitations).
- **Backend** — FastAPI + Celery + PostgreSQL (Supabase) + Redis
  (Upstash), with 7 ML models (XGBoost/LightGBM) for tire degradation, pit
  prediction, and safety car probability, plus SHAP explainability.
- **Full CI/CD** — GitHub Actions for lint/test/build across all four
  codebases, weekly automated model retraining, and this desktop release
  pipeline.

## How to install the desktop app

1. Download `F1-Strategy-Engine_1.0.0_x64-setup.exe` (or the `.msi`) from
   this release's Assets below.
2. Run the installer. See the SmartScreen note below if Windows flags it.
3. Launch **F1 Strategy Engine** from the Start menu.
4. On first launch, the app expects a backend running at
   `http://localhost:8000` (see Known limitations) — start the backend
   stack per the main [README](../README.md) before connecting.

### Windows SmartScreen warning

Windows will likely show **"Windows protected your PC"** when you run the
installer. This app is not yet signed with a paid code-signing certificate,
which is what SmartScreen checks for — this is expected for an
independently-built installer, not a sign of a compromised file.

To proceed: click **More info**, then **Run anyway**. If you'd rather verify
the file yourself first, build from source instead (`desktop/README.md`
has the steps) or compare the installer's checksum against the one GitHub
Actions produced for this release (see the `cd-desktop.yml` workflow run
logs for this tag).

## Known limitations

- **The backend is not deployed to the cloud yet.** Fly.io deployment is
  planned for Day 40. Until then, the desktop app (and web/mobile apps)
  need a locally running backend (`make dev` from the repo root) to show
  real data — `desktop/.env.production`'s `VITE_API_URL` currently points
  at a placeholder (`https://placeholder.fly.dev`) and will be updated to
  the real Fly.io URL once that deploy lands.
- The mobile app has not been tested on a physical device this sprint (no
  Apple Developer account or Android emulator set up yet) — see
  `mobile/src/README.md` for testing options.
- The overlay window and some driver-detail chart features are newer and
  have seen less real-race exercise than the core timing tower / strategy
  views.
- This is a portfolio project, not a production service — see the
  "Out of scope" notes in `CLAUDE.md`'s Deferred Wiring section for
  what's intentionally not built (e.g. Kubernetes autoscaling, disaster
  recovery, a paid Supabase tier).

## Links

- [README](../README.md) — project overview, setup, features
- [Architecture](architecture.md)
- [ML Model Registry](ml-models.md)
- [Operational Runbook](runbook.md)
- [CONTRIBUTING](../CONTRIBUTING.md)
