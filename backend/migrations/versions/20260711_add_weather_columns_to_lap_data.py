"""add_weather_columns_to_lap_data

Revision ID: 51eedcdad199
Revises: c049f6f51210
Create Date: 2026-07-11 02:57:57.630158

track_temp/air_temp were dropped from tire_deg_model's original Day 7 spec
because ingest_historical.py never loaded FastF1 sessions with weather=True
(see CLAUDE.md Deferred Schema Changes). Both columns are added here and
backfilled via scripts/backfill_weather_data.py.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "51eedcdad199"
down_revision: str | None = "c049f6f51210"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lap_data", sa.Column("track_temp", sa.Float(), nullable=True))
    op.add_column("lap_data", sa.Column("air_temp", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("lap_data", "air_temp")
    op.drop_column("lap_data", "track_temp")
