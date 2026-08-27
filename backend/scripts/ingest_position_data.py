"""Ingest 1Hz-downsampled X/Y position data for one Demo Replay curated session.

Companion to ingest_historical.py (laps/stints only, no position telemetry —
see that script's docstring) — this backfills backend.models.telemetry.
DriverPosition for a session that's already been ingested via
ingest_historical.py, restricted to a fixed lap range. Deliberately NOT a
general-purpose "ingest positions for any session" tool: raw position
telemetry at full session scale is exactly the row-volume TimescaleDB was
deferred over (see CLAUDE.md's Deferred Telemetry Features) — this only ever
runs against the 3 curated Demo Replay sessions' fixed 10-lap windows (Day
43), never a whole race.

FastF1's per-lap Lap.get_pos_data() returns ~3-4Hz native samples with a
SessionTime column (elapsed time since the session started) alongside each
lap's own LapStartTime — timestamp_in_lap is the difference between the two,
downsampled to 1Hz by keeping one sample per whole second.

Run via:
    python -m backend.scripts.ingest_position_data --season 2026 --round 9 \
        --start-lap 43 --end-lap 52
"""

import argparse
import asyncio
import logging
import uuid
from typing import Any, cast

import pandas as pd
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tqdm import tqdm

from backend.core.database import get_engine
from backend.models.telemetry import DriverPosition
from backend.scripts._ingest_common import RoundSkippedError
from backend.scripts.ingest_historical import load_session

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_BATCH_SIZE = 1000
_VALID_SESSION_TYPES = ("R", "Q", "FP1", "FP2", "FP3")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Ingest 1Hz X/Y position data for one already-ingested session's fixed lap "
            "range — Demo Replay's Circuit Map data source (Day 43)."
        )
    )
    parser.add_argument("--season", type=int, required=True, help="Season year")
    parser.add_argument("--round", type=int, required=True, help="Round number")
    parser.add_argument(
        "--session-type", type=str, default="R", choices=_VALID_SESSION_TYPES, help="Session type"
    )
    parser.add_argument(
        "--start-lap", type=int, required=True, help="First lap to ingest (inclusive)"
    )
    parser.add_argument("--end-lap", type=int, required=True, help="Last lap to ingest (inclusive)")
    args = parser.parse_args()

    if args.start_lap < 1 or args.end_lap < args.start_lap:
        parser.error("--end-lap must be >= --start-lap, both >= 1")

    return args


def _downsample_to_1hz(pos: pd.DataFrame, lap_start_time: pd.Timedelta) -> pd.DataFrame:
    """Keep one position sample per whole second elapsed since lap start.

    Args:
        pos: Raw Lap.get_pos_data() frame (native ~3-4Hz, columns include
            SessionTime, X, Y).
        lap_start_time: This lap's Lap.LapStartTime (session-elapsed Timedelta).
    Returns:
        DataFrame with one row per integer second (timestamp_in_lap, x, y),
        keeping the first sample within each second bucket.
    """
    elapsed_seconds = (pos["SessionTime"] - lap_start_time).dt.total_seconds()
    working = pd.DataFrame(
        {"timestamp_in_lap": elapsed_seconds.round().astype(int), "x": pos["X"], "y": pos["Y"]}
    )
    return working.groupby("timestamp_in_lap", as_index=False).first()


async def _resolve_ids(
    db: AsyncSession, season: int, round_number: int, session_type: str
) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """Look up the already-ingested session_id and driver_code->id map.

    Unlike ingest_historical.py's get_or_create_* helpers, this only reads —
    a curated session must already exist (via `make ingest`) before position
    data can be attached to it.

    Args:
        db: Async DB session.
        season, round_number, session_type: Identify the session.
    Returns:
        (session_id, {driver_code: driver_id}).
    Raises:
        RoundSkippedError: No matching race/session/drivers found.
    """
    from sqlalchemy import select

    from backend.models.driver import Driver
    from backend.models.race import Race
    from backend.models.race import Session as SessionModel

    query = (
        select(SessionModel.id)
        .join(Race, SessionModel.race_id == Race.id)
        .where(
            Race.season == season,
            Race.round_number == round_number,
            SessionModel.session_type == session_type,
        )
    )
    session_id = (await db.execute(query)).scalar_one_or_none()
    if session_id is None:
        raise RoundSkippedError(
            f"No ingested session for season {season} round {round_number} ({session_type}) — "
            "run `make ingest` first"
        )

    driver_rows = (await db.execute(select(Driver.code, Driver.id))).all()
    return session_id, {row.code: row.id for row in driver_rows}


