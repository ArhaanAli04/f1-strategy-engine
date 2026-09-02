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

// driver_id, not driver_code — the frontend resolves id -> code/team color via
// useDrivers, same pattern as DriverChip/LiveTimingTower.
export interface OvertakingDriver {
  position: number
  driver_id: string
  gap_seconds: number
}

// drivers_overtaken is always "drivers behind the requester within
// pit_cost_seconds at current_lap" regardless of the plan's actual result —
// the UI relabels the same list depending on position_gain_loss's sign.
export interface PlanExplanation {
  pit_cost_seconds: number
  drivers_overtaken: OvertakingDriver[]
  remaining_laps: number
  fresh_tyre_gain_per_lap: number
  total_recoverable_seconds: number
}

export interface SimulatedRaceOutcome {
  pit_laps: number[]
  compounds: string[]
  predicted_finish_time: number
  position_gain_loss: number
  confidence_interval: [number, number]
  explanation: PlanExplanation
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
  // Populated only when status === "FAILURE" — a user-facing message (a
  // known validation rejection reaching the worker, or a generic fallback
  // for anything else; the real exception is never echoed, see
  // backend/apis/v1/strategy.py's get_simulation_result).
  error: string | null
}
