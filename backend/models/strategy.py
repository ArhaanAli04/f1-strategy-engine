import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.driver import Driver
    from backend.models.race import Session


class StrategyPrediction(Base):
    __tablename__ = "strategy_predictions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id"), nullable=False, index=True
    )
    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    optimal_pit_lap: Mapped[int] = mapped_column(Integer, nullable=False)
    pit_probability: Mapped[float] = mapped_column(Float, nullable=False)
    undercut_score: Mapped[float] = mapped_column(Float, nullable=False)
    overcut_score: Mapped[float] = mapped_column(Float, nullable=False)
    tire_life_remaining: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session: Mapped["Session"] = relationship(back_populates="strategy_predictions")
    driver: Mapped["Driver"] = relationship(back_populates="strategy_predictions")


class PitEvent(Base):
    __tablename__ = "pit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id"), nullable=False, index=True
    )
    lap_number: Mapped[int] = mapped_column(Integer, nullable=False)
    compound_in: Mapped[str] = mapped_column(String(10), nullable=False)
    compound_out: Mapped[str] = mapped_column(String(10), nullable=False)
    pit_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    was_predicted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    session: Mapped["Session"] = relationship(back_populates="pit_events")
    driver: Mapped["Driver"] = relationship(back_populates="pit_events")
