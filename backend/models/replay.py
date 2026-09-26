"""Precomputed Demo Replay data, written offline and played back without a worker.

A Demo Replay in production does not run the ML pipeline live: the
precompute script (docs/internal/demo-deployment-plan-2026.md, Day 2) runs the
same prediction code once per curated lap window and stores its output, and
the playback process republishes it on a real-time clock (Day 4). Predictions
themselves go to the existing strategy_predictions table; these three tables
hold the rest of what a replay needs.

Times are seconds on FastF1's session clock (Lap.LapStartTime / Lap.Time), the
same clock replay_pipeline.py already uses to line positions up with laps.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class ReplayLapTiming(Base):
    """When one driver started and finished one lap, so playback can emit each
    driver's lap-completion event at the moment they really crossed the line."""

    __tablename__ = "replay_lap_timings"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "driver_id", "lap_number", name="uq_replay_lap_timings_session_driver_lap"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id"), nullable=False
    )
    lap_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Nullable: FastF1 leaves LapStartTime/Time empty for some laps (e.g. a
    # car that retires mid-lap); playback skips an event it has no time for.
    lap_start_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    lap_end_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)


class ReplayGapSnapshot(Base):
    """The whole field's order and gaps as each lap begins — the payload the
    timing tower reads from f1:{season}:{round}:gaps."""

    __tablename__ = "replay_gap_snapshots"
    __table_args__ = (
        UniqueConstraint("session_id", "lap_number", name="uq_replay_gap_snapshots_session_lap"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    lap_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # SessionGapsResponse-shaped, exactly as replay_pipeline._compute_lap_gaps
    # builds it (including "source": "replay"), so playback publishes it as is.
    gaps: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReplayAlertEvent(Base):
    """An alert the live pipeline would have raised on this lap. Playback turns
    each one into Alert rows for the users subscribed at replay time."""

    __tablename__ = "replay_alert_events"
    __table_args__ = (Index("ix_replay_alert_events_session_lap", "session_id", "lap_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    lap_number: Mapped[int] = mapped_column(Integer, nullable=False)
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # The driver the alert is about (for an undercut threat, the trailing car
    # that can undercut) — subscribers of this driver receive it.
    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id"), nullable=False
    )
    # The other car in the pairing (for an undercut threat, the car ahead).
    rival_driver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id"), nullable=True
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
