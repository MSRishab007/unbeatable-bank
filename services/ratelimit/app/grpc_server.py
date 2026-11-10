from __future__ import annotations

from concurrent import futures

import grpc
from ubcommon import grpcutil

from ubcommon.metrics import GrpcMetricsInterceptor
from ubproto.ratelimit.v1 import ratelimit_pb2 as pb
from ubproto.ratelimit.v1 import ratelimit_pb2_grpc as pb_grpc

from .limiter import Limiter


class RateLimitServicer(pb_grpc.RateLimitServiceServicer):
    def __init__(self, limiter: Limiter) -> None:
        self.limiter = limiter

    def Check(self, request, context):
        d = self.limiter.check(request.policy_set, [(x.scope, x.id) for x in request.descriptors],
                               request.cost, request.amount)
        return pb.CheckResponse(allowed=d.allowed, limited_by=d.limited_by, limit=d.limit,
                                remaining=d.remaining, retry_after_seconds=d.retry_after, degraded=d.degraded)


def start(limiter: Limiter, port: int) -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=32), options=grpcutil.SERVER_OPTIONS,
                         interceptors=[GrpcMetricsInterceptor("ratelimit")])
    pb_grpc.add_RateLimitServiceServicer_to_server(RateLimitServicer(limiter), server)
    server.bound_port = server.add_insecure_port(f"[::]:{port}")
    server.start()
    return server
