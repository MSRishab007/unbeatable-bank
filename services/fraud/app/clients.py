"""REST clients for the services Fraud Alert acts on."""

from __future__ import annotations

import os
from typing import Protocol
from uuid import UUID

import httpx

SYSTEM_ACTOR = "system:fraud-alert"


class Unavailable(Exception):
    pass


class IntakeClient(Protocol):
    def get(self, txn_id: UUID) -> dict | None: ...

    def release(self, txn_id: UUID, actor: str) -> dict: ...

    def cancel(self, txn_id: UUID, actor: str) -> dict: ...


class AccountClient(Protocol):
    def freeze(self, account_id: UUID, reason: str, actor: str) -> None: ...

    def unfreeze(self, account_id: UUID, actor: str) -> None: ...

    def get_account(self, account_id: UUID) -> dict | None: ...

    def get_customer(self, customer_id: UUID) -> dict | None: ...


class _Http:
    def __init__(self, base_url: str) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=5, transport=httpx.HTTPTransport(retries=2))

    def _request(self, method: str, path: str, ok=(200,), **kwargs) -> httpx.Response:
        try:
            r = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise Unavailable(str(e)) from e
        if r.status_code >= 500 or (r.status_code not in ok and r.status_code not in (404, 409)):
            raise Unavailable(f"{method} {path} -> {r.status_code}: {r.text[:200]}")
        return r


class HttpIntake(_Http):
    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or os.environ["INTAKE_URL"])

    def get(self, txn_id):
        r = self._request("GET", f"/internal/v1/payments/{txn_id}", headers={"X-Actor": SYSTEM_ACTOR})
        return r.json() if r.status_code == 200 else None

    def release(self, txn_id, actor):
        return self._request("POST", f"/internal/v1/payments/{txn_id}/release", headers={"X-Actor": actor}).json()

    def cancel(self, txn_id, actor):
        return self._request("POST", f"/internal/v1/payments/{txn_id}/cancel", headers={"X-Actor": actor}).json()


class HttpAccounts(_Http):
    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or os.environ["ACCOUNT_URL"])

    def freeze(self, account_id, reason, actor):
        self._request("POST", f"/v1/accounts/{account_id}/freeze", json={"reason": reason},
                      headers={"X-Actor": actor})

    def unfreeze(self, account_id, actor):
        self._request("POST", f"/v1/accounts/{account_id}/unfreeze", headers={"X-Actor": actor})

    def get_account(self, account_id):
        r = self._request("GET", f"/v1/accounts/{account_id}")
        return r.json() if r.status_code == 200 else None

    def get_customer(self, customer_id):
        r = self._request("GET", f"/v1/customers/{customer_id}")
        return r.json() if r.status_code == 200 else None
