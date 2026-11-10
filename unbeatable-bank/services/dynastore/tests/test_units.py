"""Building blocks: the CRDT merge rules and the hash ring."""

import itertools
import time

from app import crdt
from app.ring import Ring


# ------------------------------------------------------------------ counters


def test_counter_concurrent_increments_merge_without_loss():
    base = crdt.counter_add(None, "a", 5)
    on_a = crdt.counter_add(base, "a", 3)       # node a adds 3
    on_b = crdt.counter_add(base, "b", 10)      # meanwhile node b adds 10
    on_c = crdt.counter_add(base, "c", -2)      # and node c subtracts 2
    for order in itertools.permutations([on_a, on_b, on_c]):
        merged = None
        for env in order:
            merged = crdt.merge(merged, env)
        assert crdt.counter_value(merged) == 5 + 3 + 10 - 2


def test_merge_is_idempotent_and_commutative():
    a = crdt.counter_add(crdt.counter_add(None, "a", 4), "b", 1)
    b = crdt.counter_add(None, "b", 7)
    assert crdt.merge(a, a) == a
    assert crdt.digest(crdt.merge(a, b)) == crdt.digest(crdt.merge(b, a))
    assert crdt.digest(crdt.merge(crdt.merge(a, b), b)) == crdt.digest(crdt.merge(a, b))


# ------------------------------------------------------------------ registers


def test_write_with_context_replaces_what_it_saw():
    v1 = crdt.register_write(None, "a", b"one", None)
    v2 = crdt.register_write(v1, "b", b"two", crdt.merged_clock(v1))
    assert crdt.sibling_values(v2) == [b"two"]
    assert crdt.sibling_values(crdt.merge(v1, v2)) == [b"two"]  # the old version never comes back


def test_concurrent_writes_become_siblings_with_a_deterministic_winner():
    base = crdt.register_write(None, "a", b"base", None, ts=1)
    left = crdt.register_write(base, "a", b"left", crdt.merged_clock(base), ts=2)
    right = crdt.register_write(base, "b", b"right", crdt.merged_clock(base), ts=3)
    merged = crdt.merge(left, right)
    assert sorted(crdt.sibling_values(merged)) == [b"left", b"right"]
    assert crdt.winner(merged) == crdt.winner(crdt.merge(right, left)) == b"right"  # latest write
    resolved = crdt.register_write(merged, "a", b"resolved", crdt.merged_clock(merged), ts=4)
    assert crdt.sibling_values(resolved) == [b"resolved"]


def test_first_writer_wins_for_put_if_absent():
    early = crdt.register_write(None, "a", b"txn-1", None, first_writer_wins=True, ts=10)
    late = crdt.register_write(None, "b", b"txn-2", None, first_writer_wins=True, ts=11)
    assert crdt.winner(crdt.merge(early, late)) == crdt.winner(crdt.merge(late, early)) == b"txn-1"


def test_expired_values_are_absent():
    old = crdt.counter_add(None, "a", 5, exp=time.time() - 1)
    fresh = crdt.counter_add(None, "b", 2, exp=time.time() + 60)
    assert crdt.live(old) is None
    assert crdt.counter_value(crdt.merge(old, fresh)) == 2


# ------------------------------------------------------------------ ring


def test_preference_list_has_n_distinct_nodes():
    ring = Ring([f"n{i}" for i in range(5)])
    for i in range(200):
        nodes = ring.preference_list(f"key-{i}", 3)
        assert len(nodes) == len(set(nodes)) == 3


def test_virtual_nodes_spread_keys_evenly():
    ring = Ring([f"n{i}" for i in range(5)], vnodes=128)
    owners = [ring.preference_list(f"key-{i}", 1)[0] for i in range(10_000)]
    shares = [owners.count(f"n{i}") / 10_000 for i in range(5)]
    assert min(shares) > 0.12 and max(shares) < 0.28  # ideal is 0.20 each
    assert abs(sum(ring.ownership().values()) - 1) < 0.001


def test_adding_a_node_moves_only_a_fraction_of_keys():
    before, after = Ring([f"n{i}" for i in range(5)]), Ring([f"n{i}" for i in range(6)])
    keys = [f"key-{i}" for i in range(5_000)]
    moved = sum(before.preference_list(k, 1) != after.preference_list(k, 1) for k in keys)
    assert moved / len(keys) < 0.30  # ideal is 1/6; naive modulo hashing would move ~83%
