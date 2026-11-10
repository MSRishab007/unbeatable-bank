import os

from alembic import context
from sqlalchemy import create_engine, text

SCHEMA = "identity"


def _url() -> str:
    url = os.environ["DATABASE_URL"]  # e.g. postgresql://identity_svc:pw@host:5432/identity
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as conn:
        # The version table lives inside our own schema, so it must exist first.
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        conn.commit()
        context.configure(
            connection=conn,
            version_table="alembic_version",
            version_table_schema=SCHEMA,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
