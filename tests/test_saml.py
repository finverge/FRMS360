"""SAML 2.0 service-provider verification.

Most of this file is attacks. A SAML SP that merely parses a valid assertion correctly is
not finished - the interesting cases are all the ones where something almost-valid is
presented, and the history of SAML is a history of implementations that accepted them.
"""
import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLSigner

from cp_common import saml

NS = saml.NS
IDP_ENTITY = "https://idp.bank-a.example.com/entity"
SP_ENTITY = "https://console.example.com/saml/metadata"
ACS = "https://console.example.com/api/tenants/sso/saml/acs"


# --------------------------------------------------------------------- a test IdP
@pytest.fixture(scope="module")
def idp():
    """A throwaway signing identity, so the tests exercise real signature checking."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "test-idp.bank-a.example.com")])
    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
            .sign(key, hashes.SHA256()))
    return {
        "key": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode(),
        "cert": cert.public_bytes(serialization.Encoding.PEM).decode(),
    }


@pytest.fixture(scope="module")
def other_idp():
    """A second bank's IdP - genuine, but not ours."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "test-idp.bank-b.example.com")])
    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
            .sign(key, hashes.SHA256()))
    return {
        "key": key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode(),
        "cert": cert.public_bytes(serialization.Encoding.PEM).decode(),
    }


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_response(idp, *, name_id="asha.rao@bank-a.example.com",
                   issuer=IDP_ENTITY, audience=SP_ENTITY, recipient=ACS,
                   in_response_to="id-request-1", assertion_id="assert-0001",
                   not_before=None, not_on_or_after=None, groups=("FRAUD-ANALYSTS",),
                   status=saml.STATUS_SUCCESS, sign=True, now=None):
    """A SAML Response as a real IdP would emit it, signed at the assertion."""
    now = now or datetime.now(timezone.utc)
    nb = not_before or (now - timedelta(minutes=1))
    na = not_on_or_after or (now + timedelta(minutes=5))

    resp = etree.Element(f"{{{NS['samlp']}}}Response",
                         nsmap={"samlp": NS["samlp"], "saml": NS["saml"]},
                         ID="resp-1", Version="2.0", IssueInstant=_iso(now),
                         Destination=ACS)
    r_iss = etree.SubElement(resp, f"{{{NS['saml']}}}Issuer")
    r_iss.text = issuer
    st = etree.SubElement(resp, f"{{{NS['samlp']}}}Status")
    etree.SubElement(st, f"{{{NS['samlp']}}}StatusCode", Value=status)

    a = etree.SubElement(resp, f"{{{NS['saml']}}}Assertion",
                         ID=assertion_id, Version="2.0", IssueInstant=_iso(now))
    a_iss = etree.SubElement(a, f"{{{NS['saml']}}}Issuer")
    a_iss.text = issuer

    subj = etree.SubElement(a, f"{{{NS['saml']}}}Subject")
    nid = etree.SubElement(
        subj, f"{{{NS['saml']}}}NameID",
        Format="urn:oasis:names:tc:SAML:2.0:nameid-format:emailAddress")
    nid.text = name_id
    sc = etree.SubElement(subj, f"{{{NS['saml']}}}SubjectConfirmation",
                          Method="urn:oasis:names:tc:SAML:2.0:cm:bearer")
    scd_attrs = {"NotOnOrAfter": _iso(na)}
    if recipient:
        scd_attrs["Recipient"] = recipient
    if in_response_to:
        scd_attrs["InResponseTo"] = in_response_to
    etree.SubElement(sc, f"{{{NS['saml']}}}SubjectConfirmationData", **scd_attrs)

    cond = etree.SubElement(a, f"{{{NS['saml']}}}Conditions",
                            NotBefore=_iso(nb), NotOnOrAfter=_iso(na))
    if audience:
        ar = etree.SubElement(cond, f"{{{NS['saml']}}}AudienceRestriction")
        aud = etree.SubElement(ar, f"{{{NS['saml']}}}Audience")
        aud.text = audience

    authn = etree.SubElement(a, f"{{{NS['saml']}}}AuthnStatement",
                             AuthnInstant=_iso(now), SessionIndex="sess-1")
    ctx = etree.SubElement(authn, f"{{{NS['saml']}}}AuthnContext")
    ccr = etree.SubElement(ctx, f"{{{NS['saml']}}}AuthnContextClassRef")
    ccr.text = "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport"

    if groups:
        stmt = etree.SubElement(a, f"{{{NS['saml']}}}AttributeStatement")
        attr = etree.SubElement(stmt, f"{{{NS['saml']}}}Attribute", Name="groups")
        for g in groups:
            v = etree.SubElement(attr, f"{{{NS['saml']}}}AttributeValue")
            v.text = g

    if sign:
        signed_assertion = XMLSigner(
            signature_algorithm="rsa-sha256",
            digest_algorithm="sha256").sign(a, key=idp["key"], cert=idp["cert"])
        resp.replace(a, signed_assertion)

    return base64.b64encode(etree.tostring(resp)).decode()


