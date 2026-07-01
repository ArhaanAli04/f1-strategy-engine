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
