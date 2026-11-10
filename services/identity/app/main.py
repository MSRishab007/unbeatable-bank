"""Identity & Auth REST API.

/v1/auth/...       customers (through the API Gateway)
/v1/staff/login    bank staff
/internal/v1/...   other services (OTP for step-up confirmation)

Environment:
    DATABASE_URL      identity database
    ACCOUNT_URL       Account service
    JWT_SECRET        shared with the API Gateway
    DOCSTORE          firestore | postgres | memory     (sessions, login events)
    PUBSUB            gcp | local                        (notifications)
    DYNASTORE_NODES   optional: fraud signals for the risk engine
    RATELIMIT_GRPC    optional: login throttling
    STAFF_BOOTSTRAP   optional "id:ROLE:password,..." created at startup
    AUTO_VERIFY_KYC   "true" skips manual KYC approval (demos and load tests)
"""

import hashlib
import os
from contextlib import asynccontextmanager
from datetime import date
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, EmailStr, Field

from ubcommon import grpcutil
from ubcommon.auth import parse_actor
from ubcommon.docstore import get_docstore
from ubcommon.logging_setup import setup_logging
from ubcommon.metrics import instrument
from ubcommon.notify import get_notifier

from . import service

_state: dict = {}


class HttpAccounts:
    def __init__(self, base_url: str) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=5)

    def create_customer(self, profile: dict) -> dict:
        try:
            r = self._http.post("/v1/customers", json=profile)
        except httpx.HTTPError as e:
            raise service.Unavailable("account service unavailable") from e
        if r.status_code == 409:
            body = r.json()
            raise service.Conflict(body.get("message", "customer already exists"), **body.get("details", {}))
        if r.status_code >= 500:
            raise service.Unavailable("account service unavailable")
        if r.status_code >= 400:
            body = r.json()
            detail = body.get("detail")
            message = body.get("message") or (detail[0].get("msg") if isinstance(detail, list) else str(detail))
            raise service.IdentityError(message or "invalid customer details")
        return r.json()

    def verify_kyc(self, customer_id: UUID) -> None:
        self._http.post(f"/internal/v1/customers/{customer_id}/kyc/verify",
                        headers={"X-Actor": "system:identity"}).raise_for_status()


def _login_limiter():
    target = os.getenv("RATELIMIT_GRPC")
    if not target:
        return lambda identifier, ip: True
    import grpc
    from ubproto.ratelimit.v1 import ratelimit_pb2 as pb
    from ubproto.ratelimit.v1 import ratelimit_pb2_grpc as pb_grpc

    stub = pb_grpc.RateLimitServiceStub(grpcutil.channel(target))

    def allowed(identifier: str, ip: str) -> bool:
        descriptors = [pb.Descriptor(scope="identity", id=hashlib.sha256(identifier.encode()).hexdigest()[:20])]
        if ip:
            descriptors.append(pb.Descriptor(scope="ip", id=ip))
        try:
            return stub.Check(pb.CheckRequest(policy_set="login", descriptors=descriptors), timeout=1).allowed
        except grpc.RpcError:
            return True   # logins fail open; the password lockout still applies

    return allowed


