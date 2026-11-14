"""Planned shots: keyframes first, then the clip (M5.7).

Revision ID: 0015_shots
Revises: 0014_presets
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_shots"
down_revision = "0014_presets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shots",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("folder_id", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.JSON(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=True),
        sa.Column("video_model", sa.String(length=128), nullable=False),
        sa.Column("video_params", sa.JSON(), nullable=False),
        sa.Column("keyframe_model", sa.String(length=128), nullable=False),
        sa.Column("keyframe_params", sa.JSON(), nullable=False),
        sa.Column("keyframe_id", sa.String(length=32), nullable=True),
        sa.Column("end_frame_id", sa.String(length=32), nullable=True),
        sa.Column("continues_from", sa.String(length=32), nullable=True),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("archived", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_shots_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["folder_id"],
            ["folders.id"],
            name=op.f("fk_shots_folder_id_folders"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["keyframe_id"],
            ["assets.id"],
            name=op.f("fk_shots_keyframe_id_assets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["end_frame_id"],
            ["assets.id"],
            name=op.f("fk_shots_end_frame_id_assets"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shots")),
    )
    op.create_index(op.f("ix_shots_project_id"), "shots", ["project_id"], unique=False)
    op.create_index(op.f("ix_shots_created_at"), "shots", ["created_at"], unique=False)
    with op.batch_alter_table("generations") as batch:
        batch.add_column(sa.Column("shot_id", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("shot_role", sa.String(length=16), nullable=True))
        batch.create_index(batch.f("ix_generations_shot_id"), ["shot_id"], unique=False)
        batch.create_foreign_key(
            "fk_generations_shot_id_shots", "shots", ["shot_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("generations") as batch:
        batch.drop_constraint("fk_generations_shot_id_shots", type_="foreignkey")
        batch.drop_index(batch.f("ix_generations_shot_id"))
        batch.drop_column("shot_role")
        batch.drop_column("shot_id")
    op.drop_index(op.f("ix_shots_created_at"), table_name="shots")
    op.drop_index(op.f("ix_shots_project_id"), table_name="shots")
    op.drop_table("shots")