def _verify(b64, **kw):
    params = dict(idp_certificate=kw.pop("cert"), idp_entity_id=IDP_ENTITY,
                  sp_entity_id_=SP_ENTITY, expected_acs=ACS,
                  expected_request_id="id-request-1")
    params.update(kw)
    return saml.verify_response(b64, **params)


# --------------------------------------------------------------------- happy path
def test_a_valid_assertion_yields_the_identity(idp):
    identity = _verify(build_response(idp), cert=idp["cert"])
    assert identity.name_id == "asha.rao@bank-a.example.com"
    assert identity.assertion_id == "assert-0001"
    assert identity.attributes["groups"] == ["FRAUD-ANALYSTS"]
    assert identity.session_index == "sess-1"


def test_groups_map_to_a_role_explicitly(idp):
    identity = _verify(build_response(idp), cert=idp["cert"])
    assert saml.map_role(identity, groups_attribute="groups",
                         role_mapping={"FRAUD-ANALYSTS": "analyst"},
                         default_role="analyst") == "analyst"
    # An unmapped group gets the default, never a role named by coincidence.
    admin = _verify(build_response(idp, groups=("admin",), assertion_id="a2"),
                    cert=idp["cert"])
    assert saml.map_role(admin, groups_attribute="groups",
                         role_mapping={"FRAUD-ANALYSTS": "risk_manager"},
                         default_role="analyst") == "analyst"


# --------------------------------------------------------------------- signature
def test_an_unsigned_assertion_is_refused(idp):
    with pytest.raises(saml.SamlError) as exc:
        _verify(build_response(idp, sign=False), cert=idp["cert"])
    assert exc.value.code in ("bad_signature", "empty_signature")


def test_an_assertion_signed_by_another_bank_is_refused(idp, other_idp):
    """The cross-tenant question: one bank's IdP must not mint sessions at another.

    The ACS is one URL shared by every tenant, so this is the check that keeps them
    apart - the assertion must be signed by the certificate configured for *this*
    tenant's provider.
    """
    forged = build_response(other_idp, assertion_id="a3")
    with pytest.raises(saml.SamlError) as exc:
        _verify(forged, cert=idp["cert"])
    assert exc.value.code == "bad_signature"


def test_a_tampered_assertion_is_refused(idp):
    """Signature covers the identity; changing it after signing must break the check."""
    raw = base64.b64decode(build_response(idp, assertion_id="a4"))
    tampered = raw.replace(b"asha.rao@bank-a.example.com",
                           b"ceo.impostor@bank-a.example.com")
    assert tampered != raw
    with pytest.raises(saml.SamlError):
        _verify(base64.b64encode(tampered).decode(), cert=idp["cert"])


