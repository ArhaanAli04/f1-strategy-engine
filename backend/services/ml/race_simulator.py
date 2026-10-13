"""Monte Carlo race outcome simulator.

Combines the three Day 7 models (tire degradation, pit predictor, safety car) into a
forward simulation from the current lap to the chequered flag. Unlike tire_deg_model.py
and pit_predictor.py, this module deliberately imports across services/ml — it is the
live-inference analogue of scripts/train_models.py's orchestrator role, which is the one
place those modules are already combined.

Numba can only JIT-compile pure numeric code — it cannot call into sklearn/XGBoost/
LightGBM objects. So the simulation is split in two layers per remaining lap:
  1. Plain Python/NumPy: batch-call the three ML models ONCE per lap across the full
     (n_simulations x n_drivers) state matrix (not once per simulation).
  2. A @numba.njit inner function: given those per-lap model outputs, apply Gaussian
     noise, tyre age increments, pit-stop time, and safety-car bunching across all
     (sim, driver) pairs in compiled code. This is the "inner loop" the numba JIT
     compiles — the ML inference itself cannot be.

Several assumptions were required to bridge Day 7's models (trained for historical
batch evaluation) into a forward simulation that has no ground-truth lap times yet:

- circuit_id_encoded/driver_id_encoded/compound_encoded are treated as pre-encoded
  caller-supplied inputs (RaceSimulationInput), the same contract
  tire_deg_model.predict_life_remaining_batch already uses. This module does not
  reproduce train_models.py's pd.Categorical encoding itself.
- tire_deg_model's `fuel_load_penalty` feature is built here by
  tire_deg_model.fuel_load_penalty_seconds — the exact function training uses.
  Before 2026-09-09 the feature was `fuel_adjusted_time`, which embedded the
  actual lap time (the thing a forward simulation is trying to predict), so
  this module substituted the fuel term alone and fed the model values ~7-15
  standard deviations outside its training distribution. This module's own
  docstring used to call that "acceptable since compound/tyre_age dominate the
  model's splits" — measured feature importances showed the opposite
  (fuel_adjusted_time 0.20-0.30 vs tyre_age_laps 0.07-0.10 on the dry
  compounds), and the resulting predictions were worse than a constant. See
  docs/internal/tire-deg-model-quality-and-rival-pit-behavior.md.
- cumulative_race_time_seconds accumulates real elapsed race time (input, carrying
  today's actual gaps) plus, for each simulated lap, that driver's own
  baseline_lap_time_seconds (their real median lap time through current_lap —
  see DriverRaceState), the tire_deg model's predicted lap_time_delta, a
  fuel-trend term (below), and noise. lap_time_delta is now a deviation from
  the driver's own FUEL-CORRECTED session median (tire_deg_model.
  add_engineered_features), so it deliberately excludes the fuel effect —
  baseline + delta alone would hold the car at its current weight for the rest
  of the race. simulate_race therefore adds back the change in fuel load
  between the lap being simulated and the (raw, uncorrected) baseline's own
  reference point, so cumulative_race_time_seconds stays a real elapsed-time
  estimate and predicted_finish_time keeps the accuracy validated on
  2026-09-03. A caller that omits baseline_lap_time_seconds (defaults to 0.0)
  gets the relative-delta-only behaviour for that driver, and no fuel term is
  applied to it — there is no absolute lap time to correct.
- After a pit stop tyre age resets to 0, and a car the simulation stops on its own
  fits the dry compound most cars fitted with that many laps left
  (_compound_after_stop, from 2018-2025 races). A forced stop fits the plan's compound.
  Compounds are tracked per (sim, driver), since a car's own stops differ between
  runs. Before Day 6d (2026-10-11) the compound never changed after an unplanned
  stop, so a rival ran its old compound's wear curve from a fresh start.
- A safety car lap "neutralises gaps" by collapsing every driver's cumulative time to
  the simulation's current leader time plus an SC lap time — the exact value is
  inconsequential to relative standings since it's applied uniformly, but now that
  cumulative_race_time_seconds is a real absolute time (see above), that SC lap time
  itself must also be a plausible absolute lap time, not a small fixed constant far
  below real race pace (which would make an SC lap shorten the simulated race).
  simulate_race derives it from the field's own median baseline_lap_time_seconds
  (SC_LAP_TIME_MULTIPLIER) when at least one driver has a real baseline, falling back
  to the fixed SC_LAP_TIME_SECONDS constant only when none do (e.g. a caller that
  never supplies baseline_lap_time_seconds — see DriverRaceState).
- LAP_TIME_NOISE_STD_SECONDS is an assumed lap-to-lap variability constant (no
  computed historical variance exists in the codebase yet), in the same spirit as
  tire_deg_model.ASSUMED_START_FUEL_KG.
- MIN_LAPS_BETWEEN_PITS guards against the pit_predictor's per-lap threshold decision
  triggering unrealistic back-to-back pit stops near the threshold boundary.
- The pit model doesn't know how many stops a car has made. A car that has met F1's
  two-compound rule (DriverRaceState.two_compound_rule_met, or any stop in the
  simulation, since a stop fits a different compound in practice) only stops on its
  own when its tyre won't last to the flag (_autonomous_pit_flags). Before Day 6d
  (2026-10-11) the model stopped cars that had already made their stop again: from
  Belgian GP 2026 lap 24 OCO, COL, GAS, ANT and SAI stopped in every run, though all
  five ran to the flag.
- The pit model answers "stop within the next 3 laps", so a car it decides to stop
  stops on a lap drawn within that window, per run (_schedule_own_stops). Before
  Day 6d it stopped on the deciding lap, early and on the same lap in every run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numba
import numpy as np
import numpy.typing as npt

from backend.core.metrics import f1_ml_inference_duration_seconds
from backend.services.ml import pit_predictor, safety_car_model, tire_deg_model

logger = logging.getLogger(__name__)

N_SIMULATIONS = 1000
PIT_STOP_SECONDS = 22.0
LAP_TIME_NOISE_STD_SECONDS = 0.35
# Fallback SC lap time when no driver in the field has a real
# baseline_lap_time_seconds (see DriverRaceState / SC_LAP_TIME_MULTIPLIER
# below) — kept as the pre-existing constant for that degraded case only.
SC_LAP_TIME_SECONDS = 25.0
# An SC lap is slower than green-flag pace (bunched field, delta neutralised)
# but still a real, plausible absolute lap time — applied to the field's own
# median baseline_lap_time_seconds when at least one driver has one. 1.4 is
# an assumed multiplier (no computed historical SC-lap-time data exists in
# this codebase), same spirit as LAP_TIME_NOISE_STD_SECONDS.
SC_LAP_TIME_MULTIPLIER = 1.4
# Spacing between consecutive cars once a safety car has bunched the field.
# Assumed, like SC_LAP_TIME_MULTIPLIER: cars queue roughly a second apart
# behind the safety car. It keeps the running order through an SC lap; before
# 2026-10-07 every car was given the same time, so the order after an SC lap
# came down to tie-breaking (found when a P9 driver finished P22 in one run).
SC_BUNCHED_GAP_SECONDS = 1.0
MIN_LAPS_BETWEEN_PITS = 5
# A stop under a safety car costs this share of PIT_STOP_SECONDS: the field is
# slow, so the car loses less to it in the pit lane. Assumed (no pit-loss
# measurements exist in the data); the pit-window engine uses the same value
# (strategy_service.pit_loss_after_lap). Applied here since Day 6d (2026-10-11),
# when a stop on a simulated safety-car lap still cost the full 22 s.
SC_PIT_LOSS_FACTOR = 0.5
# The integer codes the tire_deg models were trained with (train_models.py's
# pd.Categorical over the compound names, alphabetical). prediction_worker and
# strategy_service use this table too.
COMPOUND_ENCODING = {"HARD": 0, "INTERMEDIATE": 1, "MEDIUM": 2, "SOFT": 3, "WET": 4}
_DRY_COMPOUND_CODES = (
    COMPOUND_ENCODING["SOFT"],
    COMPOUND_ENCODING["MEDIUM"],
    COMPOUND_ENCODING["HARD"],
)
# What a car fits at a stop with this many laps still to run after it, first
# choice then the one used when the first is its current compound and it still
# needs a second dry compound. Most common fitted compounds at 3,917 stops in
# dry races 2018-2025 (lap_data, measured 2026-10-11): 25+ laps left HARD
# 57-62% (MEDIUM 30-32%); 15-24 MEDIUM 35%, SOFT 33%, HARD 32%; under 15 SOFT
# 58-93%.
_COMPOUND_BY_LAPS_LEFT = (
    (25, COMPOUND_ENCODING["HARD"], COMPOUND_ENCODING["MEDIUM"]),
    (15, COMPOUND_ENCODING["MEDIUM"], COMPOUND_ENCODING["HARD"]),
    (0, COMPOUND_ENCODING["SOFT"], COMPOUND_ENCODING["MEDIUM"]),
)


@dataclass(frozen=True)
class DriverRaceState:
    """One driver's state at the lap the simulation starts from.

    compound_encoded/driver_id_encoded must match the integer codes the tire_deg
    pipeline for `compound` was trained on (see scripts/train_models.py's
    _encode_categoricals) — this module treats them as pre-encoded inputs supplied
    by the caller, not something it derives itself.
    """

    driver_id: str
    starting_position: int
    compound: str
    compound_encoded: int
    tyre_age_laps: int
    driver_id_encoded: int
    cumulative_race_time_seconds: float = 0.0
    # This driver's own real median lap time (seconds) through current_lap —
    # see prediction_worker._build_race_state, which takes a per-driver median
    # bounded to laps <= current_lap, since a forward simulation can't see the
    # session's full median. Added to every simulated lap's predicted delta so
    # cumulative_race_time_seconds accumulates a real absolute time (see this
    # module's docstring) instead of only the small delta-from-median.
    #
    # Deliberately a RAW median, NOT the fuel-corrected one
    # tire_deg_model.add_engineered_features builds its target against: this is
    # an absolute lap time to anchor elapsed race time to, so it should carry
    # the real fuel penalty of the laps it was measured over. simulate_race
    # accounts for the difference explicitly (see baseline_fuel_penalty there)
    # rather than by silently redefining this field.
    #
    # Default 0.0: a caller that omits this (e.g. an older/synthetic test
    # fixture) gets the pre-existing relative-delta-only behaviour for that
    # driver, unchanged.
    baseline_lap_time_seconds: float = 0.0
    # True once the driver has run two dry compounds, or a wet one, by the
    # start lap (strategy_service.dry_compound_obligation is None). Such a car
    # stops on its own only when its tyre won't last (see the module
    # docstring). Default False: the pit model alone decides, as before.
    two_compound_rule_met: bool = False


@dataclass(frozen=True)
class RaceSimulationInput:
    circuit_name: str
    circuit_id_encoded: int
    current_lap: int
    total_laps: int
    wet_track: bool
    # Held constant for the whole simulated remainder of the race — same
    # rationale as strategy_service.py's _project_stint_delta: weather drift
    # over a race's remaining laps is a second-order effect next to tyre wear.
    # Caller-supplied (e.g. via strategy_service._resolve_weather), same
    # contract as circuit_id_encoded/compound_encoded above.
    track_temp: float
    air_temp: float
    drivers: list[DriverRaceState] = field(default_factory=list)


@dataclass(frozen=True)
class DriverPositionDistribution:
    driver_id: str
    position_probabilities: dict[int, float]
    mean_position: float
    # Sourced from the same cumulative_time array simulate_race already
    # computes for ranking — captured before it goes out of scope, not a
    # second pass. Needed because callers (e.g. the /strategy/simulate
    # endpoint) report an actual predicted finish time, not just position.
    mean_finish_time_seconds: float
    finish_time_p5_seconds: float
    finish_time_p95_seconds: float
    # Both added for the What-If Simulator rebuild part (b) — see
    # docs/internal/core-feature-rebuild-whatif-simulator.md §7 — so a caller can
    # build a plan-explanation narrative from what the simulation itself
    # actually did, instead of a static frozen-gap heuristic. Both default
    # empty so every pre-existing DriverPositionDistribution(...) call site
    # (real code and test fixtures) that doesn't pass them keeps working
    # unchanged.
    #
    # projected_pit_laps: this driver's OWN per-lap pit probability across
    # all simulations — (lap_number, probability) pairs, sparse (probability
    # > 0.0 only) and sorted by lap ascending, same convention as
    # position_probabilities/_shape_position_probabilities. A forced what-if
    # pit lap (race_simulator.simulate_race's forced_pit_laps) shows
    # probability 1.0 at that lap for the requesting driver — it isn't a
    # special case, just what pit_flags already reflects every lap.
    projected_pit_laps: list[tuple[int, float]] = field(default_factory=list)
    # finish_ahead_probability: P(this driver finishes ahead of each OTHER
    # driver), keyed by that other driver's driver_id — one entry per other
    # driver in the field, from the same final cumulative_time array
    # position_probabilities is built from. Two entries for the same pair of
    # drivers are complementary by construction (P(i ahead of j) + P(j ahead
    # of i) == 1.0, modulo the rare exact-tie sims described in
    # simulate_race's own comment on this computation) — not independently
    # estimated, so they always agree with mean_position's own ranking.
    finish_ahead_probability: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class RaceSimulationResult:
    n_simulations: int
    driver_distributions: list[DriverPositionDistribution]


@numba.njit(cache=True)
def _advance_lap(
    cumulative_time: npt.NDArray[np.float64],
    tyre_age: npt.NDArray[np.int64],
    predicted_delta: npt.NDArray[np.float64],
    baseline_lap_time: npt.NDArray[np.float64],
    fuel_trend_seconds: npt.NDArray[np.float64],
    noise_std: float,
    pit_flags: npt.NDArray[np.bool_],
    pit_stop_seconds: float,
    sc_active: npt.NDArray[np.bool_],
    sc_lap_time_seconds: float,
) -> None:
    """Numba-compiled inner loop: advance every (simulation, driver) pair by one lap.

    Mutates cumulative_time and tyre_age in place. On a safety car lap, the field
    in that simulation is bunched behind its current leader in its current order,
    SC_BUNCHED_GAP_SECONDS apart (gaps neutralised, order kept), instead of each
    driver receiving their individually predicted pace. Pit stops
    add pit_stop_seconds (SC_PIT_LOSS_FACTOR of it on a safety-car lap) and reset
    tyre age to 0, applied after the lap's time/age update on both branches.

    Args:
        cumulative_time: (n_sims, n_drivers) elapsed race time in seconds, mutated.
        tyre_age: (n_sims, n_drivers) laps on current tyre, mutated.
        predicted_delta: (n_sims, n_drivers) tire_deg-model-predicted lap time delta.
        baseline_lap_time: (n_drivers,) each driver's own real median lap time
            (DriverRaceState.baseline_lap_time_seconds) — added on top of
            predicted_delta on a racing lap so cumulative_time accumulates a real
            absolute lap time, not just the delta. Not applied on an SC lap: the
            caller's sc_lap_time_seconds is already a real absolute lap time in
            its own right (see this function's caller).
        fuel_trend_seconds: (n_drivers,) this lap's fuel-load correction for
            each driver — how much lighter (negative) or heavier (positive) the
            car is on this lap than at the reference point baseline_lap_time was
            measured over. Needed because predicted_delta is fuel-corrected and
            therefore carries no fuel trend of its own (see module docstring).
            Zero for a driver with no real baseline, and not applied on an SC
            lap, for the same reason baseline_lap_time isn't.
        noise_std: Standard deviation of the per-lap Gaussian noise term.
        pit_flags: (n_sims, n_drivers) whether this driver pits this lap.
        pit_stop_seconds: Fixed pit stop time loss.
        sc_active: (n_sims,) whether a safety car is active this lap, per simulation.
        sc_lap_time_seconds: Uniform lap time added to every driver during an SC lap.
    Returns:
        None (in-place mutation).
    """
    n_sims, n_drivers = cumulative_time.shape
    for s in range(n_sims):
        if sc_active[s]:
            order = np.argsort(cumulative_time[s])
            sc_time = cumulative_time[s, order[0]] + sc_lap_time_seconds
            for rank in range(n_drivers):
                d = order[rank]
                cumulative_time[s, d] = sc_time + rank * SC_BUNCHED_GAP_SECONDS
                tyre_age[s, d] += 1
                if pit_flags[s, d]:
                    cumulative_time[s, d] += pit_stop_seconds * SC_PIT_LOSS_FACTOR
                    tyre_age[s, d] = 0
        else:
            for d in range(n_drivers):
                noise = np.random.normal(0.0, noise_std)
                cumulative_time[s, d] += (
                    baseline_lap_time[d] + predicted_delta[s, d] + fuel_trend_seconds[d] + noise
                )
                tyre_age[s, d] += 1
                if pit_flags[s, d]:
                    cumulative_time[s, d] += pit_stop_seconds
                    tyre_age[s, d] = 0


@numba.njit(cache=True)
def _seed_numba_rng(seed: int) -> None:
    np.random.seed(seed)


def _tire_deg_predictions(
    race_state: RaceSimulationInput,
    compound_code: npt.NDArray[np.int64],
    compound_names: dict[int, str],
    tire_deg_pipelines: dict[str, Any],
    lap_number: int,
    tyre_age: npt.NDArray[np.int64],
    driver_id_encoded: npt.NDArray[np.int64],
    fuel_load_penalty: float,
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Batch tire_deg predictions (delta + life remaining) for every (sim, driver) pair.

    race_state.track_temp/air_temp are intentionally not read here — as of
    2026-07-16 tire_deg_model.FEATURE_COLUMNS is back to 6 columns (see that
    module's docstring), matching the deployed production models pending a
    weather-aware retrain+promotion.

    Args:
        race_state: The simulation's static race context.
        compound_code: (n_sims, n_drivers) encoded compound each car is on, per run.
            Starts from race_state.drivers[i].compound_encoded and changes at stops
            (see simulate_race), so the same car can be on different compounds in
            different runs.
        compound_names: encoded compound -> name, to pick the pipeline.
        tire_deg_pipelines: Fitted tire_deg pipelines, keyed by compound.
        lap_number: Lap being predicted for.
        tyre_age: (n_sims, n_drivers) current tyre age.
        driver_id_encoded: (n_drivers,) per-driver encoded id.
        fuel_load_penalty: This lap's fuel_load_penalty feature value, from
            tire_deg_model.fuel_load_penalty_seconds (see module docstring).
    Returns:
        (predicted_delta, predicted_life_remaining), each (n_sims, n_drivers). Drivers
        on a compound with no fitted pipeline, a pipeline whose fitted feature count
        doesn't match the built feature vector (schema drift — see
        docs/internal/simulator-issues-wet-model-and-position-context.md; strategy_service.py/
        prediction_worker.py's _load_models already alias the one known offender,
        tire_deg_wet.pkl, before this is ever reached, but this is a permanent
        backstop against future drift in any compound), or a pipeline whose predict()
        raises, get delta=0 and life_remaining capped at
        tire_deg_model.MAX_LOOKAHEAD_LAPS — degrading that compound group only, never
        crashing the whole Monte Carlo task.

    Memoization (added for the What-If Simulator multi-scenario rebuild —
    see docs/internal/core-feature-rebuild-whatif-simulator.md): within one compound
    group at one lap, lap_number/fuel_adjusted_time/circuit_id_encoded are
    scalars shared by every row, and compound_encoded/driver_id_encoded vary
    only per DRIVER (identical across all n_sims copies of that driver) —
    the only column that genuinely varies per (sim, driver) pair is
    tyre_age. So most of a group's (n_sims * n_group_drivers) rows are exact
    duplicates. Measured on a real 22-driver mid-race profile (Belgian GP
    2026 R10, lap 21, 1000 sims): 528,000 rows collapsed to 528 unique
    (tyre_age, driver_id_encoded, compound_encoded) combinations — a 1000x
    ratio that took a single simulate_race call from ~53s to ~14s with
    bit-identical output (both predict() and predict_life_remaining_batch()
    are deterministic, stateless functions of their input row, so predicting
    once per unique row and scattering the result back via
    np.unique(..., return_inverse=True) is exact, not an approximation —
    confirmed in test_tire_deg_predictions_dedup_matches_naive_predictions).
    The dedup ratio is data-dependent (lower once pit decisions/forced
    what-ifs diverge sims' tyre ages from each other), but the worst case is
    still bounded by the number of distinct tyre ages actually reachable at
    a given lap, far below n_sims in every realistic race length.
    """
    n_sims, n_drivers = tyre_age.shape
    predicted_delta = np.zeros((n_sims, n_drivers))
    predicted_life_remaining = np.full(
        (n_sims, n_drivers), float(tire_deg_model.MAX_LOOKAHEAD_LAPS)
    )

    for code in np.unique(compound_code):
        compound = compound_names.get(int(code), "")
        pipeline = tire_deg_pipelines.get(compound)
        if pipeline is None:
            continue

        # Every (sim, driver) cell on this compound this lap, in row-major
        # order (boolean indexing and np.nonzero agree on it).
        cells = compound_code == code
        _, driver_index = np.nonzero(cells)
        # Settled ages (tire_deg_model.SETTLED_TYRE_AGE): the models guess wildly
        # on a fresh tyre's first laps (+5.2 s for a HARD at age 1), which the
        # pit-window engine stopped feeding them in Day 6c. Since Day 6d
        # (2026-10-11) the simulator does too. The pit model still gets the raw
        # age (_pit_scores), as in its training.
        tyre_age_flat = tire_deg_model.settled_tyre_ages(tyre_age[cells]).astype(np.int64)
        compound_encoded_flat = np.full(tyre_age_flat.shape[0], int(code), dtype=np.int64)
        driver_id_encoded_flat = driver_id_encoded[driver_index]

        # Dedup key: tyre_age is the only column that varies per (sim,
        # driver) row; compound_encoded/driver_id_encoded are included too
        # (not assumed constant within the group) so this stays correct even
        # if a future change makes either vary per-simulation — it would just
        # shrink the dedup win, never produce a wrong result.
        dedup_key = np.stack([tyre_age_flat, driver_id_encoded_flat, compound_encoded_flat], axis=1)
        unique_rows, inverse = np.unique(dedup_key, axis=0, return_inverse=True)
        unique_tyre_age = unique_rows[:, 0]
        unique_driver_id_encoded = unique_rows[:, 1]
        unique_compound_encoded = unique_rows[:, 2]
        n_unique = unique_rows.shape[0]

        lap_number_arr = np.full(n_unique, lap_number, dtype=np.int64)
        fuel_load_penalty_arr = np.full(n_unique, fuel_load_penalty)
        circuit_id_encoded_arr = np.full(n_unique, race_state.circuit_id_encoded, dtype=np.int64)

        features = np.column_stack(
            [
                lap_number_arr.astype(np.float64),
                unique_compound_encoded.astype(np.float64),
                unique_tyre_age.astype(np.float64),
                fuel_load_penalty_arr,
                circuit_id_encoded_arr.astype(np.float64),
                unique_driver_id_encoded.astype(np.float64),
            ]
        )

        # Permanent backstop against model-shape drift (see this function's
        # docstring): _load_models() already aliases the one known offender
        # (tire_deg_wet.pkl) before this is ever reached, but a future
        # promotion-guard gap on ANY compound must degrade this compound
        # group only, never crash the whole Monte Carlo task.
        expected_n_features = features.shape[1]
        actual_n_features = tire_deg_model.pipeline_feature_count(pipeline)
        if actual_n_features is not None and actual_n_features != expected_n_features:
            logger.warning(
                "tire_deg pipeline for compound %s expects %d features, got %d — "
                "skipping this compound group for lap %d (delta=0, life=MAX_LOOKAHEAD_LAPS)",
                compound,
                actual_n_features,
                expected_n_features,
                lap_number,
            )
            continue

        try:
            with f1_ml_inference_duration_seconds.labels(model="tire_deg").time():
                unique_delta = pipeline.predict(features)

                unique_life = tire_deg_model.predict_life_remaining_batch(
                    pipeline,
                    lap_number_arr,
                    unique_compound_encoded,
                    unique_tyre_age,
                    fuel_load_penalty_arr,
                    circuit_id_encoded_arr,
                    unique_driver_id_encoded,
                )
            # Scatter each unique row's prediction back to every (sim,
            # driver) position that shared its (tyre_age, driver_id_encoded,
            # compound_encoded) combination — exact, see docstring above.
            predicted_delta[cells] = unique_delta[inverse.ravel()]
            predicted_life_remaining[cells] = unique_life[inverse.ravel()]
        except Exception:  # noqa: BLE001 — degrade this compound group, never crash the task
            logger.warning(
                "tire_deg inference failed for compound %s at lap %d — "
                "skipping this compound group (delta=0, life=MAX_LOOKAHEAD_LAPS)",
                compound,
                lap_number,
                exc_info=True,
            )
            predicted_delta[cells] = 0.0
            predicted_life_remaining[cells] = float(tire_deg_model.MAX_LOOKAHEAD_LAPS)

    return predicted_delta, predicted_life_remaining


def _positions_and_gaps(
    cumulative_time: npt.NDArray[np.float64],
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.int64]]:
    """Rank drivers within each simulation by cumulative race time.

    Args:
        cumulative_time: (n_sims, n_drivers) elapsed race time so far.
    Returns:
        (gap_to_ahead, gap_to_behind, position), each (n_sims, n_drivers). Gaps are
        capped at pit_predictor.MAX_GAP_SECONDS and default to that cap for the
        leader/last car — mirrors pit_predictor.add_gap_features's convention.
        position is 1-indexed.
    """
    n_sims, n_drivers = cumulative_time.shape
    order = np.argsort(cumulative_time, axis=1)
    ranked_time = np.take_along_axis(cumulative_time, order, axis=1)

    gap_ahead_sorted = np.full((n_sims, n_drivers), pit_predictor.MAX_GAP_SECONDS)
    gap_behind_sorted = np.full((n_sims, n_drivers), pit_predictor.MAX_GAP_SECONDS)
    diffs = np.clip(np.diff(ranked_time, axis=1), 0, pit_predictor.MAX_GAP_SECONDS)
    gap_ahead_sorted[:, 1:] = diffs
    gap_behind_sorted[:, :-1] = diffs

    inv_order = np.argsort(order, axis=1)
    gap_to_ahead = np.take_along_axis(gap_ahead_sorted, inv_order, axis=1)
    gap_to_behind = np.take_along_axis(gap_behind_sorted, inv_order, axis=1)
    rank_positions = np.broadcast_to(np.arange(1, n_drivers + 1), (n_sims, n_drivers))
    position = np.take_along_axis(rank_positions, inv_order, axis=1).astype(np.int64)

    return gap_to_ahead, gap_to_behind, position


