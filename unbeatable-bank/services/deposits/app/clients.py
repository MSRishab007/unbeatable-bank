"""REST clients for the Ledger and Account services."""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from uuid import UUID

import httpx

from .service import LedgerRejected, Unavailable

ACTOR = {"X-Actor": "system:deposits"}


class _Http:
    def __init__(self, base_url: str, timeout: float = 30) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=httpx.HTTPTransport(retries=2))

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            r = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise Unavailable(f"{path}: {e}") from e
        if r.status_code >= 500:
            raise Unavailable(f"{path}: {r.status_code}")
        return r


class HttpLedger(_Http):
    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or os.environ["LEDGER_URL"])

    def post_journal(self, *, idempotency_key, txn_type, legs, description, business_date):
        r = self._request("POST", "/v1/journals", headers={"Idempotency-Key": idempotency_key}, json={
            "txn_type": txn_type, "description": description, "business_date": business_date.isoformat(),
            "legs": [{"account_id": str(a), "amount": amount} for a, amount in legs]})
        if r.status_code >= 400:
            body = r.json()
            raise LedgerRejected(body.get("error", "LEDGER_ERROR"), body.get("message", ""))
        return r.json()["journal_id"]

    def accrue_savings(self, business_date: date, annual_rate: Decimal) -> dict:
        return self._request("POST", "/v1/eod/accrue-savings", json={
            "business_date": business_date.isoformat(), "annual_rate": f"{annual_rate:.6f}"}).json()

    def post_savings_interest(self, business_date: date) -> dict:
        return self._request("POST", "/v1/eod/post-savings-interest",
                             json={"business_date": business_date.isoformat()}).json()

    def reconciliation(self) -> dict:
        return self._request("GET", "/v1/reconciliation").json()


class HttpAccounts(_Http):
    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or os.environ["ACCOUNT_URL"], timeout=5)

    def authorize(self, account_id: UUID, customer_id: UUID) -> dict:
        return self._request("GET", f"/internal/v1/accounts/{account_id}/authorize/{customer_id}").json()

    def open_deposit_account(self, account_id: UUID, product: str, customer_id: UUID) -> dict:
        r = self._request("POST", "/internal/v1/deposit-accounts", headers=ACTOR, json={
            "account_id": str(account_id), "product_type": product, "holder_ids": [str(customer_id)]})
        if r.status_code >= 400:
            raise Unavailable(f"could not open the deposit account: {r.text[:200]}")
        return r.json()

    def date_of_birth(self, customer_id: UUID) -> date | None:
        r = self._request("GET", f"/v1/customers/{customer_id}")
        return date.fromisoformat(r.json()["date_of_birth"]) if r.status_code == 200 else None
