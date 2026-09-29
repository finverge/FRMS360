"""Machine credentials: a different credential class, not a user with MFA switched off.

The distinction is the whole design. A human account with its second factor disabled is
a switch someone eventually flips on a real person, and every batch it sends is
attributed to whichever employee's password went into the switch's config. A machine
credential has no interactive login path at all and can never acquire one.

MFA exists because secrets leak. These tests are therefore mostly about what replaces it:
scope that cannot reach customer data, certificate binding, address restriction, expiry.
A test suite for this that only proved the happy path would be proving the wrong thing.
"""
from datetime import datetime, timedelta, timezone

import pytest

from cp_common.auth import MACHINE_SCOPES, Principal, is_machine, machine_may
from services.tenant_service.app import service_credentials as sc


def cred(**over):
    base = dict(
        id="c1", tenant_id="t1", name="UPI switch", client_id=sc.new_client_id(),
        secret_hash=sc.hash_secret("s3cret"), scope="ingest", ip_allowlist=[],
        cert_thumbprint="", cert_source=sc.CERT_NONE, status="active",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        revoked_reason="")
    base.update(over)
    return sc.ServiceCredential(**base)


# ---------------------------------------------------------------- the secret
def test_a_secret_is_never_stored_in_plaintext():
    secret = sc.new_secret()
    stored = sc.hash_secret(secret)
    assert secret not in stored
    assert sc.verify_secret(secret, stored)
    assert not sc.verify_secret(secret + "x", stored)


def test_secret_comparison_is_constant_time():
    """Timing-safe comparison, because the token endpoint is unauthenticated by design."""
    import inspect
    assert "compare_digest" in inspect.getsource(sc.verify_secret)


def test_generated_secrets_do_not_repeat():
    assert len({sc.new_secret() for _ in range(200)}) == 200
    assert len({sc.new_client_id() for _ in range(200)}) == 200


# ------------------------------------------------------------------ the scope
def test_full_access_is_not_a_machine_scope():
    """This is what makes a leaked intake key survivable."""
    with pytest.raises(sc.ServiceCredentialError) as exc:
        sc.validate_scope("full")
    assert "cannot be given full access" in str(exc.value)


@pytest.mark.parametrize("scope", sorted(sc.SCOPES))
def test_every_declared_scope_validates(scope):
    assert sc.validate_scope(scope) == scope


def test_a_machine_token_is_refused_by_the_standard_dependency():
    """Default is refusal. Endpoints opt in; they do not opt out.

    Defaulting the other way would expose every new endpoint until someone remembered
    to close it."""
    from fastapi import HTTPException

    from cp_common.auth import get_current_principal
    from cp_common.security import create_access_token

    token = create_access_token(subject="svc_x", role="service", tenant_id="t1",
                                scope="ingest")
    with pytest.raises(HTTPException) as exc:
        get_current_principal(f"Bearer {token}")
    assert exc.value.status_code == 403
    assert "machine credential" in exc.value.detail


def test_an_ingest_credential_cannot_request_a_decision():
    p = Principal(subject="svc_x", role="service", tenant_id="t1", scope="ingest")
    assert machine_may(p, "ingest")
    assert not machine_may(p, "decide")


def test_a_combined_scope_covers_both():
    p = Principal(subject="svc_x", role="service", tenant_id="t1", scope="ingest+decide")
    assert machine_may(p, "ingest") and machine_may(p, "decide")


def test_a_human_token_is_unaffected_by_machine_scoping():
    """Widening for machines must not narrow anything for people."""
    p = Principal(subject="a@b.c", role="analyst", tenant_id="t1", scope="full")
    assert not is_machine(p)
    assert machine_may(p, "anything at all")


# ------------------------------------------------------------- the allow-list
def test_an_unparseable_allowlist_entry_is_refused():
    """An entry that never matches would leave the credential usable from anywhere while
    looking restricted."""
    with pytest.raises(sc.ServiceCredentialError) as exc:
        sc.validate_allowlist(["10.0.0.0/8", "not-an-ip"])
    assert "not an IP address or CIDR" in str(exc.value)


