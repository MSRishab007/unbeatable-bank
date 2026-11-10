"""Ledger core: balances, journals, entries, holds, outbox.

Sign convention: every entry amount is signed from the account's point of view.
  amount > 0  -> credit (balance goes up)
  amount < 0  -> debit  (balance goes down)
The entries of one journal always sum to zero (double entry). Internal bank
accounts (interest expense, cash, ...) are allowed to go negative.

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
        CREATE SCHEMA IF NOT EXISTS ledger;

        -- One row per ledger account. Created by the Account service when an
        -- account is opened. Balances are only ever changed by the Ledger.
        CREATE TABLE ledger.balances (
            account_id        uuid        PRIMARY KEY,
            kind              text        NOT NULL CHECK (kind IN ('CUSTOMER', 'INTERNAL')),
            product_type      text        NOT NULL CHECK (product_type IN
                                  ('SAVINGS', 'CURRENT', 'FD', 'RD', 'INTERNAL')),
            status            text        NOT NULL DEFAULT 'OPEN' CHECK (status IN
                                  ('OPEN', 'DEBIT_BLOCKED', 'CLOSED')),
            currency          char(3)     NOT NULL DEFAULT 'INR' CHECK (currency = 'INR'),
            ledger_balance    bigint      NOT NULL DEFAULT 0,   -- paise
            available_balance bigint      NOT NULL DEFAULT 0,   -- paise, ledger minus active holds
            overdraft_limit   bigint      NOT NULL DEFAULT 0 CHECK (overdraft_limit >= 0),
            accrued_interest  numeric(20, 6) NOT NULL DEFAULT 0, -- fractional paise, posted by EOD
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT overdraft_only_current
                CHECK (overdraft_limit = 0 OR product_type = 'CURRENT'),
            CONSTRAINT available_not_above_ledger
                CHECK (available_balance <= ledger_balance),
            -- Last line of defence against overspending, even if app code is wrong.
            CONSTRAINT no_overspend_available
                CHECK (kind = 'INTERNAL' OR available_balance + overdraft_limit >= 0),
            CONSTRAINT no_overspend_ledger
                CHECK (kind = 'INTERNAL' OR ledger_balance + overdraft_limit >= 0)
        );

        CREATE TABLE ledger.journals (
            journal_id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            idempotency_key    text        NOT NULL UNIQUE,      -- final guard against double posting
            request_hash       text        NOT NULL,             -- detects same key, different payload
            txn_type           text        NOT NULL CHECK (txn_type IN (
                                   'TRANSFER', 'QR_PAYMENT', 'DEPOSIT', 'WITHDRAWAL',
                                   'FD_BOOKING', 'FD_MATURITY', 'FD_PREMATURE_CLOSURE',
                                   'RD_INSTALLMENT', 'RD_MATURITY', 'INTEREST', 'TDS',
                                   'PENALTY', 'FEE', 'REVERSAL')),
            description        text,
            business_date      date        NOT NULL DEFAULT CURRENT_DATE,
            metadata           jsonb       NOT NULL DEFAULT '{}'::jsonb,
            reverses_journal_id uuid       UNIQUE REFERENCES ledger.journals (journal_id),
            created_at         timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE ledger.entries (
            entry_id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            journal_id    uuid        NOT NULL REFERENCES ledger.journals (journal_id),
            account_id    uuid        NOT NULL REFERENCES ledger.balances (account_id),
            amount        bigint      NOT NULL CHECK (amount <> 0),
            balance_after bigint      NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (journal_id, account_id)
        );
        -- Statements / history: newest first per account.
        CREATE INDEX entries_account_idx ON ledger.entries (account_id, entry_id DESC);

        CREATE TABLE ledger.holds (
            hold_id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id      uuid        NOT NULL REFERENCES ledger.balances (account_id),
            amount          bigint      NOT NULL CHECK (amount > 0),
            reason          text        NOT NULL,
            idempotency_key text        NOT NULL UNIQUE,
            status          text        NOT NULL DEFAULT 'ACTIVE' CHECK (status IN
                                ('ACTIVE', 'RELEASED', 'CAPTURED', 'EXPIRED')),
            journal_id      uuid        REFERENCES ledger.journals (journal_id),
            expires_at      timestamptz,
            created_at      timestamptz NOT NULL DEFAULT now(),
            resolved_at     timestamptz,
            CONSTRAINT captured_has_journal CHECK ((status = 'CAPTURED') = (journal_id IS NOT NULL))
        );
        CREATE INDEX holds_active_idx ON ledger.holds (account_id) WHERE status = 'ACTIVE';

        -- Transactional outbox: written in the same DB transaction as the money
        -- movement, relayed to Kafka afterwards. Consumers dedupe on event_id.
        CREATE TABLE ledger.outbox (
            event_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            topic        text        NOT NULL,
            event_key    text        NOT NULL,        -- Kafka partition key (account / journal id)
            event_type   text        NOT NULL,
            payload      jsonb       NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            published_at timestamptz
        );
        CREATE INDEX outbox_unpublished_idx ON ledger.outbox (created_at) WHERE published_at IS NULL;

        -- Every journal must balance to zero and have at least two legs.
        -- DEFERRED: checked at COMMIT, after all legs are inserted.
        CREATE FUNCTION ledger.check_journal_balanced() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            total bigint;
            legs  int;
        BEGIN
            SELECT COALESCE(SUM(amount), 0), COUNT(*) INTO total, legs
            FROM ledger.entries WHERE journal_id = NEW.journal_id;
            IF total <> 0 OR legs < 2 THEN
                RAISE EXCEPTION 'journal % is unbalanced (sum=%, legs=%)', NEW.journal_id, total, legs
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END $$;

        CREATE CONSTRAINT TRIGGER entries_balanced
            AFTER INSERT ON ledger.entries
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION ledger.check_journal_balanced();

        -- Money history is append-only. Mistakes are fixed with REVERSAL journals.
        CREATE FUNCTION ledger.forbid_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
        END $$;

        CREATE TRIGGER journals_append_only BEFORE UPDATE OR DELETE ON ledger.journals
            FOR EACH ROW EXECUTE FUNCTION ledger.forbid_change();
        CREATE TRIGGER entries_append_only BEFORE UPDATE OR DELETE ON ledger.entries
            FOR EACH ROW EXECUTE FUNCTION ledger.forbid_change();
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA ledger CASCADE")
