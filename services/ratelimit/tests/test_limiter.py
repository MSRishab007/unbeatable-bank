import uuid
from concurrent.futures import ThreadPoolExecutor

import grpc
import pytest

from ubcommon.testing import LocalDynaCluster
from ubproto.ratelimit.v1 import ratelimit_pb2 as pb
from ubproto.ratelimit.v1 import ratelimit_pb2_grpc as pb_grpc

from app import grpc_server
from app.limiter import Limiter, Policies, Rule


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    cluster = LocalDynaCluster(tmp_path_factory.mktemp("dyna"))
    yield cluster
    cluster.shutdown()


@pytest.fixture(autouse=True)
def settled_cluster(store):
    """Fault tests leave the failure detectors catching up for a moment. Counts are only
    exact on a settled cluster (that is the eventual-consistency trade-off), so wait."""
    store.wait_healthy()


def make(store, clock=None, **scopes):
    policies = Policies(fail_open_max_amount=200_000, sets={"payments": scopes, "login": {}})
    return Limiter(store.client(), policies, clock or Clock())


def uid():
    return uuid.uuid4().hex[:10]


def test_allows_up_to_the_limit_then_rejects(store):
    limiter, who = make(store, account=[Rule(5, 60)]), uid()
    results = [limiter.check("payments", [("account", who)]) for _ in range(7)]
    assert [r.allowed for r in results] == [True] * 5 + [False] * 2
    assert results[0].remaining == 4 and results[4].remaining == 0
    assert results[5].limited_by == "account" and 1 <= results[5].retry_after <= 60


def test_window_slides_instead_of_resetting(store):
    """Usage from the previous window still counts, weighted by how much of it overlaps."""
    clock = Clock(1_000_020.0)   # 20s into a 60s window
    limiter, who = make(store, clock, account=[Rule(10, 60)]), uid()
    for _ in range(10):
        assert limiter.check("payments", [("account", who)]).allowed
    assert not limiter.check("payments", [("account", who)]).allowed
    clock.now = 1_000_080.0 + 6   # next window, 10% in: 90% of the previous 10 still counts (9.0)
    assert limiter.check("payments", [("account", who)]).allowed        # 9.0 + 1 = 10
    assert not limiter.check("payments", [("account", who)]).allowed    # 9.0 + 2 > 10
    clock.now = 1_000_080.0 + 54  # 90% in: only 1.0 carried over
    for _ in range(8):
        assert limiter.check("payments", [("account", who)]).allowed    # 1.0 + 1 (earlier) + 8 = 10
    assert not limiter.check("payments", [("account", who)]).allowed


def test_rejected_requests_do_not_use_up_the_budget(store):
    limiter, who = make(store, account=[Rule(3, 60)]), uid()
    for _ in range(50):
        limiter.check("payments", [("account", who)])
    assert store.client().counter(limiter._key("payments", "account", who, Rule(3, 60), int(1_000_000 // 60)), r=3) == 3


def test_every_descriptor_must_pass(store):
    limiter = make(store, customer=[Rule(100, 60)], device=[Rule(2, 60)])
    customer, device = uid(), uid()
    for _ in range(2):
        assert limiter.check("payments", [("customer", customer), ("device", device)]).allowed
    d = limiter.check("payments", [("customer", customer), ("device", device)])
    assert not d.allowed and d.limited_by == "device"
    assert limiter.check("payments", [("customer", customer), ("device", uid())]).allowed  # other device is fine


def test_multiple_windows_per_scope(store):
    clock = Clock()
    limiter, who = make(store, clock, customer=[Rule(3, 60), Rule(5, 3600)]), uid()
    for _ in range(3):
        assert limiter.check("payments", [("customer", who)]).allowed
    clock.now += 120            # per-minute budget is back...
    assert limiter.check("payments", [("customer", who)]).allowed
    assert limiter.check("payments", [("customer", who)]).allowed
    d = limiter.check("payments", [("customer", who)])
    assert not d.allowed and d.limit == 5   # ...but the hourly one is used up


def test_coordinators_share_state(store):
    """Three coordinator pods, each talking to DynaStore, enforce one shared limit."""
    who = uid()
    pods = [make(store, account=[Rule(30, 60)]) for _ in range(3)]

    def hit(i):
        return pods[i % 3].check("payments", [("account", who)]).allowed

    with ThreadPoolExecutor(max_workers=12) as ex:
        allowed = sum(ex.map(hit, range(90)))
    assert allowed == 30


def test_permit_leasing_cuts_store_calls(store):
    from app.limiter import STORE_CALLS
    limiter, who = make(store, ip=[Rule(100, 60, lease=10)]), uid()
    before = STORE_CALLS.labels("incr")._value.get()
    results = [limiter.check("payments", [("ip", who)]).allowed for _ in range(120)]
    calls = STORE_CALLS.labels("incr")._value.get() - before
    assert sum(results) == 100              # still exactly the limit on a single pod
    assert calls <= 14                      # ~1 store call per 10 permits; rejections are answered locally


def test_store_down_fails_open_for_small_and_closed_for_large(store):
    limiter, who = make(store, account=[Rule(5, 60)]), uid()
    store.set_down(True)
    try:
        small = limiter.check("payments", [("account", who)], amount=50_000)
        large = limiter.check("payments", [("account", who)], amount=5_000_000)
        assert small.allowed and small.degraded
        assert not large.allowed and large.degraded and large.limited_by == "store_unavailable"
    finally:
        store.set_down(False)


def test_one_node_down_does_not_affect_limiting(store):
    limiter, who = make(store, account=[Rule(4, 60)]), uid()
    store.nodes["node-1"].set_faults([], True)
    try:
        results = [limiter.check("payments", [("account", who)]) for _ in range(6)]
        assert [r.allowed for r in results] == [True] * 4 + [False] * 2
        assert not any(r.degraded for r in results)
    finally:
        store.nodes["node-1"].set_faults([], False)


def test_grpc_api_and_policy_file(store):
    limiter = Limiter(store.client(), Policies.load("config/policies.yaml"))
    server = grpc_server.start(limiter, 0)
    stub = pb_grpc.RateLimitServiceStub(grpc.insecure_channel(f"localhost:{server.bound_port}"))
    who = uid()
    request = pb.CheckRequest(policy_set="payments", descriptors=[pb.Descriptor(scope="account", id=who)])
    answers = [stub.Check(request) for _ in range(12)]
    assert sum(a.allowed for a in answers) == 10            # account: 10 per minute in the shipped policy
    assert answers[-1].limited_by == "account"
    server.stop(0)
