"""Run Alembic migrations programmatically (on start-up and from tests)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from framestudio.db.session import Database

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(db: Database) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    cfg.set_main_option("sqlalchemy.url", db.url.replace("%", "%%"))
    return cfg


def upgrade(db: Database, revision: str = "head") -> None:
    cfg = alembic_config(db)
    with db.engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, revision)


def downgrade(db: Database, revision: str) -> None:
    cfg = alembic_config(db)
    with db.engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.downgrade(cfg, revision)


def current_revision(db: Database) -> str | None:
    with db.engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def script_directory() -> ScriptDirectory:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    return ScriptDirectory.from_config(cfg)


def head_revision() -> str | None:
    return script_directory().get_current_head()
