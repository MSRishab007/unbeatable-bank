"""API Gateway: the bank's only public entry point.

  - verifies the access token (JWT) issued by Identity & Auth
  - checks the token's session is still open and not locked (asks Identity; an ACTIVE
    answer is remembered for SESSION_CACHE_SECONDS, so ending a session takes effect
    within that time on every gateway pod, and at once on the pod that ended it)
  - checks the caller's role, and that customers only touch their own accounts
  - checks the payment or app PIN (X-Pin header) for routes that need one
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
    SESSION_CACHE_SECONDS              how long an ACTIVE session answer is reused (default 5)

Requests the app makes on its own (checking the inbox) send `X-Background: 1`: they
neither keep an idle session awake nor get refused because it locked.
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

from .routes import ROUTES, WHILE_LOCKED, Route

SERVICES = ("identity", "account", "ledger", "intake", "deposits", "fraud", "dynastore", "ratelimit", "risk")
# Response headers passed back from internal services. Files (KYC documents, statements) carry
# their own safety headers; everything else an upstream sends is dropped.
_PASS_BACK = {"content-type", "retry-after", "content-disposition", "x-content-type-options",
              "content-security-policy", "cache-control"}
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", str(6 * 1024 * 1024)))
SESSION_CACHE_SECONDS = float(os.getenv("SESSION_CACHE_SECONDS", "5"))
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
    _state["session_cache"] = {}
    yield
    await _state["http"].aclose()


setup_logging("gateway")
app = FastAPI(title="Unbeatable Bank API Gateway", version="0.1.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")
instrument(app, "gateway")


def error(status: int, code: str, message: str, **details) -> JSONResponse:
    content = {"error": code, "message": message}
    if details:
        content["details"] = details
    return JSONResponse(status_code=status, content=content)


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
        # The edge proxy (Traefik) appends the address it saw the request come from. Anything
        # before that was written by the caller and can't be trusted, so only the last entry counts.
        return forwarded.split(",")[-1].strip()
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
    else:
        headers["x-actor"] = f"staff:{claims['sub']}:{claims.get('staff_role', '')}"
    headers["x-session-id"] = claims.get("sid", "")
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


async def check_session(request: Request, claims: dict, path: str) -> JSONResponse | None:
    """Is the token's session still open, and unlocked? None if the request may go on."""
    sid = claims.get("sid")
    if not sid:                         # tokens from before sessions were checked
        return error(401, "SESSION_ENDED", "sign in again")
    background = request.headers.get("x-background") == "1"
    cache: dict = _state.setdefault("session_cache", {})
    now = time.monotonic()
    if cache.get(sid, 0) > now:
        return None
    try:
        r = await _state["http"].post(f"{_state['upstreams']['identity']}/internal/v1/sessions/{sid}/check",
                                      params={"activity": "false" if background else "true"})
        state = r.json()["state"] if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError, KeyError):
        state = None
    if state is None:                   # can't tell: refuse rather than let an ended session through
        return error(503, "UPSTREAM_UNAVAILABLE", "can't check your session right now; try again")
    if state == "ACTIVE":
        if not background:
            if len(cache) > 50_000:
                cache.clear()
            cache[sid] = now + SESSION_CACHE_SECONDS
        return None
    if state == "LOCKED":
        if path in WHILE_LOCKED:
            return None
        return error(423, "SESSION_LOCKED", "the app is locked; unlock it with your app PIN")
    return error(401, "SESSION_ENDED", "your session has ended; sign in again")


def forget_session(claims: dict | None) -> None:
    if claims and claims.get("sid"):
        _state.setdefault("session_cache", {}).pop(claims["sid"], None)


