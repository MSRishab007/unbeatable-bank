import uuid

import grpc
import pytest

from app import grpc_server, internal_accounts
import app.gen  # noqa: F401  (puts generated code on sys.path)
from ledger.v1 import ledger_pb2, ledger_pb2_grpc


@pytest.fixture(scope="module")
def stub(pool):
    server = grpc_server.start(pool, 0)
    channel = grpc.insecure_channel(f"localhost:{server.bound_port}")
    yield ledger_pb2_grpc.LedgerServiceStub(channel)
    channel.close()
    server.stop(0)


def leg(account_id, amount):
    return ledger_pb2.Leg(account_id=str(account_id), amount=amount)


def test_post_and_replay_over_grpc(stub, make_account):
    a, b = make_account(10_000), make_account()
    req = ledger_pb2.PostJournalRequest(idempotency_key=str(uuid.uuid4()), txn_type="TRANSFER",
                                        legs=[leg(a, -2_500), leg(b, 2_500)], metadata={"channel": "test"})
    first, second = stub.PostJournal(req), stub.PostJournal(req)
    assert not first.replayed and second.replayed and first.journal_id == second.journal_id
    balance = stub.GetBalance(ledger_pb2.GetBalanceRequest(account_id=str(a)))
    assert balance.ledger_balance == 7_500 and balance.status == "OPEN"


def test_error_codes_over_grpc(stub, make_account):
    a, b = make_account(100), make_account()
    with pytest.raises(grpc.RpcError) as exc:
        stub.PostJournal(ledger_pb2.PostJournalRequest(
            idempotency_key=str(uuid.uuid4()), txn_type="TRANSFER", legs=[leg(a, -101), leg(b, 101)]))
    assert exc.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert dict(exc.value.trailing_metadata())["error-code"] == "INSUFFICIENT_FUNDS"

    with pytest.raises(grpc.RpcError) as exc:
        stub.GetBalance(ledger_pb2.GetBalanceRequest(account_id=str(uuid.uuid4())))
    assert exc.value.code() == grpc.StatusCode.NOT_FOUND

    with pytest.raises(grpc.RpcError) as exc:
        stub.GetBalance(ledger_pb2.GetBalanceRequest(account_id="not-a-uuid"))
    assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_internal_account_reachable(stub):
    assert stub.GetBalance(ledger_pb2.GetBalanceRequest(account_id=str(internal_accounts.CASH))).status == "OPEN"
