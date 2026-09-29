"""Business tier — tenant onboarding as a small orchestration (saga-lite).

Creating a tenant is more than one INSERT: it provisions the tenant record and its
first admin, then calls the branding and config services to lay down defaults. If a
downstream provisioning step fails we mark the tenant ``degraded`` rather than losing
it, so an operator can retry.
"""
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from cp_common import AppError, AuditLog, hash_password, settings, tenant_status

from .models import Tenant, TenantUser
from .repositories import TenantRepository
from .roles import seed_default_roles
from .schemas import TenantCreate

log = logging.getLogger("tenant-service")

# Allowed lifecycle transitions: action -> (states it may run from, resulting state)
LIFECYCLE = {
    "suspend": ({"active", "degraded"}, "suspended"),
    "resume": ({"suspended"}, "active"),
    "offboard": ({"active", "degraded", "suspended"}, "offboarded"),
}

#: Sandboxes a self-service tenant may already exist in (created, possibly still
#: settling its downstream provisioning) when a platform_admin promotes it. Deliberately
#: excludes "degraded": a sandbox whose branding/config provisioning failed is promoted
#: the same way any other degraded tenant is fixed - by retrying provisioning, not by
#: promotion papering over an incomplete setup.
PROMOTABLE_FROM = {"active"}


