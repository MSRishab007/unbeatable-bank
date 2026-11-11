"""End-to-end check of the whole bank through the API gateway.

Works against docker compose, scripts/dev_stack.py or the GKE deployment:

    pip install httpx
    python scripts/smoke_test.py                       # http://localhost:8080
    python scripts/smoke_test.py http://<public-ip>    # deployed system

Staff logins default to the development ones; override with ADMIN_PASSWORD,
ANALYST_PASSWORD and ANALYST2_PASSWORD when testing a real deployment (`make smoke`
reads them from infra/main/terraform.tfvars). Exits 1 if any check fails.
Use --skip-eod to leave the bank's calendar alone (fast-forward moves it ~3 months).
"""

from __future__ import annotations

import os
import re
import sys
import time
import uuid

import httpx

args = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = (args[0] if args else os.getenv("GATEWAY_URL", "http://localhost:8080")).rstrip("/")
SKIP_EOD = "--skip-eod" in sys.argv
# Each run looks like a different client address, so repeated runs don't trip the per-IP login
# limit. Only honoured when the gateway runs with ALLOW_SIM_HEADERS=true (local stacks and the demo).
RUN_IP = f"10.{uuid.uuid4().int % 250 + 1}.{uuid.uuid4().int % 250 + 1}.{uuid.uuid4().int % 250 + 1}"
G = httpx.Client(base_url=BASE, timeout=30, headers={"X-Sim-Ip": RUN_IP})
RS = 100
PASSWORD = "correct-horse-battery-1"
PAY_PIN, APP_PIN = "583921", "4826"          # every test customer sets these two
failures = 0


def check(label: str, ok: bool, detail="") -> bool:
    global failures
    failures += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n        -> {str(detail)[:300]}"))
    return ok


def wait_for(fn, timeout=15.0):
    """Events travel through the outbox and Kafka, so some effects arrive a moment later."""
    deadline = time.monotonic() + timeout
    while True:
        value = fn()
        if value or time.monotonic() > deadline:
            return value
        time.sleep(0.5)


def staff(staff_id: str, password: str) -> dict:
    r = G.post("/api/staff/login", json={"staff_id": staff_id, "password": password})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def login(email: str, device: str) -> dict:
    """First login on a device. A device the bank hasn't seen before answers 202 instead:
    see login_new_device."""
    r = G.post("/api/auth/login", json={"identifier": email, "password": PASSWORD,
                                        "device_fingerprint": device, "device_label": device})
    r.raise_for_status()
    return r.json()


def latest_code(headers: dict, after: float = 0) -> str | None:
    """The newest 6-digit code in the customer's inbox (read from a device already signed in)."""
    def find():
        notes = [n for n in G.get("/api/notifications", headers=headers).json()
                 if n.get("type") == "OTP" and n.get("created_at", 0) >= after]
        notes.sort(key=lambda n: n.get("created_at", 0))
        m = notes and re.search(r"\b(\d{6})\b", notes[-1]["body"])
        return m and m.group(1)
    return wait_for(find, 5)


