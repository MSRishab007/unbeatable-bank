"""Client for DynaStore. Any node can serve any request, so the client just
rotates through the nodes it knows and fails over when one is unreachable."""

from __future__ import annotations

import itertools
import json
import os
import time
from dataclasses import dataclass, field

import grpc

from ubcommon.grpcutil import NODE_OPTIONS
from ubproto.dynastore.v1 import dynastore_pb2 as pb
from ubproto.dynastore.v1 import dynastore_pb2_grpc as pb_grpc


class DynaUnavailable(Exception):
    """No node could satisfy the request (all unreachable, or quorum not met)."""


@dataclass
class Value:
    found: bool
    type: str = ""
    value: bytes = b""
    siblings: list[bytes] = field(default_factory=list)
    context: str = ""
    counter: int = 0

    def json(self, default=None):
        return json.loads(self.value) if self.found and self.value else default


# Which calls may be sent to another node after a failure.
#
# UNAVAILABLE means the node never started the operation, so trying another node
# is always safe. A timeout is ambiguous: the slow node may still apply it later.
# Reads and register writes are safe to repeat (a write carries its own version).
# Increment is repeated too, on purpose: these counters drive rate limits and
# velocity rules, where counting one attempt twice during a node failure is
# harmless, while losing the count, or failing the payment, is not.
_SAFE_TO_REPEAT = {"Get", "Put", "PutIfAbsent", "Increment", "ClusterStatus", "KeyInfo"}

# After a node times out or refuses, send it nothing for this long (unless every
# node is in the same state). A hung node then costs one timeout, not one per call.
SUSPECT_SECONDS = 5.0


class DynaClient:
    """Talks to any DynaStore node; each node coordinates the request for the replicas.

    Requests rotate over the nodes. One that fails is skipped for SUSPECT_SECONDS,
    and the request goes to the next node instead.
    """

    def __init__(self, targets: str | list[str] | None = None, timeout: float = 2.0,
                 suspect_seconds: float = SUSPECT_SECONDS) -> None:
        targets = targets or os.environ["DYNASTORE_NODES"]
        if isinstance(targets, str):
            targets = [t.strip() for t in targets.split(",") if t.strip()]
        self._channels = [grpc.insecure_channel(t, options=NODE_OPTIONS) for t in targets]
        self._stubs = [pb_grpc.DynaStoreStub(c) for c in self._channels]
        self._next = itertools.cycle(range(len(self._stubs)))
        self._timeout = timeout
        self._suspect_seconds = suspect_seconds
        self._suspect_until = [0.0] * len(self._stubs)

    def _order(self) -> list[int]:
        """This call's nodes: healthy ones first (rotating), recently failed ones last."""
        start = next(self._next)
        ring = [(start + i) % len(self._stubs) for i in range(len(self._stubs))]
        now = time.monotonic()
        return ([i for i in ring if self._suspect_until[i] <= now]
                + [i for i in ring if self._suspect_until[i] > now])

    def _call(self, method: str, request):
        last: Exception | None = None
        for i in self._order():
            try:
                result = getattr(self._stubs[i], method)(request, timeout=self._timeout)
            except grpc.RpcError as e:
                last = e
                failed = e.code() in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED)
                if failed:
                    self._suspect_until[i] = time.monotonic() + self._suspect_seconds
                retry = e.code() == grpc.StatusCode.UNAVAILABLE or (
                    e.code() == grpc.StatusCode.DEADLINE_EXCEEDED and method in _SAFE_TO_REPEAT)
                if not retry:
                    break
                continue
            self._suspect_until[i] = 0.0
            return result
        raise DynaUnavailable(str(last.details() if last else "no nodes")) from last

    # ------------------------------------------------------------ registers

    def get(self, key: str, r: int = 0) -> Value:
        res = self._call("Get", pb.GetRequest(key=key, r=r))
        return Value(res.found, res.type, res.value, list(res.siblings), res.context, res.counter)

    def put(self, key: str, value: bytes | str | dict | list, *, context: str = "", ttl: int = 0,
            w: int = 0) -> str:
        res = self._call("Put", pb.PutRequest(key=key, value=_encode(value), context=context,
                                              ttl_seconds=ttl, w=w))
        return res.context

    def put_if_absent(self, key: str, value: bytes | str | dict | list, *, ttl: int = 0, r: int = 0,
                      w: int = 0) -> tuple[bool, bytes]:
        res = self._call("PutIfAbsent", pb.PutIfAbsentRequest(key=key, value=_encode(value),
                                                              ttl_seconds=ttl, r=r, w=w))
        return res.created, res.value

    # ------------------------------------------------------------ counters

    def incr(self, key: str, delta: int = 1, *, ttl: int = 0, w: int = 0) -> int:
        return self._call("Increment", pb.IncrementRequest(key=key, delta=delta, ttl_seconds=ttl, w=w)).value

    def counter(self, key: str, r: int = 0) -> int:
        return self._call("Get", pb.GetRequest(key=key, r=r)).counter

    # ------------------------------------------------------------ introspection

    def status(self):
        return self._call("ClusterStatus", pb.ClusterStatusRequest())

    def key_info(self, key: str):
        return self._call("KeyInfo", pb.KeyInfoRequest(key=key))

    def close(self) -> None:
        for c in self._channels:
            c.close()


def _encode(value) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode()
    return json.dumps(value, separators=(",", ":"), default=str).encode()
