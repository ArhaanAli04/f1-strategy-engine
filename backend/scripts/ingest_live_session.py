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
import re
import threading
import time as time_module
import zlib
from datetime import UTC, datetime, timedelta
from typing import Any

import fastf1
import httpx
import redis
from apscheduler.schedulers.blocking import BlockingScheduler
from fastf1.internals.f1auth import get_auth_token
from signalrcore.hub_connection_builder import HubConnectionBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.core.config import get_live_timing_settings, get_ml_settings, get_redis_settings
from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.scripts._ingest_common import (
    SESSION_TYPE_TO_ERGAST_COLUMNS,
    combine_ergast_date_time,
    get_or_create_circuit,
    get_or_create_drivers,
    get_or_create_race,
    get_or_create_session,
    resolve_scheduled_start,
)
from backend.workers.prediction_worker import run_strategy_prediction
from backend.workers.telemetry_worker import process_lap, record_tire_stint

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_VALID_SESSION_TYPES = ("R", "Q", "FP1", "FP2", "FP3")

# Same endpoints fastf1.livetiming.client.SignalRClient connects to — these
# are reverse-engineered (F1 does not publish this API), kept in sync with
# FastF1's own reference implementation.
_CONNECTION_URL = "wss://livetiming.formula1.com/signalrcore"
_NEGOTIATE_URL = "https://livetiming.formula1.com/signalrcore/negotiate"
_TOPICS = [
    "TimingData",
    "TimingAppData",
    "CarData.z",
    "Position.z",
    "SessionInfo",
    "TrackStatus",
    "WeatherData",
    "DriverList",
]

_MAX_BACKOFF_SECONDS = 30.0
_CONNECT_TIMEOUT_SECONDS = 15.0

# Weather changes slowly (over minutes, not seconds) relative to CarData/TimingData's
# 8s TTL, so a longer TTL here is appropriate — see CLAUDE.md Redis Cache Key Schema.
_WEATHER_KEY_TTL_SECONDS = 60

# Shorter than CarData's 8s: Position.z updates more frequently (the Circuit
# Map Panel's live driver dots need to look current within a couple of
# seconds, not lag behind a stale sample) — see CLAUDE.md Redis Cache Key Schema.
_POSITION_KEY_TTL_SECONDS = 3

# f1:{season}:{round}:gaps — same key telemetry_service.py's @cacheable-wrapped
# get_session_gaps() already reads/writes (CLAUDE.md Redis Cache Key Schema,
# originally TTL 8s for the DB-reconstruction fallback's own cache write).
# Writing authoritative live gaps here directly, refreshed well inside this
# TTL on every relevant TimingData message, means cache_get() always hits
# while this ingestor is running — _compute_session_gaps's DB reconstruction
# (which requires a complete lap history from lap 1 to be accurate — broken
# whenever ingestion joins mid-race or restarts, confirmed live 2026 Dutch GP:
# a 77.655s reported gap vs an actual ~11.5s) never runs while live data is
# flowing, and still serves as the fallback once this key naturally expires.
_GAPS_KEY_TTL_SECONDS = 30


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


def _as_dict(value: Any) -> dict[str, Any]:
    """Coerce a value to a dict, treating anything else (notably F1's bare-bool
    "_kf"/unchanged-field sentinels) as absent rather than crashing on .get()."""
    return value if isinstance(value, dict) else {}


