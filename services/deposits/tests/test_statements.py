"""Statements: the right entries and balances, issued once, checkable against the ledger and by file."""

import hashlib
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from ubcommon.auth import Actor

from app import service, statements

CASH = uuid.UUID("00000000-0000-0000-0000-000000000001")
RS = 100


def set_clock(conn, today: date) -> None:
    conn.execute("UPDATE deposits.bank_clock SET business_date = %s", (today,))


def move(deps, account, rupees, on, description="Payment", txn_type="TRANSFER"):
    deps.ledger.post_journal(idempotency_key=str(uuid.uuid4()), txn_type=txn_type, description=description,
                             legs=[(CASH, -rupees * RS), (account, rupees * RS)], business_date=on)


@pytest.fixture
def riya(conn, deps):
    """A customer whose account opened on 20 Sep 2026 and has entries in September, October and November."""
    customer = deps.accounts.customer()
    account = deps.accounts.savings(customer)
    deps.accounts.opened[account] = "2026-09-20T09:30:00+00:00"
    move(deps, account, 50_000, date(2026, 9, 28), "Cash deposit", "DEPOSIT")
    move(deps, account, -12_000, date(2026, 10, 3), "Rent to Arjun M****")
    move(deps, account, 2_500, date(2026, 10, 15), "Refund")
    move(deps, account, -700, date(2026, 10, 31), "Chai ₹ and समोसा")           # not Latin-1: must not break the PDF
    move(deps, account, -1_000, date(2026, 11, 2), "November spend")
    set_clock(conn, date(2026, 11, 10))
    return {"customer": customer, "account": account, "actor": Actor("customer", str(customer))}


def pdf_text(data: bytes) -> str:
    import io

    from pypdf import PdfReader

    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def tampered(data: bytes) -> bytes:
    """The same PDF with one byte changed (as an edited statement would be)."""
    middle = len(data) // 2
    return data[:middle] + bytes([data[middle] ^ 0x01]) + data[middle + 1:]


def stored(deps):
    return sorted(str(p.relative_to(deps.objstore._root)) for p in deps.objstore._root.rglob("*.pdf"))


# ------------------------------------------------------------------ periods


def test_only_completed_months_since_opening_are_offered(conn, deps, riya):
    listing = statements.periods(conn, deps, riya["account"])
    assert [p["period"] for p in listing["periods"]] == ["2026-10", "2026-09"]       # not November: it isn't over
    assert all(p["issued"] is False for p in listing["periods"])
    set_clock(conn, date(2026, 12, 1))
    assert [p["period"] for p in statements.periods(conn, deps, riya["account"])["periods"]] == ["2026-11", "2026-10", "2026-09"]


@pytest.mark.parametrize("period, reason", [("2026-11", "isn't over"), ("2026-08", "wasn't open"),
                                            ("2026-13", "YYYY-MM"), ("oct", "YYYY-MM"), ("2026-1", "YYYY-MM")])
def test_periods_that_cannot_have_a_statement(conn, deps, riya, period, reason):
    with pytest.raises(statements.InvalidPeriod, match=reason):
        statements.monthly(conn, deps, riya["account"], period)
    assert stored(deps) == []


# ------------------------------------------------------------------ monthly statements


def test_monthly_statement_has_the_months_entries_and_balances(conn, deps, riya):
    data, name = statements.monthly(conn, deps, riya["account"], "2026-10")
    assert data.startswith(b"%PDF-") and name.endswith("-2026-10.pdf")
    text = pdf_text(data)
    for expected in ("Unbeatable Bank", "Statement of account", "Riya Sharma", "1 Oct 2026 to 31 Oct 2026",
                     "Rent to Arjun M****", "12,000.00", "Refund", "2,500.00", "Chai Rs", "?????",
                     "50,000.00",        # opening balance carried from September
                     "39,800.00",        # closing: 50,000 - 12,000 + 2,500 - 700
                     "12,700.00",        # total withdrawals
                     "Fingerprint", "Page 1 of 1"):
        assert expected in text, expected
    assert "November spend" not in text and "Cash deposit" not in text

    row = conn.execute("SELECT opening_balance, closing_balance, entry_count, pdf_sha256, object_path, issued_on "
                       "FROM deposits.statements WHERE account_id = %s", (riya["account"],)).fetchone()
    assert row[:3] == (50_000 * RS, 39_800 * RS, 3) and row[5] == date(2026, 11, 10)
    assert row[3] == hashlib.sha256(data).hexdigest() and stored(deps) == [row[4]]
    assert statements.periods(conn, deps, riya["account"])["periods"][0] == {
        "period": "2026-10", "label": "October 2026", "issued": True, "closing_balance": 39_800 * RS, "entry_count": 3}


