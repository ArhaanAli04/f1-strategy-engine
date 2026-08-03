// Mirrors backend/schemas/simulate_schema.py

export interface SimulateStrategyRequest {
  driver_id: string
  current_lap: number
  current_compound: string
  current_tyre_age: number
  remaining_laps: number
  // Empty (default): Monte Carlo decides pit timing autonomously. Non-empty:
  // forces pit stops onto these exact laps (what-if scenario).
  pit_laps?: number[]
  // Must be the same length as pit_laps when pit_laps is non-empty; backend
  // validates each value against {HARD, INTERMEDIATE, MEDIUM, SOFT, WET}.
  compounds: string[]
}

export interface SimulatedRaceOutcome {
  pit_laps: number[]
  compounds: string[]
  predicted_finish_time: number
  position_gain_loss: number
  confidence_interval: [number, number]
}

export interface SimulateStrategyResponse {
  driver_id: string
  strategies: SimulatedRaceOutcome[]
}

// 202 response for POST /strategy/{session_id}/simulate.
export interface SimulateTaskAccepted {
  task_id: string
  status: string
}

// Response for GET /strategy/simulate/{task_id}, polling the Celery result backend.
export interface SimulateTaskStatusResponse {
  task_id: string
  status: string
  result: SimulateStrategyResponse | null
}
