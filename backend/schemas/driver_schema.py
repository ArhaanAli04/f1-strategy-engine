import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict


class TeamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    constructor_id: str
    color_hex: str


class DriverContractResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    driver_id: uuid.UUID
    team_id: uuid.UUID
    season: int
    team: TeamResponse | None = None


class DriverResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    full_name: str
    nationality: str
    date_of_birth: date | None
    contracts: list[DriverContractResponse] = []


class DriverListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    full_name: str
    nationality: str
    date_of_birth: date | None


class DriverAnalysisResponse(BaseModel):
    """Driver-style fingerprint (see services/ml/driver_style.py) plus
    session-relative performance. Not ORM-backed — assembled from a cached
    population-level cluster fit and a live lap-time aggregate query.
    """

    driver_id: uuid.UUID
    season: int
    archetype: str
    cluster: int
    sector_time_variance: float
    tyre_management_index: float
    lap_time_consistency: float
    stint_length_tendency: float
    umap_x: float
    umap_y: float
    performance_vs_team_avg_seconds: float | None