def _pit_scores(
    pit_model: Any,
    tyre_age: npt.NDArray[np.int64],
    predicted_life_remaining: npt.NDArray[np.float64],
    gap_to_ahead: npt.NDArray[np.float64],
    gap_to_behind: npt.NDArray[np.float64],
    safety_car_probability: float,
    laps_to_race_end: int,
    position: npt.NDArray[np.int64],
    fuel_load_est: float,
) -> npt.NDArray[np.float64]:
    """Batch pit_predictor probabilities for every (sim, driver) pair, one lap.

    Args:
        pit_model: Fitted pit_predictor.LGBMClassifier.
        tyre_age, predicted_life_remaining, gap_to_ahead, gap_to_behind, position:
            (n_sims, n_drivers) per-pair features.
        safety_car_probability, laps_to_race_end, fuel_load_est: Scalars, identical
            for every (sim, driver) pair on a given lap.
    Returns:
        (n_sims, n_drivers) pit probability, matching pit_predictor.FEATURE_COLUMNS order.
    """
    n_sims, n_drivers = tyre_age.shape
    n = n_sims * n_drivers
    features = np.column_stack(
        [
            tyre_age.ravel().astype(np.float64),
            predicted_life_remaining.ravel(),
            gap_to_ahead.ravel(),
            gap_to_behind.ravel(),
            np.full(n, safety_car_probability),
            np.full(n, float(laps_to_race_end)),
            position.ravel().astype(np.float64),
            np.full(n, fuel_load_est),
        ]
    )
    with f1_ml_inference_duration_seconds.labels(model="pit_predictor").time():
        scores: npt.NDArray[np.float64] = pit_model.predict_proba(features)[:, 1].reshape(
            n_sims, n_drivers
        )
    return scores


