import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from ubcommon.auth import issue_token

from app import main

SECRET = "gateway-test-secret"
RIYA, ARJUN = str(uuid.uuid4()), str(uuid.uuid4())
RIYA_ACCOUNT = str(uuid.uuid4())


class Upstreams:
    """Fake internal services. Records what the gateway sent them."""

    def __init__(self):
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        service = request.url.host
        self.calls.append({"service": service, "method": request.method, "path": request.url.path,
                           "query": dict(request.url.params), "headers": dict(request.headers),
                           "body": request.content})
        if service == "account" and "/authorize/" in request.url.path:
            account_id, customer_id = request.url.path.split("/")[4], request.url.path.split("/")[6]
            return httpx.Response(200, json={"is_holder": (account_id, customer_id) == (RIYA_ACCOUNT, RIYA)})
        if service == "account" and request.url.path.endswith("/accounts") and request.method == "GET":
            return httpx.Response(200, json=[{"account_id": RIYA_ACCOUNT, "product_type": "SAVINGS"}])
        if service == "ledger" and request.url.path == "/v1/balances":
            return httpx.Response(200, json=[{"account_id": RIYA_ACCOUNT, "ledger_balance": 500, "available_balance": 400}])
        if service == "identity" and request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"%PDF-file-bytes", headers={
                "Content-Type": "application/pdf", "Content-Disposition": 'inline; filename="pan.pdf"',
                "Content-Security-Policy": "sandbox; default-src 'none'", "Cache-Control": "no-store",
                "Set-Cookie": "leak=1", "X-Internal-Debug": "secret"})
        if service == "intake" and request.url.path == "/v1/payments" and request.method == "POST":
            return httpx.Response(422, json={"status": "DECLINED", "decline_code": "INSUFFICIENT_FUNDS"})
        return httpx.Response(200, json={"ok": True, "service": service, "path": request.url.path})

    def last(self, service=None):
        return [c for c in self.calls if service in (None, c["service"])][-1]


@pytest.fixture
def up():
    return Upstreams()


@pytest.fixture
def client(up):
    main._state.clear()
    main._state.update(http=httpx.AsyncClient(transport=httpx.MockTransport(up.handler)),
                       upstreams={name: f"http://{name}" for name in main.SERVICES}, jwt_secret=SECRET)
    with TestClient(main.app) as c:
        yield c
    main._state.clear()


def customer(sub=RIYA, **claims):
    token = issue_token(subject=sub, role="customer", secret=SECRET, ttl_seconds=60,
                        sid="session-1", did="device-1", **claims)
    return {"Authorization": f"Bearer {token}"}


def staff(role):
    token = issue_token(subject="asha", role="staff", secret=SECRET, ttl_seconds=60, staff_role=role)
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------ authentication


def test_public_routes_need_no_token(client, up):
    r = client.post("/api/auth/login", json={"identifier": "a@b.c", "password": "x", "device_fingerprint": "f" * 10})
    assert r.status_code == 200 and up.last()["path"] == "/v1/auth/login"
    assert "x-actor" not in up.last()["headers"]


