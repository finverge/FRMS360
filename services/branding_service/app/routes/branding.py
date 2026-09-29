"""Presentation tier — branding CRUD, theme delivery, and internal provisioning."""
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from cp_common import (
    AppError,
    Principal,
    get_current_principal,
    get_session,
    record_audit,
    require_internal_key,
    resolve_tenant_scope,
)

from ..repositories import BrandingRepository
from ..schemas import BrandingOut, BrandingUpsert
from ..services import theme_css, theme_tokens

router = APIRouter(tags=["branding"])


@router.get("/branding/{tenant_id}", response_model=BrandingOut)
def get_branding(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> BrandingOut:
    resolve_tenant_scope(principal, tenant_id)
    row = BrandingRepository(db).get(tenant_id)
    if not row:
        raise AppError("Branding not found", 404, "not_found")
    return BrandingOut.model_validate(row)


@router.put("/branding/{tenant_id}", response_model=BrandingOut)
def put_branding(
    tenant_id: str,
    payload: BrandingUpsert,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> BrandingOut:
    resolve_tenant_scope(principal, tenant_id)  # tenant_admin may only brand their own
    row = BrandingRepository(db).upsert(tenant_id, payload)
    record_audit(
        service="branding-service", action="branding.update", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="branding",
        target_id=tenant_id, status="success",
        detail={"primary_color": payload.primary_color, "accent_color": payload.accent_color},
    )
    return BrandingOut.model_validate(row)


# --- public theme delivery (no auth: themes are non-sensitive and cached by clients) ---
@router.get("/branding/{tenant_id}/theme.json")
def get_theme_json(tenant_id: str, db: Session = Depends(get_session)) -> dict:
    row = BrandingRepository(db).get(tenant_id)
    if not row:
        raise AppError("Branding not found", 404, "not_found")
    return theme_tokens(row)


@router.get("/branding/{tenant_id}/theme.css", response_class=PlainTextResponse)
def get_theme_css(tenant_id: str, db: Session = Depends(get_session)) -> str:
    row = BrandingRepository(db).get(tenant_id)
    if not row:
        raise AppError("Branding not found", 404, "not_found")
    return theme_css(row)


# --- internal: called by tenant-service (onboarding + export) ---
@router.get("/internal/branding/{tenant_id}")
def internal_get_branding(
    tenant_id: str,
    db: Session = Depends(get_session),
    _: None = Depends(require_internal_key),
) -> dict | None:
    row = BrandingRepository(db).get(tenant_id)
    return BrandingOut.model_validate(row).model_dump(mode="json") if row else None


@router.put("/internal/branding/{tenant_id}", response_model=BrandingOut)
def provision_branding(
    tenant_id: str,
    payload: BrandingUpsert,
    db: Session = Depends(get_session),
    _: None = Depends(require_internal_key),
) -> BrandingOut:
    row = BrandingRepository(db).upsert(tenant_id, payload)
    return BrandingOut.model_validate(row)