def _autonomous_pit_flags(
    pit_scores: npt.NDArray[np.float64],
    tyre_age: npt.NDArray[np.int64],
    rule_met: npt.NDArray[np.bool_],
    predicted_life_remaining: npt.NDArray[np.float64],
    laps_to_race_end: int,
) -> npt.NDArray[np.bool_]:
    """Which (sim, driver) pairs the pit model stops this lap, before any forced plan.

    Args:
        pit_scores: (n_sims, n_drivers) pit_predictor probabilities.
        tyre_age: (n_sims, n_drivers) laps on the current tyre.
        rule_met: (n_sims, n_drivers) whether the car has met the two-compound
            rule (from the start lap, or by a simulated stop).
        predicted_life_remaining: (n_sims, n_drivers) laps until the tyre's
            predicted delta crosses tire_deg_model's degradation threshold,
            capped at tire_deg_model.MAX_LOOKAHEAD_LAPS: a tyre at the cap
            counts as lasting however many laps are left.
        laps_to_race_end: Laps still to run after this one.
    Returns:
        (n_sims, n_drivers) bool: the pit model's stops, with a car that has met
        the rule kept out unless its tyre won't last to the flag.
    """
    stops: npt.NDArray[np.bool_] = (pit_scores > pit_predictor.ALERT_THRESHOLD) & (
        tyre_age >= MIN_LAPS_BETWEEN_PITS
    )
    tyre_lasts = predicted_life_remaining >= min(
        laps_to_race_end, tire_deg_model.MAX_LOOKAHEAD_LAPS
    )
    return stops & ~(rule_met & tyre_lasts)


