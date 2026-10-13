"""Measure the Strategy Simulator's Monte Carlo against what really happened.

Demo deployment Day 6d, CP1 (2026-10-11): the before/after check for every
change to race_simulator.simulate_race and its inputs. Read-only: it writes
nothing to the database or S3 (Redis is only read, for weather).

For each curated replay race (demo_service.CURATED_RACES, as found in this
database) it starts the simulation at a few laps spread over the race
(START_LAP_FRACTIONS of the race distance), on the field state POST /simulate
builds (prediction_worker._build_race_state: every car still running, as of
that lap), and runs it twice with one fixed seed:

- real stops forced: every car makes exactly its real remaining stops (none,
  if it ran to the flag), onto the compound it really fitted: the real
  strategies replayed. Measured: finishing order against the real one (both
  ranked among the cars that really finished). Until CP2 (2026-10-11) a forced
  plan wasn't the whole plan, and this run also counted the stops the pit
  model added on top: 70.6 over the 9 starts.
- nothing forced: every car's stops are left to the simulation, as for a
  rival in the Simulator (and for the requester with an empty plan).
  Measured: expected stops against real stops, stops given to cars that ran
  to the flag (phantom), real stops the simulation mostly misses, and when the
  simulated first stop falls against the real one.

A stop's lap is its in-lap, the last lap on the old tyres, which is the lap
race_simulator charges the stop to; a stop is a lap after which the tyre age
goes down or the compound changes. The simulation starts on the lap after the
start lap, so a stop whose in-lap is the start lap itself (under way when the
simulation starts) is charged to the first simulated lap. A car's simulated stops on a lap are that
lap's share of runs with a stop (DriverPositionDistribution.projected_pit_laps),
so a car's expected stops are their sum. The real finishing order is each
finisher's position on its last lap (lap_data), the order on the road: post-race
penalties aren't ingested (CLAUDE.md, Deferred Wiring).

Models load from S3 as in the app, so run it where the app runs (the worker
container locally):

    python -m backend.scripts.evaluate_simulator [--simulations N] [--seed S]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from dataclasses import dataclass
from statistics import mean, median
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import get_redis_settings
from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.telemetry import LapData
from backend.scripts.precompute_replay import CuratedTarget, resolve_targets
from backend.services import strategy_service
from backend.services.ml import race_simulator
from backend.services.ml.race_simulator import RaceSimulationInput, RaceSimulationResult
from backend.workers import prediction_worker

logger = logging.getLogger(__name__)

# Where each race's simulations start, as fractions of its distance: early
# (most cars still to stop), middle, and late (most stops made).
START_LAP_FRACTIONS = (0.35, 0.55, 0.75)
# A driver whose last lap is this close to the race distance finished (lapped
# cars complete fewer laps); anyone further back retired.
FINISHED_WITHIN_LAPS = 3
# A real stop counts as caught when the simulation stops the car in at least
# this share of runs, summed over the remaining laps.
CAUGHT_EXPECTED_STOPS = 0.5
CLOSE_TIMING_LAPS = 2
DEFAULT_SIMULATIONS = 1000
DEFAULT_SEED = 6


@dataclass(frozen=True)
class LapRow:
    lap_number: int
    compound: str
    tyre_age_laps: int
    position: int | None


def real_stops(laps: list[LapRow], after_lap: int) -> list[tuple[int, str]]:
    """The driver's real stops with an in-lap after after_lap: (in-lap, compound fitted).

    Args:
        laps: The driver's laps, ascending by lap_number.
        after_lap: Only stops whose in-lap is later than this.
    Returns:
        (in_lap, new_compound) pairs, ascending.
    """
    stops = []
    for previous, current in zip(laps, laps[1:], strict=False):
        if previous.lap_number <= after_lap or current.lap_number != previous.lap_number + 1:
            continue
        if current.tyre_age_laps < previous.tyre_age_laps or current.compound != previous.compound:
            stops.append((previous.lap_number, current.compound))
    return stops


def first_stop_lap(projected_pit_laps: list[tuple[int, float]]) -> int | None:
    """The lap by which the simulation has stopped the car in half its runs.

    Summing a lap's share of runs with a stop treats a second stop in the same
    run as a first, so this is approximate once cars stop twice; in the
    remaining laps of a race that is rare.

    Args:
        projected_pit_laps: (lap, share of runs stopping that lap), ascending.
    Returns:
        That lap, or None if the shares never add up to a half.
    """
    total = 0.0
    for lap, share in projected_pit_laps:
        total += share
        if total >= CAUGHT_EXPECTED_STOPS:
            return lap
    return None


def ranks(values: dict[str, float]) -> dict[str, int]:
    """1-based rank of each key by value, lowest first (ties by key, so stable).

    Args:
        values: key -> value (a position or a mean position).
    Returns:
        key -> rank.
    """
    order = sorted(values, key=lambda key: (values[key], key))
    return {key: i + 1 for i, key in enumerate(order)}


@dataclass
class StartResult:
    """One race at one start lap: both runs, measured."""

    race: str
    start_lap: int
    total_laps: int
    cars: int
    rank_errors_forced: list[int]
    rank_errors_free: list[int]
    expected_stops_free: float
    real_stop_count: int
    phantom_stops_free: float
    phantom_cars: list[tuple[str, float]]
    missed_cars: list[str]
    # Simulated first stop minus the real in-lap: negative is early.
    caught_timing_errors: list[int]


async def _laps_by_driver(db: AsyncSession, session_id: uuid.UUID) -> dict[str, list[LapRow]]:
    rows = (
        await db.execute(
            select(
                LapData.driver_id,
                LapData.lap_number,
                LapData.compound,
                LapData.tyre_age_laps,
                LapData.position,
            )
            .where(LapData.session_id == session_id)
            .order_by(LapData.driver_id, LapData.lap_number)
        )
    ).all()
    laps: dict[str, list[LapRow]] = {}
    for driver_id, lap_number, compound, tyre_age, position in rows:
        laps.setdefault(str(driver_id), []).append(
            LapRow(lap_number, str(compound).upper(), int(tyre_age), position)
        )
    return laps


def _simulate(
    race_state: RaceSimulationInput,
    models: dict[str, Any],
    forced: dict[str, dict[int, tuple[str, int]]] | None,
    simulations: int,
    seed: int,
) -> RaceSimulationResult:
    tire_deg_pipelines = {
        compound: models[f"tire_deg_{suffix}.pkl"]
        for compound, suffix in prediction_worker._COMPOUND_TO_MODEL_SUFFIX.items()
    }
    return race_simulator.simulate_race(
        race_state,
        tire_deg_pipelines,
        models["pit_predictor.pkl"],
        models["safety_car_model.pkl"],
        n_simulations=simulations,
        forced_pit_laps=forced,
        rng_seed=seed,
    )


def _rank_errors(
    result: RaceSimulationResult, finishers: dict[str, int], in_field: set[str]
) -> list[int]:
    common = {d for d in finishers if d in in_field}
    simulated = ranks(
        {d.driver_id: d.mean_position for d in result.driver_distributions if d.driver_id in common}
    )
    real = ranks({d: float(finishers[d]) for d in common})
    return [abs(simulated[d] - real[d]) for d in common]


async def _evaluate_start(
    db: AsyncSession,
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
    target: CuratedTarget,
    laps: dict[str, list[LapRow]],
    codes: dict[str, str],
    models: dict[str, Any],
    start_lap: int,
    simulations: int,
    seed: int,
) -> StartResult:
    total_laps = max(lap.lap_number for driver_laps in laps.values() for lap in driver_laps)
    at_start = {
        d: next(lap for lap in driver_laps if lap.lap_number == start_lap)
        for d, driver_laps in laps.items()
        if any(lap.lap_number == start_lap for lap in driver_laps)
    }
    # The requester only decides whose compound and tyre age come from the
    # request; give it its own real ones, so it is like every other car.
    requester = min(at_start, key=lambda d: at_start[d].position or 99)
    maps_cache = await strategy_service.encoding_maps_for_database(
        db, prediction_worker._load_encoding_maps()
    )
    race_state = await prediction_worker._build_race_state(
        db,
        redis_client,
        target.session_id,
        uuid.UUID(requester),
        start_lap,
        at_start[requester].compound,
        at_start[requester].tyre_age_laps,
        total_laps,
        maps_cache,
    )
    in_field = {d.driver_id for d in race_state.drivers}
    finishers = {
        d: driver_laps[-1].position
        for d, driver_laps in laps.items()
        if driver_laps[-1].lap_number >= total_laps - FINISHED_WITHIN_LAPS
        and driver_laps[-1].position is not None
    }
    stops = {
        d: [
            (max(lap, start_lap + 1), compound)
            for lap, compound in real_stops(laps[d], start_lap - 1)
        ]
        for d in in_field
    }
    encoding = prediction_worker._COMPOUND_ENCODING
    forced = {
        d: {lap: (compound, encoding.get(compound, encoding["MEDIUM"])) for lap, compound in s}
        for d, s in stops.items()
    }

    forced_result = _simulate(race_state, models, forced, simulations, seed)
    free_result = _simulate(race_state, models, None, simulations, seed)

    expected: dict[str, float] = {}
    timing_errors: list[int] = []
    missed: list[str] = []
    for dist in free_result.driver_distributions:
        expected[dist.driver_id] = sum(share for _, share in dist.projected_pit_laps)
        real = stops.get(dist.driver_id, [])
        if not real:
            continue
        if expected[dist.driver_id] < CAUGHT_EXPECTED_STOPS:
            missed.append(codes.get(dist.driver_id, dist.driver_id))
            continue
        simulated_first = first_stop_lap(dist.projected_pit_laps)
        if simulated_first is not None:
            timing_errors.append(simulated_first - real[0][0])
    finished_no_stop = [d for d in in_field if d in finishers and not stops[d]]
    phantom_cars = sorted(
        ((codes.get(d, d), round(expected[d], 2)) for d in finished_no_stop if expected[d] >= 0.05),
        key=lambda item: -item[1],
    )

    return StartResult(
        race=target.race_name,
        start_lap=start_lap,
        total_laps=total_laps,
        cars=len(in_field),
        rank_errors_forced=_rank_errors(forced_result, finishers, in_field),
        rank_errors_free=_rank_errors(free_result, finishers, in_field),
        expected_stops_free=sum(expected.values()),
        real_stop_count=sum(len(s) for s in stops.values()),
        phantom_stops_free=sum(expected[d] for d in finished_no_stop),
        phantom_cars=phantom_cars,
        missed_cars=sorted(missed),
        caught_timing_errors=timing_errors,
    )


def _share(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.0f}%)" if whole else "0/0"


def _timing_summary(signed_errors: list[int]) -> str:
    errors = [abs(e) for e in signed_errors]
    return (
        f"median {median(errors):.1f} laps, mean {mean(errors):.1f}, within "
        f"{CLOSE_TIMING_LAPS}: "
        f"{_share(sum(e <= CLOSE_TIMING_LAPS for e in errors), len(errors))}; "
        f"early {sum(e < 0 for e in signed_errors)}, on the lap "
        f"{sum(e == 0 for e in signed_errors)}, late {sum(e > 0 for e in signed_errors)}"
    )


def _report_start(r: StartResult) -> None:
    logger.info("== %s, from lap %d of %d: %d cars ==", r.race, r.start_lap, r.total_laps, r.cars)
    for label, errors in (
        ("real stops forced", r.rank_errors_forced),
        ("nothing forced", r.rank_errors_free),
    ):
        logger.info(
            "  %s: finishing rank error mean %.2f, within 1 place %s",
            label,
            mean(errors) if errors else 0.0,
            _share(sum(e <= 1 for e in errors), len(errors)),
        )
    logger.info(
        "  nothing forced: expected stops %.1f against %d real; on cars that ran to the flag %.1f",
        r.expected_stops_free,
        r.real_stop_count,
        r.phantom_stops_free,
    )
    if r.phantom_cars:
        logger.info(
            "    phantom stops by car: %s", ", ".join(f"{c} {e}" for c, e in r.phantom_cars)
        )
    logger.info("    real stops mostly missed: %s", ", ".join(r.missed_cars) or "none")
    if r.caught_timing_errors:
        logger.info(
            "    first stop, simulated vs real in-lap: %s", _timing_summary(r.caught_timing_errors)
        )


def _report_total(results: list[StartResult]) -> None:
    forced = [e for r in results for e in r.rank_errors_forced]
    free = [e for r in results for e in r.rank_errors_free]
    timing = [e for r in results for e in r.caught_timing_errors]
    real_stop_cars = sum(r.real_stop_count for r in results)
    logger.info("== All curated races, %d starts ==", len(results))
    logger.info(
        "  finishing rank error, real stops forced: mean %.2f, within 1 place %s",
        mean(forced),
        _share(sum(e <= 1 for e in forced), len(forced)),
    )
    logger.info(
        "  finishing rank error, nothing forced: mean %.2f, within 1 place %s",
        mean(free),
        _share(sum(e <= 1 for e in free), len(free)),
    )
    logger.info(
        "  nothing forced: expected stops %.1f against %d real; on cars that ran to the flag %.1f",
        sum(r.expected_stops_free for r in results),
        real_stop_cars,
        sum(r.phantom_stops_free for r in results),
    )
    logger.info("  real stops mostly missed: %d cars", sum(len(r.missed_cars) for r in results))
    if timing:
        logger.info("  first stop, simulated vs real in-lap: %s", _timing_summary(timing))


async def run(simulations: int, seed: int) -> list[StartResult]:
    """Evaluate every curated race in this database and log the report.

    Args:
        simulations: Monte Carlo runs per simulation.
        seed: The one seed every simulation uses, so a rerun is identical.
    Returns:
        One StartResult per race and start lap.
    """
    models = prediction_worker._load_models()
    engine = get_engine()
    redis_client: aioredis.Redis = aioredis.from_url(  # type: ignore[type-arg]
        get_redis_settings().redis_url, decode_responses=True
    )
    results: list[StartResult] = []
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            codes = {
                str(row.id): row.code
                for row in (await db.execute(select(Driver.id, Driver.code))).all()
            }
            for target in await resolve_targets(db, None):
                laps = await _laps_by_driver(db, target.session_id)
                total_laps = max(
                    lap.lap_number for driver_laps in laps.values() for lap in driver_laps
                )
                for fraction in START_LAP_FRACTIONS:
                    result = await _evaluate_start(
                        db,
                        redis_client,
                        target,
                        laps,
                        codes,
                        models,
                        round(total_laps * fraction),
                        simulations,
                        seed,
                    )
                    _report_start(result)
                    results.append(result)
    finally:
        await redis_client.aclose()  # type: ignore[attr-defined]
        await engine.dispose()
    _report_total(results)
    return results


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--simulations", type=int, default=DEFAULT_SIMULATIONS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    asyncio.run(run(args.simulations, args.seed))


if __name__ == "__main__":
    main()
