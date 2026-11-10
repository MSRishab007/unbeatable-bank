"""The payment path with every real component except the Account service:

    Intake -> Rate Limit Coordinator (gRPC) -> DynaStore cluster (3 nodes)
           -> Risk Detection (gRPC)         -> DynaStore cluster
           -> Ledger (gRPC)                 -> Postgres
"""

import dataclasses
import uuid

import pytest

from ubcommon.testing import REPO_ROOT, LocalDynaCluster, load_app

from app import service
from app.clients import GrpcRateLimiter, GrpcRiskClient

RS = 100


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    load_app("ratelimit", "ratelimit_app")
    load_app("risk", "risk_app")
    from ratelimit_app import grpc_server as rl_server
    from ratelimit_app.limiter import Limiter, Policies
    from risk_app import grpc_server as risk_server
    from risk_app.engine import Engine, Rules

    import yaml

    # The shipped rules, minus the one that depends on what time the tests happen to run.
    rules = yaml.safe_load((REPO_ROOT / "services/risk/config/rules.yaml").read_text())
    rules["rules"]["ODD_HOURS"]["enabled"] = False
    rules_path = tmp_path_factory.mktemp("rules") / "rules.yaml"
    rules_path.write_text(yaml.safe_dump(rules))

    store = LocalDynaCluster(tmp_path_factory.mktemp("dyna"))
    limiter = Limiter(store.client(), Policies.load(str(REPO_ROOT / "services/ratelimit/config/policies.yaml")))
    engine = Engine(store.client(), Rules(str(rules_path)))
    servers = [rl_server.start(limiter, 0), risk_server.start(engine, 0)]
    yield {"store": store, "engine": engine,
           "limiter": GrpcRateLimiter(f"localhost:{servers[0].bound_port}"),
           "risk": GrpcRiskClient(f"localhost:{servers[1].bound_port}")}
    for s in servers:
        s.stop(0)
    store.shutdown()


@pytest.fixture
def full(deps, real):
    return dataclasses.replace(deps, limiter=real["limiter"], risk=real["risk"])


@pytest.fixture
def riya(accounts):
    cid = accounts.customer()
    acct, _ = accounts.account([cid], balance=500_000 * RS)
    friend = accounts.customer()
    _, friend_no = accounts.account([friend], name="Arjun Mehta")
    accounts.trust(cid, friend_no)
    return {"cid": cid, "acct": acct, "friend": friend_no, "device": uuid.uuid4().hex}


def pay(conn, deps, riya, payee, rupees, device=None):
    return service.submit_payment(conn, deps, customer_id=riya["cid"], idempotency_key=str(uuid.uuid4()),
                                  from_account_id=riya["acct"], payee=payee, amount=int(rupees * RS),
                                  device_id=device or riya["device"], ip="203.0.113.7")[0]


def known_device(real, riya):
    """The device has been used by this customer for a month."""
    import time
    real["engine"].store.put(f"seen:dev:{riya['cid']}:{riya['device']}", repr(time.time() - 30 * 86400))


def test_burst_meets_step_up_then_the_rate_limiter(conn, full, real, riya):
    """A script hammering one account: allowed, then asked for OTPs, then throttled."""
    known_device(real, riya)
    statuses = [pay(conn, full, riya, riya["friend"], 50) for _ in range(13)]
    codes = [(t["status"], t["decline_code"]) for t in statuses]
    assert codes[:5] == [("COMPLETED", None)] * 5                         # normal use
    assert codes[5:10] == [("CHALLENGE", None)] * 5                       # risk: more than 5 a minute
    assert statuses[5]["risk_reasons"] == ["VELOCITY_1M"]
    assert codes[10:] == [("DECLINED", "RATE_LIMITED")] * 3               # limiter: 10 a minute per account


def test_account_takeover_is_held_with_reasons(conn, full, accounts, balance, riya):
    """Unknown device, never-paid payee, as much as a new payee is allowed to receive."""
    thief = accounts.customer()
    _, thief_no = accounts.account([thief], name="Mallory Mule")
    txn = pay(conn, full, riya, thief_no, 10_000, device=uuid.uuid4().hex)
    assert txn["status"] == "HELD"
    assert {"NEW_DEVICE", "NEW_PAYEE", "ATO_PATTERN"} <= set(txn["risk_reasons"])
    assert balance(riya["acct"], available=True) == 490_000 * RS          # reserved, not gone
    done = service.deny_payment(conn, full, txn["txn_id"], riya["cid"])   # "this wasn't me"
    assert done["decline_code"] == "CUSTOMER_DENIED" and balance(riya["acct"], available=True) == 500_000 * RS


def test_ordinary_payment_passes_every_layer(conn, full, real, balance, riya):
    known_device(real, riya)
    txn = pay(conn, full, riya, riya["friend"], 2_500)
    assert (txn["status"], txn["risk_decision"], txn["risk_score"]) == ("COMPLETED", "ALLOW", 0)
    assert balance(riya["acct"]) == 497_500 * RS


def test_dynastore_node_failure_is_invisible_to_payments(conn, full, real, riya):
    known_device(real, riya)
    real["store"].nodes["node-0"].set_faults([], True)
    try:
        txn = pay(conn, full, riya, riya["friend"], 100)
    finally:
        real["store"].nodes["node-0"].set_faults([], False)
    assert txn["status"] == "COMPLETED" and txn["risk_decision"] == "ALLOW"


def test_dynastore_outage_small_payments_flow_large_ones_are_held(conn, full, real, riya):
    """Fail-open for small amounts, fail-closed for large: the limiter and the risk
    engine both lose their store, and the system degrades instead of stopping."""
    known_device(real, riya)
    real["store"].set_down(True)
    try:
        small = pay(conn, full, riya, riya["friend"], 500)
        large = pay(conn, full, riya, riya["friend"], 30_000)
    finally:
        real["store"].set_down(False)
    assert small["status"] == "COMPLETED"
    assert (large["status"], large["decline_code"]) == ("DECLINED", "RATE_LIMITED")  # limiter fails closed first
