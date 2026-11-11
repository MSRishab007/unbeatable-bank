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


def test_staff_search_finds_a_customer_by_any_identifier(conn, ledger, make_customer):
    cid = make_customer()
    other = make_customer()
    me = service.get_customer(conn, cid)
    account = service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=[cid], actor=ACTOR)

    def found(query):
        return [c["customer_id"] for c in service.search_customers(conn, query)]

    assert found(me["cif_number"].lower()) == [cid]
    assert found(me["email"].upper()) == [cid]
    assert found(me["phone"]) == [cid]
    assert found(account["account_number"]) == [cid]
    assert found(str(cid)) == [cid]
    assert cid in found(me["full_name"][:4].lower())
    assert found("no-such-person") == []
    assert found("%") == []                       # wildcards are data, not patterns
    assert "pan_hash" not in service.search_customers(conn, me["email"])[0]

    waiting = make_customer(verified=False)
    pending = [c["customer_id"] for c in service.search_customers(conn, "", kyc_status="PENDING")]
    assert pending == [waiting] and other not in pending


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
    with pytest.raises(service.AccountError) as exc:
        service.add_beneficiary(conn, make_customer(), payee_account_number="500100000421")
    assert "check digit" in str(exc.value)


def test_alias_for_scan_to_pay(conn, ledger, make_customer):
    acct = service.open_account(conn, ledger, product_type="SAVINGS",
                                holder_ids=[make_customer("Riya Sharma")], actor=ACTOR)
    alias = f"riya{acct['account_number'][-4:]}"
    service.create_alias(conn, acct["account_id"], alias)
    resolved = service.resolve_alias(conn, f"{alias}@unbeatable")
    assert resolved["account_id"] == acct["account_id"] and resolved["display_name"] == "Riya S*****"
    with pytest.raises(service.AccountError):
        service.create_alias(conn, acct["account_id"], "x")


# ------------------------------------------------------------------ later additions


def test_open_with_supplied_id_is_idempotent(conn, ledger, make_customer):
    import uuid
    cid, account_id = make_customer(), uuid.uuid4()
    first = service.open_account(conn, ledger, product_type="FD", holder_ids=[cid], account_id=account_id,
                                 actor="system:deposits")
    again = service.open_account(conn, ledger, product_type="FD", holder_ids=[cid], account_id=account_id,
                                 actor="system:deposits")
    assert first["account_number"] == again["account_number"]
    assert len(service.list_customer_accounts(conn, cid)) == 1


def test_idle_days_and_touch(conn, ledger, make_customer):
    cid = make_customer()
    acct = service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=[cid], actor=ACTOR)
    conn.execute("UPDATE account.accounts SET last_activity_at = now() - interval '120 days' "
                 "WHERE account_id = %s", (acct["account_id"],))
    assert service.authorize(conn, acct["account_id"], cid)["idle_days"] == 120            # read-only
    assert service.authorize(conn, acct["account_id"], cid, touch=True)["idle_days"] == 120  # reports, then resets
    assert service.authorize(conn, acct["account_id"], cid)["idle_days"] == 0


def test_customer_sets_own_limits(conn, ledger, make_customer):
    acct = service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=[make_customer()],
                                actor=ACTOR)
    updated = service.update_limits(conn, acct["account_id"], daily_limit=2_000_000, per_txn_limit=500_000,
                                    actor=ACTOR)
    assert (updated["daily_limit"], updated["per_txn_limit"]) == (2_000_000, 500_000)   # lowering: at once
    assert updated["pending_limits_at"] is None
    with pytest.raises(service.AccountError):  # per-transaction above daily
        service.update_limits(conn, acct["account_id"], daily_limit=100, per_txn_limit=None, actor=ACTOR)
    assert service.list_events(conn, acct["account_id"])[0]["event_type"] == "LIMITS_CHANGED"
    assert service.verify_event_chain(conn, acct["account_id"])


def _make_due(conn, account_id):
    conn.execute("UPDATE account.accounts SET pending_limits_at = now() - interval '1 second' "
                 "WHERE account_id = %s", (account_id,))


def test_raising_a_limit_waits_a_day(conn, ledger, make_customer):
    cid = make_customer()
    acct = service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=[cid], actor=ACTOR)
    before = (acct["daily_limit"], acct["per_txn_limit"])
    raised = service.update_limits(conn, acct["account_id"], daily_limit=30_000_000, per_txn_limit=None, actor=ACTOR)
    assert (raised["daily_limit"], raised["per_txn_limit"]) == before                 # not yet
    assert raised["pending_daily_limit"] == 30_000_000 and raised["pending_limits_at"] is not None
    assert service.authorize(conn, acct["account_id"], cid)["daily_limit"] == before[0]
    # a raise and a cut together: the cut is immediate, the raise waits
    mixed = service.update_limits(conn, acct["account_id"], daily_limit=None, per_txn_limit=1_000_000, actor=ACTOR)
    assert mixed["per_txn_limit"] == 1_000_000 and mixed["pending_daily_limit"] == 30_000_000
    assert mixed["pending_limits_at"] == raised["pending_limits_at"]                 # the wait doesn't restart
    _make_due(conn, acct["account_id"])
    assert service.authorize(conn, acct["account_id"], cid)["daily_limit"] == 30_000_000   # applied when due
    now = service.get_account(conn, acct["account_id"])
    assert now["pending_daily_limit"] is None and now["pending_limits_at"] is None
    assert [e["event_type"] for e in service.list_events(conn, acct["account_id"])][:3] == \
        ["LIMITS_RAISED", "LIMITS_CHANGED", "LIMITS_CHANGED"]
    assert service.verify_event_chain(conn, acct["account_id"])


