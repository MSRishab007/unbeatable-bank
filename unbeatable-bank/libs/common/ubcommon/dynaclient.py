"""Client for DynaStore. Any node can serve any request, so the client just
rotates through the nodes it knows and fails over when one is unreachable."""

from __future__ import annotations

import itertools
import json
import os
from dataclasses import dataclass, field

import grpc

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


# UNAVAILABLE means the node never started the operation, so trying another node is safe.
# A timeout is ambiguous; only operations that are safe to repeat are retried after one.
_SAFE_TO_REPEAT = {"Get", "Put", "PutIfAbsent", "ClusterStatus", "KeyInfo"}


class DynaClient:
    def __init__(self, targets: str | list[str] | None = None, timeout: float = 2.0) -> None:
        targets = targets or os.environ["DYNASTORE_NODES"]
        if isinstance(targets, str):
            targets = [t.strip() for t in targets.split(",") if t.strip()]
        self._channels = [grpc.insecure_channel(t) for t in targets]
        self._stubs = [pb_grpc.DynaStoreStub(c) for c in self._channels]
        self._next = itertools.cycle(range(len(self._stubs)))
        self._timeout = timeout

    def _call(self, method: str, request):
        start = next(self._next)
        last: Exception | None = None
        for i in range(len(self._stubs)):
            stub = self._stubs[(start + i) % len(self._stubs)]
            try:
                return getattr(stub, method)(request, timeout=self._timeout)
            except grpc.RpcError as e:
                last = e
                retry = e.code() == grpc.StatusCode.UNAVAILABLE or (
                    e.code() == grpc.StatusCode.DEADLINE_EXCEEDED and method in _SAFE_TO_REPEAT)
                if not retry:
                    break
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
