"""gRPC front of a node: the client API, the replica API and fault injection."""

from __future__ import annotations

import json
import logging
from concurrent import futures

import grpc

from ubproto.dynastore.v1 import dynastore_pb2 as pb
from ubproto.dynastore.v1 import dynastore_pb2_grpc as pb_grpc

from . import crdt
from .node import OPS, Node, NodeDown, QuorumError

log = logging.getLogger("dynastore.grpc")

FORWARDED = ("x-dynastore-forwarded", "1")
_RETRY_ELSEWHERE = {grpc.StatusCode.UNAVAILABLE}


class ClientApi(pb_grpc.DynaStoreServicer):
    """Any node accepts any request.

    Writes are coordinated by the first healthy node in the key's preference
    list, as in Dynamo: while the cluster is healthy all writes to a key pass
    through one node, which keeps counters exact and version histories linear.
    If that node can't be reached the next one takes over, and if none can,
    the node that received the request coordinates it itself. Reads can be
    coordinated by any replica of the key.
    """

    def __init__(self, node: Node) -> None:
        self.node = node

    # -- plumbing ---------------------------------------------------------

    def _run(self, op: str, key: str, request, context, local, write: bool = True):
        node = self.node
        try:
            node.check_inbound()
        except NodeDown:
            context.abort(grpc.StatusCode.UNAVAILABLE, "node is down")
        forwarded = FORWARDED in context.invocation_metadata()
        if not forwarded:
            candidates = node.coordinator_candidates(key)
            if not write and node.id in candidates:
                candidates = []
            for candidate in candidates:
                if candidate == node.id:
                    break  # we are the best available coordinator
                try:
                    stub = pb_grpc.DynaStoreStub(node._channel(candidate))
                    return getattr(stub, op)(request, timeout=node.rpc_timeout * 6, metadata=(FORWARDED,))
                except grpc.RpcError as e:
                    if e.code() in _RETRY_ELSEWHERE:
                        node._mark(candidate, ok=False)
                        continue
                    OPS.labels(node.id, op, "error").inc()
                    context.abort(e.code(), e.details())
        try:
            result = local()
            OPS.labels(node.id, op, "ok").inc()
            return result
        except QuorumError as e:
            OPS.labels(node.id, op, "no_quorum").inc()
            # ABORTED, not UNAVAILABLE: the write may be on some replicas, so callers must not blindly retry.
            context.abort(grpc.StatusCode.ABORTED, str(e))

    # -- operations -------------------------------------------------------

    def Get(self, request, context):
        def local():
            env, replicas, repaired = self.node.get(request.key, request.r)
            if env is None:
                return pb.GetResponse(found=False, replicas=replicas)
            if env["t"] == "c":
                return pb.GetResponse(found=True, type="counter", counter=crdt.counter_value(env),
                                      replicas=replicas, repaired=repaired)
            return pb.GetResponse(found=True, type="register", value=crdt.winner(env) or b"",
                                  siblings=crdt.sibling_values(env),
                                  context=json.dumps(crdt.merged_clock(env), sort_keys=True),
                                  replicas=replicas, repaired=repaired)
        return self._run("Get", request.key, request, context, local, write=False)

    def Put(self, request, context):
        def local():
            ctx, acks, sloppy = self.node.put(request.key, request.value, request.context,
                                              request.ttl_seconds, request.w)
            return pb.PutResponse(context=ctx, acks=acks, sloppy=sloppy)
        return self._run("Put", request.key, request, context, local)

    def PutIfAbsent(self, request, context):
        def local():
            created, value = self.node.put_if_absent(request.key, request.value, request.ttl_seconds,
                                                     request.r, request.w)
            return pb.PutIfAbsentResponse(created=created, value=value)
        return self._run("PutIfAbsent", request.key, request, context, local)

    def Increment(self, request, context):
        def local():
            value, acks, sloppy = self.node.increment(request.key, request.delta or 1,
                                                      request.ttl_seconds, request.w)
            return pb.IncrementResponse(value=value, acks=acks, sloppy=sloppy)
        return self._run("Increment", request.key, request, context, local)

    def ClusterStatus(self, request, context):
        s = self.node.status()
        return pb.ClusterStatusResponse(
            node_id=s["node_id"], nodes=[pb.NodeInfo(**n) for n in s["nodes"]], n=s["n"], r=s["r"], w=s["w"],
            keys=s["keys"], pending_hints=s["pending_hints"], stats=s["stats"],
            blocked_peers=s["blocked_peers"], simulated_down=s["simulated_down"])

    def KeyInfo(self, request, context):
        info = self.node.key_info(request.key)
        return pb.KeyInfoResponse(preference_list=info["preference_list"],
                                  replicas=[pb.ReplicaState(**r) for r in info["replicas"]])


class ReplicaApi(pb_grpc.ReplicaServicer):
    def __init__(self, node: Node) -> None:
        self.node = node

    def _guard(self, from_node: str, context) -> None:
        try:
            self.node.check_inbound(from_node)
        except NodeDown:
            context.abort(grpc.StatusCode.UNAVAILABLE, "unreachable")

    def Ping(self, request, context):
        self._guard(request.from_node, context)
        return pb.PingResponse(node_id=self.node.id)

    def ReplicaGet(self, request, context):
        self._guard(request.from_node, context)
        env = self.node.storage.get(request.key)
        return pb.ReplicaGetResponse(found=env is not None, envelope=json.dumps(env) if env else "")

    def ReplicaMerge(self, request, context):
        self._guard(request.from_node, context)
        self.node.replica_merge(request.key, request.envelope, request.hint_for)
        return pb.ReplicaMergeResponse(ok=True)

    def Sync(self, request, context):
        self._guard(request.from_node, context)
        updates, want = self.node.replica_sync([(d.key, d.digest) for d in request.digests])
        return pb.SyncResponse(updates=[pb.KeyEnvelope(key=k, envelope=json.dumps(e)) for k, e in updates],
                               want=want)


class AdminApi(pb_grpc.AdminServicer):
    def __init__(self, node: Node) -> None:
        self.node = node

    def SetFaults(self, request, context):
        self.node.set_faults(list(request.blocked_peers), request.down)
        return pb.SetFaultsResponse(ok=True)


def start(node: Node, port: int) -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=48, thread_name_prefix=f"{node.id}-grpc"))
    pb_grpc.add_DynaStoreServicer_to_server(ClientApi(node), server)
    pb_grpc.add_ReplicaServicer_to_server(ReplicaApi(node), server)
    pb_grpc.add_AdminServicer_to_server(AdminApi(node), server)
    server.bound_port = server.add_insecure_port(f"[::]:{port}")
    server.start()
    return server
