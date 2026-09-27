"""Check that the precomputed Demo Replay data makes sense (read-only).

Run after precompute_replay.py, against whichever database DATABASE_URL points
at (the same convention as the precompute and backfill scripts). Nothing is
written.

Hard checks — any problem makes the exit code 1:
- every driver-lap in the window has exactly one prediction;
- no pit lap (the pit model's estimate, the recommendation or its window)
  lies past the race distance;
- each stored gap snapshot's running order matches lap_data's positions on
  that lap;
- no alert breaks the live pipeline's own rules: score above the threshold,
  rival directly ahead on that lap, tyres not too fresh, not too close to
  the finish.

Reported, not failed — these need judgement, not a pass mark:
- pit probability in the laps before each real pit stop in the window,
  against the laps after it and the window's average;
- how much the undercut scores move from lap to lap, and how many sit at
  exactly 0 or 1;
- which laps the alerts fall on.

Run via:
    python -m backend.scripts.validate_replay_precompute
    python -m backend.scripts.validate_replay_precompute --session-id <uuid>
"""

import argparse
import asyncio
import logging
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from statistics import mean

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.race import Session as SessionModel
from backend.models.replay import ReplayAlertEvent, ReplayGapSnapshot
from backend.models.strategy import StrategyPrediction
from backend.models.telemetry import LapData, TireStint
from backend.scripts.precompute_replay import CuratedTarget, resolve_targets
from backend.services.alert_service import (
    UNDERCUT_ALERT_MIN_LAPS_REMAINING,
    UNDERCUT_ALERT_MIN_TYRE_AGE_LAPS,
    UNDERCUT_ALERT_THRESHOLD,
)

logger = logging.getLogger(__name__)

# The pit model predicts "pits within the next 3 laps" (pit_predictor's K=3
# label), so these are the laps where its probability should be high.
PIT_APPROACH_LAPS = 3


@dataclass(frozen=True)
class LapRow:
    driver_id: uuid.UUID
    lap_number: int
    position: int | None
    tyre_age_laps: int


@dataclass(frozen=True)
class PredictionRow:
    driver_id: uuid.UUID
    lap_number: int | None
    pit_probability: float
    undercut_score: float
    optimal_pit_lap: int
    recommended_pit_lap: int | None
    window_end: int | None


@dataclass(frozen=True)
class AlertRow:
    lap_number: int
    driver_id: uuid.UUID
    rival_driver_id: uuid.UUID | None
    score: float | None
    message: str


@dataclass(frozen=True)
class PitStop:
    driver_id: uuid.UUID
    in_lap: int


@dataclass
class SessionData:
    """Everything the checks need for one curated race."""

    target: CuratedTarget
    total_laps: int | None
    laps: list[LapRow]
    predictions: list[PredictionRow]
    snapshots: dict[int, list[uuid.UUID]]
    alerts: list[AlertRow]
    pit_stops: list[PitStop]
    codes: dict[uuid.UUID, str]


@dataclass(frozen=True)
class PitProbabilityStop:
    driver_id: uuid.UUID
    in_lap: int
    before: float | None
    after: float | None


# --- Hard checks: each returns a list of problems, empty when it passes ----


def check_prediction_coverage(
    laps: list[LapRow], predictions: list[PredictionRow], start_lap: int, end_lap: int
) -> list[str]:
    """Every driver-lap in the window has exactly one prediction, and nothing else does."""
    expected = {
        (lap.driver_id, lap.lap_number) for lap in laps if start_lap <= lap.lap_number <= end_lap
    }
    seen = Counter((p.driver_id, p.lap_number) for p in predictions)
    problems = [f"no prediction for driver {d} lap {n}" for d, n in sorted(expected - set(seen))]
    problems += [
        f"{count} predictions for driver {d} lap {n}" for (d, n), count in seen.items() if count > 1
    ]
    problems += [
        f"prediction for driver {d} lap {n}, which is not a lap in the window"
        for d, n in sorted(set(seen) - expected, key=str)
    ]
    return problems


