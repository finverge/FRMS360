"""The case transition log.

Separate from the platform audit trail on purpose. ``AuditLog`` answers "what did this
system do"; this answers "how did this case reach the state it is in" - the question an
RBI inspection, an internal auditor, or a borrower's counsel actually asks. It is
append-only: there is no update or delete path anywhere in the service.

Refused attempts are recorded too. A pattern of someone repeatedly trying to cut short a
natural-justice window is exactly the kind of thing that must not be invisible.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Index, Integer, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import CASES


class CaseTransition(Base):
    __tablename__ = "case_transitions"
    __table_args__ = (
        Index("ix_case_transition_case", "tenant_id", "case_id", "created_at"),
        Index("ix_case_transition_pending", "tenant_id", "case_id", "status"),
        {"schema": CASES},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    action: Mapped[str] = mapped_column(String(32))
    from_state: Mapped[str] = mapped_column(String(24))
    # Empty when the attempt was refused or is awaiting a second approver.
    to_state: Mapped[str] = mapped_column(String(24), default="", server_default="")
    actor: Mapped[str] = mapped_column(String(255), index=True)
    actor_role: Mapped[str] = mapped_column(String(32))
    # applied | proposed | approved | refused
    status: Mapped[str] = mapped_column(String(12), index=True)
    # Why it was refused, machine-readable (window_open, self_approval, ...).
    refusal_code: Mapped[str] = mapped_column(String(32), default="", server_default="")
    reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Set when the action ran outside the tenant's own board-approved window. Kept as a
    # fact on the record rather than recomputed later, because the policy may change.
    breached_policy: Mapped[bool] = mapped_column(Boolean, default=False,
                                                  server_default="false")
    breach_days_allowed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # For an approval, the proposal it confirms - so maker and checker are both provable.
    approves_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(), index=True)
