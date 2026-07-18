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
    gap_to_ahead_seconds: float
    gap_to_behind_seconds: float


class SessionGapsResponse(BaseModel):
    session_id: uuid.UUID
    gaps: list[DriverGap]