async def _replace_position_data(
    db: AsyncSession,
    session_id: uuid.UUID,
    start_lap: int,
    end_lap: int,
    rows: list[dict[str, Any]],
) -> int:
    """Delete any existing rows for this session/lap range, then bulk-insert fresh ones.

    A plain delete-then-reinsert (not ON CONFLICT DO NOTHING) since
    driver_positions has no unique constraint to conflict on — this script
    only ever targets a curated session's fixed lap range, so a full replace
    of that range is the simplest way to make re-running it idempotent.

    Args:
        db: Async DB session.
        session_id: Session being ingested.
        start_lap, end_lap: Lap range being (re)ingested.
        rows: Row dicts matching DriverPosition's columns.
    Returns:
        Number of rows inserted.
    """
    await db.execute(
        delete(DriverPosition).where(
            DriverPosition.session_id == session_id,
            DriverPosition.lap_number >= start_lap,
            DriverPosition.lap_number <= end_lap,
        )
    )

    inserted = 0
    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i : i + _BATCH_SIZE]
        result = cast(CursorResult[Any], await db.execute(pg_insert(DriverPosition).values(batch)))
        inserted += result.rowcount or 0

    await db.commit()
    return inserted


async def ingest(
    season: int, round_number: int, session_type: str, start_lap: int, end_lap: int
) -> None:
    fastf1_session = load_session(season, round_number, session_type, telemetry=True)

    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    async with session_factory() as db:
        session_id, driver_code_to_id = await _resolve_ids(db, season, round_number, session_type)

        laps = fastf1_session.laps
        window = laps[(laps["LapNumber"] >= start_lap) & (laps["LapNumber"] <= end_lap)]

        rows: list[dict[str, Any]] = []
        for _, lap in tqdm(window.iterrows(), total=len(window), desc="laps (position)"):
            driver_id = driver_code_to_id.get(lap["Driver"])
            if driver_id is None:
                logger.warning("Skipping lap for unmapped driver code '%s'", lap["Driver"])
                continue
            if pd.isna(lap["LapStartTime"]):
                logger.warning(
                    "Skipping lap %s for driver '%s' — no LapStartTime",
                    lap["LapNumber"],
                    lap["Driver"],
                )
                continue

            try:
                pos = lap.get_pos_data()
            except Exception as exc:  # noqa: BLE001 — no telemetry for this lap, skip and continue
                logger.warning(
                    "Skipping lap %s for driver '%s' — no position data: %s",
                    lap["LapNumber"],
                    lap["Driver"],
                    exc,
                )
                continue
            if pos.empty:
                continue

            downsampled = _downsample_to_1hz(pos, lap["LapStartTime"])
            for _, sample in downsampled.iterrows():
                rows.append(
                    {
                        "id": uuid.uuid4(),
                        "session_id": session_id,
                        "driver_id": driver_id,
                        "lap_number": int(lap["LapNumber"]),
                        "timestamp_in_lap": float(sample["timestamp_in_lap"]),
                        "x": float(sample["x"]),
                        "y": float(sample["y"]),
                    }
                )

        inserted = await _replace_position_data(db, session_id, start_lap, end_lap, rows)
        logger.info(
            "Season %d round %d (%s) laps %d-%d: inserted %d position row(s)",
            season,
            round_number,
            session_type,
            start_lap,
            end_lap,
            inserted,
        )

    await engine.dispose()


def main() -> None:
    args = _parse_args()
    try:
        asyncio.run(
            ingest(args.season, args.round, args.session_type, args.start_lap, args.end_lap)
        )
    except RoundSkippedError as exc:
        logger.warning("Skipping: %s", exc)


if __name__ == "__main__":
    main()
