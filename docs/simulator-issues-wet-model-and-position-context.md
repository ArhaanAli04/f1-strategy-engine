# Strategy Simulator — two open issues (WET model crash + nonsensical output)

> **Status:** investigated 2026-08-29, **not fixed**. Deal with in a future day.
> Surfaced while testing the new `GET /strategy/last-ingested-session` picker (Day 43 follow-up)
> — the Simulator now auto-selects a session in non-live mode, which made these easy to hit.
>
> **Neither issue is caused by the 2026-08-29 work** (dot-animation render-behind buffer,
> `avg_deg_per_lap` backfill, `/strategy/last-ingested-session` endpoint). None of that touches
> `race_simulator.py`, the tyre models, S3, or training. Both are pre-existing latent problems.

---

## TL;DR

| | Issue | Nature | Recommended path |
|---|---|---|---|
| **A** | Simulation task crashes whenever a WET compound is involved (`ValueError: X has 6 features, but StandardScaler is expecting 8`) | Stale/incompatible production model artifact (`tire_deg_wet.pkl` is 8-feature, everything else is 6-feature) + no defensive guard in the sim | **2b + 2a**: alias `WET → tire_deg_inter.pkl` at model load + add a feature-count guard in `race_simulator._tire_deg_predictions`. Deferred entry for a real 6-feature WET retrain + a promotion-guard schema check. |
| **B1** | "+16 positions" / "No drivers within pit stop window" for a leader with a rival 10 s behind | Garbage position/cumulative-time inputs for the auto-selected session (Zandvoort R12) — `lap_data.position` all NULL + unevenly-missing laps (Deferred Wiring item A) | Fix item A (NULL-lap cumulative-sum, 4 call sites). Quick mitigation: add `status = 'completed'` to `/strategy/last-ingested-session` so it doesn't land on Zandvoort R12. |
| **B2** | Pitting to INTERMEDIATE on a **dry** track shows a *time gain* instead of a catastrophic loss | Genuine model limitation — the tyre models have **no** track-condition / weather feature, so INTER/WET degradation is modelled identically wet or dry | New Deferred Wiring entry (wording below). Real fix needs a track-condition feature + retrain, or a heuristic mismatch penalty, or a UI guard. |

---

# PART A — WET tyre model schema mismatch (crashes the whole simulation)

## A.1 The crash

Reproduced 2026-08-29 in the web app: driver **NOR**, lap 68, tyre age 20, compound **HARD**, added a
pit stop at **lap 69 → WET**. UI showed "Simulation failed".

Worker log (`docker compose logs worker`), failed twice (Celery retry):

```
Task run_race_simulation[...] raised unexpected:
ValueError('X has 6 features, but StandardScaler is expecting 8 features as input.')

  prediction_worker.py:1007  run_race_simulation  ->  asyncio.run(_run_simulation(payload))
  prediction_worker.py:955   _run_simulation      ->  race_simulator.simulate_race(...)
  race_simulator.py:419      simulate_race        ->  _tire_deg_predictions(...)
  race_simulator.py:252      _tire_deg_predictions->  pipeline.predict(features)   # model="tire_deg"
  sklearn/preprocessing/_data.py:1111  StandardScaler.transform  ->  ValueError
```

## A.2 Root cause — one of the five tyre models is the wrong shape

All 5 production `tire_deg_*.pkl` models loaded from `s3://<bucket>/production/` and their
`StandardScaler.n_features_in_` inspected:

| model | `n_features_in_` | S3 `.pkl` LastModified | file size |
|---|---|---|---|
| `tire_deg_soft.pkl` | **6** | — | — |
| `tire_deg_medium.pkl` | **6** | — | — |
| `tire_deg_hard.pkl` | **6** | **2026-08-03** | ~1.23 MB |
| `tire_deg_inter.pkl` | **6** | **2026-08-03** | ~1.25 MB |
| **`tire_deg_wet.pkl`** | **8** | **2026-07-10 22:21:10 UTC** | 0.74 MB |

Every inference path builds a **6-column** feature vector
(`tire_deg_model.FEATURE_COLUMNS`):

```
[lap_number, compound_encoded, tyre_age_laps, fuel_adjusted_time, circuit_id_encoded, driver_id_encoded]
```

