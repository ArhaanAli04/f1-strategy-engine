"""add_circuit_map_geometry_and_session_scheduled_start

Revision ID: 99f0beb45b8c
Revises: 7d43a426d1c7
Create Date: 2026-08-08 13:43:53.052695

Circuit Map Panel (feature/circuit-map) checkpoint A: schema only.

circuits.map_geometry holds the rotated/normalized track polyline + viewBox
extracted one-time per circuit via scripts/extract_circuit_outlines.py (from
a real FastF1 session's fastest-lap telemetry) — same coordinate frame the
live Position.z feed will use, so live driver dots line up with this outline
without per-circuit calibration. Null until that script runs for a circuit.

sessions.scheduled_start is the real session start instant (date + time),
sourced from Ergast's per-session date/time columns at ingestion —
session_date alone (a plain Date) can't back a countdown timer. Nullable:
historical rows ingested before this column existed, and any session Ergast
doesn't carry a time for.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "99f0beb45b8c"
down_revision: str | None = "7d43a426d1c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "circuits",
        sa.Column("map_geometry", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "sessions", sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sessions", "scheduled_start")
    op.drop_column("circuits", "map_geometry")
