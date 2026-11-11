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
    IDLE_LOCK_SECONDS  customer session locks after this long idle (default 300)
    STAFF_IDLE_SECONDS staff session ends after this long idle (default 900)
    PIN_PEPPER        optional secret mixed into PIN hashes (default: derived from JWT_SECRET)
"""

import hashlib
import os
from contextlib import asynccontextmanager
from datetime import date
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, EmailStr, Field

from ubcommon import grpcutil
from ubcommon.auth import parse_actor
from ubcommon.docstore import get_docstore
from ubcommon.logging_setup import setup_logging
from ubcommon.metrics import instrument
from ubcommon.notify import get_notifier
from ubcommon.objstore import get_objstore

from . import kyc, pins, recovery, service

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

    def verify_kyc(self, customer_id: UUID, actor: str = "system:identity") -> None:
        try:
            self._http.post(f"/internal/v1/customers/{customer_id}/kyc/verify",
                            headers={"X-Actor": actor}).raise_for_status()
        except httpx.HTTPError as e:
            raise service.Unavailable("account service unavailable; try again") from e

    def get_customer(self, customer_id: UUID) -> dict:
        try:
            r = self._http.get(f"/v1/customers/{customer_id}")
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            raise service.Unavailable("account service unavailable; try again") from e

    def update_contact(self, customer_id: UUID, field: str, value: str, actor: str) -> None:
        try:
            r = self._http.patch(f"/internal/v1/customers/{customer_id}/contact", json={field: value},
                                 headers={"X-Actor": actor})
        except httpx.HTTPError as e:
            raise service.Unavailable("account service unavailable; try again") from e
        if r.status_code == 409:
            raise service.Conflict(f"another customer already uses that {field}", field=field)
        if r.status_code >= 400:
            raise service.Unavailable("could not update your details; try again")


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
        objstore=get_objstore(),
        idle_lock_seconds=int(os.getenv("IDLE_LOCK_SECONDS", "300")),
        staff_idle_seconds=int(os.getenv("STAFF_IDLE_SECONDS", "900")),
        pin_pepper=os.getenv("PIN_PEPPER", "").encode(),
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    _state["pool"] = ConnectionPool(os.environ["DATABASE_URL"], min_size=1,
                                    max_size=int(os.getenv("DB_POOL_MAX", "5")),
                                    kwargs={"autocommit": True}, open=True)
    if "deps" not in _state:            # tests put their own in first
        _state["deps"] = build_deps()
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


class LoginVerifyIn(BaseModel):
    challenge_id: str
    code: str = Field(..., min_length=6, max_length=6)


class UnlockIn(BaseModel):
    pin: str | None = None
    password: str | None = None


class PinIn(BaseModel):
    pin: str
    password: str | None = None
    current_pin: str | None = None


class PinResetIn(BaseModel):
    code: str
    password: str
    new_pin: str


class PinVerifyIn(BaseModel):
    customer_id: UUID
    kind: str
    pin: str


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


class ForgotIn(BaseModel):
    identifier: str


class ResetPasswordIn(BaseModel):
    identifier: str
    code: str
    date_of_birth: date
    new_password: str = Field(..., min_length=8)


class ContactIn(BaseModel):
    field: str = Field(..., pattern="^(email|phone)$")
    value: str
    password: str


class CodeIn(BaseModel):
    code: str


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
def login(body: LoginIn, response: Response, conn=Depends(get_conn), deps=Depends(get_deps),
          client_ip: str = Header("", alias="X-Client-Ip"), user_agent: str = Header("", alias="User-Agent"),
          geo: str = Header("", alias="X-Geo")):
    """X-Geo ("lat,lon") stands in for IP geolocation, which a production system would look up itself.

    200 with tokens, or 202 with a challenge_id when the device is new: a code was sent to the
    customer, and /v1/auth/login/verify with that code finishes the login."""
    out = service.login(conn, deps, identifier=body.identifier, password=body.password,
                        fingerprint=body.device_fingerprint, device_label=body.device_label,
                        ip=client_ip, user_agent=user_agent, geo=_geo(geo))
    if out.get("otp_required"):
        response.status_code = 202
    return out


@app.post("/v1/auth/login/verify")
def login_verify(body: LoginVerifyIn, conn=Depends(get_conn), deps=Depends(get_deps),
                 client_ip: str = Header("", alias="X-Client-Ip"), user_agent: str = Header("", alias="User-Agent")):
    return service.verify_login(conn, deps, challenge_id=body.challenge_id, code=body.code, ip=client_ip,
                                user_agent=user_agent)


@app.post("/v1/auth/refresh")
def refresh(body: RefreshIn, deps=Depends(get_deps)):
    return service.refresh(deps, body.refresh_token)


def get_session_id(x_session_id: str = Header("", alias="X-Session-Id")) -> str:
    return x_session_id


@app.post("/v1/auth/logout", status_code=204)
def logout(customer_id: UUID = Depends(get_customer), session_id: str = Depends(get_session_id),
           deps=Depends(get_deps)):
    service.logout(deps, customer_id, session_id)


@app.post("/v1/auth/lock")
def lock(customer_id: UUID = Depends(get_customer), session_id: str = Depends(get_session_id),
         deps=Depends(get_deps)):
    return service.lock_session(deps, customer_id, session_id)


@app.post("/v1/auth/unlock")
def unlock(body: UnlockIn, customer_id: UUID = Depends(get_customer), session_id: str = Depends(get_session_id),
           conn=Depends(get_conn), deps=Depends(get_deps)):
    return service.unlock_session(conn, deps, customer_id, session_id, pin=body.pin, password=body.password)


@app.delete("/v1/auth/sessions/{other_session_id}", status_code=204)
def end_session(other_session_id: str, customer_id: UUID = Depends(get_customer), deps=Depends(get_deps)):
    service.end_other_session(deps, customer_id, other_session_id)


# ---- PINs


@app.get("/v1/auth/pins")
def pin_status(customer_id: UUID = Depends(get_customer), conn=Depends(get_conn), deps=Depends(get_deps)):
    return pins.status(conn, deps, customer_id)


@app.put("/v1/auth/pins/{kind}")
def set_pin(kind: str, body: PinIn, customer_id: UUID = Depends(get_customer), conn=Depends(get_conn),
            deps=Depends(get_deps)):
    return pins.set_pin(conn, deps, customer_id, kind, body.pin, password=body.password,
                        current_pin=body.current_pin)


@app.post("/v1/auth/pins/{kind}/reset-code")
def pin_reset_code(kind: str, customer_id: UUID = Depends(get_customer), conn=Depends(get_conn),
                   deps=Depends(get_deps)):
    return pins.request_reset(conn, deps, customer_id, kind)


@app.post("/v1/auth/pins/{kind}/reset")
def pin_reset(kind: str, body: PinResetIn, customer_id: UUID = Depends(get_customer), conn=Depends(get_conn),
              deps=Depends(get_deps)):
    return pins.reset(conn, deps, customer_id, kind, code=body.code, password=body.password, new_pin=body.new_pin)


# ---- password and contact details


@app.post("/v1/auth/password")
def change_password(body: PasswordChangeIn, customer_id: UUID = Depends(get_customer),
                    session_id: str = Depends(get_session_id), conn=Depends(get_conn), deps=Depends(get_deps)):
    return recovery.change_password(conn, deps, customer_id, session_id, current_password=body.current_password,
                                    new_password=body.new_password)


@app.post("/v1/auth/password/forgot", status_code=202)
def forgot_password(body: ForgotIn, conn=Depends(get_conn), deps=Depends(get_deps)):
    return recovery.forgot_password(conn, deps, body.identifier)


@app.post("/v1/auth/password/reset")
def reset_password(body: ResetPasswordIn, conn=Depends(get_conn), deps=Depends(get_deps)):
    return recovery.reset_password(conn, deps, identifier=body.identifier, code=body.code,
                                   date_of_birth=body.date_of_birth, new_password=body.new_password)


@app.post("/v1/auth/contact", status_code=202)
def start_contact_change(body: ContactIn, customer_id: UUID = Depends(get_customer), conn=Depends(get_conn),
                         deps=Depends(get_deps)):
    return recovery.start_contact_change(conn, deps, customer_id, field=body.field, value=body.value,
                                         password=body.password)


@app.post("/v1/auth/contact/confirm")
def confirm_contact_change(body: CodeIn, customer_id: UUID = Depends(get_customer), conn=Depends(get_conn),
                           deps=Depends(get_deps)):
    return recovery.confirm_contact_change(conn, deps, customer_id, code=body.code)


@app.get("/v1/auth/devices")
def devices(customer_id: UUID = Depends(get_customer), conn=Depends(get_conn)):
    return service.list_devices(conn, customer_id)


@app.delete("/v1/auth/devices/{device_id}")
def revoke_device(device_id: UUID, customer_id: UUID = Depends(get_customer), conn=Depends(get_conn),
                  deps=Depends(get_deps)):
    return service.revoke_device(conn, deps, customer_id, device_id)


@app.get("/v1/auth/sessions")
def sessions(customer_id: UUID = Depends(get_customer), session_id: str = Depends(get_session_id),
             deps=Depends(get_deps)):
    return service.list_sessions(deps, customer_id, current=session_id)


@app.get("/v1/auth/login-history")
def login_history(customer_id: UUID = Depends(get_customer), deps=Depends(get_deps)):
    return service.login_history(deps, customer_id)


@app.post("/v1/staff/login")
def staff_login(body: StaffLoginIn, conn=Depends(get_conn), deps=Depends(get_deps)):
    return service.staff_login(conn, deps, body.staff_id, body.password)


@app.post("/v1/staff/logout", status_code=204)
def staff_logout(x_actor: str = Header(..., alias="X-Actor"), session_id: str = Depends(get_session_id),
                 deps=Depends(get_deps)):
    actor = parse_actor(x_actor)
    if actor.kind != "staff":
        raise HTTPException(status_code=403, detail="staff only")
    service.staff_logout(deps, actor.id, session_id)


@app.get("/internal/v1/sessions/{session_id}/active")
def session_active(session_id: str, deps=Depends(get_deps)):
    return {"active": service.session_is_active(deps, session_id)}


@app.post("/internal/v1/sessions/{session_id}/check")
def session_check(session_id: str, activity: bool = True, deps=Depends(get_deps)):
    """The gateway asks this on every signed-in request: ACTIVE, LOCKED or ENDED."""
    return service.check_session(deps, session_id, activity=activity)


@app.post("/internal/v1/pins/verify")
def pin_verify(body: PinVerifyIn, conn=Depends(get_conn), deps=Depends(get_deps)):
    """The gateway asks this before forwarding a request that needs a PIN."""
    return pins.verify(conn, deps, body.customer_id, body.kind, body.pin)


@app.post("/internal/v1/customers/{customer_id}/lockdown")
def lockdown(customer_id: UUID, x_actor: str = Header("system:unknown", alias="X-Actor"), conn=Depends(get_conn),
             deps=Depends(get_deps)):
    return service.lockdown(conn, deps, customer_id, x_actor)


@app.post("/internal/v1/otp/issue")
def otp_issue(body: OtpIssueIn, conn=Depends(get_conn), deps=Depends(get_deps)):
    return service.issue_otp(conn, deps, body.customer_id, body.purpose, body.context)


@app.post("/internal/v1/otp/verify")
def otp_verify(body: OtpVerifyIn, conn=Depends(get_conn)):
    return {"result": service.verify_otp(conn, body.customer_id, body.purpose, body.code)}


# ----------------------------------------------------------------- KYC documents


class RejectIn(BaseModel):
    reason: str = Field(..., min_length=5, max_length=300)


def get_actor(x_actor: str = Header(..., alias="X-Actor")):
    return parse_actor(x_actor)


def _file_response(data: bytes, content_type: str, filename: str) -> Response:
    """An uploaded file is untrusted: never let a browser guess its type or run anything in it."""
    return Response(content=data, media_type=content_type, headers={
        "Content-Disposition": f'inline; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "Cache-Control": "no-store",
    })


@app.post("/v1/kyc/documents", status_code=201)
async def kyc_upload(request: Request, doc_type: str, customer_id: UUID = Depends(get_customer),
                     deps=Depends(get_deps)):
    """The request body is the file itself (JPEG, PNG or PDF)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > kyc.MAX_BYTES:
        raise kyc.TooLarge(f"The file is larger than {kyc.MAX_BYTES // (1024 * 1024)} MB.")
    data = await request.body()

    def work():
        with _state["pool"].connection() as conn:
            return kyc.upload(conn, deps, customer_id, doc_type, data)

    return await run_in_threadpool(work)


