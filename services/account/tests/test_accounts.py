from datetime import date

import psycopg
import pytest

from app import service
from app.numbers import is_valid_account_number, luhn_check_digit, make_account_number

ACTOR = "customer:test"


# ------------------------------------------------------------------ numbers


def test_luhn_known_value():
    assert luhn_check_digit("7992739871") == "3"  # textbook Luhn example


def test_account_numbers_are_valid_and_catch_typos():
    number = make_account_number(42)
    assert len(number) == 12 and is_valid_account_number(number)
    typo = number[:5] + str((int(number[5]) + 1) % 10) + number[6:]
    assert not is_valid_account_number(typo)


# ------------------------------------------------------------------ customers


def test_customer_identity_is_unique(conn, make_customer):
    make_customer(pan="ABCDE1234F")
    with pytest.raises(service.Conflict) as exc:
        make_customer(pan="abcde1234f")  # same PAN, different case
    assert exc.value.details["field"] == "pan"


def test_raw_pan_is_never_stored(conn, make_customer):
    cid = make_customer(pan="PQRST6789Z")
    row = conn.execute("SELECT pan_hash, pan_last4 FROM account.customers WHERE customer_id = %s",
                       (cid,)).fetchone()
    assert "PQRST6789Z" not in row[0] and row[1] == "6789Z"
    assert "pan_hash" not in service.get_customer(conn, cid)


def test_invalid_phone_rejected(conn):
    with pytest.raises(service.AccountError):
        service.create_customer(conn, full_name="X Y", date_of_birth=date(2000, 1, 1),
                                phone="12345", email="xy@example.com")


# ------------------------------------------------------------------ accounts


def test_customer_can_hold_many_account_types(conn, ledger, make_customer):
    cid = make_customer()
    service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=[cid], actor=ACTOR)
    service.open_account(conn, ledger, product_type="CURRENT", holder_ids=[cid],
                         overdraft_limit=1_000_000, actor=ACTOR)
    service.open_account(conn, ledger, product_type="FD", holder_ids=[cid], actor="system:deposits")
    types = sorted(a["product_type"] for a in service.list_customer_accounts(conn, cid))
    assert types == ["CURRENT", "FD", "SAVINGS"]


def test_open_creates_ledger_account(conn, ledger, make_customer):
    acct = service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=[make_customer()],
                                actor=ACTOR)
    assert acct["status"] == "ACTIVE"
    assert ledger.opened[acct["account_id"]] == ("SAVINGS", 0)
    assert is_valid_account_number(acct["account_number"])


def test_overdraft_only_on_current(conn, ledger, make_customer):
    with pytest.raises(service.AccountError):
        service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=[make_customer()],
                             overdraft_limit=100, actor=ACTOR)


def test_pending_kyc_account_is_blocked_until_verified(conn, ledger, make_customer):
    cid = make_customer(verified=False)
    acct = service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=[cid], actor=ACTOR)
    assert acct["status"] == "PENDING_KYC"
    assert ledger.status[acct["account_id"]] == "DEBIT_BLOCKED"
    service.verify_kyc(conn, ledger, cid, "staff:kyc-officer")
    assert service.get_account(conn, acct["account_id"])["status"] == "ACTIVE"
    assert ledger.status[acct["account_id"]] == "OPEN"


# ------------------------------------------------------------------ joint accounts


def test_joint_account(conn, ledger, make_customer):
    riya, arjun = make_customer("Riya Sharma"), make_customer("Arjun Mehta")
    acct = service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=[riya, arjun],
                                actor=ACTOR)
    assert acct["operating_mode"] == "EITHER_OR_SURVIVOR"
    roles = {h["customer_id"]: h["role"] for h in acct["holders"]}
    assert roles == {riya: "PRIMARY", arjun: "JOINT"}
    # The account appears for both people.
    assert acct["account_id"] in {a["account_id"] for a in service.list_customer_accounts(conn, arjun)}
    # Either holder may debit; a stranger may not.
    assert service.authorize(conn, acct["account_id"], arjun)["allowed"]
    assert service.authorize(conn, acct["account_id"], make_customer())["reason"] == "NOT_A_HOLDER"


def test_single_mode_needs_one_holder(conn, ledger, make_customer):
    with pytest.raises(service.AccountError):
        service.open_account(conn, ledger, product_type="SAVINGS",
                             holder_ids=[make_customer(), make_customer()], operating_mode="SINGLE",
                             actor=ACTOR)


