"""Report subscription endpoints."""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from cp_common import (
    AppError, Principal, get_current_principal, get_session,
    record_audit, resolve_tenant_scope,
)
from cp_common.dynamic_roles import can_admin_tenant

from ..subscriptions_model import CADENCES, DRIVERS, FORMATS, ReportSubscription

router = APIRouter(prefix="/analytics", tags=["subscriptions"])


class SubscriptionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    entity: str = Field(default="case", pattern=r"^(alert|case|transaction)$")
    query: str = Field(default="", max_length=4000)
    cadence: str = Field(default="weekly", pattern=r"^(daily|weekly|monthly)$")
    driver: str = Field(default="spool", pattern=r"^(spool|email)$")
    recipients: str = Field(default="", max_length=1000)
    fmt: str = Field(default="csv", pattern=r"^csv$")
    active: bool = True


class SubscriptionOut(BaseModel):
    id: str
    name: str
    entity: str
    query: str
    cadence: str
    driver: str
    recipients: str
    fmt: str
    active: bool
    owner: str
    mine: bool
    run_count: int
    last_run_at: datetime | None
    last_status: str
    due_now: bool


def _out(sub: ReportSubscription, subject: str) -> SubscriptionOut:
    return SubscriptionOut(
        id=sub.id, name=sub.name, entity=sub.entity, query=sub.query,
        cadence=sub.cadence, driver=sub.driver, recipients=sub.recipients,
        fmt=sub.fmt, active=sub.active, owner=sub.owner, mine=sub.owner == subject,
        run_count=sub.run_count, last_run_at=sub.last_run_at,
        last_status=sub.last_status, due_now=sub.is_due())


@router.get("/{tenant_id}/subscriptions", response_model=list[SubscriptionOut])
def list_subscriptions(
    tenant_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> list[SubscriptionOut]:
    """A user's own subscriptions; tenant admins see the whole tenant's."""
    resolve_tenant_scope(principal, tenant_id)
    stmt = select(ReportSubscription).where(ReportSubscription.tenant_id == tenant_id)
    if not can_admin_tenant(tenant_id, principal.role):
        stmt = stmt.where(ReportSubscription.owner == principal.subject)
    return [_out(s, principal.subject)
            for s in db.scalars(stmt.order_by(ReportSubscription.name))]


@router.post("/{tenant_id}/subscriptions", response_model=SubscriptionOut, status_code=201)
def create_subscription(
    tenant_id: str,
    payload: SubscriptionIn,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> SubscriptionOut:
    resolve_tenant_scope(principal, tenant_id)
    existing = db.scalar(select(ReportSubscription).where(
        ReportSubscription.tenant_id == tenant_id,
        ReportSubscription.owner == principal.subject,
        ReportSubscription.name == payload.name))
    if existing:
        for field, value in payload.model_dump().items():
            setattr(existing, field, value)
        sub = existing
    else:
        sub = ReportSubscription(tenant_id=tenant_id, owner=principal.subject,
                                 **payload.model_dump())
        db.add(sub)
    db.commit()
    db.refresh(sub)
    record_audit(
        service="analytics-service", action="subscription.save", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="subscription",
        target_id=sub.id, status="success",
        detail={"name": sub.name, "cadence": sub.cadence, "driver": sub.driver,
                "recipients": sub.recipients})
    return _out(sub, principal.subject)


@router.delete("/{tenant_id}/subscriptions/{subscription_id}", status_code=204)
def delete_subscription(
    tenant_id: str,
    subscription_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> None:
    resolve_tenant_scope(principal, tenant_id)
    sub = db.get(ReportSubscription, subscription_id)
    if not sub or sub.tenant_id != tenant_id:
        raise AppError("Subscription not found", 404, "not_found")
    if sub.owner != principal.subject and not can_admin_tenant(tenant_id, principal.role):
        raise AppError("You may only delete your own subscriptions", 403, "not_yours")
    name = sub.name
    db.delete(sub)
    db.commit()
    record_audit(
        service="analytics-service", action="subscription.delete",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="subscription", target_id=subscription_id, status="success",
        detail={"name": name})


@router.get("/{tenant_id}/subscriptions/options")
def subscription_options(
    tenant_id: str,
    principal: Principal = Depends(get_current_principal),
) -> dict:
    resolve_tenant_scope(principal, tenant_id)
    return {"cadences": list(CADENCES), "formats": FORMATS, "drivers": DRIVERS,
            "entities": ["alert", "case", "transaction"]}
