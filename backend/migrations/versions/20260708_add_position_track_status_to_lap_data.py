"""add_position_track_status_to_lap_data

Revision ID: c049f6f51210
Revises: d9a1c3e5f7b2
Create Date: 2026-07-08 02:28:45.153416

FastF1's Laps dataframe already carries Position and TrackStatus per lap
(fetched at zero extra API cost alongside laps=True), but neither was ever
persisted. Day 7's pit_predictor needs position/gap features and
safety_car_model needs real TrackStatus-derived safety car events, so both
columns are added here and backfilled via
scripts/backfill_position_track_status.py.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c049f6f51210"
down_revision: str | None = "d9a1c3e5f7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lap_data", sa.Column("position", sa.Integer(), nullable=True))
    op.add_column("lap_data", sa.Column("track_status", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("lap_data", "track_status")
    op.drop_column("lap_data", "position")
