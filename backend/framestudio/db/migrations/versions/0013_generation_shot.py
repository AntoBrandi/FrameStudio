"""The cinema controls of a generation (M5.1).

Revision ID: 0013_generation_shot
Revises: 0012_generation_attach
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_generation_shot"
down_revision = "0012_generation_attach"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("generations") as batch:
        batch.add_column(sa.Column("shot", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("generations") as batch:
        batch.drop_column("shot")
