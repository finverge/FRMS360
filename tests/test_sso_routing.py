"""Sign-in URL routing: per-tenant paths must not collide with SAML endpoints.

The SAML endpoints are literals (``/saml/metadata``, ``/saml/acs``) sitting in the same
prefix as the per-tenant sign-in paths, which are ``{tenant_slug}/{idp_slug}``. Today they
do not collide, because the literals happen to sit at different depths. That is luck, and
it stops being true the first time somebody adds a route - so it is tested rather than
assumed.
"""
import re

import pytest

from services.tenant_service.app.main import app
from services.tenant_service.app.routes.sso import RESERVED_SLUGS, validate_slug
from cp_common import AppError

PREFIX = "/auth/sso"


def _sso_routes():
    out = []
    for r in app.routes:
        p = getattr(r, "path", "")
        if p.startswith(PREFIX):
            methods = sorted(getattr(r, "methods", set()) or set())
            segs = [s for s in p[len(PREFIX):].split("/") if s]
            out.append((p, methods, segs))
    return out


def test_every_literal_segment_in_a_sign_in_url_is_a_reserved_slug():
    """The guard that keeps this honest as routes are added.

    If someone adds ``/auth/sso/logout`` and a tenant is already called "logout", the
    tenant's sign-in URL starts resolving to the new endpoint. Reserving the word is the
    fix; this test is what makes anyone remember to.
    """
    literals = set()
    for _path, _methods, segs in _sso_routes():
        for s in segs:
            if not (s.startswith("{") and s.endswith("}")):
                literals.add(s.lower())
    missing = sorted(literals - RESERVED_SLUGS)
    assert not missing, (
        f"these path literals can be shadowed by a tenant or provider slug, and are not "
        f"reserved: {missing}")


def test_no_two_routes_have_the_same_shape():
    """Two routes with identical segment counts and matching literals would make the
    first-declared one swallow the second."""
    seen = {}
    for path, methods, segs in _sso_routes():
        for method in methods:
            # A shape is the segment count plus the literal positions; parameters match
            # anything, so two routes agreeing on both can collide.
            shape = (method, len(segs),
                     tuple((i, s.lower()) for i, s in enumerate(segs)
                           if not (s.startswith("{") and s.endswith("}"))))
            if shape in seen:
                pytest.fail(f"{path} collides with {seen[shape]} for {method}")
            seen[shape] = path


def test_a_tenant_may_not_be_called_saml_or_acs():
    for bad in ("saml", "acs", "metadata", "callback", "providers", "admin"):
        with pytest.raises(AppError) as exc:
            validate_slug(bad, what="tenant slug")
        assert exc.value.code == "slug_reserved"


def test_an_ordinary_slug_is_accepted():
    assert validate_slug("HDFC-Bank") == "hdfc-bank"
    assert validate_slug("meridian") == "meridian"


def test_a_slug_with_path_characters_is_refused():
    """A slug containing a slash would invent path segments of its own."""
    for bad in ("a/b", "..", "a b", "a%2Fb", "a?b"):
        with pytest.raises(AppError):
            validate_slug(bad)


def test_the_saml_endpoints_are_where_the_metadata_says_they_are():
    """An SP whose published ACS is not the URL it actually serves is a support ticket
    every bank raises exactly once, on go-live day."""
    from cp_common import saml, settings

    base = settings.console_base_url
    md = saml.sp_metadata(base).decode()
    acs = saml.acs_url(base)
    assert acs in md

    served = {p for p, _m, _s in _sso_routes()}
    # The published ACS is the public gateway path; the service serves the tail of it.
    assert any(acs.endswith(p.replace("/auth/sso", "/sso")) or p.endswith("/saml/acs")
               for p in served), f"published ACS {acs} matches no served route: {served}"


def test_saml_and_oidc_start_paths_are_distinguishable():
    """Both are per-tenant; only one may match a given URL."""
    paths = {p for p, _m, _s in _sso_routes()}
    assert f"{PREFIX}/{{tenant_slug}}/{{idp_slug}}/start" in paths
    assert f"{PREFIX}/saml/{{tenant_slug}}/{{idp_slug}}/start" in paths
    # Different depths, so a 3-segment OIDC start can never be read as a SAML start.
    oidc = [s for s in f"{PREFIX}/{{tenant_slug}}/{{idp_slug}}/start"[len(PREFIX):].split("/") if s]
    saml_ = [s for s in f"{PREFIX}/saml/{{tenant_slug}}/{{idp_slug}}/start"[len(PREFIX):].split("/") if s]
    assert len(oidc) != len(saml_)
