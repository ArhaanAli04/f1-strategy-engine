"""Check the Strategy Simulator against strategy scenarios with a known answer.

Demo deployment Day 6d follow-up (2026-10-13). evaluate_simulator.py compares
the Monte Carlo with what really happened; this asks whether it gets cause and
effect right: for a driver in a real race state, does changing only the
driver's plan move the result the way it must (an extra stop costs about a
stop, the last-lap stop costs exactly one, worn tyres need changing), and the
way it really did (Austria 2025, a green-flag 2-stop race: the 1-stoppers and
the 3-stopper finished behind; PIA's and HAM's overcuts didn't get past)?

Every car other than the driver under test runs its real strategy from the
start lap (a forced plan is the whole plan), so a difference between two plans
comes from the plan alone; all plans of a scenario share one seed. Each
scenario prints its plans and PASS, FAIL, or KNOWN LIMIT (a gap the plan doc
already records, expected to fail). Read-only: writes nothing to the database
or S3 (Redis is only read, for weather).

Run in the worker container, like evaluate_simulator.py:

    python -m backend.scripts.check_simulator_scenarios [--simulations N] [--seed S]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import get_redis_settings
from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.race import Race
from backend.models.race import Session as SessionModel
from backend.scripts.evaluate_simulator import (
    DEFAULT_SEED,
    DEFAULT_SIMULATIONS,
    LapRow,
    _laps_by_driver,
    _simulate,
    real_stops,
)
from backend.services import strategy_service
from backend.services.ml.race_simulator import (
    DriverPositionDistribution,
    RaceSimulationInput,
    RaceSimulationResult,
)
from backend.workers import prediction_worker

logger = logging.getLogger(__name__)

Stops = tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class Plan:
    """The tested driver's plan: (in-lap, compound fitted) per stop, or the
    driver's real stops from the start lap when stops is None."""

    label: str
    stops: Stops | None


REAL = Plan("real", None)


@dataclass
class ScenarioRun:
    """One scenario's simulation results, per plan label."""

    driver_id: str
    results: dict[str, RaceSimulationResult]
    ids_by_code: dict[str, str]
    real_finish: dict[str, int]

    def _distribution(self, label: str, driver_id: str | None = None) -> DriverPositionDistribution:
        target = driver_id or self.driver_id
        return next(d for d in self.results[label].driver_distributions if d.driver_id == target)

    def finish(self, label: str) -> float:
        """Mean finish time (s) of the tested driver."""
        return self._distribution(label).mean_finish_time_seconds

    def position(self, label: str, code: str | None = None) -> float:
        """Mean finishing position of the tested driver, or of the car with this code."""
        target = self.ids_by_code[code] if code else None
        return self._distribution(label, target).mean_position

    def ahead_of(self, label: str, code: str) -> float:
        """Share of runs in which the tested driver finishes ahead of this car."""
        return self._distribution(label).finish_ahead_probability.get(self.ids_by_code[code], 0.0)


@dataclass(frozen=True)
class Scenario:
    name: str
    season: int
    round_number: int
    driver: str
    start_lap: int
    plans: tuple[Plan, ...]
    expected: str
    check: Callable[[ScenarioRun], bool]
    rival: str | None = None
    known_limit: bool = False


def _sweep(start: int, end: int, second_stop: int) -> tuple[Plan, ...]:
    return tuple(
        Plan(f"first stop L{lap}", ((lap, "HARD"), (second_stop, "MEDIUM")))
        for lap in range(start, end + 1)
    )


def _sweep_is_smooth(run: ScenarioRun) -> bool:
    finishes = [run.finish(label) for label in run.results]
    return max(abs(b - a) for a, b in zip(finishes, finishes[1:], strict=False)) <= 3.0