def test_no_configured_certificate_refuses_rather_than_trusting_anything(idp):
    """Without a certificate every signature check is vacuous."""
    with pytest.raises(saml.SamlError) as exc:
        _verify(build_response(idp, assertion_id="a5"), cert="   ")
    assert exc.value.code == "no_idp_certificate"


# --------------------------------------------------------------------- wrapping
def test_signature_wrapping_is_refused(idp):
    """The classic SAML break.

    The attacker keeps a genuinely signed assertion and adds an unsigned one carrying
    their chosen identity. The signature still verifies - against the original - and a
    naive SP then reads the attacker's copy.
    """
    signed_b64 = build_response(idp, assertion_id="legit-1")
    doc = etree.fromstring(base64.b64decode(signed_b64))

    evil = etree.fromstring(base64.b64decode(
        build_response(idp, name_id="attacker@evil.example.com",
                       assertion_id="evil-1", sign=False)))
    evil_assertion = evil.find(f".//{{{NS['saml']}}}Assertion")
    doc.insert(0, evil_assertion)

    payload = base64.b64encode(etree.tostring(doc)).decode()
    with pytest.raises(saml.SamlError) as exc:
        _verify(payload, cert=idp["cert"])
    # Refused for having two assertions at all - the material of the attack - rather
    # than by trying to work out which one the signature meant.
    assert exc.value.code in ("assertion_count", "bad_signature")


def test_a_comment_in_the_name_id_is_refused(idp):
    """``<NameID>admin<!---->@evil.com</NameID>`` reads differently in different
    libraries, and that difference has been an account-takeover more than once."""
    doc = etree.fromstring(base64.b64decode(
        build_response(idp, assertion_id="c1", sign=False)))
    nid = doc.find(f".//{{{NS['saml']}}}NameID")
    nid.text = "asha.rao"
    nid.append(etree.Comment("x"))
    nid[-1].tail = "@evil.example.com"
    with pytest.raises(saml.SamlError):
        saml.verify_response(
            base64.b64encode(etree.tostring(doc)).decode(),
            idp_certificate=idp["cert"], idp_entity_id=IDP_ENTITY,
            sp_entity_id_=SP_ENTITY, expected_acs=ACS,
            expected_request_id="id-request-1")


# --------------------------------------------------------------------- XML hardening
def test_a_dtd_is_refused(idp):
    """Billion laughs and file disclosure both start with a DOCTYPE."""
    evil = (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x "boom">]>'
            b'<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"/>')
    with pytest.raises(saml.SamlError) as exc:
        saml.verify_response(base64.b64encode(evil).decode(),
                             idp_certificate=idp["cert"], idp_entity_id=IDP_ENTITY,
                             sp_entity_id_=SP_ENTITY, expected_acs=ACS)
    assert exc.value.code in ("dtd_present", "malformed_xml", "assertion_count")


def test_malformed_xml_is_refused(idp):
    with pytest.raises(saml.SamlError) as exc:
        saml.verify_response(base64.b64encode(b"<not-xml").decode(),
                             idp_certificate=idp["cert"], idp_entity_id=IDP_ENTITY,
                             sp_entity_id_=SP_ENTITY, expected_acs=ACS)
    assert exc.value.code == "malformed_xml"


def test_non_base64_is_refused(idp):
    with pytest.raises(saml.SamlError) as exc:
        saml.verify_response("!!!not base64!!!", idp_certificate=idp["cert"],
                             idp_entity_id=IDP_ENTITY, sp_entity_id_=SP_ENTITY,
                             expected_acs=ACS)
    assert exc.value.code == "bad_encoding"


# --------------------------------------------------------------------- conditions
def test_an_expired_assertion_is_refused(idp):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    with pytest.raises(saml.SamlError) as exc:
        _verify(build_response(idp, assertion_id="e1", not_before=past - timedelta(minutes=5),
                               not_on_or_after=past), cert=idp["cert"])
    assert exc.value.code == "expired"


