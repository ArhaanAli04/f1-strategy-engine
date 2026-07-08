import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.driver import Driver
    from backend.models.race import Session


class LapData(Base):
    __tablename__ = "lap_data"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "driver_id", "lap_number", name="uq_lap_data_session_driver_lap"
        ),
    )

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
    lap_time_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    compound: Mapped[str] = mapped_column(String(20), nullable=False)
    tyre_age_laps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Raw FastF1 TrackStatus code(s) active during this lap, e.g. "1", "24"
    # (multiple simultaneous flags are concatenated by FastF1). 4=SC, 6/7=VSC.
    track_status: Mapped[str | None] = mapped_column(String(10), nullable=True)
    sector1_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    sector2_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    sector3_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session: Mapped["Session"] = relationship(back_populates="lap_data")
    driver: Mapped["Driver"] = relationship(back_populates="lap_data")
    sector_times: Mapped[list["SectorTime"]] = relationship(
        back_populates="lap_data", cascade="all, delete-orphan"
    )


class TireStint(Base):
    __tablename__ = "tire_stints"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "driver_id", "stint_number", name="uq_tire_stints_session_driver_stint"
        ),
    )

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
    stint_number: Mapped[int] = mapped_column(Integer, nullable=False)
    compound: Mapped[str] = mapped_column(String(20), nullable=False)
    start_lap: Mapped[int] = mapped_column(Integer, nullable=False)
    end_lap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_deg_per_lap: Mapped[float | None] = mapped_column(Float, nullable=True)

    session: Mapped["Session"] = relationship(back_populates="tire_stints")
    driver: Mapped["Driver"] = relationship(back_populates="tire_stints")


class SectorTime(Base):
    __tablename__ = "sector_times"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lap_data_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lap_data.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sector: Mapped[int] = mapped_column(Integer, nullable=False)
    time_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    mini_sector_speeds: Mapped[Any] = mapped_column(JSONB, nullable=True)

    lap_data: Mapped["LapData"] = relationship(back_populates="sector_times")
