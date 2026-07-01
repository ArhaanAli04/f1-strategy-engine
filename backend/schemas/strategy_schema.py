import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class StrategyPredictionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    driver_id: uuid.UUID
    predicted_at: datetime
    optimal_pit_lap: int
    pit_probability: float
    undercut_score: float
    overcut_score: float
    tire_life_remaining: float
    confidence_score: float
    model_version: str
    created_at: datetime


class PitWindowResponse(BaseModel):
    pit_lap: int
    window_start: int
    window_end: int
    confidence_pct: float


class UndercutThreatResponse(BaseModel):
    driver_ahead: uuid.UUID
    threat_score: float
    recommended_action: str


class StrategyComparisonEntry(BaseModel):
    rank: int
    predicted_finishing_position: int
    strategy: StrategyPredictionResponse


class StrategyComparisonResponse(BaseModel):
    session_id: uuid.UUID
    driver_id: uuid.UUID
    strategies: list[StrategyComparisonEntry]
