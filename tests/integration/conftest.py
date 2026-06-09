import os

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from models import Base, EnergyOrder, MicroDepositOrder, ProcessedTx, SaaSOrder, Tenant, User


@pytest.fixture(scope="session")
def test_database_url():
    url = os.getenv("TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run database integration tests.")
    if "test" not in url.lower():
        pytest.skip("TEST_DATABASE_URL must point to a dedicated test database.")
    return url


@pytest_asyncio.fixture(loop_scope="function")
async def test_engine(test_database_url):
    engine = create_async_engine(
        test_database_url,
        pool_pre_ping=True,
        poolclass=NullPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")
async def db_session(test_engine):
    Session = async_sessionmaker(test_engine, expire_on_commit=False)
    async with Session() as session:
        for model in [ProcessedTx, EnergyOrder, MicroDepositOrder, SaaSOrder, User, Tenant]:
            await session.execute(delete(model))
        await session.commit()
        yield session
        await session.rollback()
