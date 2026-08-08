import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.strategy import PitEvent, StrategyPrediction
    from backend.models.telemetry import LapData, TireStint
    from backend.models.user import Alert


class Circuit(Base):
    __tablename__ = "circuits"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    country: Mapped[str] = mapped_column(String(80), nullable=False)
    track_length_km: Mapped[float] = mapped_column(Float, nullable=False)
    lap_record_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Circuit-Map-Panel outline: rotated/normalized track polyline + viewBox,
    # extracted one-time per circuit via scripts/extract_circuit_outlines.py
    # from a real FastF1 session's fastest-lap telemetry (same coordinate
    # frame the live Position.z feed uses, so live driver dots line up with
    # this outline without separate per-circuit calibration). Null until that
    # script has run for a given circuit.
    map_geometry: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    races: Mapped[list["Race"]] = relationship(back_populates="circuit")


class Race(Base):
    __tablename__ = "races"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    circuit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("circuits.id"), nullable=False
    )
    race_date: Mapped[date] = mapped_column(Date, nullable=False)
    weather: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
    # Display name (e.g. "Bahrain Grand Prix"), sourced from FastF1's
    # event-schedule EventName — distinct from Circuit.name (the venue).
    # Nullable: historical rows ingested before this column existed.
    event_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    circuit: Mapped["Circuit"] = relationship(back_populates="races")
    sessions: Mapped[list["Session"]] = relationship(
        back_populates="race", cascade="all, delete-orphan"
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    race_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("races.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # FP1, FP2, FP3, Q, R
    session_type: Mapped[str] = mapped_column(String(3), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Real session start instant (date + time), sourced from Ergast's
    # per-session date/time columns at ingestion — session_date alone can't
    # back a countdown timer. Nullable: historical rows ingested before this
    # column existed, and any session Ergast doesn't carry a time for.
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    race: Mapped["Race"] = relationship(back_populates="sessions")
    lap_data: Mapped[list["LapData"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    tire_stints: Mapped[list["TireStint"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    strategy_predictions: Mapped[list["StrategyPrediction"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    pit_events: Mapped[list["PitEvent"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["Alert"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
