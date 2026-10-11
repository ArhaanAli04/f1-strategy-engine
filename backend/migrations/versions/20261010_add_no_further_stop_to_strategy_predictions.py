"""add no_further_stop to strategy_predictions

Demo deployment Day 6c CP2: the pit-window engine can recommend running the
current tyre to the flag. Existing rows get false.

Revision ID: 8dee9ea8d237
Revises: e208a452244d
Create Date: 2026-10-10 17:10:21.825927

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8dee9ea8d237"
down_revision: str | None = "e208a452244d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "strategy_predictions",
        sa.Column("no_further_stop", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("strategy_predictions", "no_further_stop")
