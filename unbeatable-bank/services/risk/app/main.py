"""Risk Detection: gRPC on 50051 for Transaction Intake, HTTP for health, metrics and inspection.

Environment:
    DYNASTORE_NODES   comma-separated DynaStore node addresses
    RULES_FILE        path to rules.yaml (default: config/rules.yaml)
    GRPC_PORT         default 50051
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ubcommon.dynaclient import DynaClient
from ubcommon.logging_setup import setup_logging
from ubcommon.metrics import instrument

from . import grpc_server
from .engine import Engine, Rules

_state: dict = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    rules = Rules(os.getenv("RULES_FILE", "config/rules.yaml"))
    _state["engine"] = _state.get("engine") or Engine(DynaClient(timeout=0.5), rules)
    server = grpc_server.start(_state["engine"], int(os.getenv("GRPC_PORT", "50051")))
    yield
    server.stop(grace=2).wait()


setup_logging("risk")
app = FastAPI(title="Risk Detection", version="0.1.0", lifespan=lifespan)
instrument(app, "risk")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    return {"status": "ready"}


@app.get("/v1/rules")
def rules():
    """The rule set currently in force (after any hot reload)."""
    return _state["engine"].rules.current()
