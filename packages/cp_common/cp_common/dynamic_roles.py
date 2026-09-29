"""cp_common.rbac, extended to also recognise a tenant's own BR-113 custom roles.

Every one of the ten fixed roles resolves with **zero network calls**, through the
exact same in-process ``rbac.ROLES`` lookup every service has always used — this
changes nothing for them, and nothing for a tenant that has never created a custom
role. Only a role name that is *not* one of the ten triggers a fetch.

That fetch has to happen over HTTP: the five services this module exists for have no
database grant on the ``tenant`` schema (schema-per-service, HLD AD-03), so none of
them can query ``tenant_roles`` directly — only tenant-service, which owns it, can.
The result is cached against the shared "roles" generation (the same
``VersionedCache`` mechanism config-service's rule/policy changes already use),
invalidated the instant tenant-service bumps it on a role create/update/confirm/delete.

Unlike decision_service's ``catalogue_for()`` this is allowed to **block briefly on a
cache miss** rather than serve stale-and-refresh-in-background. Two things make that
the right trade here rather than the weaker choice: this is not the payment-critical
path any of these five services sit on, and a miss only happens for the rare tenant
that has actually created a custom role — for everyone else this function never
reaches the network at all. A blocked authorisation check that resolves in under a
second is preferable to briefly honouring a stale grant.

Scope: this covers five generic capability primitives — module access, dashboard
access, ``can_admin_tenant``, ``can_reveal_pii``, ``can_activate_config`` — the
complete set BR-113's write path lets a tenant set on a role. config_service's
``maker_checker.ensure_eligible()`` is built on the last of these, so a custom role
granted ``can_activate_config`` (a "risk_manager-equivalent" grant, without needing
full tenant admin) may propose or confirm a configuration activation the same as the
three fixed roles that always could. A smaller number of call sites still gate a
specific action behind a *named list* of fixed roles for reasons narrower than any of
these five flags (analytics_service's ``FILING_ROLES``/``CTR_ROLES``/etc., which mix
in roles like ``principal_officer`` that this platform has no flag for at all) —
extending those needs capability flags this platform does not have yet, which is real,
separately-scoped work, not attempted here.
"""
from __future__ import annotations

import logging
import threading

import httpx

from . import rbac
from .cache import VersionedCache, build_backend
from .settings import settings

log = logging.getLogger("cp_common.dynamic_roles")

#: Not on any hot path — see the module docstring — but still short enough that a
#: stuck tenant-service fails an authorisation check quickly rather than hanging a
#: request indefinitely.
FETCH_TIMEOUT_SECONDS = 5.0

_CACHE = None
_LOCK = threading.Lock()
_ROLES: dict[str, dict] = {}        # tenant_id -> {role_name: {...}}
_GENERATIONS: dict[str, int] = {}   # tenant_id -> generation the cached copy matches


def _role_cache():
    """The shared-generation cache, built lazily so importing this module needs no DB
    and no network — most processes that import it never call anything but the
    zero-network fixed-role path."""
    global _CACHE
    if _CACHE is None:
        try:
            backend = build_backend(settings.cache_backend)
            _CACHE = VersionedCache(backend, "roles")
        except Exception as exc:  # noqa: BLE001
            log.warning("no cache backend for dynamic roles (%s); custom roles will "
                       "not be recognised outside tenant-service until this resolves",
                       str(exc)[:160])
            return None
    return _CACHE


def _fetch(tenant_id: str) -> dict:
    r = httpx.get(f"{settings.tenant_service_url}/tenants/internal/{tenant_id}/roles",
                  headers={"x-internal-key": settings.internal_api_key},
                  timeout=FETCH_TIMEOUT_SECONDS)
    r.raise_for_status()
    return r.json().get("roles", {})


def invalidate() -> None:
    """For tests: drop every cached tenant's roles and forget the cache singleton."""
    global _CACHE
    with _LOCK:
        _ROLES.clear()
        _GENERATIONS.clear()
        _CACHE = None


def _custom_catalogue(tenant_id: str) -> dict:
    """This tenant's own custom-role rows, fetching (and caching) on a miss."""
    cache = _role_cache()
    gen = cache.current_generation() if cache else 0
    with _LOCK:
        stale = cache is not None and _GENERATIONS.get(tenant_id) != gen
        have_none_yet = tenant_id not in _ROLES
    if cache is not None and (stale or have_none_yet):
        try:
            roles = _fetch(tenant_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not fetch dynamic roles for tenant %s: %s — falling "
                       "back to whatever was last cached (%d role(s))",
                       tenant_id, str(exc)[:160], len(_ROLES.get(tenant_id, {})))
            roles = _ROLES.get(tenant_id, {})
        with _LOCK:
            _ROLES[tenant_id] = roles
            _GENERATIONS[tenant_id] = gen
    with _LOCK:
        return _ROLES.get(tenant_id, {})


def role_for(tenant_id: str, name: str) -> rbac.Role | None:
    """The fixed catalogue first — zero network, byte-identical to plain
    ``rbac.get_role`` for any of the ten. Only a name outside that set reaches this
    tenant's own custom-role catalogue. Returns ``None`` for a name that is neither a
    fixed role nor a custom row this tenant actually has — every function below treats
    that as "no access", the same as an unrecognised name always meant."""
    fixed = rbac.ROLES.get(name)
    if fixed is not None:
        return fixed
    custom = _custom_catalogue(tenant_id).get(name)
    if custom is None:
        return None
    return rbac.Role(
        name=name, label=custom.get("label", name),
        modules=tuple(custom.get("modules", ())),
        dashboards=tuple(custom.get("dashboards", ())),
        tenant_scoped=True,
        can_admin_tenant=bool(custom.get("can_admin_tenant")),
        can_reveal_pii=bool(custom.get("can_reveal_pii")),
        can_activate_config=bool(custom.get("can_activate_config")))


def get_role(tenant_id: str, role_name: str) -> rbac.Role:
    """Tenant-aware ``rbac.get_role``. Falls back to ``analyst`` for a name that
    resolves to nothing at all, matching ``rbac.get_role``'s own fallback — existing
    callers that never handled a ``None`` keep exactly that contract."""
    return role_for(tenant_id, role_name) or rbac.ROLES["analyst"]


def can_access_module(tenant_id: str, role_name: str, module: str) -> bool:
    role = role_for(tenant_id, role_name)
    return bool(role and module in role.modules)


def can_access_dashboard(tenant_id: str, role_name: str, dashboard: str) -> bool:
    role = role_for(tenant_id, role_name)
    return bool(role and dashboard in role.dashboards)


def can_admin_tenant(tenant_id: str, role_name: str) -> bool:
    role = role_for(tenant_id, role_name)
    return bool(role and role.can_admin_tenant)


def can_reveal_pii(tenant_id: str, role_name: str) -> bool:
    role = role_for(tenant_id, role_name)
    return bool(role and role.can_reveal_pii)


def can_activate_config(tenant_id: str, role_name: str) -> bool:
    role = role_for(tenant_id, role_name)
    return bool(role and role.can_activate_config)
