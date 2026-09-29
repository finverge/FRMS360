"""Secrets sealed at rest (BR-712).

The column that made this necessary is ``mfa_secret``. A TOTP seed readable by anyone
with SELECT means a database reader can mint a valid second factor for every user, which
makes the whole MFA control decorative.
"""
import base64
import os

import pytest
from sqlalchemy import text

from cp_common import crypto
from cp_common.db import SessionLocal

AAD = "tenant.mfa_secret"


@pytest.fixture
def provider():
    """A key set of this test's own, restored afterwards."""
    original = crypto.provider()
    k1 = base64.b64encode(b"\x01" * 32).decode()
    k2 = base64.b64encode(b"\x02" * 32).decode()
    crypto.set_provider(crypto.EnvKeyProvider(f"1:{k1},2:{k2}"))
    yield
    crypto.set_provider(original)


# ------------------------------------------------------------------ round trip
def test_a_secret_round_trips(provider):
    token = crypto.seal("JBSWY3DPEHPK3PXP", aad=AAD)
    assert token.startswith("enc:v")
    assert "JBSWY3DPEHPK3PXP" not in token
    assert crypto.unseal(token, aad=AAD) == "JBSWY3DPEHPK3PXP"


def test_the_same_plaintext_seals_differently_each_time(provider):
    """Deterministic ciphertext would leak which users share a secret."""
    a = crypto.seal("same", aad=AAD)
    b = crypto.seal("same", aad=AAD)
    assert a != b
    assert crypto.unseal(a, aad=AAD) == crypto.unseal(b, aad=AAD) == "same"


def test_the_newest_key_is_used_but_older_ones_still_decrypt(provider):
    """Rotation must not require rewriting every row before the new key works."""
    assert crypto.key_version(crypto.seal("x", aad=AAD)) == 2
    k1 = base64.b64encode(b"\x01" * 32).decode()
    old = crypto.EnvKeyProvider(f"1:{k1}")
    crypto.set_provider(old)
    v1 = crypto.seal("written-under-v1", aad=AAD)
    crypto.set_provider(crypto.EnvKeyProvider(
        f"1:{k1},2:{base64.b64encode(b'2' * 32).decode()}"))
    assert crypto.unseal(v1, aad=AAD) == "written-under-v1"


# ------------------------------------------------------------------ the AAD binding
def test_a_secret_moved_to_another_column_will_not_decrypt(provider):
    """Cheap, and it defeats a family of attacks encryption alone does not.

    Lifting a sealed value out of one column into another must fail rather than quietly
    working - otherwise a low-value secret can be promoted into a high-value one.
    """
    token = crypto.seal("s3cret", aad="tenant.idp_client_secret")
    with pytest.raises(crypto.CryptoError):
        crypto.unseal(token, aad=AAD)


def test_a_tampered_ciphertext_is_refused_not_decrypted(provider):
    token = crypto.seal("original", aad=AAD)
    head, _, body = token.partition(":")
    ver, _, b64 = body.partition(":")
    raw = bytearray(base64.b64decode(b64))
    raw[-1] ^= 0xFF
    tampered = f"{head}:{ver}:{base64.b64encode(bytes(raw)).decode()}"
    with pytest.raises(crypto.CryptoError):
        crypto.unseal(tampered, aad=AAD)


# ------------------------------------------------------------------ failing closed
def test_an_unknown_key_version_raises_rather_than_guessing(provider):
    token = crypto.seal("x", aad=AAD)
    crypto.set_provider(crypto.EnvKeyProvider(
        f"9:{base64.b64encode(b'9' * 32).decode()}"))
    with pytest.raises(crypto.KeyUnavailable):
        crypto.unseal(token, aad=AAD)


def test_no_key_configured_refuses_to_invent_one():
    """A key generated at boot would vanish on restart and take every row with it."""
    empty = crypto.EnvKeyProvider("")
    assert empty.configured is False
    with pytest.raises(crypto.KeyUnavailable):
        empty.active()


def test_a_short_key_is_refused():
    with pytest.raises(crypto.CryptoError):
        crypto.EnvKeyProvider(f"1:{base64.b64encode(b'tooshort').decode()}")


