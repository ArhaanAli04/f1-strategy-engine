import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.models.driver import Driver
    from backend.models.race import Session


class User(Base):
    __tablename__ = "users"
    # Explicit names match the DDL the initial_schema migration produced:
    # `unique=True` on the column creates `users_email_key` (unique constraint);
    # the separate `op.create_index` creates a non-unique `ix_users_email`.
    __table_args__ = (
        UniqueConstraint("email", name="users_email_key"),
        Index("ix_users_email", "email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # free, pro, team
    subscription_tier: Mapped[str] = mapped_column(String(20), nullable=False, default="free")
    fcm_token: Mapped[str | None] = mapped_column(String(255), nullable=True)

    alerts: Mapped[list["Alert"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Alert(Base):
    __tablename__ = "alerts"
    # Backs alert_service.get_alerts_for_user: WHERE user_id = :user_id [AND
    # read_at IS NULL] ORDER BY triggered_at DESC. DESC matches the ORDER BY
    # directly so Postgres can satisfy it via an index scan instead of a sort.
    __table_args__ = (Index("ix_alerts_user_triggered_at", "user_id", text("triggered_at DESC")),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No standalone index=True here: ix_alerts_user_triggered_at above already
    # leads with user_id, so a separate single-column index would be a pure
    # duplicate.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)
    driver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id"), nullable=True
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set when a user marks the alert as read (PUT /alerts/{id}/read). Distinct
    # from delivered_at, which tracks push/WS delivery, not user acknowledgement.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="alerts")
    session: Mapped["Session"] = relationship(back_populates="alerts")
    driver: Mapped["Driver | None"] = relationship(back_populates="alerts")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    driver_ids: Mapped[Any] = mapped_column(JSONB, nullable=False, default=list)
    team_ids: Mapped[Any] = mapped_column(JSONB, nullable=False, default=list)
    alert_types: Mapped[Any] = mapped_column(JSONB, nullable=False, default=list)

    user: Mapped["User"] = relationship(back_populates="subscriptions")
