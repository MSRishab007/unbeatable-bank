"""Account statements as PDF files.

Monthly statements
    A completed month's statement is built the first time someone asks for it,
    stored in object storage (statements/<account>/<yyyy-mm>.pdf) and registered
    in deposits.statements. After that the stored file is served. (Building a PDF
    for every account at every month end would mostly produce files nobody opens.)

Checking a statement
    The register keeps two fingerprints for each statement:
      entries_hash  SHA-256 over the ledger entries it covered
      pdf_sha256    SHA-256 of the file that was issued
    `verify` recomputes the first from the ledger as it is now and re-hashes the
    stored file. `verify_file` takes a PDF someone presents (a lender, say) and
    says whether the bank issued exactly that file.

Interim statements
    Any date range up to today, built on request and not stored or registered.

PDFs are rendered with the built-in Helvetica font, which covers Latin-1 only:
other characters in a description print as "?".
"""

from __future__ import annotations

import calendar
import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import psycopg
from fpdf import FPDF
from fpdf.enums import XPos, YPos
from psycopg.rows import dict_row

from .service import Conflict, DepositError, Deps, NotFound, business_date

MAX_INTERIM_DAYS = 366
MAX_PERIODS_LISTED = 24
PRODUCT_NAMES = {"SAVINGS": "Savings account", "CURRENT": "Current account",
                 "FD": "Fixed deposit", "RD": "Recurring deposit"}
INK = (27, 42, 107)


class InvalidPeriod(DepositError):
    code = "INVALID_PERIOD"
    http_status = 422


def _cur(conn: psycopg.Connection) -> psycopg.Cursor:
    return conn.cursor(row_factory=dict_row)


def check_access(deps: Deps, account_id: UUID, actor) -> None:
    """Holders (even of a frozen account) and bank staff may see an account's statements."""
    if actor.kind == "staff":
        return
    if actor.customer_id is None or not deps.accounts.authorize(account_id, actor.customer_id).get("is_holder"):
        raise NotFound("account not found")           # someone else's looks like it doesn't exist


# ------------------------------------------------------------------ periods


def period_bounds(period: str) -> tuple[date, date]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period or ""):
        raise InvalidPeriod("Give the month as YYYY-MM, for example 2026-10.")
    year, month = int(period[:4]), int(period[5:])
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _opened_on(account: dict) -> date:
    return date.fromisoformat(str(account["opened_at"])[:10])


def periods(conn: psycopg.Connection, deps: Deps, account_id: UUID) -> dict:
    """Completed months since the account opened, newest first."""
    account = deps.accounts.account(account_id)
    today = business_date(conn)
    with _cur(conn) as cur:
        cur.execute("SELECT period, closing_balance, entry_count, issued_on FROM deposits.statements "
                    "WHERE account_id = %s", (account_id,))
        issued = {r["period"]: r for r in cur.fetchall()}
    opened = _opened_on(account).replace(day=1)
    cursor, out = today.replace(day=1), []
    while len(out) < MAX_PERIODS_LISTED:
        cursor = (cursor - timedelta(days=1)).replace(day=1)          # the month before
        if cursor < opened:
            break
        period = cursor.strftime("%Y-%m")
        out.append({"period": period, "label": cursor.strftime("%B %Y"), "issued": period in issued,
                    **({"closing_balance": issued[period]["closing_balance"],
                        "entry_count": issued[period]["entry_count"]} if period in issued else {})})
    return {"account_id": account_id, "business_date": today, "opened_on": _opened_on(account), "periods": out}


# ------------------------------------------------------------------ fingerprints


def entries_hash(account_id, date_from, date_to, statement: dict) -> str:
    h = hashlib.sha256()
    h.update(f"{account_id}|{date_from}|{date_to}|{statement['opening_balance']}|"
             f"{statement['closing_balance']}\n".encode())
    for e in statement["entries"]:
        h.update(f"{e['entry_id']}|{e['business_date']}|{e['amount']}|{e['txn_type']}|"
                 f"{e.get('description') or ''}\n".encode())
    return h.hexdigest()


# ------------------------------------------------------------------ rendering


def inr(paise: int) -> str:
    """Rupees with Indian digit grouping: 12,34,567.89"""
    sign = "-" if paise < 0 else ""
    rupees, rest = divmod(abs(int(paise)), 100)
    digits = str(rupees)
    if len(digits) > 3:
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", digits[:-3])
        digits = f"{head},{digits[-3:]}"
    return f"{sign}{digits}.{rest:02d}"


def _latin(text) -> str:
    return str(text or "").replace("₹", "Rs ").encode("latin-1", "replace").decode("latin-1")


def _day(value) -> str:
    d = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    return f"{d.day} {d.strftime('%b %Y')}"


