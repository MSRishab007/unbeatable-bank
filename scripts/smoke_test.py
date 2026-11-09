"""End-to-end smoke test against running services (docker compose or Codespaces).

    pip install httpx
    python scripts/smoke_test.py

Override URLs with LEDGER_URL, ACCOUNT_URL, INTAKE_URL if needed.
Exits with status 1 if any check fails.
"""

import os
import sys
import uuid

import httpx

LEDGER = httpx.Client(base_url=os.getenv("LEDGER_URL", "http://localhost:8081"), timeout=10)
ACCOUNT = httpx.Client(base_url=os.getenv("ACCOUNT_URL", "http://localhost:8082"), timeout=10)
INTAKE = httpx.Client(base_url=os.getenv("INTAKE_URL", "http://localhost:8083"), timeout=10)
CASH = "00000000-0000-0000-0000-000000000001"
RUN = uuid.uuid4().int % 10**7  # makes phones/emails/aliases unique per run
RS = 100
failures = 0


def check(label, ok, detail=""):
    global failures
    failures += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'' if ok else f'  -> {detail}'}")


def customer(name, n):
    r = ACCOUNT.post("/v1/customers", json={
        "full_name": name, "date_of_birth": "2001-05-17",
        "phone": f"+919{(RUN * 10 + n) % 10**9:09d}",
        "email": f"{name.split()[0].lower()}.{RUN}@example.com"})
    r.raise_for_status()
    c = r.json()
    ACCOUNT.post(f"/internal/v1/customers/{c['customer_id']}/kyc/verify",
                 headers={"X-Actor": "staff:kyc-officer"}).raise_for_status()
    return c


def pay(customer_id, from_account, payee, amount, key=None, channel="TRANSFER"):
    return INTAKE.post("/v1/payments",
                       headers={"X-Actor": f"customer:{customer_id}",
                                "Idempotency-Key": key or str(uuid.uuid4())},
                       json={"from_account_id": from_account, "payee": payee, "amount": amount,
                             "channel": channel})


def balance(account_id):
    return LEDGER.get(f"/v1/accounts/{account_id}/balance").json()["ledger_balance"]


print("Setup")
riya, arjun = customer("Riya Sharma", 1), customer("Arjun Mehta", 2)
riya_acct = ACCOUNT.post("/v1/accounts", json={"product_type": "SAVINGS",
                                               "holder_ids": [riya["customer_id"]]}).json()
arjun_acct = ACCOUNT.post("/v1/accounts", json={"product_type": "SAVINGS",
                                                "holder_ids": [arjun["customer_id"]]}).json()
alias = f"arjun{RUN}@unbeatable"
ACCOUNT.post(f"/v1/accounts/{arjun_acct['account_id']}/alias", json={"alias": alias}).raise_for_status()
LEDGER.post("/v1/journals", headers={"Idempotency-Key": f"smoke-fund-{riya_acct['account_id']}"},
            json={"txn_type": "DEPOSIT", "legs": [
                {"account_id": CASH, "amount": -100_000 * RS},
                {"account_id": riya_acct["account_id"], "amount": 100_000 * RS}]}).raise_for_status()
print(f"  Riya {riya['cif_number']} acct {riya_acct['account_number']}, "
      f"Arjun {arjun['cif_number']} acct {arjun_acct['account_number']} ({alias})")

print("Payments through Transaction Intake (REST) -> Ledger (gRPC)")
key = str(uuid.uuid4())
r = pay(riya["customer_id"], riya_acct["account_id"], arjun_acct["account_number"], 2_500 * RS, key=key)
check("Rs 2,500 to Arjun by account number is COMPLETED (201)",
      r.status_code == 201 and r.json()["status"] == "COMPLETED", r.text)
check("payee name is masked", r.json().get("to_display_name") == "Arjun M****", r.text)

r2 = pay(riya["customer_id"], riya_acct["account_id"], arjun_acct["account_number"], 2_500 * RS, key=key)
check("retry with same Idempotency-Key replays (200), same txn",
      r2.status_code == 200 and r2.json()["txn_id"] == r.json()["txn_id"], r2.text)

r = pay(riya["customer_id"], riya_acct["account_id"], arjun_acct["account_number"], 15_000 * RS)
check("Rs 15,000 to a new payee is DECLINED: NEW_PAYEE_LIMIT_EXCEEDED",
      r.status_code == 422 and r.json()["decline_code"] == "NEW_PAYEE_LIMIT_EXCEEDED", r.text)

r = pay(riya["customer_id"], riya_acct["account_id"], alias, 500 * RS, channel="QR_PAYMENT")
check("Rs 500 scan-to-pay by alias is COMPLETED", r.status_code == 201, r.text)

typo = arjun_acct["account_number"][:-1] + str((int(arjun_acct["account_number"][-1]) + 1) % 10)
r = pay(riya["customer_id"], riya_acct["account_id"], typo, 100 * RS)
check("mistyped account number is DECLINED: INVALID_PAYEE",
      r.json().get("decline_code") == "INVALID_PAYEE", r.text)

r = pay(arjun["customer_id"], riya_acct["account_id"], arjun_acct["account_number"], 100 * RS)
check("Arjun paying from Riya's account is DECLINED: NOT_A_HOLDER",
      r.json().get("decline_code") == "NOT_A_HOLDER", r.text)

r = pay(riya["customer_id"], riya_acct["account_id"], arjun_acct["account_number"], 60_000 * RS)
check("Rs 60,000 is DECLINED: PER_TXN_LIMIT_EXCEEDED",
      r.json().get("decline_code") == "PER_TXN_LIMIT_EXCEEDED", r.text)

ACCOUNT.post(f"/v1/accounts/{riya_acct['account_id']}/freeze", json={"reason": "smoke test"}).raise_for_status()
r = pay(riya["customer_id"], riya_acct["account_id"], arjun_acct["account_number"], 100 * RS)
check("payment from a frozen account is DECLINED: ACCOUNT_FROZEN",
      r.json().get("decline_code") == "ACCOUNT_FROZEN", r.text)
print(f"  customer sees: \"{r.json().get('decline_message')}\"")

print("Money and books")
check("Riya: Rs 1,00,000 - 2,500 - 500 = Rs 97,000", balance(riya_acct["account_id"]) == 97_000 * RS,
      balance(riya_acct["account_id"]))
check("Arjun: Rs 3,000", balance(arjun_acct["account_id"]) == 3_000 * RS, balance(arjun_acct["account_id"]))
history = INTAKE.get("/v1/payments", headers={"X-Actor": f"customer:{riya['customer_id']}"}).json()
check("Riya's history shows 6 payments (2 completed, 4 declined)", len(history) == 6 and
      sorted(t["status"] for t in history).count("COMPLETED") == 2, len(history))
check("books balanced", LEDGER.get("/v1/reconciliation").json()["balanced"])
check("account audit trail intact",
      ACCOUNT.get(f"/internal/v1/accounts/{riya_acct['account_id']}/events/verify").json()["chain_valid"])

print("\nALL CHECKS PASSED" if not failures else f"\n{failures} CHECK(S) FAILED")
sys.exit(1 if failures else 0)
