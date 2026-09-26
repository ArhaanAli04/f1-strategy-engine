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
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from backend.scripts._raw_feed_recorder import RawFeedRecorder

# All three use the redundant "as X" alias, not a plain import — tests (and
# verify_live_feed_parity.py) reach them via ingest_live_session.process_lap/
# .run_strategy_prediction/.record_tire_stint to monkeypatch .delay, which
# mypy --strict's no_implicit_reexport check otherwise flags as an
# unexported cross-module attribute (see strategy_service.py's identical
# note for the general pattern).
from backend.workers.prediction_worker import run_strategy_prediction as run_strategy_prediction
from backend.workers.telemetry_worker import process_lap as process_lap
from backend.workers.telemetry_worker import record_tire_stint as record_tire_stint
from backend.workers.telemetry_worker import update_session_total_laps as update_session_total_laps

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
    # The real scheduled race distance, broadcast live — see
    # _handle_lap_count's own docstring and docs/internal/live-race-ingestion-and-
    # strategy-gaps-monza-2026.md Issue A. FastF1's own reference SignalR
    # client subscribes to this same topic (livetiming/client.py).
    "LapCount",
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

# Status codes documented by FastF1's own track_status parser (fastf1/_api.py)
# as green/yellow (no active incident): '1' AllClear, '2' Yellow, '12'/'21'
# a transition between the two within one lap. A lap whose accumulated code
# SET is a subset of {"1", "2"} is treated as plausible here — this is a set
# check, not a literal string match against FastF1's own concatenated-string
# values, so it doesn't depend on reproducing FastF1's exact code ORDER (see
# _is_plausible_lap's docstring for why that's a deliberate simplification,
# not an oversight). Anything else observed during the lap (Safety Car '4',
# Red Flag '5', VSC '6'/'7', or an unrecognized code) marks it implausible.
_TRACK_STATUS_VALID_CODES = frozenset({"1", "2"})

# FastF1's own accuracy check (Session._check_lap_accuracy) tolerates a 0.003s
# gap between a lap's own recorded time and the sum of its three sectors.
# Live sector times are accumulated across separate diff messages rather than
# parsed from one complete historical record, so a slightly looser tolerance
# is used here to avoid false negatives from message-timing/rounding noise —
# confirmed empirically against a real live-ingested session (Italian GP 2026
# Monza): 0 of 1052 real rows exceed even a 0.05s sum-mismatch, so this
# tolerance is not expected to pass anything a stricter check would reject.
_SECTOR_SUM_TOLERANCE_SECONDS = 0.05

# Backstop for laps ingested before this ingestor has ever received a
# TrackStatus message (so _current_track_status is still at its "1" default
# and can't yet reflect a real incident) — a magnitude check independent of
# track status. 300s comfortably separates a real lap (even a very slow
# in/out/SC lap) from a red-flag-inflated session-clock artifact: confirmed
# against the same real Monza session, this exact threshold isolates the 21
# genuinely bogus rows (all ~1955s) and 0 legitimate ones.
_MAX_PLAUSIBLE_LAP_SECONDS = 300.0

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

# f1:{season}:{round}:gaps:final — the finishing order on the road, written
# once the leader has completed the race distance and kept current while the
# rest of the field finishes. The live key above lapses 30 s after the last
# timing message; without this, telemetry_service.get_session_gaps fell back to
# its DB reconstruction, which for a live-ingested race has no lap-1 times and
# showed the wrong order (Azerbaijan GP 2026: VER "winning" by 4 s over the real
# winner RUS). It also tells race_service a race has concluded, which is why it
# must never be written mid-race.
_FINAL_GAPS_KEY_TTL_SECONDS = 30 * 24 * 60 * 60

# f1:{season}:{round}:ingest_stats — a JSON snapshot of this session's counters
# (see F1SignalRIngestor.stats_snapshot), refreshed at most this often and kept
# a day, so what the live feed actually did can be read after the race.
_STATS_KEY_TTL_SECONDS = 86400
_STATS_PUBLISH_INTERVAL_SECONDS = 15.0


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