def check_pit_laps_within_race(
    predictions: list[PredictionRow], total_laps: int | None
) -> list[str]:
    """No pit estimate, recommendation or window end lies past the race distance."""
    if total_laps is None:
        return ["sessions.total_laps is not set, so pit laps cannot be bounded"]
    problems: list[str] = []
    for p in predictions:
        for label, value in (
            ("optimal_pit_lap", p.optimal_pit_lap),
            ("recommended_pit_lap", p.recommended_pit_lap),
            ("window_end", p.window_end),
        ):
            if value is not None and value > total_laps:
                problems.append(
                    f"driver {p.driver_id} lap {p.lap_number}: {label} {value} > {total_laps}"
                )
    return problems


def _order_on_lap(laps: list[LapRow], lap_number: int) -> list[uuid.UUID]:
    placed = [lap for lap in laps if lap.lap_number == lap_number and lap.position is not None]
    return [lap.driver_id for lap in sorted(placed, key=lambda lap: lap.position or 0)]


def check_snapshot_order(snapshots: dict[int, list[uuid.UUID]], laps: list[LapRow]) -> list[str]:
    """Each gap snapshot lists the cars in the same order as lap_data's positions on that lap.

    Compared on the cars present in both: a snapshot leaves out a car FastF1
    has no time for on that lap, and lap_data may lack a car the snapshot has.
    """
    problems: list[str] = []
    for lap_number, snapshot in sorted(snapshots.items()):
        stored = _order_on_lap(laps, lap_number)
        if not stored:
            continue  # the lap before the window is only loaded when present
        common = set(snapshot) & set(stored)
        if [d for d in snapshot if d in common] != [d for d in stored if d in common]:
            problems.append(f"lap {lap_number}: snapshot order differs from lap_data positions")
    return problems


def check_alert_rules(
    alerts: list[AlertRow], laps: list[LapRow], total_laps: int | None
) -> list[str]:
    """Every stored alert obeys the rules the live pipeline applies."""
    by_lap_driver = {(lap.lap_number, lap.driver_id): lap for lap in laps}
    problems: list[str] = []
    for alert in alerts:
        where = f"lap {alert.lap_number} alert '{alert.message}'"
        if alert.score is None or alert.score <= UNDERCUT_ALERT_THRESHOLD:
            problems.append(f"{where}: score {alert.score} is not above {UNDERCUT_ALERT_THRESHOLD}")
        lap = by_lap_driver.get((alert.lap_number, alert.driver_id))
        if lap is None:
            problems.append(f"{where}: the alerted driver has no lap_data row on that lap")
            continue
        if lap.tyre_age_laps < UNDERCUT_ALERT_MIN_TYRE_AGE_LAPS:
            problems.append(f"{where}: tyres only {lap.tyre_age_laps} laps old")
        if (
            total_laps is not None
            and total_laps - alert.lap_number < UNDERCUT_ALERT_MIN_LAPS_REMAINING
        ):
            problems.append(f"{where}: only {total_laps - alert.lap_number} laps left")
        order = _order_on_lap(laps, alert.lap_number)
        if alert.driver_id in order:
            index = order.index(alert.driver_id)
            ahead = order[index - 1] if index > 0 else None
            if ahead != alert.rival_driver_id:
                problems.append(f"{where}: the rival is not the car directly ahead on that lap")
    return problems


# --- Reported metrics -------------------------------------------------------


def pit_probability_around_stops(
    predictions: list[PredictionRow], pit_stops: list[PitStop]
) -> list[PitProbabilityStop]:
    """Mean pit probability over the laps before each pit stop, and the laps after it.

    "Before" is the PIT_APPROACH_LAPS completed laps leading up to the in-lap;
    "after" is the same number of laps on the new tyres. Either is None when
    the window holds none of those laps.
    """
    by_driver_lap = {(p.driver_id, p.lap_number): p.pit_probability for p in predictions}

    def _mean_over(driver_id: uuid.UUID, laps: range) -> float | None:
        values = [by_driver_lap[(driver_id, n)] for n in laps if (driver_id, n) in by_driver_lap]
        return mean(values) if values else None

    return [
        PitProbabilityStop(
            driver_id=stop.driver_id,
            in_lap=stop.in_lap,
            before=_mean_over(stop.driver_id, range(stop.in_lap - PIT_APPROACH_LAPS, stop.in_lap)),
            after=_mean_over(
                stop.driver_id, range(stop.in_lap + 1, stop.in_lap + 1 + PIT_APPROACH_LAPS)
            ),
        )
        for stop in pit_stops
    ]


