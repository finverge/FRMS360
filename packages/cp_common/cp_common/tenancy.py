"""Tenant-scope resolution — the heart of multi-tenant isolation.

A platform_admin may target any tenant (via the path/query tenant_id). A tenant_admin
is pinned to the tenant_id embedded in their token and may not read or write another
tenant's data. Every tenant-scoped repository call must pass through this.
"""
from fastapi import HTTPException, status

from .auth import Principal


def resolve_tenant_scope(principal: Principal, requested_tenant_id: str) -> str:
    """Return the tenant_id the caller is allowed to act on, or 403."""
    if principal.is_platform_admin:
        return requested_tenant_id
    if principal.tenant_id and principal.tenant_id == requested_tenant_id:
        return requested_tenant_id
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        "You may not access another tenant's resources",
    )