`tire_deg_wet.pkl` still carries the **8-column** schema from the abandoned weather experiment
(6 columns above + `track_temp` + `air_temp`). When a simulation involves the WET compound, the
6-column vector is fed to `tire_deg_wet.pkl` → its inner `StandardScaler` (fit on 8) rejects it →
`ValueError` → whole `run_race_simulation` task dies.

### Metrics sidecars (`s3://<bucket>/production/<file>.pkl.metrics.json`)

| model | `n_samples` | `holdout_mae` | `cv_mae` | `promotion_basis` | metrics LastModified |
|---|---|---|---|---|---|
| `tire_deg_hard.pkl` | 46,963 | 0.5168 | 0.7014 | holdout | 2026-08-03 08:35:35 |
| `tire_deg_inter.pkl` | 3,845 | 3.7786 | 3.0886 | holdout | 2026-08-03 08:35:39 |
| **`tire_deg_wet.pkl`** | **319** | **5.7906** | **5.7906** | **cv_only** | **2026-07-10 22:21:10** |

`tire_deg_wet.pkl.metrics.json` LastModified is frozen at 2026-07-10 → `upload_model(..., "production", "tire_deg_wet.pkl")` has **never** run since then. Also note **cv_mae 5.79 s is a near-useless model** on any schema (HARD 0.52, INTER 3.78).

## A.3 Why WET was never retrained (workflow analysis)

**Not "skipped for insufficient data", not "silently missed."**

Base training corpus parquet on S3 (`training-data/base/laps.parquet`, 163,623 rows, seasons
2018-2025) **contains WET laps**:

| compound | rows in parquet | `is_valid=True` | by season |
|---|---|---|---|
| WET | 430 | **319** | 2021 (31), 2022 (320), 2023 (47), 2024 (32), **2025: 0** |
| INTERMEDIATE | ~6,100 | ~3,845 | 2020 (56), 2021 (480), 2022 (1357), 2023 (691), 2024 (2143), 2025 (1391) |

`train_models.py` constants: `TRAIN_SEASON_START=2018`, `TRAIN_SEASON_END=2024`,
`HOLDOUT_SEASON=2025`.

Chain (`retrain_incremental.py` / `train_models.py` tire_deg loop):

1. `c_train` for WET = 319 valid laps → **not empty** → the `if c_train.empty: … continue` skip
   branch is **never hit**. A WET model is trained on every run.
2. `c_holdout` for WET (2025 laps) = **0** → `promotion_basis = "cv_only"`,
   `holdout_mae = result.cv_mae`.
3. Promotion (`serialize_evaluate_and_upload` → `train_models.py` ~L279):
   `should_promote = current_holdout_mae is None or holdout_mae < current_holdout_mae`.
   `download_metrics(..., "production", "tire_deg_wet.pkl")` returns the **July metrics**
   (`holdout_mae` 5.79) → `current_holdout_mae = 5.79` → promotion needs **`new_cv_mae < 5.79`**.
4. The Aug 3 run promoted 6-feature SOFT/MEDIUM/HARD/INTER — each beat its own **6-feature**
   incumbent (apples-to-apples). WET's incumbent is the **8-feature July weather-experiment
   model with `cv_mae` 5.79**. The Aug 3 6-feature WET candidate did **not** get `cv_mae < 5.79`,
   so the guard **kept the incumbent** — the frozen July-10 metrics timestamp confirms it.

### Promotion-guard blind spot

The guard compares **MAE only**. It has no idea the kept incumbent is:
- a **different feature schema** (8 vs 6), and
- **unusable by the current 6-feature inference code**.

It "correctly" kept a model that crashes in production.

> **Caveat:** `gh` CLI is not installed in the dev environment, so the actual GitHub Actions run
> logs could not be read. The "candidate trained, `cv_mae` ≥ 5.79, not promoted" chain is
> **inferred** from the metrics-file timestamps + the promotion logic in `train_models.py`, not
> from a log line. A mid-run crash during WET training is a less-likely alternative, but the
> parquet data conclusively rules out the "insufficient data / deliberate skip" explanation.

## A.4 How the 8-feature `tire_deg_wet.pkl` got there

