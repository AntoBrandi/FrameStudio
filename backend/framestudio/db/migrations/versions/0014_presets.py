"""Saved presets (M5.4 palettes, M5.5 lighting rigs).

Revision ID: 0014_presets
Revises: 0013_generation_shot
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_presets"
down_revision = "0013_generation_shot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "presets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("preview_asset_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["preview_asset_id"],
            ["assets.id"],
            name=op.f("fk_presets_preview_asset_id_assets"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_presets")),
    )
    op.create_index(op.f("ix_presets_kind"), "presets", ["kind"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_presets_kind"), table_name="presets")
    op.drop_table("presets")
