"""Shared test fixtures.

Environment is configured **before** any application import, because ``cp_common.db``
builds its engine at import time from ``DATABASE_URL``. Tests therefore run against a
dedicated ``cp_test`` database that is created, migrated and dropped by this module - they
never touch the development data.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEST_DB = os.environ.get("TEST_DB_NAME", "cp_test")
ADMIN_URL = os.environ.get(
    "TEST_ADMIN_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/postgres")
TEST_URL = os.environ.get(
    "TEST_DATABASE_URL", f"postgresql+psycopg2://cp:cp_password@localhost:5432/{TEST_DB}")

# Must be set before the application packages are imported.
os.environ["DATABASE_URL"] = TEST_URL
os.environ["AUTO_CREATE_TABLES"] = "false"
os.environ["SEED_DEMO_TENANT"] = "false"
os.environ["JWT_SECRET"] = "test-secret-not-used-in-prod"
os.environ["ANALYTICS_ENGINE"] = "postgres"

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402


def _recreate_database() -> None:
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :db AND pid <> pg_backend_pid()"), {"db": TEST_DB})
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}"'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB}" OWNER cp'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def database():
    """Fresh database, schema applied through Alembic - so the migrations are tested too,
    not merely the models."""
    _recreate_database()
    env = {**os.environ, "PYTHONPATH": str(ROOT), "DATABASE_URL": TEST_URL}
    res = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                         cwd=ROOT, env=env, capture_output=True, text=True)
    assert res.returncode == 0, f"alembic upgrade failed:\n{res.stdout}\n{res.stderr}"
    yield TEST_URL


@pytest.fixture(scope="session")
def seeded(database):
    """A tenant with users across every role, plus a small analytical dataset."""
    from cp_common import SessionLocal, hash_password
    from services.analytics_service.app import models as am  # noqa: F401
    from services.tenant_service.app.models import PlatformUser, Tenant, TenantUser
    from scripts.generate_synthetic_data import generate_for_tenant
    from cp_common.rbac import ROLES

    db = SessionLocal()
    try:
        db.add(PlatformUser(email="admin@test.local", role="platform_admin",
                            password_hash=hash_password("AdminPass#2026x")))
        # mfa_policy is deliberately 'optional' here. Every other test in the suite
        # signs in to exercise authorisation, and forcing enrolment on each of them
        # would test the same enrolment flow a hundred times while making the failures
        # harder to read. test_auth_hardening.py raises the policy where it matters.
        tenant = Tenant(slug="testbank", legal_name="Test Bank Ltd.",
                        display_name="Test Bank", status="active",
                        mfa_policy="optional")
        db.add(tenant)
        db.flush()
        # Roles are rows, and the only authority: a tenant with none has no one who can do
        # anything. Onboarding does this for a real tenant; a fixture that inserts the tenant
        # directly has to do it too.
        from services.tenant_service.app.roles import seed_default_roles
        seed_default_roles(db, tenant.id)
        for role in ROLES:
            if role == "platform_admin":
                continue
            db.add(TenantUser(tenant_id=tenant.id, email=f"{role}@test.local", role=role,
                              password_hash=hash_password("UserPass#2026x")))
        db.commit()
        # The EWS rule catalogue lives in the control plane and is what detection is
        # measured against, so a tenant without it has no coverage baseline.
        from services.config_service.app.ews_catalogue import rule_configs
        from services.config_service.app.repositories import ConfigRepository
        repo = ConfigRepository(db)
        for c in rule_configs():
            repo.create(tenant.id, c["kind"], c["name"], c["version"], c["body"],
                        status="active")
        # A Tier-2 UCB, so the tests exercise a policy that is NOT the commercial-bank
        # default - otherwise "policy-driven" is indistinguishable from hard-coded.
        from services.config_service.app.policy import policy_body
        tenant.entity_type, tenant.ucb_tier = "urban_cooperative", 2
        policy = policy_body("urban_cooperative", 2)
        # Left unset by policy_body() on purpose (no sensible default - see its
        # docstring); an STR cannot be readied without one, so the fixture supplies
        # what a real tenant's board-approved policy would.
        policy["principal_officer_name"] = "A. Krishnan"
        repo.create(tenant.id, "policy", "frm-policy", "1.0.0", policy, status="active")
        db.commit()
        # Small but structurally real: rings and a burst still get injected.
        generate_for_tenant(db, tenant.id, days=30, n_txns=900)
        tid = tenant.id
    finally:
        db.close()
    return {"tenant_id": tid, "admin": ("admin@test.local", "AdminPass#2026x"),
            "user_password": "UserPass#2026x"}


@pytest.fixture(scope="session")
def tenant_client(seeded):
    from fastapi.testclient import TestClient
    from services.tenant_service.app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def analytics_client(seeded):
    from fastapi.testclient import TestClient
    from services.analytics_service.app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def lane_c_client(seeded):
    from fastapi.testclient import TestClient
    from services.lane_c_service.app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def token_for(tenant_client, seeded):
    """Return a bearer-header factory for any seeded role."""
    cache: dict[str, dict] = {}
    _secrets: dict[str, str] = {}   # TOTP secret per role, for re-verification

    def _get(role: str) -> dict:
        if role not in cache:
            email, pw = (seeded["admin"] if role == "platform_admin"
                         else (f"{role}@test.local", seeded["user_password"]))
            r = tenant_client.post("/auth/login", json={"email": email, "password": pw})
            assert r.status_code == 200, r.text
            body = r.json()

            # Platform staff always carry a second factor - they can reach every tenant.
            # The fixture completes the real challenge rather than exempting itself from
            # it, so the suite exercises the flow a bank's staff will actually use.
            if body.get("mfa_required"):
                import pyotp
                h = {"Authorization": f"Bearer {body['access_token']}"}
                if body.get("mfa_enrolment_required"):
                    secret = tenant_client.post("/auth/mfa/enrol", headers=h).json()["secret"]
                    act = tenant_client.post("/auth/mfa/activate", headers=h,
                                             json={"code": pyotp.TOTP(secret).now()})
                    assert act.status_code == 200, act.text
                else:
                    secret = _secrets[role]
                _secrets[role] = secret
                done = tenant_client.post("/auth/mfa/verify", headers=h,
                                          json={"code": pyotp.TOTP(secret).now()})
                assert done.status_code == 200, done.text
                body = done.json()

            cache[role] = {"Authorization": f"Bearer {body['access_token']}"}
        return cache[role]

    return _get


@pytest.fixture(scope="session")
def tid(seeded) -> str:
    return seeded["tenant_id"]


@pytest.fixture(scope="session")
def notification_client(seeded):
    from fastapi.testclient import TestClient
    from services.notification_service.app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def ingestion_client(seeded):
    from fastapi.testclient import TestClient
    from services.ingestion_service.app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def config_client(seeded):
    from fastapi.testclient import TestClient
    from services.config_service.app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def branding_client(seeded):
    from fastapi.testclient import TestClient
    from services.branding_service.app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session", autouse=True)
def route_tenant_provisioning_in_process(branding_client, config_client, seeded):
    """Send tenant-service's own onboarding calls (branding + default configs) to
    in-process clients.

    Without this, TenantService.onboard() - the same orchestration both platform_admin's
    POST /tenants and BR-109's POST /tenants/self-service run - always lands a freshly
    onboarded tenant in 'degraded', because there is no live branding-service/
    config-service process for it to actually reach in a test run. The seeded fixture
    sidesteps that by constructing its Tenant row directly rather than through onboard();
    tests that exercise onboarding itself (self-service, promotion) need the real
    orchestration to actually succeed.
    """
    from services.tenant_service.app import services as tenant_services

    class _Shim:
        @staticmethod
        def put(url, json=None, headers=None, timeout=None):
            path = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
            return branding_client.put(path, json=json, headers=headers or {})

        @staticmethod
        def post(url, json=None, headers=None, timeout=None):
            path = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
            return config_client.post(path, json=json, headers=headers or {})

    original = tenant_services.httpx
    tenant_services.httpx = _Shim
    yield
    tenant_services.httpx = original


@pytest.fixture(scope="session", autouse=True)
def route_invite_email_in_process(notification_client, seeded):
    """Send the tenant-service invite route's own direct-email call to an in-process
    client.

    ``routes/tenants.py``'s ``_email_invite`` posts to notification-service's
    ``POST /internal/channels/send`` over real HTTP in production. Without this bridge
    every invite in a test run fails to connect and silently falls back to
    ``emailed=False`` - which is a valid outcome to test, but not the only one; the
    happy path (a tenant with a working email channel) needs this to actually reach
    notification-service's real route.
    """
    from services.tenant_service.app.routes import tenants as tenants_routes

    class _Shim:
        @staticmethod
        def post(url, json=None, headers=None, timeout=None):
            path = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
            return notification_client.post(path, json=json, headers=headers or {})

    original = tenants_routes.httpx
    tenants_routes.httpx = _Shim
    yield
    tenants_routes.httpx = original


@pytest.fixture(scope="session", autouse=True)
def route_tenant_status_in_process(tenant_client, seeded):
    """Send cp_common.tenant_status's own lifecycle-standing fetch to an in-process
    client.

    require_machine_scope() calls tenant_status.standing() on every machine-token
    request (BR-107's enforcement point) and, since BR-109, ingestion's sandbox-
    isolation check calls it directly too. Both go over real HTTP to tenant-service in
    production; without this bridge every such call fails to connect in a test run and
    tenant_status silently falls back to its "never seen this tenant, serve it" default
    - which reads as not-suspended and not-sandbox regardless of the tenant's actual
    row. Existing lifecycle tests worked around that by monkeypatching tenant_status
    directly; this makes the real enforcement path exercisable instead.
    """
    from cp_common import tenant_status

    class _Shim:
        @staticmethod
        def get(url, headers=None, timeout=None):
            path = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
            return tenant_client.get(path, headers=headers or {})

    original = tenant_status.httpx
    tenant_status.httpx = _Shim
    yield
    tenant_status.httpx = original


@pytest.fixture(scope="session", autouse=True)
def route_roles_in_process(tenant_client, seeded):
    """Send every other service's role lookup to the in-process tenant-service.

    ``cp_common.dynamic_roles`` reads a tenant's role rows over HTTP from tenant-service (the
    only service with a grant on that schema), and since roles are the sole authority every
    authorisation check in every service goes through it. Same real contract (URL, key header,
    JSON shape), no second process.
    """
    from cp_common import dynamic_roles

    class _Shim:
        @staticmethod
        def get(url, headers=None, timeout=None):
            path = url.split("://", 1)[-1].split("/", 1)[-1]
            return tenant_client.get("/" + path, headers=headers or {})

    original = dynamic_roles.httpx
    dynamic_roles.httpx = _Shim
    dynamic_roles.invalidate()
    yield
    dynamic_roles.httpx = original


@pytest.fixture(scope="session", autouse=True)
def route_rule_bridge_in_process(config_client, seeded):
    """Send the analytics -> config-service call to an in-process client.

    The bridge deliberately talks HTTP so the two services stay independently
    deployable. Tests keep that real contract (same URL, same headers, same JSON shape)
    but avoid needing a second process listening on a port.
    """
    from services.analytics_service.app import rules as bridge
    from scripts import generate_synthetic_data as gen

    class _Shim:
        @staticmethod
        def get(url, headers=None, timeout=None):
            path = url.split("://", 1)[-1].split("/", 1)[-1]
            return config_client.get("/" + path, headers=headers or {})

    # Both the analytics bridge and the detection generator call config-service; each
    # holds its own httpx reference, so both need routing.
    originals = (bridge.httpx, gen.httpx)
    bridge.httpx = _Shim
    gen.httpx = _Shim
    # Caches are keyed by a shared generation now, so dropping them means bumping it.
    bridge.invalidate()
    yield
    bridge.httpx, gen.httpx = originals


@pytest.fixture(scope="session", autouse=True)
def route_decision_catalogue_bridge_in_process(config_client, seeded):
    """Send decision-service's catalogue/policy fetch (store.py) to an in-process client.

    Same reasoning as ``route_rule_bridge_in_process`` - decision-service really does
    reach config-service over HTTP for these (never on the decision path itself, only on
    a background refresh), and this keeps that contract without a second process.
    """
    from services.decision_service.app import store

    class _Shim:
        @staticmethod
        def get(url, headers=None, timeout=None):
            path = url.split("://", 1)[-1].split("/", 1)[-1]
            return config_client.get("/" + path, headers=headers or {})

    original = store.httpx
    store.httpx = _Shim
    yield
    store.httpx = original


@pytest.fixture(scope="session", autouse=True)
def route_detection_queue_in_process(ingestion_client, seeded):
    """Send the detection -> ingestion-service claim/ack calls to an in-process client.

    Same reasoning as the rule bridge: the two services really do talk over HTTP so they
    stay independently deployable, and the tests keep that contract (same path, same
    internal-key header, same JSON) without needing a second process on a port.
    """
    from services.analytics_service.app.detection import engine

    class _Shim:
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            path = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
            return ingestion_client.post(path, headers=headers or {}, json=json)

    original = engine.httpx
    engine.httpx = _Shim
    yield
    engine.httpx = original


@pytest.fixture(scope="session", autouse=True)
def route_filings_entity_in_process(tenant_client, seeded):
    """Send the filings -> tenant-service lookup to an in-process client.

    A regulatory return has to name the institution filing it, so the builder asks the
    tenant register who that is. Without this the tests would exercise the (correct)
    refusal path for every case, and never the returns themselves.
    """
    from services.analytics_service.app.routes import filings

    class _Shim:
        @staticmethod
        def get(url, headers=None, timeout=None):
            path = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
            return tenant_client.get(path, headers=headers or {})

    original = filings.httpx
    filings.httpx = _Shim
    yield
    filings.httpx = original


@pytest.fixture(scope="session", autouse=True)
def route_ctr_entity_in_process(tenant_client, seeded):
    """Same reasoning as route_filings_entity_in_process, for the CTR routes (BR-508) -
    a separate module with its own httpx reference."""
    from services.analytics_service.app.routes import ctr as ctr_routes

    class _Shim:
        @staticmethod
        def get(url, headers=None, timeout=None):
            path = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
            return tenant_client.get(path, headers=headers or {})

    original = ctr_routes.httpx
    ctr_routes.httpx = _Shim
    yield
    ctr_routes.httpx = original


@pytest.fixture(scope="session", autouse=True)
def route_dynamic_roles_in_process(tenant_client, seeded):
    """Send cp_common.dynamic_roles' tenant-service lookup to an in-process client.

    Shared by every service that imports it (analytics, config, decision,
    notification) - one shim here covers all of them, since they all call into the
    same module-level httpx reference rather than each holding their own.
    """
    from cp_common import dynamic_roles

    class _Shim:
        @staticmethod
        def get(url, headers=None, timeout=None):
            path = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
            return tenant_client.get(path, headers=headers or {})

    original = dynamic_roles.httpx
    dynamic_roles.httpx = _Shim
    yield
    dynamic_roles.httpx = original


@pytest.fixture(scope="session", autouse=True)
def route_board_pack_notifications_in_process(notification_client, seeded):
    """Send analytics -> notification-service calls to an in-process client.

    Same reasoning as the rule bridge: issuing a board pack really does notify each
    recipient over HTTP so the two services stay independently deployable. The test keeps
    that contract - same path, same internal-key header, same JSON - without needing a
    second process on a port.
    """
    from services.analytics_service.app.routes import board_pack as bp

    class _Shim:
        @staticmethod
        def post(url, headers=None, timeout=None, json=None):
            path = url.split("://", 1)[-1].split("/", 1)[-1]
            return notification_client.post("/" + path, headers=headers or {},
                                            json=json or {})

        @staticmethod
        def get(url, headers=None, timeout=None):
            path = url.split("://", 1)[-1].split("/", 1)[-1]
            return notification_client.get("/" + path, headers=headers or {})

    original = bp.httpx
    bp.httpx = _Shim
    yield
    bp.httpx = original


@pytest.fixture(scope="session", autouse=True)
def route_subscription_delivery_in_process(notification_client, seeded):
    """Send run_subscriptions.py's BR-609 delivery call to an in-process client.

    Same reasoning as the board-pack shim: a subscription really does hand its export to
    notification-service's /internal/channels/send over HTTP, so the two stay
    independently deployable. This keeps that contract without a second process.
    """
    from scripts import run_subscriptions

    class _Shim:
        @staticmethod
        def post(url, headers=None, timeout=None, json=None):
            path = url.split("://", 1)[-1].split("/", 1)[-1]
            return notification_client.post("/" + path, headers=headers or {},
                                            json=json or {})

    original = run_subscriptions.httpx
    run_subscriptions.httpx = _Shim
    yield
    run_subscriptions.httpx = original