BELGIUM_2026 = (2026, 10)
CANADA_2026 = (2026, 5)
AUSTRIA_2025 = (2025, 11)
RACE_NAMES = {
    BELGIUM_2026: "Belgian GP 2026",
    CANADA_2026: "Canadian GP 2026",
    AUSTRIA_2025: "Austrian GP 2025",
}
TOP_FIVE_AUSTRIA = ("NOR", "PIA", "LEC", "HAM", "RUS")

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "Pointless extra stop",
        *BELGIUM_2026,
        "LEC",
        25,
        (Plan("no further stop", ()), Plan("extra stop L41 SOFT", ((41, "SOFT"),))),
        "the extra stop finishes 10-30 s later (a stop, less 3 laps on fresh tyres),"
        " no better placed",
        lambda r: (
            10 <= r.finish("extra stop L41 SOFT") - r.finish("no further stop") <= 30
            and r.position("extra stop L41 SOFT") >= r.position("no further stop") - 0.05
        ),
    ),
    Scenario(
        "Stop on the last lap",
        *BELGIUM_2026,
        "LEC",
        25,
        (Plan("no further stop", ()), Plan("stop L44 (last lap)", ((44, "SOFT"),))),
        "costs one stop, 18-24 s (22 s, less when a safety car falls on it), no tyre benefit",
        lambda r: 18 <= r.finish("stop L44 (last lap)") - r.finish("no further stop") <= 24,
    ),
    Scenario(
        "Fairly fresh tyre, 8 laps left",
        *BELGIUM_2026,
        "LEC",
        36,
        (Plan("no further stop", ()), Plan("stop L37 SOFT", ((37, "SOFT"),))),
        "staying out is faster (HARDs aged 16 with 8 laps to go)",
        lambda r: r.finish("no further stop") < r.finish("stop L37 SOFT"),
    ),
    Scenario(
        "Worn tyre, 40 laps left",
        *CANADA_2026,
        "ANT",
        28,
        (REAL, Plan("no further stop", ())),
        "stopping (really: L31 onto MEDIUM) beats running SOFTs aged 32 for 40 more laps",
        lambda r: r.finish("real") < r.finish("no further stop"),
    ),
    Scenario(
        "Big gap behind, stop keeps the place",
        *AUSTRIA_2025,
        "HAM",
        45,
        (REAL, Plan("no further stop", ())),
        "24.5 s clear of RUS, HAM's real stop on L50 keeps P4 ahead of him in at least 80% of runs",
        lambda r: r.ahead_of("real", "RUS") >= 0.8,
        rival="RUS",
    ),
    Scenario(
        "Undercut and overcut: PIA 0.9 s behind NOR (NOR stops L20, L52)",
        *AUSTRIA_2025,
        "PIA",
        17,
        (
            Plan("undercut L19", ((19, "HARD"), (53, "MEDIUM"))),
            Plan("same lap L20", ((20, "HARD"), (53, "MEDIUM"))),
            Plan("real overcut L24", ((24, "HARD"), (53, "MEDIUM"))),
            Plan("long overcut L28", ((28, "HARD"), (53, "MEDIUM"))),
        ),
        "PIA's real overcut (L24) does not get ahead of NOR in most runs (it didn't);"
        " the undercut does at least as well as the real overcut",
        lambda r: (
            r.ahead_of("real overcut L24", "NOR") < 0.5
            and r.ahead_of("undercut L19", "NOR") >= r.ahead_of("real overcut L24", "NOR")
        ),
        rival="NOR",
    ),
    Scenario(
        "Overcut: HAM 2.8 s behind LEC (LEC stops L25, L49)",
        *AUSTRIA_2025,
        "HAM",
        19,
        (REAL, Plan("undercut L24", ((24, "HARD"), (50, "MEDIUM")))),
        "HAM's real overcut by a lap (L26) leaves him behind LEC in most runs (it did)",
        lambda r: r.ahead_of("real", "LEC") < 0.5,
        rival="LEC",
    ),
    Scenario(
        "1-stop in a 2-stop race",
        *AUSTRIA_2025,
        "NOR",
        5,
        (REAL, Plan("1-stop L32 HARD", ((32, "HARD"),)), Plan("1-stop L38 HARD", ((38, "HARD"),))),
        "NOR's real 2-stop (L20 HARD, L52 MEDIUM) is faster than either 1-stop"
        " (the top four 2-stopped; the 1-stoppers finished P6-P7)",
        lambda r: r.finish("real") < min(r.finish("1-stop L32 HARD"), r.finish("1-stop L38 HARD")),
        rival="PIA",
    ),
    Scenario(
        "Aggressive 3-stop vs the 2-stop",
        *AUSTRIA_2025,
        "NOR",
        5,
        (REAL, Plan("3-stop SOFT-MEDIUM-SOFT", ((12, "SOFT"), (37, "MEDIUM"), (53, "SOFT")))),
        "the 3-stop (L12 SOFT, L37 MEDIUM, L53 SOFT) is slower than NOR's real 2-stop"
        " (nobody won with a 3-stop; TSU's finished P16)",
        lambda r: r.finish("real") < r.finish("3-stop SOFT-MEDIUM-SOFT"),
        rival="PIA",
    ),
    Scenario(
        "Pit-lap sweep",
        *AUSTRIA_2025,
        "NOR",
        15,
        _sweep(17, 27, 52),
        "moving the first stop one lap moves the finish time by at most 3 s (a smooth curve)",
        _sweep_is_smooth,
    ),
    Scenario(
        "Same plan twice",
        *AUSTRIA_2025,
        "NOR",
        15,
        (REAL, Plan("real again", ((20, "HARD"), (52, "MEDIUM")))),
        "identical results (scenarios in a compare share their random numbers)",
        lambda r: (
            r.finish("real") == r.finish("real again")
            and r.position("real") == r.position("real again")
        ),
    ),
    Scenario(
        "Real strategies replayed",
        *AUSTRIA_2025,
        "NOR",
        5,
        (REAL,),
        "with every car on its real strategy, each of the top five finishes within"
        " 1.5 places of its real position",
        lambda r: all(
            abs(r.position("real", code) - r.real_finish[code]) <= 1.5 for code in TOP_FIVE_AUSTRIA
        ),
    ),
    Scenario(
        "Wet tyre on a dry track",
        *BELGIUM_2026,
        "LEC",
        25,
        (Plan("no further stop", ()), Plan("stop L26 INTERMEDIATE", ((26, "INTERMEDIATE"),))),
        "INTERMEDIATEs on a dry track lose at least 30 s (the tyre models have no"
        " track-condition input, a recorded gap)",
        lambda r: r.finish("stop L26 INTERMEDIATE") - r.finish("no further stop") >= 30,
        known_limit=True,
    ),
)


