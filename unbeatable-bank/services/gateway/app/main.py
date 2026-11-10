"""API Gateway: the bank's only public entry point.

  - verifies the access token (JWT) issued by Identity & Auth
  - checks the caller's role, and that customers only touch their own accounts
  - forwards the request to the right internal service, telling it who is calling
    in the X-Actor header (any X-Actor sent from outside is discarded)
  - serves the web app

Internal services are not exposed outside the cluster, so they trust X-Actor.

Environment:
    JWT_SECRET                         shared with Identity & Auth
    IDENTITY_URL, ACCOUNT_URL, LEDGER_URL, INTAKE_URL, DEPOSITS_URL, FRAUD_URL,
    DYNASTORE_URL, RATELIMIT_URL, RISK_URL
    ALLOW_SIM_HEADERS=true             accept X-Sim-Ip / X-Geo from clients (fraud simulator only)
"""

from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ubcommon.auth import AuthError, verify_token
from ubcommon.logging_setup import setup_logging
from ubcommon.metrics import instrument

from .routes import ROUTES, Route

SERVICES = ("identity", "account", "ledger", "intake", "deposits", "fraud", "dynastore", "ratelimit", "risk")
_HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding", "host"}
_state: dict = {}
_COMPILED = [(route, route.regex()) for route in ROUTES]


def upstreams() -> dict[str, str]:
    return {name: os.getenv(f"{name.upper()}_URL", f"http://{name}:8080") for name in SERVICES}


@asynccontextmanager
async def lifespan(_: FastAPI):
    _state.setdefault("http", httpx.AsyncClient(timeout=15))
    _state.setdefault("upstreams", upstreams())
    _state.setdefault("jwt_secret", os.environ.get("JWT_SECRET", ""))
    _state["holder_cache"] = {}
    yield
    await _state["http"].aclose()


setup_logging("gateway")
app = FastAPI(title="Unbeatable Bank API Gateway", version="0.1.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")
instrument(app, "gateway")


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message})


def client_ip(request: Request) -> str:
    if os.getenv("ALLOW_SIM_HEADERS") == "true" and request.headers.get("x-sim-ip"):
        return request.headers["x-sim-ip"]
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        # Google's load balancer appends "<client>, <load balancer>": the client is second from the end.
        parts = [p.strip() for p in forwarded.split(",")]
        return parts[-2] if len(parts) >= 2 else parts[0]
    return request.client.host if request.client else ""


def authenticate(request: Request) -> dict | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    try:
        return verify_token(header[7:], _state["jwt_secret"])
    except AuthError:
        return None


async def is_holder(account_id: str, customer_id: str) -> bool:
    try:
        UUID(account_id)
    except ValueError:
        return False
    cache: dict = _state["holder_cache"]
    key = (account_id, customer_id)
    hit = cache.get(key)
    if hit and hit[1] > time.time():
        return hit[0]
    r = await _state["http"].get(
        f"{_state['upstreams']['account']}/internal/v1/accounts/{account_id}/authorize/{customer_id}")
    result = r.status_code == 200 and bool(r.json().get("is_holder"))
    if len(cache) > 20_000:
        cache.clear()
    cache[key] = (result, time.time() + 30)
    return result


def actor_headers(request: Request, claims: dict | None) -> dict[str, str]:
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _HOP_HEADERS and not k.lower().startswith("x-")
               and k.lower() != "authorization"}
    for name in ("idempotency-key",):
        if name in request.headers:
            headers[name] = request.headers[name]
    headers["x-client-ip"] = client_ip(request)
    if os.getenv("ALLOW_SIM_HEADERS") == "true" and request.headers.get("x-geo"):
        headers["x-geo"] = request.headers["x-geo"]
    if claims is None:
        return headers
    if claims["role"] == "customer":
        headers["x-actor"] = f"customer:{claims['sub']}"
        headers["x-device-id"] = claims.get("did", "")
        headers["x-session-id"] = claims.get("sid", "")
    else:
        headers["x-actor"] = f"staff:{claims['sub']}:{claims.get('staff_role', '')}"
    return headers