def test_add_holder_converts_single_to_joint(conn, ledger, make_customer):
    riya, arjun = make_customer(), make_customer()
    acct = service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=[riya], actor=ACTOR)
    acct = service.add_holder(conn, acct["account_id"], arjun, ACTOR)
    assert acct["operating_mode"] == "EITHER_OR_SURVIVOR" and len(acct["holders"]) == 2
    with pytest.raises(service.Conflict):
        service.add_holder(conn, acct["account_id"], arjun, ACTOR)


def test_max_four_holders(conn, ledger, make_customer):
    ids = [make_customer() for _ in range(4)]
    acct = service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=ids, actor=ACTOR)
    with pytest.raises(service.Conflict):
        service.add_holder(conn, acct["account_id"], make_customer(), ACTOR)


def test_only_one_primary_enforced_by_db(conn, ledger, make_customer):
    acct = service.open_account(conn, ledger, product_type="SAVINGS",
                                holder_ids=[make_customer(), make_customer()], actor=ACTOR)
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("UPDATE account.account_holders SET role = 'PRIMARY' WHERE account_id = %s",
                     (acct["account_id"],))


# ------------------------------------------------------------------ freeze & authorisation


def test_freeze_blocks_ledger_first_and_authorize(conn, ledger, make_customer):
    cid = make_customer()
    acct = service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=[cid], actor=ACTOR)
    service.freeze_account(conn, ledger, acct["account_id"], reason="customer reported fraud", actor=ACTOR)
    assert ledger.status[acct["account_id"]] == "DEBIT_BLOCKED"
    assert service.authorize(conn, acct["account_id"], cid)["reason"] == "ACCOUNT_FROZEN"
    service.unfreeze_account(conn, ledger, acct["account_id"], actor="staff:analyst")
    assert ledger.status[acct["account_id"]] == "OPEN"
    assert service.authorize(conn, acct["account_id"], cid)["allowed"]


def test_deposit_accounts_not_directly_debitable(conn, ledger, make_customer):
    cid = make_customer()
    fd = service.open_account(conn, ledger, product_type="FD", holder_ids=[cid], actor="system:deposits")
    assert service.authorize(conn, fd["account_id"], cid)["reason"] == "DEPOSIT_ACCOUNT_NOT_DEBITABLE"


# ------------------------------------------------------------------ audit chain


def test_event_chain_detects_tampering(conn, ledger, make_customer):
    acct = service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=[make_customer()],
                                actor=ACTOR)
    service.freeze_account(conn, ledger, acct["account_id"], reason="test", actor=ACTOR)
    assert service.verify_event_chain(conn, acct["account_id"])
    # The table is append-only...
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("UPDATE account.account_events SET actor = 'evil' WHERE account_id = %s",
                     (acct["account_id"],))
    # ...and even a superuser bypassing the trigger is caught by the hash chain.
    with conn.transaction():
        conn.execute("ALTER TABLE account.account_events DISABLE TRIGGER account_events_append_only")
        conn.execute("UPDATE account.account_events SET actor = 'evil' WHERE account_id = %s",
                     (acct["account_id"],))
        conn.execute("ALTER TABLE account.account_events ENABLE TRIGGER account_events_append_only")
    assert not service.verify_event_chain(conn, acct["account_id"])


# ------------------------------------------------------------------ payees & aliases


def test_beneficiary_cooling_period_and_name_masking(conn, ledger, make_customer):
    payer = make_customer()
    payee_acct = service.open_account(conn, ledger, product_type="SAVINGS",
                                      holder_ids=[make_customer("Arjun Kumar Mehta")], actor=ACTOR)
    b = service.add_beneficiary(conn, payer, payee_account_number=payee_acct["account_number"])
    assert b["payee_name"] == "Arjun K**** M****"
    assert service.list_beneficiaries(conn, payer)[0]["in_cooling_period"]
    with pytest.raises(service.Conflict):
        service.add_beneficiary(conn, payer, payee_account_number=payee_acct["account_number"])


def test_beneficiary_rejects_typo(conn, make_customer):
    with pytest.raises(service.AccountError):
        service.add_beneficiary(conn, make_customer(), payee_account_number="500100000420")


def test_alias_for_scan_to_pay(conn, ledger, make_customer):
    acct = service.open_account(conn, ledger, product_type="SAVINGS",
                                holder_ids=[make_customer("Riya Sharma")], actor=ACTOR)
    alias = f"riya{acct['account_number'][-4:]}"
    service.create_alias(conn, acct["account_id"], alias)
    resolved = service.resolve_alias(conn, f"{alias}@unbeatable")
    assert resolved["account_id"] == acct["account_id"] and resolved["display_name"] == "Riya S*****"
    with pytest.raises(service.AccountError):
        service.create_alias(conn, acct["account_id"], "x")