class _Pdf(FPDF):
    COLUMNS = (("Date", 24, "L"), ("Particulars", 76, "L"), ("Withdrawal", 30, "R"),
               ("Deposit", 30, "R"), ("Balance", 30, "R"))

    def __init__(self, footer_line: str) -> None:
        super().__init__(format="A4", unit="mm")
        self.footer_line = footer_line
        self.set_margins(10, 12, 10)
        self.set_auto_page_break(True, margin=20)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", size=7)
        self.set_text_color(95, 100, 125)
        self.cell(150, 4, self.footer_line)
        self.cell(0, 4, f"Page {self.page_no()} of {{nb}}", align="R")

    def table_header(self) -> None:
        self.set_font("Helvetica", "B", 8)
        self.set_fill_color(230, 234, 247)
        self.set_text_color(*INK)
        for title, width, align in self.COLUMNS:
            self.cell(width, 7, title, border="B", align=align, fill=True)
        self.ln(7)
        self.set_text_color(26, 31, 54)
        self.set_font("Helvetica", size=8)

    def fit(self, text: str, width: float) -> str:
        if self.get_string_width(text) <= width - 2:
            return text
        while text and self.get_string_width(text + "...") > width - 2:
            text = text[:-1]
        return text + "..."

    def row(self, cells: list[str]) -> None:
        if self.get_y() > self.h - 28:                 # keep a row whole, repeat the header on the new page
            self.add_page()
            self.table_header()
        for (_, width, align), text in zip(self.COLUMNS, cells):
            self.cell(width, 6, self.fit(text, width), border="B", align=align)
        self.ln(6)


