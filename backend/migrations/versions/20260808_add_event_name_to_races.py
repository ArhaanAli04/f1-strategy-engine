"""add_event_name_to_races

Revision ID: 387bd462a4fd
Revises: 99f0beb45b8c
Create Date: 2026-08-08 14:06:04.765827

Circuit Map Panel (feature/circuit-map) checkpoint C: GET /races/upcoming
needs a human-readable race name (e.g. "Bahrain Grand Prix") to display —
distinct from Circuit.name (the venue) and not previously stored anywhere.
Sourced from FastF1's event-schedule EventName column at race-creation time
(both the ingest scripts and race_service.get_upcoming_race's FastF1-fallback
path populate it going forward). Nullable: historical rows created before
this column existed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "387bd462a4fd"
down_revision: str | None = "99f0beb45b8c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("races", sa.Column("event_name", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("races", "event_name")
