"""Create one database role per service, able to reach only its own schema.

Splitting tables into schemas documents ownership. This enforces it. Until a service
connects as a role that *cannot* read its neighbours' tables, "each service owns its data"
is a naming convention, and the first time someone is in a hurry it will be violated by a
convenient cross-service join that nothing rejects.

Run as a superuser - creating roles requires it:

    python scripts/provision_db_roles.py --superuser-url postgresql+psycopg2://postgres:postgres@localhost:5432/controlplane

Idempotent: safe to re-run after adding a table or a schema.

Migrations keep running as the owning role (``cp``), not as the service roles. Schema
changes are a deploy-time action with different privileges from serving traffic, and a
service that can ALTER its own tables at runtime can also drop them.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, text

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from cp_common.schemas_db import GRANTS, OWNER  # noqa: E402

# service name -> login role
ROLE_FOR = {
    "tenant-service": "svc_tenant",
    "branding-service": "svc_branding",
    "config-service": "svc_config",
    "analytics-service": "svc_analytics",
    "ingestion-service": "svc_ingestion",
    "notification-service": "svc_notify",
    "decision-service": "svc_decision",
    "lane-c-service": "svc_lanec",
}


def provision(url: str, password: str, owner_role: str) -> list[str]:
    engine = create_engine(url, future=True)
    done: list[str] = []
    with engine.connect() as conn:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        db_name = conn.execute(text("SELECT current_database()")).scalar()

        for service, schemas in GRANTS.items():
            role = ROLE_FOR[service]
            conn.execute(text(
                "DO $$ BEGIN "
                f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
                f"CREATE ROLE {role} LOGIN PASSWORD '{password}'; "
                f"ELSE ALTER ROLE {role} WITH LOGIN PASSWORD '{password}'; "
                "END IF; END $$;"))
            conn.execute(text(f'GRANT CONNECT ON DATABASE "{db_name}" TO {role}'))

            # Start from nothing so a re-run after a schema move revokes what is stale.
            for schema in OWNER:
                conn.execute(text(f'REVOKE ALL ON SCHEMA "{schema}" FROM {role}'))
                conn.execute(text(
                    f'REVOKE ALL ON ALL TABLES IN SCHEMA "{schema}" FROM {role}'))

            for schema in schemas:
                conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {role}'))
                conn.execute(text(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                    f'IN SCHEMA "{schema}" TO {role}'))
                conn.execute(text(
                    f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" TO {role}'))
                # Tables created by a later migration must be reachable without re-running
                # this script, or the next deploy breaks in a way nobody connects to here.
                conn.execute(text(
                    f'ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} IN SCHEMA "{schema}" '
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"))

            # Name resolution matches the grants, so an unqualified reach for someone
            # else's table fails as "relation does not exist" rather than a silent read.
            search_path = ",".join(schemas)
            conn.execute(text(f"ALTER ROLE {role} SET search_path = {search_path}"))

            # public is empty after the schema split; make that permanent.
            conn.execute(text(f"REVOKE ALL ON SCHEMA public FROM {role}"))
            done.append(f"{role:<16} -> {', '.join(schemas)}")
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--superuser-url", required=True,
                    help="A superuser connection, e.g. postgresql+psycopg2://postgres:postgres@localhost:5432/controlplane")
    ap.add_argument("--password", default="cp_password",
                    help="Password for the service roles (dev default).")
    ap.add_argument("--owner-role", default="cp",
                    help="Role that owns the schemas and runs migrations.")
    args = ap.parse_args()

    rows = provision(args.superuser_url, args.password, args.owner_role)
    print("Provisioned service roles:\n")
    for r in rows:
        print("  " + r)
    print("\nMigrations continue to run as the owning role; services never do DDL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
