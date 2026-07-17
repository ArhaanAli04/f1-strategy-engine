"""add_fcm_token_to_users

Revision ID: 45bbf8ae4598
Revises: 51eedcdad199
Create Date: 2026-07-11 22:29:06.268129

fcm_token was deferred from Day 6, when alert_worker.py found no device
token column on User (see CLAUDE.md Deferred Schema Changes). Added here
alongside Day 10's auth endpoints, which include PUT /auth/fcm-token for
clients to register their token after requesting push permission.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "45bbf8ae4598"
down_revision: str | None = "51eedcdad199"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("fcm_token", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "fcm_token")
