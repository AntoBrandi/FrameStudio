"""Elements: characters, locations and props referenced by @handle (M4.1).

Revision ID: 0009_elements
Revises: 0008_generation_frames
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_elements"
down_revision = "0008_generation_frames"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "element_folders",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_element_folders_project_id", "element_folders", ["project_id"])
    op.create_table(
        "elements",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("scope_key", sa.String(length=32), nullable=False),
        sa.Column("folder_id", sa.String(length=32), nullable=True),
        sa.Column("handle", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["folder_id"], ["element_folders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_elements_project_id", "elements", ["project_id"])
    op.create_index("ux_elements_scope_handle", "elements", ["scope_key", "handle"], unique=True)
    op.create_table(
        "element_media",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("element_id", sa.String(length=32), nullable=False),
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["element_id"], ["elements.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_element_media_element_id", "element_media", ["element_id"])
    op.create_index("ix_element_media_asset_id", "element_media", ["asset_id"])
    op.create_table(
        "element_uses",
        sa.Column("element_id", sa.String(length=32), nullable=False),
        sa.Column("generation_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["element_id"], ["elements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["generation_id"], ["generations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("element_id", "generation_id"),
    )
    op.create_index("ix_element_uses_generation_id", "element_uses", ["generation_id"])


def downgrade() -> None:
    op.drop_table("element_uses")
    op.drop_table("element_media")
    op.drop_table("elements")
    op.drop_table("element_folders")
