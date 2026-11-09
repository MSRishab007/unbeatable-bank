-- Local development only. In GCP, Terraform creates the databases and users
-- on Cloud SQL; passwords come from Secret Manager.

CREATE DATABASE core_banking;
CREATE DATABASE identity;
CREATE DATABASE cases;
CREATE DATABASE rules;

-- One database user per service. Each service creates and owns its own schema
-- via its migrations, and cannot touch other services' schemas.
CREATE ROLE ledger_svc  LOGIN PASSWORD 'ledger_dev';
CREATE ROLE account_svc LOGIN PASSWORD 'account_dev';
CREATE ROLE intake_svc  LOGIN PASSWORD 'intake_dev';

GRANT CONNECT, CREATE ON DATABASE core_banking TO ledger_svc, account_svc, intake_svc;

\c core_banking
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
