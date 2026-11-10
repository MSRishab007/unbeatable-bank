"""Clients for the services Intake depends on.

    Account     REST   who may pay from an account, who the payee is
    Rate limit  gRPC   shared limits (backed by DynaStore)
    Risk        gRPC   fraud score and decision (features in DynaStore)
    Ledger      gRPC   holds and the actual money movement
    Identity    REST   one-time passwords for step-up confirmation

Each is behind a small Protocol so tests can swap in fakes, and rate limiting,
risk and identity have do-nothing versions so the service also runs without them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

import grpc
from ubcommon import grpcutil
import httpx

from ubproto.ledger.v1 import ledger_pb2, ledger_pb2_grpc
from ubproto.ratelimit.v1 import ratelimit_pb2, ratelimit_pb2_grpc
from ubproto.risk.v1 import risk_pb2, risk_pb2_grpc


class DependencyUnavailable(Exception):
    """A downstream service could not be reached; the outcome is unknown."""


# ----------------------------------------------------------------- Account (REST)


class AccountClient(Protocol):
    def precheck(self, customer_id: UUID, from_account_id: UUID, payee: str) -> dict: ...

    def lookup_number(self, account_number: str) -> dict | None: ...

    def freeze(self, account_id: UUID, reason: str, actor: str) -> None: ...


class HttpAccountClient:
    def __init__(self, base_url: str | None = None, timeout: float = 2.0) -> None:
        self._http = httpx.Client(base_url=base_url or os.environ["ACCOUNT_URL"], timeout=timeout,
                                  transport=httpx.HTTPTransport(retries=2))

    def _request(self, method: str, path: str, ok=(200,), **kwargs):
        try:
            r = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise DependencyUnavailable(f"account service: {e}") from e
        if r.status_code not in ok:
            raise DependencyUnavailable(f"account service {r.status_code}: {r.text[:200]}")
        return r

    def precheck(self, customer_id, from_account_id, payee):
        return self._request("POST", "/internal/v1/payments/precheck", json={
            "customer_id": str(customer_id), "from_account_id": str(from_account_id), "payee": payee}).json()

    def lookup_number(self, account_number):
        r = self._request("GET", f"/v1/accounts/by-number/{account_number}", ok=(200, 400, 404))
        return r.json() if r.status_code == 200 else None

    def freeze(self, account_id, reason, actor):
        self._request("POST", f"/v1/accounts/{account_id}/freeze", ok=(200, 409),
                      json={"reason": reason}, headers={"X-Actor": actor})


# ----------------------------------------------------------------- Rate limit (gRPC)


@dataclass
class LimitDecision:
    allowed: bool
    limited_by: str = ""
    retry_after: int = 0
    degraded: bool = False


class RateLimiter(Protocol):
    def check(self, *, customer_id: str, account_id: str, device_id: str, ip: str, amount: int) -> LimitDecision: ...


class NoRateLimiter:
    def check(self, **_):
        return LimitDecision(True)


class GrpcRateLimiter:
    def __init__(self, target: str | None = None, timeout: float = 1.0) -> None:
        self._stub = ratelimit_pb2_grpc.RateLimitServiceStub(
            grpcutil.channel(target or os.environ["RATELIMIT_GRPC"]))
        self._timeout = timeout

    def check(self, *, customer_id, account_id, device_id, ip, amount):
        descriptors = [ratelimit_pb2.Descriptor(scope=s, id=i) for s, i in
                       (("customer", customer_id), ("account", account_id), ("device", device_id), ("ip", ip)) if i]
        try:
            r = self._stub.Check(ratelimit_pb2.CheckRequest(policy_set="payments", descriptors=descriptors,
                                                            cost=1, amount=amount), timeout=self._timeout)
        except grpc.RpcError:
            # The coordinator itself is down: same policy as when its store is down.
            return LimitDecision(amount <= int(os.getenv("FAIL_OPEN_MAX_AMOUNT", "200000")),
                                 "limiter_unavailable", degraded=True)
        return LimitDecision(r.allowed, r.limited_by, r.retry_after_seconds, r.degraded)


# ----------------------------------------------------------------- Risk (gRPC)


@dataclass
class RiskDecision:
    decision: str = "ALLOW"            # ALLOW, CHALLENGE, HOLD, BLOCK
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    degraded: bool = False


class RiskClient(Protocol):
    def evaluate(self, *, txn_id: str, customer_id: str, from_account_id: str, to_account_id: str,
                 amount: int, channel: str, device_id: str, ip: str, payee_trusted: bool,
                 account_idle_days: int) -> RiskDecision: ...


class NoRisk:
    def evaluate(self, **_):
        return RiskDecision()


class GrpcRiskClient:
    def __init__(self, target: str | None = None, timeout: float = 1.5) -> None:
        self._stub = risk_pb2_grpc.RiskServiceStub(grpcutil.channel(target or os.environ["RISK_GRPC"]))
        self._timeout = timeout
        self._hold_amount = int(os.getenv("RISK_DOWN_HOLD_AMOUNT", "2000000"))

    def evaluate(self, **kw):
        try:
            r = self._stub.Evaluate(risk_pb2.EvaluateRequest(**kw), timeout=self._timeout)
        except grpc.RpcError:
            # No risk engine: let small payments through, hold large ones for a human.
            held = kw["amount"] >= self._hold_amount
            return RiskDecision("HOLD" if held else "ALLOW", 0, ["RISK_ENGINE_UNAVAILABLE"] if held else [], True)
        return RiskDecision(r.decision, r.score, list(r.reasons), r.degraded)


# ----------------------------------------------------------------- Identity (REST)


class IdentityClient(Protocol):
    def issue_otp(self, customer_id: UUID, purpose: str, context: str) -> None: ...

    def verify_otp(self, customer_id: UUID, purpose: str, code: str) -> str:
        """Returns OK, WRONG or LOCKED."""


class DevIdentity:
    """Stands in when Identity & Auth isn't deployed: the code is always 123456."""

    def issue_otp(self, customer_id, purpose, context):
        pass

    def verify_otp(self, customer_id, purpose, code):
        return "OK" if code == "123456" else "WRONG"


