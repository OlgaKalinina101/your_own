"""Async SQLAlchemy engine + session factory."""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from settings import settings

DATABASE_URL = settings.DATABASE_URL
DATABASE_URL = (
    DATABASE_URL
    .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    .replace("postgresql://", "postgresql+asyncpg://")
)

def _session_timezone() -> str:
    """The user's timezone, for Postgres' own session.

    It does not change any value this application reads: a ``timestamptz`` is an
    instant, asyncpg hands it back UTC-aware, and every conversion happens in
    ``infrastructure.clock``. It matters for everything *else* that touches the
    database — ``psql``, a ``now()`` or a ``::date`` in hand-written SQL — which
    would otherwise silently use whatever the server was installed with.
    Measured on this machine: Postgres said ``Europe/Moscow`` while the app said
    ``Asia/Yerevan``.

    Read once, at engine creation: changing the setting reaches Python
    immediately and Postgres at the next restart.
    """
    try:
        from infrastructure.clock import timezone_name

        return timezone_name()
    except Exception:  # settings unreadable this early — UTC is the safe answer
        return "UTC"


engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    connect_args={"server_settings": {"timezone": _session_timezone()}},
    # Defaults are 5 + 10. One person's desktop never notices; a server with a
    # phone, a browser and two workers on it can, and the symptom is a request
    # that hangs waiting for a connection rather than an error that says so.
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

Base = declarative_base()


class DatabaseUnavailable(Exception):
    """Postgres could not be reached.

    A distinct type because the alternative is guessing. SQLAlchemy does **not**
    wrap a failed connect: what comes out of ``session.execute`` with the server
    down is a bare ``ConnectionRefusedError`` — an ``OSError``, exactly like the
    one aiohttp raises when OpenRouter is unreachable. Catching ``OSError``
    somewhere central would have reported a dead OpenRouter as a dead database.

    So the translation happens here, at the one boundary that knows which
    connection just failed.
    """


async def _connect_or_explain(session) -> None:
    """Establish the connection now, so a failure is unambiguously the database.

    Doing it up front rather than on first use is the whole point: inside a
    request handler an ``OSError`` could have come from anything, and by then
    the context is lost.
    """
    try:
        await session.connection()
    except Exception as exc:
        raise DatabaseUnavailable(str(exc)) from exc


async def _safe_rollback(session) -> None:
    """Roll back, tolerating a connection that has already gone.

    A rollback on a dead socket raises, and that second exception would replace
    the first one — the useful one — on its way out.
    """
    try:
        await session.rollback()
    except Exception:
        pass


async def get_db():
    """Per-request session.

    The rollback matters: without it a handler that raises halfway returns its
    session to the pool with a transaction still open, and ``pool_pre_ping``
    does not notice — it checks that the connection is alive, not that it is
    clean. The next borrower inherits the mess.
    """
    async with AsyncSessionLocal() as session:
        await _connect_or_explain(session)
        try:
            yield session
        except Exception:
            await _safe_rollback(session)
            raise


from contextlib import asynccontextmanager


@asynccontextmanager
async def get_db_session():
    """Context-manager variant of get_db, for workers and scripts.

    Raises the same :class:`DatabaseUnavailable`, so a worker's log line names
    the database instead of showing a Windows socket error code.
    """
    async with AsyncSessionLocal() as session:
        await _connect_or_explain(session)
        try:
            yield session
        except Exception:
            await _safe_rollback(session)
            raise