def test_unsealing_something_that_was_never_sealed_raises(provider):
    with pytest.raises(crypto.CryptoError):
        crypto.unseal("just a string", aad=AAD)


# ------------------------------------------------------------------ the column type
def test_the_column_refuses_to_store_plaintext_without_a_key(monkeypatch):
    col = crypto.EncryptedSecret("tenant.mfa_secret")
    monkeypatch.delenv(crypto.ALLOW_PLAINTEXT_ENV, raising=False)
    crypto.set_provider(crypto.EnvKeyProvider(""))
    try:
        with pytest.raises(crypto.KeyUnavailable):
            col.process_bind_param("seed", None)
    finally:
        crypto.set_provider(crypto.EnvKeyProvider())


def test_plaintext_storage_needs_an_explicit_acknowledgement(monkeypatch):
    col = crypto.EncryptedSecret("tenant.mfa_secret")
    monkeypatch.setenv(crypto.ALLOW_PLAINTEXT_ENV, "true")
    crypto.set_provider(crypto.EnvKeyProvider(""))
    try:
        assert col.process_bind_param("seed", None) == "seed"
    finally:
        crypto.set_provider(crypto.EnvKeyProvider())


def test_the_column_does_not_double_wrap(provider):
    col = crypto.EncryptedSecret(AAD)
    once = col.process_bind_param("seed", None)
    twice = col.process_bind_param(once, None)
    assert twice == once
    assert col.process_result_value(twice, None) == "seed"


def test_a_legacy_plaintext_row_still_reads(provider):
    """A migration cannot be instantaneous; refusing to read existing rows would take
    the platform down."""
    col = crypto.EncryptedSecret(AAD)
    assert col.process_result_value("written-before-encryption", None) == \
        "written-before-encryption"


def test_a_corrupt_sealed_value_raises_rather_than_returning_ciphertext(provider):
    """The failure that would matter: handing back the stored bytes as though they were
    the secret."""
    col = crypto.EncryptedSecret(AAD)
    with pytest.raises(crypto.CryptoError):
        col.process_result_value("enc:v1:not-valid-base64!!", None)


# ------------------------------------------------------------------ end to end
def test_an_mfa_seed_written_through_the_orm_is_unreadable_in_the_column(tid):
    """The whole point, and deliberately not dependent on whichever fixture happens to
    have enrolled a user first.

    Writes a seed through the model, then reads the raw column with SQL - the way a
    database reader, a replica or a dump would see it.
    """
    from services.tenant_service.app.models import TenantUser

    seed = "JBSWY3DPEHPK3PXPMFATEST"
    db = SessionLocal()
    email = "crypto.probe@test.local"
    try:
        db.execute(text("DELETE FROM tenant.tenant_users WHERE email = :e"),
                   {"e": email})
        db.add(TenantUser(tenant_id=tid, email=email, role="analyst",
                          password_hash="x", mfa_secret=seed))
        db.commit()

        raw = db.execute(text(
            "SELECT mfa_secret FROM tenant.tenant_users WHERE email = :e"),
            {"e": email}).scalar()
        assert raw, "no row written"
        assert seed not in raw, "the TOTP seed is readable in the column"
        assert crypto.is_sealed(raw)

        # And it still comes back through the model.
        db.expire_all()
        user = db.query(TenantUser).filter(TenantUser.email == email).one()
        assert user.mfa_secret == seed
    finally:
        db.execute(text("DELETE FROM tenant.tenant_users WHERE email = :e"),
                   {"e": email})
        db.commit()
        db.close()


def test_no_mfa_seed_anywhere_is_still_in_the_clear(tid):
    """Catches rows written before the column was sealed and never backfilled."""
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT mfa_secret FROM tenant.tenant_users WHERE mfa_secret <> '' "
            "UNION ALL "
            "SELECT mfa_secret FROM tenant.platform_users WHERE mfa_secret <> ''")).all()
    finally:
        db.close()
    plain = [r[0] for r in rows if not crypto.is_sealed(r[0])]
    assert not plain, (
        f"{len(plain)} MFA seed(s) are stored in the clear - a database reader can mint "
        "a valid second factor for those users. Run scripts/seal_secrets.py --commit")
