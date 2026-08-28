"""Request/response contracts for the /demo/replay/* control endpoints (Day 43).

See services/demo_service.py for the logic and the hardcoded curated-session
list these shapes carry.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CuratedSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: uuid.UUID
    race_name: str
    circuit_name: str
    description: str
    start_lap: int
    end_lap: int
    estimated_duration_minutes: int


class CuratedSessionsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sessions: list[CuratedSessionResponse]


class ReplayAvailableResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    available: bool
    # Populated only when available is False — the live-race reason from
    # live_race_detection.detect_live_race, for the UI to surface.
    reason: str | None = None


class ReplayStartRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: uuid.UUID


class ReplayStartResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    replay_id: uuid.UUID
    session_id: uuid.UUID
    race_name: str
    start_lap: int
    end_lap: int


class ReplayStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    running: bool
    # All None when running is False.
    replay_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    race_name: str | None = None
    start_lap: int | None = None
    end_lap: int | None = None
    started_at: datetime | None = None


class ReplayStopResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stopped: bool
    session_id: uuid.UUID
