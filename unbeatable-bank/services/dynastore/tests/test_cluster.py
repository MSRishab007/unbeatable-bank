"""Behaviour of a live cluster: quorums, failures, partitions and recovery."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from ubcommon.dynaclient import DynaUnavailable

from app import crdt
from conftest import wait_for


def key_owned_by(cluster, *wanted, exclude=(), prefix="k"):
    """A key whose preference list contains all of `wanted` and none of `exclude`."""
    for i in range(10_000):
        key = f"{prefix}-{i}"
        replicas = cluster.replicas(key)
        if all(w in replicas for w in wanted) and not any(e in replicas for e in exclude):
            return key
    raise AssertionError("no such key")


# ------------------------------------------------------------------ basics


def test_any_node_serves_any_key(cluster):
    """No leader: write through one node, read through each of the others."""
    cluster.client("node-0").put("greeting", "hello", w=3)
    for node_id in cluster.ids:
        assert cluster.client(node_id).get("greeting").value == b"hello"


def test_key_is_stored_on_exactly_n_replicas(cluster):
    client = cluster.client()
    for i in range(20):
        key = f"placement-{i}"
        client.put(key, "x", w=3)
        assert cluster.holders(key) == sorted(cluster.replicas(key))


def test_concurrent_increments_through_every_node_are_all_counted(cluster):
    clients = [cluster.client(n) for n in cluster.ids]

    def bump(i):
        clients[i % len(clients)].incr("visits", 1)

    with ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(bump, range(300)))
    assert cluster.client().counter("visits", r=3) == 300


def test_ttl_expiry(make_cluster):
    cluster = make_cluster(size=3)
    client = cluster.client()
    client.incr("window", 5, ttl=1)
    assert client.counter("window") == 5
    assert wait_for(lambda: client.counter("window") == 0, timeout=3)


# ------------------------------------------------------------------ the consistency dial


def test_w_and_r_trade_consistency_for_availability(make_cluster):
    """3 nodes, 1 down: W=2 still works, W=3 does not. Same for reads."""
    cluster = make_cluster(size=3)
    client = cluster.client("node-0", "node-1")
    client.put("dial", "v1", w=3)
    cluster.stop("node-2")

    client.put("dial", "v2", w=2)                   # available
    assert client.get("dial", r=2).value == b"v2"
    with pytest.raises(DynaUnavailable):
        client.put("dial", "v3", w=3)               # strict consistency refuses
    with pytest.raises(DynaUnavailable):
        client.get("dial", r=3)
    assert client.get("dial", r=1).found            # R=1 always answers


# ------------------------------------------------------------------ node failure


def test_sloppy_quorum_and_hinted_handoff(cluster):
    """A replica crashes. Writes for it go to a stand-in, and are handed over when it returns."""
    key = key_owned_by(cluster, "node-1")
    stand_ins = [n for n in cluster.ids if n not in cluster.replicas(key)]
    survivor = next(n for n in cluster.replicas(key) if n != "node-1")
    cluster.stop("node-1")
    client = cluster.client(survivor)

    for _ in range(10):
        client.incr(key, 1, w=2)                    # still succeeds
    assert client.counter(key, r=2) == 10
    holders = cluster.holders(key)
    assert any(s in holders for s in stand_ins)     # a node outside the preference list took the write
    hints = sum(n.storage.hint_count() for n in cluster.nodes.values())
    assert hints >= 1

    restarted = cluster.start("node-1")
    assert wait_for(lambda: crdt.counter_value(restarted.storage.get(key)) == 10)   # hint delivered
    assert wait_for(lambda: sum(n.storage.hint_count() for n in cluster.nodes.values()) == 0)


def test_client_fails_over_to_a_healthy_node(cluster):
    cluster.client().put("failover", "ok", w=3)
    cluster.stop("node-0")
    client = cluster.client("node-0", "node-1", "node-2")   # first target is dead
    for _ in range(6):
        assert client.get("failover").value == b"ok"


def test_a_hung_node_does_not_fail_writes_or_slow_everything_down(cluster):
    """A node that accepts connections but never replies (what `docker compose stop`
    or a frozen pod looks like) times out instead of refusing. Counter updates must
    still succeed through another node, and after the first timeout the client must
    stop waiting on the hung node for every request."""
    import time

    cluster.hang("node-0")
    client = cluster.client("node-0", "node-1", "node-2", timeout=0.5)   # the hung node is a coordinator choice
    started = time.monotonic()
    for i in range(30):
        assert client.incr("hung-counter", 1) >= i + 1         # every update succeeds
        client.put(f"hung-key-{i}", "v")
        assert client.get(f"hung-key-{i}").value == b"v"
    elapsed = time.monotonic() - started
    assert elapsed < 3.0, f"90 calls took {elapsed:.1f}s: the client keeps waiting on the hung node"
    assert 30 <= client.counter("hung-counter") <= 31       # at most one double count, from the first timeout


def test_simulated_crash_via_admin(cluster):
    """The demo switch: mark a node down without killing the process."""
    cluster.nodes["node-3"].set_faults([], down=True)
    client = cluster.client("node-3", "node-4")
    client.incr("demo", 1)
    assert client.counter("demo") == 1
    assert wait_for(lambda: not cluster.nodes["node-4"].is_alive("node-3"))
    cluster.heal()
    assert wait_for(lambda: cluster.nodes["node-4"].is_alive("node-3"))


# ------------------------------------------------------------------ partitions and repair


def test_partition_diverges_then_converges(cluster):
    """Both sides of a split keep accepting increments. After healing, nothing is lost."""
    side_a, side_b = ["node-0", "node-1"], ["node-2", "node-3", "node-4"]
    key = key_owned_by(cluster, "node-0", "node-2")
    cluster.partition(side_a, side_b)
    a, b = cluster.client(*side_a), cluster.client(*side_b)

    for _ in range(7):
        a.incr(key, 1, w=1)
    for _ in range(5):
        b.incr(key, 1, w=1)
    assert a.counter(key, r=1) == 7 and b.counter(key, r=1) == 5     # divergent views

    cluster.heal()
    assert wait_for(lambda: cluster.client().counter(key, r=3) == 12)  # CRDT merge: 7 + 5


def test_read_repair_fixes_a_stale_replica(cluster):
    key = key_owned_by(cluster, "node-0", "node-1", "node-2")
    client = cluster.client("node-0")
    client.put(key, "old", w=3)
    cluster.partition(["node-2"], ["node-0", "node-1", "node-3", "node-4"])
    client.put(key, "new", w=2)                                  # node-2 misses this
    cluster.heal()
    # Drop the hints so that only read repair can fix node-2.
    for node in cluster.nodes.values():
        for target in node.storage.hint_targets():
            for k in node.storage.hints(target):
                node.storage.remove_hint(target, k)
    assert crdt.winner(cluster.nodes["node-2"].storage.get(key)) == b"old"

    assert wait_for(lambda: cluster.nodes["node-0"].is_alive("node-2"))
    assert client.get(key, r=3).value == b"new"                  # the read notices and repairs
    assert wait_for(lambda: crdt.winner(cluster.nodes["node-2"].storage.get(key)) == b"new")
    assert sum(n.stats["read_repairs"] for n in cluster.nodes.values()) >= 1


def test_anti_entropy_converges_without_any_reads(make_cluster):
    cluster = make_cluster(size=3, sync_interval=0.2)
    client = cluster.client("node-0")
    cluster.partition(["node-2"], ["node-0", "node-1"])
    for i in range(30):
        client.incr(f"ae-{i}", i + 1, w=2)
    for node in cluster.nodes.values():                           # no hints: only anti-entropy can help
        for target in node.storage.hint_targets():
            for k in node.storage.hints(target):
                node.storage.remove_hint(target, k)
    assert cluster.nodes["node-2"].storage.count() == 0
    cluster.heal()
    assert wait_for(lambda: cluster.nodes["node-2"].storage.count() == 30, timeout=10)
    digests = [{k: d for k, d in n.storage.digests()} for n in cluster.nodes.values()]
    assert digests[0] == digests[1] == digests[2]


def test_concurrent_writes_across_a_partition_become_siblings(cluster):
    key = key_owned_by(cluster, "node-0", "node-2")
    cluster.client().put(key, "base", w=3)
    cluster.partition(["node-0", "node-1"], ["node-2", "node-3", "node-4"])
    cluster.client("node-0").put(key, "from-a", w=1)
    cluster.client("node-2").put(key, "from-b", w=1)
    cluster.heal()

    client = cluster.client()
    assert wait_for(lambda: sorted(client.get(key, r=3).siblings) == [b"from-a", b"from-b"])
    seen = client.get(key, r=3)
    client.put(key, "resolved", context=seen.context, w=3)       # a write that has seen both
    assert client.get(key, r=3).siblings == [b"resolved"]


# ------------------------------------------------------------------ put-if-absent


def test_put_if_absent(cluster):
    client = cluster.client()
    assert client.put_if_absent("idem:req-1", "txn-A") == (True, b"txn-A")
    assert client.put_if_absent("idem:req-1", "txn-B") == (False, b"txn-A")


def test_put_if_absent_is_not_linearizable_but_converges(cluster):
    """Why money can't live here: both sides of a partition 'win', then agree on one winner."""
    key = key_owned_by(cluster, "node-0", "node-2", prefix="idem")
    cluster.partition(["node-0", "node-1"], ["node-2", "node-3", "node-4"])
    won_a, _ = cluster.client("node-0").put_if_absent(key, "A", r=1, w=1)
    won_b, _ = cluster.client("node-2").put_if_absent(key, "B", r=1, w=1)
    assert won_a and won_b                                        # two winners!
    cluster.heal()
    client = cluster.client()
    assert wait_for(lambda: len(client.get(key, r=3).siblings) == 2)
    assert client.get(key, r=3).value == b"A"                     # everyone agrees: first writer wins


