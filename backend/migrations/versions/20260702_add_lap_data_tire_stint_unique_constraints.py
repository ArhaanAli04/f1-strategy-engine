"""add_lap_data_tire_stint_unique_constraints

Revision ID: c870686b5687
Revises: b2e4f6a8c0d1
Create Date: 2026-07-02 00:00:00

Adds composite unique constraints required for idempotent ingestion.
scripts/ingest_historical.py upserts LapData and TireStint rows with
ON CONFLICT DO NOTHING so re-running ingestion for a session that was
already loaded does not create duplicate rows. Postgres requires a unique
constraint (or index) on the conflict target for that clause to work.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c870686b5687"
down_revision: str | None = "b2e4f6a8c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_lap_data_session_driver_lap",
        "lap_data",
        ["session_id", "driver_id", "lap_number"],
    )
    op.create_unique_constraint(
        "uq_tire_stints_session_driver_stint",
        "tire_stints",
        ["session_id", "driver_id", "stint_number"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_tire_stints_session_driver_stint", "tire_stints", type_="unique")
    op.drop_constraint("uq_lap_data_session_driver_lap", "lap_data", type_="unique")
