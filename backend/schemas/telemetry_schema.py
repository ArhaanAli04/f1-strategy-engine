import uuid
from datetime import datetime
from typing import Any

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
    """Single 100 ms telemetry sample pushed over WebSocket."""

    driver_id: uuid.UUID
    session_id: uuid.UUID
    timestamp_ms: int
    speed_kmh: float
    throttle_pct: float
    brake: bool
    gear: int
    drs: bool


class TelemetryStreamMessage(BaseModel):
    """Envelope wrapping a LiveTelemetryEvent on the WebSocket stream."""

    event: str
    session_id: uuid.UUID
    data: LiveTelemetryEvent
