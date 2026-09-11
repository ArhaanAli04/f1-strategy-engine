# Tire Degradation Model Quality & Rival Pit Behavior in the Monte Carlo Simulator

> **Status as of 2026-09-11: CP1-CP4 done and verified against the real
> running stack (real retrain, real promotion, real advance-warning
> behavior confirmed for real drivers, and the exact §1 acceptance case
> re-run with both headline findings resolved). CP5 investigated — no
> clean threshold fix found; a monotonicity-constraint retrain was tried,
> measured against 89 real stints, made things worse, and was reverted.**
> CP6 not started. Read the "2026-09-11 Session Update" section immediately
> below first —
> it supersedes the "investigation only, NOT fixed" framing this paragraph
> originally carried (kept below as historical context for how this was
> first discovered, still accurate as a description of the ORIGINAL
> problem before any of CP1-CP4 existed).
>
> Surfaced 2026-09-07 running a
> real end-to-end sanity check of the What-If Simulator rebuild
> (`docs/core-feature-rebuild-whatif-simulator.md` CP1-CP6, ✅ complete —
> the plumbing that fix built is confirmed correct and is NOT what this
> document is about) against real data: a realistic 3-scenario compare for
> a driver running P3 at Belgian GP 2026 R10, roughly a third of the way
> through the race. That test's own mechanics (real per-rival probabilities,
> sign-aware degradation projection, correct enrichment rendering) all
> worked exactly as designed — see that document's own CP6/§7 write-up for
> the full plumbing verification. What that same test *also* revealed is
> the subject here: every one of the 3 scenarios showed the requester
> *losing* track position from a pit stop, and the pit itself was never
> compensated by any rival ever losing time to a stop of their own. This
> document investigates why, independently of the plumbing that surfaced it.
>
> Two related but mechanically distinct issues are documented:
> 1. **`tire_deg_model`'s predicted degradation curves behave non-physically**
>    across most of the realistic tyre-age range for this exact promoted
>    model set (§2a, §3 row 1).
> 2. **`race_simulator.simulate_race` has no mechanism to keep rival pit
>    behavior realistic** if the underlying classifier's calibration drifts
>    or degrades — the entire realism of "does anyone else in the field ever
>    pit" rests on one threshold cut of one classifier's raw output, with no
>    sanity check against real-world base rates (§2b, §3 row 2).
>
> A third, unexpected finding surfaced investigating issue 2 and is folded
> into it rather than split out further: the CURRENTLY DEPLOYED
> `pit_predictor.pkl` appears, on direct measurement against the same
> real drivers CLAUDE.md's own Day-43 checkpoint names as validated, to
> exhibit the exact same-lap-after-the-fact pattern that checkpoint's own
> label fix was supposed to have already eliminated (§2c). This is reported
> as a measured anomaly to independently reconcile, not a confirmed root
> cause — see that section's own hedging.
>
> Independently investigated and root-caused via direct measurement against
> the real running stack (real promoted models, real DB data, no mocks) —
> not assumed from reading code. Every number in this document was captured
> from a live command run against the local Docker stack on 2026-09-07; none
> are illustrative or approximate unless explicitly marked as such.

---

## 2026-09-11 Session Update — CP1-CP5 Done, CP6 Remains

