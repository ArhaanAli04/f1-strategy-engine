"""enable_timescale_extension

Revision ID: b2e4f6a8c0d1
Revises: a1f3b2c4d5e6
Create Date: 2026-06-30 00:01:00

Installs the TimescaleDB extension.

Hypertable conversion for lap_data is intentionally deferred.
TimescaleDB requires every unique constraint on a hypertable to include the
partition column (created_at). The current schema has sector_times.lap_data_id
→ lap_data.id as a single-column FK, which requires a standalone UNIQUE(id)
on lap_data — forbidden by TimescaleDB.

The fix (a future migration) is to add lap_data_created_at to sector_times and
switch to a composite FK: (lap_data_id, lap_data_created_at) → lap_data(id, created_at).
Once that redesign is done, a follow-up migration can call create_hypertable.
See CLAUDE.md § Architecture Decisions for full rationale.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2e4f6a8c0d1"
down_revision: str | None = "a1f3b2c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"))


def downgrade() -> None:
    op.execute(sa.text("DROP EXTENSION IF EXISTS timescaledb CASCADE"))
