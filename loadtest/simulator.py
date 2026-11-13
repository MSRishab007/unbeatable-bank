"""Fraud simulator: plays attacker and customer personas against the bank and
shows which layer of defence catches each one. Made for the demo.

    pip install httpx
    python loadtest/simulator.py                      # local stack on :8080
    python loadtest/simulator.py http://<public-ip>   # GKE deployment
    python loadtest/simulator.py --only takeover,bot  # pick personas
    python loadtest/simulator.py --with-flink         # also the patterns only the Flink job can see

Needs ALLOW_SIM_HEADERS=true on the gateway (set in dev and in the demo
deployment) so a persona can pretend to come from another IP or city.
Staff passwords: ADMIN_PASSWORD / ANALYST_PASSWORD (default: the dev ones).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import uuid

import httpx

RS = 100
PASSWORD = "simulator-pass-1"
PAY_PIN, APP_PIN = "583921", "4826"
MUMBAI, LONDON = "19.0760,72.8777", "51.5074,-0.1278"

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("base", nargs="?", default=os.getenv("GATEWAY_URL", "http://localhost:8080"))
parser.add_argument("--only", help="comma-separated persona names")
parser.add_argument("--with-flink", action="store_true", help="include patterns detected by the Flink job")
args = parser.parse_args()
G = httpx.Client(base_url=args.base.rstrip("/"), timeout=30)
results: list[tuple[str, bool, str]] = []


class Person:
    """A customer with one funded savings account."""

    _n = 0

    def __init__(self, name: str, balance: int = 0, geo: str = MUMBAI):
        Person._n += 1
        run = uuid.uuid4().int % 10**9
        self.name, self.geo = name, geo
        self.ip = f"10.{Person._n % 250}.{run % 250}.{run % 199 + 1}"
        self.email = f"{name.lower().replace(' ', '.')}.{run}@example.com"
        r = G.post("/api/auth/register", headers=self.net(), json={
            "full_name": name, "date_of_birth": "1995-03-12", "phone": f"+919{run:09d}",
            "email": self.email, "password": PASSWORD})
        r.raise_for_status()
        self.id = r.json()["customer_id"]
        G.post(f"/api/staff/kyc/{self.id}/verify", headers=ADMIN).raise_for_status()
        self.H = self.login("own-phone")
        for kind, pin in (("payment", PAY_PIN), ("app", APP_PIN)):
            G.put(f"/api/auth/pins/{kind}", headers=self.H, json={"pin": pin, "password": PASSWORD}).raise_for_status()
        account = G.post("/api/accounts", headers=self.H, json={"product_type": "SAVINGS"}).json()
        self.account_id, self.number = account["account_id"], account["account_number"]
        if balance:
            cash(self.number, balance)

    def net(self, geo: str | None = None, ip: str | None = None) -> dict:
        return {"X-Sim-Ip": ip or self.ip, "X-Geo": geo or self.geo}

    def login(self, device: str, geo: str | None = None, ip: str | None = None) -> dict:
        """Signs in; a device the bank hasn't seen needs the code sent to the customer, which
        this reads from the inbox of their own phone (self.H). Returns None if it can't."""
        r = G.post("/api/auth/login", headers=self.net(geo, ip), json={
            "identifier": self.email, "password": PASSWORD, "device_fingerprint": f"{self.id}-{device}",
            "device_label": device})
        r.raise_for_status()
        tokens = r.json()
        if r.status_code == 202:
            code = self.inbox_code("new device")
            tokens = G.post("/api/auth/login/verify", headers=self.net(geo, ip),
                            json={"challenge_id": tokens["challenge_id"], "code": code or ""}).json()
        return {"Authorization": f"Bearer {tokens['access_token']}", **self.net(geo, ip)}

    def inbox_code(self, about: str) -> str | None:
        def find():
            for n in G.get("/api/notifications", headers=self.H).json():
                if n.get("type") == "OTP" and about in n.get("body", ""):
                    m = re.search(r"\b(\d{6})\b", n["body"])
                    return m and m.group(1)
        return wait_for(find, 5)

    def pay(self, payee: str, rupees: float, headers: dict | None = None, account_id: str | None = None) -> dict:
        r = G.post("/api/payments", headers={**(headers or self.H), "Idempotency-Key": str(uuid.uuid4()),
                                             "X-Pin": PAY_PIN},
                   json={"from_account_id": account_id or self.account_id, "payee": payee,
                         "amount": int(rupees * RS)})
        return r.json()

    def open_account(self, **limits) -> tuple[str, str]:
        a = G.post("/api/accounts", headers=self.H, json={"product_type": "SAVINGS", **limits}).json()
        return a["account_id"], a["account_number"]


