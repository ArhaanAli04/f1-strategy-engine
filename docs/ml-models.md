# ML Models

All 7 models are trained by `backend/scripts/train_models.py` (full historical
retrain, `make train`) or `backend/scripts/retrain_incremental.py` (weekly
incremental retrain, see below), evaluated against a fixed 2025 holdout
season, and versioned in S3 (`f1-strategy-models` bucket) under a
timestamped tag plus the `:production` tag currently serving live traffic.
`backend/services/ml/` holds each model's feature engineering, training, and
inference code; `backend/services/ml/explainability.py` provides SHAP-based
interpretability for the two tree-based model families.

**On metrics in this document:** every number below is either sourced
directly from this repo's code/docs or from a named GitHub Release the
maintainer supplied. Where a model's current holdout metric isn't tracked
anywhere in the repo, this document says so explicitly and points at the S3
`metrics.json` for that model/tag instead of guessing.

**Training data scope, honestly stated:** the bulk historical ingestion
target (`make ingest-season`) only ever ingested **Race (R) sessions** —
practice and qualifying were not bulk-ingested for the 2018-2024 historical
range. All 7 models are therefore trained and evaluated on race-session lap
data only.

---

## 1-5. Tire degradation models (`tire_deg_{soft,medium,hard,inter,wet}.pkl`)

**Model type and library:** `XGBRegressor` (XGBoost), wrapped in a
`Pipeline(StandardScaler → XGBRegressor)`. One independently trained model
per tyre compound — `services/ml/tire_deg_model.py`.

**What it predicts:** `lap_time_delta` — a lap's time relative to that
driver's own session median lap time, as a function of tyre age and race
context. Used both directly and via
`predict_life_remaining_batch` (simulates each lap forward up to 40 laps to
estimate laps remaining before predicted degradation crosses a 1.5s
threshold — this is what feeds `pit_predictor`'s
`predicted_life_remaining` feature).

**Input features** (`tire_deg_model.FEATURE_COLUMNS`, exactly 6):
```
lap_number, compound_encoded, tyre_age_laps, fuel_adjusted_time,
circuit_id_encoded, driver_id_encoded
```
`track_temp`/`air_temp` are computed and imputed (`_impute_weather`) but are
**not** in this list — they were tried on 2026-07-16 and regressed holdout
MAE 30-40% across all compounds, so the promotion guard correctly refused
to ship that version. The weather-imputation code stays wired (in case a
future feature-engineering pass does better with it), it's just excluded
from the feature matrix actually used for inference.

**Training data:** Race sessions, seasons 2018-2024 for training, held out
against season 2025. Historical ingestion put ~139,764 lap records across
the 2018-2024 range and 26,689 laps for the full 2025 holdout season (24/24
rounds) in Postgres — `train_models.py` (`make train`) queries these
directly. The separate S3-cached corpus used by the *weekly incremental*
retrain (`retrain_incremental.py`) is a point-in-time export of the same
2018-2025 range: 163,623 lap rows / 8,271 stint rows
(`s3://f1-strategy-models/training-data/base/`).

**Performance metrics (holdout MAE, seconds — lower is better):**

| Compound | Holdout MAE | Source |
|---|---|---|
| MEDIUM | 0.5018 | GitHub Release `models-20260803-083604` |
| HARD | 0.5168 | GitHub Release `models-20260803-083604` |
| INTERMEDIATE | 3.7786 | GitHub Release `models-20260803-083604` |
| SOFT | not tracked in this doc | see `s3://f1-strategy-models/production/tire_deg_soft.pkl.metrics.json` |
| WET | not tracked in this doc | see `s3://f1-strategy-models/production/tire_deg_wet.pkl.metrics.json` |

For context: a 2026-07-11 training run (documented in `CLAUDE.md`'s Data
Quality Notes, predating the release above) recorded SOFT/MEDIUM/HARD
holdout MAE of 0.644/0.504/0.521 as the pre-weather-feature baseline. That
run is now superseded by the release-tagged numbers above for
MEDIUM/HARD — it's included here only as historical context, not as a
current SOFT figure.

INTERMEDIATE and WET compounds can have zero holdout-season laps in a dry
2025 (see `promotion_basis` in each model's `metrics.json` — falls back to
`cv_mae` instead of a true holdout score when this happens, which is why
INTER's 3.7786 is notably higher than MEDIUM/HARD: less data, and a
compound whose degradation behavior is intrinsically noisier).

**How to retrain:**
```bash
make train
# = python backend/scripts/train_models.py
# Needs: DATABASE_URL pointing at populated Postgres (2018-2025 laps),
# AWS credentials in .env for the S3 upload.
```

**How to evaluate:** there is no separate evaluate-only command — holdout
MAE is computed automatically as part of training (`evaluate_holdout` in
`tire_deg_model.py`, called inline by `train_all()`), logged as
`tire_deg_{compound}: holdout_mae=... promoted=...`, and written into each
model's `.metrics.json` alongside it in S3.