class TenantService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = TenantRepository(db)

    def onboard(self, payload: TenantCreate, *, sandbox: bool = False) -> Tenant:
        if self.repo.get_by_slug(payload.slug):
            raise AppError(f"slug '{payload.slug}' already exists", 409, "slug_conflict")
        if sandbox and self.repo.find_sandbox_by_admin_email(payload.admin_email):
            # A human clicking "request a sandbox" twice is normal; a script doing it
            # a thousand times is not. One live sandbox per email is enough to
            # integrate against, and it is a promoted (no longer sandbox) tenant's
            # admin that legitimately wants a *second*, fresh one for a new engagement.
            raise AppError(
                "An active sandbox tenant already exists for this email. Sign in to "
                "the existing one, or contact your Fraud360 contact for another.",
                409, "sandbox_exists")

        tenant = self.repo.add(
            Tenant(
                slug=payload.slug,
                legal_name=payload.legal_name,
                display_name=payload.display_name,
                region=payload.region,
                # A sandbox is never billed on any plan a self-service caller names -
                # what they send here is ignored, not trusted.
                plan="sandbox" if sandbox else payload.plan,
                entity_type=payload.entity_type,
                ucb_tier=payload.ucb_tier,
                status="provisioning",
                is_sandbox=sandbox,
            )
        )
        self.repo.add_tenant_user(
            TenantUser(
                tenant_id=tenant.id,
                email=payload.admin_email,
                password_hash=hash_password(payload.admin_password),
                role="tenant_admin",
            )
        )
        # BR-113 (phase 2a): materialise this tenant's own copy of the role catalogue.
        # Local to this database - unlike branding/config below, it cannot fail on a
        # downstream service being unreachable, so it belongs in the same transaction
        # as the tenant and its first admin rather than the degrade-on-failure block.
        seed_default_roles(self.db, tenant.id)
        # Commit the tenant + admin before calling out, so the record survives a
        # downstream hiccup.
        self.db.commit()
        self.db.refresh(tenant)

        try:
            self._provision_branding(tenant, payload)
            self._provision_default_configs(tenant)
            self.repo.set_status(tenant, "active")
        except Exception as exc:  # noqa: BLE001 - degrade, don't crash onboarding
            log.exception("provisioning failed for tenant %s: %s", tenant.slug, exc)
            self.repo.set_status(tenant, "degraded")
        self.db.commit()
        self.db.refresh(tenant)
        return tenant

    # --- inter-service provisioning calls ---
    def _provision_branding(self, tenant: Tenant, payload: TenantCreate) -> None:
        body = {
            "display_name": tenant.display_name,
            "primary_color": payload.primary_color or "#0F6E7A",
            "accent_color": payload.accent_color or "#19A6B8",
        }
        r = httpx.put(
            f"{settings.branding_service_url}/internal/branding/{tenant.id}",
            json=body,
            headers={"x-internal-key": settings.internal_api_key},
            timeout=10.0,
        )
        r.raise_for_status()

    def _provision_default_configs(self, tenant: Tenant) -> None:
        r = httpx.post(
            f"{settings.config_service_url}/internal/configs/seed",
            json={"tenant_id": tenant.id, "entity_type": tenant.entity_type,
                  "ucb_tier": tenant.ucb_tier},
            headers={"x-internal-key": settings.internal_api_key},
            timeout=10.0,
        )
        r.raise_for_status()

    # --- lifecycle ---
    def transition(self, tenant_id: str, action: str) -> Tenant:
        tenant = self.repo.get(tenant_id)
        if not tenant:
            raise AppError("Tenant not found", 404, "not_found")
        allowed_from, new_status = LIFECYCLE[action]
        if tenant.status not in allowed_from:
            raise AppError(
                f"Cannot {action} a tenant in '{tenant.status}' state", 409, "invalid_transition"
            )
        self.repo.set_status(tenant, new_status)
        self.db.commit()
        self.db.refresh(tenant)
        # Other services cache this status for a few seconds. Dropping our own copy is
        # not enough on its own - theirs still ages out - but it stops this process
        # serving an answer it has just been told is wrong.
        tenant_status.forget(tenant_id)
        return tenant

    def promote(self, tenant_id: str) -> Tenant:
        """BR-109: turn a self-service sandbox into a real, billable tenant.

        Deliberately its own action, not a side effect of any status transition above -
        promotion is a commercial/compliance decision (OD-01, the deployment model) a
        human makes about *this* bank, not something a sandbox graduates into by
        surviving long enough. is_sandbox flips exactly once, in this direction; there
        is no self-service or automatic path back.
        """
        tenant = self.repo.get(tenant_id)
        if not tenant:
            raise AppError("Tenant not found", 404, "not_found")
        if not tenant.is_sandbox:
            raise AppError("This tenant is not a sandbox", 409, "not_a_sandbox")
        if tenant.status not in PROMOTABLE_FROM:
            raise AppError(
                f"Cannot promote a tenant in '{tenant.status}' state - it must be "
                f"'active' first", 409, "invalid_transition")
        tenant.is_sandbox = False
        tenant.plan = "standard"
        self.db.commit()
        self.db.refresh(tenant)
        tenant_status.forget(tenant_id)
        return tenant

    # --- data export (RBI outsourcing exit-clause: return the tenant's data) ---
    def export_bundle(self, tenant_id: str) -> dict:
        """Everything the bank is entitled to take with it when the contract ends.

        This used to return the tenant's configuration and nothing else — branding,
        thresholds, the user list, the audit trail. All of that is real, but none of it is
        the part a bank cannot rebuild. The **fraud record** is: the cases, the alerts
        underneath them, the evidence, the accountability examinations, the LEA referrals,
        the recoveries and the filed FMR/STR returns. Those carry statutory retention
        obligations that outlive the contract, and they exist nowhere else.

        The fraud record is fetched from analytics-service, which owns those schemas —
        this service has no grant on them, deliberately.

        **A partial bundle must never look complete.** The previous version swallowed a
        failed branding or config fetch and returned the bundle as though nothing were
        missing. On an exit export that is the worst possible failure: the bank discovers
        the gap after the tenant is gone. Every fetch failure is now recorded and the
        bundle carries an explicit ``complete`` flag.
        """
        tenant = self.repo.get(tenant_id)
        if not tenant:
            raise AppError("Tenant not found", 404, "not_found")

        headers = {"x-internal-key": settings.internal_api_key}
        problems: list[str] = []

        def fetch(label: str, url: str, fallback):
            try:
                r = httpx.get(url, headers=headers, timeout=60.0)
                r.raise_for_status()
                return r.json()
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{label}: {str(exc)[:200]}")
                return fallback

        branding = fetch("branding",
                         f"{settings.branding_service_url}/internal/branding/{tenant_id}",
                         None)
        configs = fetch("configs",
                        f"{settings.config_service_url}/internal/configs/{tenant_id}",
                        [])
        fraud = fetch("fraud record",
                      f"{settings.analytics_service_url}/internal/export/{tenant_id}",
                      None)
        if isinstance(fraud, dict) and not fraud.get("complete", True):
            problems.extend(f"fraud record / {p}" for p in fraud.get("problems", []))

        audit = self.db.scalars(
            select(AuditLog).where(AuditLog.tenant_id == tenant_id).order_by(AuditLog.ts.desc())
        )
        return {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "tenant": {
                "id": tenant.id, "slug": tenant.slug, "legal_name": tenant.legal_name,
                "display_name": tenant.display_name, "region": tenant.region,
                "plan": tenant.plan, "status": tenant.status,
            },
            # Admins are exported WITHOUT password hashes.
            "users": [
                {"email": u.email, "role": u.role, "created_at": u.created_at.isoformat()}
                for u in tenant.users
            ],
            "branding": branding,
            "configs": configs,
            "fraud_record": fraud,
            "audit": [
                {"ts": a.ts.isoformat(), "actor": a.actor, "action": a.action,
                 "status": a.status, "detail": a.detail}
                for a in audit
            ],
            "complete": not problems,
            "problems": problems,
        }
