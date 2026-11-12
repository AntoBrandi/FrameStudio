"""Generations and assets (M0.5).

Revision ID: 0003_generations_assets
Revises: 0002_jobs
Create Date: 2026-11-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_generations_assets"
down_revision = "0002_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("batch_id", sa.String(length=32), nullable=False),
        sa.Column("batch_index", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("variant", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("parent_asset_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_generations_job_id_jobs"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["parent_asset_id"],
            ["assets.id"],
            name=op.f("fk_generations_parent_asset_id_assets"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generations")),
    )
    op.create_index("ix_generations_batch_id", "generations", ["batch_id"])
    op.create_index("ix_generations_job_id", "generations", ["job_id"])
    op.create_index("ix_generations_created_at", "generations", ["created_at"])
    op.create_table(
        "assets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(length=300), nullable=False),
        sa.Column("mime", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("fps", sa.Float(), nullable=True),
        sa.Column("has_audio", sa.Boolean(), nullable=True),
        sa.Column("source_generation_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_generation_id"],
            ["generations.id"],
            name=op.f("fk_assets_source_generation_id_generations"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
    )
    op.create_index("ix_assets_sha256", "assets", ["sha256"])
    op.create_index("ix_assets_source_generation_id", "assets", ["source_generation_id"])
    op.create_index("ix_assets_created_at", "assets", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_assets_created_at", table_name="assets")
    op.drop_index("ix_assets_source_generation_id", table_name="assets")
    op.drop_index("ix_assets_sha256", table_name="assets")
    op.drop_table("assets")
    op.drop_index("ix_generations_created_at", table_name="generations")
    op.drop_index("ix_generations_job_id", table_name="generations")
    op.drop_index("ix_generations_batch_id", table_name="generations")
    op.drop_table("generations")