def _parse_temp(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_LAPS_BEHIND_PATTERN = re.compile(r"^\+?\s*(\d+)\s*LAPS?$", re.IGNORECASE)


def _extract_string_field(raw: Any) -> str | None:
    """Unwrap a TimingData sub-field that may be a bare string or a {"Value": ...}
    dict — F1's protocol is inconsistent about which fields get which shape
    (GapToLeader is typically a bare string, IntervalToPositionAhead is
    typically wrapped like Sectors/LastLapTime elsewhere in this same payload),
    so this accepts either rather than assuming one."""
    if isinstance(raw, dict):
        raw = raw.get("Value")
    return raw if isinstance(raw, str) else None


def _parse_gap_string(value: str | None) -> tuple[float | None, int]:
    """Parse an F1 live-timing gap string into (seconds, laps_behind).

    "+1:18.234" / "1:18.234" -> (78.234, 0)
    "+0.123"                 -> (0.123, 0)
    "+1 LAP" / "+2 LAPS"     -> (None, 1) / (None, 2)
    "", "RETIRED", None, or anything else unparseable -> (None, 0) — the
    caller treats this as "no update this message", not "gap is zero".
    """
    if not value:
        return None, 0
    stripped = value.strip()
    if not stripped:
        return None, 0
    laps_match = _LAPS_BEHIND_PATTERN.match(stripped)
    if laps_match:
        return None, int(laps_match.group(1))
    seconds = _parse_lap_time(stripped)
    if seconds is not None:
        return seconds, 0
    return None, 0


class F1SignalRIngestor:
    """Streams F1's live timing feed and dispatches lap/telemetry events."""

    def __init__(
        self,
        season: int,
        round_number: int,
        session_id: Any,
        car_number_to_driver_id: dict[str, Any],
        driver_code_to_id: dict[str, Any],
        redis_client: redis.Redis,  # type: ignore[type-arg]
        no_auth: bool,
    ) -> None:
        self._season = season
        self._round_number = round_number
        self._session_id = session_id
        self._car_number_to_driver_id = car_number_to_driver_id
        self._driver_code_to_id = driver_code_to_id
        self._redis = redis_client
        self._no_auth = no_auth

        self._laps_seen: dict[str, int] = {}
        # Per-car accumulator: sector index ("0"/"1"/"2") -> seconds, populated
        # incrementally across separate TimingData messages (F1's feed is a diff
        # stream — a single message rarely carries all 3 sectors for a lap at
        # once, only whichever sector was just crossed) and cleared once a lap
        # completes and its raw_lap has been built from the accumulated values.
        self._sector_accumulator: dict[str, dict[str, float]] = {}
        # Per-car current tyre compound, kept up to date by TimingAppData and
        # read by _handle_timing_data when a lap completes.
        self._car_current_compound: dict[str, str] = {}
        # Per-car highest stint index already written to tire_stints, so a
        # repeated TimingAppData diff for the same stint doesn't create a
        # duplicate Celery dispatch (the DB insert itself is also idempotent
        # via ON CONFLICT DO NOTHING, this just avoids the redundant task).
        self._car_last_stint_index: dict[str, int] = {}
        # Per-car live standings state, parsed directly from TimingData's own
        # Position/GapToLeader/IntervalToPositionAhead fields — F1's own
        # authoritative gap computation, immune to gaps in our own recorded
        # lap history (unlike telemetry_service's DB-reconstruction fallback).
        # Keys: "position" (int), "gap_to_leader" (float|None),
        # "gap_to_ahead" (float|None), "laps_behind" (int, deficit to the car
        # immediately ahead). Published to Redis via _publish_live_gaps.
        self._car_live_gap_state: dict[str, dict[str, Any]] = {}
        self._connection: Any = None
        self._stopped = threading.Event()
        self._opened = threading.Event()
        self._closed = threading.Event()

    def _negotiate_headers(self) -> dict[str, str]:
        response = httpx.options(_NEGOTIATE_URL, timeout=10.0)
        return {"Cookie": f"AWSALBCORS={response.cookies['AWSALBCORS']}"}

    def _build_connection(self) -> Any:
        options: dict[str, Any] = {
            "verify_ssl": True,
            "headers": self._negotiate_headers(),
        }
        if not self._no_auth:
            options["access_token_factory"] = get_auth_token
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
            elif topic == "Position.z":
                self._handle_position_data(data)
            elif topic == "TimingData":
                self._handle_timing_data(data)
            elif topic == "TimingAppData":
                self._handle_timing_app_data(data)
            elif topic == "WeatherData":
                self._handle_weather_data(data)
            elif topic == "DriverList":
                self._handle_driver_list(data)
            else:
                logger.debug("Received %s message", topic)
        except Exception:
            logger.exception("Error handling %s message", topic)

    def _handle_car_data(self, payload: str) -> None:
        decoded = _decode_z(payload)
        for car_number, entry in decoded.get("Cars", {}).items():
            key = f"f1:{self._season}:{self._round_number}:car:{car_number}:latest"
            self._redis.setex(key, 8, json.dumps(entry))

    def _handle_position_data(self, payload: str) -> None:
        """Decode a Position.z frame and cache each car's latest X/Y/Z.

        Feeds the Circuit Map Panel's live driver dots (see CLAUDE.md's
        Planned Feature: Live Circuit Map). Payload shape:
        {"Position": [{"Timestamp": ..., "Entries": {car_number: {"X":...,
        "Y":..., "Z":..., "Status":...}}}]} — a list of snapshots (usually
        one per frame); only X/Y/Z are needed here, "Status" (e.g. OnTrack/
        OffTrack/Retired) is left for a future consumer if ever needed.
        """
        decoded = _decode_z(payload)
        for snapshot in decoded.get("Position", []):
            timestamp = snapshot.get("Timestamp")
            for car_number, entry in snapshot.get("Entries", {}).items():
                x, y, z = entry.get("X"), entry.get("Y"), entry.get("Z")
                if x is None or y is None:
                    continue
                key = f"f1:{self._season}:{self._round_number}:car:{car_number}:position"
                self._redis.setex(
                    key,
                    _POSITION_KEY_TTL_SECONDS,
                    json.dumps({"x": x, "y": y, "z": z, "timestamp": timestamp}),
                )

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

    def _handle_driver_list(self, payload: dict[str, Any]) -> None:
        """Resolve car_number->driver_id from the live DriverList topic.

        FastF1's REST driver_info() (used to build the initial mapping in
        _resolve_context) depends on official session/result files that are
        not yet published this early in a session — confirmed empty
        (`fastf1_session.drivers == []`) even ~20 minutes into the 2026 Dutch
        GP race. DriverList is the live SignalR feed's own car_number->Tla
        snapshot (sent once near connection start, then incrementally), so
        it's used here as the live source of truth, keyed against the same
        Driver.code values already seeded in the DB (driver_code_to_id).
        Without this, _handle_timing_data's car_number lookup stays
        permanently empty for the whole session and every lap is dropped.
        """
        car_number_ttl = 3 * 3600
        for car_number, entry in payload.items():
            if not isinstance(entry, dict):
                # F1's live timing feed mixes non-driver sentinel keys (e.g. "_kf":
                # true, a keyframe/full-snapshot marker) into this same top-level
                # dict alongside the real per-car entries — confirmed live (2026
                # Dutch GP): every reconnect resolved all 22 real drivers cleanly,
                # then crashed on this exact line hitting that sentinel's bool
                # value, killing the whole connection and forcing a reconnect loop.
                continue
            code = entry.get("Tla")
            if not code:
                continue
            driver_id = self._driver_code_to_id.get(code)
            if driver_id is None:
                logger.warning("DriverList entry for unknown code %s (car %s)", code, car_number)
                continue
            if self._car_number_to_driver_id.get(car_number) == driver_id:
                continue
            self._car_number_to_driver_id[car_number] = driver_id
            self._redis.setex(
                f"f1:{self._season}:{self._round_number}:driver:{driver_id}:car_number",
                car_number_ttl,
                car_number,
            )
            logger.info("Resolved car number %s -> driver %s (%s)", car_number, driver_id, code)

    def _handle_timing_app_data(self, payload: dict[str, Any]) -> None:
        """Track each car's current tyre compound and record new stints.

        TimingAppData is where F1's live feed actually carries Compound —
        TimingData (used for lap/sector completion) never includes it.
        Confirmed live (2026 Dutch GP): without this, every lap_data row's
        compound was the "UNKNOWN" placeholder and tire_stints stayed empty
        for the whole session.
        """
        for car_number, entry in payload.get("Lines", {}).items():
            if not isinstance(entry, dict):
                continue
            stint_index, current_stint = self._latest_stint(entry.get("Stints"))
            if current_stint is None:
                continue

            compound = current_stint.get("Compound")
            if not isinstance(compound, str) or not compound:
                continue
            compound = compound.upper()

            if self._car_current_compound.get(car_number) != compound:
                self._car_current_compound[car_number] = compound
                logger.info("Car %s compound updated -> %s", car_number, compound)

            driver_id = self._car_number_to_driver_id.get(car_number)
            if driver_id is None or stint_index is None:
                continue
            if self._car_last_stint_index.get(car_number, -1) >= stint_index:
                continue
            self._car_last_stint_index[car_number] = stint_index
            record_tire_stint.delay(
                {
                    "session_id": str(self._session_id),
                    "driver_id": str(driver_id),
                    "stint_number": stint_index + 1,
                    "compound": compound,
                    "start_lap": self._laps_seen.get(car_number, 0) + 1,
                }
            )

    @staticmethod
    def _latest_stint(stints_raw: Any) -> tuple[int | None, dict[str, Any] | None]:
        """Resolve (index, entry) for the most-recent stint in a Stints payload.

        F1 sends the full list on the initial snapshot but keys updates by
        index string on later diffs (e.g. {"1": {...}}) instead of resending
        the whole list — both shapes are handled here, and only entries that
        are actual dicts are considered (see the "_kf" bool-sentinel note on
        _handle_driver_list for why that guard matters).
        """
        if isinstance(stints_raw, list):
            dict_entries = [s for s in stints_raw if isinstance(s, dict)]
            if not dict_entries:
                return None, None
            return len(dict_entries) - 1, dict_entries[-1]
        if isinstance(stints_raw, dict):
            indices = sorted((k for k in stints_raw if k.isdigit()), key=int)
            if not indices:
                return None, None
            latest_key = indices[-1]
            entry = stints_raw[latest_key]
            return (int(latest_key), entry) if isinstance(entry, dict) else (None, None)
        return None, None

    def _handle_timing_data(self, payload: dict[str, Any]) -> None:
        for car_number, entry in payload.get("Lines", {}).items():
            if not isinstance(entry, dict):
                # Same "_kf"/bool-sentinel quirk as _handle_driver_list — F1's
                # diff-based TimingData updates can carry a bare bool for an
                # unchanged car entry instead of a real dict.
                continue

            # Accumulate whatever sector(s) this particular message carries —
            # F1's TimingData is a diff stream, so sectors 1/2/3 typically
            # arrive across separate messages as each timing point is crossed,
            # not all together in the one message where NumberOfLaps finally
            # increments. Confirmed live: only sector3 was ever non-null before
            # this accumulator, since it's the one that shares a message with
            # the lap-completion signal — sectors 1/2 had already come and gone
            # in earlier messages and were being discarded.
            sectors = _as_dict(entry.get("Sectors"))
            if sectors:
                acc = self._sector_accumulator.setdefault(car_number, {})
                for idx in ("0", "1", "2"):
                    value = _parse_lap_time(_as_dict(sectors.get(idx)).get("Value"))
                    if value is not None:
                        acc[idx] = value

            # Position/gap fields update on essentially every message,
            # independent of lap completion, so this runs unconditionally —
            # unlike the sector accumulator above it must NOT be gated behind
            # the "laps_completed increased" check below.
            self._update_gap_state(car_number, entry)

            laps_completed = entry.get("NumberOfLaps")
            if laps_completed is None or laps_completed <= self._laps_seen.get(car_number, 0):
                continue
            self._laps_seen[car_number] = laps_completed

            driver_id = self._car_number_to_driver_id.get(car_number)
            if driver_id is None:
                logger.warning("Skipping lap for unmapped car number %s", car_number)
                continue

            # NOTE: `x or {}` is wrong here — a bare `True` sentinel (same "_kf"
            # keyframe quirk) is truthy, so `True or {}` evaluates to `True`, not
            # `{}`, and the later .get() calls would crash exactly like the
            # unfiltered payload.items() case above. _as_dict is required.
            last_lap = _as_dict(entry.get("LastLapTime"))
            acc = self._sector_accumulator.pop(car_number, {})
            raw_lap = {
                "session_id": str(self._session_id),
                "driver_id": str(driver_id),
                "lap_number": int(laps_completed),
                "lap_time_seconds": _parse_lap_time(last_lap.get("Value")),
                "compound": self._car_current_compound.get(car_number, "UNKNOWN"),
                "tyre_age_laps": 0,
                "is_valid": True,
                "sector1_seconds": acc.get("0"),
                "sector2_seconds": acc.get("1"),
                "sector3_seconds": acc.get("2"),
            }
            process_lap.delay(raw_lap)
            run_strategy_prediction.delay(raw_lap)

        # Republish on every message, not only when _update_gap_state detects
        # a changed value — confirmed live (2026 Dutch GP): gating on "did
        # anything change" let the 30s Redis TTL lapse for a minute-plus at a
        # time whenever F1 resent identical gap strings for a stretch (two
        # cars holding a stable gap to 3 decimal places), which is common
        # enough that driver_service's live-session detection (checking
        # whether this key exists) intermittently and incorrectly read as
        # "not live". TimingData messages themselves arrive frequently
        # regardless of whether any single field's value changed, so this
        # keeps the TTL reliably warm at negligible extra cost.
        self._publish_live_gaps()

    def _update_gap_state(self, car_number: str, entry: dict[str, Any]) -> bool:
        """Parse Position/GapToLeader/IntervalToPositionAhead for one car.

        Returns True if this car's tracked state actually changed (kept for
        potential future use/diagnostics — the caller no longer gates on every
        single TimingData message regardless of content).
        """
        state = self._car_live_gap_state.setdefault(
            car_number,
            {"position": None, "gap_to_leader": None, "gap_to_ahead": None, "laps_behind": 0},
        )
        changed = False

        position_raw = entry.get("Position")
        if isinstance(position_raw, str) and position_raw.strip().isdigit():
            position = int(position_raw.strip())
            if state["position"] != position:
                state["position"] = position
                changed = True

        gap_to_leader, _ = _parse_gap_string(_extract_string_field(entry.get("GapToLeader")))
        if gap_to_leader is not None and state["gap_to_leader"] != gap_to_leader:
            state["gap_to_leader"] = gap_to_leader
            changed = True

        gap_to_ahead, laps_behind = _parse_gap_string(
            _extract_string_field(entry.get("IntervalToPositionAhead"))
        )
        if gap_to_ahead is not None and state["gap_to_ahead"] != gap_to_ahead:
            state["gap_to_ahead"] = gap_to_ahead
            state["laps_behind"] = 0
            changed = True
        elif laps_behind > 0 and (
            state["laps_behind"] != laps_behind or state["gap_to_ahead"] is not None
        ):
            state["gap_to_ahead"] = None
            state["laps_behind"] = laps_behind
            changed = True

        return changed

    def _publish_live_gaps(self) -> None:
        """Write the current best-known standings snapshot to Redis.

        Authoritative-from-F1 replacement for telemetry_service's DB
        cumulative-sum reconstruction — see f1:{season}:{round}:gaps's
        docstring at _GAPS_KEY_TTL_SECONDS for why that reconstruction is
        unreliable after a mid-race ingestion start or restart. Shape matches
        SessionGapsResponse exactly so the existing @cacheable-wrapped
        get_session_gaps() picks this up as a cache hit with no changes
        needed there — see telemetry_service.py.
        """
        entries: list[dict[str, Any]] = []
        for car_number, state in self._car_live_gap_state.items():
            position = state.get("position")
            driver_id = self._car_number_to_driver_id.get(car_number)
            if position is None or driver_id is None:
                continue
            entries.append(
                {
                    "driver_id": str(driver_id),
                    "lap_number": self._laps_seen.get(car_number, 0),
                    "position": position,
                    "gap_to_leader_seconds": state.get("gap_to_leader"),
                    "gap_to_ahead_seconds": state.get("gap_to_ahead"),
                    "gap_to_behind_seconds": None,
                    "laps_behind": state.get("laps_behind", 0),
                }
            )
        if not entries:
            return

        entries.sort(key=lambda e: e["position"])
        # gap_to_behind_seconds mirrors the next-placed car's gap_to_ahead —
        # only meaningful when that next car is on the same lap (not itself
        # newly lapped relative to this one), matching _compute_session_gaps'
        # symmetric treatment of the same lap-boundary case.
        for i, current in enumerate(entries):
            if i == len(entries) - 1:
                current["gap_to_behind_seconds"] = 0.0
            else:
                nxt = entries[i + 1]
                current["gap_to_behind_seconds"] = (
                    nxt["gap_to_ahead_seconds"] if nxt["laps_behind"] == 0 else None
                )
        # The leader has no car ahead — force this regardless of what F1's
        # own feed happened to send for GapToLeader/IntervalToPositionAhead
        # on the leader's own entry (typically blank, but not guaranteed).
        entries[0]["gap_to_ahead_seconds"] = 0.0
        entries[0]["laps_behind"] = 0

        # "source": "live" is the ONLY thing live_race_detection.detect_live_race
        # treats as a live race. The same f1:{season}:{round}:gaps key is also
        # written by replay_pipeline.py ("source": "replay") and, for any
        # historical session someone views, by telemetry_service.get_session_gaps'
        # @cacheable cache-aside (no "source") — neither must read as a live race.
        payload = {"session_id": str(self._session_id), "gaps": entries, "source": "live"}
        self._redis.setex(
            f"f1:{self._season}:{self._round_number}:gaps",
            _GAPS_KEY_TTL_SECONDS,
            json.dumps(payload),
        )

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
                self._connection.send(
                    "Subscribe", [_TOPICS], on_invocation=self._on_subscribe_result
                )
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

    def _on_subscribe_result(self, message: Any) -> None:
        """Handle the Subscribe RPC's completion payload.

        F1's live timing hub returns each topic's full initial snapshot as
        the *result* of the Subscribe invocation itself, not as a
        subsequent "feed" push — confirmed live (2026 Dutch GP): WeatherData
        deltas arrived fine on "feed" post-subscribe, but DriverList never
        did, because the driver list rarely changes mid-session and its only
        delivery is this one-time snapshot. Without reading it here,
        car_number_to_driver_id never gets populated and every lap is
        dropped for the rest of the session. Only DriverList is acted on
        here plus TimingAppData (safe — it only updates compound-tracking
        state, no lap-completion side effects) — TimingData's snapshot is
        NOT replayed through _handle_timing_data (that would mis-process
        already-completed laps as brand-new completions), but its Lines'
        Position field IS extracted directly via _update_gap_state: F1 only
        sends Position in this one-time snapshot, never on later incremental
        diffs — confirmed live (2026 Dutch GP): GapToLeader/
        IntervalToPositionAhead streamed correctly on "feed" pushes the whole
        time, but Position never appeared in any of them, so _publish_live_gaps
        silently never had a position to key off and never wrote anything.
        """
        try:
            result = getattr(message, "result", None)
            if not isinstance(result, dict):
                return
            logger.info("Subscribe snapshot received for topics: %s", list(result.keys()))
            driver_list = result.get("DriverList")
            if isinstance(driver_list, dict):
                self._handle_driver_list(driver_list)
            timing_app_data = result.get("TimingAppData")
            if isinstance(timing_app_data, dict):
                self._handle_timing_app_data(timing_app_data)
            timing_data = result.get("TimingData")
            if isinstance(timing_data, dict):
                gap_state_changed = False
                for car_number, entry in _as_dict(timing_data.get("Lines")).items():
                    if isinstance(entry, dict) and self._update_gap_state(car_number, entry):
                        gap_state_changed = True
                if gap_state_changed:
                    self._publish_live_gaps()
        except Exception:
            # Unlike _on_feed, this callback is invoked directly by signalrcore's
            # completion-message dispatch inside the websocket thread with no
            # try/except of its own — an uncaught exception here bubbles up
            # through the websocket-client library's dispatcher and kills the
            # whole connection (observed live: a reconnect loop every ~2s).
            logger.exception("Error handling Subscribe result")

    def stop(self) -> None:
        self._stopped.set()
        self._closed.set()
        if self._connection is not None:
            self._connection.stop()


async def _resolve_context(
    season: int, round_number: int, session_type: str
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Resolve the DB session_id and car-number->driver_id map for a live session.

    Args:
        season: Season year.
        round_number: Round number within the season.
        session_type: FastF1 session type code (R, Q, FP1, FP2, FP3).
    Returns:
        Tuple of (session_id, {car_number: driver_id}, {driver_code: driver_id}).
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
            event_name=fastf1_session.event["EventName"],
        )
        session_row = await get_or_create_session(
            db,
            race_id=race.id,
            session_type=session_type,
            session_date=fastf1_session.event["EventDate"].date(),
            scheduled_start=resolve_scheduled_start(fastf1_session.event, session_type),
        )
        await db.commit()

        driver_code_to_id = await get_or_create_drivers(db, fastf1_session)
        await db.commit()

        # get_or_create_drivers only returns codes resolved via fastf1_session.drivers,
        # which FastF1's driver_info() leaves empty this early in a session (confirmed
        # live against 2026 Dutch GP — SessionNotAvailableError even ~20 min post-start).
        # Query every DB-seeded driver code directly so _handle_driver_list's live
        # DriverList-topic resolution has the full 2026 grid to match against, not just
        # whatever subset FastF1's REST call happened to resolve.
        all_codes_result = await db.execute(select(Driver.code, Driver.id))
        for row in all_codes_result:
            driver_code_to_id.setdefault(row.code, row.id)

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
    return session_row.id, car_number_to_driver_id, driver_code_to_id


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
    session_id, car_number_to_driver_id, driver_code_to_id = asyncio.run(
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
        driver_code_to_id=driver_code_to_id,
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


_AUTO_LAUNCH_WINDOW = timedelta(minutes=10)


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
        for session_type, (date_col, time_col) in SESSION_TYPE_TO_ERGAST_COLUMNS.items():
            if date_col not in race or time_col not in race:
                continue
            start = combine_ergast_date_time(race[date_col], race[time_col])
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
