"""Alembic environment.

Called by ``framestudio.db.migrate`` (with a live connection in
``config.attributes``) and by the ``alembic`` CLI from ``backend/`` (which
resolves the database from ``FRAMESTUDIO_DATA_DIR`` like the app does).
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection

from framestudio.db import models  # noqa: F401  (registers tables on the metadata)
from framestudio.db.base import Base

target_metadata = Base.metadata
config = context.config


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite cannot ALTER most things in place
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    from framestudio.config import Config
    from framestudio.db.session import Database
    from framestudio.paths import DataPaths

    url = config.get_main_option("sqlalchemy.url")
    if url:
        from sqlalchemy import create_engine

        engine = create_engine(url)
    else:
        paths = DataPaths(Config().data_dir).ensure()
        engine = Database(paths.database).engine
    with engine.begin() as conn:
        _run(conn)


if context.is_offline_mode():
    raise SystemExit("FrameStudio migrations run online only.")
run_migrations_online()