@dataclass(frozen=True)
class UndercutSpread:
    drivers: int
    drivers_varying: int
    distinct_values: int
    saturated_share: float


def undercut_spread(predictions: list[PredictionRow]) -> UndercutSpread:
    """How much undercut scores move lap to lap, and how many are exactly 0 or 1."""
    by_driver: dict[uuid.UUID, set[float]] = {}
    for p in predictions:
        by_driver.setdefault(p.driver_id, set()).add(round(p.undercut_score, 3))
    scores = [p.undercut_score for p in predictions]
    return UndercutSpread(
        drivers=len(by_driver),
        drivers_varying=sum(1 for values in by_driver.values() if len(values) > 1),
        distinct_values=len({round(s, 3) for s in scores}),
        saturated_share=(sum(1 for s in scores if s in (0.0, 1.0)) / len(scores))
        if scores
        else 0.0,
    )


def pit_stops_in_window(
    stints: list[tuple[uuid.UUID, int, int | None]], start_lap: int, end_lap: int
) -> list[PitStop]:
    """Pit stops whose in-lap falls in the window.

    Args:
        stints: (driver_id, stint_number, end_lap) rows. A stint followed by
            another stint for the same driver ended with a pit stop on its
            end_lap.
        start_lap, end_lap: Inclusive window.
    Returns:
        The stops, ordered by in-lap.
    """
    numbers_by_driver: dict[uuid.UUID, set[int]] = {}
    for driver_id, stint_number, _ in stints:
        numbers_by_driver.setdefault(driver_id, set()).add(stint_number)
    stops = [
        PitStop(driver_id, stint_end)
        for driver_id, stint_number, stint_end in stints
        if stint_end is not None
        and stint_number + 1 in numbers_by_driver[driver_id]
        and start_lap <= stint_end <= end_lap
    ]
    return sorted(stops, key=lambda stop: (stop.in_lap, str(stop.driver_id)))


# --- Loading (read-only) -----------------------------------------------------


async def load_session_data(db: AsyncSession, target: CuratedTarget) -> SessionData:
    """Read everything the checks need for one curated race."""
    session_id = target.session_id
    total_laps = (
        await db.execute(select(SessionModel.total_laps).where(SessionModel.id == session_id))
    ).scalar_one()

    lap_rows = (
        await db.execute(
            select(
                LapData.driver_id, LapData.lap_number, LapData.position, LapData.tyre_age_laps
            ).where(
                LapData.session_id == session_id,
                LapData.lap_number >= target.start_lap - 1,
                LapData.lap_number <= target.end_lap,
            )
        )
    ).all()
    prediction_rows = (
        await db.execute(
            select(
                StrategyPrediction.driver_id,
                StrategyPrediction.lap_number,
                StrategyPrediction.pit_probability,
                StrategyPrediction.undercut_score,
                StrategyPrediction.optimal_pit_lap,
                StrategyPrediction.recommended_pit_lap,
                StrategyPrediction.window_end,
            ).where(StrategyPrediction.session_id == session_id)
        )
    ).all()
    snapshot_rows = (
        await db.execute(
            select(ReplayGapSnapshot.lap_number, ReplayGapSnapshot.gaps).where(
                ReplayGapSnapshot.session_id == session_id
            )
        )
    ).all()
    alert_rows = (
        await db.execute(
            select(
                ReplayAlertEvent.lap_number,
                ReplayAlertEvent.driver_id,
                ReplayAlertEvent.rival_driver_id,
                ReplayAlertEvent.score,
                ReplayAlertEvent.message,
            ).where(ReplayAlertEvent.session_id == session_id)
        )
    ).all()
    stint_rows = (
        await db.execute(
            select(TireStint.driver_id, TireStint.stint_number, TireStint.end_lap).where(
                TireStint.session_id == session_id
            )
        )
    ).all()
    code_rows = (await db.execute(select(Driver.id, Driver.code))).all()

    return SessionData(
        target=target,
        total_laps=total_laps,
        laps=[LapRow(r.driver_id, r.lap_number, r.position, r.tyre_age_laps) for r in lap_rows],
        predictions=[
            PredictionRow(
                r.driver_id,
                r.lap_number,
                r.pit_probability,
                r.undercut_score,
                r.optimal_pit_lap,
                r.recommended_pit_lap,
                r.window_end,
            )
            for r in prediction_rows
        ],
        snapshots={
            r.lap_number: [uuid.UUID(entry["driver_id"]) for entry in r.gaps["gaps"]]
            for r in snapshot_rows
        },
        alerts=[
            AlertRow(r.lap_number, r.driver_id, r.rival_driver_id, r.score, r.message)
            for r in alert_rows
        ],
        pit_stops=pit_stops_in_window(
            [(r.driver_id, r.stint_number, r.end_lap) for r in stint_rows],
            target.start_lap,
            target.end_lap,
        ),
        codes={r.id: r.code for r in code_rows},
    )