def test_protected_routes_need_a_valid_token(client, up):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    forged = issue_token(subject=RIYA, role="customer", secret="wrong-secret", ttl_seconds=60)
    assert client.get("/api/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    expired = issue_token(subject=RIYA, role="customer", secret=SECRET, ttl_seconds=-5)
    assert client.get("/api/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    assert up.calls == []


def test_caller_identity_is_passed_on_and_cannot_be_spoofed(client, up):
    r = client.get("/api/me", headers={**customer(), "X-Actor": f"customer:{ARJUN}", "X-Device-Id": "evil"})
    assert r.status_code == 200
    call = up.last("account")
    assert call["path"] == f"/v1/customers/{RIYA}"
    assert call["headers"]["x-actor"] == f"customer:{RIYA}"          # from the token, not the request
    assert call["headers"]["x-device-id"] == "device-1"
    assert "authorization" not in call["headers"]                     # the token stops at the gateway


# ------------------------------------------------------------------ authorisation


def test_customers_only_reach_their_own_accounts(client, up):
    assert client.get(f"/api/accounts/{RIYA_ACCOUNT}/transactions", headers=customer()).status_code == 200
    assert up.last("ledger")["path"] == f"/v1/accounts/{RIYA_ACCOUNT}/entries"
    r = client.get(f"/api/accounts/{RIYA_ACCOUNT}/transactions", headers=customer(ARJUN))
    assert r.status_code == 404 and up.last()["service"] == "account"   # never reached the ledger
    assert client.post(f"/api/accounts/{RIYA_ACCOUNT}/freeze", headers=customer(ARJUN),
                       json={"reason": "mischief"}).status_code == 404
    assert client.get("/api/accounts/not-a-uuid/balance", headers=customer()).status_code == 404


def test_staff_routes_check_roles(client, up):
    assert client.get("/api/staff/cases", headers=customer()).status_code == 403
    assert client.get("/api/staff/cases", headers=staff("TELLER")).status_code == 403
    assert client.get("/api/staff/cases", headers=staff("ANALYST")).status_code == 200
    assert up.last("fraud")["headers"]["x-actor"] == "staff:asha:ANALYST"
    assert client.post("/api/staff/cash-deposits", headers=staff("ANALYST"), json={}).status_code == 403
    assert client.post("/api/staff/cash-deposits", headers=staff("TELLER"), json={}).status_code == 200
    assert client.post("/api/staff/ops/eod/run", headers=staff("ANALYST")).status_code == 403
    assert client.post("/api/staff/ops/eod/run", headers=staff("ADMIN")).status_code == 200   # ADMIN does all
    assert client.get("/api/staff/ops/reconciliation", headers=staff("KYC")).status_code == 200  # any staff
    assert client.get("/api/payments", headers=staff("ADMIN")).status_code == 403             # staff aren't customers


# ------------------------------------------------------------------ forwarding


def test_payment_forwarding_keeps_status_body_and_idempotency_key(client, up):
    r = client.post("/api/payments", headers={**customer(), "Idempotency-Key": "abc-123",
                                              "X-Forwarded-For": "198.51.100.4, 10.0.0.1"},
                    json={"from_account_id": RIYA_ACCOUNT, "payee": "500100000016", "amount": 100})
    assert r.status_code == 422 and r.json()["decline_code"] == "INSUFFICIENT_FUNDS"
    call = up.last("intake")
    assert call["headers"]["idempotency-key"] == "abc-123"
    assert call["headers"]["x-client-ip"] == "198.51.100.4"
    assert json.loads(call["body"])["amount"] == 100


def test_query_string_and_path_parameters_are_forwarded(client, up):
    client.get("/api/deposits/quote?product=FD&amount=100000&tenure_months=12")
    assert up.last("deposits")["query"] == {"product": "FD", "amount": "100000", "tenure_months": "12"}
    client.get("/api/staff/ops/dynastore/keys/rl:payments:account:abc:60:123", headers=staff("ANALYST"))
    assert up.last("dynastore")["path"] == "/v1/keys/rl:payments:account:abc:60:123"


def test_dashboard_combines_accounts_and_balances(client, up):
    r = client.get("/api/me/accounts", headers=customer())
    assert r.json() == [{"account_id": RIYA_ACCOUNT, "product_type": "SAVINGS", "ledger_balance": 500,
                         "available_balance": 400}]


def test_opening_an_account_always_makes_the_caller_primary_holder(client, up):
    client.post("/api/accounts", headers=customer(),
                json={"product_type": "SAVINGS", "holder_ids": [ARJUN], "joint_holder_ids": [ARJUN]})
    assert json.loads(up.last("account")["body"])["holder_ids"] == [RIYA, ARJUN]


def test_unknown_route_and_upstream_failure(client, up):
    assert client.get("/api/nothing-here", headers=customer()).status_code == 404

    def boom(request):
        raise httpx.ConnectError("refused")

    main._state["http"] = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    r = client.get("/api/payments", headers=customer())
    assert r.status_code == 503 and r.json()["error"] == "UPSTREAM_UNAVAILABLE"


def test_serves_the_web_app(client):
    r = client.get("/")
    assert r.status_code == 200 and "Unbeatable Bank" in r.text


def test_sim_headers_only_when_enabled(client, up, monkeypatch):
    client.post("/api/auth/login", headers={"X-Sim-Ip": "1.2.3.4", "X-Geo": "51.5,-0.1"}, json={})
    assert "x-geo" not in up.last()["headers"] and up.last()["headers"]["x-client-ip"] != "1.2.3.4"
    monkeypatch.setenv("ALLOW_SIM_HEADERS", "true")
    client.post("/api/auth/login", headers={"X-Sim-Ip": "1.2.3.4", "X-Geo": "51.5,-0.1"}, json={})
    assert up.last()["headers"]["x-geo"] == "51.5,-0.1" and up.last()["headers"]["x-client-ip"] == "1.2.3.4"


# ------------------------------------------------------------------ files and limits


def test_kyc_upload_passes_the_file_through_untouched(client, up):
    data = bytes(range(256)) * 100
    r = client.post("/api/kyc/documents?doc_type=PAN", content=data,
                    headers={**customer(), "Content-Type": "image/png", "X-Actor": "staff:evil:ADMIN"})
    assert r.status_code == 200
    call = up.last("identity")
    assert (call["path"], call["query"], call["body"]) == ("/v1/kyc/documents", {"doc_type": "PAN"}, data)
    assert call["headers"]["content-type"] == "image/png" and call["headers"]["x-actor"] == f"customer:{RIYA}"


def test_kyc_review_is_for_kyc_officers_only(client, up):
    document = str(uuid.uuid4())
    for path, method in ((f"/api/staff/kyc-documents/{document}/content", "GET"), ("/api/staff/kyc-queue", "GET"),
                         (f"/api/staff/kyc-documents/{document}/approve", "POST"),
                         (f"/api/staff/kyc-documents/{document}/reject", "POST")):
        assert client.request(method, path, headers=staff("TELLER")).status_code == 403
        assert client.request(method, path, headers=staff("ANALYST")).status_code == 403
        assert client.request(method, path, headers=customer()).status_code == 403
        assert client.request(method, path).status_code == 401
        assert client.request(method, path, headers=staff("KYC")).status_code == 200
    assert up.last("identity")["headers"]["x-actor"] == "staff:asha:KYC"


def test_file_downloads_keep_their_safety_headers_and_nothing_else(client, up):
    r = client.get(f"/api/kyc/documents/{uuid.uuid4()}/content", headers=customer())
    assert r.content == b"%PDF-file-bytes" and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'inline; filename="pan.pdf"'
    assert r.headers["content-security-policy"] == "sandbox; default-src 'none'"       # the file's own, not the web app's
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert "set-cookie" not in r.headers and "x-internal-debug" not in r.headers


def test_oversized_requests_are_refused_before_reaching_a_service(client, up, monkeypatch):
    monkeypatch.setattr(main, "MAX_BODY_BYTES", 1000)
    before = len(up.calls)
    r = client.post("/api/kyc/documents?doc_type=PAN", content=b"x" * 1001, headers=customer())
    assert r.status_code == 413 and r.json()["error"] == "BODY_TOO_LARGE"

    def chunks():                                     # no Content-Length: the size is only known while reading
        for _ in range(11):
            yield b"x" * 100

    r = client.post("/api/kyc/documents?doc_type=PAN", content=chunks(), headers=customer())
    assert r.status_code == 413 and len(up.calls) == before
    assert client.post("/api/kyc/documents?doc_type=PAN", content=b"x" * 1000, headers=customer()).status_code == 200


def test_web_app_cannot_be_framed_or_load_foreign_scripts(client):
    page = client.get("/")
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert page.headers["x-frame-options"] == "DENY" and page.headers["x-content-type-options"] == "nosniff"
    api = client.get("/api/deposits/rates")
    assert "content-security-policy" not in api.headers and api.headers["x-content-type-options"] == "nosniff"


def test_statement_routes(client, up):
    base = f"/api/accounts/{RIYA_ACCOUNT}/statements"
    assert client.get(f"{base}/interim?date_from=2026-11-01&date_to=2026-11-10", headers=customer()).status_code == 200
    call = up.last("deposits")
    assert call["path"].endswith("/statements/interim") and call["query"] == {"date_from": "2026-11-01", "date_to": "2026-11-10"}
    assert client.get(f"{base}/2026-10", headers=customer()).status_code == 200
    assert up.last("deposits")["path"] == f"/v1/accounts/{RIYA_ACCOUNT}/statements/2026-10"
    assert client.get(f"{base}/2026-10/verify", headers=customer()).status_code == 200
    assert client.get(base, headers=customer(ARJUN)).status_code == 404            # not a holder
    assert client.get(f"{base}/2026-10", headers=customer(ARJUN)).status_code == 404
    assert client.get(f"/api/staff/accounts/{RIYA_ACCOUNT}/statements/2026-10", headers=staff("TELLER")).status_code == 200
    assert client.get(f"/api/staff/accounts/{RIYA_ACCOUNT}/statements/2026-10", headers=customer()).status_code == 403

    # Checking a PDF needs no login; whatever identity the caller claims is not passed on.
    r = client.post("/api/statements/verify", content=b"%PDF-1.4 file", headers={"X-Actor": "staff:evil:ADMIN"})
    assert r.status_code == 200
    call = up.last("deposits")
    assert (call["path"], call["body"]) == ("/v1/statements/verify-file", b"%PDF-1.4 file")
    assert "x-actor" not in call["headers"]


def test_public_statement_check_is_rate_limited_per_address(client, up, monkeypatch):
    monkeypatch.setenv("ALLOW_SIM_HEADERS", "true")
    lender, other = {"X-Sim-Ip": "198.51.100.7"}, {"X-Sim-Ip": "198.51.100.8"}
    results = [client.post("/api/statements/verify", content=b"%PDF-x", headers=lender).status_code for _ in range(12)]
    assert results == [200] * 10 + [429] * 2
    blocked = client.post("/api/statements/verify", content=b"%PDF-x", headers=lender)
    assert blocked.json()["error"] == "TOO_MANY_REQUESTS" and int(blocked.headers["retry-after"]) >= 1
    assert client.post("/api/statements/verify", content=b"%PDF-x", headers=other).status_code == 200   # someone else is fine
    assert len([c for c in up.calls if c["path"] == "/v1/statements/verify-file"]) == 11
