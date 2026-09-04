"""Recorded-feed harness for the live ingestion path (core-feature-rebuild
Checkpoint 7).

No live race is available on demand, so this validates Checkpoint 1's fix
(ingest_live_session.py's tyre_age_laps/position derivation) the next best
way: replay a REAL, already-ingested race (read-only from the local DB) as a
realistic sequence of F1 live-timing SignalR messages, feed them through the
UNMODIFIED production F1SignalRIngestor (the exact class ingest_live_session.py
runs against a real websocket connection), and diff its derived tyre_age_laps/
position against that same session's own ground-truth values — sourced from
ingest_historical.py's authoritative FastF1 TyreLife/Position data, not this
project's own live-derivation logic, so this is a genuine independent check,
not the code grading its own homework.

Scope: this harness validates ONLY Checkpoint 1 (the ingestor's own per-lap
derivation) — process_lap.delay/run_strategy_prediction.delay are monkeypatched
to just capture the dispatched raw_lap dict, not run the real Celery/DB
pipeline. Checkpoints 2-4 (recommendation engine, explanation, persistence)
already have their own dedicated integration coverage against a real Celery
dispatch + real DB (tests/integration/test_live_prediction_pipeline.py's
test_live_prediction_pipeline_populates_recommendation_fields) — duplicating
that here would test the same thing twice while adding nothing to Checkpoint
1's own parity question.

What this harness does NOT cover, and cannot cover without a real live race:
- The actual websocket/SignalR transport, reconnect/backoff logic, and F1TV
  auth token handling (F1SignalRIngestor.start/_build_connection) — untouched
  by Checkpoint 1, and only exercisable against F1's real feed.
- Genuine live message TIMING/interleaving/partial-diff quirks (a real message
  rarely carries a full snapshot; this harness's synthesized messages are
  cleaner than the real feed's — see _build_timing_data_message's own note).
- CarData.z/Position.z (telemetry gauges/circuit map) — Checkpoint 1 did not
  touch these handlers.
This is why CLAUDE.md's Current Project Phase must carry an explicit follow-up:
re-verify this same parity against a REAL live race connection at the next
race weekend, not just this recorded-feed harness.

Nothing here writes to S3, the database, or promotes a model.

Run via: python -m backend.scripts.verify_live_feed_parity
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.telemetry import LapData, TireStint
from backend.scripts import ingest_live_session
from backend.workers.prediction_worker import run_strategy_prediction
from backend.workers.telemetry_worker import process_lap

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Same real session used throughout this rebuild for the pit_predictor
# trajectory check (Checkpoint 6) — Belgian GP 2026 Round 10, a fully
# session_elapsed_seconds-backfilled, zero-gap session (22 drivers, 44 laps,
# confirmed via a direct DB check: 872/872 lap_data rows carry a real
# session_elapsed_seconds — no NULLs to complicate gap synthesis).
DEFAULT_SESSION_ID = "da57b9fd-4976-4fce-91a1-c7d0aac9c619"
DEFAULT_SEASON = 2026
DEFAULT_ROUND = 10


@dataclass
class _DriverLaps:
    driver_id: Any
    code: str
    car_number: str
    laps: list[Any] = field(default_factory=list)  # LapData rows, sorted by lap_number
    # start_lap -> 0-based stint index (stint_number - 1), from TireStint.
    # The 0-based index is what must actually be SENT in a synthesized
    # TimingAppData message — see _handle_timing_app_data's own dedup guard
    # (_car_last_stint_index) further down for why sending anything other
    # than the real index silently breaks stint-change tracking after the
    # first stint.
    stint_index_by_start_lap: dict[int, int] = field(default_factory=dict)


async def _fetch_session_data(session_id: str) -> dict[str, _DriverLaps]:
    """Load one session's real LapData/TireStint rows, keyed by driver.

    Returns:
        {driver_id: _DriverLaps}, car_number assigned deterministically
        (position in the sorted-by-code driver list) — real FIA car numbers
        aren't stored anywhere in this schema (see module note), and the
        ingestor only ever uses whatever car_number_to_driver_id map it's
        constructed with, so a synthetic-but-stable number is equivalent.
    """
    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    async with session_factory() as db:
        laps_result = await db.execute(
            select(LapData, Driver.code)
            .join(Driver, LapData.driver_id == Driver.id)
            .where(LapData.session_id == session_id)
            .order_by(Driver.code, LapData.lap_number)
        )
        rows = laps_result.all()

        stints_result = await db.execute(
            select(TireStint.driver_id, TireStint.start_lap, TireStint.stint_number).where(
                TireStint.session_id == session_id
            )
        )
        stint_rows = stints_result.all()

    by_driver: dict[Any, _DriverLaps] = {}
    skipped_phantom_rows = 0
    for lap, code in rows:
        # A row with BOTH position AND lap_time_seconds NULL never carries a
        # real classified result (confirmed live: Belgian GP 2026 R10 has
        # exactly one such row — RUS, lap 1 only, no further laps — a FastF1
        # grid/formation-lap artifact for a driver who never actually raced,
        # not a live-parity concern). Its session_elapsed_seconds can still
        # be populated and, by coincidence, tie the real leader's — feeding
        # it into GapToLeader ranking creates a phantom extra entry that
        # shifts every genuine driver's position by one. A real live feed
        # would never have meaningfully tracked this car either, so it's
        # excluded from the synthesized feed entirely, not just from the
        # ground-truth comparison (which already skips a NULL position via
        # its own "if truth.position is not None" check).
        if lap.position is None and lap.lap_time_seconds is None:
            skipped_phantom_rows += 1
            continue
        if lap.driver_id not in by_driver:
            car_number = str(len(by_driver) + 1)
            by_driver[lap.driver_id] = _DriverLaps(
                driver_id=lap.driver_id, code=code, car_number=car_number
            )
        by_driver[lap.driver_id].laps.append(lap)

    for driver_id, start_lap, stint_number in stint_rows:
        if driver_id in by_driver:
            by_driver[driver_id].stint_index_by_start_lap[start_lap] = stint_number - 1

    if skipped_phantom_rows:
        logger.warning(
            "Excluded %d row(s) with NULL position AND NULL lap_time_seconds "
            "(no real classified data) from the synthesized feed",
            skipped_phantom_rows,
        )

    return by_driver


def _format_lap_time(seconds: float | None) -> dict[str, str] | None:
    """Inverse of ingest_live_session._parse_lap_time — "M:SS.sss" (F1's real
    format) for >=60s, plain "SS.sss" otherwise. None (no recorded time — an
    out-lap/in-lap/SC lap) omits the field entirely, matching a real message
    that simply doesn't carry LastLapTime that tick."""
    if seconds is None:
        return None
    minutes, remainder = divmod(seconds, 60)
    if minutes >= 1:
        return {"Value": f"{int(minutes)}:{remainder:06.3f}"}
    return {"Value": f"{remainder:.3f}"}