# ------------------------------------------------------------------ durability


def test_restart_keeps_data_and_counter_slots(cluster):
    key = key_owned_by(cluster, "node-0")
    client = cluster.client("node-0")
    client.incr(key, 4, w=3)
    actor = cluster.nodes["node-0"].actor
    cluster.stop("node-0")
    node = cluster.start("node-0")
    assert node.actor == actor                                    # same disk, same identity
    client = cluster.client("node-0")
    client.incr(key, 1, w=2)
    assert client.counter(key, r=3) == 5


def test_node_that_lost_its_disk_never_undercounts(cluster):
    key = key_owned_by(cluster, "node-0")
    cluster.client("node-0").incr(key, 10, w=3)
    old_actor = cluster.nodes["node-0"].actor
    cluster.stop("node-0")
    cluster.wipe("node-0")
    node = cluster.start("node-0")
    assert node.actor != old_actor                                # new epoch, fresh counter slots
    client = cluster.client("node-0")
    client.incr(key, 1, w=2)                                      # coordinated by the amnesiac node
    assert client.counter(key, r=3) == 11


# ------------------------------------------------------------------ introspection


def test_key_info_shows_divergence(cluster):
    key = key_owned_by(cluster, "node-0", "node-1", "node-2")
    client = cluster.client("node-0")
    client.incr(key, 1, w=3)
    cluster.partition(["node-2"], ["node-0", "node-1", "node-3", "node-4"])
    client.incr(key, 1, w=2)
    info = client.key_info(key)
    states = {r.node_id: r for r in info.replicas}
    assert list(info.preference_list) == cluster.replicas(key)
    assert not states["node-2"].reachable
    cluster.heal()
    assert wait_for(lambda: len({r.digest for r in cluster.client("node-0").key_info(key).replicas}) == 1)


def test_cluster_status(cluster):
    status = cluster.client("node-1").status()
    assert status.node_id == "node-1" and len(status.nodes) == 5
    assert (status.n, status.r, status.w) == (3, 2, 2)
    assert all(n.alive for n in status.nodes)
