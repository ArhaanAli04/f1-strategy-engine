"""Measure the pit-window recommendation engine against what really happened.

Demo deployment Day 6c, CP1 (2026-10-10): the before/after check for every
change to strategy_service.compute_pit_recommendation. Read-only: it writes
nothing to the database, Redis or S3.

For each curated replay race (demo_service.CURATED_RACES, as found in this
database) and each driver still running, at every lap of the replay window it
runs the engine on exactly the state the per-lap pipeline gives it
(_current_state as of that lap: compound, tyre age, lap, race distance,
circuit) and compares the recommendation with the driver's real next stop in
lap_data. A real stop's lap is its in-lap (the last lap on the old tyres),
which is what the engine's pit_lap means; a stop is a lap after which the tyre
age goes down or the compound changes. The real next stop counts from the lap
after the one predicted: a stop whose in-lap is that lap is already under way
(corrected 2026-10-10, Day 6c CP4: counting it put 30 of 150 unadvisable stops
into the comparison).

Measured, overall and per race:
- stop vs no stop: does the engine recommend a stop when the driver really
  stopped again, and not when they ran to the flag;
- compound: the recommended compound against the one really fitted;
- timing: how far the recommended lap is from the real in-lap;
- window width, and how often the real in-lap falls inside the window
  (coverage); how often the answer sits on the search limit
  (lap + PIT_WINDOW_LOOKAHEAD_LAPS, or the last lap);
- the two-compound rule (dry races only): recommending the only dry compound
  the driver has used so far.

Drivers who retired are left out of the stop comparisons: their "no further
stop" says nothing about strategy. Models load from S3 as in the app, so run it
where the app runs (the worker container locally):

    python -m backend.scripts.evaluate_pit_recommendations [--csv out.csv]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from statistics import mean, median
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.telemetry import LapData
from backend.scripts.precompute_replay import CuratedTarget, resolve_targets
from backend.services import strategy_service

logger = logging.getLogger(__name__)

DRY_COMPOUNDS = frozenset({"SOFT", "MEDIUM", "HARD"})
WET_COMPOUNDS = frozenset({"INTERMEDIATE", "WET"})
# A driver whose last lap is this close to the race distance finished (lapped
# cars complete fewer laps); anyone further back retired.
FINISHED_WITHIN_LAPS = 3
# "Close" timing: the recommended lap within this many laps of the real in-lap.
CLOSE_TIMING_LAPS = 2


@dataclass
class LapRow:
    lap_number: int
    compound: str
    tyre_age_laps: int


@dataclass
class Evaluation:
    """One driver at one lap: the engine's answer next to what happened."""

    race: str
    driver: str
    lap: int
    compound: str
    tyre_age: int
    dry_compounds_used: str
    engine_stops: bool
    engine_pit_lap: int | None
    engine_compound: str | None
    window_start: int | None
    window_end: int | None
    confidence: float | None
    at_search_limit: bool
    real_stops: bool | None  # None: the driver retired later, so unknown
    real_pit_lap: int | None
    real_compound: str | None


def _next_real_stop(laps: list[LapRow], from_lap: int) -> tuple[int, str] | None:
    """The driver's next real stop at or after from_lap: (in-lap, compound fitted).

    Args:
        laps: The driver's laps, ascending by lap_number.
        from_lap: First in-lap to consider.
    Returns:
        (in_lap, new_compound), or None if they made no further stop.
    """
    for previous, current in zip(laps, laps[1:], strict=False):
        if previous.lap_number < from_lap or current.lap_number != previous.lap_number + 1:
            continue
        if current.tyre_age_laps < previous.tyre_age_laps or current.compound != previous.compound:
            return previous.lap_number, current.compound
    return None


async def _evaluate_race(
    db: AsyncSession,
    target: CuratedTarget,
    models: dict[str, Any],
    mae_cache: dict[str, float | None],
) -> list[Evaluation]:
    rows = (
        await db.execute(
            select(LapData.driver_id, LapData.lap_number, LapData.compound, LapData.tyre_age_laps)
            .where(LapData.session_id == target.session_id)
            .order_by(LapData.driver_id, LapData.lap_number)
        )
    ).all()
    laps_by_driver: dict[uuid.UUID, list[LapRow]] = {}
    for driver_id, lap_number, compound, tyre_age in rows:
        laps_by_driver.setdefault(driver_id, []).append(
            LapRow(lap_number, str(compound).upper(), int(tyre_age))
        )
    code_rows = (
        await db.execute(select(Driver.id, Driver.code).where(Driver.id.in_(laps_by_driver)))
    ).all()
    codes: dict[uuid.UUID, str] = {row.id: row.code for row in code_rows}
    wet_race = any(
        lap.compound in WET_COMPOUNDS for laps in laps_by_driver.values() for lap in laps
    )
    maps_cache = await strategy_service.encoding_maps_for_database(
        db, strategy_service._load_encoding_maps(), list(laps_by_driver)
    )

    evaluations: list[Evaluation] = []
    for driver_id, laps in laps_by_driver.items():
        by_lap = {lap.lap_number: lap for lap in laps}
        for lap_number in range(target.start_lap, target.end_lap + 1):
            if lap_number not in by_lap:
                continue
            state = await strategy_service._current_state(
                db, target.session_id, driver_id, as_of_lap=lap_number
            )
            state["compounds_used"] = await strategy_service.compounds_used(
                db, target.session_id, driver_id, lap_number
            )
            total_laps = int(state["total_laps"])
            candidates = strategy_service.compute_pit_recommendation(
                models, maps_cache, mae_cache, driver_id, state
            )
            top = candidates[0] if candidates else None
            stops = top is not None and not top.get("no_further_stop", False)

            used = sorted(
                {lap.compound for lap in laps if lap.lap_number <= lap_number} & DRY_COMPOUNDS
            )
            # A stop whose in-lap is this lap is already under way when the
            # prediction is made (the car is in the pit lane), so the real
            # next stop counts from the next lap.
            real = _next_real_stop(laps, lap_number + 1)
            finished = laps[-1].lap_number >= total_laps - FINISHED_WITHIN_LAPS
            real_stops: bool | None = True if real else (False if finished else None)
            limit = min(lap_number + strategy_service.PIT_WINDOW_LOOKAHEAD_LAPS, total_laps)

            evaluations.append(
                Evaluation(
                    race=target.race_name,
                    driver=codes.get(driver_id, str(driver_id)),
                    lap=lap_number,
                    compound=by_lap[lap_number].compound,
                    tyre_age=by_lap[lap_number].tyre_age_laps,
                    dry_compounds_used="/".join(used) if not wet_race else "WET RACE",
                    engine_stops=stops,
                    engine_pit_lap=top["pit_lap"] if top and stops else None,
                    engine_compound=top["recommended_compound"] if top and stops else None,
                    window_start=top["window_start"] if top and stops else None,
                    window_end=top["window_end"] if top and stops else None,
                    confidence=top["confidence_score"] if top else None,
                    at_search_limit=stops and top is not None and top["pit_lap"] >= limit,
                    real_stops=real_stops,
                    real_pit_lap=real[0] if real else None,
                    real_compound=real[1] if real else None,
                )
            )
    return evaluations