def test_the_allowlist_admits_and_refuses_correctly():
    allow = sc.validate_allowlist(["10.20.0.0/16", "203.0.113.7"])
    assert sc.ip_allowed("10.20.5.9", allow)
    assert sc.ip_allowed("203.0.113.7", allow)
    assert not sc.ip_allowed("10.21.0.1", allow)
    assert not sc.ip_allowed("", allow)


def test_an_unparseable_caller_address_is_not_admitted():
    """Cannot be shown to be on the list, therefore is not on it."""
    assert not sc.ip_allowed("no-such-address", ["10.0.0.0/8"])


def test_an_empty_allowlist_admits_everything_and_says_so():
    assert sc.ip_allowed("1.2.3.4", [])
    posture = sc.describe_posture(cred())
    assert "unrestricted" in posture["network"]


# ------------------------------------------------------------ the certificate
def test_thumbprints_normalise_across_the_formats_they_arrive_in():
    a = sc.normalise_thumbprint("AA:BB:CC:dd")
    assert a == sc.normalise_thumbprint("aabbccdd") == sc.normalise_thumbprint("AA BB CC DD")


def test_a_bound_certificate_must_match():
    c = cred(cert_thumbprint="aabbccdd", cert_source=sc.CERT_FROM_CONNECTION)
    sc.check_certificate(c, "AA:BB:CC:DD")
    with pytest.raises(sc.ServiceCredentialError) as exc:
        sc.check_certificate(c, "11223344")
    assert "secret alone is not sufficient" in str(exc.value)


def test_an_unbound_credential_does_not_require_a_certificate():
    sc.check_certificate(cred(), "")


def test_posture_distinguishes_mtls_from_a_forwarded_header():
    """A thumbprint from the connection proves possession. One forwarded by the bank's
    edge proves that we trust that edge. They must never read as the same."""
    mtls = sc.describe_posture(
        cred(cert_thumbprint="aabb", cert_source=sc.CERT_FROM_CONNECTION))
    header = sc.describe_posture(
        cred(cert_thumbprint="aabb", cert_source=sc.CERT_FROM_HEADER))
    assert mtls["proves_possession"] is True
    assert header["proves_possession"] is False
    assert "trusting edge" in header["certificate"]


# ---------------------------------------------------------------- the lifecycle
def test_a_revoked_credential_is_refused_with_its_reason():
    with pytest.raises(sc.ServiceCredentialError) as exc:
        sc.check_usable(cred(status="revoked", revoked_reason="switch decommissioned"))
    assert "switch decommissioned" in str(exc.value)


def test_an_expired_credential_is_refused():
    """Expiry exists so a forgotten integration stops working rather than living forever."""
    past = datetime.now(timezone.utc) - timedelta(days=1)
    with pytest.raises(sc.ServiceCredentialError) as exc:
        sc.check_usable(cred(expires_at=past))
    assert "expired" in str(exc.value)


def test_a_credential_expiring_tomorrow_still_works():
    sc.check_usable(cred(expires_at=datetime.now(timezone.utc) + timedelta(days=1)))


def test_a_naive_expiry_is_treated_as_utc_not_crashed_on():
    sc.check_usable(cred(expires_at=datetime.now() + timedelta(days=5)))


# --------------------------------------------------------------- the disclosure
def test_posture_is_stated_rather_than_scored():
    """An IS reviewer needs to see which configuration is in force, not a grade."""
    p = sc.describe_posture(cred(cert_thumbprint="aabb",
                                 cert_source=sc.CERT_FROM_CONNECTION,
                                 ip_allowlist=["10.0.0.0/8"]))
    assert set(p) >= {"secret", "certificate", "network", "scope", "expiry",
                      "proves_possession"}
    assert "hashed" in p["secret"]
