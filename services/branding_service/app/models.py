"""Data tier — branding owned by the branding-service.

Note: tenant_id is a logical reference to the tenant-service's tenants.id. There is
deliberately NO database foreign key across the service boundary.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import BRANDING


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Branding(Base):
    __tablename__ = "branding"
    __table_args__ = {"schema": BRANDING}

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    logo_url: Mapped[str] = mapped_column(String(1024), default="")
    primary_color: Mapped[str] = mapped_column(String(9), default="#0F6E7A")
    accent_color: Mapped[str] = mapped_column(String(9), default="#19A6B8")
    neutral_color: Mapped[str] = mapped_column(String(9), default="#5A6472")
    default_theme: Mapped[str] = mapped_column(String(8), default="light")  # light|dark
    custom_domain: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_now
    )