def test_a_statement_is_issued_once_and_served_from_storage_afterwards(conn, deps, riya):
    first, _ = statements.monthly(conn, deps, riya["account"], "2026-10")
    calls = []
    original = deps.ledger.statement
    deps.ledger.statement = lambda *a: calls.append(a) or original(*a)
    set_clock(conn, date(2027, 3, 1))                                 # months later
    again, _ = statements.monthly(conn, deps, riya["account"], "2026-10")
    assert again == first and calls == []                              # the same file, without asking the ledger
    assert conn.execute("SELECT count(*) FROM deposits.statements").fetchone()[0] == 1


def test_a_lost_file_is_rebuilt_identically(conn, deps, riya):
    first, _ = statements.monthly(conn, deps, riya["account"], "2026-10")
    deps.objstore.delete(stored(deps)[0])
    set_clock(conn, date(2027, 1, 15))
    rebuilt, _ = statements.monthly(conn, deps, riya["account"], "2026-10")
    assert rebuilt == first                                            # byte for byte: same fingerprint, same issue date
    assert statements.verify(conn, deps, riya["account"], "2026-10")["valid"] is True


def test_months_join_up(conn, deps, riya):
    set_clock(conn, date(2026, 12, 3))
    texts = {p: pdf_text(statements.monthly(conn, deps, riya["account"], p)[0]) for p in ("2026-09", "2026-10", "2026-11")}
    assert "Cash deposit" in texts["2026-09"] and "38,800.00" in texts["2026-11"]      # 39,800 - 1,000
    rows = dict(conn.execute("SELECT period, (opening_balance, closing_balance) FROM deposits.statements").fetchall())
    assert rows["2026-09"][1] == rows["2026-10"][0] and rows["2026-10"][1] == rows["2026-11"][0]


def test_a_long_statement_runs_over_pages(conn, deps, riya):
    for i in range(90):
        move(deps, riya["account"], -1, date(2026, 10, 1 + i % 28), f"Small payment {i}")
    data, _ = statements.monthly(conn, deps, riya["account"], "2026-10")
    text = pdf_text(data)
    assert "Page 3 of 3" in text and text.count("Particulars") == 3     # the header repeats on every page
    assert "Small payment 89" in text and "Closing balance" in text


def test_an_account_with_no_activity_still_gets_a_statement(conn, deps):
    customer = deps.accounts.customer()
    account = deps.accounts.savings(customer)
    deps.accounts.opened[account] = "2026-09-01T00:00:00+00:00"
    set_clock(conn, date(2026, 11, 10))
    text = pdf_text(statements.monthly(conn, deps, account, "2026-10")[0])
    assert "No transactions in this period." in text


# ------------------------------------------------------------------ interim statements


def test_interim_statement_for_a_date_range_is_not_registered(conn, deps, riya):
    data, name = statements.interim(conn, deps, riya["account"], date(2026, 10, 10), date(2026, 11, 10))
    text = pdf_text(data)
    assert "Interim statement" in text and "cannot be verified" in text and "Fingerprint" not in text
    assert "Refund" in text and "November spend" in text and "Rent to Arjun" not in text
    assert "37,500.00" in text or "38,000.00" in text                  # opening 38,000 after the 3 Oct rent
    assert stored(deps) == [] and conn.execute("SELECT count(*) FROM deposits.statements").fetchone()[0] == 0
    assert statements.verify_file(conn, deps, data)["genuine"] is False


@pytest.mark.parametrize("start, end, reason", [
    (date(2026, 11, 5), date(2026, 11, 1), "after the end"),
    (date(2026, 11, 1), date(2026, 11, 11), "after today"),
    (date(2025, 1, 1), date(2026, 11, 1), "one year"),
])
def test_interim_range_limits(conn, deps, riya, start, end, reason):
    with pytest.raises(statements.InvalidPeriod, match=reason):
        statements.interim(conn, deps, riya["account"], start, end)


# ------------------------------------------------------------------ checking a statement


