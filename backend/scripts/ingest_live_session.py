"""Real-time F1 session ingestor.

Connects directly to F1's live timing SignalR feed (the same endpoints used
internally by fastf1.livetiming.client.SignalRClient) rather than using that
client directly: SignalRClient only dumps raw frames to a file for later
offline replay, with no per-topic callbacks, no selective subscription, and
no reconnect/backoff — none of which this ingestor can do without.

Run via: make ingest-live SEASON=2025 ROUND=1 SESSION_TYPE=R
or directly: python backend/scripts/ingest_live_session.py --season 2025 --round 1 --session-type R
or to auto-launch on the next race weekend:
    python backend/scripts/ingest_live_session.py --season 2025 --poll
"""

import argparse
import asyncio
import base64
import json
import logging
import os
import threading
import time as time_module
import zlib
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time
from typing import Any

import fastf1
import httpx
import pandas as pd
import redis
from apscheduler.schedulers.blocking import BlockingScheduler
from fastf1.internals.f1auth import get_auth_token
from signalrcore.hub_connection_builder import HubConnectionBuilder
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.core.config import get_live_timing_settings, get_ml_settings, get_redis_settings
from backend.core.database import get_engine
from backend.scripts._ingest_common import (
    get_or_create_circuit,
    get_or_create_drivers,
    get_or_create_race,
    get_or_create_session,
)
from backend.workers.prediction_worker import run_strategy_prediction
from backend.workers.telemetry_worker import process_lap

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_VALID_SESSION_TYPES = ("R", "Q", "FP1", "FP2", "FP3")

# Same endpoints fastf1.livetiming.client.SignalRClient connects to — these
# are reverse-engineered (F1 does not publish this API), kept in sync with
# FastF1's own reference implementation.
_CONNECTION_URL = "wss://livetiming.formula1.com/signalrcore"
_NEGOTIATE_URL = "https://livetiming.formula1.com/signalrcore/negotiate"
_TOPICS = ["TimingData", "CarData.z", "SessionInfo", "TrackStatus", "WeatherData"]

_MAX_BACKOFF_SECONDS = 30.0
_CONNECT_TIMEOUT_SECONDS = 15.0

# Weather changes slowly (over minutes, not seconds) relative to CarData/TimingData's
# 8s TTL, so a longer TTL here is appropriate — see CLAUDE.md Redis Cache Key Schema.
_WEATHER_KEY_TTL_SECONDS = 60


def _decode_z(payload: str) -> dict[str, Any]:
    """Decode a gzip-over-base64 '.z' channel payload from the live timing feed."""
    raw = zlib.decompress(base64.b64decode(payload), -zlib.MAX_WBITS)
    result: dict[str, Any] = json.loads(raw)
    return result


def _parse_lap_time(value: str | None) -> float | None:
    if not value:
        return None
    parts = value.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except ValueError:
        return None


