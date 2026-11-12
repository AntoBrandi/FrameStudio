"""Library: projects, folders, asset metadata, full-text search (M1).

Revision ID: 0004_library
Revises: 0003_generations_assets
Create Date: 2026-11-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_library"
down_revision = "0003_generations_assets"
branch_labels = None
depends_on = None

# Full-text index over what people search assets by. Not an ORM table: it is
# kept in step by framestudio.library.search and ignored by model comparison.
FTS_TABLE = "asset_search"


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=140), nullable=False),
        sa.Column("description", sa.String(length=140), server_default="", nullable=False),
        sa.Column("cover_asset_id", sa.String(length=32), nullable=True),
        sa.Column("brief", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["cover_asset_id"],
            ["assets.id"],
            name=op.f("fk_projects_cover_asset_id_assets"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("slug", name=op.f("uq_projects_slug")),
    )
    op.create_table(
        "folders",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("parent_id", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["folders.id"], name=op.f("fk_folders_parent_id_folders"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_folders_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_folders")),
    )
    op.create_index("ix_folders_project_id", "folders", ["project_id"])

    with op.batch_alter_table("assets") as batch:
        batch.add_column(sa.Column("video_codec", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("audio_codec", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("thumb_path", sa.String(length=300), nullable=True))
        batch.add_column(sa.Column("poster_path", sa.String(length=300), nullable=True))
        batch.add_column(sa.Column("preview_path", sa.String(length=300), nullable=True))
        batch.add_column(
            sa.Column("provenance", sa.String(length=16), server_default="generated", nullable=False)
        )
        batch.add_column(sa.Column("original_name", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("project_id", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("folder_id", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column("favorite", sa.Boolean(), server_default=sa.text("0"), nullable=False)
        )
        batch.add_column(sa.Column("notes", sa.Text(), server_default="", nullable=False))
        batch.add_column(sa.Column("caption", sa.Text(), nullable=True))
        batch.add_column(sa.Column("tags", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("last_viewed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("last_downloaded_at", sa.DateTime(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_assets_project_id_projects"), "projects", ["project_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_foreign_key(
            op.f("fk_assets_folder_id_folders"), "folders", ["folder_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_assets_project_id", ["project_id"])
        batch.create_index("ix_assets_folder_id", ["folder_id"])
        batch.create_index("ix_assets_deleted_created", ["deleted_at", "created_at"])

    with op.batch_alter_table("generations") as batch:
        batch.add_column(sa.Column("project_id", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("folder_id", sa.String(length=32), nullable=True))
        batch.create_foreign_key(
            op.f("fk_generations_project_id_projects"),
            "projects",
            ["project_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            op.f("fk_generations_folder_id_folders"),
            "folders",
            ["folder_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.execute(
        f"CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5("
        "asset_id UNINDEXED, prompt, name, caption, notes, "
        "tokenize = 'unicode61 remove_diacritics 2')"
    )
    op.execute(
        f"INSERT INTO {FTS_TABLE} (asset_id, prompt, name, caption, notes) "
        "SELECT a.id, coalesce(g.prompt, ''), coalesce(a.original_name, ''), "
        "coalesce(a.caption, ''), coalesce(a.notes, '') "
        "FROM assets a LEFT JOIN generations g ON g.id = a.source_generation_id"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE {FTS_TABLE}")
    with op.batch_alter_table("generations") as batch:
        batch.drop_constraint(op.f("fk_generations_folder_id_folders"), type_="foreignkey")
        batch.drop_constraint(op.f("fk_generations_project_id_projects"), type_="foreignkey")
        batch.drop_column("folder_id")
        batch.drop_column("project_id")
    with op.batch_alter_table("assets") as batch:
        batch.drop_index("ix_assets_deleted_created")
        batch.drop_index("ix_assets_folder_id")
        batch.drop_index("ix_assets_project_id")
        batch.drop_constraint(op.f("fk_assets_folder_id_folders"), type_="foreignkey")
        batch.drop_constraint(op.f("fk_assets_project_id_projects"), type_="foreignkey")
        for column in (
            "last_downloaded_at",
            "last_viewed_at",
            "tags",
            "caption",
            "notes",
            "favorite",
            "folder_id",
            "project_id",
            "original_name",
            "provenance",
            "preview_path",
            "poster_path",
            "thumb_path",
            "audio_codec",
            "video_codec",
        ):
            batch.drop_column(column)
    op.drop_index("ix_folders_project_id", table_name="folders")
    op.drop_table("folders")
    op.drop_table("projects")
