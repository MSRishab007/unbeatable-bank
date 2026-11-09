"""gRPC interface of the Ledger service (port 50051). Same posting engine as REST."""

from __future__ import annotations

import logging
from concurrent import futures
from uuid import UUID

import grpc
from psycopg_pool import ConnectionPool

from . import gen  # noqa: F401  (puts the generated code on sys.path)
from . import posting
from ledger.v1 import ledger_pb2, ledger_pb2_grpc  # noqa: E402

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


def start(pool: ConnectionPool, port: int, max_workers: int = 10) -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    ledger_pb2_grpc.add_LedgerServiceServicer_to_server(LedgerServicer(pool), server)
    bound = server.add_insecure_port(f"[::]:{port}")
    server.start()
    log.info("gRPC listening on %s", bound)
    server.bound_port = bound  # useful when port=0 in tests
    return server
