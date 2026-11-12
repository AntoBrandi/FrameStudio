"""Run Alembic migrations programmatically (on start-up and from tests)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from framestudio.db.session import Database

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
#: Tables managed outside the ORM (the FTS5 index and its shadow tables).
UNMANAGED_PREFIXES = ("asset_search",)


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Leave unmanaged tables out of autogenerate comparisons."""
    return not (type_ == "table" and name is not None and name.startswith(UNMANAGED_PREFIXES))


def alembic_config(db: Database) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    cfg.set_main_option("sqlalchemy.url", db.url.replace("%", "%%"))
    return cfg


def _run(db: Database, action: str, revision: str) -> None:
    """Run with foreign keys off, as SQLite batch migrations require.

    Batch mode rebuilds a table by copying it and dropping the original; with
    foreign keys on, that drop would cascade (ON DELETE SET NULL) into the rows
    that reference it. The check afterwards proves the copy kept every link.
    """
    cfg = alembic_config(db)
    with db.engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            cfg.attributes["connection"] = connection
            getattr(command, action)(cfg, revision)
            connection.commit()
            broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise RuntimeError(f"migration left broken foreign keys: {broken[:5]}")
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def upgrade(db: Database, revision: str = "head") -> None:
    _run(db, "upgrade", revision)


def downgrade(db: Database, revision: str) -> None:
    _run(db, "downgrade", revision)


def current_revision(db: Database) -> str | None:
    with db.engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def script_directory() -> ScriptDirectory:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    return ScriptDirectory.from_config(cfg)


def head_revision() -> str | None:
    return script_directory().get_current_head()