class HttpIdentityClient:
    def __init__(self, base_url: str | None = None, timeout: float = 3.0) -> None:
        self._http = httpx.Client(base_url=base_url or os.environ["IDENTITY_URL"], timeout=timeout)

    def _post(self, path: str, body: dict) -> dict:
        try:
            r = self._http.post(path, json=body)
        except httpx.HTTPError as e:
            raise DependencyUnavailable(f"identity service: {e}") from e
        if r.status_code >= 500:
            raise DependencyUnavailable(f"identity service {r.status_code}")
        return r.json()

    def issue_otp(self, customer_id, purpose, context):
        self._post("/internal/v1/otp/issue", {"customer_id": str(customer_id), "purpose": purpose,
                                              "context": context})

    def verify_otp(self, customer_id, purpose, code):
        return self._post("/internal/v1/otp/verify", {"customer_id": str(customer_id), "purpose": purpose,
                                                      "code": code}).get("result", "WRONG")


# ----------------------------------------------------------------- Ledger (gRPC)


@dataclass
class LedgerRejected(Exception):
    """The Ledger refused for a business reason (a definite outcome)."""

    code: str
    message: str


class LedgerClient(Protocol):
    def post_journal(self, *, idempotency_key: str, txn_type: str, legs: list[tuple[UUID, int]],
                     description: str | None = None, metadata: dict[str, str] | None = None,
                     capture_hold_id: UUID | None = None) -> str: ...

    def create_hold(self, *, idempotency_key: str, account_id: UUID, amount: int, reason: str) -> str: ...

    def release_hold(self, hold_id: UUID) -> None: ...


# gRPC codes that mean "the ledger decided"; anything else means "unknown, retry".
_DEFINITE = {grpc.StatusCode.FAILED_PRECONDITION, grpc.StatusCode.INVALID_ARGUMENT,
             grpc.StatusCode.NOT_FOUND, grpc.StatusCode.ALREADY_EXISTS}


class GrpcLedgerClient:
    def __init__(self, target: str | None = None, timeout: float = 3.0) -> None:
        self._channel = grpcutil.channel(target or os.environ["LEDGER_GRPC"])
        self._stub = ledger_pb2_grpc.LedgerServiceStub(self._channel)
        self._timeout = timeout

    def _call(self, method: str, request):
        try:
            return getattr(self._stub, method)(request, timeout=self._timeout)
        except grpc.RpcError as e:
            if e.code() in _DEFINITE:
                code = dict(e.trailing_metadata() or ()).get("error-code", e.code().name)
                raise LedgerRejected(code, e.details()) from e
            raise DependencyUnavailable(f"ledger: {e.code().name}") from e

    def post_journal(self, *, idempotency_key, txn_type, legs, description=None, metadata=None,
                     capture_hold_id=None):
        return self._call("PostJournal", ledger_pb2.PostJournalRequest(
            idempotency_key=idempotency_key, txn_type=txn_type,
            legs=[ledger_pb2.Leg(account_id=str(a), amount=amt) for a, amt in legs],
            description=description or "", metadata=metadata or {},
            capture_hold_id=str(capture_hold_id) if capture_hold_id else "")).journal_id

    def create_hold(self, *, idempotency_key, account_id, amount, reason):
        return self._call("CreateHold", ledger_pb2.CreateHoldRequest(
            idempotency_key=idempotency_key, account_id=str(account_id), amount=amount, reason=reason)).hold_id

    def release_hold(self, hold_id):
        self._call("ReleaseHold", ledger_pb2.ReleaseHoldRequest(hold_id=str(hold_id)))

    def close(self) -> None:
        self._channel.close()