def staff(staff_id: str, password: str) -> dict:
    r = G.post("/api/staff/login", json={"staff_id": staff_id, "password": password})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def cash(number: str, amount: int) -> None:
    G.post("/api/staff/cash-deposits", headers={**ADMIN, "Idempotency-Key": str(uuid.uuid4())},
           json={"account_number": number, "amount": amount}).raise_for_status()


def outcome(txn: dict) -> str:
    text = txn.get("status", "?")
    if txn.get("decline_code"):
        text += f" ({txn['decline_code']})"
    if txn.get("risk_reasons"):
        text += f" score {txn.get('risk_score')} {txn['risk_reasons']}"
    return text


def report(persona: str, caught: bool, by: str, detail: str) -> None:
    results.append((persona, caught, by))
    print(f"    -> {'CAUGHT' if caught else 'MISSED'} by {by}: {detail}\n")


def wait_for(fn, timeout=20.0):
    deadline = time.monotonic() + timeout
    while True:
        value = fn()
        if value or time.monotonic() > deadline:
            return value
        time.sleep(1)


# ----------------------------------------------------------------------------- personas


def normal():
    """An ordinary customer: a few everyday payments. Nothing should get in the way."""
    riya, shop = Person("Riya Sharma", 50_000 * RS), Person("Corner Shop")
    outcomes = [riya.pay(shop.number, amount) for amount in (120, 450, 80)]
    ok = all(o["status"] == "COMPLETED" for o in outcomes)
    print(f"    3 small payments: {[o['status'] for o in outcomes]}")
    results.append(("normal customer", ok, "nobody (correct)"))
    print(f"    -> {'NOT DISTURBED' if ok else 'WRONGLY BLOCKED'}: honest customers see no friction\n")


def bot():
    """A script hammering the payment API with a stolen session."""
    victim, mule = Person("Bot Victim", 5_000 * RS), Person("Collector")
    outcomes = [victim.pay(mule.number, 1) for _ in range(30)]
    limited = sum(o.get("decline_code") == "RATE_LIMITED" for o in outcomes)
    done = sum(o["status"] == "COMPLETED" for o in outcomes)
    challenged = sum(o["status"] == "CHALLENGE" for o in outcomes)
    report("payment bot", limited > 0 and done <= 10, "Rate Limit Coordinator (+ velocity rule)",
           f"30 rapid payments: {done} went through, {challenged} asked for an OTP, {limited} rate-limited")


def stolen_password():
    """Stolen password only: the thief tries to sign in on their own laptop."""
    victim = Person("Kavya Iyer", 40_000 * RS)
    r = G.post("/api/auth/login", headers=victim.net(LONDON, "185.220.101.7"), json={
        "identifier": victim.email, "password": PASSWORD, "device_fingerprint": "thief-laptop"})
    print(f"    thief signs in from London with the right password: {r.status_code} "
          f"({'code sent to the real customer' if r.status_code == 202 else r.text[:80]})")
    report("stolen password", r.status_code == 202 and "access_token" not in r.json(),
           "Identity (new-device code)", "no session without the code sent to the customer's own devices")


def takeover():
    """Password, SIM and PIN all stolen: the thief gets the new-device code and signs in from
    another country, then tries to drain the account."""
    victim, thief = Person("Asha Verma", 80_000 * RS), Person("Thief Account")
    stolen = victim.login("attacker-laptop", geo=LONDON, ip="185.220.101.7")
    txn = victim.pay(thief.number, 10_000, headers=stolen)
    print(f"    attacker sends Rs 10,000 from a new device in London: {outcome(txn)}")
    held = txn["status"] in ("HELD", "DECLINED")
    if txn["status"] == "HELD":
        denied = G.post(f"/api/payments/{txn['txn_id']}/deny", headers=victim.H).json()
        account = G.get(f"/api/accounts/{victim.account_id}", headers=victim.H).json()
        print(f"    real customer taps 'this wasn't me': {denied.get('decline_code')}, account {account.get('status')}")
    how = ("payment blocked outright; the customer is alerted" if txn["status"] == "DECLINED"
           else "money never left; account frozen; fraud case opened")
    report("account takeover", held, "Risk Detection (new device + impossible travel + new payee)", how)


def probing():
    """Card-testing style: tiny payments to see if the account works, then a big one."""
    victim, thief = Person("Probe Victim", 60_000 * RS), Person("Prober")
    small = [victim.pay(thief.number, 2)["status"] for _ in range(3)]
    time.sleep(1)
    big = victim.pay(thief.number, 9_500)
    print(f"    3 x Rs 2: {small}; then Rs 9,500: {outcome(big)}")
    report("micro-payment probing", big["status"] in ("HELD", "CHALLENGE", "DECLINED"),
           "Risk Detection (MICRO_PROBING)", "the large payment after the probes is stopped")


