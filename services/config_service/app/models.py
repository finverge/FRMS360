"""Data tier — versioned Tazama configs per tenant.

Each (tenant_id, kind, name, version) is unique. Activating a version archives the
previously active one of the same (tenant, kind, name) so exactly one is live.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import CONFIG


def _uuid() -> str:
    return str(uuid.uuid4())


class TenantConfig(Base):
    __tablename__ = "tenant_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", "name", "version", name="uq_config_version"),
        {"schema": CONFIG},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # rule | typology | network_map
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    # draft|pending_activation|active|archived. See maker_checker.py (BR-715): activation
    # passes through pending_activation so a second, different person confirms it.
    status: Mapped[str] = mapped_column(String(20), default="draft")
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # --- BR-715: maker-checker on activation ---
    proposed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    proposed_by_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    proposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_by_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