def render(account: dict, statement: dict, *, date_from: date, date_to: date, issued_on: date,
           fingerprint: str | None) -> bytes:
    """The PDF. The same inputs always give the same bytes, so a file can be recognised by its hash."""
    footer = (f"Fingerprint {fingerprint[:40]}. To check this statement is genuine, upload it on the bank's sign-in page."
              if fingerprint else "Interim statement: not registered with the bank, so it cannot be verified.")
    pdf = _Pdf(footer)
    pdf.set_creation_date(datetime(issued_on.year, issued_on.month, issued_on.day, tzinfo=timezone.utc))
    pdf.set_title(f"Statement {account['account_number']} {date_from} to {date_to}")
    pdf.set_author("Unbeatable Bank")
    pdf.set_creator("Unbeatable Bank statements")
    pdf.add_page()

    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 9, "Unbeatable Bank", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 6, "Statement of account" if fingerprint else "Interim statement of account",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    holders = ", ".join(_latin(h["full_name"]) for h in account.get("holders", [])) or "Account holder"
    facts = [("Account holder", holders),
             ("Account number", str(account["account_number"])),
             ("Account type", PRODUCT_NAMES.get(account.get("product_type"), str(account.get("product_type", "")))),
             ("Period", f"{_day(date_from)} to {_day(date_to)}"),
             ("Issued on", _day(issued_on))]
    pdf.set_text_color(26, 31, 54)
    for label, value in facts:
        pdf.set_font("Helvetica", size=9)
        pdf.set_text_color(95, 100, 125)
        pdf.cell(34, 5.5, label)
        pdf.set_text_color(26, 31, 54)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5.5, pdf.fit(value, 150), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)

    summary = [("Opening balance", statement["opening_balance"]), ("Deposits", statement["total_credits"]),
               ("Withdrawals", statement["total_debits"]), ("Closing balance", statement["closing_balance"])]
    pdf.set_fill_color(246, 248, 252)
    top = pdf.get_y()
    for i, (label, amount) in enumerate(summary):
        pdf.set_xy(10 + i * 47.5, top)
        pdf.set_font("Helvetica", size=8)
        pdf.set_text_color(95, 100, 125)
        pdf.cell(47.5, 6, label, fill=True)
        pdf.set_xy(10 + i * 47.5, top + 6)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(26, 31, 54)
        pdf.cell(47.5, 8, inr(amount), fill=True)
    pdf.set_xy(10, top + 18)
    pdf.set_font("Helvetica", size=7)
    pdf.set_text_color(95, 100, 125)
    pdf.cell(0, 4, "All amounts are in Indian rupees (INR).", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    pdf.table_header()
    pdf.row([_day(date_from), "Opening balance", "", "", inr(statement["opening_balance"])])
    for e in statement["entries"]:
        text = _latin(e.get("description") or str(e["txn_type"]).replace("_", " ").capitalize())
        pdf.row([_day(e["business_date"]), text, inr(-e["amount"]) if e["amount"] < 0 else "",
                 inr(e["amount"]) if e["amount"] > 0 else "", inr(e["balance"])])
    pdf.set_font("Helvetica", "B", 8)
    pdf.row([_day(date_to), "Closing balance", inr(statement["total_debits"]), inr(statement["total_credits"]),
             inr(statement["closing_balance"])])
    if not statement["entries"]:
        pdf.ln(4)
        pdf.set_font("Helvetica", size=9)
        pdf.cell(0, 6, "No transactions in this period.")
    return bytes(pdf.output())


# ------------------------------------------------------------------ monthly statements


def monthly(conn: psycopg.Connection, deps: Deps, account_id: UUID, period: str) -> tuple[bytes, str]:
    """(PDF bytes, file name) for a completed month. Issued on first request, then served from storage."""
    date_from, date_to = period_bounds(period)
    today = business_date(conn)
    if date_to >= today:
        raise InvalidPeriod(f"{date_from.strftime('%B %Y')} isn't over yet. Use a date range for an interim statement.")
    account = deps.accounts.account(account_id)
    if date_to < _opened_on(account).replace(day=1):
        raise InvalidPeriod("The account wasn't open then.")
    name = f"statement-{str(account['account_number'])[-4:]}-{period}.pdf"

    with _cur(conn) as cur:
        cur.execute("SELECT * FROM deposits.statements WHERE account_id = %s AND period = %s", (account_id, period))
        row = cur.fetchone()
    if row:
        data = deps.objstore.get(row["object_path"])
        if data is not None:
            return data, name

    statement = deps.ledger.statement(account_id, date_from, date_to)
    fingerprint = entries_hash(account_id, date_from, date_to, statement)
    if row and row["entries_hash"] != fingerprint:
        raise Conflict("The stored statement is missing and the ledger no longer matches what was issued.")
    data = render(account, statement, date_from=date_from, date_to=date_to,
                  issued_on=row["issued_on"] if row else today, fingerprint=fingerprint)
    path = f"statements/{account_id}/{period}.pdf"
    deps.objstore.put(path, data, "application/pdf")
    with conn.transaction(), _cur(conn) as cur:
        cur.execute(
            """
            INSERT INTO deposits.statements (account_id, account_number, period, date_from, date_to, opening_balance,
                closing_balance, entry_count, entries_hash, pdf_sha256, object_path, issued_on)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (account_id, period) DO UPDATE SET pdf_sha256 = EXCLUDED.pdf_sha256
            """,
            (account_id, str(account["account_number"]), period, date_from, date_to, statement["opening_balance"],
             statement["closing_balance"], len(statement["entries"]), fingerprint,
             hashlib.sha256(data).hexdigest(), path, today))
    return data, name


def interim(conn: psycopg.Connection, deps: Deps, account_id: UUID, date_from: date, date_to: date) -> tuple[bytes, str]:
    today = business_date(conn)
    if date_from > date_to:
        raise InvalidPeriod("The start date is after the end date.")
    if date_to > today:
        raise InvalidPeriod(f"The end date can't be after today's bank date ({today.isoformat()}).")
    if (date_to - date_from).days > MAX_INTERIM_DAYS:
        raise InvalidPeriod("Choose a range of one year or less.")
    account = deps.accounts.account(account_id)
    statement = deps.ledger.statement(account_id, date_from, date_to)
    data = render(account, statement, date_from=date_from, date_to=date_to, issued_on=today, fingerprint=None)
    return data, f"statement-{str(account['account_number'])[-4:]}-{date_from}-to-{date_to}.pdf"


# ------------------------------------------------------------------ checking


def _mask(number: str) -> str:
    return "x" * max(0, len(number) - 4) + number[-4:]


def verify(conn: psycopg.Connection, deps: Deps, account_id: UUID, period: str) -> dict:
    """Does an issued statement still match the ledger, and is the stored file the one that was issued?"""
    period_bounds(period)
    with _cur(conn) as cur:
        cur.execute("SELECT * FROM deposits.statements WHERE account_id = %s AND period = %s", (account_id, period))
        row = cur.fetchone()
    if row is None:
        raise NotFound("That statement hasn't been issued yet. Download it first.")
    statement = deps.ledger.statement(account_id, row["date_from"], row["date_to"])
    ledger_matches = entries_hash(account_id, row["date_from"], row["date_to"], statement) == row["entries_hash"]
    data = deps.objstore.get(row["object_path"])
    file_intact = data is not None and hashlib.sha256(data).hexdigest() == row["pdf_sha256"]
    return {"period": period, "valid": ledger_matches and file_intact, "ledger_matches": ledger_matches,
            "file_intact": file_intact, "entry_count": row["entry_count"], "issued_on": row["issued_on"],
            "fingerprint": row["entries_hash"]}


def verify_file(conn: psycopg.Connection, deps: Deps, data: bytes) -> dict:
    """Was exactly this PDF issued by the bank? Open to anyone holding the file: the answer
    reveals nothing the file doesn't already show."""
    if not data.startswith(b"%PDF-"):
        raise InvalidPeriod("That isn't a PDF file.")
    with _cur(conn) as cur:
        cur.execute("SELECT * FROM deposits.statements WHERE pdf_sha256 = %s", (hashlib.sha256(data).hexdigest(),))
        row = cur.fetchone()
    if row is None:
        return {"genuine": False,
                "message": "This file is not a statement issued by Unbeatable Bank, or it has been changed."}
    statement = deps.ledger.statement(row["account_id"], row["date_from"], row["date_to"])
    current = entries_hash(row["account_id"], row["date_from"], row["date_to"], statement) == row["entries_hash"]
    return {"genuine": True, "account_number": _mask(row["account_number"]), "period": row["period"],
            "date_from": row["date_from"], "date_to": row["date_to"], "closing_balance": row["closing_balance"],
            "issued_on": row["issued_on"], "matches_ledger_now": current,
            "message": "Unbeatable Bank issued exactly this file."}
