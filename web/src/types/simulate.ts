// Mirrors backend/schemas/simulate_schema.py

// One candidate pit plan within a multi-scenario compare request — see
// ScenarioPlan's own docstring in simulate_schema.py. label is optional and
// display-only.
export interface ScenarioPlan {
  pit_laps: number[]
  compounds: string[]
  label?: string | null
}

export interface SimulateStrategyRequest {
  driver_id: string
  current_lap: number
  current_compound: string
  current_tyre_age: number
  remaining_laps: number
  // Empty (default): Monte Carlo decides pit timing autonomously. Non-empty:
  // forces pit stops onto these exact laps (what-if scenario). Mutually
  // exclusive with scenarios below — the backend 422s if both are set.
  pit_laps?: number[]
  // Must be the same length as pit_laps when pit_laps is non-empty; backend
  // validates each value against {HARD, INTERMEDIATE, MEDIUM, SOFT, WET}.
  compounds?: string[]
  // Undefined/omitted: single-plan request (pit_laps/compounds above), same
  // as before this field existed. 1-4 entries: multi-scenario compare — the
  // backend runs all of them against the identical field state in one
  // Celery task, sharing one random seed across them, and returns one
  // SimulatedRaceOutcome per scenario in `strategies`, in request order.
  scenarios?: ScenarioPlan[]
}

// driver_id, not driver_code — the frontend resolves id -> code/team color via
// useDrivers, same pattern as DriverChip/LiveTimingTower.
//
// finish_ahead_probability/rival_projected_pit_lap/rival_pit_probability are
// real outputs of the SAME 1000-run Monte Carlo simulate_race call behind
// position_gain_loss/position_probabilities — added for the What-If
// Simulator rebuild part (b) (see
// docs/core-feature-rebuild-whatif-simulator.md §7). All three are null only
// when the simulation genuinely has no data for this rival (should not
// happen in practice), never coerced to a misleading 0.
export interface OvertakingDriver {
  position: number
  driver_id: string
  gap_seconds: number
  // P(the requester finishes ahead of THIS rival specifically).
  finish_ahead_probability: number | null
  // This rival's own single most likely pit lap, from that rival's own
  // per-lap pit probability across all simulations, summarized to its peak.
  rival_projected_pit_lap: number | null
  // The pit probability AT rival_projected_pit_lap — always present together
  // with rival_projected_pit_lap (both null or both set).
  rival_pit_probability: number | null
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

// One finishing position's probability from the real 1000-run Monte Carlo
// outcome — sparse (probability > 0 only) and sorted by position ascending,
// see backend/schemas/simulate_schema.py's PositionProbability docstring.
export interface PositionProbability {
  position: number
  probability: number
}

export interface SimulatedRaceOutcome {
  pit_laps: number[]
  compounds: string[]
  // Passed through verbatim from the matching ScenarioPlan.label in a
  // multi-scenario compare request; null for the single-plan path.
  label: string | null
  predicted_finish_time: number
  position_gain_loss: number
  // Unrounded mean finishing position across all simulations — position_gain_loss
  // is round(starting_position - mean_position), which loses precision a
  // scenario-comparison view may want.
  mean_position: number
  position_probabilities: PositionProbability[]
  confidence_interval: [number, number]
  explanation: PlanExplanation
}

export interface SimulateStrategyResponse {
  driver_id: string
  // The requester's real track position before any scenario's laps run —
  // identical across every entry in strategies. Used to compute P(gain)/
  // P(hold)/P(lose) per strategy from its position_probabilities.
  starting_position: number
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