- ~2026-07-10/11: **weather experiment** — `track_temp`/`air_temp` added to `FEATURE_COLUMNS` → 8
  features. Per CLAUDE.md Data Quality Notes (2026-07-11): regressed holdout MAE 30-40 % for
  SOFT/MEDIUM/HARD → promotion guard **refused** those. But **WET's 8-feature version got
  promoted** on 2026-07-10 (no good incumbent to beat, and weather plausibly *helps* wet-tyre
  prediction, which is exactly the case where track temperature matters).
- 2026-07-16: code reverted — `FEATURE_COLUMNS` back to 6 (`tire_deg_model.py` L35-44).
- 2026-08-03 retrain: 6-feature SOFT/MEDIUM/HARD/INTER promoted (beat their 6-feature
  incumbents). WET's incumbent is now the 8-feature model with `cv_mae` 5.79 → 6-feature WET
  candidate can't beat it → **stale 8-feature model persists** → schema mismatch → crash.

## A.5 Trigger conditions

Any simulation where the **WET** compound appears:
- `current_compound == "WET"`, **or**
- any pit-stop target compound == `"WET"` (`payload["compounds"]` contains `"WET"`).

SOFT / MEDIUM / HARD / **INTERMEDIATE** are all fine (all 6-feature).

The crash kills the **entire Monte Carlo task**, not just the WET stint:
- `race_simulator._tire_deg_predictions` (L225-252) guards `if pipeline is None: continue` but has
  **no `try/except`** around `pipeline.predict(features)` and **no feature-count check**.
- Contrast: the live per-lap path (`prediction_worker._run_inference`) wraps tyre `.predict()` in
  `try/except Exception` and degrades to a null prediction. The simulation path does not.
- `strategy_service.get_undercut_score` / `get_overcut_score` catch `ModelNotLoadedError` only,
  not `ValueError` — a WET-involved undercut call would also 500 (secondary path, not reported).

## A.6 Options

| # | Option | Effort | Produces genuinely usable WET predictions? | Risk to SOFT/MEDIUM/HARD/INTER |
|---|---|---|---|---|
| **1** | **Retrain `tire_deg_wet.pkl` with the 6-feature schema.** Run `train_models.py` against the local corpus, then **force-promote WET only** (delete `production/tire_deg_wet.pkl.metrics.json` first so `current_holdout_mae is None` → auto-promote — otherwise the guard rejects it again for the same `cv_mae ≥ 5.79` reason). | Moderate (~1-2 h incl. a training run + verification). | **Barely.** 319 valid laps / 4 seasons / 7 sessions → a valid, plausibly-shaped model (fresh faster, ages slower) but high-variance, ~5-6 s MAE (HARD 0.52, INTER 3.78 for comparison). Stops the crash; wet sims run but shouldn't be trusted. **Does nothing** for the dry-INTER/dry-WET nonsense (B2 — no track-condition feature either way). | **None** if you upload **only** the WET artifact + its metrics. A full `train_models.py` run also re-promotes the other 4 through their own guards (6-vs-6, low risk) — scope to WET to be safe. |
| **2a** | **Defensive guard — refuse WET cleanly.** In `race_simulator._tire_deg_predictions`, detect the `n_features_in_` mismatch (`pipeline.named_steps[...].n_features_in_ != features.shape[1]`), and either zero that compound group's `predicted_delta` + flag it, or raise a typed error that `_run_simulation` / the endpoint turns into a clean 422 ("wet-weather tyre modelling unavailable"). | Small (~30 lines + a unit test). Frontend already has a failure state; add a specific message. | **No** — explicitly declines. But it stops the **whole task** dying; non-WET plans in the same request still return. | **Zero** — the guard only fires on a shape mismatch, which today is WET-only. |
| **2b** | **Alias `WET → tire_deg_inter.pkl` at model load.** In `_load_models()` (duplicated in `prediction_worker` and `strategy_service`), after loading, validate each tyre model's `n_features_in_` against `len(tire_deg_model.FEATURE_COLUMNS)`; if WET doesn't match, replace `_model_cache["tire_deg_wet.pkl"]` with the INTER model and log a warning. Everything downstream (`race_simulator`, `strategy_service`, `_run_inference`) then just works. | Small (~15 lines × 2 modules + a test). | **Approximately.** INTER (n=3,845, MAE 3.78) is a decent proxy for WET — both wet-weather, similar ballpark, INTER degrades slightly faster on a drying line. Physically plausible; *better* than the dedicated 5.79-MAE WET model. Slight semantic fudge (INTER curve reported as WET). | **Zero** — only remaps the `"WET"` key. |
| **3** | **Pad the feature vector to 8 and feed the stale model correctly.** The 8-feature schema is the 6 columns + `track_temp` + `air_temp`; those *are* available (`race_state.track_temp/air_temp`, imputed defaults 35/25 °C). Append them for the WET group only. | Small-moderate. | **Marginal.** Same 5.79-MAE model, just fed correctly. Not garbage, not good. **Re-entangles the codebase with the reverted 8-feature schema for exactly one compound** — a fragile special case that will cause the next surprise. | Low if scoped to WET, but this is the kind of special-case that rots. |

