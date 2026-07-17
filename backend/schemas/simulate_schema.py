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


class SimulatedRaceOutcome(BaseModel):
    pit_laps: list[int]
    compounds: list[str]
    predicted_finish_time: float
    position_gain_loss: int
    confidence_interval: tuple[float, float]


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
