"""widen_compound_columns

Revision ID: d9a1c3e5f7b2
Revises: c870686b5687
Create Date: 2026-07-02 00:01:00

lap_data.compound and tire_stints.compound were String(10), but FastF1
reports compound values like 'INTERMEDIATE' (12 chars) and 'TEST-UNKNOWN'
(13 chars), which overflow that width. Widened to String(20).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9a1c3e5f7b2"
down_revision: str | None = "c870686b5687"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("lap_data", "compound", type_=sa.String(20))
    op.alter_column("tire_stints", "compound", type_=sa.String(20))


def downgrade() -> None:
    op.alter_column("tire_stints", "compound", type_=sa.String(10))
    op.alter_column("lap_data", "compound", type_=sa.String(10))
