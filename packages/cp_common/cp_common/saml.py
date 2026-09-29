"""SAML 2.0 service-provider support.

OIDC is the protocol banks are moving to; SAML is the one their ADFS estate already runs,
and a platform that cannot speak it is a platform their IAM team cannot onboard. This is
the SP half: metadata, an AuthnRequest, and - the part that matters - verification of what
comes back.

**Signature verification is delegated to signxml, deliberately.** Hand-rolling it means
implementing exclusive canonicalisation, and the history of SAML is a history of
implementations that got that subtly wrong and accepted forged assertions for years. The
one thing this module does insist on is using ``signed_xml`` - the subtree the signature
actually covered - and never the parsed document. That single discipline is what defeats
XML Signature Wrapping, where an attacker keeps a validly-signed assertion and bolts an
unsigned one alongside it: the signature checks out, and a naive implementation then reads
the attacker's copy.

The other defences here, each against a real attack class:

* **No DTDs, no entities, no network.** The parser is constructed to refuse them, so
  billion-laughs and file-disclosure via external entity never reach the verifier.
* **Exactly one Assertion.** Multiple assertions are the raw material of wrapping attacks
  and have no legitimate use here.
* **Replay is refused.** An assertion is a bearer credential; without an ID cache, anyone
  who observes one can present it again until it expires.
* **Audience, Destination and InResponseTo are all checked.** An assertion minted for a
  different service provider is a valid assertion - just not for us.
* **Clock skew is bounded, not ignored.** Real IdPs drift; unbounded tolerance means
  expiry stops meaning anything.
"""
from __future__ import annotations

import base64
import secrets
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from lxml import etree
from signxml import XMLVerifier

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "md": "urn:oasis:names:tc:SAML:2.0:metadata",
}

#: How far an IdP's clock may differ from ours before its assertions are refused. Real
#: deployments drift by seconds; anything beyond a couple of minutes is a broken clock or
#: a replayed credential, and treating expiry as advisory defeats the point of it.
CLOCK_SKEW = timedelta(minutes=2)

#: Upper bound on how long a consumed assertion id is remembered. An assertion valid for
#: longer than this is refused outright rather than trusted beyond our replay memory.
MAX_ASSERTION_LIFETIME = timedelta(hours=1)

STATUS_SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"


