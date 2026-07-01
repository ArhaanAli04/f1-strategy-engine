import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.strategy import PitEvent, StrategyPrediction
    from backend.models.telemetry import LapData, TireStint
    from backend.models.user import Alert


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(3), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    nationality: Mapped[str] = mapped_column(String(60), nullable=False)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)

    contracts: Mapped[list["DriverContract"]] = relationship(back_populates="driver")
    lap_data: Mapped[list["LapData"]] = relationship(back_populates="driver")
    tire_stints: Mapped[list["TireStint"]] = relationship(back_populates="driver")
    strategy_predictions: Mapped[list["StrategyPrediction"]] = relationship(back_populates="driver")
    pit_events: Mapped[list["PitEvent"]] = relationship(back_populates="driver")
    alerts: Mapped[list["Alert"]] = relationship(back_populates="driver")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    constructor_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    color_hex: Mapped[str] = mapped_column(String(7), nullable=False)

    contracts: Mapped[list["DriverContract"]] = relationship(back_populates="team")


class DriverContract(Base):
    __tablename__ = "driver_contracts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id"), nullable=False, index=True
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False, index=True
    )
    season: Mapped[int] = mapped_column(Integer, nullable=False)

    driver: Mapped["Driver"] = relationship(back_populates="contracts")
    team: Mapped["Team"] = relationship(back_populates="contracts")