def test_lowering_a_limit_cancels_a_waiting_raise(conn, ledger, make_customer):
    acct = service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=[make_customer()], actor=ACTOR)
    service.update_limits(conn, acct["account_id"], daily_limit=30_000_000, per_txn_limit=None, actor=ACTOR)
    cut = service.update_limits(conn, acct["account_id"], daily_limit=5_000_000, per_txn_limit=None, actor=ACTOR)
    assert cut["daily_limit"] == 5_000_000 and cut["pending_daily_limit"] is None and cut["pending_limits_at"] is None
    with pytest.raises(service.AccountError):        # a waiting per-txn raise above the daily limit
        service.update_limits(conn, acct["account_id"], daily_limit=None, per_txn_limit=9_000_000, actor=ACTOR)


# ------------------------------------------------------------------ nominees, payees, contact


def test_nominees(conn, ledger, make_customer):
    from datetime import date as d
    acct = service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=[make_customer()], actor=ACTOR)
    aid = acct["account_id"]
    first = service.add_nominee(conn, aid, full_name="Meera Sharma", relation="spouse", date_of_birth=d(1975, 3, 2),
                                share_pct=60, guardian_name=None, actor=ACTOR)
    assert first["relation"] == "SPOUSE"
    with pytest.raises(service.AccountError, match="guardian"):
        service.add_nominee(conn, aid, full_name="Kabir Sharma", relation="CHILD", date_of_birth=d(2020, 1, 1),
                            share_pct=40, guardian_name=None, actor=ACTOR)
    with pytest.raises(service.AccountError, match="100%"):
        service.add_nominee(conn, aid, full_name="Kabir Sharma", relation="CHILD", date_of_birth=d(2020, 1, 1),
                            share_pct=50, guardian_name="Meera Sharma", actor=ACTOR)
    with pytest.raises(service.AccountError, match="relation"):
        service.add_nominee(conn, aid, full_name="Ravi", relation="FRIEND", date_of_birth=None, share_pct=10,
                            guardian_name=None, actor=ACTOR)
    service.add_nominee(conn, aid, full_name="Kabir Sharma", relation="CHILD", date_of_birth=d(2020, 1, 1),
                        share_pct=40, guardian_name="Meera Sharma", actor=ACTOR)
    assert sum(n["share_pct"] for n in service.list_nominees(conn, aid)) == 100
    service.remove_nominee(conn, aid, first["nominee_id"], ACTOR)
    assert [n["full_name"] for n in service.list_nominees(conn, aid)] == ["Kabir Sharma"]
    with pytest.raises(service.NotFound):
        service.remove_nominee(conn, aid, first["nominee_id"], ACTOR)
    types = [e["event_type"] for e in service.list_events(conn, aid)]
    assert types[:3] == ["NOMINEE_REMOVED", "NOMINEE_ADDED", "NOMINEE_ADDED"]


def test_remove_beneficiary(conn, ledger, make_customer):
    payer = make_customer()
    payee = service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=[make_customer("Arjun Mehta")],
                                 actor=ACTOR)
    b = service.add_beneficiary(conn, payer, payee_account_number=payee["account_number"], nickname="Arjun")
    with pytest.raises(service.NotFound):
        service.remove_beneficiary(conn, make_customer(), b["beneficiary_id"])      # someone else's
    service.remove_beneficiary(conn, payer, b["beneficiary_id"])
    assert service.list_beneficiaries(conn, payer) == []
    again = service.add_beneficiary(conn, payer, payee_account_number=payee["account_number"])   # can re-add
    assert again["beneficiary_id"] != b["beneficiary_id"]


def test_update_contact(conn, make_customer):
    a, b = make_customer(), make_customer()
    updated = service.update_contact(conn, a, email="New.Address@Example.com")
    assert updated["email"] == "new.address@example.com"
    with pytest.raises(service.Conflict):
        service.update_contact(conn, b, email="new.address@example.com")


def test_payment_precheck(conn, ledger, make_customer):
    payer, friend = make_customer(), make_customer("Arjun Mehta")
    mine = service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=[payer], actor=ACTOR)
    mine2 = service.open_account(conn, ledger, product_type="CURRENT", holder_ids=[payer], actor=ACTOR)
    theirs = service.open_account(conn, ledger, product_type="SAVINGS", holder_ids=[friend], actor=ACTOR)

    def check(payee):
        return service.precheck_payment(conn, customer_id=payer, from_account_id=mine["account_id"],
                                        payee_ref=payee)

    own = check(mine2["account_number"])
    assert own["auth"]["allowed"] and own["payee_trusted"]                      # own account
    stranger = check(theirs["account_number"])
    assert stranger["payee"]["display_name"] == "Arjun M****" and not stranger["payee_trusted"]
    service.add_beneficiary(conn, payer, payee_account_number=theirs["account_number"])
    assert not check(theirs["account_number"])["payee_trusted"]                 # saved, but still cooling
    conn.execute("UPDATE account.beneficiaries SET cooling_until = now() - interval '1 minute' "
                 "WHERE customer_id = %s", (payer,))
    assert check(theirs["account_number"])["payee_trusted"]                     # cooling period over
    typo = theirs["account_number"][:-1] + str((int(theirs["account_number"][-1]) + 1) % 10)
    assert check(typo)["payee_error"] == "INVALID_PAYEE"
    assert check("nobody@unbeatable")["payee_error"] == "PAYEE_NOT_FOUND"
    assert service.precheck_payment(conn, customer_id=friend, from_account_id=mine["account_id"],
                                    payee_ref=theirs["account_number"])["auth"]["reason"] == "NOT_A_HOLDER"
