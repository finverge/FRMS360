"""What a role may do, as every service outside tenant-service decides it: from the database.

A tenant's roles are rows in ``tenant.tenant_roles``, written by its administrators. This
module fetches them over HTTP from tenant-service (the only service with a grant on that
schema; schema-per-service, HLD AD-03), caches them against the shared "roles" generation,
and answers every authorisation question from them. There is no in-code catalogue to fall
back on and no list of role names anywhere: a role name that is not a row for this tenant
can do nothing, and a tenant whose roles cannot be fetched (and were never cached) is refused
rather than guessed at. Reducing a role in the console is therefore effective on the very next
request, with no deploy.

The one exception is the platform operator (``PLATFORM_OPERATOR``): Finverge staff who work
across tenants and are not part of any bank's catalogue. They are recognised by that single
name, and cannot be created, edited or removed by a tenant.

Capabilities a role can hold, all stored on its row:

* modules and dashboards it may open;
* ``can_admin_tenant``, ``can_reveal_pii``, ``can_activate_config``;
* ``permissions`` - the gated actions it may perform (``cp_common.permissions``).

Unlike a payment-path lookup this is allowed to **block briefly on a cache miss** rather than
serve stale-and-refresh-in-background: a blocked authorisation check that resolves in under a
second is preferable to briefly honouring a grant an administrator has just withdrawn.
"""
from __future__ import annotations

import logging
import threading

import httpx

from . import rbac
from .cache import VersionedCache, build_backend
from .permissions import ALL_PERMISSIONS
from .settings import settings

#: Finverge operating staff. Not a tenant role; see the module docstring.
PLATFORM_OPERATOR = "platform_admin"

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
    """This tenant's role rows, fetching (and caching) when stale.

    A failed fetch is never recorded as "this tenant has no roles": that would turn one
    network blip into a lasting lock-out. With a previous copy it keeps serving that copy
    and retries on the next call; with none it returns nothing for this call only, which
    every caller treats as no access (fail closed), and retries on the next.
    """
    cache = _role_cache()
    if cache is None:
        try:
            return _fetch(tenant_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not fetch roles for tenant %s: %s", tenant_id, str(exc)[:160])
            return {}
    gen = cache.current_generation()
    with _LOCK:
        stale = _GENERATIONS.get(tenant_id) != gen or tenant_id not in _ROLES
    if stale:
        try:
            roles = _fetch(tenant_id)
        except Exception as exc:  # noqa: BLE001
            with _LOCK:
                last = _ROLES.get(tenant_id)
            log.warning("could not fetch roles for tenant %s: %s - %s", tenant_id,
                        str(exc)[:160],
                        f"serving the last copy ({len(last)} role(s))" if last is not None
                        else "no copy to fall back on, refusing")
            return last if last is not None else {}
        with _LOCK:
            _ROLES[tenant_id] = roles
            _GENERATIONS[tenant_id] = gen
    with _LOCK:
        return _ROLES.get(tenant_id, {})


def platform_operator() -> rbac.Role:
    return rbac.Role(PLATFORM_OPERATOR, "Platform Administrator", tuple(rbac.ALL_MODULES),
                     tuple(rbac.DASHBOARDS), tenant_scoped=False, can_admin_tenant=True,
                     can_reveal_pii=False, can_activate_config=True,
                     permissions=ALL_PERMISSIONS)


def _row_to_role(name: str, row: dict) -> rbac.Role:
    return rbac.Role(
        name=name, label=row.get("label", name),
        modules=tuple(row.get("modules", ())), dashboards=tuple(row.get("dashboards", ())),
        tenant_scoped=True,
        can_admin_tenant=bool(row.get("can_admin_tenant")),
        can_reveal_pii=bool(row.get("can_reveal_pii")),
        can_activate_config=bool(row.get("can_activate_config")),
        permissions=tuple(row.get("permissions", ())),
        description=row.get("description", "") or "")


def role_for(tenant_id: str | None, name: str) -> rbac.Role | None:
    """This tenant's role of that name, or ``None`` when it has no such role.

    ``None`` is "no access" for every function below - an unknown name, a role an
    administrator deleted, and a tenant whose roles could not be read all look the same.
    """
    if name == PLATFORM_OPERATOR:
        return platform_operator()
    if not tenant_id:
        return None
    row = _custom_catalogue(tenant_id).get(name)
    return _row_to_role(name, row) if row is not None else None


def catalogue(tenant_id: str) -> dict[str, rbac.Role]:
    """Every role this tenant has, by name."""
    return {n: _row_to_role(n, r) for n, r in _custom_catalogue(tenant_id).items()}


def get_role(tenant_id: str | None, role_name: str) -> rbac.Role:
    """The role, or an empty one that can do nothing - never a default with privileges."""
    return role_for(tenant_id, role_name) or rbac.Role(
        role_name, role_name, (), (), tenant_scoped=True, can_admin_tenant=False)


def has_permission(tenant_id: str | None, role_name: str, permission: str) -> bool:
    """Whether this tenant's role of that name may perform the gated action."""
    role = role_for(tenant_id, role_name)
    return bool(role and permission in role.permissions)


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