@app.get("/v1/kyc/documents")
def kyc_mine(customer_id: UUID = Depends(get_customer), conn=Depends(get_conn)):
    return kyc.my_documents(conn, customer_id)


@app.get("/v1/kyc/documents/{document_id}/content")
def kyc_content(document_id: UUID, actor=Depends(get_actor), conn=Depends(get_conn), deps=Depends(get_deps)):
    return _file_response(*kyc.content(conn, deps, document_id, actor))


@app.get("/internal/v1/kyc/queue")
def kyc_queue(limit: int = 50, conn=Depends(get_conn)):
    return kyc.queue(conn, limit)


@app.get("/internal/v1/kyc/customers/{customer_id}/documents")
def kyc_customer_documents(customer_id: UUID, conn=Depends(get_conn)):
    return kyc.customer_documents(conn, customer_id)


@app.get("/internal/v1/kyc/customers/{customer_id}/events")
def kyc_events(customer_id: UUID, conn=Depends(get_conn)):
    return kyc.events(conn, customer_id)


@app.post("/internal/v1/kyc/documents/{document_id}/approve")
def kyc_approve(document_id: UUID, actor=Depends(get_actor), conn=Depends(get_conn), deps=Depends(get_deps)):
    return kyc.approve(conn, deps, document_id, actor)


@app.post("/internal/v1/kyc/documents/{document_id}/reject")
def kyc_reject(document_id: UUID, body: RejectIn, actor=Depends(get_actor), conn=Depends(get_conn),
               deps=Depends(get_deps)):
    return kyc.reject(conn, deps, document_id, actor, body.reason)
