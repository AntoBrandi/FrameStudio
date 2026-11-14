"""Start and end frames on generations (M2.8).

Revision ID: 0008_generation_frames
Revises: 0007_project_describe
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_generation_frames"
down_revision = "0007_project_describe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("generations") as batch:
        batch.add_column(sa.Column("start_frame_id", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("end_frame_id", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("generations") as batch:
        batch.drop_column("end_frame_id")
        batch.drop_column("start_frame_id")