## A.7 Recommendation (Part A)

**Do 2b + 2a now:**
- **2b** — alias `WET → tire_deg_inter.pkl` at load. Plausible wet-stint numbers, zero risk to the
  4 working compounds, no ML work, unblocks the Simulator immediately.
- **2a** — add the feature-count guard in `_tire_deg_predictions` as a **permanent backstop**, so
  any future schema drift (any model) degrades gracefully instead of killing the task.

**Deferred Wiring entries to add:**
1. Retrain a real 6-feature `tire_deg_wet.pkl` (Option 1) — low priority. Note: 319 laps means it
   will never be accurate; it just removes the alias fudge.
2. **The promotion guard needs a feature-schema-compatibility check** — it currently keeps a
   schema-incompatible model in production purely on MAE. Add: reject promotion (or force it) if
   the candidate's feature count differs from what inference expects; and/or write the model's
   `feature_names`/`n_features` into the metrics sidecar and validate at load.

---

# PART B — nonsensical simulation output (positions & dry-INTER)

## B.1 The symptom

Test 2026-08-29: **NOR** (race leader per the frontend timing tower), lap **68/72**, pit at lap
**69 → INTERMEDIATE**, dry track.

Result shown:
- **"+16 positions"**
- **"No drivers within pit stop window — position unchanged by pit stop timing"**

Both nonsensical:
1. Inters on a dry track with 3 laps left is a terrible call (they overheat / grain in ~2 laps) →
   should be a large time/position **loss**, not a +16 gain.
2. **ANT** was ~10 s behind in P2. A ~22 s pit stop obviously drops NOR behind ANT (≥ 1 position
   lost), yet the result claims "position unchanged by pit stop timing".

This is **two independent problems**.

---

## B.2 — B1: garbage position & gap inputs for the auto-selected session

**Session:** Dutch GP 2026 Round 12 (Zandvoort), `c89a2b75-c95c-4c4d-bc29-b2f8c8c73caa`.
This is the session the new `GET /strategy/last-ingested-session` picker auto-selects
(newest `race_date` among R sessions with lap data; no `status` filter → it picks this
`status="scheduled"` session, ingested by the **Day 36 live dry run**, not a clean historical
ingest).

### B1a — `lap_data.position` is NULL for the entire session

Confirmed by DB query — `position` is NULL at lap ≤ 68 **and** at the latest lap for NOR / ANT /
LEC / PIA (all drivers). The Day 36 live ingest never populated the column for this session.

In `prediction_worker._build_race_state`:
```python
starting_position = position_by_driver.get(lap.driver_id) or lap.position or len(latest_laps)
```
- `position_by_driver.get(id)` — from `position_subq` join on `LapData.position` at lap ≤
  `current_lap` → **None** (NULL column).
