import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class AlertType(StrEnum):
    UNDERCUT_THREAT = "UNDERCUT_THREAT"
    PIT_WINDOW_OPEN = "PIT_WINDOW_OPEN"
    SAFETY_CAR_PROBABILITY = "SAFETY_CAR_PROBABILITY"
    FASTEST_LAP_THREAT = "FASTEST_LAP_THREAT"
    COMPETITOR_PITTED = "COMPETITOR_PITTED"


class AlertCreate(BaseModel):
    user_id: uuid.UUID
    session_id: uuid.UUID
    alert_type: AlertType
    driver_id: uuid.UUID | None = None
    message: str
    triggered_at: datetime


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    session_id: uuid.UUID
    alert_type: str
    driver_id: uuid.UUID | None
    message: str
    triggered_at: datetime
    delivered_at: datetime | None