def _parse_temp(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class F1SignalRIngestor:
    """Streams F1's live timing feed and dispatches lap/telemetry events."""

    def __init__(
        self,
        season: int,
        round_number: int,
        session_id: Any,
        car_number_to_driver_id: dict[str, Any],
        redis_client: redis.Redis,  # type: ignore[type-arg]
        no_auth: bool,
    ) -> None:
        self._season = season
        self._round_number = round_number
        self._session_id = session_id
        self._car_number_to_driver_id = car_number_to_driver_id
        self._redis = redis_client
        self._no_auth = no_auth

        self._laps_seen: dict[str, int] = {}
        self._connection: Any = None
        self._stopped = threading.Event()
        self._opened = threading.Event()
        self._closed = threading.Event()

    def _negotiate_headers(self) -> dict[str, str]:
        response = httpx.options(_NEGOTIATE_URL, timeout=10.0)
        return {"Cookie": f"AWSALBCORS={response.cookies['AWSALBCORS']}"}

    def _build_connection(self) -> Any:
        options = {
            "verify_ssl": True,
            "access_token_factory": None if self._no_auth else get_auth_token,
            "headers": self._negotiate_headers(),
        }
        connection = (
            HubConnectionBuilder()
            .with_url(_CONNECTION_URL, options=options)
            .configure_logging(logging.INFO)
            .build()
        )
        connection.on_open(self._on_open)
        connection.on_close(self._on_close)
        connection.on("feed", self._on_feed)
        return connection

    def _on_open(self) -> None:
        logger.info("Live timing connection established")
        self._opened.set()
        self._closed.clear()

    def _on_close(self) -> None:
        logger.warning("Live timing connection closed")
        self._opened.clear()
        self._closed.set()

    def _on_feed(self, args: list[Any]) -> None:
        if len(args) < 2:
            return
        topic, data = args[0], args[1]
        try:
            if topic == "CarData.z":
                self._handle_car_data(data)
            elif topic == "TimingData":
                self._handle_timing_data(data)
            elif topic == "WeatherData":
                self._handle_weather_data(data)
            else:
                logger.debug("Received %s message", topic)
        except Exception:
            logger.exception("Error handling %s message", topic)

    def _handle_car_data(self, payload: str) -> None:
        decoded = _decode_z(payload)
        for car_number, entry in decoded.get("Cars", {}).items():
            key = f"f1:{self._season}:{self._round_number}:car:{car_number}:latest"
            self._redis.setex(key, 8, json.dumps(entry))

    def _handle_weather_data(self, payload: dict[str, Any]) -> None:
        track_temp = _parse_temp(payload.get("TrackTemp"))
        air_temp = _parse_temp(payload.get("AirTemp"))
        if track_temp is None or air_temp is None:
            logger.debug("Incomplete WeatherData message, skipping: %s", payload)
            return

        key = f"f1:{self._season}:{self._round_number}:weather:latest"
        self._redis.setex(
            key,
            _WEATHER_KEY_TTL_SECONDS,
            json.dumps({"track_temp": track_temp, "air_temp": air_temp}),
        )

    def _handle_timing_data(self, payload: dict[str, Any]) -> None:
        for car_number, entry in payload.get("Lines", {}).items():
            laps_completed = entry.get("NumberOfLaps")
            if laps_completed is None or laps_completed <= self._laps_seen.get(car_number, 0):
                continue
            self._laps_seen[car_number] = laps_completed

            driver_id = self._car_number_to_driver_id.get(car_number)
            if driver_id is None:
                logger.warning("Skipping lap for unmapped car number %s", car_number)
                continue

            last_lap = entry.get("LastLapTime") or {}
            sectors = entry.get("Sectors") or {}
            raw_lap = {
                "session_id": str(self._session_id),
                "driver_id": str(driver_id),
                "lap_number": int(laps_completed),
                "lap_time_seconds": _parse_lap_time(last_lap.get("Value")),
                "compound": "UNKNOWN",
                "tyre_age_laps": 0,
                "is_valid": True,
                "sector1_seconds": _parse_lap_time((sectors.get("0") or {}).get("Value")),
                "sector2_seconds": _parse_lap_time((sectors.get("1") or {}).get("Value")),
                "sector3_seconds": _parse_lap_time((sectors.get("2") or {}).get("Value")),
            }
            process_lap.delay(raw_lap)
            run_strategy_prediction.delay(raw_lap)

    def start(self) -> None:
        """Connect and stream until stop() is called, reconnecting with backoff on drops."""
        backoff = 1.0
        while not self._stopped.is_set():
            self._opened.clear()
            self._closed.clear()
            try:
                self._connection = self._build_connection()
                self._connection.start()
                if not self._opened.wait(timeout=_CONNECT_TIMEOUT_SECONDS):
                    raise TimeoutError("Timed out waiting for live timing connection to open")
                self._connection.send("Subscribe", [_TOPICS])
            except Exception:
                logger.exception("Failed to establish live timing connection")
                time_module.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
                continue

            backoff = 1.0
            self._closed.wait()
            if self._stopped.is_set():
                break
            logger.info("Reconnecting in %.0fs", backoff)
            time_module.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

    def stop(self) -> None:
        self._stopped.set()
        self._closed.set()
        if self._connection is not None:
            self._connection.stop()


async def _resolve_context(
    season: int, round_number: int, session_type: str
) -> tuple[Any, dict[str, Any]]:
    """Resolve the DB session_id and car-number->driver_id map for a live session.

    Args:
        season: Season year.
        round_number: Round number within the season.
        session_type: FastF1 session type code (R, Q, FP1, FP2, FP3).
    Returns:
        Tuple of (session_id, {car_number: driver_id}).
    """
    settings = get_ml_settings()
    os.makedirs(settings.fastf1_cache_dir, exist_ok=True)
    fastf1.Cache.enable_cache(settings.fastf1_cache_dir)

    fastf1_session = fastf1.get_session(season, round_number, session_type)
    fastf1_session.load(laps=False, telemetry=False, weather=False, messages=False)

    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as db:
        circuit = await get_or_create_circuit(db, fastf1_session.event["Location"])
        race = await get_or_create_race(
            db,
            season=season,
            round_number=round_number,
            circuit_id=circuit.id,
            race_date=fastf1_session.event["EventDate"].date(),
        )
        session_row = await get_or_create_session(
            db,
            race_id=race.id,
            session_type=session_type,
            session_date=fastf1_session.event["EventDate"].date(),
        )
        await db.commit()

        driver_code_to_id = await get_or_create_drivers(db, fastf1_session)
        await db.commit()

    car_number_to_driver_id: dict[str, Any] = {}
    for driver_number in fastf1_session.drivers:
        try:
            info = fastf1_session.get_driver(driver_number)
        except Exception as exc:  # noqa: BLE001 — per-driver skip, unresolvable car number
            logger.warning("Skipping unresolvable driver number %s: %s", driver_number, exc)
            continue
        driver_id = driver_code_to_id.get(info.get("Abbreviation"))
        if driver_id is not None:
            car_number_to_driver_id[str(driver_number)] = driver_id

    await engine.dispose()
    return session_row.id, car_number_to_driver_id


def run_live_ingestor(
    season: int,
    round_number: int,
    session_type: str,
    no_auth: bool,
    max_duration: timedelta = timedelta(hours=3),
) -> None:
    """Resolve DB context and stream one live session until it ends or max_duration elapses.

    Args:
        season: Season year.
        round_number: Round number within the season.
        session_type: FastF1 session type code (R, Q, FP1, FP2, FP3).
        no_auth: Connect without F1TV authentication (partial/best-effort data).
        max_duration: Safety cap on how long to stream before stopping.
    Returns:
        None.
    """
    session_id, car_number_to_driver_id = asyncio.run(
        _resolve_context(season, round_number, session_type)
    )
    redis_client: redis.Redis = redis.Redis.from_url(  # type: ignore[type-arg]
        get_redis_settings().redis_url, decode_responses=True
    )

    # telemetry_service.get_live_lap needs the reverse of this map (driver_id ->
    # car_number) to resolve the f1:{season}:{round}:car:{car_number}:latest key
    # from an API-facing driver_id — persist it here since car_number_to_driver_id
    # itself only lives in this process's memory. TTL matches max_duration: the
    # mapping is only valid for as long as this ingestor session runs.
    car_number_ttl = int(max_duration.total_seconds())
    for car_number, mapped_driver_id in car_number_to_driver_id.items():
        redis_client.setex(
            f"f1:{season}:{round_number}:driver:{mapped_driver_id}:car_number",
            car_number_ttl,
            car_number,
        )

    ingestor = F1SignalRIngestor(
        season=season,
        round_number=round_number,
        session_id=session_id,
        car_number_to_driver_id=car_number_to_driver_id,
        redis_client=redis_client,
        no_auth=no_auth,
    )

    timer = threading.Timer(max_duration.total_seconds(), ingestor.stop)
    timer.daemon = True
    timer.start()
    try:
        ingestor.start()
    finally:
        timer.cancel()
        redis_client.close()


_SESSION_TYPE_TO_ERGAST_COLUMNS = {
    "FP1": ("fp1Date", "fp1Time"),
    "FP2": ("fp2Date", "fp2Time"),
    "FP3": ("fp3Date", "fp3Time"),
    "Q": ("qualifyingDate", "qualifyingTime"),
    "R": ("raceDate", "raceTime"),
}
_AUTO_LAUNCH_WINDOW = timedelta(minutes=10)


def _combine_date_time(date_val: Any, time_val: Any) -> datetime | None:
    if pd.isna(date_val):
        return None
    if pd.isna(time_val):
        time_val = dt_time(0, 0)
    return datetime.combine(date_val, time_val, tzinfo=UTC)


def _find_upcoming_session(season: int) -> tuple[int, str, datetime] | None:
    """Find the next F1 session of any type starting within the auto-launch window.

    Args:
        season: Season year to check against Ergast's race schedule.
    Returns:
        (round_number, session_type, session_start_utc), or None if nothing
        starts soon.
    """
    from fastf1.ergast import Ergast

    schedule = Ergast().get_race_schedule(season)
    now = datetime.now(UTC)

    for _, race in schedule.iterrows():
        for session_type, (date_col, time_col) in _SESSION_TYPE_TO_ERGAST_COLUMNS.items():
            if date_col not in race or time_col not in race:
                continue
            start = _combine_date_time(race[date_col], race[time_col])
            if start is not None and now <= start <= now + _AUTO_LAUNCH_WINDOW:
                return int(race["round"]), session_type, start

    return None


def _run_scheduler(season: int, no_auth: bool) -> None:
    """Poll Ergast's race schedule hourly and auto-launch the ingestor for the next session.

    Args:
        season: Season year to monitor.
        no_auth: Passed through to the live timing client.
    Returns:
        None. Runs until interrupted.
    """

    def _check() -> None:
        upcoming = _find_upcoming_session(season)
        if upcoming is None:
            logger.info("No session starting within %s", _AUTO_LAUNCH_WINDOW)
            return
        round_number, session_type, start = upcoming
        logger.info(
            "Auto-launching live ingestor: round %d (%s), starts %s",
            round_number,
            session_type,
            start,
        )
        run_live_ingestor(season, round_number, session_type, no_auth)

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(_check, "interval", hours=1, next_run_time=datetime.now(UTC))
    scheduler.start()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real-time FastF1 live timing ingestor.")
    parser.add_argument("--season", type=int, required=True, help="Season year")
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--round", type=int, help="Round number — launch immediately")
    mode_group.add_argument(
        "--poll",
        action="store_true",
        help="Poll hourly and auto-launch on the next race weekend session",
    )
    parser.add_argument(
        "--session-type", type=str, choices=_VALID_SESSION_TYPES, help="Required with --round"
    )
    parser.add_argument(
        "--auth",
        action="store_true",
        help="Use authenticated F1TV live timing (requires a cached subscription token)",
    )
    args = parser.parse_args()

    if args.round is not None and args.session_type is None:
        parser.error("--session-type is required with --round")

    return args


def main() -> None:
    args = _parse_args()
    no_auth = not (args.auth or get_live_timing_settings().f1tv_authenticated)

    if args.poll:
        _run_scheduler(args.season, no_auth)
    else:
        run_live_ingestor(args.season, args.round, args.session_type, no_auth)


if __name__ == "__main__":
    main()