async def check_pin(request: Request, kind: str, claims: dict) -> JSONResponse | None:
    """The X-Pin header against Identity. None if it is right."""
    names = {"PAYMENT": "payment PIN", "APP": "app PIN"}
    pin = request.headers.get("x-pin", "")
    try:
        r = await _state["http"].post(f"{_state['upstreams']['identity']}/internal/v1/pins/verify",
                                      json={"customer_id": claims["sub"], "kind": kind, "pin": pin})
        outcome = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        outcome = None
    if outcome is None:
        return error(503, "UPSTREAM_UNAVAILABLE", "can't check your PIN right now; nothing was done")
    result = outcome.get("result")
    if result == "OK":
        return None
    if result == "REQUIRED":
        return error(428, "PIN_REQUIRED", f"enter your {names[kind]}", kind=kind)
    if result == "NOT_SET":
        return error(428, "PIN_NOT_SET", f"set your {names[kind]} first (Profile > Security)", kind=kind)
    if result == "LOCKED":
        return error(423, "PIN_LOCKED", f"your {names[kind]} is locked for 24 hours after 3 wrong tries; "
                     "reset it from Profile > Security", kind=kind, locked_until=outcome.get("locked_until"))
    left = outcome.get("attempts_left")
    return error(403, "PIN_WRONG", f"wrong {names[kind]}; {left} {'try' if left == 1 else 'tries'} left",
                 kind=kind, attempts_left=left)


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
async def healthz():
    # async: answered on the event loop, so a busy thread pool (every worker waiting
    # for a database connection under load) can't make the liveness probe time out.
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
    denied = await check_session(request, claims, "/api/me/accounts")
    if denied:
        return denied
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
    denied = await check_session(request, claims, "/api/accounts")
    if denied:
        return denied
    try:
        body = json.loads(await read_body(request) or b"{}")
    except ValueError:
        return error(400, "INVALID_REQUEST", "body must be JSON")
    joint = [h for h in body.pop("joint_holder_ids", []) if h != claims["sub"]]
    body["holder_ids"] = [claims["sub"], *joint]
    return await forward(request, "account", "/v1/accounts", claims, json.dumps(body).encode())


@app.post("/api/me/lockdown")
async def lockdown(request: Request):
    """'Block everything': freeze every account, end every session, open a fraud case.

    For a customer who thinks someone else is in their account. Works while the app is
    locked and needs no PIN: the person pressing it may have just had the PIN stolen.
    Money first (freeze), then sessions, then the case; each step is reported."""
    request.state.metrics_path = "/api/me/lockdown"
    claims = authenticate(request)
    if claims is None or claims["role"] != "customer":
        return error(401, "UNAUTHENTICATED", "log in to continue")
    denied = await check_session(request, claims, "/api/me/lockdown")
    if denied:
        return denied
    http, up, sub = _state["http"], _state["upstreams"], claims["sub"]
    actor = {"x-actor": f"customer:{sub}", "x-client-ip": client_ip(request)}
    result = {"accounts_frozen": [], "accounts_failed": [], "sessions_ended": None, "case_number": None}
    try:
        accounts = (await http.get(f"{up['account']}/v1/customers/{sub}/accounts")).json()
    except (httpx.HTTPError, ValueError):
        return error(503, "UPSTREAM_UNAVAILABLE", "couldn't reach your accounts; call the bank now")
    for a in accounts:
        if a.get("status") in ("CLOSED", "FROZEN"):
            continue
        try:
            r = await http.post(f"{up['account']}/v1/accounts/{a['account_id']}/freeze", headers=actor,
                                json={"reason": "Customer pressed Block everything"})
            ok = r.status_code == 200
        except httpx.HTTPError:
            ok = False
        result["accounts_frozen" if ok else "accounts_failed"].append(a.get("account_number") or a["account_id"])
    try:
        r = await http.post(f"{up['fraud']}/v1/lockdown", headers=actor,
                            json={"account_ids": [a["account_id"] for a in accounts]})
        result["case_number"] = r.json().get("case_number") if r.status_code == 201 else None
    except (httpx.HTTPError, ValueError):
        pass
    try:
        r = await http.post(f"{up['identity']}/internal/v1/customers/{sub}/lockdown", headers=actor)
        result["sessions_ended"] = r.json().get("sessions_ended") if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        pass
    _state.setdefault("session_cache", {}).clear()     # this pod forgets every session it vouched for
    status = 200 if not result["accounts_failed"] and result["sessions_ended"] is not None else 207
    return JSONResponse(status_code=status, content=result)


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
        if claims is not None and route.auth != "public":
            denied = await check_session(request, claims, route.path)
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
        if route.pin:
            denied = await check_pin(request, route.pin, claims)
            if denied:
                return denied
        upstream = route.upstream.format(sub=claims["sub"] if claims else "", **params)
        if route.path in ("/api/auth/logout", "/api/auth/lock", "/api/staff/logout"):
            forget_session(claims)
        return await forward(request, route.service, upstream, claims)
    return error(404, "NOT_FOUND", "no such API endpoint")


_static = Path(os.getenv("STATIC_DIR", Path(__file__).resolve().parents[1] / "static"))
if _static.is_dir():
    app.mount("/", StaticFiles(directory=_static, html=True), name="web")