- `or lap.position` — `lap` is from `latest_laps` (the driver's absolute-latest lap) → also
  **None**.
- → falls through to `or len(latest_laps)` → **~20 for every driver** (number of drivers with any
  lap in the session).

Then in `_run_simulation`:
```python
position_gain_loss = round(requester_state.starting_position - requesting_distribution.mean_position)
```
`= round(~20 - mean_position)`. The Monte Carlo ranks NOR near P1 (see B1b) → `~20 - ~1 ≈ +19`
→ the **"+16 positions"**. NOR is not gaining anything — the *start* number is a fallback
constant.

### B1b — cumulative race time is non-comparable across drivers (Deferred Wiring item A)

This is exactly **CLAUDE.md Deferred Wiring item A** (the
`SUM(lap_time_seconds) … WHERE lap_time_seconds IS NOT NULL` bug). `_build_race_state`'s batched
cumulative-time query is one of item A's **four listed call sites**.

DB query for laps ≤ 68 in this session:

| driver | laps ingested (range 9-72) | laps actually summed (`lap_time_seconds IS NOT NULL`) for ≤ 68 | `SUM(lap_time_seconds)` ≤ 68 | `pos` at ≤ 68 | `pos` latest |
|---|---|---|---|---|---|
| NOR | 61 | **56** | 4356.2 s | NULL | NULL |
| LEC | 62 | 58 | 4516.2 s | NULL | NULL |
| ANT | 62 | 58 | 4520.1 s | NULL | NULL |
| PIA | 63 | 59 | 4634.7 s | NULL | NULL |

- Laps 1-8 are missing for **everyone** (Day 36 finding — "Dutch GP laps 1-8 never live-ingested").
- Within laps 9-72, **different drivers are missing different numbers of laps** (NOR 61, LEC/ANT
  62, PIA 63), plus NULL-time laps (NOR has 1).
- For laps ≤ 68: NOR's `SUM` covers **56** laps, LEC/ANT **58**, PIA **59**. NOR's cumulative time
  is ~2 laps (~150 s) short of a comparable figure.
- Result: NOR sits at the **minimum** cumulative time in `race_state` (4356 vs LEC 4516 / ANT 4520
  / PIA 4635). The sim thinks **NOR leads by ~160-280 s** (real on-track gap: ~10 s).

Then `prediction_worker._build_plan_explanation`:
```python
drivers_overtaken = sorted(
    (... for driver in race_state.drivers
     if driver.driver_id != requester_state.driver_id
     and 0.0 < driver.cumulative_race_time_seconds - requester_state.cumulative_race_time_seconds
             < race_simulator.PIT_STOP_SECONDS),   # PIT_STOP_SECONDS = 22.0
    ...)
```
Every other driver's computed gap to NOR is 160-280 s → all **>> 22 s** → `drivers_overtaken` is
**empty** → frontend `PlanExplanationCard` renders
`"No drivers within pit stop window — position unchanged by pit stop timing"`.
ANT being 10 s behind in reality is invisible because the *computed* gap is ~164 s.

### B1c — where `_build_race_state` sits relative to `_resolve_position_context`

- `_resolve_position_context` (undercut/overcut, per-lap prediction path) — **fully unfixed**
  (its own Deferred Wiring entry: no `lap_number <= current_lap` bound).
- `_build_race_state` (this simulation path) — got the "anchor position/cumulative to
  `current_lap`" fix (`position_subq` / `cumulative_time_by_driver` capped at `current_lap`, under
  `feature/pre-day30-monte-carlo-fix`), **but still uses the broken
  `SUM(lap_time_seconds) … IS NOT NULL`**.
- **Sibling bugs, different functions.** Both are call sites of item A. This simulation did **not**
  execute `_resolve_position_context`.
- Zandvoort R12 makes item A bite *harder* than a replay of a clean historical session would,
  because it's partially live-ingested: missing laps AND a fully-NULL `position` column.

### B1 — suggested fixes

- **Real fix:** Deferred Wiring item A (the NULL-lap cumulative-sum, all 4 call sites). Needs
  either an ingestion-time absolute `Lap.Time` capture or query-time interpolation — see item A
  for the full analysis.
- **Cheap targeted mitigation (independent of item A):** add `WHERE status = 'completed'` to
  `strategy_service._fetch_last_ingested_session`'s query. That excludes Zandvoort R12
  (`status="scheduled"`) → the picker resolves to **Belgian GP 2026 R10** (a clean full
  `ingest_historical.py` ingest with real `position` data and contiguous laps), where the sim
  behaves far better. Decide whether that belongs in this endpoint or is out of scope.
  - Trade-off: on Supabase all 3 sessions are `completed`, so no change there; on local it moves
    the default from Zandvoort R12 → Belgian R10.
- **Also worth considering:** `_build_race_state`'s `starting_position` fallback
  (`or len(latest_laps)`) silently produces a meaningless number when `position` is NULL for the
  whole session — it could instead derive order from cumulative time, or the endpoint/sim could
  refuse a session with no position data with a clear message.

---

## B.3 — B2: the tyre models have no track-condition awareness (genuine model limitation)

### Findings

