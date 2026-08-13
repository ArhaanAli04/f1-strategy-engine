import uuid

from pydantic import BaseModel, model_validator

# Matches strategy_service._COMPOUND_ENCODING / prediction_worker._COMPOUND_ENCODING's
# key set — the only compounds any tire_deg pipeline was ever trained on.
_KNOWN_COMPOUNDS = frozenset({"HARD", "INTERMEDIATE", "MEDIUM", "SOFT", "WET"})


class SimulateStrategyRequest(BaseModel):
    driver_id: uuid.UUID
    current_lap: int
    current_compound: str
    current_tyre_age: int
    remaining_laps: int
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
