# Tire Degradation Model Quality & Rival Pit Behavior in the Monte Carlo Simulator

> **Status: investigation only, NOT fixed.** Surfaced 2026-09-07 running a
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
