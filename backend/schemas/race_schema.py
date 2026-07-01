import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class CircuitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    country: str
    track_length_km: float
    lap_record_seconds: float | None
    created_at: datetime


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    race_id: uuid.UUID
    session_type: str
    session_date: date


class RaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    season: int
    round_number: int
    circuit_id: uuid.UUID
    race_date: date
    weather: str | None
    status: str
    circuit: CircuitResponse | None = None
    sessions: list[SessionResponse] = []


class RaceListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    season: int
    round_number: int
    circuit_id: uuid.UUID
    race_date: date
    weather: str | None
    status: str
    circuit: CircuitResponse | None = None