def _schedule_own_stops(
    decided: npt.NDArray[np.bool_],
    planned_stop_lap: npt.NDArray[np.int64],
    delay: npt.NDArray[np.int64],
    lap_number: int,
    laps_to_race_end: int,
) -> tuple[npt.NDArray[np.bool_], npt.NDArray[np.int64]]:
    """Turn the pit model's decisions into stops within its 3-lap window.

    pit_predictor's label is "stops within PIT_LABEL_HORIZON_LAPS laps,
    this one included", so a decision on lap t schedules the stop on lap
    t + delay, delay drawn per (sim, driver) in [0, horizon). Before Day 6d
    (2026-10-11) the stop came on lap t itself, which pulled every simulated
    stop early and made all runs agree on one lap (NOR, Belgian GP 2026 lap 25:
    lap 26 in 100% of runs; he stopped on lap 31). A decision while a stop is
    already scheduled is ignored.

    Args:
        decided: (n_sims, n_drivers) the model's stop decisions this lap
            (_autonomous_pit_flags), forced drivers excluded by the caller.
        planned_stop_lap: (n_sims, n_drivers) the lap a scheduled stop falls
            on, 0 for none.
        delay: (n_sims, n_drivers) drawn delays, each in [0, horizon).
        lap_number: This lap.
        laps_to_race_end: Laps still to run after this one; a delay is capped
            so the stop falls by the last lap.
    Returns:
        (stops this lap, the updated planned_stop_lap with those stops cleared).
    """
    new = decided & (planned_stop_lap == 0)
    capped = np.minimum(delay, laps_to_race_end)
    planned = np.where(new, lap_number + capped, planned_stop_lap)
    stops: npt.NDArray[np.bool_] = planned == lap_number
    remaining: npt.NDArray[np.int64] = np.where(stops, 0, planned).astype(np.int64)
    return stops, remaining


