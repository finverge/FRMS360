"""Audit logging — a cross-cutting, append-only trail of who did what.

Design points:
  * ``record_audit`` writes on its OWN short-lived session so an audit write neither
    joins nor breaks the caller's transaction, and a failed business transaction never
    silently drops the audit record.
  * It is best-effort and NEVER raises into the caller — an auditing outage must not take
    down the operation being audited. Failures are logged locally instead.
  * The table is shared (like the rest of the control-plane schema) and scoped by tenant_id.
"""
import logging
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, SessionLocal
from .schemas_db import PLATFORM

log = logging.getLogger("audit")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = {"schema": PLATFORM}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    service: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(255), default="")
    actor_role: Mapped[str] = mapped_column(String(32), default="")
    tenant_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(32), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="success")  # success | failure
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


def record_audit(
    *,
    service: str,
    action: str,
    actor: str | None = "",
    actor_role: str | None = "",
    tenant_id: str | None = "",
    target_type: str = "",
    target_id: str = "",
    status: str = "success",
    detail: dict | None = None,
) -> None:
    """Best-effort audit write. Swallows and logs any failure — never raises."""
    db = SessionLocal()
    try:
        db.add(
            AuditLog(
                service=service,
                action=action,
                actor=actor or "",
                actor_role=actor_role or "",
                tenant_id=tenant_id or "",
                target_type=target_type,
                target_id=target_id,
                status=status,
                detail=detail or {},
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001 - auditing must not break the audited operation
        db.rollback()
        log.exception("failed to persist audit log (service=%s action=%s)", service, action)
    finally:
        db.close()
