-- Local development only (docker compose and scripts/dev_stack.py).
-- In GCP, Terraform creates the databases and users on Cloud SQL and the
-- passwords live in Secret Manager.

CREATE DATABASE core_banking;   -- ledger, account, intake, deposits (one schema each)
CREATE DATABASE identity;
CREATE DATABASE cases;          -- fraud alert: cases, approvals, reports
CREATE DATABASE docstore;       -- local stand-in for Firestore

-- One database user per service. Each service creates and owns its own schema
-- through its migrations, and cannot read other services' tables.
CREATE ROLE ledger_svc   LOGIN PASSWORD 'ledger_dev';
CREATE ROLE account_svc  LOGIN PASSWORD 'account_dev';
CREATE ROLE intake_svc   LOGIN PASSWORD 'intake_dev';
CREATE ROLE deposits_svc LOGIN PASSWORD 'deposits_dev';
CREATE ROLE identity_svc LOGIN PASSWORD 'identity_dev';
CREATE ROLE fraud_svc    LOGIN PASSWORD 'fraud_dev';
CREATE ROLE docstore_svc LOGIN PASSWORD 'docstore_dev';

GRANT CONNECT, CREATE ON DATABASE core_banking TO ledger_svc, account_svc, intake_svc, deposits_svc;
GRANT CONNECT, CREATE ON DATABASE identity TO identity_svc;
GRANT CONNECT, CREATE ON DATABASE cases TO fraud_svc;
ALTER DATABASE docstore OWNER TO docstore_svc;

\c core_banking
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