def _share(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.0f}%)" if whole else "0/0"


def _report(title: str, evaluations: list[Evaluation]) -> None:
    n = len(evaluations)
    known = [e for e in evaluations if e.real_stops is not None]
    real_stop = [e for e in known if e.real_stops]
    real_no_stop = [e for e in known if not e.real_stops]
    agree = [e for e in known if e.engine_stops == e.real_stops]
    both_stop = [e for e in real_stop if e.engine_stops]
    compound_match = [e for e in both_stop if e.engine_compound == e.real_compound]
    distances = [
        abs(e.engine_pit_lap - e.real_pit_lap)
        for e in both_stop
        if e.engine_pit_lap is not None and e.real_pit_lap is not None
    ]
    covered = [
        e
        for e in both_stop
        if e.window_start is not None
        and e.window_end is not None
        and e.real_pit_lap is not None
        and e.window_start <= e.real_pit_lap <= e.window_end
    ]
    widths = [
        e.window_end - e.window_start + 1
        for e in evaluations
        if e.window_start is not None and e.window_end is not None
    ]
    dry_single = [
        e for e in evaluations if e.engine_stops and e.dry_compounds_used in DRY_COMPOUNDS
    ]
    violations = [e for e in dry_single if e.engine_compound == e.dry_compounds_used]
    confidences = [e.confidence for e in evaluations if e.confidence is not None]

    logger.info("== %s: %d driver-laps (%d with a known outcome) ==", title, n, len(known))
    logger.info(
        "  real outcome: %s stop again, %s run to the flag",
        _share(len(real_stop), len(known)),
        _share(len(real_no_stop), len(known)),
    )
    logger.info(
        "  engine recommends a stop: %s", _share(sum(e.engine_stops for e in evaluations), n)
    )
    logger.info("  stop/no-stop agrees with reality: %s", _share(len(agree), len(known)))
    logger.info(
        "  when both stop, compound matches: %s", _share(len(compound_match), len(both_stop))
    )
    if distances:
        logger.info(
            "  when both stop, laps from the real in-lap: median %.1f, mean %.1f, within %d: %s",
            median(distances),
            mean(distances),
            CLOSE_TIMING_LAPS,
            _share(sum(d <= CLOSE_TIMING_LAPS for d in distances), len(distances)),
        )
    logger.info("  engine compounds: %s", dict(Counter(e.engine_compound for e in evaluations)))
    logger.info("  real next compounds: %s", dict(Counter(e.real_compound for e in real_stop)))
    logger.info(
        "  when both stop, real in-lap inside the window: %s", _share(len(covered), len(both_stop))
    )
    if widths:
        logger.info(
            "  window width: mean %.1f laps, one-lap windows %s",
            mean(widths),
            _share(sum(w == 1 for w in widths), len(widths)),
        )
    logger.info(
        "  answer on the search limit: %s", _share(sum(e.at_search_limit for e in evaluations), n)
    )
    logger.info(
        "  two-compound rule broken (only one dry compound used so far): %s",
        _share(len(violations), len(dry_single)),
    )
    if confidences:
        logger.info("  confidence: mean %.2f", mean(confidences))


async def run(csv_path: str | None) -> list[Evaluation]:
    """Evaluate every curated race in this database and log the report.

    Args:
        csv_path: Also write one row per driver-lap here, or None.
    Returns:
        Every evaluation, all races together.
    """
    models = strategy_service._load_models()
    mae_cache = strategy_service._load_holdout_mae()
    engine = get_engine()
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            targets = await resolve_targets(db, None)
            evaluations: list[Evaluation] = []
            for target in targets:
                race_evaluations = await _evaluate_race(db, target, models, mae_cache)
                _report(
                    f"{target.race_name}, laps {target.start_lap}-{target.end_lap}",
                    race_evaluations,
                )
                evaluations.extend(race_evaluations)
    finally:
        await engine.dispose()

    _report("All curated races", evaluations)
    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(asdict(evaluations[0])))
            writer.writeheader()
            writer.writerows(asdict(e) for e in evaluations)
        logger.info("Wrote %d rows to %s", len(evaluations), csv_path)
    return evaluations


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", help="Also write one row per driver-lap to this CSV file.")
    args = parser.parse_args()
    asyncio.run(run(args.csv))


if __name__ == "__main__":
    main()
