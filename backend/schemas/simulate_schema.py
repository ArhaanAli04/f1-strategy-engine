import uuid

from pydantic import BaseModel


class SimulateStrategyRequest(BaseModel):
    driver_id: uuid.UUID
    current_lap: int
    current_compound: str
    current_tyre_age: int
    remaining_laps: int
    pit_laps: list[int]
    compounds: list[str]


class SimulatedRaceOutcome(BaseModel):
    pit_laps: list[int]
    compounds: list[str]
    predicted_finish_time: float
    position_gain_loss: int
    confidence_interval: tuple[float, float]


class SimulateStrategyResponse(BaseModel):
    driver_id: uuid.UUID
    strategies: list[SimulatedRaceOutcome]
