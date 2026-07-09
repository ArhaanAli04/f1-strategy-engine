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
- tire_deg_model's `fuel_adjusted_time` feature is defined at training time as
  `lap_time_seconds - fuel_penalty` — i.e. it partially encodes the actual lap time,
  which a forward simulation does not have (that's what we're simulating). We
  approximate it with just the fuel-burn trend component (the penalty term with the
  unknown lap_time_seconds term dropped), which preserves the feature's monotonic
  trend across the race at the cost of not matching the training distribution's
  absolute scale. Acceptable since compound/tyre_age dominate the model's splits.
- cumulative_race_time_seconds accumulates real elapsed race time (input, carrying
  today's actual gaps) plus simulated lap_time_delta going forward. Since
  lap_time_delta is relative to each driver's own session median (not absolute pace),
  this preserves real observed pace differences at simulation start and simulates how
  tyre wear/variance/pit stops change the field from there — it does not attempt to
  model absolute per-driver pace.
- After a pit stop, compound is assumed unchanged (no compound-choice model exists
  yet) and tyre age resets to 0.
- A safety car lap "neutralises gaps" by collapsing every driver's cumulative time to
  the simulation's current leader time plus a fixed SC lap time — the exact SC lap
  time value is inconsequential to relative standings since it's applied uniformly.
- LAP_TIME_NOISE_STD_SECONDS is an assumed lap-to-lap variability constant (no
  computed historical variance exists in the codebase yet), in the same spirit as
  tire_deg_model.ASSUMED_START_FUEL_KG.
- MIN_LAPS_BETWEEN_PITS guards against the pit_predictor's per-lap threshold decision
  triggering unrealistic back-to-back pit stops near the threshold boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numba
import numpy as np
import numpy.typing as npt

from backend.services.ml import pit_predictor, safety_car_model, tire_deg_model

N_SIMULATIONS = 1000
PIT_STOP_SECONDS = 22.0
LAP_TIME_NOISE_STD_SECONDS = 0.35
SC_LAP_TIME_SECONDS = 25.0
MIN_LAPS_BETWEEN_PITS = 5


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


@dataclass(frozen=True)
class RaceSimulationInput:
    circuit_name: str
    circuit_id_encoded: int
    current_lap: int
    total_laps: int
    wet_track: bool
    drivers: list[DriverRaceState] = field(default_factory=list)


@dataclass(frozen=True)
class DriverPositionDistribution:
    driver_id: str
    position_probabilities: dict[int, float]
    mean_position: float


@dataclass(frozen=True)
class RaceSimulationResult:
    n_simulations: int
    driver_distributions: list[DriverPositionDistribution]


@numba.njit(cache=True)  # type: ignore[untyped-decorator]
def _advance_lap(
    cumulative_time: npt.NDArray[np.float64],
    tyre_age: npt.NDArray[np.int64],
    predicted_delta: npt.NDArray[np.float64],
    noise_std: float,
    pit_flags: npt.NDArray[np.bool_],
    pit_stop_seconds: float,
    sc_active: npt.NDArray[np.bool_],
    sc_lap_time_seconds: float,
) -> None:
    """Numba-compiled inner loop: advance every (simulation, driver) pair by one lap.

    Mutates cumulative_time and tyre_age in place. On a safety car lap, every driver
    in that simulation is bunched to the simulation's current leader time (gaps
    neutralised) instead of receiving their individually predicted pace. Pit stops
    add pit_stop_seconds and reset tyre age to 0, applied after the lap's time/age
    update on both the racing and safety-car branches.

    Args:
        cumulative_time: (n_sims, n_drivers) elapsed race time in seconds, mutated.
        tyre_age: (n_sims, n_drivers) laps on current tyre, mutated.
        predicted_delta: (n_sims, n_drivers) tire_deg-model-predicted lap time delta.
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
            leader_time = cumulative_time[s, 0]
            for d in range(1, n_drivers):
                if cumulative_time[s, d] < leader_time:
                    leader_time = cumulative_time[s, d]
            sc_time = leader_time + sc_lap_time_seconds
            for d in range(n_drivers):
                cumulative_time[s, d] = sc_time
                tyre_age[s, d] += 1
                if pit_flags[s, d]:
                    cumulative_time[s, d] += pit_stop_seconds
                    tyre_age[s, d] = 0
        else:
            for d in range(n_drivers):
                noise = np.random.normal(0.0, noise_std)
                cumulative_time[s, d] += predicted_delta[s, d] + noise
                tyre_age[s, d] += 1
                if pit_flags[s, d]:
                    cumulative_time[s, d] += pit_stop_seconds
                    tyre_age[s, d] = 0


@numba.njit(cache=True)  # type: ignore[untyped-decorator]
def _seed_numba_rng(seed: int) -> None:
    np.random.seed(seed)


def _tire_deg_predictions(
    race_state: RaceSimulationInput,
    compound_groups: dict[str, npt.NDArray[np.intp]],
    tire_deg_pipelines: dict[str, Any],
    lap_number: int,
    tyre_age: npt.NDArray[np.int64],
    driver_id_encoded: npt.NDArray[np.int64],
    fuel_adjusted_time: float,
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Batch tire_deg predictions (delta + life remaining) for every (sim, driver) pair.

    Args:
        race_state: The simulation's static race context.
        compound_groups: compound -> array of driver column indices with that compound.
        tire_deg_pipelines: Fitted tire_deg pipelines, keyed by compound.
        lap_number: Lap being predicted for.
        tyre_age: (n_sims, n_drivers) current tyre age.
        driver_id_encoded: (n_drivers,) per-driver encoded id.
        fuel_adjusted_time: This lap's fuel_adjusted_time proxy (see module docstring).
    Returns:
        (predicted_delta, predicted_life_remaining), each (n_sims, n_drivers). Drivers
        on a compound with no fitted pipeline get delta=0 and life_remaining capped at
        tire_deg_model.MAX_LOOKAHEAD_LAPS.
    """
    n_sims, n_drivers = tyre_age.shape
    predicted_delta = np.zeros((n_sims, n_drivers))
    predicted_life_remaining = np.full(
        (n_sims, n_drivers), float(tire_deg_model.MAX_LOOKAHEAD_LAPS)
    )

    for compound, idx in compound_groups.items():
        pipeline = tire_deg_pipelines.get(compound)
        if pipeline is None:
            continue

        group_tyre_age = tyre_age[:, idx]
        flat_shape = group_tyre_age.shape
        tyre_age_flat = group_tyre_age.ravel().astype(np.int64)
        compound_encoded_flat = np.tile(
            np.array([race_state.drivers[i].compound_encoded for i in idx], dtype=np.int64),
            n_sims,
        )
        driver_id_encoded_flat = np.tile(driver_id_encoded[idx], n_sims)
        lap_number_arr = np.full(tyre_age_flat.shape[0], lap_number, dtype=np.int64)
        fuel_adjusted_time_arr = np.full(tyre_age_flat.shape[0], fuel_adjusted_time)
        circuit_id_encoded_arr = np.full(
            tyre_age_flat.shape[0], race_state.circuit_id_encoded, dtype=np.int64
        )

        features = np.column_stack(
            [
                lap_number_arr.astype(np.float64),
                compound_encoded_flat.astype(np.float64),
                tyre_age_flat.astype(np.float64),
                fuel_adjusted_time_arr,
                circuit_id_encoded_arr.astype(np.float64),
                driver_id_encoded_flat.astype(np.float64),
            ]
        )
        predicted_delta[:, idx] = pipeline.predict(features).reshape(flat_shape)

        life_flat = tire_deg_model.predict_life_remaining_batch(
            pipeline,
            lap_number_arr,
            compound_encoded_flat,
            tyre_age_flat,
            fuel_adjusted_time_arr,
            circuit_id_encoded_arr,
            driver_id_encoded_flat,
        )
        predicted_life_remaining[:, idx] = life_flat.reshape(flat_shape)

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
    scores: npt.NDArray[np.float64] = pit_model.predict_proba(features)[:, 1].reshape(
        n_sims, n_drivers
    )
    return scores


def simulate_race(
    race_state: RaceSimulationInput,
    tire_deg_pipelines: dict[str, Any],
    pit_model: Any,
    sc_model: safety_car_model.SafetyCarModel,
    n_simulations: int = N_SIMULATIONS,
    rng_seed: int | None = None,
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
    Returns:
        RaceSimulationResult with a finishing-position probability distribution per driver.
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

    compound_groups: dict[str, npt.NDArray[np.intp]] = {
        compound: np.array([i for i, d in enumerate(race_state.drivers) if d.compound == compound])
        for compound in {d.compound for d in race_state.drivers}
    }

    for lap_number in range(race_state.current_lap + 1, race_state.total_laps + 1):
        fuel_at_lap = tire_deg_model.ASSUMED_START_FUEL_KG * (
            1 - lap_number / race_state.total_laps
        )
        fuel_adjusted_time = -tire_deg_model.FUEL_TIME_PENALTY_PER_KG * (
            tire_deg_model.ASSUMED_START_FUEL_KG - fuel_at_lap
        )

        predicted_delta, predicted_life_remaining = _tire_deg_predictions(
            race_state,
            compound_groups,
            tire_deg_pipelines,
            lap_number,
            tyre_age,
            driver_id_encoded,
            fuel_adjusted_time,
        )

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
        pit_flags = (pit_scores > pit_predictor.ALERT_THRESHOLD) & (
            tyre_age >= MIN_LAPS_BETWEEN_PITS
        )

        _advance_lap(
            cumulative_time,
            tyre_age,
            predicted_delta,
            LAP_TIME_NOISE_STD_SECONDS,
            pit_flags,
            PIT_STOP_SECONDS,
            sc_active,
            SC_LAP_TIME_SECONDS,
        )

    order = np.argsort(cumulative_time, axis=1)
    finishing_positions = np.argsort(order, axis=1) + 1

    distributions = []
    for i, driver in enumerate(race_state.drivers):
        counts = np.bincount(finishing_positions[:, i], minlength=n_drivers + 1)[1 : n_drivers + 1]
        probabilities = counts / n_simulations
        distributions.append(
            DriverPositionDistribution(
                driver_id=driver.driver_id,
                position_probabilities={p + 1: float(probabilities[p]) for p in range(n_drivers)},
                mean_position=float(np.mean(finishing_positions[:, i])),
            )
        )

    return RaceSimulationResult(n_simulations=n_simulations, driver_distributions=distributions)
