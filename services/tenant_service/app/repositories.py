"""Data-access tier — all SQL lives here, keeping the service tier persistence-agnostic."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import PlatformUser, Tenant, TenantUser


class TenantRepository:
    def __init__(self, db: Session):
        self.db = db

    # --- tenants ---
    def list(self) -> list[Tenant]:
        return list(self.db.scalars(select(Tenant).order_by(Tenant.created_at.desc())))

    def get(self, tenant_id: str) -> Tenant | None:
        return self.db.get(Tenant, tenant_id)

    def get_by_slug(self, slug: str) -> Tenant | None:
        return self.db.scalar(select(Tenant).where(Tenant.slug == slug))

    def find_sandbox_by_admin_email(self, email: str) -> Tenant | None:
        """A live (not offboarded) sandbox this email already administers - the
        abuse guard on repeated self-service signups. TenantUser.email is unique
        only per tenant (a person can be tenant_admin of more than one bank), so
        this joins through the user row rather than assuming a global lookup."""
        return self.db.scalar(
            select(Tenant)
            .join(TenantUser, TenantUser.tenant_id == Tenant.id)
            .where(Tenant.is_sandbox.is_(True), Tenant.status != "offboarded",
                  TenantUser.email == email))

    def add(self, tenant: Tenant) -> Tenant:
        self.db.add(tenant)
        self.db.flush()
        return tenant

    def set_status(self, tenant: Tenant, status: str) -> Tenant:
        tenant.status = status
        self.db.flush()
        return tenant

    # --- users ---
    def add_tenant_user(self, user: TenantUser) -> TenantUser:
        self.db.add(user)
        self.db.flush()
        return user

    def find_tenant_user(self, email: str) -> TenantUser | None:
        return self.db.scalar(select(TenantUser).where(TenantUser.email == email))

    def get_tenant_user_by_id(self, tenant_id: str, user_id: str) -> TenantUser | None:
        user = self.db.get(TenantUser, user_id)
        return user if user and user.tenant_id == tenant_id else None

    def delete_tenant_user(self, user: TenantUser) -> None:
        self.db.delete(user)
        self.db.flush()

    def find_platform_user(self, email: str) -> PlatformUser | None:
        return self.db.scalar(select(PlatformUser).where(PlatformUser.email == email))
