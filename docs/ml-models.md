# ML Models

All 7 models are trained by `backend/scripts/train_models.py` (full historical
retrain, `make train`) or `backend/scripts/retrain_incremental.py` (weekly
incremental retrain, see below), evaluated against a fixed 2025 holdout
season, and versioned in S3 (`f1-strategy-models` bucket) under a
timestamped tag plus the `:production` tag currently serving live traffic.
`backend/services/ml/` holds each model's feature engineering, training, and
inference code; `backend/services/ml/explainability.py` provides SHAP-based
interpretability for the two tree-based model families.

**On metrics in this document:** the numbers below are from the most recent
full retrain that was promoted to production (2026-09-11), which fixed a
fuel-correction bug and a feature leak in the tyre models, relabelled the pit
predictor, and retrained the safety car model on the right data (details in
each section). Every production model's live numbers are also in its S3
sidecar, `s3://f1-strategy-models/production/<model>.pkl.metrics.json`, which
is the source of truth if the two ever disagree.

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

**What it predicts:** `lap_time_delta` — a lap's fuel-corrected time relative
to that driver's own session median lap time, as a function of tyre age and
race context. Removing the fuel effect from the target matters: a car gets
lighter and faster as it burns fuel, which otherwise hides tyre wear. Used both directly and via
`predict_life_remaining_batch` (simulates each lap forward up to 40 laps to
estimate laps remaining before predicted degradation crosses a 1.5s
threshold — this is what feeds `pit_predictor`'s
`predicted_life_remaining` feature).

**Input features** (`tire_deg_model.FEATURE_COLUMNS`, exactly 6):
```
lap_number, compound_encoded, tyre_age_laps, fuel_load_penalty,
circuit_id_encoded, driver_id_encoded
```
`fuel_load_penalty` is the seconds of lap time attributable to the fuel still
on board at that lap. One function, `fuel_load_penalty_seconds()`, defines it,
and training and every inference call site use that same function. It replaced
an earlier `fuel_adjusted_time` feature that had two problems: its fuel
correction had the wrong sign (it subtracted fuel already burned instead of
fuel still carried), and it was computed from the lap time itself, so it
leaked the target into the inputs and looked far better in training than it
was at inference.

`driver_id_encoded`/`circuit_id_encoded` are the exact category codes each
model was trained with. Each model's metrics sidecar stores its own
driver→code and circuit→code maps, and inference reads them from there. A
driver or circuit the model has never seen falls back to a stable hash.

`track_temp`/`air_temp` are computed and imputed (`_impute_weather`) but are
**not** in this list — they were tried and regressed holdout MAE 30-40%
across all compounds, so the promotion guard correctly refused to ship that
version. The weather-imputation code stays wired (in case a future
feature-engineering pass does better with it), it's just excluded from the
feature matrix actually used for inference. The models also have no
wet/dry track input, so INTERMEDIATE and WET degradation is modelled the same
on a dry track as on a wet one.

**Training data:** Race sessions, seasons 2018-2024 for training, held out
against season 2025. Historical ingestion put ~139,764 lap records across
the 2018-2024 range and 26,689 laps for the full 2025 holdout season (24/24
rounds) in Postgres — `train_models.py` (`make train`) queries these
directly. The separate S3-cached corpus used by the *weekly incremental*
retrain (`retrain_incremental.py`) is a point-in-time export of the same
2018-2025 range: 163,623 lap rows / 8,271 stint rows
(`s3://f1-strategy-models/training-data/base/`).

The 2026-09-11 retrain used 119,984 training laps and 23,043 holdout laps
after filtering to valid laps.

**Performance metrics (holdout MAE, seconds — lower is better; 2026-09-11 retrain):**

| Compound | Holdout MAE |
|---|---|
| SOFT | 0.6356 |
| MEDIUM | 0.5609 |
| HARD | 0.5849 |
| INTERMEDIATE | 1.7695 |
| WET | 3.9233 (cross-validation only, see below) |

MEDIUM and HARD are numerically worse than the previous models (0.4972 and
0.5168). That is expected: the old numbers were flattered by the leaked
feature described above, so the two sets aren't comparable. The new models
describe tyre wear the right way round: the training target now gets slower
as tyres age on SOFT, MEDIUM and HARD, where the old fuel correction made it
appear to get faster.

WET (and sometimes INTERMEDIATE) can have zero laps in a dry holdout season.
When that happens the model is judged on its cross-validation MAE instead
(`promotion_basis` in its `metrics.json` says which). WET is still noisy: the
whole 2018-2025 corpus has only about 320 valid WET laps.

**Known issue:** `tire_deg_hard.pkl` over-predicts degradation on a brand-new
HARD tyre's first lap (`tyre_age_laps=1`), most likely because out-laps are
rare in the training data. The current pit predictor is no longer fooled by
it, but the tyre model itself still needs better first-lap coverage.

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
`shap.TreeExplainer` on the raw `XGBRegressor`, giving each feature's
contribution to one prediction. The pit-window recommendation combines these
with the pit predictor's own SHAP values into the plain-English explanation
shown in the app.

---

## 6. Pit predictor (`pit_predictor.pkl`)

**Model type and library:** `LGBMClassifier` (LightGBM), binary classifier
with `scale_pos_weight` to counter class imbalance (a driver pits ~1-3 times
across a 50-70 lap race) — `services/ml/pit_predictor.py`.

