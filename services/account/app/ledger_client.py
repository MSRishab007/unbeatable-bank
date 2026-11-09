"""Calls to the Ledger service. Both operations are idempotent on the Ledger
side, so retries are safe."""

import os
from typing import Protocol
from uuid import UUID

import httpx


class LedgerUnavailable(Exception):
    pass


class LedgerClient(Protocol):
    def open_account(self, account_id: UUID, product_type: str, overdraft_limit: int) -> None: ...

    def set_status(self, account_id: UUID, status: str) -> None: ...


class HttpLedgerClient:
    def __init__(self, base_url: str | None = None, timeout: float = 3.0) -> None:
        self._http = httpx.Client(
            base_url=base_url or os.environ["LEDGER_URL"],
            timeout=timeout,
            transport=httpx.HTTPTransport(retries=2),
        )

    def _call(self, method: str, path: str, json: dict) -> None:
        try:
            r = self._http.request(method, path, json=json)
        except httpx.HTTPError as e:
            raise LedgerUnavailable(str(e)) from e
        if r.status_code >= 400:
            raise LedgerUnavailable(f"ledger {r.status_code}: {r.text}")

    def open_account(self, account_id: UUID, product_type: str, overdraft_limit: int) -> None:
        self._call("POST", "/v1/accounts", {
            "account_id": str(account_id), "product_type": product_type,
            "overdraft_limit": overdraft_limit,
        })

    def set_status(self, account_id: UUID, status: str) -> None:
        self._call("PATCH", f"/v1/accounts/{account_id}", {"status": status})
