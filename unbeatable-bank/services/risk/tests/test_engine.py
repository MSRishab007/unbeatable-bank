import shutil
import uuid
from datetime import datetime, timedelta, timezone

import grpc
import pytest

from ubcommon.testing import LocalDynaCluster
from ubproto.risk.v1 import risk_pb2 as pb
from ubproto.risk.v1 import risk_pb2_grpc as pb_grpc

from app import grpc_server
from app.engine import Engine, Payment, Rules

IST = timezone(timedelta(hours=5, minutes=30))
NOON = datetime(2026, 11, 10, 12, 0, tzinfo=IST).timestamp()
RS = 100


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    cluster = LocalDynaCluster(tmp_path_factory.mktemp("dyna"))
    yield cluster
    cluster.shutdown()


@pytest.fixture
def engine(store):
    return Engine(store.client(), Rules("config/rules.yaml"))


class Customer:
    """A customer with a device and payee the bank has known for a long time."""

    def __init__(self, engine):
        self.engine = engine
        self.id, self.account, self.device, self.payee = (uuid.uuid4().hex for _ in range(4))
        self.know_device(self.device)
        self.know_payee(self.payee)
        self.t = NOON

    def know_device(self, device):
        self.engine.store.put(f"seen:dev:{self.id}:{device}", repr(NOON - 30 * 86400))

    def know_payee(self, payee):
        self.engine.store.put(f"seen:payee:{self.id}:{payee}", repr(NOON - 30 * 86400))

    def pay(self, rupees, *, payee=None, device=None, trusted=False, idle_days=0, at=None, gap=120):
        """Each payment is `gap` seconds after the previous one unless `at` is given."""
        self.t = at if at is not None else self.t + gap
        return self.engine.evaluate(Payment(
            txn_id=uuid.uuid4().hex, customer_id=self.id, from_account_id=self.account,
            to_account_id=payee or self.payee, amount=int(rupees * RS), device_id=device or self.device,
            payee_trusted=trusted, account_idle_days=idle_days, event_time_ms=int(self.t * 1000)))


# ------------------------------------------------------------------ everyday payments


def test_ordinary_payment_is_allowed(engine):
    result = Customer(engine).pay(2_500)
    assert (result.decision, result.score, result.reasons) == ("ALLOW", 0, [])


def test_small_payment_to_a_new_payee_is_fine(engine):
    result = Customer(engine).pay(800, payee=uuid.uuid4().hex)
    assert result.decision == "ALLOW"


# ------------------------------------------------------------------ fraud patterns


def test_velocity_burst_triggers_step_up(engine):
    c = Customer(engine)
    decisions = [c.pay(100, gap=5).decision for _ in range(7)]
    assert decisions[:5] == ["ALLOW"] * 5
    assert decisions[5] == "CHALLENGE"
    assert "VELOCITY_1M" in c.pay(100, gap=5).reasons


def test_account_takeover_pattern_is_held(engine):
    """New device + new payee + large amount: the classic stolen-credentials drain."""
    c = Customer(engine)
    result = c.pay(25_000, device=uuid.uuid4().hex, payee=uuid.uuid4().hex)
    assert result.decision == "HOLD"
    assert {"NEW_DEVICE", "NEW_PAYEE", "ATO_PATTERN"} <= set(result.reasons)


def test_new_device_stays_new_for_a_day(engine):
    c, device, payee = Customer(engine), uuid.uuid4().hex, uuid.uuid4().hex
    assert c.pay(25_000, device=device, payee=payee).decision == "HOLD"
    assert c.pay(25_000, device=device, payee=payee, gap=3600).decision == "HOLD"        # retry: still new
    later = c.pay(25_000, device=device, payee=payee, gap=2 * 86400)                     # two days on
    assert "NEW_DEVICE" not in later.reasons and "ATO_PATTERN" not in later.reasons


def test_micro_probing_then_large_payment_is_held(engine):
    """Fraudsters test a stolen account with tiny payments before the real one."""
    c = Customer(engine)
    for _ in range(3):
        assert c.pay(5, gap=40).decision == "ALLOW"
    result = c.pay(20_000, gap=40)
    assert result.decision == "HOLD" and "MICRO_PROBING" in result.reasons


def test_dormant_account_drain_is_held(engine):
    result = Customer(engine).pay(45_000, idle_days=200)
    assert result.decision == "HOLD" and {"DORMANT_ACCOUNT", "HIGH_AMOUNT"} <= set(result.reasons)


def test_amount_far_above_customers_usual_needs_confirmation(engine):
    c = Customer(engine)
    for _ in range(6):
        c.pay(400)
    result = c.pay(30_000)
    assert result.decision == "CHALLENGE" and result.reasons == ["AMOUNT_ANOMALY"]
    assert "x the customer's usual amount" in result.hits[0].detail


def test_structuring_just_under_the_threshold(engine):
    c = Customer(engine)
    decisions = [c.pay(amount, gap=1800) for amount in (49_900, 49_500, 48_800)]
    assert [d.decision for d in decisions[:2]] == ["ALLOW", "ALLOW"]     # HIGH_AMOUNT alone is not enough
    assert decisions[2].decision == "HOLD" and "STRUCTURING" in decisions[2].reasons


