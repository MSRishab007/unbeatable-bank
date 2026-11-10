"""The public API: which path goes to which service, and who may call it.

    auth     public    no token needed
             customer  a logged-in customer
             staff     bank staff; `roles` narrows it further (ADMIN may do everything)
    owner    "account": the customer must be a holder of the {account_id} in the path

In upstream paths, {sub} is the caller's id from the token, and other {names}
come from the public path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    method: str
    path: str                      # public path template
    service: str                   # key in the upstream table
    upstream: str                  # upstream path template
    auth: str = "customer"
    roles: tuple[str, ...] = ()
    owner: str | None = None

    def regex(self) -> re.Pattern:
        pattern = re.sub(r"\{(\w+):path\}", r"(?P<\1>.+)", self.path)
        pattern = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern)
        return re.compile(f"^{pattern}$")


C, S, P = "customer", "staff", "public"

ROUTES: list[Route] = [
    # ---- sign-up and login
    Route("POST", "/api/auth/register", "identity", "/v1/auth/register", P),
    Route("POST", "/api/auth/login", "identity", "/v1/auth/login", P),
    Route("POST", "/api/auth/refresh", "identity", "/v1/auth/refresh", P),
    Route("POST", "/api/auth/logout", "identity", "/v1/auth/logout"),
    Route("GET", "/api/auth/devices", "identity", "/v1/auth/devices"),
    Route("DELETE", "/api/auth/devices/{device_id}", "identity", "/v1/auth/devices/{device_id}"),
    Route("GET", "/api/auth/sessions", "identity", "/v1/auth/sessions"),
    Route("GET", "/api/auth/login-history", "identity", "/v1/auth/login-history"),
    Route("POST", "/api/staff/login", "identity", "/v1/staff/login", P),

    # ---- profile, accounts, payees
    Route("GET", "/api/me", "account", "/v1/customers/{sub}"),
    Route("GET", "/api/me/beneficiaries", "account", "/v1/customers/{sub}/beneficiaries"),
    Route("POST", "/api/me/beneficiaries", "account", "/v1/customers/{sub}/beneficiaries"),
    Route("GET", "/api/accounts/{account_id}", "account", "/v1/accounts/{account_id}", owner="account"),
    Route("GET", "/api/accounts/{account_id}/events", "account", "/v1/accounts/{account_id}/events",
          owner="account"),
    Route("POST", "/api/accounts/{account_id}/freeze", "account", "/v1/accounts/{account_id}/freeze",
          owner="account"),
    Route("PATCH", "/api/accounts/{account_id}/limits", "account", "/v1/accounts/{account_id}/limits",
          owner="account"),
    Route("POST", "/api/accounts/{account_id}/alias", "account", "/v1/accounts/{account_id}/alias",
          owner="account"),
    Route("POST", "/api/accounts/{account_id}/holders", "account", "/v1/accounts/{account_id}/holders",
          owner="account"),
    Route("GET", "/api/accounts/{account_id}/balance", "ledger", "/v1/accounts/{account_id}/balance",
          owner="account"),
    Route("GET", "/api/accounts/{account_id}/transactions", "ledger", "/v1/accounts/{account_id}/entries",
          owner="account"),
    Route("GET", "/api/payees/{account_number}", "account", "/v1/accounts/by-number/{account_number}"),
    Route("GET", "/api/aliases/{alias}", "account", "/v1/aliases/{alias}"),

    # ---- payments
    Route("POST", "/api/payments", "intake", "/v1/payments"),
    Route("GET", "/api/payments", "intake", "/v1/payments"),
    Route("GET", "/api/payments/{txn_id}", "intake", "/v1/payments/{txn_id}"),
    Route("POST", "/api/payments/{txn_id}/confirm", "intake", "/v1/payments/{txn_id}/confirm"),
    Route("POST", "/api/payments/{txn_id}/deny", "intake", "/v1/payments/{txn_id}/deny"),

    # ---- deposits
    Route("GET", "/api/deposits/rates", "deposits", "/v1/rates", P),
    Route("GET", "/api/deposits/quote", "deposits", "/v1/quote", P),
    Route("GET", "/api/deposits", "deposits", "/v1/deposits"),
    Route("POST", "/api/deposits/fd", "deposits", "/v1/deposits/fd"),
    Route("POST", "/api/deposits/rd", "deposits", "/v1/deposits/rd"),
    Route("GET", "/api/deposits/{deposit_id}", "deposits", "/v1/deposits/{deposit_id}"),
    Route("GET", "/api/deposits/{deposit_id}/closure-quote", "deposits", "/v1/deposits/{deposit_id}/closure-quote"),
    Route("POST", "/api/deposits/{deposit_id}/close", "deposits", "/v1/deposits/{deposit_id}/close"),

    # ---- alerts and fraud reports
    Route("GET", "/api/notifications", "fraud", "/v1/notifications"),
    Route("POST", "/api/notifications/{notification_id}/read", "fraud", "/v1/notifications/{notification_id}/read"),
    Route("POST", "/api/reports", "fraud", "/v1/reports"),
    Route("GET", "/api/reports", "fraud", "/v1/reports"),

    # ---- staff: fraud desk
    Route("GET", "/api/staff/alerts", "fraud", "/v1/alerts", S, ("ANALYST",)),
    Route("GET", "/api/staff/cases", "fraud", "/v1/cases", S, ("ANALYST",)),
    Route("GET", "/api/staff/cases/{case_id}", "fraud", "/v1/cases/{case_id}", S, ("ANALYST",)),
    Route("POST", "/api/staff/cases/{case_id}/resolve", "fraud", "/v1/cases/{case_id}/resolve", S, ("ANALYST",)),
    Route("GET", "/api/staff/approvals", "fraud", "/v1/approvals", S, ("ANALYST",)),
    Route("POST", "/api/staff/approvals", "fraud", "/v1/approvals", S, ("ANALYST",)),
    Route("POST", "/api/staff/approvals/{approval_id}/approve", "fraud", "/v1/approvals/{approval_id}/approve",
          S, ("ANALYST",)),
    Route("POST", "/api/staff/approvals/{approval_id}/reject", "fraud", "/v1/approvals/{approval_id}/reject",
          S, ("ANALYST",)),
    Route("GET", "/api/staff/analytics", "fraud", "/v1/analytics", S),
    Route("GET", "/api/staff/payments", "intake", "/internal/v1/payments", S, ("ANALYST",)),
    Route("GET", "/api/staff/payments/{txn_id}", "intake", "/internal/v1/payments/{txn_id}", S, ("ANALYST",)),

    # ---- staff: branch and operations
    Route("POST", "/api/staff/kyc/{customer_id}/verify", "account",
          "/internal/v1/customers/{customer_id}/kyc/verify", S, ("KYC",)),
    Route("POST", "/api/staff/cash-deposits", "intake", "/internal/v1/cash-deposits", S, ("TELLER",)),
    Route("GET", "/api/staff/customers/{customer_id}", "account", "/v1/customers/{customer_id}", S),
    Route("GET", "/api/staff/customers/{customer_id}/accounts", "account", "/v1/customers/{customer_id}/accounts", S),
    Route("GET", "/api/staff/accounts/{account_id}", "account", "/v1/accounts/{account_id}", S),
    Route("GET", "/api/staff/accounts/{account_id}/events", "account", "/v1/accounts/{account_id}/events", S),
    Route("GET", "/api/staff/accounts/{account_id}/audit", "account",
          "/internal/v1/accounts/{account_id}/events/verify", S),
    Route("GET", "/api/staff/accounts/{account_id}/transactions", "ledger", "/v1/accounts/{account_id}/entries", S),
    Route("GET", "/api/staff/ops/reconciliation", "ledger", "/v1/reconciliation", S),
    Route("GET", "/api/staff/ops/dynastore", "dynastore", "/v1/status", S),
    Route("GET", "/api/staff/ops/dynastore/keys/{key:path}", "dynastore", "/v1/keys/{key}", S),
    Route("GET", "/api/staff/ops/rate-limits", "ratelimit", "/v1/policies", S),
    Route("GET", "/api/staff/ops/risk-rules", "risk", "/v1/rules", S),
    Route("GET", "/api/staff/ops/clock", "deposits", "/internal/v1/clock", S),
    Route("POST", "/api/staff/ops/eod/run", "deposits", "/internal/v1/eod/run", S, ("ADMIN",)),
    Route("POST", "/api/staff/ops/eod/fast-forward", "deposits", "/internal/v1/eod/fast-forward", S, ("ADMIN",)),
    Route("POST", "/api/staff/ops/dynastore/faults", "dynastore", "/v1/admin/faults", S, ("ADMIN",)),
]
