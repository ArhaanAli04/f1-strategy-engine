"""add_session_elapsed_seconds_to_lap_data

Revision ID: 67b065d3f40b
Revises: 9cd0463cbe60
Create Date: 2026-09-02 15:40:31.982676

lap_time_seconds is NULL for any out-lap/in-lap/SC lap FastF1 didn't record
a valid delta for, so SUM(lap_time_seconds) across drivers with differing
NULL-lap counts produces non-comparable cumulative totals — see CLAUDE.md's
Deferred Wiring item A ("NULL-lap cumulative-sum gap/race-time
reconstruction"). session_elapsed_seconds captures FastF1's `Time` column
directly (elapsed session time at lap completion, relative to the session's
own first LapStartTime) — confirmed populated on 100% of lap rows across
2020-2026, including every row with a NULL lap_time_seconds. Only
ingest_historical.py populates this going forward; backfill_lap_session_
time.py backfills existing rows (R sessions only). NULL for live-ingested
sessions — ingest_live_session.py's TimingData stream carries no absolute
session clock, and those sessions have their own authoritative Redis gaps
anyway (see _publish_live_gaps), so this column is deliberately left
unpopulated there rather than added complexity for a rarely-reached path.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "67b065d3f40b"
down_revision: str | None = "9cd0463cbe60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lap_data", sa.Column("session_elapsed_seconds", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("lap_data", "session_elapsed_seconds")