def bearer(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def set_pins(headers: dict) -> None:
    for kind, pin in (("payment", PAY_PIN), ("app", APP_PIN)):
        G.put(f"/api/auth/pins/{kind}", headers=headers, json={"pin": pin, "password": PASSWORD}).raise_for_status()


def new_customer(name: str, admin: dict, balance: int = 0) -> dict:
    run = uuid.uuid4().int % 10**9
    email = f"{name.split()[0].lower()}.{run}@example.com"
    r = G.post("/api/auth/register", json={
        "full_name": name, "date_of_birth": "2001-05-17", "phone": f"+919{run:09d}", "email": email,
        "password": PASSWORD})
    r.raise_for_status()
    customer_id = r.json()["customer_id"]
    G.post(f"/api/staff/kyc/{customer_id}/verify", headers=admin).raise_for_status()
    tokens = login(email, f"{name}-phone")
    headers = bearer(tokens)
    set_pins(headers)
    account = G.post("/api/accounts", headers=headers, json={"product_type": "SAVINGS"}).json()
    if balance:
        G.post("/api/staff/cash-deposits", headers={**admin, "Idempotency-Key": str(uuid.uuid4())},
               json={"account_number": account["account_number"], "amount": balance}).raise_for_status()
    return {"id": customer_id, "email": email, "H": headers, "account_id": account["account_id"],
            "number": account["account_number"], "refresh": tokens["refresh_token"]}


def pay(headers: dict, from_account: str, payee: str, amount: int, key: str | None = None,
        channel: str = "TRANSFER", pin: str = PAY_PIN) -> httpx.Response:
    return G.post("/api/payments", headers={**headers, "Idempotency-Key": key or str(uuid.uuid4()), "X-Pin": pin},
                  json={"from_account_id": from_account, "payee": payee, "amount": amount, "channel": channel})


def balance(c: dict) -> int:
    return G.get(f"/api/accounts/{c['account_id']}/balance", headers=c["H"]).json()["ledger_balance"]


def otp_for(c: dict, txn_id: str) -> str | None:
    def find():
        for n in G.get("/api/notifications", headers=c["H"]).json():
            if n.get("type") == "OTP" and txn_id in str(n.get("data")):
                m = re.search(r"\b(\d{6})\b", n["body"])
                return m and m.group(1)
    return wait_for(find, 5)


print(f"Unbeatable Bank smoke test against {BASE}\n")
ADMIN = staff("admin", os.getenv("ADMIN_PASSWORD", "admin-dev-pass"))
ANALYST1 = staff("analyst1", os.getenv("ANALYST_PASSWORD", "analyst-dev-pass"))
ANALYST2 = staff("analyst2", os.getenv("ANALYST2_PASSWORD") or os.getenv("ANALYST_PASSWORD", "analyst-dev-pass"))

# ---------------------------------------------------------------------------------------------
print("1. Sign-up, KYC, accounts (Identity -> Account -> Ledger)")
riya = new_customer("Riya Sharma", ADMIN, balance=300_000 * RS)
arjun = new_customer("Arjun Mehta", ADMIN)
me = G.get("/api/me", headers=riya["H"]).json()
check("customer profile is KYC verified", me.get("kyc_status") == "VERIFIED", me)
check("teller cash deposit shows in the balance", balance(riya) == 300_000 * RS, balance(riya))
check("no token -> 401", G.get("/api/me").status_code == 401)
check("another customer's account looks like it doesn't exist (404)",
      G.get(f"/api/accounts/{riya['account_id']}/balance", headers=arjun["H"]).status_code == 404)
check("customers cannot call staff APIs (403)", G.get("/api/staff/cases", headers=riya["H"]).status_code == 403)
alias = f"arjun{uuid.uuid4().int % 10**6}"
check("payment alias created",
      G.post(f"/api/accounts/{arjun['account_id']}/alias", headers=arjun["H"], json={"alias": alias}).status_code == 201)

# ---------------------------------------------------------------------------------------------
print("1b. KYC documents (Identity -> Cloud Storage; staff review -> Account)")
PNG_DOC = b"\x89PNG\r\n\x1a\n" + uuid.uuid4().bytes * 20
PDF_DOC = b"%PDF-1.4\n" + uuid.uuid4().bytes * 20
run = uuid.uuid4().int % 10**9
kyc_email = f"kyc.{run}@example.com"
reg = G.post("/api/auth/register", json={"full_name": "Kiran Rao", "date_of_birth": "1995-02-11",
                                         "phone": f"+918{run:09d}", "email": kyc_email, "password": PASSWORD}).json()
kiran = bearer(login(kyc_email, "kiran-phone"))
kiran_account = G.post("/api/accounts", headers=kiran, json={"product_type": "SAVINGS"}).json()
check("account opened before KYC waits as PENDING_KYC", kiran_account.get("status") == "PENDING_KYC", kiran_account)
bad = G.post("/api/kyc/documents", params={"doc_type": "PAN"}, headers={**kiran, "Content-Type": "image/png"},
             content=b"<script>not a picture</script>")
check("a file that is not a picture or PDF is refused (422)", bad.status_code == 422, bad.text)
pan = G.post("/api/kyc/documents", params={"doc_type": "PAN"}, headers={**kiran, "Content-Type": "image/png"}, content=PNG_DOC)
addr = G.post("/api/kyc/documents", params={"doc_type": "ADDRESS_PROOF"}, headers={**kiran, "Content-Type": "application/pdf"},
              content=PDF_DOC)
check("PAN and address proof uploaded", pan.status_code == 201 and addr.status_code == 201, pan.text + addr.text)
pan_id, addr_id = pan.json().get("document_id"), addr.json().get("document_id")
mine = G.get(f"/api/kyc/documents/{pan_id}/content", headers=kiran)
check("owner downloads exactly what was uploaded", mine.status_code == 200 and mine.content == PNG_DOC, mine.status_code)
check("another customer cannot (404)", G.get(f"/api/kyc/documents/{pan_id}/content", headers=riya["H"]).status_code == 404)
check("fraud analysts cannot open ID documents (403)",
      G.get(f"/api/staff/kyc-documents/{pan_id}/content", headers=ANALYST1).status_code == 403)
seen = G.get(f"/api/staff/kyc-documents/{pan_id}/content", headers=ADMIN)
check("a KYC reviewer can, and the file is served with sandbox headers",
      seen.content == PNG_DOC and seen.headers.get("content-security-policy", "").startswith("sandbox"), seen.headers)
rejected = G.post(f"/api/staff/kyc-documents/{pan_id}/reject", headers=ADMIN, json={"reason": "The number is not readable"})
check("reviewer rejects the PAN; the file is deleted (410)", rejected.json().get("status") == "REJECTED" and
      G.get(f"/api/kyc/documents/{pan_id}/content", headers=kiran).status_code == 410, rejected.text)
pan2 = G.post("/api/kyc/documents", params={"doc_type": "PAN"}, headers={**kiran, "Content-Type": "image/png"},
              content=PNG_DOC + b"clearer").json()
G.post(f"/api/staff/kyc-documents/{addr_id}/approve", headers=ADMIN)
done = G.post(f"/api/staff/kyc-documents/{pan2.get('document_id')}/approve", headers=ADMIN).json()
check("both documents approved: KYC verified", done.get("kyc_verified") is True, done)
check("the waiting account is now ACTIVE", G.get(f"/api/accounts/{kiran_account['account_id']}", headers=kiran).json()
      .get("status") == "ACTIVE" and G.get("/api/me", headers=kiran).json().get("kyc_status") == "VERIFIED")
trail = [e["event_type"] for e in G.get(f"/api/staff/customers/{reg['customer_id']}/kyc-events", headers=ADMIN).json()]
check(f"audit trail: {', '.join(trail)}",
      trail == ["UPLOADED", "UPLOADED", "VIEWED", "REJECTED", "UPLOADED", "APPROVED", "APPROVED", "KYC_VERIFIED"], trail)

# ---------------------------------------------------------------------------------------------
print("1c. PINs and sessions (Gateway checks every request with Identity)")
r = pay(riya["H"], riya["account_id"], arjun["number"], 100 * RS, pin="")
check("a payment without the payment PIN is refused (428 PIN_REQUIRED)",
      r.status_code == 428 and r.json().get("error") == "PIN_REQUIRED", r.text)
r = pay(riya["H"], riya["account_id"], arjun["number"], 100 * RS, pin=APP_PIN + "00")
check("a wrong payment PIN: 403 with tries left, nothing paid",
      r.status_code == 403 and r.json().get("details", {}).get("attempts_left") == 2, r.text)
r = G.put("/api/auth/pins/app", headers=riya["H"], json={"pin": PAY_PIN[:4], "current_pin": APP_PIN})
check("the app PIN can't be the start of the payment PIN", r.status_code == 400, r.text)
r = G.post("/api/deposits/fd", headers={**riya["H"], "Idempotency-Key": str(uuid.uuid4()), "X-Pin": PAY_PIN},
           json={"funding_account_id": riya["account_id"], "amount": 1_000 * RS, "tenure_months": 12})
check("a deposit needs the app PIN, not the payment PIN", r.status_code == 403 and
      r.json().get("details", {}).get("kind") == "APP", r.text)
G.post("/api/auth/lock", headers=riya["H"])
locked = G.get("/api/me", headers=riya["H"])
check("a locked session is refused (423 SESSION_LOCKED)", locked.status_code == 423 and
      locked.json().get("error") == "SESSION_LOCKED", locked.text)
wrong = G.post("/api/auth/unlock", headers=riya["H"], json={"pin": "9182"})
right = G.post("/api/auth/unlock", headers=riya["H"], json={"pin": APP_PIN})
check("unlocked with the app PIN (a wrong one is refused)", wrong.status_code == 403 and right.status_code == 200
      and G.get("/api/me", headers=riya["H"]).status_code == 200, wrong.text + right.text)
tokens = G.post("/api/auth/refresh", json={"refresh_token": riya["refresh"]}).json()
check("refresh gives a new refresh token", tokens.get("refresh_token") not in (None, riya["refresh"]), tokens)
riya["H"], riya["refresh"] = bearer(tokens), tokens.get("refresh_token")
# A separate customer for the destructive checks: a replayed token raises a risk signal
# that would colour Riya's payments in the sections below.
sam = new_customer("Sam Iyer", ADMIN)
spare = login(sam["email"], "Sam Iyer-phone")
replay = G.post("/api/auth/refresh", json={"refresh_token": spare["refresh_token"]}).json()
reused = G.post("/api/auth/refresh", json={"refresh_token": spare["refresh_token"]})
check("an old refresh token used again ends that session for everyone holding it",
      reused.status_code == 401 and wait_for(lambda: G.get("/api/me", headers=bearer(replay)).status_code == 401, 8),
      reused.text)
other = login(sam["email"], "Sam Iyer-phone")
G.post("/api/auth/logout", headers=bearer(other))
check("logging out stops the access token at once (not 15 minutes later)",
      wait_for(lambda: G.get("/api/me", headers=bearer(other)).status_code == 401, 8))
check("other sessions are untouched", G.get("/api/me", headers=sam["H"]).status_code == 200)

# ---------------------------------------------------------------------------------------------
print("2. Payments (Gateway -> Intake -REST-> Account, -gRPC-> Rate limit, Risk, Ledger)")
key = str(uuid.uuid4())
r = pay(riya["H"], riya["account_id"], arjun["number"], 2_500 * RS, key=key)
check("Rs 2,500 to Arjun: COMPLETED", r.status_code == 201 and r.json()["status"] == "COMPLETED", r.text)
check("payee name is masked", r.json().get("to_display_name") == "Arjun M****", r.json().get("to_display_name"))
again = pay(riya["H"], riya["account_id"], arjun["number"], 2_500 * RS, key=key)
check("same Idempotency-Key again: replayed, not charged twice",
      again.status_code == 200 and again.json()["txn_id"] == r.json()["txn_id"] and balance(riya) == 297_500 * RS,
      again.text)
r = pay(riya["H"], riya["account_id"], f"{alias}@unbeatable", 500 * RS, channel="QR_PAYMENT")
check("Rs 500 scan-to-pay by alias: COMPLETED", r.status_code == 201, r.text)
typo = arjun["number"][:-1] + str((int(arjun["number"][-1]) + 1) % 10)
r = pay(riya["H"], riya["account_id"], typo, 100 * RS)
check("mistyped account number: INVALID_PAYEE", r.json().get("decline_code") == "INVALID_PAYEE", r.text)
r = pay(riya["H"], riya["account_id"], arjun["number"], 15_000 * RS)
check("Rs 15,000 to a payee added today: NEW_PAYEE_LIMIT_EXCEEDED",
      r.json().get("decline_code") == "NEW_PAYEE_LIMIT_EXCEEDED", r.text)
r = pay(arjun["H"], riya["account_id"], arjun["number"], 100 * RS)
check("paying from someone else's account is refused", r.status_code in (403, 404, 422), r.text)

# ---------------------------------------------------------------------------------------------
print("3. Rate limiting (Rate Limit Coordinator on DynaStore)")
bot = new_customer("Bot Victim", ADMIN, balance=1_000 * RS)
outcomes = [pay(bot["H"], bot["account_id"], arjun["number"], 1 * RS).json() for _ in range(16)]
codes = [o.get("decline_code") for o in outcomes]
completed = sum(o["status"] == "COMPLETED" for o in outcomes)
check(f"burst of 16 payments: some refused as RATE_LIMITED ({codes.count('RATE_LIMITED')} refused, "
      f"{completed} completed)", "RATE_LIMITED" in codes and completed <= 10, codes)
limited = next((o for o in outcomes if o.get("decline_code") == "RATE_LIMITED"), {})
check("customer is told why", bool(limited.get("decline_message")), limited)

# ---------------------------------------------------------------------------------------------
print("4. Risk: new device -> OTP challenge (Risk Detection, Identity, Notification)")
asked = time.time() - 1
challenge = G.post("/api/auth/login", json={"identifier": riya["email"], "password": PASSWORD,
                                            "device_fingerprint": "unknown-laptop", "device_label": "unknown-laptop"})
check("password alone is not enough on a new device (202, code sent)",
      challenge.status_code == 202 and "access_token" not in challenge.json(), challenge.text)
device_code = latest_code(riya["H"], after=asked)
check("the code reached the inbox of Riya's other device", bool(device_code))
second = G.post("/api/auth/login/verify", json={"challenge_id": challenge.json().get("challenge_id"),
                                                "code": device_code or ""}).json()
H2 = bearer(second) if "access_token" in second else {}
check("with the code, the new device signs in, flagged as new", second.get("new_device") is True, second)
r = pay(H2, riya["account_id"], arjun["number"], 7_000 * RS)
txn = r.json()
check(f"Rs 7,000 from the new device: CHALLENGE (score {txn.get('risk_score')}, {txn.get('risk_reasons')})",
      r.status_code == 202 and txn["status"] == "CHALLENGE", r.text)
wrong = G.post(f"/api/payments/{txn['txn_id']}/confirm", headers=H2, json={"otp": "000000"})
check("wrong OTP is rejected", wrong.status_code >= 400 or wrong.json().get("status") == "CHALLENGE", wrong.text)
code = otp_for(riya, txn["txn_id"])
check("OTP arrived in the customer's inbox", bool(code))
done = G.post(f"/api/payments/{txn['txn_id']}/confirm", headers=H2, json={"otp": code or ""})
check("correct OTP completes the payment", done.json().get("status") == "COMPLETED", done.text)

# ---------------------------------------------------------------------------------------------
print("5. Risk: account-takeover pattern -> HOLD -> analyst review (Fraud Alert, cases)")
before = balance(riya)
r = pay(H2, riya["account_id"], arjun["number"], 10_000 * RS)
held = r.json()
check(f"Rs 10,000, new device + new payee: HELD (score {held.get('risk_score')})",
      r.status_code == 202 and held["status"] == "HELD", r.text)
b = G.get(f"/api/accounts/{riya['account_id']}/balance", headers=riya["H"]).json()
check("held money is reserved: still in the balance, not spendable",
      b["ledger_balance"] == before and b["available_balance"] == before - 10_000 * RS, b)
case = wait_for(lambda: next((c for c in G.get("/api/staff/cases", headers=ANALYST1).json()
                              if c.get("txn_id") == held["txn_id"]), None))
check("a fraud case was opened automatically", bool(case), "no case found")
if case:
    resolved = G.post(f"/api/staff/cases/{case['case_id']}/resolve", headers=ANALYST1,
                      json={"resolution": "FALSE_POSITIVE", "note": "customer confirmed by phone"})
    check("analyst marks it legitimate", resolved.json().get("status") == "RESOLVED", resolved.text)
    after = G.get(f"/api/payments/{held['txn_id']}", headers=H2).json()
    check("held payment is released and completes", after["status"] == "COMPLETED" and
          balance(riya) == before - 10_000 * RS, after)

# ---------------------------------------------------------------------------------------------
print("6. 'This wasn't me' -> freeze -> two-person unfreeze (maker-checker)")
r = pay(H2, riya["account_id"], arjun["number"], 10_000 * RS)
held2 = r.json()
check("another suspicious payment is HELD", held2.get("status") == "HELD", r.text)
denied = G.post(f"/api/payments/{held2['txn_id']}/deny", headers=riya["H"]).json()
check("customer denies it: payment cancelled", denied.get("decline_code") == "CUSTOMER_DENIED", denied)
acct = G.get(f"/api/accounts/{riya['account_id']}", headers=riya["H"]).json()
check("account is frozen", acct.get("status") == "FROZEN", acct.get("status"))
r = pay(riya["H"], riya["account_id"], arjun["number"], 100 * RS)
check("frozen account cannot pay", r.json().get("status") == "DECLINED", r.text)
alert = wait_for(lambda: next((a for a in G.get("/api/staff/alerts", headers=ANALYST1).json()
                               if a.get("type") == "ACCOUNT_TAKEOVER" and a.get("account_id") == riya["account_id"]),
                              None))
check("fraud alert raised for the analysts", bool(alert), "no alert")
approval = G.post("/api/staff/approvals", headers=ANALYST1,
                  json={"target_id": riya["account_id"], "reason": "identity verified at the branch"}).json()
own = G.post(f"/api/staff/approvals/{approval.get('approval_id')}/approve", headers=ANALYST1)
check("the analyst who asked cannot approve their own request", own.status_code == 403, own.text)
other = G.post(f"/api/staff/approvals/{approval.get('approval_id')}/approve", headers=ANALYST2)
check("a second analyst approves", other.json().get("status") == "APPROVED", other.text)
acct = G.get(f"/api/accounts/{riya['account_id']}", headers=riya["H"]).json()
check("account is active again", acct.get("status") == "ACTIVE", acct.get("status"))
audit = G.get(f"/api/staff/accounts/{riya['account_id']}/audit", headers=ADMIN).json()
check("account history hash chain is intact", audit.get("chain_valid") is True, audit)

# ---------------------------------------------------------------------------------------------
print("7. DynaStore: a node fails, payments continue")
status = G.get("/api/staff/ops/dynastore", headers=ADMIN).json()
nodes = status.get("nodes", [])
already_down = [n["node_id"] for n in nodes if not n["alive"]]
check(f"ring has {len(nodes)} nodes, N={status.get('n')} R={status.get('r')} W={status.get('w')}",
      len(nodes) >= 3 and len(already_down) <= 1, status)
carol = new_customer("Carol Dsouza", ADMIN, balance=5_000 * RS)
if already_down:
    # Someone stopped a node by hand (docker compose stop dyna-1). Switching off a second
    # one would leave 1 of 3 and no write quorum, so test against the failure that is there.
    print(f"  note  {', '.join(already_down)} is already down: testing payments against that failure")
    for i in range(3):
        r = pay(carol["H"], carol["account_id"], arjun["number"], 300 * RS)
        check(f"payment {i + 1} of 3 completes with {already_down[0]} down", r.json().get("status") == "COMPLETED", r.text)
else:
    down = G.post("/api/staff/ops/dynastore/faults", headers=ADMIN, json={"blocked_peers": [], "down": True})
    check("one node switched off (fault injection)", down.status_code == 200 and down.json().get("simulated_down"), down.text)
    r = pay(carol["H"], carol["account_id"], arjun["number"], 300 * RS)
    check("payment still completes with a DynaStore node down", r.json().get("status") == "COMPLETED", r.text)
    G.post("/api/staff/ops/dynastore/faults", headers=ADMIN, json={"blocked_peers": [], "down": False})
    healed = wait_for(lambda: all(n["alive"] for n in G.get("/api/staff/ops/dynastore", headers=ADMIN).json()["nodes"]))
    check("node is back and the ring is healthy", bool(healed))

# ---------------------------------------------------------------------------------------------
print("8. Deposits and end-of-day (Deposits + EOD engine -> Ledger)")
dave = new_customer("Dave Kapoor", ADMIN, balance=200_000 * RS)
quote = G.get("/api/deposits/quote", params={"product": "FD", "amount": 100_000 * RS, "tenure_months": 3}).json()
check(f"FD quote: Rs 1,00,000 for 3 months -> Rs {quote.get('maturity_amount', 0) / 100:,.2f}",
      quote.get("maturity_amount", 0) > 100_000 * RS, quote)
fd_key = str(uuid.uuid4())
body = {"funding_account_id": dave["account_id"], "amount": 100_000 * RS, "tenure_months": 3}
DAVE_APP = {**dave["H"], "X-Pin": APP_PIN}
fd = G.post("/api/deposits/fd", headers={**DAVE_APP, "Idempotency-Key": fd_key}, json=body)
fd_again = G.post("/api/deposits/fd", headers={**DAVE_APP, "Idempotency-Key": fd_key}, json=body)
check("FD booked; retry with the same key books it once",
      fd.status_code in (200, 201) and fd_again.json().get("deposit_id") == fd.json().get("deposit_id")
      and balance(dave) == 100_000 * RS, fd.text)
nominee = G.post(f"/api/accounts/{fd.json().get('deposit_id')}/nominees", headers=DAVE_APP,
                 json={"full_name": "Meera Kapoor", "relation": "SPOUSE", "share_pct": 100})
check("a nominee named for the FD (app PIN)", nominee.status_code == 201, nominee.text)
rd = G.post("/api/deposits/rd", headers={**DAVE_APP, "Idempotency-Key": str(uuid.uuid4())},
            json={"funding_account_id": dave["account_id"], "amount": 2_000 * RS, "tenure_months": 6})
check("RD opened with the first installment paid", rd.status_code in (200, 201), rd.text)
if not SKIP_EOD:
    ff = G.post("/api/staff/ops/eod/fast-forward", headers=ADMIN, json={"days": 95}, timeout=300)
    check("bank calendar fast-forwarded 95 days", ff.status_code == 200, ff.text)
    deposit = G.get(f"/api/deposits/{fd.json().get('deposit_id')}", headers=dave["H"]).json()
    check(f"FD matured and paid out (status {deposit.get('status')})", deposit.get("status") == "MATURED", deposit)
    entries = G.get(f"/api/accounts/{dave['account_id']}/transactions", headers=dave["H"]).json()
    kinds = {e["txn_type"] for e in entries}
    check("savings account received the FD payout and RD installments were collected",
          "FD_MATURITY" in kinds and "RD_INSTALLMENT" in kinds, kinds)
    check("EOD rerun is safe: balance above Rs 1,00,000 principal + interest - installments",
          balance(dave) > 190_000 * RS, balance(dave))

# ---------------------------------------------------------------------------------------------
print("8b. Statements (Deposits -> Ledger -> Cloud Storage)")
from datetime import date as _date, timedelta as _timedelta   # noqa: E402

bank_today = _date.fromisoformat(G.get("/api/staff/ops/clock", headers=ADMIN).json()["business_date"])
stm = f"/api/accounts/{riya['account_id']}/statements"
listing = G.get(stm, headers=riya["H"])
check("statement months listed for the account", listing.status_code == 200 and "periods" in listing.json(), listing.text)
check("someone else's statements look like they don't exist (404)", G.get(stm, headers=arjun["H"]).status_code == 404)
interim = G.get(f"{stm}/interim", headers=riya["H"], timeout=60,
                params={"date_from": (bank_today - _timedelta(days=200)).isoformat(), "date_to": bank_today.isoformat()})
check("interim statement for a date range is a PDF", interim.status_code == 200 and interim.content.startswith(b"%PDF-")
      and interim.headers.get("content-type") == "application/pdf", interim.text[:200])
unregistered = G.post("/api/statements/verify", content=interim.content)
check("an interim statement is not a registered one", unregistered.json().get("genuine") is False, unregistered.text)
periods = listing.json().get("periods", []) if listing.status_code == 200 else []
if periods:
    period = periods[-1]["period"]                      # the month the account was opened in
    pdf = G.get(f"{stm}/{period}", headers=riya["H"], timeout=60)
    again = G.get(f"{stm}/{period}", headers=riya["H"], timeout=60)
    check(f"monthly statement {period} issued; asking again returns the identical file",
          pdf.status_code == 200 and pdf.content.startswith(b"%PDF-") and again.content == pdf.content, pdf.text[:200])
    verdict = G.get(f"{stm}/{period}/verify", headers=riya["H"]).json()
    check(f"it matches the ledger ({verdict.get('entry_count')} entries) and the stored file is intact",
          verdict.get("valid") is True, verdict)
    genuine = G.post("/api/statements/verify", content=pdf.content).json()         # no login: anyone holding the file
    check("anyone holding the PDF can confirm the bank issued it; the account number is masked",
          genuine.get("genuine") is True and riya["number"] not in str(genuine)
          and genuine.get("account_number", "").endswith(riya["number"][-4:]), genuine)
    middle = len(pdf.content) // 2
    forged = pdf.content[:middle] + bytes([pdf.content[middle] ^ 1]) + pdf.content[middle + 1:]
    check("the same PDF with one byte changed is not recognised",
          G.post("/api/statements/verify", content=forged).json().get("genuine") is False)
else:
    print("  note  no completed month yet for this account (the bank date is still in its opening month);")
    print("        run without --skip-eod, which moves the calendar forward, to test monthly statements")

# ---------------------------------------------------------------------------------------------
print("8c. Payees, limits, contact details, Block everything")
erin = new_customer("Erin Thomas", ADMIN, balance=20_000 * RS)
EPAY = {**erin["H"], "X-Pin": PAY_PIN}
saved = G.post("/api/me/beneficiaries", headers=EPAY, json={"payee_account_number": arjun["number"], "nickname": "Arjun"})
check("payee saved with the payment PIN, in its 24-hour cooling period",
      saved.status_code == 201 and G.get("/api/me/beneficiaries", headers=erin["H"]).json()[0].get("in_cooling_period"),
      saved.text)
check("payee removed", G.delete(f"/api/me/beneficiaries/{saved.json().get('beneficiary_id')}", headers=erin["H"]).status_code == 204)
acct = G.get(f"/api/accounts/{erin['account_id']}", headers=erin["H"]).json()
raised = G.patch(f"/api/accounts/{erin['account_id']}/limits", headers=EPAY,
                 json={"daily_limit": acct["daily_limit"] * 2, "per_txn_limit": acct["per_txn_limit"] // 2}).json()
check("a raised limit waits 24 hours; a lowered one applies at once",
      raised.get("daily_limit") == acct["daily_limit"] and raised.get("pending_daily_limit") == acct["daily_limit"] * 2
      and raised.get("per_txn_limit") == acct["per_txn_limit"] // 2, raised)
asked = time.time() - 1
new_email = f"erin.new.{uuid.uuid4().int % 10**9}@example.com"
started = G.post("/api/auth/contact", headers=erin["H"], json={"field": "email", "value": new_email, "password": PASSWORD})
code = latest_code(erin["H"], after=asked)
confirmed = G.post("/api/auth/contact/confirm", headers=erin["H"], json={"code": code or ""})
check("email changed with a code sent to the old one", started.status_code == 202 and confirmed.status_code == 200
      and G.get("/api/me", headers=erin["H"]).json().get("email") == new_email, started.text + confirmed.text)
r = pay(erin["H"], erin["account_id"], arjun["number"], 6_000 * RS)
check(f"paying a new payee right after: weighed, not blocked ({r.json().get('status')}, {r.json().get('risk_reasons')})",
      r.json().get("status") in ("COMPLETED", "CHALLENGE", "HELD") and "CONTACT_CHANGED" in (r.json().get("risk_reasons") or []),
      r.text)
blocked = G.post("/api/me/lockdown", headers=erin["H"])
check("Block everything: accounts frozen, case opened", blocked.status_code == 200 and
      blocked.json().get("accounts_frozen") and blocked.json().get("case_number"), blocked.text)
check("... and every session ended", wait_for(lambda: G.get("/api/me", headers=erin["H"]).status_code == 401, 8))
check("the frozen account shows to the bank", G.get(f"/api/staff/accounts/{erin['account_id']}", headers=ADMIN)
      .json().get("status") == "FROZEN")

# ---------------------------------------------------------------------------------------------
print("9. Books and operations")
recon = G.get("/api/staff/ops/reconciliation", headers=ADMIN).json()
check("ledger reconciliation: books balanced", recon.get("balanced") is True, recon)
inbox = wait_for(lambda: [n for n in G.get("/api/notifications", headers=riya["H"]).json()
                          if n.get("type") != "OTP"])
check(f"customer inbox has security and payment notifications ({len(inbox or [])})", bool(inbox))
check("rate-limit policies and risk rules are visible to staff",
      G.get("/api/staff/ops/rate-limits", headers=ADMIN).status_code == 200
      and G.get("/api/staff/ops/risk-rules", headers=ADMIN).status_code == 200)

print("\nALL CHECKS PASSED" if not failures else f"\n{failures} CHECK(S) FAILED")
sys.exit(1 if failures else 0)