**SHAP interpretability:** `explainability.explain_prediction()` unwraps
the fitted `Pipeline`, applies `StandardScaler` manually, and runs
`shap.TreeExplainer` on the raw `XGBRegressor`. In practice, `tyre_age_laps`
and `fuel_adjusted_time` dominate contributions for most laps (degradation
and fuel burn are the two largest real effects on pace); `circuit_id_encoded`
contributes more on circuits with unusual tyre wear characteristics (e.g.
high-degradation street circuits).

---

## 6. Pit predictor (`pit_predictor.pkl`)

**Model type and library:** `LGBMClassifier` (LightGBM), binary classifier
with `scale_pos_weight` to counter class imbalance (a driver pits ~1-3 times
across a 50-70 lap race) — `services/ml/pit_predictor.py`.

**What it predicts:** `did_pit_this_lap` — probability a driver pits on the
current lap. `ALERT_THRESHOLD = 0.65` is the cutoff used to surface a pit
alert to the frontend.

**Input features** (`pit_predictor.FEATURE_COLUMNS`, exactly 8):
```
current_tyre_age, predicted_life_remaining, gap_to_car_ahead,
gap_to_car_behind, safety_car_probability, laps_to_race_end, position,
fuel_load_est
```
`predicted_life_remaining` and `safety_car_probability` are cross-model
features — computed by `train_models.py`'s orchestrator from the fitted
tire degradation and safety car models respectively (`add_predicted_life_remaining`,
`add_safety_car_probability`), not columns that exist natively in the laps
table. This is why `pit_predictor` trains *after* the other two model
families in `train_all()`.

**Training data:** Race sessions, 2018-2024 training / 2025 holdout — same
split as the tire degradation models, except **`is_valid` laps are not
filtered out** here (unlike tire_deg/safety_car): pit/in/out laps FastF1
marks invalid are exactly this model's positive-class label
(`label_pit_laps`), so excluding them would remove the signal being
predicted.