`tire_deg_model.FEATURE_COLUMNS` (`tire_deg_model.py` L25-32):
```
[lap_number, compound_encoded, tyre_age_laps, fuel_adjusted_time, circuit_id_encoded, driver_id_encoded]
```
- **No weather, no `track_status`, no wet/dry flag.**
- Even the **rejected** weather-experiment version only added raw `track_temp` / `air_temp`
  (temperature) — **never a track-condition or wet/dry signal**.
- `compound_encoded` for INTERMEDIATE is just the integer `1`
  (`{HARD:0, INTERMEDIATE:1, MEDIUM:2, SOFT:3, WET:4}`). The model learned INTER's degradation
  curve from ~6,500 historical INTER laps, which are essentially **all wet/damp** (nobody races
  inters on a dry track). It has one "how inters wear in the wet" curve and applies it
  unconditionally.
- `RaceSimulationInput.wet_track: bool` exists but is used in **exactly one place** —
  `sc_model.probability_within(race_state.circuit_name, lap_number, race_state.wet_track, 1)`
  (`race_simulator.py` L431-432), i.e. safety-car likelihood. It **never** reaches the tyre model.
  `_tire_deg_predictions` docstring explicitly: *"race_state.track_temp/air_temp are intentionally
  not read here"*.

### Why it produces a "gain"

A forced pit to INTER on lap 69 → fresh tyre (age 0), 3 laps to go. The model predicts a small
**negative** `lap_time_delta` for INTER at age 0 (fresh tyre = fast, same as any compound at age
0, relative to session median). So the sim shows NOR *gaining* time and never reproduces the
real-world "inters overheat and fall apart in ~2 laps on dry tarmac" catastrophe. Combined with
B1 (NOR already ranked ~P1 on garbage cumulative time), the net headline is "+16 positions".

### Why it can't be fixed by retraining alone

There is **no dry-INTER / dry-WET training data** — in a real race nobody runs a wet compound on a
dry track. Any model conditioned on a track-state feature would be *extrapolating* into an
unobserved region for the dry+wet-compound case.

### B2 — options

| Option | Effort | Notes |
|---|---|---|
| **(a) Add `track_status` / track-condition to `FEATURE_COLUMNS` and retrain all 5 tyre models** | Large | The model would learn "INTER in the wet" fine but still *extrapolate* for "INTER on dry" (no data). Also re-opens the whole 6-vs-8-feature promotion mess. Not clearly worth it. |
| **(b) Heuristic mismatch guardrail (recommended)** | Small-moderate | In `race_simulator.simulate_race` / `_run_simulation`: if a forced-pit compound ∈ {INTERMEDIATE, WET} while `race_state.wet_track` is False (or a dry compound while `wet_track` is True), apply a large fixed per-lap degradation penalty for that stint, **or** return the plan flagged "compound does not match track conditions — result not modelled". Cheap, honest, no retraining. |
| **(c) Frontend guard** | Small | Grey out / warn on selecting INTERMEDIATE or WET as a pit compound when the session/track is dry (and vice-versa). Weakest — hides the modelling gap rather than addressing it, but stops users generating nonsense. |

### B2 — proposed Deferred Wiring entry (exact wording to paste into CLAUDE.md)

> **[deferred — model limitation] The tyre-degradation models have no track-condition input, so
> INTERMEDIATE/WET degradation is modelled identically on a dry or wet track.**
> `tire_deg_model.FEATURE_COLUMNS` has no weather / `track_status` / wet-dry feature;
> `compound_encoded` is the only compound signal. INTER/WET curves were learned from historical
> (wet-only) laps and are applied unconditionally, so a Simulator what-if that pits to INTER/WET
> on a dry track shows a fresh-tyre time *gain* instead of the real-world catastrophic loss
> (`RaceSimulationInput.wet_track` exists but only feeds the safety-car model, never the tyre
> model). No dry-INTER/dry-WET training data exists, so retraining alone can't fix it — needs
> either a track-condition feature (large, still extrapolating), a heuristic compound-vs-
> conditions penalty in `race_simulator`, or a Simulator UI guard blocking wet-compound-on-dry
> what-ifs. Discovered 2026-08-29 via a NOR lap-68 dry-track pit-to-INTERMEDIATE sim that also
> surfaced the Zandvoort-R12 garbage `starting_position` / cumulative-time issue (see Deferred
> Wiring item A). Full analysis: `docs/simulator-issues-wet-model-and-position-context.md`.

