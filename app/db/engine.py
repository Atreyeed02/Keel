from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.config import settings

# We use SQLAlchemy Core (not the ORM) — explicit Table/select/insert
# statements over the ledger tables, no session/unit-of-work magic.
# This keeps the double-entry invariants enforced in code we control,
# not hidden behind ORM flush semantics.
#
# The URL is DATABASE_URL respelled for asyncpg (app/config.py), and TLS goes
# in as asyncpg's own `ssl` argument, because asyncpg refuses `sslmode` in
# the URL.
engine: AsyncEngine = create_async_engine(
    settings.database.async_url,
    connect_args={"ssl": settings.database.ssl} if settings.database.ssl else {},
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    echo=False,
)


async def get_connection() -> AsyncGenerator[AsyncConnection, None]:
    """FastAPI dependency — yields a connection, always closed after the request."""
    async with engine.connect() as conn:
        yield conn


async def ping() -> bool:
    """Used by the health endpoint. Returns False instead of raising."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
