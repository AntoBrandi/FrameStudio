"""Prompts stored as token lists (M4.3).

Revision ID: 0010_prompt_tokens
Revises: 0009_elements
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_prompt_tokens"
down_revision = "0009_elements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("generations") as batch:
        batch.add_column(sa.Column("prompt_tokens", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("generations") as batch:
        batch.drop_column("prompt_tokens")
