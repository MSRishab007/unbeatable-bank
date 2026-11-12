"""More requests than pooled connections must queue, not deadlock (see ubcommon.dbpool).

Needs Postgres: TEST_ADMIN_URL=postgresql://... (skipped without it)."""

import asyncio
import os

import httpx
import pytest
from fastapi import Depends, FastAPI

from ubcommon.dbpool import connection_dependency

URL = os.getenv("TEST_ADMIN_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set TEST_ADMIN_URL to run against Postgres")


def app_with(get_conn) -> FastAPI:
    app = FastAPI()

    @app.get("/work")
    def work(conn=Depends(get_conn)):          # a sync endpoint, like the services' own
        conn.execute("SELECT pg_sleep(0.02)")
        return {"ok": True}

    return app


def plain_dependency(pool):
    def get_conn():
        with pool.connection() as conn:
            yield conn
    return get_conn


async def fire(app, n: int) -> int:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=30) as client:
        results = await asyncio.gather(*[client.get("/work") for _ in range(n)], return_exceptions=True)
    return sum(1 for r in results if not isinstance(r, Exception) and r.status_code == 200)


@pytest.fixture
def pool():
    from psycopg_pool import ConnectionPool

    p = ConnectionPool(URL, min_size=1, max_size=2, timeout=3, kwargs={"autocommit": True}, open=True)
    yield p
    p.close()


def test_many_more_requests_than_connections_all_succeed(pool):
    assert asyncio.run(fire(app_with(connection_dependency(lambda: pool)), 150)) == 150


def test_the_plain_dependency_really_does_deadlock(pool):
    """Why the helper exists: the obvious version loses most requests to pool timeouts."""
    assert asyncio.run(fire(app_with(plain_dependency(pool)), 150)) < 150


def test_a_request_that_waits_too_long_gets_503(pool):
    app = app_with(connection_dependency(lambda: pool, wait_seconds=0.01))
    assert asyncio.run(fire(app, 60)) < 60