def _is_plausible_lap(
    lap_time_seconds: float | None,
    sector1_seconds: float | None,
    sector2_seconds: float | None,
    sector3_seconds: float | None,
    status_codes: set[str],
    prev_lap_status_codes: set[str],
) -> bool:
    """Whether a completed lap looks like real racing pace, not a session-
    stoppage/red-flag artifact — mirrors FastF1's own Session._check_lap_
    accuracy (the logic behind its IsAccurate column, which ingest_historical.py
    already relies on) closely enough to apply the same standard live, without
    depending on data only FastF1's post-hoc reconstruction has (PitInTime/
    PitOutTime, FastF1Generated — pit in/out lap exclusion is intentionally
    NOT attempted here, see docs/internal/live-race-ingestion-and-strategy-gaps-monza-
    2026.md Issue D's "what needs investigating" list).

    Checks, all must hold:
    - every track-status code observed during the lap is green/yellow only
      (a SET subset check against _TRACK_STATUS_VALID_CODES, not a literal
      string match — FastF1's own check accepts both '12' and '21' as the
      same green<->yellow transition, so which code was seen FIRST is not a
      meaningful distinction for plausibility, only which codes were seen at
      all)
    - lap_time_seconds and all three sector times are present (an incomplete
      lap, e.g. the very first lap of a race with no sector 1 reference
      point, can't be judged plausible either way)
    - lap_time_seconds is under _MAX_PLAUSIBLE_LAP_SECONDS — a backstop
      independent of track status, for laps ingested before this ingestor's
      first TrackStatus message ever arrives
    - the three sectors sum to lap_time_seconds within
      _SECTOR_SUM_TOLERANCE_SECONDS
    - the PREVIOUS lap had no incident code either — FastF1's own check_3:
      "first lap after safety car often has timing issues (as do all laps
      under safety car)"

    Args:
        lap_time_seconds: This lap's recorded time, or None if never received.
        sector1_seconds: This lap's sector 1 time, or None if never received.
        sector2_seconds: This lap's sector 2 time, or None if never received.
        sector3_seconds: This lap's sector 3 time, or None if never received.
        status_codes: Track-status codes observed while this lap was being run.
        prev_lap_status_codes: Track-status codes observed during this same
            car's immediately preceding lap (empty set if this is lap 1).
    Returns:
        True if this lap looks like plausible racing pace.
    """
    if (
        lap_time_seconds is None
        or sector1_seconds is None
        or sector2_seconds is None
        or sector3_seconds is None
    ):
        return False
    if lap_time_seconds > _MAX_PLAUSIBLE_LAP_SECONDS:
        return False
    if not status_codes <= _TRACK_STATUS_VALID_CODES:
        return False
    if not prev_lap_status_codes <= _TRACK_STATUS_VALID_CODES:
        return False
    sector_sum = sector1_seconds + sector2_seconds + sector3_seconds
    return abs(sector_sum - lap_time_seconds) <= _SECTOR_SUM_TOLERANCE_SECONDS


