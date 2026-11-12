"""DynaStore node process: gRPC for data, a small HTTP side for health, metrics
and the cluster view used by the operations dashboard.

Environment:
    NODE_ID      this node's id, e.g. dynastore-0 (the StatefulSet pod name)
    PEERS        every node: "dynastore-0=dynastore-0.dynastore:7000,dynastore-1=..."
    DATA_DIR     where to keep the SQLite file (a persistent volume)
    GRPC_PORT    default 7000
    REPLICAS_N / READ_R / WRITE_W   defaults 3 / 2 / 2
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ubcommon.logging_setup import setup_logging
from ubcommon.metrics import instrument

from . import server
from .node import Node, QuorumError

_state: dict = {}


def build_node() -> Node:
    peers = dict(pair.split("=", 1) for pair in os.environ["PEERS"].split(",") if pair)
    node_id = os.environ["NODE_ID"]
    if node_id not in peers:
        raise SystemExit(f"NODE_ID {node_id} is not in PEERS")
    return Node(
        node_id, peers, os.path.join(os.getenv("DATA_DIR", "/data"), "dynastore.db"),
        n=int(os.getenv("REPLICAS_N", "3")), r=int(os.getenv("READ_R", "2")), w=int(os.getenv("WRITE_W", "2")),
        sync_interval=float(os.getenv("SYNC_INTERVAL", "15")),
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    node = _state.get("node") or build_node()
    _state["node"] = node
    grpc_server = server.start(node, int(os.getenv("GRPC_PORT", "7000")))
    node.start_background()
    yield
    grpc_server.stop(grace=2).wait()
    node.stop()


setup_logging("dynastore")
app = FastAPI(title="DynaStore node", version="0.1.0", lifespan=lifespan)
instrument(app, "dynastore")


class FaultsIn(BaseModel):
    blocked_peers: list[str] = []
    down: bool = False


@app.get("/healthz")
async def healthz():
    # async: answered on the event loop, so a busy thread pool (every worker waiting
    # for a database connection under load) can't make the liveness probe time out.
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    return {"status": "ready", "node": _state["node"].id}


@app.get("/v1/status")
def status():
    return _state["node"].status()


@app.get("/v1/keys/{key:path}")
def key_info(key: str):
    """Where a key lives and whether its replicas agree."""
    node = _state["node"]
    info = node.key_info(key)
    try:
        env, replicas, _ = node.get(key, 1)
    except QuorumError as e:
        raise HTTPException(503, str(e))
    from . import crdt
    info["merged"] = {"found": env is not None, "type": {"c": "counter", "r": "register"}.get((env or {}).get("t")),
                      "counter": crdt.counter_value(env),
                      "value": (crdt.winner(env) or b"").decode(errors="replace")}
    return info


@app.post("/v1/admin/faults")
def set_faults(body: FaultsIn):
    """Demo control: simulate a crashed node or a network partition."""
    _state["node"].set_faults(body.blocked_peers, body.down)
    return _state["node"].status()
