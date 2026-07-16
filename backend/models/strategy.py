import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.driver import Driver
    from backend.models.race import Session


class StrategyPrediction(Base):
    __tablename__ = "strategy_predictions"
    # Backs alert_service._latest_undercut_scores: filters by session_id,
    # groups by driver_id, aggregates MAX(predicted_at) — then re-joins on
    # (driver_id, predicted_at) to fetch the winning row. DESC matches the
    # "most recent prediction" access pattern; the single-column indexes
    # below on session_id/driver_id don't help SQLAlchemy plan the composite
    # filter+group+max as one index scan.
    __table_args__ = (
        Index(
            "ix_strategy_predictions_session_driver_predicted_at",
            "session_id",
            "driver_id",
            text("predicted_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No standalone index=True here: ix_strategy_predictions_session_driver_predicted_at
    # above already leads with session_id, so a separate single-column index
    # on it would be a pure duplicate.
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
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
