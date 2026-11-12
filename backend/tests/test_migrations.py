"""M0.2: migrations run up and down on empty and fixture databases, and the
ORM models never drift from what the migrations create."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from framestudio.db import migrate
from framestudio.db.base import Base
from framestudio.db.session import Database


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    yield database
    database.dispose()


def _revisions() -> list[str]:
    script = migrate.script_directory()
    return [rev.revision for rev in reversed(list(script.walk_revisions()))]


def _tables(db: Database) -> set[str]:
    return set(inspect(db.engine).get_table_names()) - {"alembic_version"}


def test_upgrade_downgrade_upgrade_on_empty_database(db: Database) -> None:
    migrate.upgrade(db)
    assert migrate.current_revision(db) == migrate.head_revision()
    assert {"settings", "jobs", "generations", "assets", "projects", "folders"} <= _tables(db)
    migrate.downgrade(db, "base")
    assert _tables(db) == set()
    migrate.upgrade(db)
    assert migrate.current_revision(db) == migrate.head_revision()


def test_every_step_goes_up_down_up(db: Database) -> None:
    previous = "base"
    for revision in _revisions():
        migrate.upgrade(db, revision)
        migrate.downgrade(db, previous)
        migrate.upgrade(db, revision)
        previous = revision
    assert migrate.current_revision(db) == migrate.head_revision()


def test_fixture_database_keeps_its_data(db: Database) -> None:
    migrate.upgrade(db, "0001_baseline")
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO settings (key, value, updated_at) "
                "VALUES ('theme', '\"light\"', '2026-11-01 10:00:00')"
            )
        )
    migrate.upgrade(db)
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT value FROM settings WHERE key='theme'")).scalar_one() == (
            '"light"'
        )
    migrate.downgrade(db, "0001_baseline")
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM settings")).scalar_one() == 1


def test_models_match_migrations(db: Database) -> None:
    migrate.upgrade(db)
    with db.engine.connect() as conn:
        context = MigrationContext.configure(
            conn, opts={"compare_type": True, "include_object": migrate.include_object}
        )
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"Models and migrations disagree; add a migration: {diff}"


def test_single_head() -> None:
    script = migrate.script_directory()
    assert len(script.get_heads()) == 1


def test_rebuilding_assets_keeps_links_to_it(db: Database) -> None:
    """0004 rebuilds the assets table; rows pointing at assets must survive."""
    migrate.upgrade(db, "0003_generations_assets")
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO assets (id, kind, sha256, path, mime, size_bytes, created_at) "
                "VALUES ('a1', 'image', 'x', 'ab/cd/x.png', 'image/png', 1, '2026-11-01 10:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO generations (id, batch_id, batch_index, mode, model_id, provider, "
                "prompt, params, status, progress, parent_asset_id, created_at) VALUES "
                "('g1', 'b', 0, 'image', 'mock', 'mock', 'p', '{}', 'succeeded', 1, 'a1', "
                "'2026-11-01 10:00:00')"
            )
        )
    migrate.upgrade(db)
    with db.engine.connect() as conn:
        parent = conn.execute(text("SELECT parent_asset_id FROM generations")).scalar_one()
        provenance = conn.execute(text("SELECT provenance FROM assets")).scalar_one()
        indexed = conn.execute(text("SELECT count(*) FROM asset_search")).scalar_one()
    assert parent == "a1"
    assert provenance == "generated"
    assert indexed == 1
