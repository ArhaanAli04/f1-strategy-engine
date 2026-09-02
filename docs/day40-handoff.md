# Day 40 Handoff — Fly.io Deployment (paused)

Written mid-Day-40, pausing after A1-A3, before A4 (actual Fly.io account
setup + deploy). Read this in full before resuming — it's the single
source of truth for exactly where things stand.

## ⚠️ Two discrepancies to resolve before running A4

These surfaced between what's already committed to `fly.toml`/`docs/runbook.md`
this session and the next-steps as dictated for this handoff note. Resolve
both explicitly with the user before running any `fly` command — don't
silently pick one.

1. **App name mismatch.** `fly.toml`'s `app = "f1-strategy"` (chosen to match
   the original spec's example URL `https://f1-strategy.fly.dev/health`) and
   `docs/runbook.md`'s "Fly.io deployment" section both say `f1-strategy`.
   The dictated resume steps below say `fly apps create f1-strategy-engine`.
   **Confirm which name is intended before creating the app** — whichever is
   chosen, `fly.toml`'s `app =` line must match exactly or `fly deploy` will
   target the wrong (or a nonexistent) app.

2. **`fly launch --no-deploy` vs. `fly apps create`.** `docs/runbook.md`'s
   "Fly.io deployment" section explicitly recommends `fly apps create
   <name>` (claims the name, no deploy) and explicitly *against* `fly
   launch`, because `fly launch`'s interactive flow can detect the existing
   hand-authored `fly.toml` and offer to regenerate/overwrite it — risking
   silently losing the `[processes]` (web/worker/beat), per-process `[[vm]]`
   sizing, and `auto_stop_machines = "off"` config already built and
   reasoned through in `fly.toml`'s own header comment. The dictated resume
   steps below say `fly launch --no-deploy`. **If `fly launch` is run, do
   not accept any prompt to regenerate `fly.toml`** — confirm it detects and
   keeps the existing file before proceeding. Given the risk, `fly apps
   create <name>` (skipping `fly launch` entirely) is the safer choice and
   is what's actually documented in `docs/runbook.md`.

---

## 1. Current state

- **Branch:** `feature/day-40-flyio-deployment`
- **A1 complete:** `fly.toml`, `infra/fly/scale-race-weekend.sh` (executable),
  `Makefile` targets (`fly-race-up`/`fly-race-down`), `docs/runbook.md`
  updated (new "Fly.io deployment" section, race-day scaling procedure
  rewritten for Fly, secret rotation procedure updated, TOC anchors fixed).
- **A2 complete:** `web/src/hooks/useLiveTelemetry.ts` exposes
  `staleConnection` (true after 30s connected-but-silent), consumed by
  `web/src/components/telemetry/LiveTimingTower.tsx`'s new banner.
- **A3 complete:** `web/src/hooks/useStrategy.ts`'s `useSimulationResult`
  exposes `timedOut` (true after 60s pending), consumed by
  `web/src/pages/SimulatorPage.tsx`; mirrored identically in
  `desktop/src/hooks/useStrategy.ts` / `desktop/src/pages/SimulatorPage.tsx`.
- **Verified this session:** `cd web && npx tsc -b` clean, `cd desktop &&
  npx tsc --noEmit` clean, `cd web && npx vitest run` — 4 files, 12/12
  tests pass (one pre-existing test fixture updated for the new
  `staleConnection` field). `make -n fly-race-up`/`fly-race-down` dry-run
  correctly, Makefile tabs verified byte-level (`cat -A`).
- **A4 NOT STARTED:** no Fly.io account actions taken, nothing deployed.
- **Nothing committed yet** — see section 7. No git commands have been run
  this session (status, log, etc. included) per explicit instruction.

## 2. Deployment decision (already researched and approved)

- **Platform:** Fly.io — cheapest of every alternative researched
  (Railway, Render, Google Cloud Run, Koyeb all came out more expensive;
  see conversation history for the full comparison table).
