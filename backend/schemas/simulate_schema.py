import uuid

from pydantic import BaseModel, Field, model_validator

# Matches strategy_service._COMPOUND_ENCODING / prediction_worker._COMPOUND_ENCODING's
# key set — the only compounds any tire_deg pipeline was ever trained on.
_KNOWN_COMPOUNDS = frozenset({"HARD", "INTERMEDIATE", "MEDIUM", "SOFT", "WET"})


class SimulateStrategyRequest(BaseModel):
    driver_id: uuid.UUID
    # >= 1, not >= 0: a session with zero ingested laps yet is a genuine
    # pre-race what-if (see strategy_service.validate_current_lap), but "lap
    # 0" itself isn't a meaningful race state to simulate from — the earliest
    # is "currently on lap 1". The actual upper bound (this can't exceed the
    # session's real progress by more than one lap) needs a DB lookup and is
    # enforced at request time by strategy_service.validate_current_lap, not
    # here — see docs/simulator-issues-wet-model-and-position-context.md's
    # Checkpoint-6 follow-up finding.
    current_lap: int = Field(ge=1)
    current_compound: str
    # 0 is a fresh tyre, not invalid — unlike current_lap/remaining_laps.
    current_tyre_age: int = Field(ge=0)
    remaining_laps: int = Field(ge=1)
    # Empty (default): the Monte Carlo simulation decides pit timing for this
    # driver autonomously, same as every other driver in the field. Non-empty:
    # forces this driver's simulated pit stops onto these exact laps — the
    # what-if scenario race_simulator.simulate_race's forced_pit_laps override
    # implements (see race_simulator.py).
    pit_laps: list[int] = []
    # Compound to switch to after the pit stop at the same-index entry in
    # pit_laps — must be the same length as pit_laps when pit_laps is non-empty.
    compounds: list[str]

    @model_validator(mode="after")
    def _validate_pit_plan(self) -> "SimulateStrategyRequest":
        if self.pit_laps and len(self.pit_laps) != len(self.compounds):
            raise ValueError(
                f"pit_laps ({len(self.pit_laps)}) and compounds ({len(self.compounds)}) "
                "must be the same length when pit_laps is non-empty"
            )
        unknown = set(self.compounds) - _KNOWN_COMPOUNDS
        if unknown:
            raise ValueError(f"Unknown compound(s): {sorted(unknown)}")
        # A forced pit lap outside the simulated horizon was previously
        # silently ignored — race_simulator.simulate_race only ever checks
        # `if lap_number in schedule` inside its `range(current_lap+1,
        # total_laps+1)` loop, so a pit_laps entry <= current_lap or beyond
        # current_lap + remaining_laps never fires, with no error to say so.
        # Rejecting it here surfaces that as a clear 422 instead of a
        # what-if that quietly does nothing.
        horizon_end = self.current_lap + self.remaining_laps
        out_of_range = [lap for lap in self.pit_laps if not (self.current_lap < lap <= horizon_end)]
        if out_of_range:
            raise ValueError(
                f"pit_laps {out_of_range} must each be greater than current_lap "
                f"({self.current_lap}) and at most current_lap + remaining_laps "
                f"({horizon_end})"
            )
        return self


class OvertakingDriver(BaseModel):
    """One driver within a pit stop's worth of time of the requester at current_lap.

    driver_id, not driver_code — the frontend resolves id -> code/team color via
    its own driver roster query, same pattern as DriverChip/LiveTimingTower.
    """

    position: int
    driver_id: str
    gap_seconds: float


class PlanExplanation(BaseModel):
    """Why this plan's position_gain_loss came out the way it did.

    drivers_overtaken is always the same list (drivers behind the requester at
    current_lap, within pit_cost_seconds) regardless of whether the plan's
    result is a gain or a loss — the frontend relabels it depending on
    position_gain_loss's sign ("overtake you" vs "you overtake").
    """

    pit_cost_seconds: float
    drivers_overtaken: list[OvertakingDriver]
    remaining_laps: int
    fresh_tyre_gain_per_lap: float
    total_recoverable_seconds: float


class SimulatedRaceOutcome(BaseModel):
    pit_laps: list[int]
    compounds: list[str]
    predicted_finish_time: float
    position_gain_loss: int
    confidence_interval: tuple[float, float]
    explanation: PlanExplanation


class SimulateStrategyResponse(BaseModel):
    driver_id: uuid.UUID
    strategies: list[SimulatedRaceOutcome]


class SimulateTaskAccepted(BaseModel):
    """202 response for POST /strategy/{session_id}/simulate."""

    task_id: str
    status: str


class SimulateTaskStatusResponse(BaseModel):
    """Response for GET /strategy/simulate/{task_id}, polling the Celery result backend."""

    task_id: str
    status: str
    result: SimulateStrategyResponse | None = None
    # Populated only when status == FAILURE. Deliberately narrow: this route
    # is unauthenticated (see apis/v1/strategy.py's module docstring), so the
    # underlying exception is never echoed verbatim — see get_simulation_result
    # for the F1StrategyError-only safe-message policy this mirrors from
    # core/exceptions.py's unhandled_error_handler.
    error: str | None = None
