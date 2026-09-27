"""
The integration fixtures refuse to wipe a database alembic has migrated.

Postgres-backed; skipped without TEST_DATABASE_URL.
"""

import os
import uuid

import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.schema import accounts, metadata
from tests.support import reset_schema

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def test_engine():
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL page integration tests")
    engine = create_async_engine(TEST_DATABASE_URL)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    await engine.dispose()


async def test_reset_schema_refuses_a_migrated_database_and_touches_nothing(test_engine):
    # A database the app has been running on: app tables with data in them,
    # stamped by alembic.
    async with test_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)
        await conn.execute(
            insert(accounts).values(
                id=uuid.uuid4(), name="Real data", account_type="asset", currency="USD"
            )
        )
        await conn.execute(text("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)"))
        await conn.execute(text("INSERT INTO alembic_version VALUES ('7d2e4b9c1a58')"))

    async with test_engine.begin() as conn:
        with pytest.raises(pytest.fail.Exception, match="alembic has migrated"):
            await reset_schema(conn)

    async with test_engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(accounts)) == 1
        assert await conn.scalar(text("SELECT version_num FROM alembic_version")) == "7d2e4b9c1a58"
