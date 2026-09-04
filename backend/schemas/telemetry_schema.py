import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class SectorTimeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lap_data_id: uuid.UUID
    sector: int
    time_seconds: float
    mini_sector_speeds: Any


class LapDataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    driver_id: uuid.UUID
    lap_number: int
    lap_time_seconds: float | None
    compound: str
    tyre_age_laps: int
    is_valid: bool
    sector1_seconds: float | None
    sector2_seconds: float | None
    sector3_seconds: float | None
    created_at: datetime
    sector_times: list[SectorTimeResponse] = []


class LapDataCreate(BaseModel):
    session_id: uuid.UUID
    driver_id: uuid.UUID
    lap_number: int
    lap_time_seconds: float | None = None
    compound: str
    tyre_age_laps: int = 0
    is_valid: bool = True
    sector1_seconds: float | None = None
    sector2_seconds: float | None = None
    sector3_seconds: float | None = None
    # Optional: historical ingestion has always populated LapData.position
    # directly from FastF1's own Position column. The live ingestor
    # (ingest_live_session.py) previously never set it at all, leaving every
    # live-ingested row NULL — see CLAUDE.md's core-feature-rebuild Checkpoint
    # 1 fix, which derives it live from the streaming GapToLeader field
    # (F1's live feed sends Position itself only once, in the Subscribe
    # snapshot). Still optional here since replay/live callers that have no
    # resolvable position yet (e.g. before any GapToLeader message has
    # arrived) must not be forced to send a fabricated value.
    position: int | None = None


class TireStintCreate(BaseModel):
    session_id: uuid.UUID
    driver_id: uuid.UUID
    stint_number: int
    compound: str
    start_lap: int
    end_lap: int | None = None
    avg_deg_per_lap: float | None = None


class TireStintResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    driver_id: uuid.UUID
    stint_number: int
    compound: str
    start_lap: int
    end_lap: int | None
    avg_deg_per_lap: float | None


class LiveTelemetryEvent(BaseModel):
    """Single 100 ms telemetry sample.

    Still unwired — see CLAUDE.md's "Deferred Telemetry Features": raw
    high-frequency Throttle/Brake/Speed channels were never ingested (Day 5
    deliberately skipped them). Kept here for when that lands; the WS
    endpoint broadcasts LapCompletedEvent instead (see below), which is built
    from data this codebase actually has.
    """

    driver_id: uuid.UUID
    session_id: uuid.UUID
    timestamp_ms: int
    speed_kmh: float
    throttle_pct: float
    brake: bool
    gear: int
    drs: bool


class LapCompletedEvent(BaseModel):
    """WebSocket payload broadcast on /ws/telemetry/{session_id} when a new lap is ingested.

    Lap-summary fields come from the just-persisted LapData row. The
    speed_kmh/throttle_pct/brake/gear/drs fields are best-effort: read from
    the live f1:{season}:{round}:car:{car_number}:latest CarData cache at
    broadcast time (see telemetry_service._decode_car_channels) and are None
    if that key has expired or no live ingestor is running for this session.
    """

    driver_id: uuid.UUID
    session_id: uuid.UUID
    lap_number: int
    lap_time_seconds: float | None
    compound: str
    sector1_seconds: float | None
    sector2_seconds: float | None
    sector3_seconds: float | None
    speed_kmh: float | None = None
    throttle_pct: float | None = None
    brake: bool | None = None
    gear: int | None = None
    drs: Literal["off", "available", "enabled", "open", "unknown"] | None = None


class TelemetryStreamMessage(BaseModel):
    """Envelope wrapping a LapCompletedEvent on the WebSocket stream."""

    event: str
    session_id: uuid.UUID
    data: LapCompletedEvent


class LiveTelemetryResponse(BaseModel):
    """GET /telemetry/{session_id}/{driver_id}/live — raw normalized CarData sample."""

    session_id: uuid.UUID
    driver_id: uuid.UUID
    data: dict[str, Any]


class LapHistoryBucket(BaseModel):
    bucket: str
    avg_sector1_seconds: float | None
    avg_sector2_seconds: float | None
    avg_sector3_seconds: float | None
    avg_lap_time_seconds: float | None
    lap_count: int


class DriverGap(BaseModel):
    driver_id: uuid.UUID
    lap_number: int
    position: int
    # None when the adjacent driver (ahead/behind respectively) is on a
    # different lap_number — subtracting cumulative_seconds across a lap
    # boundary compares a different amount of race distance and produces a
    # meaningless (often negative) value. laps_behind carries the lap deficit
    # to the car immediately ahead instead (0 when on the same lap).
    gap_to_ahead_seconds: float | None
    gap_to_behind_seconds: float | None
    laps_behind: int = 0
    # Only populated by the live-ingestion path (ingest_live_session.py's
    # _publish_live_gaps, parsed directly from TimingData's own GapToLeader
    # field) — None for the DB-reconstruction fallback path, which has no
    # equivalent authoritative source and would have the same lap-1-missing
    # unreliability documented on gap_to_ahead_seconds above.
    gap_to_leader_seconds: float | None = None


class SessionGapsResponse(BaseModel):
    session_id: uuid.UUID
    gaps: list[DriverGap]


class DriverPosition(BaseModel):
    """One car's latest live X/Y/Z, read from f1:{season}:{round}:car:{car_number}:position.

    Keyed by driver_number (the car number FastF1's live feed uses), not
    driver_id — unlike the rest of this file, the Circuit Map Panel's live
    dots don't need a DB round trip to resolve which driver a car number
    belongs to (the frontend already has the roster via GET /drivers).
    """

    driver_number: str
    x: float
    y: float
    z: float | None = None
    timestamp: str | None = None


class DriverCarNumber(BaseModel):
    """One driver's live-session car number, from f1:{season}:{round}:driver:{driver_id}:car_number.

    Bridges DriverPosition's driver_number (a car number, not a driver_id) to
    the driver roster (GET /drivers) so the Circuit Map Panel can resolve
    each live dot's team color and match it against the selected driver.
    Session-scoped rather than a static roster field: sourced from the same
    live-ingestor-written key for the lifetime of this specific session, so
    it self-corrects for a reserve-driver substitution instead of assuming a
    driver's car number never changes.
    """

    driver_id: uuid.UUID
    car_number: str