def dry_compound_for_stop(current: str, rule_met: bool, laps_left: int) -> str:
    """The dry compound most cars fit at a stop with this many laps left after it.

    One car's case of _compound_after_stop, by name. The pit-window engine
    (strategy_service.compute_pit_recommendation) uses it too, since Day 6d
    (2026-10-11): the tyre models misjudge the compounds' pace against each
    other, so their own choice rarely matched the real one.

    Args:
        current: The compound the car is on.
        rule_met: Whether the car has met the two-compound rule (or needs no
            dry change, e.g. after running a wet compound).
        laps_left: Laps still to run after the stop.
    Returns:
        "SOFT", "MEDIUM" or "HARD": the table's first choice, or its second
        when the first is the car's compound and it still needs a second dry one.
    """
    _, first, second = next(row for row in _COMPOUND_BY_LAPS_LEFT if laps_left >= row[0])
    names = {code: name for name, code in COMPOUND_ENCODING.items()}
    code = second if names[first] == current and not rule_met else first
    return names[code]


def _compound_after_stop(
    compound_code: npt.NDArray[np.int64],
    rule_met: npt.NDArray[np.bool_],
    laps_left: int,
) -> npt.NDArray[np.int64]:
    """The compound a car fits when the simulation stops it on its own.

    Args:
        compound_code: Encoded compound each car is on (any shape).
        rule_met: Whether each car has met the two-compound rule (same shape).
        laps_left: Laps still to run after the stop.
    Returns:
        The encoded compound to fit, same shape. A car on a wet compound keeps
        it: nothing here models the track drying or getting wetter.
    """
    _, first, second = next(row for row in _COMPOUND_BY_LAPS_LEFT if laps_left >= row[0])
    fitted = np.where((compound_code == first) & ~rule_met, second, first)
    is_dry = np.isin(compound_code, _DRY_COMPOUND_CODES)
    result: npt.NDArray[np.int64] = np.where(is_dry, fitted, compound_code).astype(np.int64)
    return result