> **Read this section first if resuming.** It supersedes the 2026-09-09
> section's status below (that section's own findings — the fuel-sign/
> leakage root cause, §2c's resolution — hold and are not repeated here in
> full) but adds: CP2 is now actually verified (was: code written, zero
> tests run), CP3's real retrain has actually happened and been promoted to
> S3 (was: not started), CP4 re-ran §1's exact acceptance case and both its
> headline findings are resolved, CP5 was investigated and a real fix
> attempted, measured, and reverted (no clean answer found — see its own
> section below for why, and what's left open), and two unplanned bugs were
> found and fixed along the way that the 2026-09-09 session had no way to
> know about. Only CP6 (optional resilience layer) remains, and CP5's own
> negative result is a real reason to reconsider whether CP6 is still
> "probably not needed" the way the 2026-09-09 pilot concluded.

### CP2 — promotion guard, now verified

`.venv/Scripts/python.exe -m pytest backend/tests/unit/test_train_models.py -m unit -q` from
the host venv: the 23 tests written 2026-09-09 all passed as written. Added
one more direct-coverage test the existing set was missing
(`test_version_mismatch_takes_priority_over_names_mismatch_at_matching_count`,
using tire_deg_hard's real production sidecar shape — version AND names
both fire at once, version must win) → 24 passed. `ruff check`/`ruff format
--check`/`mypy --strict` clean on all 5 changed files. Full backend unit
suite: 340 passed (329 CP1 baseline + 11 new CP2 tests), then 341 after
adding the test above and the cache-staleness fix below.

Before trusting it against real data, ran `serialize_evaluate_and_upload`
(the real function, not a reimplementation) against the REAL S3 production
sidecars — reads hit real S3, every write intercepted into an in-memory
overlay, zero touched real S3. Confirmed all 7 real production models would
force-promote on the next real retrain via `training_schema_version_
mismatch` (none had a `training_schema_version` recorded; 4/5 tire_deg
sidecars still carried `fuel_adjusted_time` in `feature_names`).

**Found along the way, not yet explainable at the time:** `production/
safety_car_model.pkl`'s `holdout_mae` was exactly `0.0` — `default_rate=
0.0`, all 24 `circuit_rates=0.0`. This is what led into the next section
rather than a blind `TRAINING_SCHEMA_VERSION` bump.

### The safety_car_model bug — Option A, investigated before trusting it

A bare version bump on `safety_car_model` alone would have force-promoted a
NEW all-zero model, since the training call sites still fed it `is_valid`-
filtered laps. Root cause, confirmed against the real local DB corpus (not
assumed): a lap run under an active SC/VSC period has an anomalous lap time
FastF1 marks `is_valid=False`, so filtering to `is_valid=True` removes
almost the ENTIRE positive-class signal `build_lap_flags` depends on —
3832 laps carry `track_status="4"` (SC) in the raw 163,623-lap corpus, **0**
after the `is_valid` filter. `_is_sc_or_vsc` itself works correctly on the
raw strings; it's just never given a row where it would fire.

Fixed: `safety_car_model.py` gained its own `TRAINING_SCHEMA_VERSION = 2`;
both `train_models.train_all()` and `retrain_incremental.retrain()` now fit
it on the already-unfiltered `pit_train_laps`/`pit_holdout_laps` frame
`pit_predictor` already builds for the identical reason, instead of the
filtered `train_laps`/`holdout_laps`. Verified against real data BEFORE
promoting (writes intercepted, no real S3 touched): refit locally found 286
real SC events in train (137,286 rows) and 33 in holdout (26,337 rows), 24
circuits get their own rate, `default_rate=0.00188`, holdout_mae=0.003653.
Ranked rates are directionally sane — Jeddah (0.00403), Baku (0.00333),
Suzuka rank highest; Spa (0.00046) and Las Vegas lowest.

### CP3 — the real retrain, executed and promoted

Ran `train_models.train_all()` for real against the local DB (119,984 train
laps / 23,043 holdout laps). Every one of the 7 models force-promoted via
`training_schema_version_mismatch`, exactly as CP2's dry run predicted:

| Model | Candidate holdout_mae | Previous production | Note |
|---|---|---|---|
| tire_deg_soft | 0.6356 | 0.6443 | genuinely better |
| tire_deg_medium | 0.5609 | 0.4972 | numerically worse — expected, not comparable across the leakage fix |
| tire_deg_hard | 0.5849 | 0.5168 | same |
| tire_deg_inter | 1.7695 | 3.7370 | genuinely better |
| tire_deg_wet | 3.9233 (cv-only, no 2025 WET holdout) | 5.9981 | genuinely better |
| safety_car_model | 0.00365 | 0.00000 (illegitimate) | now a real, non-degenerate model |
| pit_predictor | 0.3250 | 0.0327 | numerically worse — expected, old MAE was inflated by the broken same-lap label |

### An unrelated infrastructure bug found trying to verify CP3

The first verification pass looked IDENTICAL to this document's original
"before" numbers — because it was testing the same, unchanged, pre-fix
models. `prediction_worker`/`strategy_service`'s `_download_from_s3`/
`_download_metrics_from_s3` cached downloaded models to a LOCAL DISK
directory (`get_ml_settings().model_cache_dir`, default `/tmp/f1_models`)
and never re-checked S3 once a file existed there — not a per-process
cache, a forever cache. Confirmed via file timestamps: the host machine's
cache held `.pkl` files dated **2026-07-16** (pre-dating this entire
investigation); the LIVE `docker-worker-1` container's own cache held files
dated **2026-09-09** — CP1's session, not today's. This means `docker
compose restart worker` — this project's own documented convention for
picking up a newly-promoted model, stated plainly in CLAUDE.md's ML Model
Registry section — had never actually worked, because a plain restart
doesn't touch the container's writable filesystem layer where this cache
lived.

Fixed by removing the skip-if-cached-on-disk short-circuit entirely in both
duplicated helpers — always download fresh from S3 on process start. This
does not reintroduce a real per-call cost: `_load_models()`'s own module-
level `_model_cache` dict still guards each filename to exactly one fetch
per process, unchanged. 75 targeted tests + the full 341-test suite pass.
Verified live: cleared both containers' stale caches, restarted `backend`+
`worker`, confirmed fresh 2026-09-11 timestamps and `n_features=6` for
every tire_deg model including WET (no more `apply_incompatible_model_
fallbacks` alias needed).

### Real-world alignment check — against the newly-promoted models, verified two ways

**Via a fresh host-venv script AND, separately, inside the live
`docker-worker-1` process itself (bit-identical results both ways) —**
`pit_predictor` now shows genuine advance warning for all three real
drivers this document has used throughout, Belgian GP 2026 R10:

| Driver | Real pit lap | pit_probability the 5 laps before | On/after the pit |
|---|---|---|---|
| COL | 16 | 0.845 → 0.851 → 0.877 → 0.871 → 0.638 | 0.033 → 0.030 |
| LEC | 21 | 0.745 → 0.764 → 0.838 → 0.795 → 0.800 | 0.093 → 0.046 |
| GAS | 15 | 0.901 → 0.905 → 0.804 → 0.827 → 0.776 | 0.053 → 0.017 |

All three comfortably cross `ALERT_THRESHOLD=0.65` well before their real
pit — this is the exact mechanism §2b identified as the central, missing
piece (`pit_scores` staying ~0.0001 for every non-requester driver,
four orders of magnitude below threshold). **This also resolves §2c
definitively** (previously "a measured anomaly... not conclusively root-
caused"): the pre-CP3 deployed `pit_predictor.pkl` WAS the stale
pre-label-fix model — its `positive_rate=0.027613886339467` is bit-
identical (Δ=0.00e+00) to the old out-lap-only label's recomputed rate on
the identical corpus, versus the new K=3 label's `0.087409` (Δ=5.98e-2).
Not a stale-vs-fresh ambiguity or a regression — confirmed, not merely
plausible.

**`predicted_life_remaining` broke free of the permanent-40 peg — real
per-driver context, `prediction_worker._resolve_inference_context`'s own
real `fuel_load_penalty`, not a synthetic value:**

- COL (MEDIUM, laps 11→15): 40, 40, 39, 38, 37, then 0.0 on the real pit
  lap (fresh HARD, tyre_age=1), then 35.
- LEC (MEDIUM, laps 16→20): 36, 35, 34, 33, 32, then 0.0 on the real pit
  lap, then 10.
- GAS (MEDIUM, laps 10→14): 40, 40, 39, 38, 37, then 0.0 on the real pit
  lap, then 40.

**Honest nuance:** the raw `predicted_life_remaining` number does NOT cross
to 0 right at the real pit lap for MEDIUM (COL's is still 37 the lap before
pitting) — the advance warning above comes from `pit_predictor` reading
OTHER features (tyre age directly, gap, position), not from `predicted_
life_remaining` itself threshold-crossing. The `0.0` at `tyre_age=1` on
fresh HARD, present for all three drivers exactly on their real pit lap, is
the ALREADY-DOCUMENTED `tire_deg_hard.pkl` artifact (CLAUDE.md's Deferred
Wiring) — confirmed to persist unchanged after this retrain — but the new
`pit_predictor` is no longer fooled by it into a same-lap spike the way the
old same-lap-label model was (see the table above: probability correctly
DROPS right after each real pit, doesn't spike).

A synthetic sweep (ages 0-38, `lap_number=20`, a realistic `fuel_load_
penalty` via `fuel_load_penalty_seconds(20, 44)` — the first attempt used a
stale hardcoded `-1.0` calibrated to the OLD leaky feature's scale and gave
misleading flat-zero results, corrected before trusting it) confirms the
CORE problem — permanent pegging at 40 across the ENTIRE range — is gone
for SOFT/MEDIUM/HARD, though the shape is noisy (not perfectly monotonic —
a first-pass retrain with no dedicated hyperparameter tuning for curve
smoothness):

| Compound | life at ages 0,2,4,...,38 |
|---|---|
| SOFT | 0, 0, 30, 28, 26, 24, 29, 20, 18, 16, 14, 28, 26, 8, 6, 4, 2, 0, 0, 2 |
| MEDIUM | 0, 40, 40, 40, 40, 40, 40, 37, 34, 32, 31, 29, 27, 25, 23, 21, 19, 17, 15, 13 |
| HARD | 1, 16, 16, 16, 15, 15, 14, 15, 15, 15, 16, 15, 15, 13, 11, 9, 7, 5, 3, 1 |
| WET | 0, 0, 0, 7, 5, 3, 1, then 40 for every remaining age (small-sample noise) |
| INTERMEDIATE | 0 for ages 0-12, 15,13,11,9,7,6,3,2,2,1,2,3,8 (small-sample noise) |

WET/INTER stay noisy — expected, unchanged from the already-tracked
low-priority item (only 319/3845 real laps exist for those compounds).

### Updated checkpoint table

| CP | Work | Status |
|---|---|---|
| 1 | Fuel-correction sign fix + leakage removal | ✅ done 2026-09-09 |
| 2 | Promotion-guard gap (training_schema_version + feature_names) | ✅ done, verified 2026-09-11 |
| 3 | Real retrain of all 7 models, promoted via CP2's guard, real-world alignment check | ✅ done, verified 2026-09-11 (two unplanned bugs found+fixed along the way, see above) |
| 4 | Re-verify the LEC/Belgian GP 2026 R10/lap 15/3-scenario acceptance case against §1's before-numbers | ✅ done, verified 2026-09-11 |
| 5 | Revisit ALERT_THRESHOLD/DEGRADATION_THRESHOLD_SECONDS/MIN_LAPS_BETWEEN_PITS — tuned against broken inputs | ✅ investigated 2026-09-11 — measured, no clean threshold win available; a monotonicity-constraint retrain was tried and measured, did NOT help, reverted (see below) |
| 6 | Optional §2b base-rate sanity check in race_simulator | not started, optional — pilot evidence says probably not needed |

### CP4 — the exact §1 acceptance case, re-run against the newly-promoted models

Called the real `run_race_simulation` Celery task's own function body directly
(`.run()`, no broker — the same pattern this project's own integration tests
use, exercising the real task code, not a reimplementation) with the IDENTICAL
request §1 used: LEC, Belgian GP 2026 R10 (`da57b9fd-4976-4fce-91a1-
c7d0aac9c619`), `current_lap=15`, `current_tyre_age=15`, `current_compound=
MEDIUM`, `remaining_laps=29`, the same 3 scenarios (pit now → lap 16 HARD;
pit in 3 → lap 18 HARD; pit in 6 → lap 21 MEDIUM).

| Scenario | position_gain_loss | mean_position | fresh_tyre_gain_per_lap | drivers_overtaken rows with a real rival_projected_pit_lap |
|---|---|---|---|---|
| Pit now | **−2** (was −5) | **5.163** (was 8.000) | **+0.094 s/lap** (was −0.910) | **7 / 7** (was 0 / 7) |
| Pit in 3 | −5 (unchanged) | **7.666** (was 8.000) | **+0.075 s/lap** (was −0.947) | **7 / 7** (was 0 / 7) |
| Pit in 6 | −4 (unchanged) | 7.32 (was 6.997 — slightly worse) | **+0.444 s/lap** (was −0.164) | **7 / 7** (was 0 / 7) |

**Every one of §1's two headline findings is resolved:**

1. **"Every scenario's own degradation projection says the fresh tyre is
   slower than the one already on the car."** `fresh_tyre_gain_per_lap`'s
   sign flipped from negative to positive in EVERY scenario — the model now
   correctly projects a fresh tyre as faster than an aging one, in every
   comparison, including the MEDIUM→MEDIUM case (scenario 3) that previously
   showed a 21-lap-old tyre beating a brand-new one of the identical
   compound.
2. **"Not one rival, in any scenario, was ever modelled pitting."** All 7
   real `drivers_overtaken` rows, in all 3 scenarios (21 rows total), now
   carry a real, non-null `rival_projected_pit_lap` (16 or 18) with real
   `rival_pit_probability` (0.504-1.0) — every rival within a pit stop's
   worth of gap of LEC is now projected to pit organically, consistent with
   the genuine advance-warning `pit_probability` shape confirmed for real
   drivers in the alignment check above. This is exactly the mechanism §2b/
   §3-row-2 identified as the central, missing piece.

**"Pit now" improved substantially** (position_gain_loss −5→−2, mean_
position 8.000→5.163) — a materially less costly pit stop now that rivals
are modelled paying a symmetric price. "Pit in 3" and "Pit in 6" still show
a net loss, and that's expected, not a remaining bug: every rival's own
projected pit lap clusters at 16-18, the SAME narrow window as LEC's own
candidate stops — when the whole field boxes in the same few laps, an
individual stop doesn't uniquely disadvantage the requester the way a
frozen non-pitting field did, but it also doesn't guarantee a net gain
just because rivals eventually pit too. That is a plausible, physically
grounded outcome, not the "one driver serving a time penalty inside a field
that runs at fixed pace forever" failure mode §1 described. `starting_
position=3` matches the real DB data (LEC really ran P3 at this point).

**Not independently re-verified as part of CP4** (out of scope for this
specific acceptance check, tracked separately): whether `finish_ahead_
probability`'s own values are internally consistent with `position_
probabilities` the way the original What-If Simulator rebuild's CP6
verified — spot-checked only, not exhaustively; e.g. Pit-in-3's position-4
rival shows `finish_ahead_probability=0.006` (LEC almost certainly finishes
BEHIND them) alongside a −5 position_gain_loss for that scenario overall,
which is directionally consistent but wasn't cross-checked row-by-row
against `position_probabilities` the way the rebuild's own verification did.

### CP5 — investigated, no clean fix found, one real experiment tried and reverted

**Structural finding first, before any measurement:** `pit_predictor.
ALERT_THRESHOLD` isn't scoped to `race_simulator.py` alone — it's shared with
`strategy_service._first_pit_laps_over_threshold_batch`, the real, already-
shipped, already-validated `GET /pit-window` recommendation feature (CLAUDE.md's
Day-43 checkpoint). Any change ripples into both. `MIN_LAPS_BETWEEN_PITS` is
scoped only to `race_simulator.py` — safe in isolation, but the measurement
below found no evidence it's the binding constraint. `DEGRADATION_THRESHOLD_
SECONDS` is baked into `predicted_life_remaining`, one of `pit_predictor`'s own
training features — the CP3-promoted `pit_predictor.pkl` was trained against
this exact value, so changing it requires a full retrain, not a constant edit.

**Real measurement: does ALERT_THRESHOLD=0.65 currently produce sensible pit
timing?** Reused the real production function (`_first_pit_laps_over_
threshold_batch`) against 89 real historical stints across the three 2026
races this investigation has used throughout (Belgian R10, British R9, Canada
R5) — for each, evaluated 10 laps before the driver's real pit, with real
position/DB context, not synthetic inputs.

- 25/89 (28.1%) never crossed 0.65 within the 15-lap search horizon at all.
- Of the 64 that did cross, the predicted crossing lap was 5.7 laps early on
  average (median 7 laps early).
- **The miscalibration is compound-dependent, not a single global bias:**
  HARD (44% never-crossed) and SOFT (44% never-crossed) under-signal; MEDIUM
  (11% never-crossed) over-signals ~6-9 laps early.

**Threshold sweep (0.45-0.70) against the same 89 stints — no clean win at any
value:**

| threshold | never-crossed | mean error |
|---|---|---|
| 0.45 | 12.4% | −7.06 laps |
| 0.50 | 19.1% | −6.57 |
| 0.55 | 22.5% | −6.36 |
| 0.60 | 24.7% | −6.06 |
| 0.65 (current) | 28.1% | −5.66 |
| 0.70 | 36.0% | −5.02 |

A monotonic trade-off, not a sweet spot: every step down catches more stints
but predicts even earlier on average; every step up does the reverse.
Confirmed this isn't threshold-sensitive for most HARD "never-crossed" cases
specifically — their probabilities sat at 0.001-0.002, nowhere near any
threshold in this range, ruling out "just pick a lower number" for that
compound's real problem.

**The one real fix attempted: a monotonicity constraint on `tyre_age_laps`,
retrained and measured — did NOT help.** `tire_deg_model._build_pipeline()`'s
`XGBRegressor` had nothing telling it that an older tyre cannot physically be
faster than the same tyre one lap younger (a fact that's always true,
independent of each compound's legitimately-different curve SHAPE) — free to
fit any curve the training noise suggested, including HARD's measured
plateau (CP3's sweep: predicted life-remaining sitting flat around 15-16 laps
for nearly the whole realistic age range instead of counting down).
`monotone_constraints=(0, 0, 1, 0, 0, 0)` (only `tyre_age_laps`, index 2)
enforces this at training time via XGBoost's native support.

Retrained all 5 tire_deg models + `pit_predictor` (which depends on tire_deg's
own `predicted_life_remaining` as a training feature, so both must be
retrained together to avoid the exact train/inference skew CP1 was about) —
entirely in memory, no S3 writes, before deciding anything:

| Model | Candidate holdout_mae | CP3 production | Delta |
|---|---|---|---|
| tire_deg_soft | 0.6328 | 0.6356 | better |
| tire_deg_medium | 0.5909 | 0.5609 | worse |
| tire_deg_hard | 0.5896 | 0.5849 | worse (small) |
| tire_deg_inter | 2.1360 | 1.7695 | worse |
| tire_deg_wet | 5.1380 | 3.9233 | worse |
| pit_predictor | 0.3239 | 0.3250 | better (tiny) |

Then re-ran the identical 89-stint real-world measurement against these
candidates (still nothing promoted):

| threshold | NEW never% / mean err | CP3 baseline never% / mean err |
|---|---|---|
| 0.45 | 11.2% / −7.34 | 12.4% / −7.06 |
| 0.50 | 14.6% / −6.91 | 19.1% / −6.57 |
| 0.55 | 22.5% / −6.80 | 22.5% / −6.36 |
| 0.60 | 23.6% / −6.37 | 24.7% / −6.06 |
| 0.65 | 30.3% / −5.90 | 28.1% / −5.66 |
| 0.70 | 36.0% / −5.56 | 36.0% / −5.02 |

| Compound | NEW never% / mean err (@0.65) | CP3 baseline (@0.65) |
|---|---|---|
| HARD | **52%** / −6.15 | 44% / −6.67 |
| MEDIUM | 11% / −5.74 | 11% / −5.79 |
| SOFT | 44% / **−6.20** | 44% / −3.60 |

**Worse, not better, on nearly every axis** — including the exact HARD
never-crossed rate this was built to fix (44%→52%). SOFT's timing accuracy
notably worsened (−3.60→−6.20 laps early). MEDIUM was essentially unchanged.

**Working hypothesis for why, not yet independently confirmed:** monotonicity
only forbids local REVERSALS — it doesn't force a steeper rise. If HARD's
real degradation genuinely is flatter and more gradual than SOFT's (the whole
point of choosing a harder compound), a monotonic model can still legitimately
predict a curve that never reaches `DEGRADATION_THRESHOLD_SECONDS=1.5` within
a normal stint, and this constraint didn't change that. That reframes the
likely real problem: real F1 teams often pit HARD tyres for STRATEGIC
reasons — fuel-corrected stint planning, undercut threats, safety-car
timing — well before the tyre itself crosses a fixed degradation threshold. A
model that only sees lap-time degradation structurally can't predict that,
regardless of how physically sane its curve shape is. This experiment is real
evidence for that explanation (fixing curve sanity didn't move the real-world
number the expected direction), not just a restated guess.

**Reverted.** `_build_pipeline()` is back to the unconstrained XGBRegressor —
the change measurably underperformed CP3 on both the metric it was trained on
and the real-world test it was meant to improve, and nothing was ever
promoted to S3 (production ran CP3's models throughout this entire
experiment, unaffected). Full unit suite reconfirmed 341 passed after the
revert.

**Conclusion: CP5 as originally scoped ("retune these 3 constants") doesn't
have a good answer.** No threshold value is a clean win, `MIN_LAPS_BETWEEN_
PITS` isn't the bottleneck, `DEGRADATION_THRESHOLD_SECONDS` isn't safely
touchable without another full retrain, and the one real curve-shape fix
tried made things worse. The compound-dependent pit-timing miscalibration
this investigation found is real and quantified, but the fix isn't a
constant or a training constraint — it likely needs either (a) a genuinely
different signal beyond pure lap-time degradation (e.g. a feature capturing
typical strategic stint-length targets per compound/circuit, not just
predicted tyre pace), or (b) accepting this as an inherent limitation of a
degradation-only model and leaving CP6-style resilience (§2b's option) as
the more promising lever after all — a reversal of the 2026-09-09 pilot's
own conclusion, worth re-weighing now that CP5's own experiment failed to
close the gap a different way. Neither is attempted here; both are real,
separately-scoped future work, not a same-session follow-on.

### A mistake worth recording

While cleaning up local temp files during this session, a `rm -f <dir> -r`
command (GNU `rm` parses flags regardless of position, so this was `rm
-rf`) deleted a backup directory of the pre-CP3 stale model files, despite
the same command's own echo claiming they were being kept. Low real-world
impact — those were superseded, pre-fix artifacts already replaced in S3's
`production/` tag, not source of truth — but flagged here for the same
reason everything else in this document is: report outcomes faithfully,
including mistakes, not just successes.

### Anchor Prompt for Resumption (CP6) — paste into the new session

```
Read docs/tire-deg-model-quality-and-rival-pit-behavior.md in full, then read
its "2026-09-11 Session Update" section again carefully — that section (not
the 2026-09-09 section below it, and not the original §1-§5 further below
that) is the authoritative status. CP1-CP5 are done — do not redo them. CP5's
own section is important context for CP6: it investigated ALERT_THRESHOLD/
DEGRADATION_THRESHOLD_SECONDS/MIN_LAPS_BETWEEN_PITS, found no threshold value
is a clean fix (a full sweep against 89 real stints showed a monotonic
trade-off, not a sweet spot), then tried the one real curve-shape fix that
seemed principled (a monotonicity constraint on tyre_age_laps, retrained and
re-measured against the same real stints) and it measurably made things
WORSE, not better — reverted. Only CP6 remains.

CP6: a resilience layer in race_simulator independent of pit_predictor's own
calibration (e.g. a sanity check on the field's overall organic pit rate).
The 2026-09-09 pilot said this was probably NOT needed; CP4's real result
(organic rival pits firing correctly in that one acceptance case) supported
that at the time — but CP5's own negative result is real evidence the
underlying models still have real, unresolved compound-dependent bias (HARD
44% never signals at all, SOFT undershoots by ~4-6 laps, MEDIUM overshoots
early) that neither a threshold change nor the curve-shape fix tried so far
could close. Re-weigh "probably not needed" in light of that before deciding
whether to build CP6 or skip it again — it may now be the more honest
answer rather than the fallback.

CP5 also left two real, unresolved directions for anyone who wants to keep
pursuing a genuine fix rather than a resilience layer (not committed to,
just identified): (a) a feature capturing typical strategic stint-length
targets per compound/circuit — real pit timing is driven by fuel-corrected
race planning and undercut/safety-car strategy, not pure tyre-degradation
physics, which may be a structural ceiling on how well a degradation-only
model can ever predict it; (b) accepting the compound-dependent bias as a
known limitation and documenting it rather than continuing to chase a fix.

Also still open, tracked separately, NOT part of this doc's remaining scope:
the tire_deg_hard.pkl fresh-HARD-tyre-age=1 misprediction (CLAUDE.md's
Deferred Wiring — re-confirmed persisting after the CP3 retrain, downstream
harm reduced but not eliminated), and train-models.yml (CI) still fetching
zero 2026 laps (escalated GitHub-Actions/FastF1 item, unrelated to this doc).

Do NOT trust this document's findings blindly — independently re-verify
against the current codebase and real running stack before proposing
anything, the same way every checkpoint in this document was verified.
Propose a plan and wait for approval before implementing — same checkpoint
convention as every other session in this document's history. Do not run
git commands unless explicitly asked.
```

---

## 2026-09-09 Session Update — Root Cause Re-Diagnosed, Fix In Progress (CP1 done, CP2 code written but UNTESTED)

> **Read this section first if resuming.** It supersedes nothing above (the
> original investigation's findings were re-verified and hold), but corrects
> and extends §2a's root cause, resolves §2c definitively, and records exactly
> what has and hasn't been done toward a fix. The old §5 anchor prompt below is
> now historical — use this section's own "Anchor Prompt for Resumption" at the
> very end of this update instead.

### What this session found, independently re-verified against the real running stack

**§2a's root cause was NOT primarily training-data quality — it was two
upstream feature-engineering defects, both proven, not theorized:**

1. **Inverted fuel-correction sign.** `tire_deg_model.add_engineered_features`
   computed `lap_time - 0.03*(110 - fuel_at_lap)` — that's `0.03 × fuel BURNED`,
   which *grows* across the race. The correct term is `0.03 × fuel_at_lap`
   (fuel *on board*), which *shrinks*. A heavy car is slow, so the deployed
   formula made late (light, fast) laps look faster still — roughly doubling
   the fuel artefact instead of removing it. Measured within-stint slope of
   corrected lap time vs tyre age, ages 3-25, real 2018-2024 corpus (physical
   F1 degradation is +0.02..+0.15 s/lap): raw lap time SOFT −0.0130 / MEDIUM
   −0.0133 / HARD −0.0103; deployed (wrong sign) SOFT −0.0490 / MEDIUM −0.0536
   / HARD −0.0561; sign-fixed SOFT +0.0223 / MEDIUM +0.0261 / HARD +0.0351.
   `tire_stints.avg_deg_per_lap` (the DB's own stored ground truth, computed by
   `backfill_tire_data.py`'s sibling regression) had the **same bug** —
   negative median for every compound across 7381 real stints.
2. **`fuel_adjusted_time` was target leakage, fed out-of-distribution at
   inference.** The feature was `lap_time_seconds - fuel_penalty`; the target
   was `lap_time_seconds − median`. Identity `(fat - target) == (median -
   fuel_penalty)` confirmed to **1.42e-14** on the real corpus. Within a
   (session, driver) group the feature correlated with the target at
   **+0.91 to +0.96 median** (tyre_age_laps: only −0.07 to −0.10). Training
   mean ≈86-99s; every inference call site fed only the fuel term, ≈−1.5s (z =
   −6.9 to −15.1 outside the training distribution). Scored the way production
   actually calls the models, holdout MAE was **worse than a naive constant on
   every compound** (SOFT 0.885 vs 0.723, MEDIUM 0.932 vs 0.566, HARD 0.904 vs
   0.586).

**§2c is resolved, exactly, not just corroborated.** The deployed
`pit_predictor.pkl` (S3 `production/`, dated 2026-09-03 19:41 — one day
*before* the 2026-09-04 label-fix commit) is the **old, pre-label-fix model**.
Recomputing both labels on the identical training corpus: old-label
positive_rate = `0.027613886339467` — **bit-identical** (Δ = 0.00e+00) to the
deployed sidecar's own recorded `positive_rate`. New-label (K=3) positive_rate
= `0.087409` (Δ = 5.98e-2). Not a stale-vs-fresh ambiguity, not a measurement
difference — the fix was written and never promoted. This also directly
refutes the document's fallback explanation in §2c: the deployed model spikes
to 0.9995 at `tyre_age=1` even when `predicted_life_remaining=40` (a healthy
tyre) — it keys on the tyre-age reset itself, the exact pre-fix same-lap-
detector shape, not a `tire_deg_hard.pkl`-driven side effect.

**§2b: no resilience layer needed — confirmed by a full pilot retrain, not
argued from theory.** Built the corrected stack (sign fix + no leakage + new
K=3 pit label) end-to-end and asked the only question that matters: does a
normal mid-stint driver's `pit_probability` ever cross `ALERT_THRESHOLD=0.65`
organically? Result: 0.245 (age 1) → 0.541 (age 15) → **0.736 (age 18,
crosses threshold)** → 0.826 (age 30) — monotonic, physically realistic. All
three dry compounds' pilot models showed +0.030 to +0.040 s/lap degradation
slope and `predicted_life_remaining` finally varied with age instead of
pegging at the 40-lap cap. **The models were the whole problem** — no
`race_simulator` architecture change is needed. CP6 in the plan below is now
explicitly optional regression insurance, not required.

### The approved plan (6 checkpoints, user approved in full 2026-09-09)

| CP | Work | Type | Status |
|---|---|---|---|
| **1** | Fix fuel-correction sign; remove `fuel_adjusted_time` leakage; new shared `fuel_load_penalty_seconds()`; update all 8 real call sites (7 originally found + `ingest_historical.py`'s inline slope calc, caught by `mypy --strict`) | code | **✅ DONE, fully verified** |
| **2** | Promotion-guard gap — corrected models' honest MAE loses to the incumbent's leakage-inflated MAE, and the item-9 schema check (feature COUNT only) doesn't fire since both are 6/8 features | code | **🔶 Code written, ZERO tests run yet** — see below |
| **3** | Real retrain of all 5 tire_deg + safety_car + pit_predictor (new label), promote via CP2's fixed guard. Add a real-world alignment check (do predicted crossings line up with real historical pit timing?) | **real ML** | not started |
| **4** | Re-verify the LEC / Belgian GP 2026 R10 / lap 15 / 3-scenario acceptance case against §1's before-numbers | verification | not started |
| **5** | Revisit `ALERT_THRESHOLD`/`DEGRADATION_THRESHOLD_SECONDS`/`MIN_LAPS_BETWEEN_PITS` — implicitly tuned against broken inputs | ML tuning | not started |
| **6** | *Optional* — §2b base-rate sanity check in `race_simulator`. Pilot evidence says not required; keep as regression insurance only if wanted | code | not started, optional |

### CP1 — done, fully verified (do not redo)

All 8 call sites updated: `tire_deg_model.py` (new `fuel_load_penalty_seconds()`
— the single fuel definition; `FEATURE_COLUMNS` renamed `fuel_adjusted_time` →
`fuel_load_penalty`; `add_engineered_features` sign-fixed + target now built
from fuel-corrected times; `project_stint_delta`, `predict_life_remaining_batch`
updated), `race_simulator.py` (feature construction + a new `fuel_trend_seconds`
term added to `_advance_lap`/`simulate_race` so `predicted_finish_time`'s
2026-09-03-validated accuracy is preserved now that the target excludes the
fuel trend), `prediction_worker.py`, `strategy_service.py` (×4 call sites),
`explainability.py` (SHAP label), `train_models.py`, `backfill_tire_data.py`
(`_regression_slope` gained a `laps_in_session` param + fuel correction — **any
already-backfilled `tire_stints.avg_deg_per_lap` value is now stale and needs
`make backfill-tire-data` re-run, locally and on Supabase — not done this
session, deliberately left for the user**), `ingest_historical.py` (the inline
sibling slope calc in `_upsert_tire_stints`, caught by `mypy --strict`, not in
the original 7-call-site count).

**Verified, against the real corpus, through the actual production functions**
(not a standalone reimplementation): `add_engineered_features`' target slope
now +0.0152/+0.0259/+0.0363 s/lap on SOFT/MEDIUM/HARD (was negative);
`backfill_tire_data._regression_slope` median +0.0511/+0.0465/+0.0465 with
71-81% of real stints positive (was negative median, 45-48% positive); train/
inference feature parity exact to 0.00e+00 (was ~88s apart). Full unit suite:
**329 passed** (324 baseline + 5 new). Integration
(`test_race_simulation_serialization`/`test_strategy_endpoint`/
`test_live_prediction_pipeline`): **14 passed**, matches documented baseline.
`ruff check`, `ruff format --check`, `mypy --strict` — clean across all 147
backend files. Stack restarted, `/health` ok, all 4 inference paths (fuel
term, `project_stint_delta`, `predict_life_remaining_batch`,
`simulate_race`) smoke-tested against the real promoted models without error.

**Two things intentionally NOT done as part of CP1, both still open:**
- `tire_stints.avg_deg_per_lap` backfill re-run (local DB + Supabase) —
  affects `driver_style`'s `tyre_management_index` / Driver Style radar in the
  interim.
- Production behavior does **not** improve yet — the deployed models are
  still leakage-trained, so `predicted_life_remaining` is still pegged at 40,
  organic rival pits are still ~zero. This is expected; CP3 is what fixes it.

### CP2 — code written, **ZERO tests have been run**. Start here.

**What's implemented (needs verification, not re-design):**

- `tire_deg_model.py`: `TRAINING_SCHEMA_VERSION = 2` constant added, with a
  docstring explaining v1 (implicit, unrecorded) was the pre-2026-09-09 schema.
- `pit_predictor.py`: `TRAINING_SCHEMA_VERSION = 2` added — v1 (implicit) is
  the out-lap-only label (currently deployed, per §2c above), v2 is the
  2026-09-04 `pit_within_k_laps` K=3 label.
- `train_models.py`:
  - Two new pure helper functions: `_feature_names_mismatch(candidate_names,
    current_metrics)` (catches a same-count-different-meaning rename, e.g.
    CP1's own `fuel_adjusted_time` → `fuel_load_penalty` — a shape check alone
    would miss this) and `_training_schema_mismatch(candidate_version,
    current_metrics)` (catches a pure target-definition change with an
    UNCHANGED feature vector — pit_predictor's exact case, which
    `_feature_names_mismatch` cannot see at all since its FEATURE_COLUMNS
    never changed between labels).
  - `serialize_evaluate_and_upload` gained a `training_schema_version: int |
    None = None` parameter, stores it in every sidecar
    (`schema_metrics["training_schema_version"]`), and the promotion decision
    now force-promotes on ANY of three independent axes: feature-count
    mismatch (existing, item 9), feature-names mismatch (new), or
    training-schema-version mismatch (new) — reason string reports whichever
    fired, with a documented priority (`schema_mismatch` >
    `training_schema_version_mismatch` > `feature_names_mismatch`) when more
    than one fires at once.
  - `PromotionOutcome`'s docstring updated with the two new reason values.
  - Both `train_all()` call sites (tire_deg loop, pit_predictor) now pass
    `training_schema_version=tire_deg_model.TRAINING_SCHEMA_VERSION` /
    `pit_predictor.TRAINING_SCHEMA_VERSION` respectively.
- `retrain_incremental.py`: `_promote_and_record` gained the same parameter
  and threads it through identically at its tire_deg and pit_predictor call
  sites (mirrors `train_all`'s structure, per this file's own docstring
  convention of reusing `train_models`' promote logic).

**Tests written but NEVER RUN** (added to
`backend/tests/unit/test_train_models.py`): direct unit coverage of both new
helper functions (6 tests: None-candidate no-op, legacy-incumbent-no-value
mismatch, identical-values no mismatch, ×2 axes); an isolated
`training_schema_version`-only mismatch test using the **real MEDIUM MAE
numbers from this session's investigation** (incumbent 0.497, candidate
0.543, must force-promote despite the worse number); an isolated
`pit_predictor`-specific version-mismatch test with byte-identical
`feature_names` on both sides (proves the names check alone cannot catch
this class of drift); an isolated `feature_names`-only mismatch test (no
version declared on either side, same count, different names — the literal
CP1 rename); a non-regression test (matching version+names+count, worse MAE
→ correctly does NOT promote); a three-way-conflict priority test
(`schema_mismatch` wins). Import of `pit_predictor.FEATURE_COLUMNS` added at
the top of the test file.

**⚠️ Immediate next action for the resuming session:** run
`.venv/Scripts/python.exe -m pytest backend/tests/unit/test_train_models.py -m unit -q`
from the host venv (NOT inside the worker container — pytest markers aren't
registered there and it hung twice this session with no output for that
reason; ~48-160s is the expected real runtime from the host based on this
session's other suite timings). These tests have literally never executed —
treat every one as unverified until it has actually passed. If anything
fails, it's most likely either: a JSON round-trip type mismatch (the fake S3
client stores/reads real JSON, so `int` vs `float` for
`training_schema_version` could differ from what a live-Python-object
comparison expects — check `_training_schema_mismatch`'s `!=` against a
JSON-round-tripped value), or a priority-ordering edge case in the three-way
test. After tests pass: run `ruff check`/`ruff format --check`/`mypy --strict`
on `train_models.py`, `retrain_incremental.py`, `tire_deg_model.py`,
`pit_predictor.py`, `test_train_models.py` (none of this has been linted or
type-checked yet either — CP1's clean lint run does NOT cover these CP2
edits). Then run the full unit suite once more to confirm no other test
depends on `serialize_evaluate_and_upload`'s old signature in a way CP2's new
optional parameter broke (it's additive/keyword-only-by-position-default, so
this should be a formality, but has not been confirmed).

**After CP2 is verified:** report results to the user in the same style as
CP1's report (real measurements, what was verified, what wasn't, honest
framing) and **wait for explicit approval before starting CP3** — this
project's established checkpoint convention (propose→approve→implement→
verify→wait), which this session's user message re-confirmed ("Report
results and wait for approval before proceeding to CP2" — the same applies
to every subsequent checkpoint unless the user says otherwise).

### Anchor Prompt for Resumption — paste into the new session

```
Read docs/tire-deg-model-quality-and-rival-pit-behavior.md in full, then read
its "2026-09-09 Session Update" section again carefully — that section (not
the original §1-§5 below it) is the authoritative status. CP1 (fuel-correction
sign + leakage fix) is done and fully verified — do not redo it. CP2
(promotion-guard fix: training_schema_version + feature_names mismatch
detection) has code written in train_models.py, retrain_incremental.py,
tire_deg_model.py, and pit_predictor.py, and tests written in
test_train_models.py, but NONE of it has been run yet — no tests, no lint, no
mypy. Your first action must be running those tests from the host venv
(.venv/Scripts/python.exe -m pytest backend/tests/unit/test_train_models.py -m
unit -q — NOT inside the Docker worker container, which hung twice with no
output in the prior session), then lint/mypy on the 5 changed files, then the
full unit suite. Fix anything that fails using the same rigor as CP1 (real
measurements, no unverified claims). Report results to the user in the same
style as the CP1 report this session already gave, and WAIT for explicit
approval before starting CP3 (the real ML retrain) — do not proceed
automatically. Do not run any git commands unless explicitly asked.
```

---

## 1. Original Vision / Current Behavior

From this project's own architecture rationale (CLAUDE.md, "Why Monte Carlo
for race simulation, not a deterministic model?"):

> F1 strategy is inherently probabilistic. Safety cars, reliability
> failures, rain, and opponent reactions are random. A deterministic model
> gives false confidence. Monte Carlo with 1000 simulations returns a
> probability distribution over outcomes which is the honest representation
> of uncertainty.

Implicit in "opponent reactions are random" is that opponents *react* —
specifically, that rivals pit during the race, at realistic times, subject
to the same tyre-degradation pressure the requester faces. A pit stop's
*cost* (track position lost while stationary and rejoining) is only ever
worth comparing against its *benefit* (fresher tyres, eventually overtaking
rivals who are on older tyres or who paid the same cost themselves) if the
simulation actually models rivals eventually needing to make the same
decision. A Simulator where the requester is the only driver who ever pits
is not simulating a race — it's simulating one driver serving a time
penalty inside a field that runs at fixed pace forever.

**What the real test case showed instead.** LEC, real P3 at Belgian GP 2026
R10, lap 15 of 44 (current_tyre_age 15, real compound MEDIUM — all captured
directly from `lap_data`, not assumed), compared three scenarios:

| Scenario | pit_laps → compound | position_gain_loss | mean_position | fresh_tyre_gain_per_lap |
|---|---|---|---|---|
| Pit now | [16] → HARD | **−5** | 8.000 | **−0.910** s/lap |
| Pit in 3 | [18] → HARD | **−5** | 8.000 | **−0.947** s/lap |
| Pit in 6 | [21] → MEDIUM | **−4** | 6.997 | **−0.164** s/lap |

Every scenario is a net loss, and every scenario's own degradation
projection says the *fresh* tyre is *slower* than the one already on the
car — including the MEDIUM→MEDIUM comparison (scenario 3), where a 21-lap-old
tyre is projected faster than a brand-new one of the identical compound.
Every one of the 7 real `drivers_overtaken` rows across all three scenarios
came back with `rival_projected_pit_lap: null` — not one rival, in any
scenario, was ever modelled pitting during the whole 23-29 remaining-lap
window. A real Belgian Grand Prix has 20 cars and, in reality, every one of
them pits at least once. The Simulator's own field, run forward from a real
mid-race snapshot, models effectively zero of them doing so.

This is the gap this document investigates: not "is the plumbing built
correctly" (it is — see the parent rebuild doc), but "does what the
plumbing correctly reports match how F1 strategy actually behaves."

---

## 2. What Currently Exists

### 2a. `tire_deg_model`'s predicted degradation curves, measured directly

`tire_deg_model.predict_life_remaining_batch` (see its own docstring)
simulates `tyre_age_laps + 0..MAX_LOOKAHEAD_LAPS-1` (40 laps) forward from a
given lap and returns the first offset where predicted `lap_time_delta`
crosses `DEGRADATION_THRESHOLD_SECONDS` (1.5s), capped at `MAX_LOOKAHEAD_LAPS`
if it never crosses. This is the ONLY tyre-health signal fed into
`pit_predictor`'s 8-feature vector (as `predicted_life_remaining`), in BOTH
the live per-lap prediction path (`prediction_worker._run_inference`) and
the forward Monte Carlo path (`race_simulator._tire_deg_predictions`) — the
same function, same models, same call shape in both.

Measured directly inside the running worker container, sweeping
`tyre_age_laps` 0→38 in steps of 2 against each of the 5 currently-promoted
tire_deg pipelines (lap_number=20, fuel_adjusted_time=-1.0 held constant,
matching `predict_life_remaining_batch`'s own documented "fixed at
current-lap value" behaviour):

| Compound | `predicted_life_remaining` across ages 0-38 | Raw predicted `lap_time_delta` trend |
|---|---|---|
| SOFT | **40 for every age ≥ 0** | Near-zero to mildly positive (+0.05 to +0.68s), never re-crosses 1.5s after the first lap |
| MEDIUM | **0 at age 0, then 40 for every age ≥ 2** | **+3.32s at age 0** (a fresh tyre showing an implausible spike — see §2c's related HARD finding), then trends from **−0.66s down to −1.58s** as age climbs — i.e. the model predicts an AGING tyre gets FASTER, not slower |
| HARD | **40 for every age ≥ 0** | −1.74s to −1.01s across the whole range, gently trending toward slower but never within 0.5s of the threshold |
| WET | **40 for every age ≥ 0** | −0.08s to −5.2s (getting much faster with age — the model's steepest "backwards" trend of any compound) |
| INTERMEDIATE | 40 at ages 0-2, then **9 for every age ≥ 4** | The one compound that DOES cross the threshold, and does so almost immediately — separately explained by CLAUDE.md's own "no track-condition input" limitation (INTER curves are learned from historical WET-track laps only and applied unconditionally) |

Cross-checked against the real simulation, not just this synthetic sweep:
instrumenting `race_simulator._pit_scores` during a real `simulate_race`
call for COL (a real Belgian GP driver, tyre_age growing from 10 at
current_lap=10) showed `predicted_life_remaining_col=40.00` for every one
of the 8 simulated laps checked, and pit_score mean 0.0000-0.0001 (max
0.0003) throughout — four orders of magnitude below `ALERT_THRESHOLD=0.65`.

**This is a real, measured, non-physical characteristic of the currently
promoted tire_deg models for SOFT/HARD/WET across virtually their entire
realistic tyre-age range, and for MEDIUM past roughly age 2** — not a
one-off outlier and not an artifact of this document's own synthetic sweep
(the real-simulation instrumentation shows the identical number). A tyre
that never predicts meaningfully degrading, or that predicts *getting
faster* with age, gives `pit_predictor` no legitimate signal to ever
recommend a stop on tyre-health grounds alone.

### 2b. How `race_simulator.simulate_race` currently decides IF/WHEN any driver pits

Every driver in the field — requester and rivals alike — is evaluated
identically, every lap, by the same code path (`race_simulator.py`,
`simulate_race`'s per-lap loop):

1. `_tire_deg_predictions` batches `predicted_life_remaining` for every
   (simulation, driver) pair via each driver's own compound's pipeline (§2a).
2. `_pit_scores` batches `pit_predictor.pkl`'s raw probability for every
   (simulation, driver) pair from its 8-feature vector (`tyre_age`,
   `predicted_life_remaining`, `gap_to_ahead`, `gap_to_behind`,
   `safety_car_probability`, `laps_to_race_end`, `position`, `fuel_load_est`).
3. `pit_flags = (pit_scores > pit_predictor.ALERT_THRESHOLD) & (tyre_age >=
   MIN_LAPS_BETWEEN_PITS)` — `ALERT_THRESHOLD=0.65`, `MIN_LAPS_BETWEEN_PITS=5`.
   This is a **flat, global threshold** — the same 0.65 cut for every
   driver, every lap, every compound, with no per-compound or per-race
   adjustment and no fallback if the classifier's own calibration is off.
4. `forced_pit_laps` (the requester's own what-if plan) ONLY ever overrides
   the requester's `pit_flags` entry — every other driver's `pit_flags`
   comes exclusively from step 3, autonomously, every lap, for the whole
   simulated remainder. This is architecturally correct and exactly what a
   Monte Carlo simulation of a full field should do — **the mechanism for
   "rivals pit too" already exists and is wired correctly.**

"Rivals who've already pitted before the requester's decision point" are
already represented correctly and need no new mechanism: `_build_race_state`
reads each driver's real current `compound`/`tyre_age_laps` from their
latest `LapData` row at `current_lap`, so a rival who pitted on lap 10 (real
data) simply starts the simulation already on their real post-pit tyre —
this part of "realistic rival state" already works, confirmed by this
document's own LEC test (HAD, HAM, PIA, NOR etc. all entered the simulation
with their real lap-15 compound/tyre_age).

**The gap is entirely in step 3 above**: given real field data, `pit_scores`
essentially never exceeds 0.65 for a non-requester driver during the
remaining simulated window, so `pit_flags` for every rival stays `False`
for the entire simulated remainder in practice — not because the mechanism
is missing, but because the one signal it depends on (§2a) rarely produces
a value the threshold will ever catch.

### 2c. A measured anomaly in the currently-deployed `pit_predictor.pkl` — investigated, not fully resolved

`pit_predictor.py`'s own module docstring documents a real, already-fixed
bug (2026-09-04, core-feature-rebuild-strategy-recommendations.md
Checkpoint 6): the original training label marked only a stint's OUT-lap
(the first lap on a fresh tyre) as positive, which taught the model
"`tyre_age` just reset → a pit just happened" instead of genuine advance
warning — measured at the time as "`pit_probability` stayed near 0.0000 for
every lap up to and including the lap before a real pit, spiked to ~0.9999
exactly ON the pit lap, then collapsed back to near-zero the next lap." The
fix changed the label to `pit_within_k_laps` (K=3), and CLAUDE.md's own Notes
record it validated against LEC/COL/GAS's real Belgian GP 2026 R10 pit stops
— the very same session and drivers used throughout this document — as "new
model elevated 0.73-0.92 for the 5 laps approaching each pit, dropping
sharply on the out-lap."

Calling the live inference path directly (`prediction_worker._resolve_
inference_context` + `_run_inference`, the exact function `POST
/predict`/the live per-lap pipeline uses) for COL at every real lap from 10
through 16 against the CURRENTLY loaded, freshly-downloaded-from-S3
`pit_predictor.pkl` gave:

| lap | compound | tyre_age | `pit_probability` | `predicted_life_remaining` |
|---|---|---|---|---|
| 10 | MEDIUM | 10 | 0.000067 | 40.0 |
| 13 | MEDIUM | 13 | 0.000069 | 40.0 |
| 14 | MEDIUM | 14 | 0.000069 | 40.0 |
| 15 | MEDIUM | 15 | 0.000059 | 40.0 |
| 16 | **HARD** | **1** | **0.999904** | **0.0** |

COL's real pit was between laps 15 and 16 (lap 16 is their real, recorded
first lap on HARD). LEC (real pit lap 21) and GAS (real pit lap 15) each
show the identical shape in their own persisted `StrategyPrediction` rows —
`pit_probability` ~0.99-0.9999 exactly on their own real pit lap, near-zero
(0.0001-0.13) on every lap around it, both before and after.

This is the exact same-lap-after-the-fact shape the docstring describes as
the PRE-fix bug, for the exact three drivers the post-fix validation names
— not the "elevated 0.73-0.92 approaching, dropping on the out-lap" shape
the checkpoint recorded. It is also directly explained by an entirely
different, already-documented bug rather than a training-label regression:
CLAUDE.md's `tire_deg_hard.pkl` entry records that a FRESH HARD tyre
(`tyre_age_laps=1`) mispredicts an implausible **+1.71s** degradation spike
— exactly the mechanism visible in the row above (`predicted_life_remaining`
collapsing to `0.0` the instant COL is on a brand-new HARD tyre, which is
what actually drives `pit_probability` to spike, one lap too late to be a
genuine advance warning of the pit that already happened).

**This is reported as a measured, reproducible anomaly to independently
reconcile, not a conclusively root-caused finding of its own** — the
`pit_predictor.pkl` currently downloaded from S3 could genuinely predate
the label-fix retrain's promotion (`train-models.yml`'s own already-tracked
"fetches zero 2026 laps" problem, per CLAUDE.md, could plausibly have
prevented a fixed-label retrain from ever landing against 2026 data), or
this session's specific driver/lap combination could differ from whatever
the original CP7 harness measured in a way not yet identified. No sidecar
metrics file exists locally for `pit_predictor.pkl` (unlike every tire_deg
model) to check its training provenance directly. Whichever it is, the
PRACTICAL effect on `race_simulator.simulate_race` is the same either way:
zero usable advance-warning signal for a non-requester driver anywhere
in the approach to a real pit, only a same-lap-or-later spike driven by an
already-fresh (not aging) tyre being misread as needing to pit — which, per
§2b's mechanism, arrives too late in the simulated timeline to ever
register as an organic `pit_flags=True` BEFORE the requester's own
what-if decision point in a typical comparison window.

---

## 3. Gap Analysis

| What should happen | What currently happens | Root cause |
|---|---|---|
| A tyre's predicted degradation should increase (or at least not decrease) as it ages, eventually crossing a threshold that triggers a pit recommendation within a realistic stint length (typically 15-30 laps). | SOFT/HARD/WET show flat, near-zero predicted delta across the ENTIRE realistic age range (never approaching the 1.5s threshold); MEDIUM predicts an aging tyre getting FASTER past age ~2. `predicted_life_remaining` stays pegged at the 40-lap cap for all of these, for virtually the whole race. | **§2a** — a real, measured tire_deg model quality issue, confirmed via direct pipeline sweep AND live in-simulation instrumentation, not assumed. |
| Every driver in a Monte Carlo field simulation — not just the requester — should organically pit at some point during a 20-40 lap simulated window, since `pit_flags` is architecturally computed identically for everyone. | In every real scenario tested (7 rivals × 3 scenarios, 2 separate real driver/lap windows across two verification sessions), `pit_flags` never fires organically for a single non-requester driver. `projected_pit_laps` (the very field this session's own rebuild added to surface exactly this) is `null`/empty for every rival, every time. | **Directly downstream of §2a** — the mechanism (§2b) is correctly built and requires no structural change to fire correctly; it simply never receives a `pit_scores` value anywhere near `ALERT_THRESHOLD=0.65` under real conditions, because `predicted_life_remaining` never leaves its near-permanent "healthy" state. |
| A driver approaching a real pit stop should show a rising `pit_probability` in the laps BEFORE the stop — the entire point of the 2026-09-04 label fix and its own validated 0.73-0.92 result. | For LEC/COL/GAS in Belgian GP 2026 R10 — the exact drivers/session that fix was validated against — the CURRENTLY deployed model shows near-zero probability approaching the pit and a same-lap-or-after spike exactly matching the PRE-fix bug shape, driven by the separately-documented HARD-tyre-fresh-lap misprediction. | **§2c — unresolved**, either a stale/unpromoted model, an unreconciled measurement difference from the original validation, or both. Investigate before assuming §2a's fix alone would restore genuine advance warning. |
| A pit stop's cost (position lost) should be weighed against a realistic chance of gaining position back as rivals eventually pay the same cost. | Every scenario in the real test showed a clean, uncompensated loss — `position_gain_loss` −5/−5/−4 with zero rivals ever losing time to their own stop in the same simulated window. | **Compounding consequence of the two rows above** — not a separate bug in `simulate_race`'s ranking/probability math itself (that machinery, and this session's own new `finish_ahead_probability`/`projected_pit_laps` fields exposing it, are confirmed correct — see the parent rebuild doc). |
| The simulation's realism should not depend entirely on one classifier's calibration holding up perfectly forever. | `ALERT_THRESHOLD` is a single hardcoded global cut with no sanity check against real-world pit-rate base rates (a real 44-lap race has ~20 real pit stops across the field; this simulation's own field currently produces ~0 organic ones in a 20-30 lap window) and no fallback layer if the classifier drifts. | **Architectural gap, independent of §2a's specific bug** — even a perfectly retrained tire_deg model would leave the simulation's rival-pit realism as a single point of failure resting entirely on one threshold cut of one classifier. |

---

## 4. What Needs To Change

High-level scoping only — the future session should design the actual
approach, per its own anchor prompt below.

- **Fix (or work around) `tire_deg_model`'s non-physical degradation
  curves for SOFT/HARD/WET/MEDIUM.** This is real ML work — auditing
  training-data coverage across the realistic tyre-age range (mirroring the
  already-documented `tire_deg_hard.pkl` first-lap investigation's method:
  characterize whether this is a training-data sparsity issue, a feature
  mis-specification, or something structural about how `lap_time_delta` is
  defined relative to fuel burn), then retraining and re-validating against
  real historical stint lengths — does a retrained model predict threshold
  crossings that line up with when drivers actually, historically, pitted?
  That real-world alignment check does not currently exist anywhere in this
  codebase for `predict_life_remaining_batch`'s output, and would be the
  right acceptance criterion for any retrain here.
- **Independently reconcile §2c before assuming a tire_deg retrain alone
  fixes rival pit timing.** Confirm whether the currently-deployed
  `pit_predictor.pkl` is genuinely the post-label-fix model (check S3's
  `:production` tag's training date/label directly, or force a fresh local
  retrain via `evaluate_pit_predictor_label_fix.py` and compare) before
  concluding the label fix itself needs revisiting — it may simply never
  have been promoted against real 2026 data, consistent with CLAUDE.md's
  own already-tracked `train-models.yml` zero-2026-laps problem.
- **Decide whether `race_simulator` needs a resilience layer independent of
  a perfectly-calibrated classifier.** Options to weigh, not pre-decided:
  a per-compound/circuit `ALERT_THRESHOLD` calibrated against real
  historical pit-lap distributions instead of one global 0.65; a
  soft/probabilistic pit-lap sampling approach (e.g., sample each driver's
  pit lap from a distribution fit to real historical stint lengths for
  their compound/circuit, rather than a hard per-lap threshold on a single
  classifier); or a monitoring/sanity check that flags when a simulated
  field's overall organic pit rate falls far outside a plausible real-world
  range, so a future model regression is caught rather than silently
  producing scenarios like this document's own LEC test. Each has real
  trade-offs on realism vs. complexity vs. how much it entangles with
  `pit_predictor`'s own intended role — the future session should treat
  this as a genuine design decision, not a given.
- **Re-verify with the SAME real test case this document used** (LEC,
  Belgian GP 2026 R10, lap 15, the 3-scenario compare) once a fix lands, as
  the concrete before/after acceptance check: does at least one rival show
  a non-null `rival_projected_pit_lap` somewhere in a 20+ lap window, and
  does a reasonable what-if (e.g., pitting onto a fresher tyre at a
  sensible lap) show a plausible chance of *gaining* position, not a
  guaranteed loss every time?
- **This is real ML/simulation-logic work, not a wiring fix** — likely
  touching model retraining, `race_simulator.py`'s pit-decision logic, and
  possibly `pit_predictor.py`'s own feature set or threshold calibration.
  Size accordingly; this is not a same-session follow-on to the CP1-CP6
  rebuild that surfaced it.

---

## 5. Anchor Prompt — paste into a new session

```
Read docs/tire-deg-model-quality-and-rival-pit-behavior.md in full before
doing anything else. It documents two related but mechanically distinct
issues found during real end-to-end verification of the What-If Simulator
rebuild (docs/core-feature-rebuild-whatif-simulator.md, CP1-CP6 — that
rebuild's own plumbing is confirmed correct and is NOT what this document is
about):

1. tire_deg_model's predicted degradation curves behave non-physically
   (near-flat or improving with tyre age) across most of the realistic
   tyre-age range for SOFT/HARD/WET/MEDIUM on the currently promoted models.
2. race_simulator.simulate_race has no resilience mechanism to keep rival
   pit behavior realistic when the underlying classifier's signal fails —
   every non-requester driver in a real Monte Carlo run currently pits
   effectively never, so every forced what-if pit stop looks like a pure,
   uncompensated cost with no rival ever paying a symmetric price.

A third finding (§2c) is a measured anomaly — the currently-deployed
pit_predictor.pkl appears to show the exact same-lap-after-the-fact pattern
its own documented 2026-09-04 label fix was supposed to have eliminated,
for the same real drivers (LEC/COL/GAS, Belgian GP 2026 R10) CLAUDE.md's own
Day-43 checkpoint names as validated with the fixed behavior. This is
reported as unresolved, not root-caused — independently confirm or refute
it before assuming either direction.

Do NOT trust this document's findings blindly — independently re-verify
everything against the current codebase and, ideally, against the real
running stack (same method this document used: direct instrumentation and
real API calls, not just reading code) before proposing anything. Model
files, training data, and CI status (train-models.yml's own already-tracked
"fetches zero 2026 laps" problem) may have changed since 2026-09-07.

This is real ML/simulation-logic work — likely touching tire_deg model
training/retraining, pit_predictor's feature set or calibration, AND
race_simulator.py's pit-decision logic for non-requester drivers. It is NOT
a quick wiring fix and should NOT be scoped or estimated like one. Before
writing any code:

1. Re-investigate §2a-§2c's findings against the current codebase and
   current promoted models — confirm or correct each one, with real
   measurements (pipeline sweeps, real in-simulation instrumentation, real
   API calls against real data), not just re-reading this document's own
   numbers.
2. Propose a complete plan for what should change — covering at minimum:
   whether/how to retrain or correct the tire_deg degradation curves; how
   to resolve §2c (stale model vs. genuine regression vs. measurement
   difference); and whether race_simulator needs a resilience layer
   independent of classifier calibration (see §4's options, not
   pre-decided). Be explicit about which parts are real ML/retraining work
   versus code changes, and give an honest effort estimate for each.
3. Present that plan and wait for approval before implementing anything —
   same checkpoint-based convention as this project's other rebuild
   sessions (propose, get approval, implement checkpoint by checkpoint,
   report + verify against real data + wait between each). Expect this to
   span multiple checkpoints and very possibly multiple sessions.

Use the exact real test case in this document (LEC, Belgian GP 2026 R10,
lap 15, current_tyre_age 15, the 3-scenario compare: pit now/in 3/in 6 laps)
as your concrete before/after acceptance check once you have a fix
candidate — re-run the identical request and compare the new
position_gain_loss/fresh_tyre_gain_per_lap/rival_projected_pit_lap values
against the "before" numbers recorded in §1 and §2c's tables.

Do not run git commands unless explicitly asked.
```