def test_odd_hours_adds_weight(engine):
    two_am = datetime(2026, 11, 11, 2, 30, tzinfo=IST).timestamp()
    result = Customer(engine).pay(500, at=two_am)
    assert result.reasons == ["ODD_HOURS"] and result.decision == "ALLOW"


# ------------------------------------------------------------------ signals from other services


def test_paying_a_flagged_mule_account_is_held(engine):
    c = Customer(engine)
    engine.raise_signal(f"sig:mule:{c.payee}")           # set by Fraud Alert from stream analytics
    result = c.pay(1_000)
    assert result.decision == "HOLD" and result.reasons == ["MULE_PAYEE"]


def test_fd_break_and_flee_needs_confirmation(engine):
    c = Customer(engine)
    engine.raise_signal(f"sig:cust:{c.id}:fd_break")     # set by Deposits on early closure
    assert c.pay(60_000, trusted=True).reasons == ["HIGH_AMOUNT"]            # to own account: fine
    result = c.pay(60_000, payee=uuid.uuid4().hex)
    assert result.decision == "CHALLENGE" and "FD_BREAK_AND_FLEE" in result.reasons


def test_impossible_travel_with_new_device_is_held(engine):
    c = Customer(engine)
    engine.raise_signal(f"sig:cust:{c.id}:impossible_travel")
    result = c.pay(3_000, device=uuid.uuid4().hex)
    assert result.decision == "HOLD" and {"IMPOSSIBLE_TRAVEL", "NEW_DEVICE"} <= set(result.reasons)


def test_deny_list_blocks(engine):
    c = Customer(engine)
    engine.store.put(f"deny:acct:{c.payee}", "confirmed fraud")
    result = c.pay(10)
    assert (result.decision, result.score) == ("BLOCK", 100)


# ------------------------------------------------------------------ rule management


def rules_copy(tmp_path, mutate):
    import yaml
    path = tmp_path / "rules.yaml"
    shutil.copy("config/rules.yaml", path)
    config = yaml.safe_load(path.read_text())
    mutate(config)
    path.write_text(yaml.safe_dump(config))
    return str(path)


def test_shadow_rule_is_logged_but_not_counted(store, tmp_path):
    path = rules_copy(tmp_path, lambda c: c["rules"]["DORMANT_ACCOUNT"].update(shadow=True))
    result = Customer(Engine(store.client(), Rules(path))).pay(20_000, idle_days=200)
    assert result.decision == "ALLOW" and result.reasons == []
    assert [(h.rule, h.shadow) for h in result.hits] == [("DORMANT_ACCOUNT", True)]


def test_rules_reload_without_restart(store, tmp_path):
    path = rules_copy(tmp_path, lambda c: None)
    rules = Rules(path)
    engine = Engine(store.client(), rules)
    assert Customer(engine).pay(20_000, idle_days=200).decision == "CHALLENGE"   # 55
    rules_copy(tmp_path, lambda c: c["rules"]["DORMANT_ACCOUNT"].update(weight=90))
    import os
    os.utime(path, (1, 1))
    rules._checked = 0                                   # skip the 10-second poll interval
    assert Customer(engine).pay(20_000, idle_days=200).decision == "BLOCK"


def test_invalid_rules_file_keeps_previous_rules(store, tmp_path):
    path = rules_copy(tmp_path, lambda c: None)
    rules = Rules(path)
    open(path, "w").write("rules: {BROKEN: {weight: lots}}\nbands: {challenge: 30, hold: 60, block: 85}\n")
    rules._checked = 0
    assert "VELOCITY_1M" in rules.current()["rules"]


# ------------------------------------------------------------------ degraded mode


def test_feature_store_down_holds_large_and_allows_small(store, engine):
    c = Customer(engine)
    store.set_down(True)
    try:
        small, large = c.pay(500), c.pay(35_000)
    finally:
        store.set_down(False)
    assert small.decision == "ALLOW" and small.degraded
    assert large.decision == "HOLD" and large.degraded and large.hits[-1].rule == "DEGRADED_MODE"


def test_one_store_node_down_changes_nothing(store, engine):
    store.nodes["node-2"].set_faults([], True)
    try:
        c = Customer(engine)
        result = c.pay(25_000, device=uuid.uuid4().hex, payee=uuid.uuid4().hex)
    finally:
        store.nodes["node-2"].set_faults([], False)
    assert result.decision == "HOLD" and not result.degraded


# ------------------------------------------------------------------ gRPC


def test_grpc_api(engine):
    server = grpc_server.start(engine, 0)
    stub = pb_grpc.RiskServiceStub(grpc.insecure_channel(f"localhost:{server.bound_port}"))
    c = Customer(engine)
    res = stub.Evaluate(pb.EvaluateRequest(
        txn_id="t1", customer_id=c.id, from_account_id=c.account, to_account_id=uuid.uuid4().hex,
        amount=2_500_000, device_id=uuid.uuid4().hex, event_time_ms=int(NOON * 1000)))
    assert res.decision == "HOLD" and "ATO_PATTERN" in res.reasons
    assert any(h.rule == "ATO_PATTERN" and h.weight == 40 for h in res.hits)
    server.stop(0)
