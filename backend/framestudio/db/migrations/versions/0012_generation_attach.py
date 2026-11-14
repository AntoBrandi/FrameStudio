"""Generations that add their result to an element (M4.6).

Revision ID: 0012_generation_attach
Revises: 0011_generation_resolved
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_generation_attach"
down_revision = "0011_generation_resolved"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("generations") as batch:
        batch.add_column(sa.Column("attach_element_id", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("attach_role", sa.String(length=16), nullable=True))
        batch.create_foreign_key(
            "fk_generations_attach_element_id_elements",
            "elements",
            ["attach_element_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("generations") as batch:
        batch.drop_constraint("fk_generations_attach_element_id_elements", type_="foreignkey")
        batch.drop_column("attach_role")
        batch.drop_column("attach_element_id")