def _format_gap(seconds: float) -> str:
    """Inverse of ingest_live_session._parse_gap_string's time-gap branch."""
    minutes, remainder = divmod(seconds, 60)
    if minutes >= 1:
        return f"+{int(minutes)}:{remainder:06.3f}"
    return f"+{remainder:.3f}"


def _build_timing_data_message(lap_by_car: dict[str, Any]) -> dict[str, Any]:
    """One TimingData message covering every car that has a row at lap_number.

    Simplification disclosed in the module docstring: bundles a whole lap's
    worth of updates into one message, whereas the real feed drip-feeds
    sector-by-sector across many small messages per lap (see
    F1SignalRIngestor's own _sector_accumulator). Harmless here — this
    harness doesn't touch sector times at all, and _handle_timing_data's
    two-pass design (gap state fully updated before any lap-completion is
    processed) already makes it order-independent for the fields this
    harness DOES send.

    GapToLeader is derived from real session_elapsed_seconds (the same
    ground-truth field the position comparison is checked against), not
    invented — the leader (min elapsed at this lap) gets no GapToLeader key
    at all, matching F1's own blank-for-the-leader convention that
    _recompute_positions' leader-disambiguation logic depends on.
    """
    elapsed_by_car = {
        car: lap.session_elapsed_seconds
        for car, lap in lap_by_car.items()
        if lap.session_elapsed_seconds is not None
    }
    leader_car = min(elapsed_by_car, key=lambda c: elapsed_by_car[c]) if elapsed_by_car else None
    leader_elapsed = elapsed_by_car.get(leader_car) if leader_car else None

    lines: dict[str, Any] = {}
    for car_number, lap in lap_by_car.items():
        entry: dict[str, Any] = {"NumberOfLaps": lap.lap_number}
        last_lap = _format_lap_time(lap.lap_time_seconds)
        if last_lap is not None:
            entry["LastLapTime"] = last_lap
        if car_number != leader_car and leader_elapsed is not None and lap.session_elapsed_seconds:
            entry["GapToLeader"] = _format_gap(lap.session_elapsed_seconds - leader_elapsed)
        lines[car_number] = entry

    return {"Lines": lines}


