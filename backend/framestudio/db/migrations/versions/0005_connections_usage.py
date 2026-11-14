"""Connections to hosted providers and the usage ledger (M2.3).

Revision ID: 0005_connections_usage
Revises: 0004_library
Create Date: 2026-11-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0005_connections_usage"
down_revision = "0004_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("secret_name", sa.String(length=80), nullable=True),
        sa.Column("secret_backend", sa.String(length=16), nullable=True),
        sa.Column("key_hint", sa.String(length=8), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("status_message", sa.String(length=300), nullable=True),
        sa.Column("validated_at", sa.DateTime(), nullable=True),
        sa.Column("monthly_cap_usd", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("provider"),
    )
    op.create_table(
        "usage",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("month", sa.String(length=7), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("images", sa.Integer(), nullable=False),
        sa.Column("video_seconds", sa.Float(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("generation_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_provider_month", "usage", ["provider", "month"])


def downgrade() -> None:
    op.drop_index("ix_usage_provider_month", table_name="usage")
    op.drop_table("usage")
    op.drop_table("connections")