def structuring():
    """Moving a large sum in pieces just under the Rs 50,000 threshold."""
    launderer = Person("Split Sender")
    # Raising a limit now takes a day, so this account is opened with a high one instead.
    big_id, big_number = launderer.open_account(daily_limit=400_000 * RS)
    cash(big_number, 300_000 * RS)
    outcomes = [launderer.pay(launderer.number, amount, account_id=big_id) for amount in (49_900, 49_500, 49_800, 49_700)]
    for o in outcomes:
        print(f"    Rs {o['amount'] / 100:,.0f}: {outcome(o)}")
    caught = any(o["status"] in ("HELD", "CHALLENGE", "DECLINED") and "STRUCTURING" in (o.get("risk_reasons") or [])
                 for o in outcomes)
    report("structuring", caught, "Risk Detection (STRUCTURING)", "repeated amounts just under the threshold")


def fd_break():
    """Break a fixed deposit early and immediately send the money to a stranger."""
    victim, thief = Person("Deposit Holder", 120_000 * RS), Person("Stranger")
    app = {**victim.H, "X-Pin": APP_PIN}
    fd = G.post("/api/deposits/fd", headers={**app, "Idempotency-Key": str(uuid.uuid4())},
                json={"funding_account_id": victim.account_id, "amount": 100_000 * RS, "tenure_months": 12}).json()
    closed = G.post(f"/api/deposits/{fd.get('deposit_id')}/close", headers={**app, "Idempotency-Key": str(uuid.uuid4())})
    txn = victim.pay(thief.number, 9_000)
    print(f"    FD closed early ({closed.status_code}), then Rs 9,000 to a new payee: {outcome(txn)}")
    report("FD break-and-flee", "FD_BREAK_AND_FLEE" in (txn.get("risk_reasons") or []),
           "Risk Detection (signal from Deposits via DynaStore)", "customer must confirm with an OTP")


def mule_ring():
    """Several victims are tricked into paying the same collection account. No single
    payment looks odd; only the stream analytics job sees the pattern."""
    mule = Person("Mule Account")
    victims = [Person(f"Victim {i}", 20_000 * RS) for i in range(6)]
    statuses = [v.pay(mule.number, 9_000)["status"] for v in victims]
    print(f"    6 different customers each sent Rs 9,000 to one account: {statuses}")
    print("    waiting for the Flink window to close ...")
    # Flink's windows run on event time: a window closes only when a *later* payment shows
    # that its time has passed (the watermark). In a real bank payments never stop; here
    # an ordinary shopper keeps paying every 10 s (well under every velocity rule) so the
    # stream keeps moving after the mule payments.
    shopper, shop = Person("Steady Shopper", 20_000 * RS), Person("Grocery Store")
    last = [0.0]

    def mule_alert():
        if time.monotonic() - last[0] >= 10:
            shopper.pay(shop.number, 50)
            last[0] = time.monotonic()
        return next((a for a in G.get("/api/staff/alerts", headers=ANALYST).json()
                     if a.get("account_id") == mule.account_id), None)

    alert = wait_for(mule_alert, timeout=240)
    report("mule ring", bool(alert), "Audit Analytics (Flink, 10-minute fan-in window) -> Fraud Alert",
           f"alert: {alert.get('title') if alert else 'none within 240 s (is the Flink job running?)'}")


PERSONAS = {"normal": normal, "bot": bot, "stolen-password": stolen_password, "takeover": takeover, "probing": probing,
            "structuring": structuring, "fd-break": fd_break}
FLINK_PERSONAS = {"mule-ring": mule_ring}

if __name__ == "__main__":
    ADMIN = staff("admin", os.getenv("ADMIN_PASSWORD", "admin-dev-pass"))
    ANALYST = staff("analyst1", os.getenv("ANALYST_PASSWORD", "analyst-dev-pass"))
    chosen = {**PERSONAS, **(FLINK_PERSONAS if args.with_flink else {})}
    if args.only:
        every = {**PERSONAS, **FLINK_PERSONAS}
        chosen = {name: every[name] for name in args.only.split(",")}
    print(f"Fraud simulator against {args.base}\n")
    for name, persona in chosen.items():
        print(f"[{name}] {re.sub(r'\\s+', ' ', persona.__doc__).strip()}")
        try:
            persona()
        except Exception as e:  # keep going: one broken persona shouldn't hide the others
            results.append((name, False, f"error: {e}"))
            print(f"    -> ERROR: {e}\n")

    time.sleep(2)
    cases = G.get("/api/staff/cases", headers=ANALYST).json()
    alerts = G.get("/api/staff/alerts", headers=ANALYST).json()
    print("Summary")
    for persona, caught, by in results:
        print(f"  {'OK  ' if caught else 'FAIL'} {persona:24s} {by}")
    print(f"\nFraud team now has {len(cases)} open case(s) and {len(alerts)} alert(s).")
    sys.exit(0 if all(c for _, c, _ in results) else 1)
