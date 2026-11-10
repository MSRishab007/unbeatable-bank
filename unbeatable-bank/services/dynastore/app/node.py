"""A DynaStore node: coordinates client requests and replicates with its peers.

There is no leader. Any node accepts any request and acts as the coordinator
for it:

  write  apply locally, send to the other replicas, succeed once W have acked
  read   ask the replicas, wait for R answers, merge them, repair stale ones

Availability techniques, as in Amazon's Dynamo:

  sloppy quorum     if a replica is down, the write goes to the next healthy
                    node on the ring instead, so writes keep succeeding
  hinted handoff    that stand-in remembers who the write was for and hands it
                    over when the replica returns
  read repair       a read that finds replicas disagreeing fixes them
  anti-entropy      a background task compares digests with peers and
                    exchanges whatever differs
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections import Counter as Tally
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

import grpc
from prometheus_client import Counter, Gauge, Histogram

from ubproto.dynastore.v1 import dynastore_pb2 as pb
from ubproto.dynastore.v1 import dynastore_pb2_grpc as pb_grpc

from . import crdt
from .ring import Ring
from .storage import Storage

log = logging.getLogger("dynastore")

OPS = Counter("dynastore_ops_total", "Client operations", ["node", "op", "result"])
QUORUM_LATENCY = Histogram("dynastore_quorum_seconds", "Time to reach quorum", ["node", "op"],
                           buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1))
EVENTS = Counter("dynastore_events_total", "Replication events",
                 ["node", "event"])  # read_repair, hint_stored, hint_delivered, sync_repair
HINTS_PENDING = Gauge("dynastore_hints_pending", "Hints waiting for their replica", ["node"])
PEER_ALIVE = Gauge("dynastore_peer_alive", "1 if this node believes the peer is up", ["node", "peer"])
KEYS = Gauge("dynastore_keys", "Live keys stored on this node", ["node"])


class QuorumError(Exception):
    """Not enough replicas answered. The write may still exist on some of them."""


class Unreachable(Exception):
    pass


class NodeDown(Exception):
    """This node is simulating a crash."""


class Node:
    def __init__(
        self,
        node_id: str,
        peers: dict[str, str],          # every node id (including this one) -> host:port
        data_path: str,
        *,
        n: int = 3,
        r: int = 2,
        w: int = 2,
        vnodes: int = 64,
        rpc_timeout: float = 0.25,
        heartbeat_interval: float = 1.0,
        hint_interval: float = 1.0,
        sync_interval: float = 15.0,
        sweep_interval: float = 30.0,
    ) -> None:
        self.id = node_id
        self.peers = dict(peers)
        self.n, self.r, self.w = min(n, len(peers)), r, w
        self.ring = Ring(list(peers), vnodes)
        self.storage = Storage(data_path)
        self.actor = f"{node_id}:{self.storage.epoch()}"
        self.rpc_timeout = rpc_timeout
        self._intervals = (heartbeat_interval, hint_interval, sync_interval, sweep_interval)

        self._alive = {p: True for p in peers if p != node_id}
        self._failures: Tally = Tally()
        self._blocked: set[str] = set()
        self._down = False
        self.stats: Tally = Tally()

        self._channels: dict[str, grpc.Channel] = {}
        self._pool = ThreadPoolExecutor(max_workers=32, thread_name_prefix=f"{node_id}-fanout")
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._sync_cursor = ""
        for peer in self._alive:
            PEER_ALIVE.labels(self.id, peer).set(1)

    # ------------------------------------------------------------------ lifecycle

    def start_background(self) -> None:
        heartbeat, hint, sync, sweep = self._intervals
        for name, fn, interval in (("heartbeat", self._heartbeat_round, heartbeat),
                                   ("hints", self._deliver_hints, hint),
                                   ("sync", self._sync_round, sync),
                                   ("sweep", self._sweep, sweep)):
            if interval > 0:
                t = threading.Thread(target=self._loop, args=(fn, interval), name=f"{self.id}-{name}", daemon=True)
                t.start()
                self._threads.append(t)

    def _loop(self, fn, interval: float) -> None:
        while not self._stop.wait(interval * random.uniform(0.8, 1.2)):
            try:
                fn()
            except Exception:
                log.exception("%s background task failed", self.id)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2)
        self._pool.shutdown(wait=False, cancel_futures=True)
        for c in self._channels.values():
            c.close()
        self.storage.close()

    # ------------------------------------------------------------------ fault injection

    def set_faults(self, blocked_peers: list[str], down: bool) -> None:
        self._blocked = set(blocked_peers) & set(self.peers)
        self._down = down
        log.warning("%s faults: blocked=%s down=%s", self.id, sorted(self._blocked), down)

    def check_inbound(self, from_node: str = "") -> None:
        if self._down or (from_node and from_node in self._blocked):
            raise NodeDown()

    # ------------------------------------------------------------------ peer RPC

    def _channel(self, peer: str) -> grpc.Channel:
        if peer not in self._channels:
            self._channels[peer] = grpc.insecure_channel(self.peers[peer])
        return self._channels[peer]

    def _replica(self, peer: str, method: str, request, timeout: float | None = None):
        if self._down or peer in self._blocked:
            raise Unreachable(peer)
        try:
            result = getattr(pb_grpc.ReplicaStub(self._channel(peer)), method)(
                request, timeout=timeout or self.rpc_timeout)
        except grpc.RpcError as e:
            self._mark(peer, ok=False)
            raise Unreachable(peer) from e
        self._mark(peer, ok=True)
        return result

    def _mark(self, peer: str, ok: bool) -> None:
        if ok:
            self._failures[peer] = 0
            if not self._alive[peer]:
                log.info("%s sees %s back up", self.id, peer)
        else:
            self._failures[peer] += 1
            if self._alive[peer]:
                log.warning("%s sees %s down", self.id, peer)
        self._alive[peer] = ok
        PEER_ALIVE.labels(self.id, peer).set(1 if ok else 0)

    def is_alive(self, node: str) -> bool:
        if node == self.id:
            return not self._down
        return self._alive[node] and node not in self._blocked

    # ------------------------------------------------------------------ placement

    def preference_list(self, key: str) -> list[str]:
        return self.ring.preference_list(key, self.n)

    def _write_targets(self, key: str) -> list[tuple[str, str]]:
        """(node, hint_for) pairs: the preference list, with each unreachable
        replica replaced by the next healthy node further round the ring."""
        walk = self.ring.walk(key)
        preferred, rest = walk[:self.n], walk[self.n:]
        spares = [node for node in rest if self.is_alive(node)]
        targets: list[tuple[str, str]] = []
        for node in preferred:
            if self.is_alive(node):
                targets.append((node, ""))
            elif spares:
                targets.append((spares.pop(0), node))
        return targets

    def _read_targets(self, key: str) -> list[str]:
        """The first N healthy nodes on the ring (stand-ins hold hinted data)."""
        return [node for node in self.ring.walk(key) if self.is_alive(node)][:self.n]

    def coordinator_candidates(self, key: str) -> list[str]:
        """Healthy members of the preference list, in ring order."""
        return [node for node in self.preference_list(key) if self.is_alive(node)]

    # ------------------------------------------------------------------ replicate

    def _send_merge(self, node: str, key: str, env: dict, hint_for: str) -> None:
        if node == self.id:
            self.storage.merge(key, env)
            if hint_for:
                self._store_hint(hint_for, key)
            return
        self._replica(node, "ReplicaMerge", pb.ReplicaMergeRequest(
            from_node=self.id, key=key, envelope=json.dumps(env), hint_for=hint_for))

    def _store_hint(self, target: str, key: str) -> None:
        self.storage.add_hint(target, key)
        self.stats["hints_stored"] += 1
        EVENTS.labels(self.id, "hint_stored").inc()

    def _replicate(self, key: str, env: dict, w: int) -> tuple[int, bool]:
        """Send an already locally applied value to the other replicas.
        Returns (acks, sloppy). Raises QuorumError if fewer than w acked."""
        w = w or self.w
        targets = self._write_targets(key)
        used = {node for node, _ in targets}
        acks, sloppy = 1, False  # the coordinator's own copy
        pending: dict[Future, tuple[str, str]] = {}
        for node, hint_for in targets:
            if node == self.id:
                if hint_for:
                    self._store_hint(hint_for, key)
                    sloppy = True
                continue
            sloppy = sloppy or bool(hint_for)
            pending[self._pool.submit(self._send_merge, node, key, env, hint_for)] = (node, hint_for)
        if self.id not in used:
            # Coordinating for a key we don't normally hold: keep our copy as a hint
            # for a preferred replica that is down, if any.
            missing = [p for p in self.preference_list(key) if not self.is_alive(p)]
            if missing:
                self._store_hint(missing[0], key)
                sloppy = True

        spares = [node for node in self.ring.walk(key)[self.n:] if node not in used and self.is_alive(node)]
        deadline = time.monotonic() + self.rpc_timeout * 2
        while pending and acks < w:
            done, _ = wait(pending, timeout=max(0.0, deadline - time.monotonic()), return_when=FIRST_COMPLETED)
            if not done:
                break
            for future in done:
                node, hint_for = pending.pop(future)
                if future.exception() is None:
                    acks += 1
                    continue
                # The replica turned out to be unreachable. We hold the value, so keep a
                # hint for it, and try a stand-in so the write is still on enough nodes.
                intended = hint_for or node
                self._store_hint(intended, key)
                sloppy = True
                if spares:
                    stand_in = spares.pop(0)
                    pending[self._pool.submit(self._send_merge, stand_in, key, env, intended)] = (stand_in, intended)
        for future, (node, hint_for) in pending.items():
            # Sends still in flight after quorum: if one fails later, remember to retry it.
            future.add_done_callback(
                lambda f, intended=hint_for or node: f.exception() and self._late_failure(intended, key))
        if acks < w:
            raise QuorumError(f"write quorum not met: {acks}/{w} acks")
        return acks, sloppy

    def _late_failure(self, intended: str, key: str) -> None:
        try:
            self._store_hint(intended, key)
        except Exception:  # storage closed during shutdown
            pass

    # ------------------------------------------------------------------ client operations

    def increment(self, key: str, delta: int, ttl: int, w: int) -> tuple[int, int, bool]:
        with QUORUM_LATENCY.labels(self.id, "increment").time():
            exp = time.time() + ttl if ttl else 0
            env = self.storage.update(key, lambda cur: crdt.counter_add(cur, self.actor, delta, exp))
            acks, sloppy = self._replicate(key, env, w)
        return crdt.counter_value(env), acks, sloppy

    def put(self, key: str, value: bytes, context: str, ttl: int, w: int,
            first_writer_wins: bool = False) -> tuple[str, int, bool]:
        with QUORUM_LATENCY.labels(self.id, "put").time():
            exp = time.time() + ttl if ttl else 0
            clock = json.loads(context) if context else None
            env = self.storage.update(key, lambda cur: crdt.register_write(
                cur, self.actor, value, clock, exp, first_writer_wins))
            acks, sloppy = self._replicate(key, env, w)
        return json.dumps(crdt.merged_clock(env), sort_keys=True), acks, sloppy

    def get(self, key: str, r: int) -> tuple[dict | None, int, bool]:
        """Returns (merged value, replicas that answered, read repair triggered)."""
        r = r or self.r
        with QUORUM_LATENCY.labels(self.id, "get").time():
            answers: dict[str, dict | None] = {}
            pending: dict[Future, str] = {}
            for node in self._read_targets(key):
                if node == self.id:
                    answers[node] = self.storage.get(key)
                else:
                    pending[self._pool.submit(self._fetch, node, key)] = node
            deadline = time.monotonic() + self.rpc_timeout * 2
            while pending and len(answers) < r:
                done, _ = wait(pending, timeout=max(0.0, deadline - time.monotonic()), return_when=FIRST_COMPLETED)
                if not done:
                    break
                for future in done:
                    node = pending.pop(future)
                    if future.exception() is None:
                        answers[node] = future.result()
            # Take any other answers that are already in, without waiting for them.
            for future, node in list(pending.items()):
                if future.done() and future.exception() is None:
                    answers[node] = future.result()
            if len(answers) < r:
                raise QuorumError(f"read quorum not met: {len(answers)}/{r} answers")

            merged = None
            for env in answers.values():
                merged = crdt.merge(merged, env)
            repaired = self._read_repair(key, merged, answers)
        return merged, len(answers), repaired

    def _fetch(self, node: str, key: str) -> dict | None:
        res = self._replica(node, "ReplicaGet", pb.ReplicaGetRequest(from_node=self.id, key=key))
        return json.loads(res.envelope) if res.found else None

    def _read_repair(self, key: str, merged: dict | None, answers: dict[str, dict | None]) -> bool:
        if merged is None:
            return False
        target = crdt.digest(merged)
        stale = [node for node, env in answers.items() if crdt.digest(crdt.live(env)) != target]
        for node in stale:
            if node == self.id:
                self.storage.merge(key, merged)
            else:
                self._pool.submit(self._quiet, self._send_merge, node, key, merged, "")
        if stale:
            self.stats["read_repairs"] += len(stale)
            EVENTS.labels(self.id, "read_repair").inc(len(stale))
        return bool(stale)

    @staticmethod
    def _quiet(fn, *args) -> None:
        try:
            fn(*args)
        except Unreachable:
            pass

    def put_if_absent(self, key: str, value: bytes, ttl: int, r: int, w: int) -> tuple[bool, bytes]:
        existing, _, _ = self.get(key, r)
        current = crdt.winner(existing)
        if current is not None:
            return False, current
        self.put(key, value, "", ttl, w, first_writer_wins=True)
        # If a concurrent writer's earlier version has already reached us, it wins.
        final = crdt.winner(self.storage.get(key)) or value
        return final == value, final

    # ------------------------------------------------------------------ replica side

    def replica_merge(self, key: str, envelope: str, hint_for: str) -> None:
        self.storage.merge(key, json.loads(envelope))
        if hint_for and hint_for != self.id:
            self._store_hint(hint_for, key)

    def replica_sync(self, digests: list[tuple[str, str]]) -> tuple[list[tuple[str, dict]], list[str]]:
        """Anti-entropy responder: compare a peer's digests with ours."""
        updates, want = [], []
        for key, theirs in digests:
            mine = self.storage.get(key)
            if crdt.digest(mine) == theirs:
                continue
            want.append(key)                 # they have something we lack or differ on
            if mine is not None:
                updates.append((key, mine))  # and so do we
        return updates, want

    # ------------------------------------------------------------------ background work

    def _heartbeat_round(self) -> None:
        for peer in list(self._alive):
            if self._down or peer in self._blocked:
                continue
            try:
                self._replica(peer, "Ping", pb.PingRequest(from_node=self.id))
            except Unreachable:
                pass
        HINTS_PENDING.labels(self.id).set(self.storage.hint_count())
        KEYS.labels(self.id).set(self.storage.count())

    def _deliver_hints(self) -> None:
        for target in self.storage.hint_targets():
            if target == self.id or not self.is_alive(target):
                continue
            for key in self.storage.hints(target):
                env = self.storage.get(key)
                if env is not None:
                    try:
                        self._send_merge(target, key, env, "")
                    except Unreachable:
                        break
                    self.stats["hints_delivered"] += 1
                    EVENTS.labels(self.id, "hint_delivered").inc()
                self.storage.remove_hint(target, key)

    def _sync_round(self) -> None:
        peers = [p for p in self._alive if self.is_alive(p)]
        if not peers or self._down:
            return
        peer = random.choice(peers)
        batch = self.storage.digests(self._sync_cursor, 500)
        self._sync_cursor = batch[-1][0] if len(batch) == 500 else ""
        shared = [(k, d) for k, d in batch if peer in self.preference_list(k)]
        if not shared:
            return
        try:
            res = self._replica(peer, "Sync", pb.SyncRequest(
                from_node=self.id, digests=[pb.KeyDigest(key=k, digest=d) for k, d in shared]), timeout=2.0)
        except Unreachable:
            return
        repaired = 0
        for item in res.updates:
            _, changed = self.storage.merge(item.key, json.loads(item.envelope))
            repaired += changed
        for key in res.want:
            env = self.storage.get(key)
            if env is not None:
                self._quiet(self._send_merge, peer, key, env, "")
                repaired += 1
        if repaired:
            self.stats["sync_repairs"] += repaired
            EVENTS.labels(self.id, "sync_repair").inc(repaired)

    def _sweep(self) -> None:
        self.storage.sweep()

    # ------------------------------------------------------------------ introspection

    def status(self) -> dict:
        return {
            "node_id": self.id,
            "nodes": [{"node_id": node, "address": address, "alive": self.is_alive(node),
                       "is_self": node == self.id} for node, address in sorted(self.peers.items())],
            "n": self.n, "r": self.r, "w": self.w,
            "keys": self.storage.count(),
            "pending_hints": self.storage.hint_count(),
            "stats": dict(self.stats),
            "blocked_peers": sorted(self._blocked),
            "simulated_down": self._down,
            "ownership": self.ring.ownership(),
        }

    def key_info(self, key: str) -> dict:
        replicas = []
        for node in self.preference_list(key):
            try:
                env = self.storage.get(key) if node == self.id else self._fetch(node, key)
                if node == self.id and self._down:
                    raise Unreachable(node)
                replicas.append({"node_id": node, "reachable": True, "found": env is not None,
                                 "counter": crdt.counter_value(env),
                                 "siblings": len((env or {}).get("s", [])), "digest": crdt.digest(env)})
            except Unreachable:
                replicas.append({"node_id": node, "reachable": False, "found": False, "counter": 0,
                                 "siblings": 0, "digest": ""})
        return {"preference_list": self.preference_list(key), "replicas": replicas}