def test_verify_passes_for_an_untouched_statement(conn, deps, riya):
    with pytest.raises(service.NotFound):
        statements.verify(conn, deps, riya["account"], "2026-10")       # nothing issued yet
    statements.monthly(conn, deps, riya["account"], "2026-10")
    result = statements.verify(conn, deps, riya["account"], "2026-10")
    assert (result["valid"], result["ledger_matches"], result["file_intact"], result["entry_count"]) == (True, True, True, 3)


def test_verify_notices_a_changed_file(conn, deps, riya):
    data, _ = statements.monthly(conn, deps, riya["account"], "2026-10")
    deps.objstore.put(stored(deps)[0], tampered(data), "application/pdf")
    result = statements.verify(conn, deps, riya["account"], "2026-10")
    assert (result["valid"], result["ledger_matches"], result["file_intact"]) == (False, True, False)


def test_verify_notices_a_ledger_that_no_longer_matches(conn, deps, riya):
    statements.monthly(conn, deps, riya["account"], "2026-10")
    move(deps, riya["account"], -5_000, date(2026, 10, 20), "Entry backdated into an issued month")
    result = statements.verify(conn, deps, riya["account"], "2026-10")
    assert (result["valid"], result["ledger_matches"], result["file_intact"]) == (False, False, True)


def test_anyone_holding_the_pdf_can_check_the_bank_issued_it(conn, deps, riya):
    data, _ = statements.monthly(conn, deps, riya["account"], "2026-10")
    result = statements.verify_file(conn, deps, data)
    assert result["genuine"] is True and result["period"] == "2026-10" and result["closing_balance"] == 39_800 * RS
    number = deps.accounts.account(riya["account"])["account_number"]
    assert result["account_number"] == "x" * 8 + number[-4:] and number not in str(result)   # masked
    edited = statements.verify_file(conn, deps, tampered(data))
    assert edited["genuine"] is False and "account_number" not in edited
    assert statements.verify_file(conn, deps, b"%PDF-1.4 something else entirely")["genuine"] is False
    with pytest.raises(statements.InvalidPeriod):
        statements.verify_file(conn, deps, b"<html>not a pdf</html>")


# ------------------------------------------------------------------ who may see statements


def test_holders_and_staff_only(conn, deps, riya):
    statements.check_access(deps, riya["account"], riya["actor"])
    statements.check_access(deps, riya["account"], Actor("staff", "teller1", "TELLER"))
    for outsider in (Actor("customer", str(uuid.uuid4())), Actor("system", "unknown")):
        with pytest.raises(service.NotFound):
            statements.check_access(deps, riya["account"], outsider)


@pytest.fixture
def api(db_url, deps, monkeypatch):
    from app import main

    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setitem(main._state, "deps", deps)
    with TestClient(main.app) as client:
        yield client


def test_over_http(api, riya):
    me = {"X-Actor": f"customer:{riya['customer']}"}
    base = f"/v1/accounts/{riya['account']}/statements"
    assert [p["period"] for p in api.get(base, headers=me).json()["periods"]] == ["2026-10", "2026-09"]
    pdf = api.get(f"{base}/2026-10", headers=me)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    assert pdf.headers["content-type"] == "application/pdf" and "2026-10.pdf" in pdf.headers["content-disposition"]
    assert pdf.headers["cache-control"] == "no-store" and pdf.headers["x-content-type-options"] == "nosniff"
    assert api.get(f"{base}/2026-10/verify", headers=me).json()["valid"] is True
    interim = api.get(f"{base}/interim?date_from=2026-11-01&date_to=2026-11-10", headers=me)
    assert interim.status_code == 200 and interim.content.startswith(b"%PDF-")
    assert api.get(f"{base}/2026-11", headers=me).status_code == 422
    assert api.get(f"{base}/interim?date_from=2026-11-01&date_to=2027-01-01", headers=me).status_code == 422

    stranger = {"X-Actor": f"customer:{uuid.uuid4()}"}
    assert api.get(base, headers=stranger).status_code == 404
    assert api.get(f"{base}/2026-10", headers=stranger).status_code == 404
    assert api.get(f"{base}/2026-10", headers={"X-Actor": "staff:teller1:TELLER"}).status_code == 200

    check = api.post("/v1/statements/verify-file", content=pdf.content)           # no login
    assert check.status_code == 200 and check.json()["genuine"] is True
    assert api.post("/v1/statements/verify-file", content=pdf.content + b" ").json()["genuine"] is False
