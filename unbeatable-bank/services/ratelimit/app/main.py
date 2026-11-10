"""Rate Limit Coordinator: gRPC on 50051 for callers, HTTP for health, metrics and inspection.

Environment:
    DYNASTORE_NODES   comma-separated DynaStore node addresses
    POLICIES_FILE     path to policies.yaml (default: config/policies.yaml)
    GRPC_PORT         default 50051
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from ubcommon.dynaclient import DynaClient
from ubcommon.logging_setup import setup_logging
from ubcommon.metrics import instrument

from . import grpc_server
from .limiter import Limiter, Policies

_state: dict = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    policies = Policies.load(os.getenv("POLICIES_FILE", "config/policies.yaml"))
    _state["limiter"] = _state.get("limiter") or Limiter(DynaClient(timeout=0.5), policies)
    server = grpc_server.start(_state["limiter"], int(os.getenv("GRPC_PORT", "50051")))
    yield
    server.stop(grace=2).wait()


setup_logging("ratelimit")
app = FastAPI(title="Rate Limit Coordinator", version="0.1.0", lifespan=lifespan)
instrument(app, "ratelimit")


class DescriptorIn(BaseModel):
    scope: str
    id: str


class CheckIn(BaseModel):
    policy_set: str = "payments"
    descriptors: list[DescriptorIn]
    cost: int = 1
    amount: int = 0


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    return {"status": "ready"}


@app.get("/v1/policies")
def policies():
    return _state["limiter"].policies.describe()


@app.post("/v1/check")
def check(body: CheckIn):
    """REST mirror of the gRPC Check call, for debugging and demos."""
    return vars(_state["limiter"].check(body.policy_set, [(d.scope, d.id) for d in body.descriptors],
                                        body.cost, body.amount))
