"""Copy precomputed Demo Replay data from one database to another, translating ids.

Why this exists: the tyre-degradation models resolve each driver through a
lookup table keyed by DRIVER UUID, built from the database they were trained
on (the local one). Production Supabase assigned its own UUIDs when the races
were ingested there, so none of its drivers is in that table and every
prediction computed against Supabase would silently use a stand-in driver code
(docs/internal/demo-deployment-plan-2026.md, Day 3 CP4). Until that lookup is
keyed by something stable, the replay data is precomputed and validated
against the local database and copied here.

Every id is translated: drivers by their 3-letter code, sessions by (season,
round, "R") via precompute_replay.resolve_targets — including the ids stored
inside each gap snapshot's JSON. The write reuses precompute_replay.
write_session, so each race is replaced in ONE transaction (the target's old
predictions and replay rows deleted, the copies inserted).

Connection strings are read from environment variables named on the command
line, never passed as arguments. A postgresql:// URL is converted to the
+asyncpg driver form.

Run via:
    python -m backend.scripts.copy_replay_precompute \
        --source-url-env DATABASE_URL --target-url-env SUPABASE_DIRECT_URL --dry-run
"""

import argparse
import asyncio
import logging
import os
import sys
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.models.driver import Driver
from backend.models.replay import (
    ReplayAlertEvent,
    ReplayCarNumber,
    ReplayGapSnapshot,
    ReplayLapTiming,
)
from backend.models.strategy import StrategyPrediction
from backend.models.telemetry import LapData
from backend.scripts.precompute_replay import (
    CarNumber,
    CuratedTarget,
    LapTiming,
    SessionPrecompute,
    resolve_targets,
    write_session,
)
from backend.services.alert_service import LapUndercutAlert

logger = logging.getLogger(__name__)

# StrategyPrediction columns that identify the row rather than carry the
# prediction; write_session sets them itself.
_PREDICTION_IDENTITY_COLUMNS = frozenset(
    {"id", "session_id", "driver_id", "predicted_at", "lap_number", "created_at"}
)


def asyncpg_url(url: str) -> str:
    """The same database URL with the asyncpg driver, as create_async_engine needs."""
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    return url