def simulate_race(
    race_state: RaceSimulationInput,
    tire_deg_pipelines: dict[str, Any],
    pit_model: Any,
    sc_model: safety_car_model.SafetyCarModel,
    n_simulations: int = N_SIMULATIONS,
    rng_seed: int | None = None,
    forced_pit_laps: dict[str, dict[int, tuple[str, int]]] | None = None,
) -> RaceSimulationResult:
    """Monte Carlo race outcome simulation from the current lap to the chequered flag.

    Args:
        race_state: Current race snapshot (lap, per-driver compound/tyre age/gap state).
        tire_deg_pipelines: Fitted tire degradation pipelines, keyed by compound
            (e.g. "SOFT" -> the fitted Pipeline from tire_deg_model.train_tire_degradation_model).
        pit_model: Fitted pit_predictor.LGBMClassifier.
        sc_model: Fitted safety_car_model.SafetyCarModel.
        n_simulations: Number of Monte Carlo simulations to run.
        rng_seed: Optional seed for reproducibility (seeds both the Python-level RNG
            used for safety car sampling and the Numba-level RNG used for lap noise).
        forced_pit_laps: Optional what-if override, driver_id -> {lap_number:
            (compound_name, compound_encoded)}. A driver listed here stops on
            exactly those laps and no others: the plan is the whole plan
            (owner's decision, Day 6d, 2026-10-11; before it the pit model could
            add stops of its own), and an empty schedule means no stop at all.
            A forced lap bypasses MIN_LAPS_BETWEEN_PITS too, since an explicit
            what-if lap should be honored even if unrealistically soon, and the
            compound switches to the given one from the following lap onward
            (pipeline selection and the encoded feature both change). A stop
            the simulation makes on its own fits _compound_after_stop's
            compound. None or empty: every driver's pit timing is model-driven.
    Returns:
        RaceSimulationResult with a finishing-position probability distribution
        and finish-time distribution per driver.
    """
    n_drivers = len(race_state.drivers)
    if n_drivers == 0:
        return RaceSimulationResult(n_simulations=n_simulations, driver_distributions=[])

    rng = np.random.default_rng(rng_seed)
    if rng_seed is not None:
        _seed_numba_rng(rng_seed)

    cumulative_time = np.tile(
        np.array([d.cumulative_race_time_seconds for d in race_state.drivers], dtype=np.float64),
        (n_simulations, 1),
    )
    tyre_age = np.tile(
        np.array([d.tyre_age_laps for d in race_state.drivers], dtype=np.int64),
        (n_simulations, 1),
    )
    driver_id_encoded = np.array([d.driver_id_encoded for d in race_state.drivers], dtype=np.int64)
    # Static for the whole simulation — a driver's own real median pace does
    # not change mid-race (unlike compound/tyre age, which forced_pit_laps
    # can mutate). See DriverRaceState.baseline_lap_time_seconds.
    baseline_lap_time = np.array(
        [d.baseline_lap_time_seconds for d in race_state.drivers], dtype=np.float64
    )
    # SC lap time must itself be a real absolute lap time now that
    # cumulative_time accumulates one (see module docstring) — derived from
    # the field's own median baseline when at least one driver has a real,
    # nonzero baseline; falls back to the fixed constant only when none do
    # (e.g. every DriverRaceState omitted baseline_lap_time_seconds).
    nonzero_baselines = baseline_lap_time[baseline_lap_time > 0]
    sc_lap_time_seconds = (
        float(np.median(nonzero_baselines)) * SC_LAP_TIME_MULTIPLIER
        if nonzero_baselines.size > 0
        else SC_LAP_TIME_SECONDS
    )
    # baseline_lap_time is a RAW (uncorrected) median over laps 1..current_lap,
    # so it already embeds the average fuel penalty over that window — best
    # approximated at its midpoint. Each simulated lap's fuel term below is
    # measured relative to this reference, so a driver's simulated pace picks
    # up the fuel gain from here to the flag rather than double-counting the
    # portion the baseline already contains. An assumed midpoint is in the same
    # spirit as this module's other documented approximations
    # (ASSUMED_START_FUEL_KG, SC_LAP_TIME_MULTIPLIER).
    baseline_fuel_penalty = float(
        tire_deg_model.fuel_load_penalty_seconds(
            max(race_state.current_lap, 1) / 2.0, float(race_state.total_laps)
        )
    )
    # Zero for any driver with no real baseline: there is no absolute lap time
    # to fuel-correct, so that driver keeps the documented relative-delta-only
    # behaviour exactly as before.
    has_baseline = baseline_lap_time > 0

    driver_index_by_id = {d.driver_id: i for i, d in enumerate(race_state.drivers)}
    # Mutable per (sim, driver): the compound changes at stops, and a car's
    # own stops differ between runs (see the module docstring).
    compound_code = np.tile(
        np.array([d.compound_encoded for d in race_state.drivers], dtype=np.int64),
        (n_simulations, 1),
    )
    compound_names = {code: name for name, code in COMPOUND_ENCODING.items()}
    compound_names.update({d.compound_encoded: d.compound for d in race_state.drivers})
    for schedule in (forced_pit_laps or {}).values():
        compound_names.update({code: name for name, code in schedule.values()})
    # Drivers on a forced plan: their stops and compounds come from the plan.
    forced_columns = np.array(
        [d.driver_id in (forced_pit_laps or {}) for d in race_state.drivers], dtype=np.bool_
    )
    # Per-lap, per-driver pit probability across all n_simulations — What-If
    # Simulator rebuild part (b) (see docs/internal/core-feature-rebuild-whatif-
    # simulator.md §7 and DriverPositionDistribution.projected_pit_laps'
    # own docstring). O(n_laps x n_drivers) floats, negligible next to the
    # (n_sims x n_drivers) arrays already held for the whole loop.
    pit_probability_by_lap: dict[int, npt.NDArray[np.float64]] = {}
    # Mutable per (sim, driver): a stop in the simulation meets the rule too.
    rule_met = np.tile(
        np.array([d.two_compound_rule_met for d in race_state.drivers], dtype=np.bool_),
        (n_simulations, 1),
    )
    # The lap each car's scheduled own stop falls on, 0 for none (see
    # _schedule_own_stops).
    planned_stop_lap = np.zeros((n_simulations, n_drivers), dtype=np.int64)

    for lap_number in range(race_state.current_lap + 1, race_state.total_laps + 1):
        fuel_load_penalty = float(
            tire_deg_model.fuel_load_penalty_seconds(lap_number, float(race_state.total_laps))
        )
        fuel_at_lap = tire_deg_model.ASSUMED_START_FUEL_KG * (
            1 - lap_number / race_state.total_laps
        )
        # How much lighter (negative) this lap is than the baseline's own
        # reference point — see baseline_fuel_penalty above.
        fuel_trend_seconds = np.where(has_baseline, fuel_load_penalty - baseline_fuel_penalty, 0.0)

        predicted_delta, predicted_life_remaining = _tire_deg_predictions(
            race_state,
            compound_code,
            compound_names,
            tire_deg_pipelines,
            lap_number,
            tyre_age,
            driver_id_encoded,
            fuel_load_penalty,
        )

        with f1_ml_inference_duration_seconds.labels(model="safety_car").time():
            p_sc = sc_model.probability_within(
                race_state.circuit_name, lap_number, race_state.wet_track, 1
            )
        sc_active = rng.random(n_simulations) < p_sc

        gap_to_ahead, gap_to_behind, position = _positions_and_gaps(cumulative_time)
        laps_to_race_end = race_state.total_laps - lap_number
        fuel_load_est = max(fuel_at_lap, 0.0)

        pit_scores = _pit_scores(
            pit_model,
            tyre_age,
            predicted_life_remaining,
            gap_to_ahead,
            gap_to_behind,
            p_sc,
            laps_to_race_end,
            position,
            fuel_load_est,
        )
        decided = _autonomous_pit_flags(
            pit_scores, tyre_age, rule_met, predicted_life_remaining, laps_to_race_end
        )
        # Drawn for every cell every lap, whatever was decided, so the number
        # of draws per lap is fixed and scenarios sharing a seed stay on the
        # same random numbers (prediction_worker._run_one_scenario).
        delay = rng.integers(0, pit_predictor.PIT_LABEL_HORIZON_LAPS, size=decided.shape)
        pit_flags, planned_stop_lap = _schedule_own_stops(
            decided & ~forced_columns, planned_stop_lap, delay, lap_number, laps_to_race_end
        )

        if forced_pit_laps:
            for forced_driver_id, schedule in forced_pit_laps.items():
                idx = driver_index_by_id.get(forced_driver_id)
                if idx is not None:
                    pit_flags[:, idx] = lap_number in schedule

        # Captured AFTER the forced-pit override above, so a what-if's forced
        # pit lap shows probability 1.0 for the requesting driver at that lap
        # — pit_flags is already what actually fires the pit stop below, this
        # is just recording the same array's per-driver mean across sims,
        # not a separate computation that could disagree with it.
        pit_probability_by_lap[lap_number] = pit_flags.mean(axis=0)

        _advance_lap(
            cumulative_time,
            tyre_age,
            predicted_delta,
            baseline_lap_time,
            fuel_trend_seconds,
            LAP_TIME_NOISE_STD_SECONDS,
            pit_flags,
            PIT_STOP_SECONDS,
            sc_active,
            sc_lap_time_seconds,
        )
        # The new tyres, for the stops just made: the simulation's own choice
        # (taken before rule_met updates, since it depends on whether the car
        # still needed a second compound), then each plan's.
        own_stops = pit_flags & ~forced_columns
        compound_code = np.where(
            own_stops,
            _compound_after_stop(compound_code, rule_met, laps_to_race_end),
            compound_code,
        )
        rule_met |= pit_flags

        if forced_pit_laps:
            for forced_driver_id, schedule in forced_pit_laps.items():
                idx = driver_index_by_id.get(forced_driver_id)
                if idx is None:
                    continue
                forced = schedule.get(lap_number)
                if forced is not None:
                    compound_code[:, idx] = forced[1]

    order = np.argsort(cumulative_time, axis=1)
    finishing_positions = np.argsort(order, axis=1) + 1

    # P(driver i finishes ahead of driver j) for every (i, j) pair, from the
    # same final cumulative_time array position_probabilities is built from
    # — What-If Simulator rebuild part (b) (see DriverPositionDistribution.
    # finish_ahead_probability's own docstring). Lower cumulative_time is
    # better (less elapsed race time), hence "<" not ">". An (n_sims,
    # n_drivers, n_drivers) intermediate boolean array — for a real ~22-car
    # field at n_simulations=1000, that's ~484K booleans, negligible next to
    # this function's existing (n_sims, n_drivers) arrays. A sim where two
    # drivers end on an EXACT tie (e.g. both bunched to the same SC time on
    # the final lap) contributes to neither direction, so the pair's two
    # probabilities can in principle sum to slightly under 1.0 rather than
    # exactly 1.0 — an accurate reflection of a genuine tie, not a bug.
    finish_ahead_matrix = (cumulative_time[:, :, None] < cumulative_time[:, None, :]).mean(axis=0)

    distributions = []
    for i, driver in enumerate(race_state.drivers):
        counts = np.bincount(finishing_positions[:, i], minlength=n_drivers + 1)[1 : n_drivers + 1]
        probabilities = counts / n_simulations
        driver_times = cumulative_time[:, i]
        projected_pit_laps = [
            (lap, float(probs[i]))
            for lap, probs in sorted(pit_probability_by_lap.items())
            if probs[i] > 0.0
        ]
        finish_ahead_probability = {
            other.driver_id: float(finish_ahead_matrix[i, j])
            for j, other in enumerate(race_state.drivers)
            if j != i
        }
        distributions.append(
            DriverPositionDistribution(
                driver_id=driver.driver_id,
                position_probabilities={p + 1: float(probabilities[p]) for p in range(n_drivers)},
                mean_position=float(np.mean(finishing_positions[:, i])),
                mean_finish_time_seconds=float(np.mean(driver_times)),
                finish_time_p5_seconds=float(np.percentile(driver_times, 5)),
                finish_time_p95_seconds=float(np.percentile(driver_times, 95)),
                projected_pit_laps=projected_pit_laps,
                finish_ahead_probability=finish_ahead_probability,
            )
        )

    return RaceSimulationResult(n_simulations=n_simulations, driver_distributions=distributions)
