"""Each service can reach its own schema and nothing else.

Splitting tables into schemas only documents ownership. These tests check the part that
enforces it: a service connecting as its own database role must be *refused* by Postgres
when it reaches for a neighbour's table. Without that, "each service owns its data" lasts
until the first convenient cross-service join.

Skipped when no superuser connection is configured, since creating roles requires one.
Set SUPERUSER_DATABASE_URL to run them.
"""
import os

import pytest
from sqlalchemy import create_engine, text

from cp_common.schemas_db import ALL, GRANTS, OWNER

SUPERUSER = os.environ.get(
    "SUPERUSER_DATABASE_URL",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/cp_test")
PASSWORD = "cp_password"


@pytest.fixture(scope="module")
def roles(database):
    """Provision the per-service roles against the test database."""
    from scripts.provision_db_roles import ROLE_FOR, provision
    try:
        provision(SUPERUSER, PASSWORD, owner_role="cp")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no superuser connection for role provisioning: {exc}")
    return ROLE_FOR


def _can_read(role: str, table: str) -> bool:
    url = SUPERUSER.rsplit("/", 1)[0].replace(
        "postgres:postgres@", f"{role}:{PASSWORD}@")
    engine = create_engine(f"{url}/{SUPERUSER.rsplit('/', 1)[1]}", future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        engine.dispose()


# One representative table per schema.
TABLE_IN = {
    "tenant": "tenant.tenants",
    "branding": "branding.branding",
    "config": "config.tenant_configs",
    "analytics": "analytics.fact_case",
    "cases": "cases.case_transitions",
    "ingestion": "ingestion.raw_transaction",
    "notify": "notify.notifications",
    "decision": "decision.decision_log",
    "platform": "platform.audit_logs",
}


def test_every_schema_has_a_representative_table_here():
    """Add a schema without adding it here and the isolation tests silently skip it."""
    assert set(TABLE_IN) == set(ALL), \
        f"schemas with no coverage in this file: {sorted(set(ALL) - set(TABLE_IN))}"


@pytest.mark.parametrize("service", sorted(GRANTS))
def test_a_service_can_read_every_schema_it_owns(roles, service):
    role = roles[service]
    for schema in GRANTS[service]:
        assert _can_read(role, TABLE_IN[schema]), \
            f"{role} cannot read {TABLE_IN[schema]}, which {service} owns"


@pytest.mark.parametrize("service", sorted(GRANTS))
def test_a_service_cannot_read_any_schema_it_does_not_own(roles, service):
    role = roles[service]
    forbidden = [s for s in ALL if s not in GRANTS[service]]
    assert forbidden, f"{service} was granted everything - the split means nothing"
    for schema in forbidden:
        assert not _can_read(role, TABLE_IN[schema]), \
            (f"{role} can read {TABLE_IN[schema]}. Schema ownership is not enforced; "
             f"{service} has reach into another service's data.")


def test_the_analytics_role_cannot_reach_the_tenant_register(roles):
    """The case that matters most in a multi-tenant bank platform.

    The tenant register holds the banks themselves and their users. Analytics has no
    business in it - it works from tenant_id alone - and an RBI outsourcing review will
    ask precisely which components can read customer and institution records.
    """
    assert not _can_read(roles["analytics-service"], "tenant.tenant_users")
    assert not _can_read(roles["analytics-service"], "tenant.platform_users")


def test_every_schema_has_a_declared_owner():
    assert set(OWNER) == set(ALL), "a schema exists with no owning service declared"


def test_no_service_is_granted_every_schema():
    for service, schemas in GRANTS.items():
        assert set(schemas) != set(ALL), f"{service} has the whole database"


def test_public_is_empty_after_the_split(database):
    """Anything left in public is a table nobody has claimed."""
    engine = create_engine(database, future=True)
    try:
        with engine.connect() as conn:
            rows = [r[0] for r in conn.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))]
    finally:
        engine.dispose()
    # alembic_version is migration bookkeeping and belongs to no service.
    leftovers = [t for t in rows if t != "alembic_version"]
    assert not leftovers, f"tables left unowned in public: {leftovers}"