class SamlError(Exception):
    """Refusal, with a reason safe to log. Never contains assertion content."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code


def _parser() -> etree.XMLParser:
    """A parser that refuses everything SAML does not need.

    ``resolve_entities=False`` alone is not enough - ``no_network`` and ``load_dtd``
    matter too, and ``huge_tree`` stays off so a deeply nested document cannot exhaust
    memory before anything is validated.
    """
    return etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, dtd_validation=False,
        huge_tree=False, recover=False)


def _parse(xml: bytes) -> etree._Element:
    try:
        doc = etree.fromstring(xml, parser=_parser())
    except etree.XMLSyntaxError as exc:
        raise SamlError(f"Response is not well-formed XML: {exc}", "malformed_xml")
    # A DOCTYPE that survived parsing, or any entity declaration, means the parser was
    # not the hardened one. Checked rather than assumed.
    if doc.getroottree().docinfo.doctype:
        raise SamlError("Response carries a DOCTYPE, which is refused.", "dtd_present")
    return doc


def _text(el) -> str:
    """Element text with descendants excluded.

    ``NameID`` containing a comment splits into several text nodes, and different
    libraries reassemble them differently - the trick behind the comment-truncation
    account-takeover. Only the first text node is taken, and a comment makes the value
    ambiguous, so it is refused.
    """
    if el is None:
        return ""
    if any(isinstance(c, etree._Comment) for c in el):
        raise SamlError("Identity value contains a comment, which is refused.",
                        "comment_in_value")
    return (el.text or "").strip()


def _dt(value: str) -> datetime:
    v = (value or "").strip()
    if not v:
        raise SamlError("Missing timestamp on the assertion.", "missing_timestamp")
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(v)
    except ValueError:
        raise SamlError(f"Unparseable SAML timestamp: {value!r}", "bad_timestamp")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------- SP side
def sp_entity_id(console_base_url: str) -> str:
    return f"{console_base_url.rstrip('/')}/saml/metadata"


def acs_url(console_base_url: str) -> str:
    return f"{console_base_url.rstrip('/')}/api/tenants/sso/saml/acs"


def sp_metadata(console_base_url: str) -> bytes:
    """SP metadata for the bank's IAM team to import.

    ``WantAssertionsSigned`` is advertised as true because this SP genuinely refuses
    unsigned assertions - advertising a requirement that is not enforced is how a
    misconfiguration on either side goes unnoticed.
    """
    entity = sp_entity_id(console_base_url)
    acs = acs_url(console_base_url)
    md = etree.Element(f"{{{NS['md']}}}EntityDescriptor", nsmap={"md": NS["md"]},
                       entityID=entity)
    sso = etree.SubElement(md, f"{{{NS['md']}}}SPSSODescriptor",
                           protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol",
                           AuthnRequestsSigned="false", WantAssertionsSigned="true")
    nameid = etree.SubElement(sso, f"{{{NS['md']}}}NameIDFormat")
    nameid.text = "urn:oasis:names:tc:SAML:2.0:nameid-format:emailAddress"
    etree.SubElement(
        sso, f"{{{NS['md']}}}AssertionConsumerService",
        Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
        Location=acs, index="0", isDefault="true")
    return etree.tostring(md, pretty_print=True, xml_declaration=True, encoding="UTF-8")


def new_request_id() -> str:
    # Must start with a letter: xs:ID forbids a leading digit, and some IdPs reject it.
    return "id" + secrets.token_hex(16)


def authn_request(*, idp_sso_url: str, sp_entity: str, acs: str,
                  request_id: str, relay_state: str = "",
                  now: datetime | None = None) -> str:
    """A redirect-binding AuthnRequest URL."""
    now = now or datetime.now(timezone.utc)
    root = etree.Element(
        f"{{{NS['samlp']}}}AuthnRequest",
        nsmap={"samlp": NS["samlp"], "saml": NS["saml"]},
        ID=request_id, Version="2.0",
        IssueInstant=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        Destination=idp_sso_url, AssertionConsumerServiceURL=acs,
        ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST")
    issuer = etree.SubElement(root, f"{{{NS['saml']}}}Issuer")
    issuer.text = sp_entity
    xml = etree.tostring(root, encoding="UTF-8", xml_declaration=False)
    # HTTP-Redirect binding: raw-deflate, base64, urlencode.
    deflated = zlib.compress(xml)[2:-4]
    params = {"SAMLRequest": base64.b64encode(deflated).decode()}
    if relay_state:
        params["RelayState"] = relay_state
    sep = "&" if "?" in idp_sso_url else "?"
    return f"{idp_sso_url}{sep}{urlencode(params)}"


# --------------------------------------------------------------------- IdP side
@dataclass
class SamlIdentity:
    name_id: str
    session_index: str = ""
    assertion_id: str = ""
    not_on_or_after: datetime | None = None
    attributes: dict[str, list[str]] = field(default_factory=dict)

    def first(self, name: str, default: str = "") -> str:
        vals = self.attributes.get(name) or []
        return vals[0] if vals else default


def verify_response(
    saml_response_b64: str, *,
    idp_certificate: str,
    idp_entity_id: str,
    sp_entity_id_: str,
    expected_acs: str,
    expected_request_id: str = "",
    now: datetime | None = None,
) -> SamlIdentity:
    """Verify a SAMLResponse and return the identity it asserts.

    Raises ``SamlError`` on any failure. There is no partial success: an assertion is
    either wholly trustworthy or it is refused.
    """
    now = now or datetime.now(timezone.utc)
    if not idp_certificate.strip():
        # Without a certificate every signature check is vacuous, and the SP would accept
        # anything. Refused loudly rather than degraded quietly.
        raise SamlError(
            "No IdP signing certificate is configured for this provider, so no assertion "
            "can be trusted.", "no_idp_certificate")

    try:
        raw = base64.b64decode(saml_response_b64, validate=True)
    except Exception:
        raise SamlError("SAMLResponse is not valid base64.", "bad_encoding")

    doc = _parse(raw)

    # Status first: a failed authentication carries no assertion, and reporting
    # "no assertion found" for a user who simply cancelled is a bad diagnostic.
    status = doc.find(".//samlp:Status/samlp:StatusCode", NS)
    if status is not None:
        value = status.get("Value", "")
        if value != STATUS_SUCCESS:
            raise SamlError(f"The identity provider refused the sign-in ({value}).",
                            "idp_status_failure")

    assertions = doc.findall(".//saml:Assertion", NS)
    if len(assertions) != 1:
        # Zero is a malformed or failed response; more than one is the raw material of a
        # signature-wrapping attack and has no legitimate use here.
        raise SamlError(
            f"Expected exactly one assertion, found {len(assertions)}.",
            "assertion_count")

    # ---- signature ---------------------------------------------------------------
    # signed_xml is the subtree the signature actually covered. Reading anything else -
    # including the document we just parsed - is precisely the wrapping vulnerability.
    try:
        verified = XMLVerifier().verify(
            raw, x509_cert=idp_certificate, expect_references=1)
    except Exception as exc:  # noqa: BLE001
        raise SamlError(f"Assertion signature is not valid: "
                        f"{type(exc).__name__}", "bad_signature")

    signed = verified.signed_xml
    if signed is None:
        raise SamlError("Signature covered nothing.", "empty_signature")

    tag = etree.QName(signed).localname
    if tag == "Response":
        found = signed.findall(".//saml:Assertion", NS)
        if len(found) != 1:
            raise SamlError("Signed response does not contain exactly one assertion.",
                            "assertion_count")
        signed_assertion = found[0]
    elif tag == "Assertion":
        signed_assertion = signed
    else:
        raise SamlError(f"Signature covers a <{tag}>, not an assertion or response.",
                        "unexpected_signed_element")

    # Everything below reads from signed_assertion only.
    assertion_id = signed_assertion.get("ID", "")
    if not assertion_id:
        raise SamlError("Assertion has no ID, so replay cannot be prevented.",
                        "missing_assertion_id")

    issuer = _text(signed_assertion.find("saml:Issuer", NS))
    if issuer != idp_entity_id:
        raise SamlError(
            "Assertion was issued by a different identity provider than the one "
            "configured for this tenant.", "issuer_mismatch")

    # ---- conditions --------------------------------------------------------------
    conditions = signed_assertion.find("saml:Conditions", NS)
    not_on_or_after = None
    if conditions is not None:
        nb = conditions.get("NotBefore")
        na = conditions.get("NotOnOrAfter")
        if nb and now + CLOCK_SKEW < _dt(nb):
            raise SamlError("Assertion is not yet valid.", "not_yet_valid")
        if na:
            not_on_or_after = _dt(na)
            if now - CLOCK_SKEW >= not_on_or_after:
                raise SamlError("Assertion has expired.", "expired")
            if not_on_or_after - now > MAX_ASSERTION_LIFETIME:
                # A very long-lived assertion outlives our replay memory, so accepting it
                # would mean accepting an unbounded replay window.
                raise SamlError(
                    "Assertion is valid for longer than this service will remember it, "
                    "so replay could not be prevented.", "lifetime_too_long")

        audiences = [_text(a) for a in
                     conditions.findall("saml:AudienceRestriction/saml:Audience", NS)]
        if audiences and sp_entity_id_ not in audiences:
            # A perfectly valid assertion - for somebody else.
            raise SamlError(
                "Assertion is addressed to a different service provider.",
                "audience_mismatch")

    # ---- subject confirmation ----------------------------------------------------
    subject = signed_assertion.find("saml:Subject", NS)
    if subject is None:
        raise SamlError("Assertion has no subject.", "no_subject")
    name_id = _text(subject.find("saml:NameID", NS))
    if not name_id:
        raise SamlError("Assertion carries no NameID.", "no_name_id")

    for scd in subject.findall(
            "saml:SubjectConfirmation/saml:SubjectConfirmationData", NS):
        recipient = scd.get("Recipient")
        if recipient and recipient.rstrip("/") != expected_acs.rstrip("/"):
            raise SamlError(
                "Assertion was minted for a different consumer URL.",
                "recipient_mismatch")
        na = scd.get("NotOnOrAfter")
        if na and now - CLOCK_SKEW >= _dt(na):
            raise SamlError("Subject confirmation has expired.", "expired")
        in_response_to = scd.get("InResponseTo")
        if expected_request_id and in_response_to and in_response_to != expected_request_id:
            raise SamlError(
                "Assertion does not answer the request this browser started.",
                "in_response_to_mismatch")
        if expected_request_id and not in_response_to:
            # We started an SP-initiated flow; an assertion with no InResponseTo is
            # unsolicited, and accepting it here would allow a login-CSRF.
            raise SamlError(
                "Unsolicited assertion presented against a pending sign-in request.",
                "unsolicited_assertion")

    # ---- attributes --------------------------------------------------------------
    attributes: dict[str, list[str]] = {}
    for attr in signed_assertion.findall(
            "saml:AttributeStatement/saml:Attribute", NS):
        name = attr.get("Name") or attr.get("FriendlyName") or ""
        if not name:
            continue
        attributes[name] = [
            _text(v) for v in attr.findall("saml:AttributeValue", NS)
            if _text(v)]

    session_index = ""
    stmt = signed_assertion.find("saml:AuthnStatement", NS)
    if stmt is not None:
        session_index = stmt.get("SessionIndex", "") or ""

    return SamlIdentity(name_id=name_id, session_index=session_index,
                        assertion_id=assertion_id, not_on_or_after=not_on_or_after,
                        attributes=attributes)


def map_role(identity: SamlIdentity, *, groups_attribute: str,
             role_mapping: dict, default_role: str) -> str:
    """Groups to platform role, explicitly and never by coincidence of naming.

    Same rule the OIDC path follows: a directory group called "admin" must not become a
    platform administrator because the names happen to match.
    """
    groups = identity.attributes.get(groups_attribute) or []
    for g in groups:
        if g in role_mapping:
            return role_mapping[g]
    return default_role
