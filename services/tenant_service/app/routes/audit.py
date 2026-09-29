"""Presentation tier — the audit trail.

Was platform-staff-only. cp_common.rbac has always granted the ``audit`` module to
six tenant-scoped roles (tenant_admin, risk_manager, principal_officer, supervisor,
cro, rbi_inspector) — the console has shown all of them an "Audit" nav item this whole
time, and every one of them 403'd the moment they clicked it. That mismatch is fixed
here: a tenant-scoped caller with audit access now sees their own tenant's trail, and
only their own — resolve_tenant_scope enforces that the same way it does everywhere
else, and a role's audit access is checked dynamically (BR-113's ``roles.capability``),
so a custom role granted the audit module gets exactly what a seeded one would.
Platform staff keep the unscoped, cross-tenant view this always was for them.
"""
import csv
import io
import re

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from cp_common import (AppError, AuditLog, Principal, get_current_principal,
                       get_session, resolve_tenant_scope)
from cp_common.rbac import MOD_AUDIT

from .. import roles as roles_mod
from ..schemas import AuditOut

router = APIRouter(prefix="/audit", tags=["audit"])


def _rows(db: Session, *, limit: int, tenant_id: str | None,
         action_prefix: str | None) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit)
    if tenant_id:
        stmt = stmt.where(AuditLog.tenant_id == tenant_id)
    if action_prefix:
        # Escape %/_ so a prefix containing them (there are none in this platform's
        # closed action vocabulary today, but nothing enforces that) filters literally
        # rather than as a wildcard.
        escaped = action_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(AuditLog.action.like(f"{escaped}%", escape="\\"))
    return list(db.scalars(stmt))


def _resolve_scope(principal: Principal, db: Session, tenant_id: str | None) -> str | None:
    """Platform staff may see any tenant, or every tenant (tenant_id=None).

    Anyone else must have audit access - checked dynamically, so a custom role BR-113
    granted the audit module works exactly like a seeded one - and is pinned to their
    own tenant regardless of what tenant_id they asked for.
    """
    if principal.is_platform_admin:
        return tenant_id
    if not principal.tenant_id:
        raise AppError("No tenant to scope this to", 403, "no_tenant")
    effective = resolve_tenant_scope(principal, tenant_id or principal.tenant_id)
    if not roles_mod.capability(db, effective, principal.role, MOD_AUDIT):
        raise AppError("Insufficient role", 403, "insufficient_role")
    return effective


@router.get("", response_model=list[AuditOut])
def list_audit(
    limit: int = Query(default=50, le=500),
    tenant_id: str | None = Query(default=None),
    action_prefix: str | None = Query(default=None, max_length=64),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> list[AuditOut]:
    scope = _resolve_scope(principal, db, tenant_id)
    rows = _rows(db, limit=limit, tenant_id=scope, action_prefix=action_prefix)
    return [AuditOut.model_validate(r) for r in rows]


@router.get("/export")
def export_audit_csv(
    limit: int = Query(default=500, le=500),
    tenant_id: str | None = Query(default=None),
    action_prefix: str | None = Query(default=None, max_length=64),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> Response:
    """A downloadable report over the same trail ``GET /audit`` serves — e.g.
    ``?action_prefix=role.`` for a role-management audit report. Same access rule as
    the JSON view: platform staff see any tenant, everyone else sees only their own,
    checked dynamically against the caller's actual role.
    """
    scope = _resolve_scope(principal, db, tenant_id)
    rows = _rows(db, limit=limit, tenant_id=scope, action_prefix=action_prefix)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "actor", "actor_role", "tenant_id", "action",
                     "target_type", "target_id", "status", "detail"])
    for r in rows:
        writer.writerow([r.ts.isoformat(), r.actor, r.actor_role, r.tenant_id,
                         r.action, r.target_type, r.target_id, r.status,
                         # Flattened to a compact string, not re-parsed JSON - a CSV
                         # cell is not a good home for nested structure, and this is
                         # for a human reviewing the report, not another program.
                         "; ".join(f"{k}={v}" for k, v in (r.detail or {}).items())])

    # A query param reflected into a response header is a header-injection surface
    # (CRLF, quotes) regardless of how implausible the input looks - sanitised to a
    # safe charset before it ever reaches Content-Disposition, not merely truncated.
    safe_prefix = re.sub(r"[^a-zA-Z0-9_.-]", "", (action_prefix or "")).rstrip(".")
    filename = "audit-" + (safe_prefix + "-" if safe_prefix else "") + "export.csv"
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
