"""Account core: customers, accounts, joint holders, nominees, beneficiaries,
payment aliases and the append-only, hash-chained account event log.

Money (balances, entries) is NOT here: it belongs to the Ledger service.

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
        CREATE SCHEMA IF NOT EXISTS account;

        CREATE FUNCTION account.touch_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END $$;

        -- ------------------------------------------------------------ customers
        -- One row per real person. CIF = Customer Information File number.
        CREATE SEQUENCE account.cif_seq START 100000001;

        CREATE TABLE account.customers (
            customer_id   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            cif_number    text        NOT NULL UNIQUE
                              DEFAULT 'CIF' || nextval('account.cif_seq')::text,
            full_name     text        NOT NULL CHECK (length(trim(full_name)) BETWEEN 2 AND 120),
            date_of_birth date        NOT NULL CHECK (date_of_birth > DATE '1900-01-01'),
            phone         text        NOT NULL UNIQUE CHECK (phone ~ '^\\+91[6-9][0-9]{9}$'),
            email         text        NOT NULL UNIQUE CHECK (email = lower(email) AND email LIKE '%_@_%'),
            pan_hash      text        UNIQUE,  -- keyed hash of PAN; raw PAN is never stored
            pan_last4     text        CHECK (pan_last4 ~ '^[0-9]{4}[A-Z]$'),  -- for display: ******1234F
            kyc_status    text        NOT NULL DEFAULT 'PENDING'
                              CHECK (kyc_status IN ('PENDING', 'VERIFIED', 'REJECTED')),
            status        text        NOT NULL DEFAULT 'ACTIVE'
                              CHECK (status IN ('ACTIVE', 'FROZEN', 'CLOSED')),
            daily_transfer_limit bigint NOT NULL DEFAULT 20000000   -- Rs 2,00,000 across all accounts
                              CHECK (daily_transfer_limit > 0),
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now()
        );
        CREATE TRIGGER customers_touch BEFORE UPDATE ON account.customers
            FOR EACH ROW EXECUTE FUNCTION account.touch_updated_at();

        -- ------------------------------------------------------------ accounts
        -- Every product (savings, current, FD, RD) is an account.
        -- Account numbers: 4-digit branch + 7-digit serial + Luhn check digit.
        CREATE SEQUENCE account.account_no_seq START 1 MAXVALUE 9999999;

        CREATE TABLE account.accounts (
            account_id      uuid        PRIMARY KEY,
            account_number  text        NOT NULL UNIQUE CHECK (account_number ~ '^[0-9]{12}$'),
            product_type    text        NOT NULL CHECK (product_type IN ('SAVINGS', 'CURRENT', 'FD', 'RD')),
            operating_mode  text        NOT NULL DEFAULT 'SINGLE'
                                CHECK (operating_mode IN ('SINGLE', 'EITHER_OR_SURVIVOR', 'JOINTLY')),
            status          text        NOT NULL DEFAULT 'PENDING_KYC'
                                CHECK (status IN ('PENDING_KYC', 'ACTIVE', 'DORMANT', 'FROZEN', 'CLOSED')),
            currency        char(3)     NOT NULL DEFAULT 'INR' CHECK (currency = 'INR'),
            overdraft_limit bigint      NOT NULL DEFAULT 0 CHECK (overdraft_limit >= 0),
            daily_limit     bigint      NOT NULL DEFAULT 10000000 CHECK (daily_limit > 0),   -- Rs 1,00,000
            per_txn_limit   bigint      NOT NULL DEFAULT 5000000 CHECK (per_txn_limit > 0),  -- Rs 50,000
            nickname        text,
            freeze_reason   text,
            opened_at       timestamptz NOT NULL DEFAULT now(),
            last_activity_at timestamptz NOT NULL DEFAULT now(),  -- drives DORMANT
            closed_at       timestamptz,
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT overdraft_only_current CHECK (overdraft_limit = 0 OR product_type = 'CURRENT'),
            CONSTRAINT per_txn_within_daily CHECK (per_txn_limit <= daily_limit),
            CONSTRAINT closed_has_date CHECK ((status = 'CLOSED') = (closed_at IS NOT NULL)),
            CONSTRAINT frozen_has_reason CHECK (status <> 'FROZEN' OR freeze_reason IS NOT NULL)
        );
        CREATE TRIGGER accounts_touch BEFORE UPDATE ON account.accounts
            FOR EACH ROW EXECUTE FUNCTION account.touch_updated_at();

        -- Joint accounts: many customers <-> many accounts.
        CREATE TABLE account.account_holders (
            account_id  uuid        NOT NULL REFERENCES account.accounts (account_id),
            customer_id uuid        NOT NULL REFERENCES account.customers (customer_id),
            role        text        NOT NULL CHECK (role IN ('PRIMARY', 'JOINT')),
            added_at    timestamptz NOT NULL DEFAULT now(),
            removed_at  timestamptz,
            PRIMARY KEY (account_id, customer_id)
        );
        CREATE UNIQUE INDEX one_primary_holder
            ON account.account_holders (account_id) WHERE role = 'PRIMARY' AND removed_at IS NULL;
        CREATE INDEX holders_by_customer
            ON account.account_holders (customer_id) WHERE removed_at IS NULL;

        CREATE TABLE account.nominees (
            nominee_id    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id    uuid        NOT NULL REFERENCES account.accounts (account_id),
            full_name     text        NOT NULL,
            relation      text        NOT NULL,
            date_of_birth date,
            share_pct     numeric(5, 2) NOT NULL CHECK (share_pct > 0 AND share_pct <= 100),
            created_at    timestamptz NOT NULL DEFAULT now(),
            removed_at    timestamptz
        );
        CREATE INDEX nominees_by_account ON account.nominees (account_id) WHERE removed_at IS NULL;

        -- ------------------------------------------------------------ payees & aliases
        -- Saved payees. A new payee has a cooling period (fraud control).
        CREATE TABLE account.beneficiaries (
            beneficiary_id       uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id          uuid        NOT NULL REFERENCES account.customers (customer_id),
            payee_account_number text        NOT NULL CHECK (payee_account_number ~ '^[0-9]{12}$'),
            payee_name           text        NOT NULL,
            nickname             text,
            cooling_until        timestamptz NOT NULL,
            created_at           timestamptz NOT NULL DEFAULT now(),
            removed_at           timestamptz
        );
        CREATE UNIQUE INDEX one_active_beneficiary
            ON account.beneficiaries (customer_id, payee_account_number) WHERE removed_at IS NULL;

        -- Payment alias for scan-to-pay, e.g. riya@unbeatable
        CREATE TABLE account.aliases (
            alias       text        PRIMARY KEY CHECK (alias ~ '^[a-z0-9][a-z0-9._-]{2,30}@unbeatable$'),
            account_id  uuid        NOT NULL REFERENCES account.accounts (account_id),
            created_at  timestamptz NOT NULL DEFAULT now(),
            disabled_at timestamptz
        );
        CREATE UNIQUE INDEX one_active_alias_per_account
            ON account.aliases (account_id) WHERE disabled_at IS NULL;

        -- ------------------------------------------------------------ events
        -- Append-only and hash-chained per account: each row stores the hash of
        -- the previous one, so editing history breaks the chain.
        CREATE TABLE account.account_events (
            event_id    bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            account_id  uuid        NOT NULL REFERENCES account.accounts (account_id),
            event_type  text        NOT NULL,
            payload     jsonb       NOT NULL DEFAULT '{}'::jsonb,
            actor       text        NOT NULL,   -- 'customer:<id>', 'staff:<id>', 'system:<service>'
            prev_hash   text,
            hash        text        NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX events_by_account ON account.account_events (account_id, event_id);

        -- Transactional outbox, relayed to Kafka (account.events topic).
        CREATE TABLE account.outbox (
            event_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            topic        text        NOT NULL,
            event_key    text        NOT NULL,
            event_type   text        NOT NULL,
            payload      jsonb       NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            published_at timestamptz
        );
        CREATE INDEX account_outbox_unpublished ON account.outbox (created_at) WHERE published_at IS NULL;

        CREATE FUNCTION account.forbid_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
        END $$;
        CREATE TRIGGER account_events_append_only BEFORE UPDATE OR DELETE ON account.account_events
            FOR EACH ROW EXECUTE FUNCTION account.forbid_change();
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA account CASCADE")