@dataclass
class _MismatchRecord:
    code: str
    lap_number: int
    field_name: str
    expected: Any
    actual: Any


async def run(session_id: str) -> None:
    logger.info("Fetching real session data for %s (read-only)...", session_id)
    by_driver = await _fetch_session_data(session_id)
    await get_engine().dispose()
    if not by_driver:
        raise SystemExit(f"No lap_data found for session {session_id} — nothing to verify.")

    max_lap = max(lap.lap_number for d in by_driver.values() for lap in d.laps)
    print(
        f"Loaded {len(by_driver)} driver(s), {max_lap} lap(s), "
        f"{sum(len(d.laps) for d in by_driver.values())} lap_data row(s)."
    )

    car_number_to_driver_id = {d.car_number: d.driver_id for d in by_driver.values()}
    ground_truth: dict[tuple[str, int], Any] = {
        (d.code, lap.lap_number): lap for d in by_driver.values() for lap in d.laps
    }

    ingestor = ingest_live_session.F1SignalRIngestor(
        season=DEFAULT_SEASON,
        round_number=DEFAULT_ROUND,
        session_id=session_id,
        car_number_to_driver_id=car_number_to_driver_id,
        driver_code_to_id={},
        redis_client=MagicMock(),
        no_auth=True,
    )

    dispatched: list[dict[str, Any]] = []
    # F1SignalRIngestor._handle_timing_data dispatches raw_lap["driver_id"] as
    # str(driver_id) — car_number_by_driver_id must be keyed the same way, or
    # every lookup below silently misses (a uuid.UUID never equals its own
    # str() form; same pitfall hit earlier validating pit_predictor's label
    # fix — see evaluate_pit_predictor_label_fix.py's own note on this).
    car_number_by_driver_id = {str(v): k for k, v in car_number_to_driver_id.items()}
    code_by_car_number = {d.car_number: d.code for d in by_driver.values()}

    def _capture_process_lap(raw_lap: dict[str, Any]) -> None:
        dispatched.append(raw_lap)

    # Mutates the shared Celery Task singleton's .delay directly (not via
    # ingest_live_session's own module-level names — mypy --strict disallows
    # accessing an un-re-exported attribute cross-module) — F1SignalRIngestor
    # resolves process_lap/run_strategy_prediction as globals inside
    # ingest_live_session.py at call time, and that's the SAME object
    # imported here, so this mutation is visible to it regardless of which
    # import path reached the object.
    process_lap_delay = process_lap.delay
    run_strategy_prediction_delay = run_strategy_prediction.delay
    process_lap.delay = _capture_process_lap
    run_strategy_prediction.delay = lambda raw_lap: None

    try:
        laps_by_lap_number: dict[int, dict[str, Any]] = defaultdict(dict)
        for driver in by_driver.values():
            for lap in driver.laps:
                laps_by_lap_number[lap.lap_number][driver.car_number] = lap

        stint_index_by_car = {d.car_number: d.stint_index_by_start_lap for d in by_driver.values()}
        # A driver's last real DB row is treated as their retirement lap —
        # this schema has no dedicated retirement/DNF field, and a driver
        # who simply has no further lap_data rows is exactly what a
        # retirement looks like from this data source. One tick after their
        # last row, synthesize the explicit "RETIRED" GapToLeader marker a
        # real F1 feed would send — without this, the harness never
        # exercises _update_gap_state's retirement-eviction fix at all (it
        # only fires on that specific marker), even though the underlying
        # bug it fixes is real. Confirmed live: PER/STR/ALO/BOT's positions
        # only diverged AFTER their real retirement lap once this was added.
        last_lap_by_car = {
            d.car_number: max(lap.lap_number for lap in d.laps)
            for d in by_driver.values()
            if d.laps
        }

        for lap_number in range(1, max_lap + 1):
            lap_by_car = laps_by_lap_number.get(lap_number, {})
            if not lap_by_car:
                continue

            # TimingAppData first for any car starting a new stint THIS lap —
            # matches real F1 sequencing (compound update arrives at/around
            # the out-lap, before that lap's own TimingData completion) and
            # is what _current_tyre_age's start_lap tracking depends on.
            #
            # The dict-keyed diff shape (index string -> entry) is used
            # deliberately, not the list shape — _latest_stint resolves a
            # list's stint_index as len(list)-1 regardless of which real
            # stint it is, so a naive one-entry-list-per-message send would
            # always resolve to index 0. _handle_timing_app_data's own dedup
            # guard (_car_last_stint_index.get(car, -1) >= stint_index) then
            # silently treats every stint after the first as an
            # already-seen duplicate and never updates _car_stint_start_lap
            # again — confirmed live: this was the harness's actual bug on
            # its first run, not a Checkpoint 1 defect (tyre_age_laps was
            # ~65% wrong for every driver with 2+ real stints, always by a
            # constant per-driver offset — the signature of a start_lap that
            # stopped updating after the first stint change).
            stint_starters = {
                car: idx
                for car, starts in stint_index_by_car.items()
                if (idx := starts.get(lap_number)) is not None and car in lap_by_car
            }
            if stint_starters:
                ingestor._handle_timing_app_data(
                    {
                        "Lines": {
                            car: {
                                "Stints": {
                                    str(idx): {"Compound": lap_by_car[car].compound or "UNKNOWN"}
                                }
                            }
                            for car, idx in stint_starters.items()
                        }
                    }
                )

            timing_data_message = _build_timing_data_message(lap_by_car)
            for car, last_lap in last_lap_by_car.items():
                if last_lap + 1 == lap_number:
                    timing_data_message["Lines"][car] = {"GapToLeader": "RETIRED"}
            ingestor._handle_timing_data(timing_data_message)
    finally:
        process_lap.delay = process_lap_delay
        run_strategy_prediction.delay = run_strategy_prediction_delay

    print(f"Ingestor dispatched {len(dispatched)} raw_lap event(s).")

    mismatches: list[_MismatchRecord] = []
    checked = 0
    for raw_lap in dispatched:
        car_number = car_number_by_driver_id.get(raw_lap["driver_id"])
        if car_number is None:
            continue
        code = code_by_car_number.get(car_number, "?")
        key = (code, raw_lap["lap_number"])
        truth = ground_truth.get(key)
        if truth is None:
            continue
        checked += 1
        if truth.tyre_age_laps is not None and raw_lap["tyre_age_laps"] != truth.tyre_age_laps:
            mismatches.append(
                _MismatchRecord(
                    code,
                    raw_lap["lap_number"],
                    "tyre_age_laps",
                    truth.tyre_age_laps,
                    raw_lap["tyre_age_laps"],
                )
            )
        if truth.position is not None and raw_lap["position"] != truth.position:
            mismatches.append(
                _MismatchRecord(
                    code, raw_lap["lap_number"], "position", truth.position, raw_lap["position"]
                )
            )

    if checked == 0:
        raise SystemExit(
            "No dispatched raw_lap events matched a ground-truth (code, lap_number) key — "
            "nothing was compared. This means the harness itself is broken (e.g. a car_number "
            "mapping mismatch), not that parity holds."
        )

    tyre_age_mismatches = [m for m in mismatches if m.field_name == "tyre_age_laps"]
    position_mismatches = [m for m in mismatches if m.field_name == "position"]

    print(f"\n=== Parity report ({checked} lap(s) compared against DB ground truth) ===")
    print(
        f"tyre_age_laps: {checked - len(tyre_age_mismatches)}/{checked} match "
        f"({100 * (checked - len(tyre_age_mismatches)) / checked:.1f}%)"
    )
    print(
        f"position:      {checked - len(position_mismatches)}/{checked} match "
        f"({100 * (checked - len(position_mismatches)) / checked:.1f}%)"
    )

    if mismatches:
        print(f"\n--- {len(mismatches)} mismatch(es) ---")
        for m in mismatches[:40]:
            print(
                f"{m.code} lap {m.lap_number}: {m.field_name} "
                f"expected={m.expected} actual={m.actual}"
            )
        if len(mismatches) > 40:
            print(f"... and {len(mismatches) - 40} more")

    if not tyre_age_mismatches and not position_mismatches:
        print("\nVERDICT: exact parity — live-derived values matched ground truth on every lap.")
    else:
        print(
            "\nVERDICT: mismatches found — see above. Not necessarily a regression; "
            "cross-check each against _recompute_positions' documented edge cases "
            "(leader disambiguation, retirements) before treating as a bug."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", type=str, default=DEFAULT_SESSION_ID)
    args = parser.parse_args()
    asyncio.run(run(args.session_id))


if __name__ == "__main__":
    main()
