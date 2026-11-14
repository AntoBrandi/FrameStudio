"""Reference images on generations (M2.5).

Revision ID: 0006_generation_references
Revises: 0005_connections_usage
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_generation_references"
down_revision = "0005_connections_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("generations") as batch:
        batch.add_column(sa.Column("reference_ids", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("generations") as batch:
        batch.drop_column("reference_ids")