def build_deps() -> service.Deps:
    docstore = get_docstore()
    signals = None
    if os.getenv("DYNASTORE_NODES"):
        from ubcommon.dynaclient import DynaClient
        signals = DynaClient(timeout=0.5)
    return service.Deps(
        accounts=HttpAccounts(os.environ["ACCOUNT_URL"]),
        docstore=docstore,
        notifier=get_notifier(docstore),
        jwt_secret=os.environ["JWT_SECRET"],
        signals=signals,
        login_allowed=_login_limiter(),
        auto_verify_kyc=os.getenv("AUTO_VERIFY_KYC", "false").lower() == "true",
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    _state["pool"] = ConnectionPool(os.environ["DATABASE_URL"], min_size=1,
                                    max_size=int(os.getenv("DB_POOL_MAX", "5")),
                                    kwargs={"autocommit": True}, open=True)
    _state.setdefault("deps", build_deps())
    with _state["pool"].connection() as conn:
        for entry in filter(None, os.getenv("STAFF_BOOTSTRAP", "").split(",")):
            staff_id, role, password = entry.split(":", 2)
            service.upsert_staff(conn, staff_id.strip().lower(), role.strip().upper(), password)
    yield
    _state["pool"].close()


setup_logging("identity")
app = FastAPI(title="Identity & Auth", version="0.1.0", lifespan=lifespan)
instrument(app, "identity")


def get_conn():
    with _state["pool"].connection() as conn:
        yield conn


def get_deps() -> service.Deps:
    return _state["deps"]


def get_customer(x_actor: str = Header(..., alias="X-Actor")) -> UUID:
    actor = parse_actor(x_actor)
    if actor.customer_id is None:
        raise HTTPException(status_code=403, detail="customers only")
    return actor.customer_id


@app.exception_handler(service.IdentityError)
def identity_error(_: Request, exc: service.IdentityError):
    return JSONResponse(status_code=exc.http_status,
                        content={"error": exc.code, "message": str(exc), "details": exc.details})


class RegisterIn(BaseModel):
    full_name: str = Field(..., min_length=2)
    date_of_birth: date
    phone: str = Field(..., examples=["+919876543210"])
    email: EmailStr
    password: str = Field(..., min_length=8)
    pan: str | None = None


class LoginIn(BaseModel):
    identifier: str = Field(..., description="email or phone")
    password: str
    device_fingerprint: str = Field(..., min_length=8, description="stable id generated by the app on this device")
    device_label: str | None = Field(None, examples=["Chrome on Windows"])


class RefreshIn(BaseModel):
    refresh_token: str


class StaffLoginIn(BaseModel):
    staff_id: str
    password: str


class OtpIssueIn(BaseModel):
    customer_id: UUID
    purpose: str
    context: str = ""


class OtpVerifyIn(BaseModel):
    customer_id: UUID
    purpose: str
    code: str


def _geo(header: str) -> tuple[float, float] | None:
    try:
        lat, lon = (float(x) for x in header.split(","))
        return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None
    except ValueError:
        return None


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz(conn=Depends(get_conn)):
    conn.execute("SELECT 1")
    return {"status": "ready"}


@app.post("/v1/auth/register", status_code=201)
def register(body: RegisterIn, conn=Depends(get_conn), deps=Depends(get_deps)):
    return service.register(conn, deps, **body.model_dump())


@app.post("/v1/auth/login")
def login(body: LoginIn, conn=Depends(get_conn), deps=Depends(get_deps),
          client_ip: str = Header("", alias="X-Client-Ip"), user_agent: str = Header("", alias="User-Agent"),
          geo: str = Header("", alias="X-Geo")):
    """X-Geo ("lat,lon") stands in for IP geolocation, which a production system would look up itself."""
    return service.login(conn, deps, identifier=body.identifier, password=body.password,
                         fingerprint=body.device_fingerprint, device_label=body.device_label,
                         ip=client_ip, user_agent=user_agent, geo=_geo(geo))


@app.post("/v1/auth/refresh")
def refresh(body: RefreshIn, deps=Depends(get_deps)):
    return service.refresh(deps, body.refresh_token)


@app.post("/v1/auth/logout", status_code=204)
def logout(customer_id: UUID = Depends(get_customer), session_id: str = Header(..., alias="X-Session-Id"),
           deps=Depends(get_deps)):
    service.logout(deps, customer_id, session_id)


@app.get("/v1/auth/devices")
def devices(customer_id: UUID = Depends(get_customer), conn=Depends(get_conn)):
    return service.list_devices(conn, customer_id)


@app.delete("/v1/auth/devices/{device_id}")
def revoke_device(device_id: UUID, customer_id: UUID = Depends(get_customer), conn=Depends(get_conn),
                  deps=Depends(get_deps)):
    return service.revoke_device(conn, deps, customer_id, device_id)


@app.get("/v1/auth/sessions")
def sessions(customer_id: UUID = Depends(get_customer), deps=Depends(get_deps)):
    return service.list_sessions(deps, customer_id)


@app.get("/v1/auth/login-history")
def login_history(customer_id: UUID = Depends(get_customer), deps=Depends(get_deps)):
    return service.login_history(deps, customer_id)


@app.post("/v1/staff/login")
def staff_login(body: StaffLoginIn, conn=Depends(get_conn), deps=Depends(get_deps)):
    return service.staff_login(conn, deps, body.staff_id, body.password)


@app.get("/internal/v1/sessions/{session_id}/active")
def session_active(session_id: str, deps=Depends(get_deps)):
    """Lets the gateway reject access tokens whose session was ended (e.g. device logged out)."""
    return {"active": service.session_is_active(deps, session_id)}


@app.post("/internal/v1/otp/issue")
def otp_issue(body: OtpIssueIn, conn=Depends(get_conn), deps=Depends(get_deps)):
    return service.issue_otp(conn, deps, body.customer_id, body.purpose, body.context)


@app.post("/internal/v1/otp/verify")
def otp_verify(body: OtpVerifyIn, conn=Depends(get_conn)):
    return {"result": service.verify_otp(conn, body.customer_id, body.purpose, body.code)}
