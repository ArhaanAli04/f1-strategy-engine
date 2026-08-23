# Operational Runbook

Procedures for rolling back a bad deploy, migration, or model; scaling for
race day; and rotating secrets. Written Day 21, ahead of the Day 22
Kubernetes deployment. Deployment names below (`f1-strategy-engine-backend`/
`f1-strategy-engine-worker`) are confirmed against the actual Helm chart's
generated resource names (`{{ include "f1-strategy-engine.fullname" . }}-backend`/
`-worker`) — verified directly against a running cluster on Day 24, not just
inferred from the templates.

**Production is Fly.io, not Kubernetes (decided Day 24, real deploy landed
Day 40 — see [Fly.io deployment](#flyio-deployment) below).**
`infra/helm-chart/` and the kubectl/Helm procedures in this runbook (App
rollback, the old Race day scaling procedure, the Kubernetes-secret half of
Secret rotation) were built and validated only against the local Docker
Desktop cluster (`-n local`; see CLAUDE.md's Deployment Strategy and Day
22/24 notes) — there is no `production` Kubernetes namespace or cluster and
there never will be one; `infra/helm-chart/`/`infra/k8s/` remain local-only
reference material, not something running in production. Every `-n
production` command below is local-cluster procedure, not a real target —
substitute `-n local` if you're actually running one of these today. Fly.io
equivalents (deploy, rollback, scaling, secrets) are their own sections
below, not a 1:1 mapping of the Kubernetes commands.

## Table of Contents

- [Race day checklist](#race-day-checklist)
- [Common issues and fixes](#common-issues-and-fixes)
- [How to replay a historical session for testing](#how-to-replay-a-historical-session-for-testing)
- [Fly.io deployment](#flyio-deployment)
- [App rollback (Helm — local cluster only)](#app-rollback-helm--local-cluster-only)
- [Database rollback (Alembic)](#database-rollback-alembic)
- [Model rollback (S3)](#model-rollback-s3)
- [Promoting a candidate model to production](#promoting-a-candidate-model-to-production)
- [Race day scaling procedure (Fly.io)](#race-day-scaling-procedure-flyio)
- [Secret rotation procedure](#secret-rotation-procedure)

---

## Race day checklist

Step-by-step, before a live race session. This is the pre-flight list — see
[Race day scaling procedure](#race-day-scaling-procedure) below for the
Kubernetes-specific pre-scaling steps once a real cluster target exists (it
doesn't yet — see that section's own caveat).

1. **Unpause the Supabase project**, if it's been idle. The free tier pauses
   a project after 7 days of inactivity — check the
   [Supabase dashboard](https://supabase.com/dashboard) and unpause if
   needed (takes ~2 minutes to come back up).
2. **Verify Upstash Redis is active** — check the Upstash console; Upstash's
   free tier doesn't auto-pause the way Supabase's does, but confirm it's
   reachable before relying on it (`redis-cli -u "$UPSTASH_REDIS_URL" ping`
   from a shell that has the URL, or a quick console check).
3. **Start the full local stack:**
   ```bash
   make dev   # docker compose up --build — see Makefile
   ```
4. **Verify the backend is healthy:**
   ```bash
   curl localhost:8000/health
   ```
5. **Start live ingestion** for the session about to run, with the correct
   season/round (or `--poll` to wait for the next scheduled session):
   ```bash
   make ingest-live SEASON=2026 ROUND=<round> SESSION_TYPE=R
   # = python backend/scripts/ingest_live_session.py --season 2026 --round <round> --session-type R
   ```
6. **Open the web app and verify the timing tower populates** —
   `http://localhost:5173` (`cd web && npm run dev` if not already running),
   confirm laps are showing up for the drivers as the session progresses.
7. **Verify Grafana shows traffic** — `http://localhost:3000`, confirm the
   ingestion/API dashboards show non-zero request/lap-processing rates.
8. **Keep `replay_publisher.py` available as a fallback.** If live FastF1
   ingestion stalls or errors out mid-session, `replay_publisher.py` against
   a known-good historical session (see
   [How to replay a historical session for testing](#how-to-replay-a-historical-session-for-testing))
   is the fastest way to get a populated timing tower back for a demo,
   even though it isn't the actual live session.

---

## Common issues and fixes

| Issue | Fix |
|---|---|
| **Supabase project paused** — DB connections start timing out or refusing. | Supabase free tier pauses after 7 days of inactivity. A GitHub Actions cron (`.github/workflows/keep-supabase-alive.yml`) runs every 5 days to keep it active, so this should be rare — if it happens anyway, go to supabase.com → your project → **Restore project** (takes ~2 minutes to come back online); retry the failing operation after that. |
| **Celery worker not picking up tasks** — `prediction_queue` depth grows but nothing drains it. | `docker compose restart worker`. Celery workers don't hot-reload code changes either (see `CLAUDE.md`'s "Celery worker — restart required after code changes") — restart after any `backend/workers/` edit too. |
| **Redis connection refused (production/Upstash only)** — works locally but not against the cloud Redis. | Confirm `REDIS_URL`/`UPSTASH_REDIS_URL` uses the **`rediss://`** (TLS) scheme, not `redis://` — Upstash requires TLS. Celery specifically needs this stated explicitly (`ssl_cert_reqs`) or it crashes at worker boot; see `CLAUDE.md`'s "Celery + Upstash's `rediss://`" note — already fixed in `workers/celery_app.py`, but a hand-edited `.env` that drops the `s` in `rediss://` will reproduce this. |
| **ML models not loading** — worker/backend errors on first prediction request. | Two independent causes to check: (1) AWS credentials — `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` must be set in the container's environment (boto3's default credential chain does not read pydantic-settings `.env` values — see `CLAUDE.md`'s AWS Credentials note); (2) `libgomp1` — LightGBM `dlopen()`s it at import time, and `python:3.11-slim` strips it by default. Both `Dockerfile.backend`/`Dockerfile.worker` install it in their final stage; if you're running outside those images (e.g. a bare venv on a minimal Linux box), install it manually. |
| **FastF1 403 error fetching a session.** | For **current-season (2026) data specifically, this is a known, not-yet-fully-fixed gap** — see `CLAUDE.md`'s "retrain_incremental.py FastF1 403→mirror fallback for 2026 data" entry. FastF1 automatically falls back to `livetiming-mirror.fastf1.dev` on a 403 from `livetiming.formula1.com`, but that mirror has no 2026 data (it only patches a couple of corrupted 2021-2022 sessions), so the fallback itself fails with `SessionNotAvailableError`. Rounds are currently skipped gracefully rather than crashing the run. On race day, if this hits live ingestion: try `fastf1.Cache.clear_cache()` and retry once — a stale/corrupted cache entry is the most common transient cause — and fall back to `replay_publisher.py` (see the race day checklist above) if it doesn't clear. For **historical (2018-2025) data**, a 403 here is unexpected — retry, and if it persists, check whether FastF1's upstream source has changed. |
| **Supabase connection string changed** (e.g. after a password rotation or a Supabase-side pooler change). | Get the new session-mode pooler URL from the Supabase dashboard (Project Settings → Database → Connection string, "Session mode") and update the `SUPABASE_DIRECT_URL` GitHub Secret with it — this is what `cd.yml`'s migration job uses (see `.env.example`'s comment on why session mode specifically: the transaction-mode pooler used for app runtime doesn't support the advisory locks/prepared statements a migration needs). Update `SUPABASE_DATABASE_URL` too if the transaction-mode pooler URL also changed, and re-run `infra/k8s/create-secrets.sh` if a local Kubernetes Secret needs to pick up the change (see [Secret rotation procedure](#secret-rotation-procedure)). |

---

## How to replay a historical session for testing

`backend/tests/load/replay_publisher.py` republishes a previously-ingested
session's lap-completion events onto the same Redis pub/sub channel live
ingestion would use, at a configurable rate — useful for testing the
WebSocket/frontend pipeline (or as a race-day fallback, see the checklist
above) without a live session actually running.

```bash
python backend/tests/load/replay_publisher.py --session-id <uuid> --rate 5
```

`--rate` is laps/second-equivalent publish speed (see the script's own
`--help` for the exact semantics); the session must already be ingested
into Postgres (either historically via `make ingest`, or live via
`make ingest-live` in an earlier session).

Known-good session IDs (verified directly against the local Postgres —
`sessions` joined to `races`/`circuits`, `season = 2025`, `session_type =
'R'`):

| Race | Session ID |
|---|---|
| 2025 Abu Dhabi (Yas Marina Circuit) | `b5fafd04-5397-4b51-b732-875ba99d66fd` |
| 2025 Brazil (Autódromo José Carlos Pace) | `a4410511-cdcb-49e4-ae0c-ab9896bfff3c` |
| 2025 Singapore (Marina Bay Street Circuit) | `1c70522f-010a-466b-8dee-f440ad8e88ba` |

These are specific to whatever's currently in the local database — if it's
been reseeded/reingested, re-derive them with a query like:
```sql
SELECT s.id, r.season, c.name, s.session_type
FROM sessions s
JOIN races r ON s.race_id = r.id
JOIN circuits c ON r.circuit_id = c.id
WHERE r.season = 2025 AND s.session_type = 'R'
ORDER BY c.name;
```

---

## Fly.io deployment

Single Fly app (`f1-strategy`, `fly.toml` at repo root) with three process
groups sharing one build — `web` (FastAPI, always-on), `worker` (Celery,
scaled to 0 except race weekends), `beat` (Celery Beat, same). See
`fly.toml`'s own header comment for the architecture reasoning (single app
vs. three, sizing, why `web` doesn't auto-stop) — this section is the
day-to-day procedure, not the reasoning.

### Prerequisites (one-time)

1. Create a Fly.io account at [fly.io](https://fly.io) and install `flyctl`:
   ```powershell
   powershell -c "iwr https://fly.io/install.ps1 -useb | iex"
   ```
2. `fly auth login`
3. `fly apps create f1-strategy` — claims the app name; does not deploy.
   `fly.toml` already exists and is complete, so **do not** run `fly
   launch` (its interactive flow can regenerate/overwrite `fly.toml`).

### Secrets

One `fly secrets set` call populates all three process groups (they share
the app). Same conversion `infra/k8s/create-secrets.sh` already does for
the local Kubernetes Secret — Supabase's transaction-mode pooler URL (port
6543) becomes `DATABASE_URL`/`TIMESCALE_URL` with the `+asyncpg` scheme
swap, Upstash's `rediss://` URL becomes `REDIS_URL` directly:

```bash
fly secrets set --app f1-strategy \
  DATABASE_URL="postgresql+asyncpg://...<SUPABASE_DATABASE_URL, scheme swapped>..." \
  TIMESCALE_URL="postgresql+asyncpg://...<same as DATABASE_URL>..." \
  REDIS_URL="<UPSTASH_REDIS_URL, rediss://...>" \
  SECRET_KEY="<production SECRET_KEY>" \
  SENTRY_DSN="<production SENTRY_DSN>" \
  AWS_ACCESS_KEY_ID="<...>" \
  AWS_SECRET_ACCESS_KEY="<...>" \
  AWS_REGION="ap-south-1" \
  AWS_BUCKET_NAME="f1-strategy-models" \
  ALLOWED_ORIGINS="https://<real Vercel URL>.vercel.app" \
  ENVIRONMENT="production"
```

`ALLOWED_ORIGINS` is set here directly, **not** as a GitHub Secret — CORS is
a runtime concern of the deployed app, not a CI-time one. `SLACK_WEBHOOK_DEPLOY`
is deliberately **not** set here — `backend/core/config.py` never reads it;
it's a GitHub Actions-only secret for CI deploy notifications (see
CLAUDE.md's GitHub Secrets Checklist).

### Deploy

```bash
fly deploy --app f1-strategy
```

Builds from `infra/docker/Dockerfile.worker` (see `fly.toml`'s `[build]`)
and starts one machine per process group (`web`, `worker`, `beat`) on first
deploy.

### Verify

```bash
curl https://f1-strategy.fly.dev/health
# {"status":"ok","db":"ok","redis":"ok"}

fly logs --app f1-strategy -i <worker machine id>   # look for "celery@... ready."
fly logs --app f1-strategy -i <beat machine id>     # look for the beat scheduler tick
```

Then immediately scale `worker`/`beat` down to the resting state (see
[Race day scaling procedure](#race-day-scaling-procedure-flyio) below) —
the point of confirming they boot correctly right after deploy is to catch
a broken image *before* the next race weekend, not to leave them running.

```bash
make fly-race-down
```

---

## App rollback (Helm — local cluster only)

Fastest rollback path — reverts to a previous Helm release revision, which
points at a previously-built image already in ECR. No rebuild, no CI run.

```bash
# List revision history for the release
helm history f1-strategy-engine -n production

# Roll back to the previous revision
helm rollback f1-strategy-engine -n production

# Or to a specific revision number shown in `helm history`
helm rollback f1-strategy-engine <revision> -n production

# Confirm pods came back healthy on the reverted image
kubectl rollout status deployment/f1-strategy-engine-backend -n production
kubectl rollout status deployment/f1-strategy-engine-worker -n production
```

Release name matches `cd.yml`'s `helm upgrade --install f1-strategy-engine`
(not the shorter `f1-strategy` — use the name the CD pipeline actually
deploys under).

This is an infrastructure/image rollback only — it does not touch the
database or the ML models. If the bad deploy also shipped a migration or a
model regression, combine this with the sections below.

---

## Database rollback (Alembic)

**Use sparingly — prefer a forward-fixing migration over a downgrade.**
`downgrade()` methods exist for every revision but are rarely exercised in
practice, and a downgrade that drops or alters columns can lose data written
under the newer schema in the time between deploy and rollback.

```bash
# Always run from the host venv, never inside Docker (see CLAUDE.md's
# Alembic note — alembic.ini and pyproject.toml aren't in the container).
.venv/Scripts/python.exe -m alembic downgrade -1

# Verify the schema matches what the reverted app code expects
.venv/Scripts/python.exe -m alembic check
```

Before downgrading in production: confirm the specific revision's
`downgrade()` doesn't drop a column that already has post-deploy data you'd
lose (check the migration file in `backend/migrations/versions/` first). If
in doubt, write a new forward migration that reverts the problematic change
instead of downgrading.

---

## Model rollback (S3)

**The 60-second-pickup assumption does not hold — read this before relying
on it during an incident.** `strategy_service.py` and `prediction_worker.py`
each load models into a module-level, per-process cache
(`_model_cache`) on first use and never invalidate it, and the on-disk cache
(`_download_from_s3`'s "unless already cached locally" check) means even a
fresh S3 object is never re-fetched by a running process. This matches
CLAUDE.md's ML Model Registry note: *"restart the worker to pick up a newly
promoted model version."* A rollback that only touches S3 will not take
effect until you restart the processes that serve predictions.

1. **Find the previous version.** Every training run (`train_models.py` /
   `retrain_incremental.py`) uploads under a timestamped version tag as well
   as promoting to `production`:

   ```bash
   aws s3 ls s3://f1-strategy-models/
   #  PRE 20260720-020000/
   #  PRE 20260727-020000/
   #  PRE production/
   ```

   Cross-reference the desired timestamp against the GitHub Release created
   by a promoted `train-models.yml` run, or the `retrain_summary.json`
   artifact attached to that workflow run, to confirm its holdout MAE before
   rolling back to it.

2. **Copy both the model file and its metrics.json back to `production`** —
   the metrics.json matters as much as the model: the next training run's
   promotion guard (`download_metrics(..., "production", ...)`) reads it to
   decide whether a *future* candidate is actually an improvement. Rolling
   back the model without its metrics.json leaves the next run comparing
   against the wrong baseline.

   ```bash
   PREV_TAG=20260720-020000
   FILENAME=tire_deg_soft.pkl   # repeat per affected model file

   aws s3 cp "s3://f1-strategy-models/${PREV_TAG}/${FILENAME}" \
             "s3://f1-strategy-models/production/${FILENAME}"
   aws s3 cp "s3://f1-strategy-models/${PREV_TAG}/${FILENAME}.metrics.json" \
             "s3://f1-strategy-models/production/${FILENAME}.metrics.json"
   ```

3. **Restart the processes that actually serve predictions** — this is the
   step that makes the rollback take effect:

   ```bash
   # Local Docker Compose
   docker compose restart worker backend

   # Kubernetes (Day 22+)
   kubectl rollout restart deployment/f1-strategy-engine-worker -n production
   kubectl rollout restart deployment/f1-strategy-engine-backend -n production
   ```

---

## Promoting a candidate model to production

When `train-models.yml` uploads a model as `:candidate` only (its holdout MAE
did not improve on the current `production` model), it opens a GitHub issue
and posts to Slack `#deploy-alerts` for review. To manually promote a
candidate after reviewing it:

1. Pull the version tag from the GitHub issue body or the workflow run's
   `retrain_summary.json` artifact.
2. Copy the candidate's model **and its metrics.json** to `production`,
   exactly as in [Model rollback](#model-rollback-s3) step 2, using the
   candidate's version tag instead of a previous one.
3. Restart the worker/backend processes (step 3 above) so the promotion
   actually takes effect.
4. Close the review issue, noting the reason you chose to promote despite
   the automated guard's recommendation (e.g. the holdout comparison basis
   was `cv_only` because the holdout season had no data for that compound —
   see `promotion_basis` in the metrics.json).

---

## Race day scaling procedure (Fly.io)

`worker`/`beat` rest at 0 machines between races (see `fly.toml`'s module
comment) — this is the cost-saving side of the Day 40 hybrid deployment
decision, not an incident response. `web` never needs scaling for this;
it's always-on and everything it serves alone (dashboard, driver stats,
historical races, pit-window/undercut/overview predictions — these run
inline in `web` on a cache miss, not via Celery, see the Day 40
worker-dependency audit) keeps working the whole time regardless.

1. **30+ minutes before a session:** scale `worker`/`beat` up.

   ```bash
   make fly-race-up
   # = ./infra/fly/scale-race-weekend.sh up
   # = fly scale count worker=1 beat=1 --app f1-strategy --yes
   ```

2. **Confirm both came up healthy:**

   ```bash
   fly status --app f1-strategy
   fly logs --app f1-strategy -i <worker machine id>   # "celery@... ready."
   ```

3. **Warm the cache** before the session starts, so the first wave of
   viewers doesn't all hit a cold `/strategy/overview` computation
   simultaneously (see CLAUDE.md's "Redis cache hit rate under burst
   ramp-up" note):

   ```bash
   python backend/scripts/warm_strategy_cache.py --session-id <race session UUID>
   ```

4. **Watch Grafana** (Celery queue depth, cache hit rate, DB pool usage)
   during the session. A single `--pool=solo` worker machine is the only
   consumer for `telemetry_queue`/`prediction_queue`/`alert_queue` — if
   queue depth climbs faster than it drains, scale worker count up further
   (Fly bills per-second, so this is cheap for the duration of a session):

   ```bash
   fly scale count worker=<N> --app f1-strategy --yes
   ```

   Only ever run **one** `beat` machine — Fly has no declarative
   single-instance guarantee for a process group (confirmed Day 40
   research), so this is operator discipline, not something the platform
   enforces. Never scale `beat` above 1.

5. **Scale back down** after the session ends:

   ```bash
   make fly-race-down
   # = fly scale count worker=0 beat=0 --app f1-strategy --yes
   ```

If you forget step 5, the cost impact is small (Fly's per-second billing
means idle-but-running `worker`/`beat` cost roughly $9.24/month combined at
the sizing in `fly.toml` — see CLAUDE.md's Day 40 cost research) but not
zero, and `beat` left running outside a race weekend just polls Ergast to
no effect (see CLAUDE.md's Auto Race Detection section) rather than causing
harm.

---

## Secret rotation procedure

All secrets are GitHub Secrets (CI/CD) or environment variables sourced from
`core/config.py` (runtime) — never hardcoded (see CLAUDE.md's Secrets rule).
Rotate on suspicion of exposure, on a routine schedule, or when an
individual with access leaves the project.

1. **Generate the new value** at the source:
   - `SECRET_KEY`: a fresh 64-character random string
     (`python -c "import secrets; print(secrets.token_hex(32))"`).
   - `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`: create a new IAM access
     key in the AWS Console for the `f1-strategy-s3-access` policy's user,
     confirm it works, then deactivate (don't yet delete) the old key.
   - `SUPABASE_DATABASE_URL` / `UPSTASH_REDIS_URL` passwords: rotate at the
     Supabase/Upstash dashboard, which issues a new connection string. Note
     these are distinct from `.env`'s own `DATABASE_URL`/`REDIS_URL`, which
     point at docker-compose's local Postgres/Redis for local dev and are
     never used for a Kubernetes Secret (see `infra/k8s/create-secrets.sh`'s
     header comment, corrected Day 24 after this exact confusion broke a
     local K8s deploy).
   - `SLACK_WEBHOOK_DEPLOY`: regenerate the incoming webhook URL in the Slack
     app configuration for the F1 Strategy Engine workspace.

2. **Update GitHub Secrets** (repo Settings → Secrets and variables →
   Actions) with the new value. Do this for every secret listed in
   `cd.yml`'s and `train-models.yml`'s header comments.

3. **Update the production secret — Fly.io:**

   ```bash
   fly secrets set --app f1-strategy <NAME>="<new value>"
   ```

   `fly secrets set` restarts every machine across all three process groups
   (`web`/`worker`/`beat`) automatically to pick up the new value — no
   separate roll step needed, unlike the Kubernetes path below.

   **Local Kubernetes Secret (local dev cluster only, not production)** —
   never commit the plaintext value to any YAML file. Sealed Secrets is
   still deferred (see CLAUDE.md's Deferred Wiring); the interim mechanism,
   confirmed working Day 24, is `infra/k8s/create-secrets.sh`, which reads
   `.env` (including `SUPABASE_DATABASE_URL`/`UPSTASH_REDIS_URL` — update
   `.env` with the new value first) and recreates the Secret:

   ```bash
   ./infra/k8s/create-secrets.sh local
   ```

4. **Roll the local Kubernetes deployment** so running pods pick up the new
   value (secrets mounted as env vars are not hot-reloaded; skip this step
   for Fly.io — step 3 already restarted it):

   ```bash
   kubectl rollout restart deployment/f1-strategy-engine-backend -n local
   kubectl rollout restart deployment/f1-strategy-engine-worker -n local
   ```

5. **Verify** the app is healthy on the new secret (`GET /health`, a real
   login round-trip for `SECRET_KEY`, a real S3 call for AWS keys) before
   proceeding to step 6.

6. **Revoke the old value** only after step 5 confirms the new one works —
   deactivate/delete the old IAM key, invalidate the old webhook, etc. Doing
   this before verification risks a self-inflicted outage if the new value
   was copied incorrectly.
