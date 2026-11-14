"""What the model was given, stored with each generation (M4.4).

Revision ID: 0011_generation_resolved
Revises: 0010_prompt_tokens
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_generation_resolved"
down_revision = "0010_prompt_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("generations") as batch:
        batch.add_column(sa.Column("resolved", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("generations") as batch:
        batch.drop_column("resolved")
