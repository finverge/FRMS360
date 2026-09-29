"""Saved-view endpoints."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from cp_common import (
    AppError, Principal, get_current_principal, get_session,
    record_audit, resolve_tenant_scope,
)
from cp_common.dynamic_roles import can_admin_tenant
from cp_common.rbac import ASSIGNABLE_TENANT_ROLES, get_role

from ..views_model import SavedView

router = APIRouter(prefix="/analytics", tags=["views"])


class ViewIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    dashboard: str = Field(default="", max_length=32)
    query: str = Field(default="", max_length=4000)
    shared: bool = False
    # Empty with shared=True means the whole tenant; otherwise only these roles.
    shared_roles: list[str] = Field(default_factory=list)


class ViewOut(BaseModel):
    id: str
    name: str
    dashboard: str
    query: str
    shared: bool
    shared_roles: list[str]
    audience: str
    owner: str
    mine: bool
    updated_at: datetime

    model_config = {"from_attributes": True}


def _pack_roles(roles: list[str]) -> str:
    """Normalise to ',role,role,' after rejecting anything not a real tenant role.

    Validating here matters twice over: an unknown role silently stored would make the
    view invisible to everyone, and the stored value is interpolated into a LIKE pattern.
    """
    cleaned = []
    for r in roles:
        r = (r or "").strip()
        if not r:
            continue
        if r not in ASSIGNABLE_TENANT_ROLES:
            raise AppError(f"Unknown role: {r}", 400, "unknown_role")
        if r not in cleaned:
            cleaned.append(r)
    return "," + ",".join(sorted(cleaned)) + "," if cleaned else ""


def _unpack_roles(packed: str) -> list[str]:
    return [r for r in (packed or "").split(",") if r]


def _visible(db: Session, tenant_id: str, subject: str, role: str):
    """A user's own views, plus tenant-shared views whose audience includes their role.

    The tenant_id predicate is the only thing standing between two banks for a caller
    admitted to both (platform staff), so it is applied unconditionally.
    """
    audience_ok = or_(
        SavedView.shared_roles == "",                       # everyone in the tenant
        SavedView.shared_roles.like(f"%,{role},%"),          # this role specifically
    )
    return db.scalars(
        select(SavedView)
        .where(SavedView.tenant_id == tenant_id)
        .where(or_(SavedView.owner == subject,
                   and_(SavedView.shared.is_(True), audience_ok)))
        .order_by(SavedView.shared, SavedView.name)
    )


def _audience(v: SavedView) -> str:
    """How the sharing scope should read to a human."""
    if not v.shared:
        return "Private"
    roles = _unpack_roles(v.shared_roles)
    if not roles:
        return "Everyone in this tenant"
    return ", ".join(get_role(r).label for r in roles)


def _out(v: SavedView, subject: str) -> ViewOut:
    return ViewOut(id=v.id, name=v.name, dashboard=v.dashboard, query=v.query,
                   shared=v.shared, shared_roles=_unpack_roles(v.shared_roles),
                   audience=_audience(v), owner=v.owner, mine=v.owner == subject,
                   updated_at=v.updated_at)


@router.get("/{tenant_id}/views", response_model=list[ViewOut])
def list_views(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> list[ViewOut]:
    resolve_tenant_scope(principal, tenant_id)
    return [_out(v, principal.subject)
            for v in _visible(db, tenant_id, principal.subject, principal.role)]


@router.get("/{tenant_id}/view-audiences")
def audiences(
    tenant_id: str,
    principal: Principal = Depends(get_current_principal),
) -> list[dict]:
    """Roles a view may be shared with.

    Served from the RBAC table rather than duplicated in the console, so a role added to
    ASSIGNABLE_TENANT_ROLES becomes shareable without a second edit that can be missed.
    """
    resolve_tenant_scope(principal, tenant_id)
    return [{"name": r, "label": get_role(r).label} for r in ASSIGNABLE_TENANT_ROLES]


@router.post("/{tenant_id}/views", response_model=ViewOut, status_code=201)
def save_view(
    tenant_id: str,
    payload: ViewIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> ViewOut:
    """Create, or overwrite the caller's view of the same name."""
    resolve_tenant_scope(principal, tenant_id)
    packed = _pack_roles(payload.shared_roles) if payload.shared else ""
    existing = db.scalar(
        select(SavedView).where(SavedView.tenant_id == tenant_id,
                                SavedView.owner == principal.subject,
                                SavedView.name == payload.name))
    if existing:
        existing.dashboard = payload.dashboard
        existing.query = payload.query
        existing.shared = payload.shared
        existing.shared_roles = packed
        existing.updated_at = datetime.now(timezone.utc)
        view = existing
    else:
        view = SavedView(tenant_id=tenant_id, owner=principal.subject, name=payload.name,
                         dashboard=payload.dashboard, query=payload.query,
                         shared=payload.shared, shared_roles=packed)
        db.add(view)
    db.commit()
    db.refresh(view)
    record_audit(
        service="analytics-service", action="view.save", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="saved_view",
        target_id=view.id, status="success",
        detail={"name": view.name, "shared": view.shared, "dashboard": view.dashboard,
                "audience": _audience(view)})
    return _out(view, principal.subject)


@router.delete("/{tenant_id}/views/{view_id}", status_code=204)
def delete_view(
    tenant_id: str,
    view_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> None:
    resolve_tenant_scope(principal, tenant_id)
    view = db.get(SavedView, view_id)
    if not view or view.tenant_id != tenant_id:
        raise AppError("View not found", 404, "not_found")
    # A shared view is still its owner's; only they or a tenant admin may remove it.
    if view.owner != principal.subject and not can_admin_tenant(tenant_id, principal.role):
        raise AppError("You may only delete your own views", 403, "not_your_view")
    name = view.name
    db.delete(view)
    db.commit()
    record_audit(
        service="analytics-service", action="view.delete", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="saved_view",
        target_id=view_id, status="success", detail={"name": name})
