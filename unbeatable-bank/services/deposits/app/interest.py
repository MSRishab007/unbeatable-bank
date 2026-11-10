"""Interest arithmetic for deposits.

Rules, the same for the quote shown to the customer and for end-of-day processing:

  - Interest accrues daily on the deposit's balance:  balance * annual_rate / 365
  - Every three months it is credited to the deposit (quarterly compounding)
  - Accruals are kept as exact fractions of a paisa and only rounded down to
    whole paise when credited; the remainder carries forward
  - At maturity the last accrual is rounded half-up and credited

Because `project` replays exactly what end-of-day processing does, the maturity
amount quoted at booking is the amount that is finally paid out.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

DAYS_IN_YEAR = Decimal(365)
PREMATURE_PENALTY = Decimal("0.0100")     # 1% off the rate for breaking a deposit early
SENIOR_BONUS = Decimal("0.0050")          # +0.50% for customers aged 60 or over
RD_MISSED_PENALTY = Decimal("0.015")      # 1.5% of the installment for each one missed


def add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year, month = d.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


MICRO = Decimal("0.000001")   # accruals are kept to a millionth of a paisa, as stored in the database


def daily_interest(balance: int, rate: Decimal) -> Decimal:
    return (Decimal(balance) * Decimal(rate) / DAYS_IN_YEAR).quantize(MICRO, ROUND_HALF_EVEN)


def whole_paise(amount: Decimal) -> int:
    return int(amount.to_integral_value(ROUND_FLOOR))


def final_paise(amount: Decimal) -> int:
    return int(amount.to_integral_value(ROUND_HALF_UP))


@dataclass
class Projection:
    balance: int            # principal paid in + interest credited
    paid_in: int
    interest: int


def project(cashflows: list[tuple[date, int]], rate: Decimal, start: date, end: date,
            final: bool = True) -> Projection:
    """Replay a deposit day by day from `start` up to (not including) `end`.

    cashflows: (date, amount) paid into the deposit. If `final`, the leftover
    accrual is rounded half-up and credited, as at maturity or closure."""
    flows: dict[date, int] = {}
    for when, amount in cashflows:
        flows[when] = flows.get(when, 0) + amount
    balance = paid_in = interest = 0
    accrued = Decimal(0)
    next_cap = add_months(start, 3)
    quarter = 1
    day = start
    while day < end:
        if day >= next_cap:
            credit = whole_paise(accrued)
            balance, interest, accrued = balance + credit, interest + credit, accrued - credit
            quarter += 1
            next_cap = add_months(start, 3 * quarter)
        if day in flows:
            balance += flows[day]
            paid_in += flows[day]
        accrued += daily_interest(balance, rate)
        day += timedelta(days=1)
    if final:
        credit = final_paise(accrued)
        balance, interest = balance + credit, interest + credit
    return Projection(balance, paid_in, interest)


def rd_schedule(start: date, tenure_months: int) -> list[date]:
    """Installment due dates: one on the start date, then monthly."""
    return [add_months(start, i) for i in range(tenure_months)]


def is_quarter_end(d: date) -> bool:
    return (d.month, d.day) in ((3, 31), (6, 30), (9, 30), (12, 31))


def age_on(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
