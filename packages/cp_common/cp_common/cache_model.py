"""The shared generation counter backing DatabaseBackend.

Platform infrastructure rather than any one service's data: every service may read it and
the service that owns a piece of configuration bumps it. When the schema-per-service split
happens this belongs in a shared `platform` schema, alongside anything else that is
legitimately common (it is the same role Redis would play).
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .schemas_db import PLATFORM


class ConfigGeneration(Base):
    __tablename__ = "config_generation"
    __table_args__ = {"schema": PLATFORM}

    # e.g. "rules:<tenant_id>" or "policy:<tenant_id>"
    namespace: Mapped[str] = mapped_column(String(128), primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
