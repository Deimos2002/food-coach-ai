"""Alembic environment: plain SQLAlchemy Core, psycopg 3, no autogenerate metadata."""

from __future__ import annotations

from logging.config import fileConfig

import sqlalchemy as sa
from alembic import context

from app.config import get_settings

config = context.config

# Only configure logging when run from the CLI: callers that pass a connection or set
# ``configure_logger = False`` (tests) keep their own logging setup untouched.
if (
    config.config_file_name is not None
    and config.attributes.get("configure_logger", True)
    and "connection" not in config.attributes
):
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = sa.create_engine(_url(), poolclass=sa.pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