# --- Report --------------------------------------------------------------------


def report_session(data: SessionData) -> tuple[list[str], list[str]]:
    """Run every check for one race.

    Returns:
        (hard_problems, report_lines).
    """
    target = data.target
    hard = {
        "prediction coverage": check_prediction_coverage(
            data.laps, data.predictions, target.start_lap, target.end_lap
        ),
        "pit laps within the race": check_pit_laps_within_race(data.predictions, data.total_laps),
        "gap snapshot order": check_snapshot_order(data.snapshots, data.laps),
        "alert rules": check_alert_rules(data.alerts, data.laps, data.total_laps),
    }
    code = data.codes.get
    lines = [
        f"== {target.race_name} (laps {target.start_lap}-{target.end_lap} of {data.total_laps})",
        f"   rows: {len(data.predictions)} predictions, {len(data.snapshots)} gap snapshots, "
        f"{len(data.alerts)} alerts, {len(data.pit_stops)} real pit stops in the window",
    ]
    for name, problems in hard.items():
        lines.append(f"   [{'PASS' if not problems else 'FAIL'}] {name}")
        lines += [f"          - {problem}" for problem in problems[:10]]
        if len(problems) > 10:
            lines.append(f"          ... and {len(problems) - 10} more")

    window_mean = mean(p.pit_probability for p in data.predictions) if data.predictions else 0.0
    lines.append(f"   pit probability: window average {window_mean:.2f}")
    for stop in pit_probability_around_stops(data.predictions, data.pit_stops):
        before = "n/a" if stop.before is None else f"{stop.before:.2f}"
        after = "n/a" if stop.after is None else f"{stop.after:.2f}"
        lines.append(
            f"     {code(stop.driver_id, str(stop.driver_id)):<4} pits end of lap {stop.in_lap}: "
            f"before {before}, after {after}"
        )

    spread = undercut_spread(data.predictions)
    lines.append(
        f"   undercut scores: {spread.drivers_varying}/{spread.drivers} drivers vary lap to lap, "
        f"{spread.distinct_values} distinct values, {spread.saturated_share:.0%} exactly 0 or 1"
    )
    per_lap = Counter(alert.lap_number for alert in data.alerts)
    lines.append(
        "   alerts per lap: "
        + (", ".join(f"L{lap}: {n}" for lap, n in sorted(per_lap.items())) or "none")
    )
    problems = [f"{target.race_name}: {name}: {p}" for name, ps in hard.items() for p in ps]
    return problems, lines


async def run(session_id: uuid.UUID | None) -> int:
    """Report on every curated race (or one); return the number of hard problems."""
    engine = get_engine()
    total_problems = 0
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            for target in await resolve_targets(db, session_id):
                problems, lines = report_session(await load_session_data(db, target))
                print("\n".join(lines), flush=True)
                total_problems += len(problems)
            await db.rollback()
    finally:
        await engine.dispose()
    return total_problems


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Check the precomputed Demo Replay data.")
    parser.add_argument("--session-id", type=uuid.UUID, default=None)
    args = parser.parse_args()
    try:
        problems = asyncio.run(run(args.session_id))
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(2)
    print(f"\n{problems} hard problem(s)." if problems else "\nAll hard checks passed.")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