@dataclass
class RaceData:
    session_id: uuid.UUID
    laps: dict[str, list[LapRow]]
    total_laps: int
    race_state_by_start: dict[tuple[str, int], RaceSimulationInput] = field(default_factory=dict)


def _real_plan(laps: list[LapRow], start_lap: int) -> Stops:
    # A stop under way at the start lap is charged to the first simulated lap,
    # as in evaluate_simulator.
    return tuple(
        (max(lap, start_lap + 1), compound) for lap, compound in real_stops(laps, start_lap - 1)
    )


async def _race_data(db: AsyncSession, season: int, round_number: int) -> RaceData:
    session_id = (
        await db.execute(
            select(SessionModel.id)
            .join(Race, SessionModel.race_id == Race.id)
            .where(
                Race.season == season,
                Race.round_number == round_number,
                SessionModel.session_type == "R",
            )
        )
    ).scalar_one()
    laps = await _laps_by_driver(db, session_id)
    total = max(lap.lap_number for driver_laps in laps.values() for lap in driver_laps)
    return RaceData(session_id, laps, total)


async def _run_scenario(
    db: AsyncSession,
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
    scenario: Scenario,
    race: RaceData,
    ids_by_code: dict[str, str],
    models: dict[str, Any],
    simulations: int,
    seed: int,
) -> ScenarioRun:
    driver_id = ids_by_code[scenario.driver]
    at_start = next(lap for lap in race.laps[driver_id] if lap.lap_number == scenario.start_lap)
    key = (driver_id, scenario.start_lap)
    if key not in race.race_state_by_start:
        maps_cache = await strategy_service.encoding_maps_for_database(
            db, prediction_worker._load_encoding_maps()
        )
        race.race_state_by_start[key] = await prediction_worker._build_race_state(
            db,
            redis_client,
            race.session_id,
            uuid.UUID(driver_id),
            scenario.start_lap,
            at_start.compound,
            at_start.tyre_age_laps,
            race.total_laps,
            maps_cache,
        )
    race_state = race.race_state_by_start[key]
    in_field = {d.driver_id for d in race_state.drivers}
    encoding = prediction_worker._COMPOUND_ENCODING

    def schedule(stops: Stops) -> dict[int, tuple[str, int]]:
        return {lap: (c, encoding.get(c, encoding["MEDIUM"])) for lap, c in stops}

    rivals = {
        d: schedule(_real_plan(race.laps[d], scenario.start_lap))
        for d in in_field
        if d != driver_id
    }
    own_real = _real_plan(race.laps[driver_id], scenario.start_lap)
    results = {
        plan.label: _simulate(
            race_state,
            models,
            {**rivals, driver_id: schedule(own_real if plan.stops is None else plan.stops)},
            simulations,
            seed,
        )
        for plan in scenario.plans
    }
    codes_by_id = {v: k for k, v in ids_by_code.items()}
    real_finish = {
        codes_by_id[d]: laps[-1].position
        for d, laps in race.laps.items()
        if d in codes_by_id and laps[-1].position is not None
    }
    return ScenarioRun(driver_id, results, ids_by_code, real_finish)