- **Approach:** Hybrid, ~$7/mo net (worker+beat idle most of the month).
  - `web`: always-on, `shared-cpu-1x`, 1GB RAM. Never auto-stopped — the
    ~88s XGBoost/LightGBM/SHAP import cold start (CLAUDE.md's K8s
    startupProbe precedent) makes Fly's auto-stop a bad fit; the whole
    portfolio-browsable surface (dashboard, driver stats, historical
    races, pit-window/undercut/overview predictions — these run inline in
    `web` on a cache miss, not via Celery) rides on `web` alone.
  - `worker`: scaled to 0 machines between races, `shared-cpu-1x`, 1GB RAM
    when scaled up (revised up from an originally-proposed 512MB after
    tracing real OOM risk — see `fly.toml`'s comment and the Day 40
    research in conversation history).
  - `beat`: scaled to 0 machines between races, `shared-cpu-1x`, 512MB RAM
    when scaled up (revised up from an originally-proposed 256MB — beat
    finalizes the same Celery app as worker, importing xgboost/lightgbm/shap
    at module level even though it never runs inference).
  - Scale up before a race: `make fly-race-up`
  - Scale down after a race: `make fly-race-down`
- **Known degradation while worker/beat are at 0** (by design, made
  visible rather than silent — this is what A2/A3 built):
  - Live WebSocket timing tower shows the muted/blue "No live race data.
    Showing last completed race. Live timing is active during race
    weekends." banner after 30s of connected-but-silent.
  - `POST /strategy/simulate` → step-3 spinner swaps to "Strategy
    simulation requires an active race weekend. The worker is currently
    offline — scale up before the next race to enable this feature." after
    60s pending, with a "Try Again" button.
  - Auto race detection needs *both* `beat` (schedule) and `worker`
    (executes the actual `ingest_live_session.py` subprocess launch) — see
    CLAUDE.md's Auto Race Detection section.

## 3. Next steps — A4, in exact order

**Resolve the two discrepancies in the callout above first.**

1. Install flyctl (if not already installed):
   ```powershell
   powershell -c "iwr https://fly.io/install.ps1 -useb | iex"
   ```
