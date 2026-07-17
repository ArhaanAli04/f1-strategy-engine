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


class FeatureContributionResponse(BaseModel):
    """One SHAP feature contribution — see services/ml/explainability.py."""

    feature_name: str
    value: float
    contribution: float
    direction: str


class PitWindowResponse(BaseModel):
    pit_lap: int
    window_start: int
    window_end: int
    projected_total_delta_seconds: float
    shap_explanation: list[FeatureContributionResponse] | None = None


class UndercutThreatResponse(BaseModel):
    target_driver_id: uuid.UUID
    probability_pit_now_gains_position: float
    projected_gap_seconds: float
    n_laps_projected: int
    recommended_action: str


class CompetitorStrategyEntry(BaseModel):
    driver_id: uuid.UUID
    predicted_pit_lap: int
    pit_probability: float


class StrategyOverviewResponse(BaseModel):
    session_id: uuid.UUID
    drivers: list[CompetitorStrategyEntry]


class StrategyComparisonEntry(BaseModel):
    rank: int
    predicted_finishing_position: int
    strategy: StrategyPredictionResponse


class StrategyComparisonResponse(BaseModel):
    session_id: uuid.UUID
    driver_id: uuid.UUID
    strategies: list[StrategyComparisonEntry]