---

# Key files & line references

| Path | Relevance |
|---|---|
| `backend/services/ml/race_simulator.py:225-252` | `_tire_deg_predictions` — builds the **6-column** feature vector; `pipeline.predict()` at **L252** is unguarded (only guards `pipeline is None` at L227). Crash site for Part A. Also the place to add the 2a feature-count guard. |
| `backend/services/ml/race_simulator.py:94-101, 431-432` | `wet_track` / `track_temp` / `air_temp` are inputs but only `wet_track` is used, and only for `sc_model.probability_within`. Part B2. |
| `backend/services/ml/tire_deg_model.py:25-44` | `FEATURE_COLUMNS` (6 cols, no weather/condition); L35-44 comment documents the 2026-07-16 revert from 8 → 6. |
| `backend/workers/prediction_worker.py` `_build_race_state` (~L663-826) | `starting_position` fallback `or len(latest_laps)` (B1a); `cumulative_time_by_driver` = `SUM(lap_time_seconds) … IS NOT NULL` (B1b, item A call site); `_run_simulation` (~L896-1007). |
| `backend/workers/prediction_worker.py` `_build_plan_explanation` (~L835-893) | `drivers_overtaken` = drivers within `0 < gap < PIT_STOP_SECONDS` behind requester → empty → "No drivers within pit stop window". |
| `backend/workers/prediction_worker.py` `_run_inference` | Contrast: wraps tyre `.predict()` in `try/except` and degrades. The sim path does not. |
| `backend/scripts/train_models.py` | `COMPOUND_TO_FILENAME` (L64-70); `download_metrics` key = `{tag}/{filename}.metrics.json` (L221-225); `serialize_evaluate_and_upload` promotion logic (~L257-289: `should_promote = current_holdout_mae is None or holdout_mae < current_holdout_mae`); `fetch_laps_from_db` — no compound / `is_valid` filter (L75-135). |
| `backend/scripts/retrain_incremental.py` | Weekly CI entrypoint; tire_deg loop — `if c_train.empty: continue` (WET's `c_train` is NOT empty → trained every run). |
| `backend/scripts/export_training_data.py` | Builds the base corpus parquet; no compound filter → WET laps ARE in it. |
| `backend/services/strategy_service.py` / `prediction_worker.py` `_load_models()` | Where the 2b WET→INTER alias would go (duplicated in both, per the codebase's no-cross-import convention). |
| `backend/services/strategy_service.py` `_fetch_last_ingested_session` | Add `status = 'completed'` here for the B1 mitigation. |
| `CLAUDE.md` → Deferred Wiring item A | The NULL-lap cumulative-sum bug (4 call sites, incl. `_build_race_state`). B1b is a manifestation of it. |
| `CLAUDE.md` → Data Quality Notes → "Weather features … regressed holdout MAE" (2026-07-11) | Context for how the 8-feature models came to exist and why SOFT/MEDIUM/HARD were refused. |
| `docs/day36-fixes.md` | The Dutch GP (Zandvoort R12) live dry run — source of the "laps 1-8 missing" + likely the NULL `position` column. |

---

# Data captured during investigation (for reference / re-verification)

**Model shapes** (worker container, `joblib.load` + `.named_steps[...].n_features_in_`):
`soft=6, medium=6, hard=6, inter=6, wet=8`.

**S3 `production/` timestamps:** `tire_deg_wet.pkl` = 2026-07-10 22:21:10;
`tire_deg_hard.pkl` / `tire_deg_inter.pkl` = 2026-08-03 08:35:3x.

**S3 metrics sidecars:**
- `tire_deg_wet.pkl.metrics.json` = `{cv_mae: 5.7906, cv_rmse: 6.9391, holdout_mae: 5.7906, n_samples: 319, promotion_basis: "cv_only"}`
- `tire_deg_inter.pkl.metrics.json` = `{cv_mae: 3.0886, cv_rmse: 3.6839, holdout_mae: 3.7786, n_samples: 3845, promotion_basis: "holdout"}`
- `tire_deg_hard.pkl.metrics.json` = `{cv_mae: 0.7014, cv_rmse: 1.6016, holdout_mae: 0.5168, n_samples: 46963, promotion_basis: "holdout"}`

**Base corpus parquet** (`training-data/base/laps.parquet`): 163,623 rows, seasons 2018-2025.
WET = 430 rows / 319 valid, seasons 2021 (31) / 2022 (320) / 2023 (47) / 2024 (32) / 2025 (0).
INTER by season: 2020 (56) / 2021 (480) / 2022 (1357) / 2023 (691) / 2024 (2143) / 2025 (1391).

**Local DB, 2018-2025 corpus, laps by compound:**
HARD 63,465 (56,727 valid) / MEDIUM 59,110 (51,133) / SOFT 28,665 (23,444) /
INTERMEDIATE 6,518 (4,837) / WET 493 (319) / plus SUPERSOFT/ULTRASOFT/HYPERSOFT/`nan`/`None`.

**Zandvoort R12 (`c89a2b75-…`) laps ≤ 68:** NOR 56 summed / 4356.2 s; LEC 58 / 4516.2 s;
ANT 58 / 4520.1 s; PIA 59 / 4634.7 s. `position` column NULL for all rows in the session.

**Worker log entry:**
`Task run_race_simulation[722d6827-…] raised unexpected: ValueError('X has 6 features, but StandardScaler is expecting 8 features as input.')` — repeated (retry).

---

# ANCHOR PROMPT — paste into the new session

```
Read docs/simulator-issues-wet-model-and-position-context.md in full, then read
CLAUDE.md (especially the Deferred Wiring section, particularly item A — the
NULL-lap cumulative-sum bug). Also read:
  - backend/services/ml/race_simulator.py
  - backend/services/ml/tire_deg_model.py
  - backend/workers/prediction_worker.py (_build_race_state, _build_plan_explanation, _run_simulation, _run_inference)
  - backend/scripts/train_models.py (COMPOUND_TO_FILENAME, download_metrics, serialize_evaluate_and_upload)
  - backend/scripts/retrain_incremental.py
  - backend/services/strategy_service.py (_load_models, _fetch_last_ingested_session)

Context: the Strategy Simulator has two independent open issues, both found
2026-08-29, both documented in that doc.

PART A — the sim task crashes with
"ValueError: X has 6 features, but StandardScaler is expecting 8 features"
whenever a WET compound is involved. Root cause: production tire_deg_wet.pkl is
an 8-feature model (July weather-experiment leftover) while soft/medium/hard/inter
are all 6-feature; the Aug-3 retrain's 6-feature WET candidate couldn't beat the
stale 8-feature model's cv_mae (5.79) so the promotion guard kept the incompatible
one. The crash kills the entire Monte Carlo task (no guard in
race_simulator._tire_deg_predictions).

PART B — separate. A dry-track pit-to-INTERMEDIATE sim for the race leader showed
"+16 positions" and "No drivers within pit stop window". Two causes:
  B1 = garbage position/cumulative-time inputs for the auto-selected session
       (Zandvoort R12: lap_data.position all NULL + unevenly-missing laps) — a
       manifestation of Deferred Wiring item A, in _build_race_state.
  B2 = genuine model limitation: the tyre models have NO track-condition feature,
       so INTER/WET degradation is modelled identically wet or dry — a fresh INTER
       predicts a time GAIN even on a dry track.

Tasks for this session (confirm scope with me before implementing):
  A) Implement option 2b + 2a from the doc: alias WET -> tire_deg_inter.pkl at
     model load (in both _load_models copies), AND add a feature-count guard in
     race_simulator._tire_deg_predictions so any model-shape drift degrades
     gracefully instead of crashing the task. Add tests. Add two Deferred Wiring
     entries: (i) retrain a real 6-feature tire_deg_wet.pkl, (ii) the promotion
     guard needs a feature-schema-compatibility check.
  B1) Quick mitigation: add `status = 'completed'` to
      strategy_service._fetch_last_ingested_session so the Simulator's non-live
      default stops landing on Zandvoort R12. (The deep fix is Deferred Wiring
      item A — do NOT attempt that here.)
  B2) Add the proposed Deferred Wiring entry (exact wording is in the doc). Decide
      with me whether to also add a heuristic compound-vs-track-conditions
      guardrail in race_simulator (option b) or a frontend guard (option c) now,
      or leave both fully deferred.

Do NOT run git commands. Report a plan before writing code.
```
