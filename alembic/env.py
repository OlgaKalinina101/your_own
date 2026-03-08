"""Alembic environment — async-compatible, reads DATABASE_URL from .env."""
import asyncio
import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

load_dotenv()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override URL from environment so alembic.ini doesn't need a real password
db_url = os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url") or "")
db_url = (
    db_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    .replace("postgresql://", "postgresql+asyncpg://")
)
config.set_main_option("sqlalchemy.url", db_url)

# Import all models so their metadata is registered
from infrastructure.database.engine import Base  # noqa: E402
import infrastructure.database.models  # noqa: F401, E402 — registers Message

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
