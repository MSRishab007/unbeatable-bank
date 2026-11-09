"""Clients for the services Intake depends on.

Account over REST (httpx), Ledger over gRPC. Both are behind small Protocols
so tests can swap in fakes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import grpc
import httpx

from . import gen  # noqa: F401  (puts generated code on sys.path)
from ledger.v1 import ledger_pb2, ledger_pb2_grpc  # noqa: E402


class DependencyUnavailable(Exception):
    """A downstream service could not be reached; the outcome is unknown."""


class NotFoundError(Exception):
    pass


class BadRequest(Exception):
    pass


# ----------------------------------------------------------------- Account (REST)


class AccountClient(Protocol):
    def authorize(self, account_id: UUID, customer_id: UUID) -> dict: ...

    def lookup_number(self, account_number: str) -> dict: ...

    def resolve_alias(self, alias: str) -> dict: ...

    def beneficiaries(self, customer_id: UUID) -> list[dict]: ...

    def customer_accounts(self, customer_id: UUID) -> list[dict]: ...


class HttpAccountClient:
    def __init__(self, base_url: str | None = None, timeout: float = 2.0) -> None:
        self._http = httpx.Client(base_url=base_url or os.environ["ACCOUNT_URL"], timeout=timeout,
                                  transport=httpx.HTTPTransport(retries=2))

    def _get(self, path: str):
        try:
            r = self._http.get(path)
        except httpx.HTTPError as e:
            raise DependencyUnavailable(f"account service: {e}") from e
        if r.status_code == 404:
            raise NotFoundError(r.json().get("message", "not found"))
        if r.status_code == 400:
            raise BadRequest(r.json().get("message", "bad request"))
        if r.status_code >= 400:
            raise DependencyUnavailable(f"account service {r.status_code}")
        return r.json()

    def authorize(self, account_id: UUID, customer_id: UUID) -> dict:
        return self._get(f"/internal/v1/accounts/{account_id}/authorize/{customer_id}")

    def lookup_number(self, account_number: str) -> dict:
        return self._get(f"/v1/accounts/by-number/{account_number}")

    def resolve_alias(self, alias: str) -> dict:
        return self._get(f"/v1/aliases/{alias}")

    def beneficiaries(self, customer_id: UUID) -> list[dict]:
        return self._get(f"/v1/customers/{customer_id}/beneficiaries")

    def customer_accounts(self, customer_id: UUID) -> list[dict]:
        return self._get(f"/v1/customers/{customer_id}/accounts")


# ----------------------------------------------------------------- Ledger (gRPC)


@dataclass
class LedgerRejected(Exception):
    """The Ledger refused the posting for a business reason (definite outcome)."""

    code: str
    message: str


class LedgerClient(Protocol):
    def post_transfer(self, *, idempotency_key: str, txn_type: str, from_account: UUID,
                      to_account: UUID, amount: int, description: str | None,
                      metadata: dict[str, str]) -> str: ...


# gRPC codes that mean "the ledger decided"; anything else means "unknown, retry".
_DEFINITE = {
    grpc.StatusCode.FAILED_PRECONDITION,
    grpc.StatusCode.INVALID_ARGUMENT,
    grpc.StatusCode.NOT_FOUND,
    grpc.StatusCode.ALREADY_EXISTS,
}


class GrpcLedgerClient:
    def __init__(self, target: str | None = None, timeout: float = 3.0) -> None:
        self._channel = grpc.insecure_channel(target or os.environ["LEDGER_GRPC"])
        self._stub = ledger_pb2_grpc.LedgerServiceStub(self._channel)
        self._timeout = timeout

    def post_transfer(self, *, idempotency_key, txn_type, from_account, to_account, amount,
                      description, metadata) -> str:
        request = ledger_pb2.PostJournalRequest(
            idempotency_key=idempotency_key,
            txn_type=txn_type,
            legs=[
                ledger_pb2.Leg(account_id=str(from_account), amount=-amount),
                ledger_pb2.Leg(account_id=str(to_account), amount=amount),
            ],
            description=description or "",
            metadata=metadata,
        )
        try:
            return self._stub.PostJournal(request, timeout=self._timeout).journal_id
        except grpc.RpcError as e:
            if e.code() in _DEFINITE:
                code = dict(e.trailing_metadata() or ()).get("error-code", e.code().name)
                raise LedgerRejected(code, e.details()) from e
            raise DependencyUnavailable(f"ledger: {e.code().name}") from e

    def close(self) -> None:
        self._channel.close()
