"""gRPC interface of the Ledger service (port 50051). Same posting engine as REST."""

from __future__ import annotations

import logging
from concurrent import futures
from uuid import UUID

import grpc
from ubcommon import grpcutil
from psycopg_pool import ConnectionPool

from ubcommon.metrics import GrpcMetricsInterceptor
from ubproto.ledger.v1 import ledger_pb2, ledger_pb2_grpc

from . import posting

log = logging.getLogger("ledger.grpc")

STATUS_FOR = {
    posting.InvalidJournal: grpc.StatusCode.INVALID_ARGUMENT,
    posting.AccountNotFound: grpc.StatusCode.NOT_FOUND,
    posting.NotFound: grpc.StatusCode.NOT_FOUND,
    posting.AccountNotOpen: grpc.StatusCode.FAILED_PRECONDITION,
    posting.InsufficientFunds: grpc.StatusCode.FAILED_PRECONDITION,
    posting.HoldError: grpc.StatusCode.FAILED_PRECONDITION,
    posting.IdempotencyConflict: grpc.StatusCode.ALREADY_EXISTS,
}


def _abort(context: grpc.ServicerContext, exc: posting.LedgerError):
    context.set_trailing_metadata((("error-code", exc.code),))
    context.abort(STATUS_FOR.get(type(exc), grpc.StatusCode.UNKNOWN), str(exc))


def _uuid(context: grpc.ServicerContext, value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        context.set_trailing_metadata((("error-code", "INVALID_ARGUMENT"),))
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"{field} is not a valid UUID")


class LedgerServicer(ledger_pb2_grpc.LedgerServiceServicer):
    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool

    def PostJournal(self, request, context):
        legs = [posting.Leg(_uuid(context, l.account_id, "account_id"), l.amount) for l in request.legs]
        try:
            with self.pool.connection() as conn:
                result = posting.post_journal(
                    conn,
                    idempotency_key=request.idempotency_key,
                    txn_type=request.txn_type,
                    legs=legs,
                    description=request.description or None,
                    metadata=dict(request.metadata),
                    capture_hold_id=(_uuid(context, request.capture_hold_id, "capture_hold_id")
                                     if request.capture_hold_id else None),
                )
        except posting.LedgerError as e:
            _abort(context, e)
        return ledger_pb2.PostJournalResponse(
            journal_id=str(result.journal_id),
            replayed=result.replayed,
            txn_type=result.txn_type,
            entries=[
                ledger_pb2.Entry(account_id=str(e["account_id"]), amount=e["amount"],
                                 balance_after=e["balance_after"])
                for e in result.entries
            ],
        )

    def GetBalance(self, request, context):
        account_id = _uuid(context, request.account_id, "account_id")
        try:
            with self.pool.connection() as conn:
                b = posting.get_balance(conn, account_id)
        except posting.LedgerError as e:
            _abort(context, e)
        return ledger_pb2.Balance(
            account_id=str(b["account_id"]), status=b["status"], ledger_balance=b["ledger_balance"],
            available_balance=b["available_balance"], overdraft_limit=b["overdraft_limit"],
        )


    def CreateHold(self, request, context):
        account_id = _uuid(context, request.account_id, "account_id")
        try:
            with self.pool.connection() as conn:
                hold = posting.create_hold(conn, account_id=account_id, amount=request.amount,
                                           idempotency_key=request.idempotency_key,
                                           reason=request.reason or "hold")
        except posting.LedgerError as e:
            _abort(context, e)
        return _hold(hold)

    def ReleaseHold(self, request, context):
        hold_id = _uuid(context, request.hold_id, "hold_id")
        try:
            with self.pool.connection() as conn:
                hold = posting.release_hold(conn, hold_id)
        except posting.LedgerError as e:
            _abort(context, e)
        return _hold(hold)


def _hold(hold: dict) -> ledger_pb2.Hold:
    return ledger_pb2.Hold(hold_id=str(hold["hold_id"]), account_id=str(hold["account_id"]),
                           amount=hold["amount"], status=hold["status"])


def start(pool: ConnectionPool, port: int, max_workers: int = 10) -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers), options=grpcutil.SERVER_OPTIONS,
                         interceptors=[GrpcMetricsInterceptor("ledger")])
    ledger_pb2_grpc.add_LedgerServiceServicer_to_server(LedgerServicer(pool), server)
    bound = server.add_insecure_port(f"[::]:{port}")
    server.start()
    log.info("gRPC listening on %s", bound)
    server.bound_port = bound  # useful when port=0 in tests
    return server