# Real F1 lapped-car gap strings, confirmed against Monza 2026's archived
# TimingData stream (docs/internal/live-race-ingestion-and-strategy-gaps-monza-2026.md
# Issue C): "1 L", "1L", "52L" — not the "+1 LAP"/"2 LAPS" spelling this
# pattern originally required, which meant a lapped car's gap was silently
# never updated again and its last numeric gap was held forever.
_LAPS_BEHIND_PATTERN = re.compile(r"^\+?\s*(\d+)\s*L(?:AP)?S?$", re.IGNORECASE)


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
    "1 L" / "1L" / "52L"     -> (None, 1) / (None, 1) / (None, 52)  (F1's real format)
    "+1 LAP" / "+2 LAPS"     -> (None, 1) / (None, 2)
    "", None, "LAP 14" (the leader's own lap counter), or anything else
    unparseable -> (None, 0) — the caller treats this as "no update this
    message", not "gap is zero".
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
        recorder: RawFeedRecorder | None = None,
    ) -> None:
        self._season = season
        self._round_number = round_number
        self._session_id = session_id
        self._car_number_to_driver_id = car_number_to_driver_id
        self._driver_code_to_id = driver_code_to_id
        self._redis = redis_client
        self._no_auth = no_auth
        # Optional raw-feed recorder (off unless RECORD_RAW_FEED) — see
        # _raw_feed_recorder.py. Only ever written to, never read back here.
        self._recorder = recorder
        # Always-on counters describing what this session's feed actually did,
        # published to Redis by publish_stats and logged at the end. Answers,
        # after a live race: did F1's Position field stream (and when), which
        # ranking source ran, how many cars were flagged out, reconnects.
        self._stats: Counter[str] = Counter()
        self._stats_published_at = 0.0

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
        # Per-car start_lap of the CURRENT stint (same value as the start_lap
        # dispatched to record_tire_stint), used by _current_tyre_age to
        # derive a real tyre_age_laps for every completed lap — previously
        # this ingestor hardcoded tyre_age_laps=0 for every lap (see
        # CLAUDE.md's core-feature-rebuild Checkpoint 1: this silently
        # starved tire_deg/pit_predictor inference of any real degradation
        # signal for the whole live path). Populated by
        # _handle_timing_app_data alongside its own start_lap computation, so
        # the two can never disagree.
        self._car_stint_start_lap: dict[str, int] = {}
        # Per-car live standings state, parsed directly from TimingData's own
        # Position/GapToLeader/IntervalToPositionAhead fields — F1's own
        # authoritative gap computation, immune to gaps in our own recorded
        # lap history (unlike telemetry_service's DB-reconstruction fallback).
        # Keys: "position" (int), "gap_to_leader" (float|None),
        # "gap_to_ahead" (float|None), "laps_behind" (int, deficit to the car
        # immediately ahead). Published to Redis via _publish_live_gaps.
        self._car_live_gap_state: dict[str, dict[str, Any]] = {}
        # Per-car F1 race-status flags, from TimingData's own boolean fields:
        # "retired" (Retired), "hidden" (ShowPosition false), "stopped"
        # (Stopped). A car with ANY of them set is left out of the ranking and
        # the published standings (see _is_out_of_ranking) — its gap state is
        # kept, not deleted, so a car that recovers from a stop (LEC stopped
        # and restarted twice at Monza 2026) rejoins with its history intact.
        self._car_status_flags: dict[str, dict[str, bool]] = {}
        # True once a Position field has arrived on a live "feed" diff (not
        # just the Subscribe snapshot). F1's own archive carries frequent
        # Position updates, but earlier live runs (2026 Dutch GP) saw it only
        # in the snapshot — so _recompute_positions trusts F1's Position for
        # ranking only after it has actually been seen streaming this session,
        # and otherwise falls back to the gap-based ranking.
        self._position_diff_seen: bool = False
        # Counts TimingData messages handled. The feed carries no timestamps,
        # so this arrival order is what says which of two cars crossed the line
        # first on the same lap — used to order lapped cars (see _rank_by_gaps).
        # Deterministic in a replay too, unlike a wall clock.
        self._message_seq: int = 0
        # Global track status (F1's TrackStatus topic is session-wide, not
        # per-car). Defaults to AllClear so laps ingested before this
        # ingestor's first TrackStatus message ever arrives aren't
        # incorrectly treated as under an incident — see
        # _MAX_PLAUSIBLE_LAP_SECONDS's own comment for the magnitude
        # backstop that covers that same gap the other way.
        self._current_track_status: str = "1"
        # Per-car accumulator of every status code observed while that car's
        # CURRENT lap has been in progress — same accumulate-across-messages
        # shape as _sector_accumulator above, cleared into a completed lap's
        # track_status the same way _sector_accumulator is. Populated
        # unconditionally on every TimingData message that mentions a car
        # (see _handle_timing_data's pass 1), not only on a status CHANGE, so
        # a car that never received a mid-lap status update still correctly
        # records whatever status was current for its whole lap.
        self._car_track_status_codes: dict[str, set[str]] = {}
        # Per-car status codes from the PREVIOUS completed lap — used by
        # _is_plausible_lap's check_3 equivalent (a lap immediately following
        # an SC/VSC/red-flag lap often has its own timing anomalies, per
        # FastF1's own Session._check_lap_accuracy). Empty set (no incident
        # assumed) for a car's first completed lap.
        self._car_prev_lap_status_codes: dict[str, set[str]] = {}
        # The real scheduled race distance, once resolved from the live
        # feed's LapCount topic — None until the first LapCount message
        # arrives. Tracked so _handle_lap_count only dispatches a DB write
        # when the value is new/changed, not on every repeated message (F1
        # can resend the same value many times over a session).
        self._total_laps_dispatched: int | None = None
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
        self._stats["connections_opened"] += 1
        if self._recorder is not None:
            self._recorder.record_event("opened")
        self._opened.set()
        self._closed.clear()

    def _on_close(self) -> None:
        logger.warning("Live timing connection closed")
        if self._recorder is not None:
            self._recorder.record_event("closed")
        self._opened.clear()
        self._closed.set()

    def _on_feed(self, args: list[Any]) -> None:
        if len(args) < 2:
            return
        topic, data = args[0], args[1]
        # Recorded before handling, so a message the handler chokes on is still
        # in the record.
        if self._recorder is not None:
            self._recorder.record(topic, data)
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
            elif topic == "TrackStatus":
                self._handle_track_status(data)
            elif topic == "LapCount":
                self._handle_lap_count(data)
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

    def _handle_track_status(self, payload: dict[str, Any]) -> None:
        """Track the session-wide flag status (green/yellow/SC/VSC/red flag).

        Was subscribed (_TOPICS already lists "TrackStatus") but never
        handled at all before this fix — the signal was arriving and being
        silently discarded on every `else: logger.debug(...)` branch of
        _on_feed. See docs/internal/live-race-ingestion-and-strategy-gaps-monza-
        2026.md Issue D: this is the signal _is_plausible_lap needs to
        distinguish a red-flag-inflated "lap" from real racing pace, and the
        reason `track_status` was NULL for every live-ingested row before
        this fix.

        Does not itself write anything to a car's accumulator — that happens
        unconditionally in _handle_timing_data's own per-car loop (see its
        comment), so a status change is picked up the next time each car's
        own TimingData entry is processed, same as every other per-car state
        in this class.
        """
        status = _extract_string_field(payload.get("Status"))
        if status is None:
            return
        if status != self._current_track_status:
            logger.info(
                "Track status changed: %s -> %s (%s)",
                self._current_track_status,
                status,
                payload.get("Message"),
            )
        self._current_track_status = status

    def _handle_lap_count(self, payload: dict[str, Any]) -> None:
        """Resolve the session's real scheduled race distance from F1's own feed.

        F1's live timing broadcasts this directly (see _TOPICS's own comment)
        as {"TotalLaps": N} — no FastF1 REST fetch works mid-race for this
        (session.total_laps is gated behind session.load(laps=True), and
        this codebase's live path deliberately loads laps=False; see
        docs/internal/live-race-ingestion-and-strategy-gaps-monza-2026.md Issue A for
        the full "why this was missing" writeup). This is a genuine race
        property, not a per-car one — unlike every other handler in this
        class, there is nothing to key by car number here.

        Dispatches update_session_total_laps at most once per distinct value
        seen (see _total_laps_dispatched's own comment) — sessions.
        total_laps only needs to be set once, and F1's own documentation of
        this field notes it "shouldn't usually change," but a genuine
        correction (a rare, real possibility, e.g. a race distance shortened
        for a red flag) is still picked up and re-dispatched here, not
        permanently locked to the first value seen.
        """
        total_laps = payload.get("TotalLaps")
        if not isinstance(total_laps, int) or total_laps <= 0:
            return
        if total_laps == self._total_laps_dispatched:
            return
        self._total_laps_dispatched = total_laps
        logger.info("Session total_laps resolved from live feed: %d", total_laps)
        update_session_total_laps.delay(str(self._session_id), total_laps)

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
            start_lap = self._laps_seen.get(car_number, 0) + 1
            # Tracked alongside the record_tire_stint dispatch below (not
            # derived separately) so _current_tyre_age's tyre_age_laps always
            # agrees with whichever start_lap this same stint was actually
            # recorded under.
            self._car_stint_start_lap[car_number] = start_lap
            record_tire_stint.delay(
                {
                    "session_id": str(self._session_id),
                    "driver_id": str(driver_id),
                    "stint_number": stint_index + 1,
                    "compound": compound,
                    "start_lap": start_lap,
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
        """Two passes over this message's Lines, not one.

        Pass 1 updates sector accumulation and gap/position tracking state
        for every car this message mentions; _recompute_positions() then
        re-ranks the WHOLE field once from that now-current state; pass 2
        checks for lap completions and builds/dispatches raw_lap, so a
        completed lap's own "position" reflects the full field snapshot this
        message just established — not a value computed before this same
        message's OTHER cars' gap updates were applied (a single message
        commonly carries updates for several cars at once, and the car whose
        lap just completed is not guaranteed to be processed last).
        """
        self._message_seq += 1
        self._stats["timing_messages"] += 1
        lines = payload.get("Lines", {})

        for car_number, entry in lines.items():
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
            # Only a Position that arrives on a live feed diff counts — the
            # Subscribe snapshot goes through _on_subscribe_result instead,
            # never this method — see _position_diff_seen's own comment.
            position_raw = entry.get("Position")
            if isinstance(position_raw, str) and position_raw.strip().isdigit():
                if not self._position_diff_seen:
                    self._stats["position_first_message_seq"] = self._message_seq
                    logger.info(
                        "F1's Position field is streaming on the live feed: first seen on "
                        "TimingData message %d (car %s) — ranking now follows it",
                        self._message_seq,
                        car_number,
                    )
                self._position_diff_seen = True

            # Record the status current AS OF this message for this car's
            # in-progress lap — unconditional (every message this car
            # appears in, not just ones carrying a Sectors update), same
            # reasoning as the gap-state update immediately above: this is
            # what lets a car whose lap saw no mid-lap status CHANGE still
            # correctly end up with whatever single status was in force the
            # whole time (see _current_track_status's own comment).
            self._car_track_status_codes.setdefault(car_number, set()).add(
                self._current_track_status
            )

        # Re-rank the whole field from the now-current gap state, once per
        # message rather than once per car — see _recompute_positions' own
        # docstring for why this replaces F1's own Position field (sent only
        # once, in the Subscribe snapshot; never on a later diff).
        self._recompute_positions()

        for car_number, entry in lines.items():
            if not isinstance(entry, dict):
                continue

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
            lap_time_seconds = _parse_lap_time(last_lap.get("Value"))
            sector1_seconds, sector2_seconds, sector3_seconds = (
                acc.get("0"),
                acc.get("1"),
                acc.get("2"),
            )

            # Pop (not peek) this car's accumulated status codes — the set
            # belongs to the lap that's completing right now; the next
            # message this car appears in re-seeds a fresh accumulator for
            # its NEXT lap (see the setdefault(...).add(...) call above).
            # Default to the current global status if this car's own
            # accumulator is somehow empty (e.g. its very first-ever
            # message coincides with lap completion), rather than an empty
            # set, which _is_plausible_lap would otherwise vacuously accept.
            status_codes = self._car_track_status_codes.pop(car_number, None) or {
                self._current_track_status
            }
            prev_status_codes = self._car_prev_lap_status_codes.get(car_number, set())
            self._car_prev_lap_status_codes[car_number] = status_codes

            raw_lap = {
                "session_id": str(self._session_id),
                "driver_id": str(driver_id),
                "lap_number": int(laps_completed),
                "lap_time_seconds": lap_time_seconds,
                "compound": self._car_current_compound.get(car_number, "UNKNOWN"),
                "tyre_age_laps": self._current_tyre_age(car_number, int(laps_completed)),
                "is_valid": _is_plausible_lap(
                    lap_time_seconds,
                    sector1_seconds,
                    sector2_seconds,
                    sector3_seconds,
                    status_codes,
                    prev_status_codes,
                ),
                "sector1_seconds": sector1_seconds,
                "sector2_seconds": sector2_seconds,
                "sector3_seconds": sector3_seconds,
                "track_status": "".join(sorted(status_codes)),
                "position": self._car_live_gap_state.get(car_number, {}).get("position"),
            }
            process_lap.delay(raw_lap)
            run_strategy_prediction.delay(raw_lap)
            self._stats["laps_dispatched"] += 1

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
        self.publish_stats()

    def _current_tyre_age(self, car_number: str, lap_number: int) -> int:
        """tyre_age_laps for a just-completed lap, from the tracked stint start_lap.

        Matches ingest_historical.py's FastF1 TyreLife convention (the first
        lap on a fresh tyre is age 1, not 0) — confirmed against real ingested
        rows: tyre_age_laps == 1 on a stint's own start_lap for the
        overwhelming majority of recorded pit-stop laps (a stint's start_lap
        IS the out-lap on the new tyre). _car_stint_start_lap is populated by
        _handle_timing_app_data whenever a new stint is detected; a car with
        no TimingAppData seen yet defaults to stint start_lap 1 — "on the
        tyre they started the session on" — same fallback spirit as
        _car_current_compound's own "UNKNOWN" default for the same gap.

        Args:
            car_number: F1 live-timing car number string.
            lap_number: The lap number that was just completed.
        Returns:
            Tyre age in laps, floored at 0 (defensive against an out-of-order
            message reporting a lap before the tracked stint's own start_lap).
        """
        start_lap = self._car_stint_start_lap.get(car_number, 1)
        return max(lap_number - start_lap + 1, 0)

    def _is_out_of_ranking(self, car_number: str) -> bool:
        """Whether F1 has flagged this car retired, hidden from the tower, or stopped.

        Confirmed against Monza 2026's archived feed: F1 never sends a
        "RETIRED" gap string. It sends booleans on the car's own entry —
        Retired, ShowPosition (false once removed from the tower) and Stopped.
        LEC got Stopped, then Retired, then ShowPosition=false; STR got
        Stopped then ShowPosition=false and NEVER Retired, so any one flag is
        enough. Stopped is included because Retired can arrive 25 minutes
        after a car actually stops (LEC), and a stationary car is not a
        realistic undercut target; it only ever flagged the three retiring
        cars in that race, never a pit stop (107 InPit events, zero Stopped).
        """
        flags = self._car_status_flags.get(car_number)
        if not flags:
            return False
        return (
            flags.get("retired", False) or flags.get("hidden", False) or flags.get("stopped", False)
        )

    def _recompute_positions(self) -> None:
        """Re-rank every car still in the race and blank the position of the rest.

        Two ranking sources, chosen per call:

        1. F1's own Position field — used once a Position update has been seen
           streaming on a live "feed" diff (_position_diff_seen) AND every
           ranked car has one AND they are pairwise distinct. Cars flagged out
           of the race are dropped first and the rest ranked densely by F1's
           order, so a retired car's slot never leaves a hole. Distinctness is
           the transient-safety check: F1 sends one car's new Position per
           message, so two cars can briefly share a value mid-overtake.
        2. Otherwise the original gap-based ranking (_rank_by_gaps): needed
           right after Subscribe, and whenever a live connection does not
           actually stream Position (earlier live runs saw it only in the
           snapshot, which is why this ingestor was gap-based to begin with).

        A car out of the ranking (_is_out_of_ranking) gets position None, so
        _publish_live_gaps leaves it out of the standings.

        Args:
            None — operates on self._car_live_gap_state.
        Returns:
            None. Mutates every known car's "position" entry in place.
        """
        ranking: list[str] = []
        for car, state in self._car_live_gap_state.items():
            if self._is_out_of_ranking(car):
                state["position"] = None
            else:
                ranking.append(car)

        f1_positions = {car: self._car_live_gap_state[car].get("f1_position") for car in ranking}
        f1_usable = (
            self._position_diff_seen
            and all(isinstance(p, int) for p in f1_positions.values())
            and len(set(f1_positions.values())) == len(ranking)
        )
        ordered = (
            sorted(ranking, key=lambda car: f1_positions[car] or 0)
            if f1_usable
            else self._rank_by_gaps(ranking)
        )
        self._stats["rankings_by_f1_position" if f1_usable else "rankings_by_gaps"] += 1
        for i, car in enumerate(ordered, start=1):
            self._car_live_gap_state[car]["position"] = i

    def _rank_by_gaps(self, cars: list[str]) -> list[str]:
        """Order cars by cumulative GapToLeader (the pre-Position-field method).

        F1 sends a blank/unparseable GapToLeader for whoever currently leads
        (so _update_gap_state never writes a value for them, and their state
        stays at its None default), which makes the leader identifiable as
        the one lead-lap car with gap_to_leader still None. Every other
        lead-lap car ranks by its own gap_to_leader, ascending. If more than
        one lead-lap car has no gap_to_leader (e.g. right after Subscribe,
        before any GapToLeader message has arrived for anyone), the leader
        can't be disambiguated from gap data alone: those cars fall back to
        their last-known position instead of a guess, which reproduces the
        Subscribe-snapshot order for the common "no gap data at all yet" case.

        Lapped cars (F1 sends "1 L", "2L"... — see _LAPS_BEHIND_PATTERN) have
        no seconds gap to the leader, so they go behind every lead-lap car,
        ordered by laps down and then by their previous position. Without
        this split a lapped car's gap_to_leader (None) would be mistaken for
        "the leader".

        Args:
            cars: Car numbers to order (already excluding out-of-race cars).
        Returns:
            The same car numbers, best position first.
        """
        state_of = self._car_live_gap_state
        lapped = [car for car in cars if state_of[car].get("laps_down", 0) > 0]
        lead_lap = [car for car in cars if car not in set(lapped)]
        with_gap = [car for car in lead_lap if state_of[car]["gap_to_leader"] is not None]
        no_gap = [car for car in lead_lap if state_of[car]["gap_to_leader"] is None]

        if len(no_gap) != 1:
            no_gap.sort(key=lambda car: state_of[car]["position"] or 999)
        lapped.sort(
            key=lambda car: (
                state_of[car]["laps_down"],
                -state_of[car].get("laps_completed", 0),
                state_of[car].get("lap_seq", 0),
                state_of[car]["position"] or 999,
            )
        )

        return [*no_gap, *sorted(with_gap, key=lambda car: state_of[car]["gap_to_leader"]), *lapped]

    def _update_gap_state(self, car_number: str, entry: dict[str, Any]) -> bool:
        """Parse Position/GapToLeader/IntervalToPositionAhead for one car.

        Returns True if this car's tracked state actually changed (kept for
        potential future use/diagnostics — the caller no longer gates on every
        single TimingData message regardless of content).

        Also records F1's own race-status booleans for the car — Retired,
        ShowPosition, Stopped (see _is_out_of_ranking for why these, and not
        a "RETIRED" gap string, are what retirement really looks like) — and
        F1's own Position value (kept separately as "f1_position" so the
        derived rank in "position" and F1's raw one never overwrite each
        other). A lapped car's GapToLeader ("1 L", "52L") sets "laps_down"
        instead of being dropped as unparseable, which used to leave its last
        numeric gap frozen for the rest of the race.
        """
        flags = self._car_status_flags.setdefault(car_number, {})
        was_out = self._is_out_of_ranking(car_number)
        for feed_key, flag in (("Retired", "retired"), ("Stopped", "stopped")):
            if isinstance(entry.get(feed_key), bool):
                flags[flag] = entry[feed_key]
        if isinstance(entry.get("ShowPosition"), bool):
            flags["hidden"] = not entry["ShowPosition"]

        state = self._car_live_gap_state.setdefault(
            car_number,
            {"position": None, "gap_to_leader": None, "gap_to_ahead": None, "laps_behind": 0},
        )
        now_out = self._is_out_of_ranking(car_number)
        changed = was_out != now_out
        if now_out and not was_out:
            self._stats["cars_flagged_out"] += 1

        laps_completed = entry.get("NumberOfLaps")
        if isinstance(laps_completed, int) and laps_completed > state.get("laps_completed", 0):
            state["laps_completed"] = laps_completed
            state["lap_seq"] = self._message_seq
            changed = True

        position_raw = entry.get("Position")
        if isinstance(position_raw, str) and position_raw.strip().isdigit():
            position = int(position_raw.strip())
            state["f1_position"] = position
            if state["position"] != position:
                state["position"] = position
                changed = True

        gap_to_leader, leader_laps_down = _parse_gap_string(
            _extract_string_field(entry.get("GapToLeader"))
        )
        if gap_to_leader is not None:
            if state["gap_to_leader"] != gap_to_leader or state.get("laps_down", 0) != 0:
                state["gap_to_leader"] = gap_to_leader
                state["laps_down"] = 0
                changed = True
        elif leader_laps_down > 0 and (
            state.get("laps_down", 0) != leader_laps_down or state["gap_to_leader"] is not None
        ):
            state["gap_to_leader"] = None
            state["laps_down"] = leader_laps_down
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
        if self._leader_has_finished(entries):
            # "source": "final", not "live": this key is not a live race to
            # live_race_detection (it only matches keys ending in ":gaps").
            final_payload = {**payload, "source": "final"}
            self._redis.setex(
                f"f1:{self._season}:{self._round_number}:gaps:final",
                _FINAL_GAPS_KEY_TTL_SECONDS,
                json.dumps(final_payload),
            )

    def _leader_has_finished(self, entries: list[dict[str, Any]]) -> bool:
        """Whether the car in first place has completed the race distance.

        Needs the real distance from LapCount (_total_laps_dispatched); until
        that is known — or in a race stopped short of it, e.g. a red flag not
        restarted — this stays False and no final standings are written.
        """
        total_laps = self._total_laps_dispatched
        return total_laps is not None and entries[0]["lap_number"] >= total_laps

    def stats_snapshot(self) -> dict[str, Any]:
        """Counters for this session: what the live feed actually did.

        Returns:
            timing_messages, laps_dispatched, rankings_by_f1_position /
            rankings_by_gaps (which ranking source ran, per TimingData
            message), cars_flagged_out, connections_opened, subscribe_snapshots,
            position_first_message_seq (the TimingData message on which F1's
            Position field first streamed on a live diff — None if it never
            did), the recording path (or None), and updated_at.
        """
        stats: dict[str, Any] = dict(self._stats)
        stats["position_first_message_seq"] = self._stats.get("position_first_message_seq")
        stats["recording"] = str(self._recorder.path) if self._recorder is not None else None
        stats["updated_at"] = datetime.now(UTC).isoformat()
        return stats

    def publish_stats(self, *, force: bool = False) -> None:
        """Write stats_snapshot to f1:{season}:{round}:ingest_stats (throttled unless forced)."""
        now = time_module.monotonic()
        if not force and now - self._stats_published_at < _STATS_PUBLISH_INTERVAL_SECONDS:
            return
        self._stats_published_at = now
        try:
            self._redis.setex(
                f"f1:{self._season}:{self._round_number}:ingest_stats",
                _STATS_KEY_TTL_SECONDS,
                json.dumps(self.stats_snapshot()),
            )
        except redis.RedisError:
            logger.warning("Could not publish ingest stats to Redis", exc_info=True)

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
        LapCount is read here too: live, F1 sends TotalLaps only in this
        snapshot ({"CurrentLap": 1, "TotalLaps": 51}); every later LapCount
        feed message carries just CurrentLap — confirmed from the Azerbaijan
        GP 2026 recording. Without it sessions.total_laps was never stored
        for that race, so pit laps went unclamped (up to lap 91 of 51) and
        the pit-window recommendation was missing on 967 of 976 predictions.
        (F1's archive is different — its first LapCount message does carry
        TotalLaps — which is why the V3 shadow race never showed this.)
        """
        try:
            result = getattr(message, "result", None)
            if not isinstance(result, dict):
                return
            logger.info("Subscribe snapshot received for topics: %s", list(result.keys()))
            self._stats["subscribe_snapshots"] += 1
            if self._recorder is not None:
                self._recorder.record_snapshot(result)
            driver_list = result.get("DriverList")
            if isinstance(driver_list, dict):
                self._handle_driver_list(driver_list)
            lap_count = result.get("LapCount")
            if isinstance(lap_count, dict):
                self._handle_lap_count(lap_count)
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


def _log_session_summary(stats: dict[str, Any]) -> None:
    """Log what the live feed did this session; warn if Position never streamed.

    That warning is the answer to the open question from
    docs/internal/live-race-ingestion-and-strategy-gaps-monza-2026.md section 7c (V5):
    if it fires after a real race, the whole race ran on the gap-based fallback.
    """
    logger.info("Live ingest summary: %s", stats)
    if stats.get("timing_messages", 0) > 0 and stats.get("position_first_message_seq") is None:
        logger.warning(
            "F1's Position field never streamed on the live feed this session "
            "(%d TimingData messages): ranking used the gap-based fallback throughout",
            stats["timing_messages"],
        )


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

    recorder: RawFeedRecorder | None = None
    live_settings = get_live_timing_settings()
    if live_settings.record_raw_feed:
        recorder = RawFeedRecorder.try_create(
            Path(live_settings.raw_feed_record_dir), season, round_number, session_type
        )
        if recorder is not None:
            logger.info("Recording the raw live feed to %s", recorder.path)

    ingestor = F1SignalRIngestor(
        season=season,
        round_number=round_number,
        session_id=session_id,
        car_number_to_driver_id=car_number_to_driver_id,
        driver_code_to_id=driver_code_to_id,
        redis_client=redis_client,
        no_auth=no_auth,
        recorder=recorder,
    )

    timer = threading.Timer(max_duration.total_seconds(), ingestor.stop)
    timer.daemon = True
    timer.start()
    try:
        ingestor.start()
    finally:
        timer.cancel()
        ingestor.publish_stats(force=True)
        _log_session_summary(ingestor.stats_snapshot())
        if recorder is not None:
            recorder.close()
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
