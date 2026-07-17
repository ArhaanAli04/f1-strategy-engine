"""add_read_at_to_alerts

Revision ID: effbdf9da5cc
Revises: 45bbf8ae4598
Create Date: 2026-07-12 23:46:18.192754

Day 11: GET /alerts?unread=true and PUT /alerts/{id}/read need a read-tracking
column. delivered_at already exists but tracks push/WS delivery, not user
acknowledgement — reusing it would conflate the two, so this adds a separate
nullable read_at instead.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "effbdf9da5cc"
down_revision: str | None = "45bbf8ae4598"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("alerts", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("alerts", "read_at")
