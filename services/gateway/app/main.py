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
    MAX_BODY_BYTES                     largest request body accepted (default 6 MB: a KYC document)
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
# Response headers passed back from internal services. Files (KYC documents, statements) carry
# their own safety headers; everything else an upstream sends is dropped.
_PASS_BACK = {"content-type", "retry-after", "content-disposition", "x-content-type-options",
              "content-security-policy", "cache-control"}
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", str(6 * 1024 * 1024)))
# The web app is static files from this origin plus Google Fonts; nothing else may load or frame it.
_WEB_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; media-src 'self' blob:; "
            "frame-src blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'")
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


class BodyTooLarge(Exception):
    pass


@app.exception_handler(BodyTooLarge)
def body_too_large(_: Request, __: BodyTooLarge):
    return error(413, "BODY_TOO_LARGE", f"the request is larger than {MAX_BODY_BYTES // (1024 * 1024)} MB")


async def read_body(request: Request) -> bytes:
    """The request body, refusing anything over MAX_BODY_BYTES without buffering it all first."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise BodyTooLarge()
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise BodyTooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if not request.url.path.startswith("/api/"):
        response.headers.setdefault("Content-Security-Policy", _WEB_CSP)
        response.headers.setdefault("X-Frame-Options", "DENY")
    return response


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
            content=body if body is not None else await read_body(request),
            headers=actor_headers(request, claims))
    except httpx.HTTPError:
        return error(503, "UPSTREAM_UNAVAILABLE", f"the {service} service is not reachable right now")
    headers = {k: v for k, v in r.headers.items() if k.lower() in _PASS_BACK}
    return Response(content=r.content, status_code=r.status_code, headers=headers)


def over_limit(route: Route, ip: str) -> int:
    """Seconds to wait if this address has used up the route's allowance, else 0.

    Counted in this pod's memory, so with several gateway pods the real ceiling is a few
    times higher. It is a brake on abuse of endpoints that need no login, not an exact quota
    (payments and logins are limited precisely by the Rate Limit Coordinator)."""
    count, window = route.limit
    hits: dict = _state.setdefault("route_hits", {})
    now = time.monotonic()
    recent = [t for t in hits.get((route.path, ip), []) if now - t < window]
    if len(recent) >= count:
        hits[(route.path, ip)] = recent
        return max(1, int(window - (now - recent[0])) + 1)
    recent.append(now)
    hits[(route.path, ip)] = recent
    if len(hits) > 50_000:                          # forget everything rather than grow without bound
        hits.clear()
    return 0


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
        body = json.loads(await read_body(request) or b"{}")
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
        if route.limit:
            wait = over_limit(route, client_ip(request))
            if wait:
                response = error(429, "TOO_MANY_REQUESTS", f"too many requests; try again in {wait} seconds")
                response.headers["Retry-After"] = str(wait)
                return response
        params = match.groupdict()
        if route.owner == "account" and not await is_holder(params["account_id"], claims["sub"]):
            return error(404, "NOT_FOUND", "account not found")   # same answer as a non-existent account
        upstream = route.upstream.format(sub=claims["sub"] if claims else "", **params)
        return await forward(request, route.service, upstream, claims)
    return error(404, "NOT_FOUND", "no such API endpoint")


_static = Path(os.getenv("STATIC_DIR", Path(__file__).resolve().parents[1] / "static"))
if _static.is_dir():
    app.mount("/", StaticFiles(directory=_static, html=True), name="web")
