"""Deposits: fixed and recurring deposits, rate cards, the bank's business date, EOD runs.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE SCHEMA IF NOT EXISTS deposits;

        -- The bank's "today". Interest and maturities follow this date, not the wall clock,
        -- so end-of-day processing can be replayed and (for demos) fast-forwarded.
        CREATE TABLE deposits.bank_clock (
            id            boolean PRIMARY KEY DEFAULT true CHECK (id),
            business_date date    NOT NULL
        );
        INSERT INTO deposits.bank_clock (business_date) VALUES ((now() AT TIME ZONE 'Asia/Kolkata')::date);

        -- Interest rates by product and tenure. New rows with a later effective_from
        -- change rates for new deposits only; existing deposits keep their booked rate.
        CREATE TABLE deposits.rate_cards (
            id             integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            product        text    NOT NULL CHECK (product IN ('SAVINGS', 'FD', 'RD')),
            min_days       integer NOT NULL,
            max_days       integer NOT NULL,
            annual_rate    numeric(6, 4) NOT NULL CHECK (annual_rate >= 0 AND annual_rate < 1),
            effective_from date    NOT NULL DEFAULT DATE '2000-01-01',
            CHECK (min_days <= max_days)
        );
        INSERT INTO deposits.rate_cards (product, min_days, max_days, annual_rate) VALUES
            ('SAVINGS', 0,    0,     0.0350),
            ('FD',      7,    45,    0.0400),
            ('FD',      46,   179,   0.0550),
            ('FD',      180,  364,   0.0625),
            ('FD',      365,  729,   0.0700),
            ('FD',      730,  1825,  0.0725),
            ('FD',      1826, 3660,  0.0675),
            ('RD',      180,  365,   0.0650),
            ('RD',      366,  730,   0.0675),
            ('RD',      731,  3660,  0.0700);

        CREATE TABLE deposits.deposits (
            deposit_id          uuid        PRIMARY KEY,          -- also the deposit's account id
            customer_id         uuid        NOT NULL,
            idempotency_key     text        NOT NULL,
            product             text        NOT NULL CHECK (product IN ('FD', 'RD')),
            status              text        NOT NULL DEFAULT 'PENDING'
                                    CHECK (status IN ('PENDING', 'ACTIVE', 'MATURED', 'CLOSED', 'FAILED')),
            funding_account_id  uuid        NOT NULL,             -- money comes from, and returns to, here
            account_number      text,
            principal           bigint      NOT NULL CHECK (principal > 0),  -- FD amount, or RD monthly installment
            tenure_months       integer     NOT NULL CHECK (tenure_months BETWEEN 1 AND 120),
            annual_rate         numeric(6, 4) NOT NULL,
            start_date          date        NOT NULL,
            maturity_date       date        NOT NULL,
            projected_maturity  bigint      NOT NULL,             -- shown to the customer at booking
            balance             bigint      NOT NULL DEFAULT 0,   -- mirrors the deposit account in the ledger
            interest_posted     bigint      NOT NULL DEFAULT 0,
            accrued             numeric(20, 6) NOT NULL DEFAULT 0,  -- earned, not yet credited (fractional paise)
            last_accrual_date   date,
            next_capitalisation date        NOT NULL,             -- interest is compounded quarterly
            installments_paid    integer     NOT NULL DEFAULT 0,
            installments_missed  integer     NOT NULL DEFAULT 0,
            payout_amount       bigint,
            penalty_amount      bigint,
            failure_code        text,
            created_at          timestamptz NOT NULL DEFAULT now(),
            closed_at           timestamptz,
            UNIQUE (customer_id, idempotency_key)
        );
        CREATE INDEX deposits_by_customer ON deposits.deposits (customer_id, created_at DESC);
        CREATE INDEX deposits_active ON deposits.deposits (deposit_id) WHERE status = 'ACTIVE';

        CREATE TABLE deposits.rd_installments (
            deposit_id uuid    NOT NULL REFERENCES deposits.deposits (deposit_id),
            n          integer NOT NULL,
            due_date   date    NOT NULL,
            status     text    NOT NULL DEFAULT 'DUE' CHECK (status IN ('DUE', 'PAID', 'MISSED')),
            paid_on    date,
            PRIMARY KEY (deposit_id, n)
        );
        CREATE INDEX rd_due ON deposits.rd_installments (due_date) WHERE status = 'DUE';

        -- One row per business date makes end-of-day processing restartable and idempotent.
        CREATE TABLE deposits.eod_runs (
            business_date date        PRIMARY KEY,
            status        text        NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING', 'DONE')),
            stats         jsonb       NOT NULL DEFAULT '{}'::jsonb,
            started_at    timestamptz NOT NULL DEFAULT now(),
            finished_at   timestamptz
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA deposits CASCADE")