def build_driver_map(
    source_codes: dict[uuid.UUID, str], target_ids: dict[str, uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """Source driver id -> target driver id, matched by driver code.

    Args:
        source_codes: Source database's driver id -> code.
        target_ids: Target database's code -> driver id.
    Returns:
        The id map for every source driver.
    Raises:
        ValueError: a source driver's code has no driver in the target.
    """
    missing = sorted(code for code in source_codes.values() if code not in target_ids)
    if missing:
        raise ValueError(f"No driver in the target database for code(s): {', '.join(missing)}")
    return {driver_id: target_ids[code] for driver_id, code in source_codes.items()}


def translate_snapshot(
    payload: dict[str, Any],
    driver_map: dict[uuid.UUID, uuid.UUID],
    target_session_id: uuid.UUID,
) -> dict[str, Any]:
    """A gap-snapshot payload with its session id and every entry's driver id translated.

    Raises:
        KeyError: an entry's driver is not in driver_map.
    """
    return {
        **payload,
        "session_id": str(target_session_id),
        "gaps": [
            {**entry, "driver_id": str(driver_map[uuid.UUID(entry["driver_id"])])}
            for entry in payload["gaps"]
        ],
    }


async def _driver_codes(db: AsyncSession) -> dict[uuid.UUID, str]:
    return {row.id: row.code for row in (await db.execute(select(Driver.id, Driver.code))).all()}


async def _race_driver_codes(
    db: AsyncSession, session_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Driver id -> code for the drivers who took part in these sessions only.

    The local database also holds every 2018-2025 driver, most of whom were
    never loaded into production; only these races' drivers need a match.
    """
    query = (
        select(Driver.id, Driver.code)
        .join(LapData, LapData.driver_id == Driver.id)
        .where(LapData.session_id.in_(session_ids))
        .distinct()
    )
    return {row.id: row.code for row in (await db.execute(query)).all()}


async def load_translated(
    source_db: AsyncSession,
    source: CuratedTarget,
    target: CuratedTarget,
    driver_map: dict[uuid.UUID, uuid.UUID],
) -> SessionPrecompute:
    """Read one race's precomputed rows from the source, with ids translated to the target."""
    session_id = source.session_id
    result = SessionPrecompute(target=target)

    prediction_rows = (
        (
            await source_db.execute(
                select(StrategyPrediction).where(StrategyPrediction.session_id == session_id)
            )
        )
        .scalars()
        .all()
    )
    columns = [c.key for c in StrategyPrediction.__table__.columns]
    for row in prediction_rows:
        if row.lap_number is None:
            continue  # a stray pre-migration row; the precompute never writes these
        values = {
            column: getattr(row, column)
            for column in columns
            if column not in _PREDICTION_IDENTITY_COLUMNS
        }
        result.predictions.append((driver_map[row.driver_id], row.lap_number, values))

    for timing in (
        await source_db.execute(
            select(ReplayLapTiming).where(ReplayLapTiming.session_id == session_id)
        )
    ).scalars():
        result.lap_timings.append(
            LapTiming(
                driver_map[timing.driver_id],
                timing.lap_number,
                timing.lap_start_seconds,
                timing.lap_end_seconds,
            )
        )

    for snapshot in (
        await source_db.execute(
            select(ReplayGapSnapshot).where(ReplayGapSnapshot.session_id == session_id)
        )
    ).scalars():
        result.gap_snapshots[snapshot.lap_number] = translate_snapshot(
            snapshot.gaps, driver_map, target.session_id
        )

    for alert in (
        await source_db.execute(
            select(ReplayAlertEvent).where(ReplayAlertEvent.session_id == session_id)
        )
    ).scalars():
        if alert.rival_driver_id is None or alert.score is None:
            raise ValueError(f"Alert on lap {alert.lap_number} has no rival or score")
        result.alerts.append(
            LapUndercutAlert(
                lap_number=alert.lap_number,
                alert_type=alert.alert_type,
                driver_id=driver_map[alert.driver_id],
                rival_driver_id=driver_map[alert.rival_driver_id],
                message=alert.message,
                score=alert.score,
            )
        )

    for car in (
        await source_db.execute(
            select(ReplayCarNumber).where(ReplayCarNumber.session_id == session_id)
        )
    ).scalars():
        result.car_numbers.append(CarNumber(driver_map[car.driver_id], car.car_number))

    return result


def _summary(result: SessionPrecompute) -> str:
    target = result.target
    return (
        f"{target.race_name} -> target session {target.session_id}: "
        f"{len(result.predictions)} prediction(s), {len(result.alerts)} alert(s), "
        f"{len(result.lap_timings)} lap timing(s), {len(result.gap_snapshots)} gap snapshot(s), "
        f"{len(result.car_numbers)} car number(s)"
    )


async def run(source_url: str, target_url: str, dry_run: bool) -> list[SessionPrecompute]:
    """Copy every curated race's precomputed data from source to target (unless dry_run).

    Raises:
        ValueError: source and target are the same database, a curated race is
            in one database but not the other, or a driver cannot be matched.
    """
    if asyncpg_url(source_url) == asyncpg_url(target_url):
        raise ValueError("Source and target are the same database")

    engines = [
        create_async_engine(asyncpg_url(url), connect_args={"statement_cache_size": 0})
        for url in (source_url, target_url)
    ]
    source_factory, target_factory = (
        async_sessionmaker(engine, expire_on_commit=False) for engine in engines
    )
    results: list[SessionPrecompute] = []
    try:
        async with source_factory() as source_db, target_factory() as target_db:
            source_targets = await resolve_targets(source_db, None)
            target_by_race = {
                (t.season, t.round_number): t for t in await resolve_targets(target_db, None)
            }
            driver_map = build_driver_map(
                await _race_driver_codes(source_db, [t.session_id for t in source_targets]),
                {code: driver_id for driver_id, code in (await _driver_codes(target_db)).items()},
            )
            for source in source_targets:
                target = target_by_race.get((source.season, source.round_number))
                if target is None:
                    raise ValueError(f"{source.race_name} is not in the target database")
                results.append(await load_translated(source_db, source, target, driver_map))
            await source_db.rollback()
            await target_db.rollback()

        for result in results:
            if dry_run:
                logger.info("DRY RUN, not written: %s", _summary(result))
            else:
                async with target_factory() as target_db:
                    await write_session(target_db, result)
                logger.info("Written: %s", _summary(result))
    finally:
        for engine in engines:
            await engine.dispose()
    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy precomputed Demo Replay data between databases, translating ids."
    )
    parser.add_argument("--source-url-env", required=True, help="Env var with the source DB URL")
    parser.add_argument("--target-url-env", required=True, help="Env var with the target DB URL")
    parser.add_argument("--dry-run", action="store_true", help="Read and translate, write nothing")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _parse_args()
    urls = [os.environ.get(name) for name in (args.source_url_env, args.target_url_env)]
    if not all(urls):
        logger.error("Both --source-url-env and --target-url-env must name set variables")
        sys.exit(2)
    source_url, target_url = (url or "" for url in urls)
    try:
        results = asyncio.run(run(source_url, target_url, args.dry_run))
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    for result in results:
        print(("DRY RUN " if args.dry_run else "") + _summary(result), flush=True)


if __name__ == "__main__":
    main()
