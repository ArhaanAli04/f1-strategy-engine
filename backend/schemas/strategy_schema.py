import uuid
from datetime import date, datetime
from typing import Literal

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


class ExplanationFact(BaseModel):
    """One structured fact behind a pit recommendation.

    A single named quantity plus its plain-English-formatted value, for a
    frontend to render as a bullet/chip without re-deriving formatting logic
    from raw numbers itself. source names which mechanism the underlying
    number came from — "tire_deg" (build_pit_recommendation's own candidate
    search or its SHAP explanation), "pit_predictor" (the rival-gap-aware
    classifier's SHAP explanation, or the undercut/overcut probabilities),
    or "field" (raw context — tyre age, track position — not itself a model
    output) — so a UI that wants to visually group facts by source can.
    """

    label: str
    value: str
    source: Literal["tire_deg", "pit_predictor", "field"]


class PitRecommendationExplanation(BaseModel):
    """Combined explanation for one pit recommendation's #1 candidate.

    facts/narrative are DERIVED from tire_deg_shap/pit_predictor_shap (plus
    raw field context and the undercut/overcut probabilities) — see
    strategy_service.build_pit_recommendation_explanation. Both raw SHAP
    arrays are included alongside the derived text so a client that wants
    the full contribution breakdown (not just the top-1 each narrative/facts
    surfaces) still has it.
    """

    facts: list[ExplanationFact]
    narrative: str
    tire_deg_shap: list[FeatureContributionResponse]
    pit_predictor_shap: list[FeatureContributionResponse]


class PitWindowResponse(BaseModel):
    """One ranked pit-lap candidate from strategy_service.build_pit_recommendation.

    window_start/window_end are identical across every candidate in one
    response — a narrow band (see build_pit_recommendation's own docstring)
    around the #1 (rank 0) candidate, not a per-candidate range.
    recommended_compound is real per candidate (the stint-2 compound that won
    that pit_lap's own optimization). confidence_score and explanation are
    populated only on the #1 candidate — both answer "how sure/why for THIS
    recommendation," which isn't a meaningful question for a candidate that
    isn't the one being recommended.
    """

    pit_lap: int
    window_start: int
    window_end: int
    projected_total_delta_seconds: float
    recommended_compound: str
    confidence_score: float | None = None
    explanation: PitRecommendationExplanation | None = None


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


class StrategyPredictionHistoryEntry(BaseModel):
    """One StrategyPrediction row in a driver's lap-by-lap progression history.

    Supplementary to StrategyOverviewResponse (which stays live/current, one
    row per driver) — this is the full history for a single driver, ordered
    oldest-first. predicted_pit_lap maps from the StrategyPrediction model's
    optimal_pit_lap column (renamed at the API boundary only — see
    strategy_service.get_strategy_prediction_history).

    recommended_pit_lap/window_start/window_end/recommended_compound/
    confidence_score/explanation are the core-feature-rebuild's Checkpoint 4
    addition — the SAME rich recommendation the on-demand /pit-window REST
    endpoint returns (build_pit_recommendation + get_pit_window_with_
    explanation), now persisted per lap by prediction_worker.
    _compute_recommendation_fields so a replayed/live-progressing driver's
    history carries it too, not just pit_predictor's older, cruder
    predicted_pit_lap/pit_probability above. All nullable except
    confidence_score (0.0 default, matching the underlying column) — a row
    predating this migration, or one where the recommendation computation
    degraded gracefully that lap (see _compute_recommendation_fields' own
    docstring), has none of them.
    """

    model_config = ConfigDict(from_attributes=True)

    lap_number: int | None
    predicted_pit_lap: int
    pit_probability: float
    undercut_score: float
    overcut_score: float
    created_at: datetime
    recommended_pit_lap: int | None
    window_start: int | None
    window_end: int | None
    recommended_compound: str | None
    confidence_score: float
    explanation: PitRecommendationExplanation | None


class StrategyPredictionHistoryResponse(BaseModel):
    session_id: uuid.UUID
    driver_id: uuid.UUID
    predictions: list[StrategyPredictionHistoryEntry]


class StrategyComparisonEntry(BaseModel):
    rank: int
    predicted_finishing_position: int
    strategy: StrategyPredictionResponse


class StrategyComparisonResponse(BaseModel):
    session_id: uuid.UUID
    driver_id: uuid.UUID
    strategies: list[StrategyComparisonEntry]


class LastIngestedSessionResponse(BaseModel):
    """The R session with the newest race_date that has ingested lap data.

    Backs GET /strategy/last-ingested-session — the Strategy Simulator's
    session source when no race is live. event_name is NULL for rows ingested
    before that column was populated (pre-2026); the frontend falls back to
    circuit_name.
    """

    model_config = ConfigDict(from_attributes=True)

    session_id: uuid.UUID
    season: int
    round_number: int
    event_name: str | None
    circuit_name: str
    race_date: date
