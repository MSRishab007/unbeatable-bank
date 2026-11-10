from __future__ import annotations

from concurrent import futures

import grpc
from ubcommon import grpcutil

from ubcommon.metrics import GrpcMetricsInterceptor
from ubproto.risk.v1 import risk_pb2 as pb
from ubproto.risk.v1 import risk_pb2_grpc as pb_grpc

from .engine import Engine, Payment


class RiskServicer(pb_grpc.RiskServiceServicer):
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def Evaluate(self, request, context):
        result = self.engine.evaluate(Payment(
            txn_id=request.txn_id, customer_id=request.customer_id, from_account_id=request.from_account_id,
            to_account_id=request.to_account_id, amount=request.amount, channel=request.channel,
            device_id=request.device_id, ip=request.ip, payee_trusted=request.payee_trusted,
            account_idle_days=request.account_idle_days, event_time_ms=request.event_time_ms))
        return pb.EvaluateResponse(
            score=result.score, decision=result.decision, reasons=result.reasons, degraded=result.degraded,
            hits=[pb.RuleHit(rule=h.rule, weight=h.weight, shadow=h.shadow, detail=h.detail) for h in result.hits])


def start(engine: Engine, port: int) -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=32), options=grpcutil.SERVER_OPTIONS,
                         interceptors=[GrpcMetricsInterceptor("risk")])
    pb_grpc.add_RiskServiceServicer_to_server(RiskServicer(engine), server)
    server.bound_port = server.add_insecure_port(f"[::]:{port}")
    server.start()
    return server