def _report(scenario: Scenario, run: ScenarioRun, race: RaceData) -> str:
    passed = scenario.check(run)
    if scenario.known_limit:
        verdict = "PASS (limit not hit)" if passed else "KNOWN LIMIT (fails as expected)"
    else:
        verdict = "PASS" if passed else "FAIL"
    logger.info(
        "== %s: %s, %s from lap %d of %d ==",
        scenario.name,
        RACE_NAMES[(scenario.season, scenario.round_number)],
        scenario.driver,
        scenario.start_lap,
        race.total_laps,
    )
    base = run.finish(next(iter(run.results)))
    for plan in scenario.plans:
        rival = (
            f", ahead of {scenario.rival} in {run.ahead_of(plan.label, scenario.rival) * 100:.0f}%"
            if scenario.rival
            else ""
        )
        logger.info(
            "  %-26s mean P%.2f, finish %+.1f s%s",
            plan.label,
            run.position(plan.label),
            run.finish(plan.label) - base,
            rival,
        )
    if scenario.name == "Real strategies replayed":
        for code in TOP_FIVE_AUSTRIA:
            logger.info(
                "  %s: simulated mean P%.2f, real P%d",
                code,
                run.position("real", code),
                run.real_finish[code],
            )
    logger.info("  expected: %s", scenario.expected)
    logger.info("  %s", verdict)
    return verdict


async def run(simulations: int, seed: int) -> dict[str, str]:
    """Run every scenario and log the report.

    Args:
        simulations: Monte Carlo runs per plan.
        seed: The seed every plan of every scenario uses.
    Returns:
        Scenario name -> verdict.
    """
    models = prediction_worker._load_models()
    engine = get_engine()
    redis_client: aioredis.Redis = aioredis.from_url(  # type: ignore[type-arg]
        get_redis_settings().redis_url, decode_responses=True
    )
    verdicts: dict[str, str] = {}
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            ids_by_code = {
                row.code: str(row.id)
                for row in (await db.execute(select(Driver.id, Driver.code))).all()
            }
            races: dict[tuple[int, int], RaceData] = {}
            for scenario in SCENARIOS:
                key = (scenario.season, scenario.round_number)
                if key not in races:
                    races[key] = await _race_data(db, *key)
                run_result = await _run_scenario(
                    db, redis_client, scenario, races[key], ids_by_code, models, simulations, seed
                )
                verdicts[scenario.name] = _report(scenario, run_result, races[key])
    finally:
        await redis_client.aclose()  # type: ignore[attr-defined]
        await engine.dispose()
    logger.info("== Summary ==")
    for name, verdict in verdicts.items():
        logger.info("  %-62s %s", name, verdict)
    return verdicts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--simulations", type=int, default=DEFAULT_SIMULATIONS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    asyncio.run(run(args.simulations, args.seed))


if __name__ == "__main__":
    main()