2. Sign up at [fly.io](https://fly.io) — a credit card is required; there
   is no free tier as of 2026 (see Day 40 pricing research).
3. `fly auth login` (opens a browser).
4. Tell Claude Code: *"fly auth login complete, proceed with A4"* — it will
   then:
   - Confirm the app name against `fly.toml`'s `app =` line (see
     discrepancy #1 above) before creating anything.
   - Claim the app name (`fly apps create <name>` — see discrepancy #2
     above for why this is preferred over `fly launch --no-deploy`).
   - List every secret needed (`DATABASE_URL`/`TIMESCALE_URL` built from
     Supabase's transaction-pooler URL with the `+asyncpg` scheme swap,
     `REDIS_URL` from Upstash's `rediss://` URL, `SECRET_KEY`,
     `SENTRY_DSN`, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_REGION`/
     `AWS_BUCKET_NAME`, `ALLOWED_ORIGINS`, `ENVIRONMENT=production` —
     deliberately *not* `SLACK_WEBHOOK_DEPLOY`, which `core/config.py`
     never reads at runtime; see `docs/runbook.md`'s "Fly.io deployment"
     section for the exact command).
   - Guide through `fly secrets set` for each value (the user supplies the
     real values — Claude Code does not have them).
   - Run `fly deploy`.
   - Verify `curl https://<app>.fly.dev/health` returns
     `{"status":"ok","db":"ok","redis":"ok"}`.
   - Run `fly scale count worker=0 beat=0` (hybrid resting state).
   - Run `fly scale count worker=1 beat=1` (one-time boot verification —
     confirm both processes actually come up healthy before leaving them
     at 0; check `fly logs` for `celery@... ready.` and a beat scheduler
     tick).
   - Run `fly scale count worker=0 beat=0` again (back to resting state).

## 4. Post-deployment — Checkpoint B (not started)

- Update the `VITE_API_URL_PROD` GitHub Secret with the real Fly.io URL.
- Trigger `cd-web.yml` to redeploy Vercel (push an empty commit or run the
  workflow manually).
- Update `desktop/.env.production`'s `VITE_API_URL` with the real Fly.io
  URL (currently a placeholder — see file).
- Create `mobile/.env` (doesn't exist yet, already gitignored) with
  `EXPO_PUBLIC_API_URL`/`EXPO_PUBLIC_WS_URL` set to the real Fly.io URL —
  note the `EXPO_PUBLIC_` prefix, not `VITE_`.
- Verify the Vercel web app actually connects to the Fly.io backend
  end-to-end (sign in, confirm a real API call succeeds).
- Update `docs/runbook.md` with the confirmed-working production URL once
  verified (the deployment procedure is already documented; this is just
  swapping in the real hostname where relevant).

## 5. Remaining checkpoints (not started)

- **Checkpoint C — local integration test** (against docker-compose, *not*
  Fly.io — this is a separate local-stack test):
  ```bash
  docker compose up -d
  python backend/tests/load/replay_publisher.py \
    --session-id b5fafd04-5397-4b51-b732-875ba99d66fd --rate 5
  ```
  Then walk the 9-point verification list from the original Day 40 spec
  (timing tower updates, desktop overlay, strategy wall, undercut alerts,
  Grafana traffic, 22-driver position ordering, auto race detection beat
  firing — see conversation history for the full list).
- **Checkpoint D — final docs:**
  - `docs/final-metrics.md` (LOC by component, real test counts, real
    endpoint/task/model/table/migration counts, real CI run duration).
  - `docs/retrospective.md` (project summary, what worked, what would be
    done differently, what's next, remaining tech debt — see conversation
    history for the full outline the user provided).

## 6. Dutch GP dry run — separate from Day 40, but time-sensitive

Dutch GP, round 12, **August 23 13:00 UTC**.

- Run `make fly-race-up` at **12:30 UTC on August 23** (30 min before
  session start, per the race-day checklist pattern in `docs/runbook.md`).
- Run `make fly-race-down` after the race ends (**~16:00 UTC** estimate —
  confirm actual finish time closer to the date).

This requires A4 (a real deployed app) to be done first — if A4 hasn't
happened before August 23, this dry run has no Fly.io target to scale.

## 7. Files changed this session (uncommitted)

New files:
- `fly.toml`
- `infra/fly/scale-race-weekend.sh` (executable)
- `docs/day40-handoff.md` (this file)

Modified files:
- `Makefile` — `fly-race-up`/`fly-race-down` targets
- `docs/runbook.md` — new "Fly.io deployment" section, "Race day scaling
  procedure (Fly.io)" rewrite (was Kubernetes-based), "Secret rotation
  procedure" updated with Fly.io steps, TOC/anchors updated
- `web/src/hooks/useLiveTelemetry.ts` — `staleConnection` (30s timeout)
- `web/src/components/telemetry/LiveTimingTower.tsx` — WS banner
- `web/src/hooks/useStrategy.ts` — `useSimulationResult`'s `timedOut`
  (60s timeout)
- `web/src/pages/SimulatorPage.tsx` — timeout message + Try Again button
- `web/src/__tests__/LiveTimingTower.test.tsx` — mock fixture updated for
  the new `staleConnection` field
- `desktop/src/hooks/useStrategy.ts` — mirrors web's `timedOut`
- `desktop/src/pages/SimulatorPage.tsx` — mirrors web's timeout message

No git commands (add/commit/etc.) have been run this session — everything
above is working-tree-only. Staging and committing remain the user's step
per CLAUDE.md's Git Discipline / this project's established workflow.

## 8. Resume prompt

When resuming, paste this to Claude Code:

> Read docs/day40-handoff.md first. We are resuming Day 40 exactly where
> we left off. A1-A3 are complete. I have completed fly auth login.
> Proceed with A4 — fly apps create, secrets setup, and deployment. Do not
> re-implement anything already done.

Claude Code should read the ⚠️ discrepancies section first and confirm
resolution with the user before running any `fly` command.
