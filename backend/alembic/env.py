from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.db import Base
from app import models  # noqa: F401

config = context.config
if config.config_file_name:
    # Do NOT disable our application loggers (main, uvicorn, tg_manager, etc.)
    # that were already configured via app.logging_config. The default
    # fileConfig(disable_existing_loggers=True) would set
    # logging.getLogger("main").disabled = True and replace the root
    # handlers (removing data/logs/app.log), which is why "backend startup
    # complete" and "Uvicorn running..." vanished from the file after the
    # alembic upgrade step.
    fileConfig(config.config_file_name, disable_existing_loggers=False)
config.set_main_option("sqlalchemy.url", settings.DB_URL.replace("+aiosqlite", ""))
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
