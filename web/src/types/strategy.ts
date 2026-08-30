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

export interface PitWindowResponse {
  pit_lap: number
  window_start: number
  window_end: number
  projected_total_delta_seconds: number
  shap_explanation: FeatureContributionResponse[] | null
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
// replay. lap_number is null for pre-Day-42 rows (see backend docstring).
export interface StrategyPredictionHistoryEntry {
  lap_number: number | null
  predicted_pit_lap: number
  pit_probability: number
  undercut_score: number
  overcut_score: number
  created_at: string
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
