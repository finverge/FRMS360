"""Data-access tier for tenant configs, including version activation semantics."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import TenantConfig


class ConfigRepository:
    def __init__(self, db: Session):
        self.db = db

    def list(self, tenant_id: str, kind: str | None = None) -> list[TenantConfig]:
        stmt = select(TenantConfig).where(TenantConfig.tenant_id == tenant_id)
        if kind:
            stmt = stmt.where(TenantConfig.kind == kind)
        return list(self.db.scalars(stmt.order_by(TenantConfig.created_at.desc())))

    def get(self, tenant_id: str, config_id: str) -> TenantConfig | None:
        row = self.db.get(TenantConfig, config_id)
        return row if row and row.tenant_id == tenant_id else None

    def exists(self, tenant_id: str, kind: str, name: str, version: str) -> bool:
        stmt = select(TenantConfig.id).where(
            TenantConfig.tenant_id == tenant_id,
            TenantConfig.kind == kind,
            TenantConfig.name == name,
            TenantConfig.version == version,
        )
        return self.db.scalar(stmt) is not None

    def create(
        self, tenant_id: str, kind: str, name: str, version: str, body: dict, status: str = "draft"
    ) -> TenantConfig:
        row = TenantConfig(
            tenant_id=tenant_id, kind=kind, name=name, version=version, body=body, status=status
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def persist(self, row: TenantConfig) -> TenantConfig:
        """Commit whatever the caller already set on the row (BR-715 propose/reject)."""
        self.db.commit()
        self.db.refresh(row)
        return row

    def activate(self, tenant_id: str, row: TenantConfig) -> TenantConfig:
        """Final step: archive any currently-active sibling and mark this version
        active. Called only after maker_checker.confirm() has recorded the second
        approver on ``row`` - this method does not check who did the confirming."""
        siblings = self.db.scalars(
            select(TenantConfig).where(
                TenantConfig.tenant_id == tenant_id,
                TenantConfig.kind == row.kind,
                TenantConfig.name == row.name,
                TenantConfig.status == "active",
            )
        )
        for s in siblings:
            s.status = "archived"
        row.status = "active"
        self.db.commit()
        self.db.refresh(row)
        return row
