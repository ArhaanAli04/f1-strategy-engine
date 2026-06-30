"""initial_schema

Revision ID: a1f3b2c4d5e6
Revises:
Create Date: 2026-06-30 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "a1f3b2c4d5e6"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "circuits",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("country", sa.String(80), nullable=False),
        sa.Column("track_length_km", sa.Float(), nullable=False),
        sa.Column("lap_record_seconds", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )

    op.create_table(
        "races",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column(
            "circuit_id",
            UUID(as_uuid=True),
            sa.ForeignKey("circuits.id"),
            nullable=False,
        ),
        sa.Column("race_date", sa.Date(), nullable=False),
        sa.Column("weather", sa.String(50), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "race_id",
            UUID(as_uuid=True),
            sa.ForeignKey("races.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_type", sa.String(3), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
    )
    op.create_index("ix_sessions_race_id", "sessions", ["race_id"])

    op.create_table(
        "drivers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(3), unique=True, nullable=False),
        sa.Column("full_name", sa.String(100), nullable=False),
        sa.Column("nationality", sa.String(60), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
    )

    op.create_table(
        "teams",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("constructor_id", sa.String(50), unique=True, nullable=False),
        sa.Column("color_hex", sa.String(7), nullable=False),
    )

    op.create_table(
        "driver_contracts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "driver_id",
            UUID(as_uuid=True),
            sa.ForeignKey("drivers.id"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            UUID(as_uuid=True),
            sa.ForeignKey("teams.id"),
            nullable=False,
        ),
        sa.Column("season", sa.Integer(), nullable=False),
    )
    op.create_index("ix_driver_contracts_driver_id", "driver_contracts", ["driver_id"])
    op.create_index("ix_driver_contracts_team_id", "driver_contracts", ["team_id"])

    op.create_table(
        "lap_data",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "driver_id",
            UUID(as_uuid=True),
            sa.ForeignKey("drivers.id"),
            nullable=False,
        ),
        sa.Column("lap_number", sa.Integer(), nullable=False),
        sa.Column("lap_time_seconds", sa.Float(), nullable=True),
        sa.Column("compound", sa.String(10), nullable=False),
        sa.Column("tyre_age_laps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_valid", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sector1_seconds", sa.Float(), nullable=True),
        sa.Column("sector2_seconds", sa.Float(), nullable=True),
        sa.Column("sector3_seconds", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.create_index("ix_lap_data_session_id", "lap_data", ["session_id"])
    op.create_index("ix_lap_data_driver_id", "lap_data", ["driver_id"])

    op.create_table(
        "tire_stints",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "driver_id",
            UUID(as_uuid=True),
            sa.ForeignKey("drivers.id"),
            nullable=False,
        ),
        sa.Column("stint_number", sa.Integer(), nullable=False),
        sa.Column("compound", sa.String(10), nullable=False),
        sa.Column("start_lap", sa.Integer(), nullable=False),
        sa.Column("end_lap", sa.Integer(), nullable=True),
        sa.Column("avg_deg_per_lap", sa.Float(), nullable=True),
    )
    op.create_index("ix_tire_stints_session_id", "tire_stints", ["session_id"])
    op.create_index("ix_tire_stints_driver_id", "tire_stints", ["driver_id"])

    op.create_table(
        "sector_times",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "lap_data_id",
            UUID(as_uuid=True),
            sa.ForeignKey("lap_data.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sector", sa.Integer(), nullable=False),
        sa.Column("time_seconds", sa.Float(), nullable=False),
        sa.Column("mini_sector_speeds", JSONB(), nullable=True),
    )
    op.create_index("ix_sector_times_lap_data_id", "sector_times", ["lap_data_id"])

    op.create_table(
        "strategy_predictions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "driver_id",
            UUID(as_uuid=True),
            sa.ForeignKey("drivers.id"),
            nullable=False,
        ),
        sa.Column("predicted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("optimal_pit_lap", sa.Integer(), nullable=False),
        sa.Column("pit_probability", sa.Float(), nullable=False),
        sa.Column("undercut_score", sa.Float(), nullable=False),
        sa.Column("overcut_score", sa.Float(), nullable=False),
        sa.Column("tire_life_remaining", sa.Float(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("model_version", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.create_index("ix_strategy_predictions_session_id", "strategy_predictions", ["session_id"])
    op.create_index("ix_strategy_predictions_driver_id", "strategy_predictions", ["driver_id"])

    op.create_table(
        "pit_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "driver_id",
            UUID(as_uuid=True),
            sa.ForeignKey("drivers.id"),
            nullable=False,
        ),
        sa.Column("lap_number", sa.Integer(), nullable=False),
        sa.Column("compound_in", sa.String(10), nullable=False),
        sa.Column("compound_out", sa.String(10), nullable=False),
        sa.Column("pit_duration_seconds", sa.Float(), nullable=True),
        sa.Column("was_predicted", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_pit_events_session_id", "pit_events", ["session_id"])
    op.create_index("ix_pit_events_driver_id", "pit_events", ["driver_id"])

    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), unique=True, nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(150), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("subscription_tier", sa.String(20), nullable=False, server_default="free"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "alerts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alert_type", sa.String(50), nullable=False),
        sa.Column(
            "driver_id",
            UUID(as_uuid=True),
            sa.ForeignKey("drivers.id"),
            nullable=True,
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_alerts_user_id", "alerts", ["user_id"])
    op.create_index("ix_alerts_session_id", "alerts", ["session_id"])

    op.create_table(
        "subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("driver_ids", JSONB(), nullable=False, server_default="[]"),
        sa.Column("team_ids", JSONB(), nullable=False, server_default="[]"),
        sa.Column("alert_types", JSONB(), nullable=False, server_default="[]"),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])


def downgrade() -> None:
    op.drop_table("subscriptions")
    op.drop_table("alerts")
    op.drop_table("users")
    op.drop_table("pit_events")
    op.drop_table("strategy_predictions")
    op.drop_table("sector_times")
    op.drop_table("tire_stints")
    op.drop_table("lap_data")
    op.drop_table("driver_contracts")
    op.drop_table("teams")
    op.drop_table("drivers")
    op.drop_table("sessions")
    op.drop_table("races")
    op.drop_table("circuits")