async def forward(request: Request, service: str, path: str, claims: dict | None,
                  body: bytes | None = None) -> Response:
    url = _state["upstreams"][service] + path
    try:
        r = await _state["http"].request(
            request.method, url, params=dict(request.query_params),
            content=body if body is not None else await request.body(),
            headers=actor_headers(request, claims))
    except httpx.HTTPError:
        return error(503, "UPSTREAM_UNAVAILABLE", f"the {service} service is not reachable right now")
    headers = {k: v for k, v in r.headers.items() if k.lower() in ("content-type", "retry-after")}
    return Response(content=r.content, status_code=r.status_code, headers=headers)


def authorize(route: Route, claims: dict | None) -> JSONResponse | None:
    if route.auth == "public":
        return None
    if claims is None:
        return error(401, "UNAUTHENTICATED", "log in to continue")
    if route.auth == "customer" and claims["role"] != "customer":
        return error(403, "FORBIDDEN", "this is for customers")
    if route.auth == "staff":
        if claims["role"] != "staff":
            return error(403, "FORBIDDEN", "this is for bank staff")
        role = claims.get("staff_role", "")
        if role != "ADMIN" and route.roles and role not in route.roles:
            return error(403, "FORBIDDEN", f"needs one of the roles: {', '.join(route.roles)}")
    return None


# ----------------------------------------------------------------- composed endpoints


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    return {"status": "ready"}


@app.get("/api/me/accounts")
async def my_accounts(request: Request):
    """Accounts with their balances: one call for the dashboard (Account + Ledger)."""
    request.state.metrics_path = "/api/me/accounts"
    claims = authenticate(request)
    if claims is None or claims["role"] != "customer":
        return error(401, "UNAUTHENTICATED", "log in to continue")
    http, up = _state["http"], _state["upstreams"]
    try:
        accounts = (await http.get(f"{up['account']}/v1/customers/{claims['sub']}/accounts")).json()
        balances = {}
        if accounts:
            ids = ",".join(a["account_id"] for a in accounts)
            r = await http.get(f"{up['ledger']}/v1/balances", params={"ids": ids})
            balances = {b["account_id"]: b for b in r.json()} if r.status_code == 200 else {}
    except httpx.HTTPError:
        return error(503, "UPSTREAM_UNAVAILABLE", "accounts are not available right now")
    for a in accounts:
        b = balances.get(a["account_id"], {})
        a["ledger_balance"] = b.get("ledger_balance")
        a["available_balance"] = b.get("available_balance")
    return accounts


@app.post("/api/accounts")
async def open_account(request: Request):
    """Open a savings or current account. The caller is always the primary holder;
    `joint_holder_ids` adds others."""
    request.state.metrics_path = "/api/accounts"
    claims = authenticate(request)
    if claims is None or claims["role"] != "customer":
        return error(401, "UNAUTHENTICATED", "log in to continue")
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        return error(400, "INVALID_REQUEST", "body must be JSON")
    joint = [h for h in body.pop("joint_holder_ids", []) if h != claims["sub"]]
    body["holder_ids"] = [claims["sub"], *joint]
    return await forward(request, "account", "/v1/accounts", claims, json.dumps(body).encode())


# ----------------------------------------------------------------- everything else: the route table


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(request: Request, rest: str):
    path = "/api/" + rest
    for route, regex in _COMPILED:
        if route.method != request.method:
            continue
        match = regex.match(path)
        if not match:
            continue
        request.state.metrics_path = route.path
        claims = authenticate(request)
        denied = authorize(route, claims)
        if denied:
            return denied
        params = match.groupdict()
        if route.owner == "account" and not await is_holder(params["account_id"], claims["sub"]):
            return error(404, "NOT_FOUND", "account not found")   # same answer as a non-existent account
        upstream = route.upstream.format(sub=claims["sub"] if claims else "", **params)
        return await forward(request, route.service, upstream, claims)
    return error(404, "NOT_FOUND", "no such API endpoint")


_static = Path(os.getenv("STATIC_DIR", Path(__file__).resolve().parents[1] / "static"))
if _static.is_dir():
    app.mount("/", StaticFiles(directory=_static, html=True), name="web")
