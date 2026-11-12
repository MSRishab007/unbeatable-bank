"""The public API: which path goes to which service, and who may call it.

    auth     public    no token needed
             customer  a logged-in customer
             staff     bank staff; `roles` narrows it further (ADMIN may do everything)
    owner    "account": the customer must be a holder of the {account_id} in the path
    limit    (requests, seconds) allowed per client address: a brake for endpoints that need no login
    pin      "PAYMENT" or "APP": the request must carry that PIN in the X-Pin header. The gateway
             checks it with Identity and does not pass it on.
             PAYMENT (6 digits): money leaving, new payees, higher limits, new joint holders
             APP (4 digits):     deposits and nominees

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
    limit: tuple[int, int] | None = None   # (requests, seconds) per client address, for public endpoints
    pin: str | None = None                 # "PAYMENT" | "APP"

    def regex(self) -> re.Pattern:
        pattern = re.sub(r"\{(\w+):path\}", r"(?P<\1>.+)", self.path)
        pattern = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern)
        return re.compile(f"^{pattern}$")


C, S, P = "customer", "staff", "public"
PAY, APP = "PAYMENT", "APP"

# Allowed while the session is locked (idle or locked by the customer).
WHILE_LOCKED = {"/api/auth/unlock", "/api/auth/logout", "/api/auth/lock", "/api/me/lockdown", "/api/staff/logout"}

ROUTES: list[Route] = [
    # ---- sign-up and login
    Route("POST", "/api/auth/register", "identity", "/v1/auth/register", P),
    Route("POST", "/api/auth/login", "identity", "/v1/auth/login", P),
    Route("POST", "/api/auth/login/verify", "identity", "/v1/auth/login/verify", P, limit=(10, 60)),
    Route("POST", "/api/auth/refresh", "identity", "/v1/auth/refresh", P),
    Route("POST", "/api/auth/logout", "identity", "/v1/auth/logout"),
    Route("POST", "/api/auth/lock", "identity", "/v1/auth/lock"),
    Route("POST", "/api/auth/unlock", "identity", "/v1/auth/unlock"),
    Route("GET", "/api/auth/devices", "identity", "/v1/auth/devices"),
    Route("DELETE", "/api/auth/devices/{device_id}", "identity", "/v1/auth/devices/{device_id}"),
    Route("GET", "/api/auth/sessions", "identity", "/v1/auth/sessions"),
    Route("DELETE", "/api/auth/sessions/{session_id}", "identity", "/v1/auth/sessions/{session_id}"),
    Route("GET", "/api/auth/login-history", "identity", "/v1/auth/login-history"),
    Route("POST", "/api/staff/login", "identity", "/v1/staff/login", P),
    Route("POST", "/api/staff/logout", "identity", "/v1/staff/logout", S),
    Route("GET", "/api/staff/settings/notifications", "identity", "/v1/staff/settings/notifications", S, ("ADMIN",)),
    Route("PATCH", "/api/staff/settings/notifications", "identity", "/v1/staff/settings/notifications", S, ("ADMIN",)),
    Route("POST", "/api/staff/settings/notifications/resume-gmail", "identity",
          "/v1/staff/settings/notifications/resume-gmail", S, ("ADMIN",)),

    # ---- PINs, password, contact details
    Route("GET", "/api/auth/pins", "identity", "/v1/auth/pins"),
    Route("PUT", "/api/auth/pins/{kind}", "identity", "/v1/auth/pins/{kind}"),
    Route("POST", "/api/auth/pins/{kind}/reset-code", "identity", "/v1/auth/pins/{kind}/reset-code"),
    Route("POST", "/api/auth/pins/{kind}/reset", "identity", "/v1/auth/pins/{kind}/reset"),
    Route("POST", "/api/auth/password", "identity", "/v1/auth/password"),
    Route("POST", "/api/auth/password/forgot", "identity", "/v1/auth/password/forgot", P, limit=(5, 300)),
    Route("POST", "/api/auth/password/reset", "identity", "/v1/auth/password/reset", P, limit=(10, 300)),
    Route("POST", "/api/auth/contact", "identity", "/v1/auth/contact"),
    Route("POST", "/api/auth/contact/confirm", "identity", "/v1/auth/contact/confirm"),

    # ---- profile, accounts, payees
    Route("GET", "/api/me", "account", "/v1/customers/{sub}"),
    Route("GET", "/api/me/beneficiaries", "account", "/v1/customers/{sub}/beneficiaries"),
    Route("POST", "/api/me/beneficiaries", "account", "/v1/customers/{sub}/beneficiaries", pin=PAY),
    Route("DELETE", "/api/me/beneficiaries/{beneficiary_id}", "account",
          "/v1/customers/{sub}/beneficiaries/{beneficiary_id}"),
    Route("GET", "/api/accounts/{account_id}", "account", "/v1/accounts/{account_id}", owner="account"),
    Route("GET", "/api/accounts/{account_id}/events", "account", "/v1/accounts/{account_id}/events",
          owner="account"),
    Route("POST", "/api/accounts/{account_id}/freeze", "account", "/v1/accounts/{account_id}/freeze",
          owner="account"),
    Route("PATCH", "/api/accounts/{account_id}/limits", "account", "/v1/accounts/{account_id}/limits",
          owner="account", pin=PAY),
    Route("POST", "/api/accounts/{account_id}/alias", "account", "/v1/accounts/{account_id}/alias",
          owner="account"),
    Route("POST", "/api/accounts/{account_id}/holders", "account", "/v1/accounts/{account_id}/holders",
          owner="account", pin=PAY),
    Route("GET", "/api/accounts/{account_id}/nominees", "account", "/v1/accounts/{account_id}/nominees",
          owner="account"),
    Route("POST", "/api/accounts/{account_id}/nominees", "account", "/v1/accounts/{account_id}/nominees",
          owner="account", pin=APP),
    Route("DELETE", "/api/accounts/{account_id}/nominees/{nominee_id}", "account",
          "/v1/accounts/{account_id}/nominees/{nominee_id}", owner="account", pin=APP),
    Route("GET", "/api/accounts/{account_id}/balance", "ledger", "/v1/accounts/{account_id}/balance",
          owner="account"),
    Route("GET", "/api/accounts/{account_id}/transactions", "ledger", "/v1/accounts/{account_id}/entries",
          owner="account"),
    Route("GET", "/api/payees/{account_number}", "account", "/v1/accounts/by-number/{account_number}"),
    Route("GET", "/api/aliases/{alias}", "account", "/v1/aliases/{alias}"),

    # ---- payments
    Route("POST", "/api/payments", "intake", "/v1/payments", pin=PAY),
    Route("GET", "/api/payments", "intake", "/v1/payments"),
    Route("GET", "/api/payments/{txn_id}", "intake", "/v1/payments/{txn_id}"),
    Route("POST", "/api/payments/{txn_id}/confirm", "intake", "/v1/payments/{txn_id}/confirm"),
    Route("POST", "/api/payments/{txn_id}/deny", "intake", "/v1/payments/{txn_id}/deny"),

    # ---- deposits
    Route("GET", "/api/deposits/rates", "deposits", "/v1/rates", P),
    Route("GET", "/api/deposits/quote", "deposits", "/v1/quote", P),
    Route("GET", "/api/deposits", "deposits", "/v1/deposits"),
    Route("POST", "/api/deposits/fd", "deposits", "/v1/deposits/fd", pin=APP),
    Route("POST", "/api/deposits/rd", "deposits", "/v1/deposits/rd", pin=APP),
    Route("GET", "/api/deposits/{deposit_id}", "deposits", "/v1/deposits/{deposit_id}"),
    Route("GET", "/api/deposits/{deposit_id}/closure-quote", "deposits", "/v1/deposits/{deposit_id}/closure-quote"),
    Route("POST", "/api/deposits/{deposit_id}/close", "deposits", "/v1/deposits/{deposit_id}/close", pin=APP),

    # ---- statements (PDF). "interim" must come before "{period}": the first matching route wins.
    Route("GET", "/api/accounts/{account_id}/statements", "deposits", "/v1/accounts/{account_id}/statements",
          owner="account"),
    Route("GET", "/api/accounts/{account_id}/statements/interim", "deposits",
          "/v1/accounts/{account_id}/statements/interim", owner="account"),
    Route("GET", "/api/accounts/{account_id}/statements/{period}", "deposits",
          "/v1/accounts/{account_id}/statements/{period}", owner="account"),
    Route("GET", "/api/accounts/{account_id}/statements/{period}/verify", "deposits",
          "/v1/accounts/{account_id}/statements/{period}/verify", owner="account"),
    Route("POST", "/api/statements/verify", "deposits", "/v1/statements/verify-file", P,
          limit=(10, 60)),                                                                  # body: the PDF

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

    # ---- KYC documents (the request body of an upload is the file itself)
    Route("POST", "/api/kyc/documents", "identity", "/v1/kyc/documents"),
    Route("GET", "/api/kyc/documents", "identity", "/v1/kyc/documents"),
    Route("GET", "/api/kyc/documents/{document_id}/content", "identity", "/v1/kyc/documents/{document_id}/content"),
    Route("GET", "/api/staff/kyc-queue", "identity", "/internal/v1/kyc/queue", S, ("KYC",)),
    Route("GET", "/api/staff/kyc-documents/{document_id}/content", "identity",
          "/v1/kyc/documents/{document_id}/content", S, ("KYC",)),
    Route("POST", "/api/staff/kyc-documents/{document_id}/approve", "identity",
          "/internal/v1/kyc/documents/{document_id}/approve", S, ("KYC",)),
    Route("POST", "/api/staff/kyc-documents/{document_id}/reject", "identity",
          "/internal/v1/kyc/documents/{document_id}/reject", S, ("KYC",)),
    Route("GET", "/api/staff/customers/{customer_id}/kyc-documents", "identity",
          "/internal/v1/kyc/customers/{customer_id}/documents", S, ("KYC",)),
    Route("GET", "/api/staff/customers/{customer_id}/kyc-events", "identity",
          "/internal/v1/kyc/customers/{customer_id}/events", S, ("KYC",)),
    Route("POST", "/api/staff/cash-deposits", "intake", "/internal/v1/cash-deposits", S, ("TELLER",)),
    Route("GET", "/api/staff/customers", "account", "/internal/v1/customers", S),
    Route("GET", "/api/staff/customers/{customer_id}", "account", "/v1/customers/{customer_id}", S),
    Route("GET", "/api/staff/customers/{customer_id}/accounts", "account", "/v1/customers/{customer_id}/accounts", S),
    Route("GET", "/api/staff/accounts/{account_id}", "account", "/v1/accounts/{account_id}", S),
    Route("GET", "/api/staff/accounts/{account_id}/events", "account", "/v1/accounts/{account_id}/events", S),
    Route("GET", "/api/staff/accounts/{account_id}/audit", "account",
          "/internal/v1/accounts/{account_id}/events/verify", S),
    Route("GET", "/api/staff/accounts/{account_id}/transactions", "ledger", "/v1/accounts/{account_id}/entries", S),
    Route("GET", "/api/staff/accounts/{account_id}/statements", "deposits",
          "/v1/accounts/{account_id}/statements", S),
    Route("GET", "/api/staff/accounts/{account_id}/statements/{period}", "deposits",
          "/v1/accounts/{account_id}/statements/{period}", S),
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
