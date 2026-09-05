from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from .config import settings
from .config import BACKEND_ROOT


class Base(DeclarativeBase):
    pass


_is_sqlite = settings.DB_URL.startswith("sqlite")
# For SQLite, give writers a busy timeout so concurrent startup writes (many
# accounts connecting at once) wait for the file lock instead of raising
# "database is locked".
_connect_args = {"timeout": 30} if _is_sqlite else {}

engine = create_async_engine(settings.DB_URL, echo=False, future=True, connect_args=_connect_args)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

if _is_sqlite:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):
        # WAL lets readers and a writer coexist; busy_timeout backs up the
        # connect_args timeout; NORMAL sync is the standard WAL pairing.
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


async def init_db():
    """Pre-migration hooks. Kept minimal: the authoritative schema now lives in
    Django-style Alembic migrations (see run_migrations). Legacy databases that
    predate Alembic are stamped to the baseline instead of being re-created."""
    pass


async def run_migrations():
    """Upgrade the local schema to the bundled Alembic head revision.

    Standardized bootstrap so a 2nd (or later) migration is safe:
      - A truly empty database: `upgrade head` runs the baseline DDL (0001)
        followed by any newer revisions -> full schema, tracked by Alembic.
      - A pre-existing database with tables but no alembic_version (created by
        the old ``create_all`` flow): stamp the *baseline* revision only, then
        `upgrade head` so new revisions build on the assumed-baseline tables
        instead of failing with "duplicate column" (which is what would happen
        if create_all continued to build from the *latest* models).
    """
    import asyncio
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect as sa_inspect

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    # The desktop executable can run from any working directory, so never
    # resolve Alembic scripts relative to cwd.
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)
    root_rev = None
    for rev in script.walk_revisions():
        if rev.down_revision is None:
            root_rev = rev.revision
            break

    def _stamp_legacy_if_needed():
        """Stamp a pre-Alembic database to the baseline so migrations can run."""
        engine = create_engine(settings.DB_URL.replace("+aiosqlite", ""))
        try:
            insp = sa_inspect(engine)
            tables = insp.get_table_names()
            if "accounts" in tables and "alembic_version" not in tables:
                command.stamp(config, root_rev or "head")
        finally:
            engine.dispose()

    def _upgrade():
        command.upgrade(config, "head")

    await asyncio.to_thread(_stamp_legacy_if_needed)
    await asyncio.to_thread(_upgrade)


async def check_database_integrity() -> tuple[bool, str]:
    """Run SQLite's lightweight startup integrity check."""
    if not _is_sqlite:
        return True, "ok"
    try:
        async with engine.connect() as conn:
            result = await conn.exec_driver_sql("PRAGMA quick_check")
            rows = [str(row[0]) for row in result.fetchall()]
        ok = rows == ["ok"]
        return ok, "ok" if ok else "; ".join(rows[:10])
    except Exception as exc:
        return False, type(exc).__name__


async def shutdown_db():
    """Flush SQLite WAL state and close pooled connections cleanly."""
    try:
        if _is_sqlite:
            async with engine.begin() as conn:
                await conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        await engine.dispose()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
