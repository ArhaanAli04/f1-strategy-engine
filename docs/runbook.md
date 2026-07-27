# Operational Runbook

Procedures for rolling back a bad deploy, migration, or model; scaling for
race day; and rotating secrets. Written Day 21, ahead of the Day 22
Kubernetes deployment — the Helm/kubectl commands below assume the cluster
and Helm release described in `infra/k8s/` exist (Day 22+), and use
`f1-strategy-backend`/`f1-strategy-worker` as placeholder Deployment names —
confirm/update these against the actual Helm chart's generated resource
names once it's written. Everything else (model rollback, secret rotation)
applies today.

## Table of Contents

- [App rollback (Helm)](#app-rollback-helm)
- [Database rollback (Alembic)](#database-rollback-alembic)
- [Model rollback (S3)](#model-rollback-s3)
- [Promoting a candidate model to production](#promoting-a-candidate-model-to-production)
- [Race day scaling procedure](#race-day-scaling-procedure)
- [Secret rotation procedure](#secret-rotation-procedure)

---

## App rollback (Helm)

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
kubectl rollout status deployment/f1-strategy-backend -n production
kubectl rollout status deployment/f1-strategy-worker -n production
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
   kubectl rollout restart deployment/f1-strategy-worker -n production
   kubectl rollout restart deployment/f1-strategy-backend -n production
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

## Race day scaling procedure

Race day traffic is bursty and predictable (practice/qualifying/race
sessions at known times) — pre-scale ahead of it rather than relying on
reactive autoscaling alone, since the Day 18 500-user load test showed the
default HPA/KEDA response lag isn't fast enough to absorb the step-change at
a session's green light (see CLAUDE.md's Deferred Wiring notes on Celery
worker backlog and DB pool exhaustion).

1. **30+ minutes before a session:** confirm `infra/k8s/race-weekend-cronjob.yaml`
   pre-scaled the backend Deployment to 5 replicas (or do it manually if the
   CronJob isn't deployed yet):

   ```bash
   kubectl scale deployment/f1-strategy-backend -n production --replicas=5
   ```

2. **Confirm the worker pool is scaled for the queue depth you expect.** The
   Day 18 load test found a single `--pool=solo` worker takes 65-88s per
   `run_race_simulation` task — size worker replicas from expected concurrent
   `/strategy/simulate` calls, not from CPU alone:

   ```bash
   kubectl scale deployment/f1-strategy-worker -n production --replicas=8
   ```

3. **Warm the cache** before the session starts, so the first wave of
   viewers doesn't all hit a cold `/strategy/overview` computation
   simultaneously (see CLAUDE.md's "Redis cache hit rate under burst
   ramp-up" note):

   ```bash
   python backend/scripts/warm_strategy_cache.py --session-id <race session UUID>
   ```

4. **Watch Grafana** (Celery queue depth, cache hit rate, DB pool usage)
   during the session. If `prediction_queue` length climbs faster than
   workers are draining it, scale workers further:

   ```bash
   kubectl scale deployment/f1-strategy-worker -n production --replicas=<N>
   ```

5. **Scale back down** after the session ends to avoid idle cost:

   ```bash
   kubectl scale deployment/f1-strategy-backend -n production --replicas=2
   kubectl scale deployment/f1-strategy-worker -n production --replicas=2
   ```

Once `infra/k8s/hpa.yaml` and `infra/k8s/worker-scaledobject.yaml` are
applied (Day 22), steps 4-5 become largely automatic — this manual procedure
is the fallback for whenever autoscaling isn't fast enough for a session's
opening minutes, or before KEDA is installed.

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
   - `DATABASE_URL` / `REDIS_URL` passwords: rotate at the Supabase/Upstash
     dashboard, which issues a new connection string.
   - `SLACK_WEBHOOK_DEPLOY`: regenerate the incoming webhook URL in the Slack
     app configuration for the F1 Strategy Engine workspace.

2. **Update GitHub Secrets** (repo Settings → Secrets and variables →
   Actions) with the new value. Do this for every secret listed in
   `cd.yml`'s and `train-models.yml`'s header comments.

3. **Update the production environment** (Kubernetes Sealed Secrets, once
   Day 22+ lands) — never commit the plaintext value to any YAML file:

   ```bash
   kubectl create secret generic f1-strategy-secrets -n production \
     --from-literal=SECRET_KEY="$NEW_SECRET_KEY" \
     --dry-run=client -o yaml | kubeseal -o yaml > infra/k8s/sealed-secrets/f1-strategy-secrets.yaml
   kubectl apply -f infra/k8s/sealed-secrets/f1-strategy-secrets.yaml
   ```

4. **Roll the deployment** so running pods pick up the new value (secrets
   mounted as env vars are not hot-reloaded):

   ```bash
   kubectl rollout restart deployment/f1-strategy-backend -n production
   kubectl rollout restart deployment/f1-strategy-worker -n production
   ```

5. **Verify** the app is healthy on the new secret (`GET /health`, a real
   login round-trip for `SECRET_KEY`, a real S3 call for AWS keys) before
   proceeding to step 6.

6. **Revoke the old value** only after step 5 confirms the new one works —
   deactivate/delete the old IAM key, invalidate the old webhook, etc. Doing
   this before verification risks a self-inflicted outage if the new value
   was copied incorrectly.
