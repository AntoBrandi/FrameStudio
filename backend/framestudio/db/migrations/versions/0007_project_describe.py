"""Opt-in image descriptions per project (M2.6).

Revision ID: 0007_project_describe
Revises: 0006_generation_references
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_project_describe"
down_revision = "0006_generation_references"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(
            sa.Column("describe_images", sa.Boolean(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("describe_images")