def test_an_assertion_from_the_future_is_refused(idp):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    with pytest.raises(saml.SamlError) as exc:
        _verify(build_response(idp, assertion_id="f1", not_before=future,
                               not_on_or_after=future + timedelta(minutes=5)),
                cert=idp["cert"])
    assert exc.value.code == "not_yet_valid"


def test_an_assertion_outliving_our_replay_memory_is_refused(idp):
    """Accepting it would mean accepting an unbounded replay window."""
    now = datetime.now(timezone.utc)
    with pytest.raises(saml.SamlError) as exc:
        _verify(build_response(idp, assertion_id="l1", not_before=now - timedelta(minutes=1),
                               not_on_or_after=now + timedelta(days=2)),
                cert=idp["cert"])
    assert exc.value.code == "lifetime_too_long"


def test_an_assertion_for_another_service_provider_is_refused(idp):
    """Perfectly valid - just not for us."""
    with pytest.raises(saml.SamlError) as exc:
        _verify(build_response(idp, assertion_id="au1",
                               audience="https://someone-else.example.com/saml"),
                cert=idp["cert"])
    assert exc.value.code == "audience_mismatch"


def test_an_assertion_from_an_unexpected_issuer_is_refused(idp):
    with pytest.raises(saml.SamlError) as exc:
        _verify(build_response(idp, assertion_id="i1",
                               issuer="https://impostor.example.com/entity"),
                cert=idp["cert"])
    assert exc.value.code == "issuer_mismatch"


def test_an_assertion_for_a_different_consumer_url_is_refused(idp):
    with pytest.raises(saml.SamlError) as exc:
        _verify(build_response(idp, assertion_id="r1",
                               recipient="https://evil.example.com/acs"),
                cert=idp["cert"])
    assert exc.value.code == "recipient_mismatch"


def test_an_idp_failure_status_is_reported_as_such(idp):
    with pytest.raises(saml.SamlError) as exc:
        _verify(build_response(idp, assertion_id="s1", sign=False,
                               status="urn:oasis:names:tc:SAML:2.0:status:Requester"),
                cert=idp["cert"])
    assert exc.value.code == "idp_status_failure"


# --------------------------------------------------------------------- login CSRF
def test_an_unsolicited_assertion_is_refused_against_a_pending_request(idp):
    """Login-CSRF: the attacker POSTs their own valid assertion, and the victim ends up
    signed in as the attacker and files their work into the wrong account."""
    with pytest.raises(saml.SamlError) as exc:
        _verify(build_response(idp, assertion_id="u1", in_response_to=None),
                cert=idp["cert"])
    assert exc.value.code == "unsolicited_assertion"


def test_an_assertion_answering_a_different_request_is_refused(idp):
    with pytest.raises(saml.SamlError) as exc:
        _verify(build_response(idp, assertion_id="u2",
                               in_response_to="id-somebody-elses-request"),
                cert=idp["cert"])
    assert exc.value.code == "in_response_to_mismatch"


# --------------------------------------------------------------------- SP metadata
def test_sp_metadata_advertises_only_what_is_enforced():
    md = saml.sp_metadata("https://console.example.com").decode()
    assert 'WantAssertionsSigned="true"' in md
    assert saml.acs_url("https://console.example.com") in md
    doc = etree.fromstring(md.encode())
    assert doc.get("entityID") == SP_ENTITY


def test_the_authn_request_is_deflated_and_addressed_to_the_idp():
    import zlib
    from urllib.parse import parse_qs, urlparse

    url = saml.authn_request(idp_sso_url="https://idp.example.com/sso",
                             sp_entity=SP_ENTITY, acs=ACS, request_id="id-abc")
    q = parse_qs(urlparse(url).query)
    xml = zlib.decompress(base64.b64decode(q["SAMLRequest"][0]), -15)
    doc = etree.fromstring(xml)
    assert doc.get("ID") == "id-abc"
    assert doc.get("Destination") == "https://idp.example.com/sso"
    assert doc.get("AssertionConsumerServiceURL") == ACS
