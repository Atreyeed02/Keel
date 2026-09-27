"""
Shared setup for the Postgres-backed test fixtures.

Every integration fixture rebuilds the schema with `metadata.drop_all` and
`metadata.create_all`. That is only safe on a scratch database, and one
failure mode is worse than it looks: `alembic_version` is not part of
`metadata`, so the drop leaves it behind. Point the suite at a database
the app has migrated and you get back a database stamped "at head" with no
app tables in it, on which `alembic upgrade head` silently does nothing.

So the schema is only rebuilt on a database alembic has never touched.
"""

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.schema import metadata

MIGRATED_DATABASE_MESSAGE = (
    "TEST_DATABASE_URL points at a database alembic has migrated (it has an "
    "alembic_version table). The integration fixtures drop every app table, "
    "so they refuse to run there. Point TEST_DATABASE_URL at a scratch "
    "database instead."
)


async def reset_schema(conn: AsyncConnection) -> None:
    """Drop and recreate every app table, refusing to on a migrated database."""
    if await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("alembic_version")):
        pytest.fail(MIGRATED_DATABASE_MESSAGE, pytrace=False)
    await conn.run_sync(metadata.drop_all)
    await conn.run_sync(metadata.create_all)