**Performance metrics:** cv_AUC = 0.992, holdout MAE = 0.0328 (per
`CLAUDE.md`'s ML Model Registry and the corresponding GitHub Release).

**How to retrain:** same as above — `make train` trains all 7 models in one
run, `pit_predictor` last (it depends on the other two families' fitted
outputs).

**How to evaluate:** no standalone command — `evaluate_holdout` in
`pit_predictor.py` runs inline during `train_all()`, computing MAE between
predicted pit probability and the actual `did_pit_this_lap` indicator on
the 2025 holdout set; `cv_auc` is the 5-fold `GroupKFold` (grouped by
`session_id`, so no session leaks across folds) cross-validated AUC
computed during training itself.

**SHAP interpretability:** `pit_predictor` is a raw `LGBMClassifier` (no
preprocessing pipeline to unwrap), so `TreeExplainer` runs directly on it.
`predicted_life_remaining` and `safety_car_probability` are typically the
two largest-magnitude contributors — makes sense, since a driver pitting is
overwhelmingly a function of "tyres are nearly done" or "a safety car just
made pitting free."

---

## 7. Safety car model (`safety_car_model.pkl`)

**Model type and library:** Per-circuit homogeneous Poisson process,
closed-form MLE (`lambda = event_count / lap_exposure`) — plain
NumPy/pandas, no sklearn/XGBoost/LightGBM involved.
`services/ml/safety_car_model.py`.

**What it predicts:** `P(≥1 SC/VSC event in the next N laps)` for
`N ∈ {1, 2, 3, 5, 10}`, via `1 - exp(-lambda * N)`. The base per-circuit
rate is adjusted by fixed multipliers: `LAP1_MULTIPLIER=2.5`,
`WET_MULTIPLIER=3.0`, `STREET_MULTIPLIER=1.8` (for a hardcoded set of 5
street circuits — Monaco, Baku, Marina Bay, Jeddah, Las Vegas).

**Input features:** not a feature-vector model in the ML sense — it's fit
per circuit from three inputs derived from FastF1's `TrackStatus` codes:
`circuit_name`, `lap_number` (for the lap-1 multiplier), and `wet_track`
(compound-derived, for the wet multiplier). Circuits with fewer than 200
dry, non-lap-1 laps in the training window (`MIN_LAPS_FOR_CIRCUIT_ESTIMATE`)
fall back to a global `default_rate` rather than an unstable per-circuit
estimate.

**Training data:** Race sessions, 2018-2024 training / 2025 holdout, same
split as the tire degradation models (`is_valid` laps only). Fit via
`build_lap_flags` + `train_safety_car_model` in `train_all()`.

**Performance metrics:** not tracked in this doc — see
`s3://f1-strategy-models/production/safety_car_model.pkl.metrics.json` for
the current production `holdout_mae` (MAE between predicted P(SC in next
lap) and the actual onset indicator).

**How to retrain:** same `make train` run as the others — safety car model
trains second, after tire degradation, before pit predictor (its output
feeds `pit_predictor`'s `safety_car_probability` feature).

**How to evaluate:** no standalone command — `evaluate_holdout` in
`safety_car_model.py` runs inline during `train_all()`.

**SHAP interpretability:** **not applicable.** This isn't a tree model —
`explainability.py` only supports `tire_deg_model` (XGBoost) and
`pit_predictor` (LightGBM). The Poisson model's "interpretability" is
inherent to its structure instead: `SafetyCarModel.circuit_rates` is a
plain `dict[str, float]` of per-circuit base rates, directly inspectable,
and the lap-1/wet/street multipliers are fixed constants rather than
learned weights — there's nothing SHAP would add here.

---

## Weekly automated retraining pipeline

`.github/workflows/train-models.yml` ("Train Models") runs
`backend/scripts/retrain_incremental.py` every **Monday 02:00 UTC**
(`cron: "0 2 * * 1"`), plus on-demand via `workflow_dispatch`. The runner
(`ubuntu-latest`) has no local Postgres and no persistent FastF1 cache, so
`retrain_incremental.py` is built specifically to need neither — see
"Incremental retraining approach" below.

Each of the 7 models is promoted or held back **independently** (matching
`train_models.py`'s per-model promotion guard) — a single Monday run can
promote some models to `production` and leave others as `:candidate`
awaiting human review. The workflow branches on `retrain_summary.json`
(one entry per model: `holdout_mae`, `previous_production_holdout_mae`,
`promoted`) to decide what happens next:

- **Any model promoted** → a GitHub Release is created (tag
  `models-YYYYMMDD-HHMMSS`) listing what was promoted, and `#deploy-alerts`
  gets a Slack notification.
- **Any model held back as a candidate** → a GitHub issue is opened for
  human review, plus a separate Slack warning. See
  `docs/runbook.md`'s "Promoting a candidate model to production" for the
  manual review/promote procedure.

Both Slack notifications are best-effort (`curl ... || echo "::warning..."`)
— a Slack delivery failure never fails the job, since the actual
training/promotion work has already succeeded by that point.

## S3 `:production` tag promotion

Every training run — full (`train_models.py`) or incremental
(`retrain_incremental.py`) — uploads each model under a timestamped version
tag (`YYYYMMDD-HHMMSS/{filename}` plus `{filename}.metrics.json`) **and**
compares its `holdout_mae` against whatever is currently at the
`production/` tag. If the new run's holdout MAE is lower (or there is no
existing production model yet), it's copied to `production/` too —
`serialize_evaluate_and_upload()` in `train_models.py` is the shared
promote-or-don't logic both training entrypoints call.

Two things worth knowing about this mechanism:
- **The metrics file matters as much as the model file.** The next run's
  promotion decision reads `production/{filename}.metrics.json` to know
  what `holdout_mae` it needs to beat — copying a model without its
  metrics leaves the next comparison referencing the wrong baseline (see
  `docs/runbook.md`'s Model rollback section, which makes the same point
  for manual rollbacks).
- **A promoted model does not take effect until the serving processes
  restart.** `strategy_service.py` and `prediction_worker.py` cache models
  in a module-level, per-process dict on first use and never invalidate
  it — `docker compose restart worker backend` (or a Kubernetes rollout
  restart) is required to actually pick up a new `production` model.

## Incremental retraining approach

`retrain_incremental.py` exists because the weekly CI runner has neither a
persistent Postgres connection nor a warm FastF1 cache — re-deriving the
full 2018-2025 corpus from FastF1 every Monday would be slow and wasteful.
Instead it combines two sources:

1. **A static base corpus, exported once.** `export_training_data.py` was
   run once, manually, against a real Postgres (not on CI) and uploaded to
   `s3://f1-strategy-models/training-data/base/{laps,stints}.parquet` —
   163,623 lap rows / 8,271 stint rows, 2018-2025. This step only needs
   re-running if new historical seasons are added to the corpus; it is
   **not** part of the weekly cron.
2. **The current season's completed rounds, fetched live from FastF1.**
   Every Monday run re-fetches all of `CURRENT_SEASON`'s (2026) completed
   rounds directly from FastF1 — no Postgres involved, since only the
   *write* side of historical ingestion (`ingest_historical.py`) ever
   needed a database; the FastF1 *fetch* itself doesn't. A season has at
   most 24 rounds against the base corpus's ~168 (7 seasons × 24), so this
   stays cheap (~5-10 minutes) even late in the season, versus a full
   historical rebuild's ~30-60 minutes.

Training then proceeds exactly like `train_models.py`, reusing its
`encode_categoricals`/`split_train_holdout`/`serialize_evaluate_and_upload`
helpers rather than duplicating them — the only difference is the training
set is `{2018..2024} ∪ {2026's completed rounds}`, still evaluated against
the same fixed 2025 holdout season so MAE stays comparable run over run.