**What it predicts:** `pit_within_k_laps` — probability the driver pits
within the next 3 laps (`PIT_LABEL_HORIZON_LAPS = 3`, counting the pit lap
itself). An earlier version predicted `did_pit_this_lap` (pits on *this*
lap), which in practice only lit up once the driver was already in the pit
lane, so it gave no warning. The 3-lap label gives genuine advance warning:
on real 2026 races the probability now climbs over the laps before a stop and
drops right after it.

`ALERT_THRESHOLD = 0.65` is the cutoff at which a driver counts as "about to
pit". The Monte Carlo simulator uses it to decide when each rival pits, and
the strategy service uses it to project competitors' pit laps.

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

**Performance metrics:** holdout MAE = 0.3250 (2026-09-11 retrain). The
previous model's 0.0328 is not comparable: with a same-lap label almost every
lap is a "no", so a model that nearly always predicts "no" scores a tiny MAE.
The 3-lap label has three times as many positives, which is a harder and more
useful target. The cross-validated AUC for the current model is in its
sidecar (`cv_auc`).

**Threshold calibration, honestly stated:** a sweep of `ALERT_THRESHOLD` from
0.45 to 0.70 against 89 real 2026 stints found a trade-off, not a best value.
Lower thresholds catch more stops but predict them earlier; higher ones do the
reverse. The bias also depends on the compound: HARD and SOFT stints often
never cross the threshold, while MEDIUM crosses it several laps early. 0.65
was kept.

**How to retrain:** same as above — `make train` trains all 7 models in one
run, `pit_predictor` last (it depends on the other two families' fitted
outputs).

**How to evaluate:** no standalone command — `evaluate_holdout` in
`pit_predictor.py` runs inline during `train_all()`, computing MAE between
predicted pit probability and the actual `pit_within_k_laps` label on
the 2025 holdout set; `cv_auc` is the 5-fold `GroupKFold` (grouped by
`session_id`, so no session leaks across folds) cross-validated AUC
computed during training itself.

**SHAP interpretability:** `pit_predictor` is a raw `LGBMClassifier` (no
preprocessing pipeline to unwrap), so `TreeExplainer` runs directly on it.
The two inputs the model is designed to lean on are `predicted_life_remaining`
("tyres are nearly done") and `safety_car_probability` ("a safety car would
make pitting cheap").

**Known issue:** when projecting *competitors'* pit laps (the strategy
overview), three of the eight inputs are currently fixed values rather than
real per-driver ones: both gaps are set to 120 s and `safety_car_probability`
to 0. Measured against real stops, those projected pit laps are off by 5.8
laps on average. The per-driver pit prediction made on every completed lap
uses real values and is not affected.

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
split as the tire degradation models, but on **all** laps, not just
`is_valid` ones. A lap run behind a safety car has an unusual lap time, which
FastF1 marks invalid, so filtering to valid laps removed almost every safety
car event from the data: 3,832 safety-car laps before the filter, 0 after. An
earlier version made exactly that mistake and learned a rate of zero
everywhere. The model now uses the same unfiltered laps as the pit predictor
(286 safety car starts in training, 33 in holdout). Fit via `build_lap_flags`
+ `train_safety_car_model` in `train_all()`.

**Performance metrics:** holdout MAE = 0.00365 (2026-09-11 retrain), the MAE
between predicted P(SC in the next lap) and whether one actually started.
24 circuits have enough laps for their own rate; the rest use a global
default of about 0.0019 per lap.

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
compares it against whatever is currently at the `production/` tag.
`serialize_evaluate_and_upload()` in `train_models.py` is the shared
promote-or-don't logic both training entrypoints call. A new model is copied
to `production/` when any of these is true:

- there is no production model yet;
- its holdout MAE is lower than production's;
- **the two models aren't comparable**, in which case the new one is
  promoted regardless of MAE. That covers a different feature count, different
  feature names, a changed `TRAINING_SCHEMA_VERSION` (bumped when the target or
  a feature is redefined), or a production `.pkl` that can't be loaded.

The comparability check exists because MAE alone can pick the wrong model. A
production model built on an older feature set can crash current inference
code even if its MAE looks better. A model whose MAE was flattered by a data
bug will also beat the honest fix. Both have happened in this project.

Two things worth knowing about this mechanism:
- **The metrics file matters as much as the model file.** The next run's
  promotion decision reads `production/{filename}.metrics.json` to know
  what `holdout_mae` it needs to beat — copying a model without its
  metrics leaves the next comparison referencing the wrong baseline (see
  `docs/runbook.md`'s Model rollback section, which makes the same point
  for manual rollbacks).
- **A promoted model does not take effect until the serving processes
  restart.** `strategy_service.py` and `prediction_worker.py` download each
  model fresh from S3 the first time a process needs it, then keep it in
  memory for the life of that process. `docker compose restart worker
  backend` (or a Kubernetes rollout restart) is required to pick up a new
  `production` model.

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

**Known issue:** from GitHub's hosted runners, FastF1 currently returns empty
data for almost every 2026 round (the same code and version work from a home
connection), so the weekly run currently adds no 2026 data and trains on the
base corpus only, while still reporting success.
