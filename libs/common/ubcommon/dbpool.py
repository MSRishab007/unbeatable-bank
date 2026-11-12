"""A FastAPI dependency that hands out pooled database connections without deadlocking.

The plain way, a sync generator dependency around `pool.connection()`, deadlocks under
load. FastAPI runs a sync dependency, and every sync endpoint, on a worker thread
(40 by default). When more requests arrive than the pool has connections, the waiting
requests take every worker thread, each blocked in `pool.getconn()`. The requests that
do hold a connection then need a worker thread to run their endpoint and give the
connection back, and there is none: nothing moves until the pool's timeout fires and
every waiter fails. (Seen in the GKE load test: identity at 3% CPU, "couldn't get a
connection after 30 sec", the health check starved, the pod restarted in a loop.)

Here a request first waits for one of the pool's slots on the event loop, which costs no
thread, and only then takes a thread to get the connection, which is free by then.
Worker threads are only used by requests that can make progress. A request that waits
longer than `wait_seconds` gets 503 rather than hanging.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

_gates: dict[tuple[int, int], asyncio.Semaphore] = {}


def _gate(pool) -> asyncio.Semaphore:
    key = (id(pool), id(asyncio.get_running_loop()))    # one per pool and event loop (tests make several)
    gate = _gates.get(key)
    if gate is None:
        gate = _gates[key] = asyncio.Semaphore(pool.max_size)
    return gate


def connection_dependency(get_pool: Callable, wait_seconds: float = 10.0):
    """`get_conn = connection_dependency(lambda: _state["pool"])`, used as Depends(get_conn)."""

    async def get_conn():
        pool = get_pool()
        gate = _gate(pool)
        try:
            await asyncio.wait_for(gate.acquire(), wait_seconds)
        except TimeoutError:
            raise HTTPException(status_code=503, detail="busy; try again",
                                headers={"Retry-After": "1"}) from None
        try:
            conn = await run_in_threadpool(pool.getconn)
            try:
                yield conn
            finally:
                await run_in_threadpool(pool.putconn, conn)
        finally:
            gate.release()

    return get_conn
