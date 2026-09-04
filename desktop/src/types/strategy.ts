// Mirrors backend/schemas/strategy_schema.py

export interface StrategyPredictionResponse {
  id: string
  session_id: string
  driver_id: string
  predicted_at: string
  optimal_pit_lap: number
  pit_probability: number
  undercut_score: number
  overcut_score: number
  tire_life_remaining: number
  confidence_score: number
  model_version: string
  created_at: string
}

// One SHAP feature contribution — see services/ml/explainability.py.
export interface FeatureContributionResponse {
  feature_name: string
  value: number
  contribution: number
  direction: string
}

// One structured fact behind a pit recommendation — see
// backend/schemas/strategy_schema.py's ExplanationFact.
export interface ExplanationFact {
  label: string
  value: string
  source: "tire_deg" | "pit_predictor" | "field"
}

// Combined tire_deg + pit_predictor explanation for one recommendation's #1
// candidate — see backend/schemas/strategy_schema.py's
// PitRecommendationExplanation. facts/narrative are server-derived from
// tire_deg_shap/pit_predictor_shap plus field/undercut context; both raw
// SHAP arrays are included too for a client that wants the full breakdown.
export interface PitRecommendationExplanation {
  facts: ExplanationFact[]
  narrative: string
  tire_deg_shap: FeatureContributionResponse[]
  pit_predictor_shap: FeatureContributionResponse[]
}

// window_start/window_end are identical across every candidate in one
// response — a narrow band around the #1 (rank 0) candidate (see
// build_pit_recommendation's own docstring), not a per-candidate range.
// confidence_score/explanation are populated only on the #1 candidate.
export interface PitWindowResponse {
  pit_lap: number
  window_start: number
  window_end: number
  projected_total_delta_seconds: number
  recommended_compound: string
  confidence_score: number | null
  explanation: PitRecommendationExplanation | null
}

export interface UndercutThreatResponse {
  target_driver_id: string
  probability_pit_now_gains_position: number
  projected_gap_seconds: number
  n_laps_projected: number
  recommended_action: string
}

export interface CompetitorStrategyEntry {
  driver_id: string
  predicted_pit_lap: number
  pit_probability: number
}

export interface StrategyOverviewResponse {
  session_id: string
  drivers: CompetitorStrategyEntry[]
}

export interface StrategyComparisonEntry {
  rank: number
  predicted_finishing_position: number
  strategy: StrategyPredictionResponse
}

export interface StrategyComparisonResponse {
  session_id: string
  driver_id: string
  strategies: StrategyComparisonEntry[]
}

// One StrategyPrediction row in a driver's lap-by-lap progression history —
// supplementary to StrategyOverviewResponse/UndercutThreatResponse (always
// live/current), used to reconstruct "the prediction valid at lap N" during
// replay/live progression. lap_number is null for pre-Day-42 rows (see
// backend docstring). recommended_pit_lap/window_start/window_end/
// recommended_compound/confidence_score/explanation are the core-feature-
// rebuild's Checkpoint 4 addition — the SAME rich recommendation
// PitWindowResponse carries, persisted per lap by prediction_worker so a
// replayed/live-progressing driver's history carries it too, not just
// pit_predictor's older, cruder predicted_pit_lap/pit_probability. All
// nullable except confidence_score (0.0 default) — a row predating that
// migration, or one where the computation degraded gracefully that lap, has
// none of them.
export interface StrategyPredictionHistoryEntry {
  lap_number: number | null
  predicted_pit_lap: number
  pit_probability: number
  undercut_score: number
  overcut_score: number
  created_at: string
  recommended_pit_lap: number | null
  window_start: number | null
  window_end: number | null
  recommended_compound: string | null
  confidence_score: number
  explanation: PitRecommendationExplanation | null
}

export interface StrategyPredictionHistoryResponse {
  session_id: string
  driver_id: string
  predictions: StrategyPredictionHistoryEntry[]
}

// GET /strategy/last-ingested-session — the R session with the newest
// race_date that has ingested lap data. The Strategy Simulator's session
// source when no race is live; resolved per-environment. event_name is null
// for rows ingested before that column existed (pre-2026) — fall back to
// circuit_name.
export interface LastIngestedSessionResponse {
  session_id: string
  season: number
  round_number: number
  event_name: string | null
  circuit_name: string
  race_date: string
}
