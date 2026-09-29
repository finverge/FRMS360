"""Staff accountability examination (BR-414).

The Master Directions do not stop at reporting the fraud. A bank must also examine
whether its own people contributed to it - by negligence, by ignoring a control, or by
collusion - and record what was done about it. This is the part of fraud governance most
often kept in a spreadsheet, and it is exactly the part an inspection asks to see.

Three design choices are worth stating, because each of them is a rule about behaviour
rather than a detail of storage:

* **The examination never gates the regulatory return.** RBI is explicit that reporting
  must not wait for the accountability exercise, and a system that blocked ``file_fmr``
  until staff had been examined would convert a governance requirement into a reporting
  delay - the opposite of the intent. Nothing here touches the filing path. It gates
  ``close_case`` instead: a fraud may be reported before its accountability is settled,
  but it may not be *closed* with the question unanswered.

* **"No one was at fault" is a finding, not an absence.** An examination concluded with
  zero findings is indistinguishable from one nobody performed. So concluding requires at
  least one recorded finding, and ``no_lapse`` is an available one. Someone has to have
  looked at a named person and said so.

* **Concluding freezes the record.** While the examination is open a mistyped entry can
  be removed - and the removal is audited. Once concluded, the findings are the bank's
  answer to a supervisor, and there is no edit or delete path to them anywhere in this
  service.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import CASES

#: Examination lifecycle. Deliberately short - this is a record of an offline exercise,
#: not a workflow of its own.
STATUSES = ("in_progress", "concluded")

#: What the examination concluded about a named member of staff. The ordering is by
#: severity, and the labels avoid euphemism: an inspection reads these.
FINDINGS = {
    "no_lapse": "No lapse established",
    "procedural_lapse": "Procedural lapse - control not followed",
    "supervisory_lapse": "Supervisory lapse - failure to review or escalate",
    "gross_negligence": "Gross negligence",
    "malafide": "Malafide intent / collusion established",
}

#: What the bank did about it. ``pending`` is honest and common: the finding is settled
#: but the disciplinary process runs on its own timetable.
ACTIONS = {
    "none": "No action required",
    "pending": "Action yet to be decided",
    "counselling": "Counselled",
    "warning": "Warning / displeasure recorded",
    "recovery": "Recovery from the employee ordered",
    "disciplinary": "Disciplinary proceedings initiated",
    "suspension": "Suspended",
    "dismissal": "Dismissed / services terminated",
    "police_referral": "Referred to law enforcement",
}

#: Findings that mean staff were implicated. Used by the register and the board pack -
#: "examined" and "examined, and someone was at fault" are different supervisory facts.
ADVERSE = ("procedural_lapse", "supervisory_lapse", "gross_negligence", "malafide")


class StaffAccountability(Base):
    """One examination per case. Created when someone starts the exercise."""

    __tablename__ = "staff_accountability"
    __table_args__ = (
        # One examination per case: a second one would make "was this examined?"
        # ambiguous, which is the question the whole table exists to answer.
        UniqueConstraint("tenant_id", "case_id", name="uq_accountability_case"),
        Index("ix_accountability_tenant_status", "tenant_id", "status"),
        {"schema": CASES},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(16), default="in_progress", index=True)
    opened_by: Mapped[str] = mapped_column(String(255))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                server_default=func.now())
    # The examining authority - who ran the exercise, which is not always who records it.
    examiner: Mapped[str] = mapped_column(String(255), default="", server_default="")
    conclusion: Mapped[str] = mapped_column(Text, default="", server_default="")
    # A fraud can happen with nobody at fault and a control still missing. Kept separate
    # from the per-person findings so a systemic gap is not recorded as someone's lapse.
    systemic_lapse: Mapped[bool] = mapped_column(Boolean, default=False,
                                                 server_default="false")
    systemic_note: Mapped[str] = mapped_column(Text, default="", server_default="")
    concluded_by: Mapped[str] = mapped_column(String(255), default="", server_default="")
    concluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                          nullable=True)
    # True when the conclusion landed after the tenant's own board-approved window. A
    # fact on the record, not something recomputed later against a policy that may change.
    breached_policy: Mapped[bool] = mapped_column(Boolean, default=False,
                                                  server_default="false")
    breach_days_allowed: Mapped[int] = mapped_column(Integer, default=0,
                                                     server_default="0")


class AccountabilityFinding(Base):
    """One named member of staff, and what was concluded about them."""

    __tablename__ = "accountability_findings"
    __table_args__ = (
        Index("ix_accountability_finding_exam", "tenant_id", "examination_id"),
        {"schema": CASES},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    examination_id: Mapped[str] = mapped_column(String(36), index=True)
    # The bank's own employee reference. Required, because "Suresh from the Andheri
    # branch" is not an answer a disciplinary process or a supervisor can act on.
    staff_ref: Mapped[str] = mapped_column(String(64))
    staff_name: Mapped[str] = mapped_column(String(255))
    # Role and branch *at the time of the fraud*, which is often not the role they hold
    # when the examination concludes.
    role_at_time: Mapped[str] = mapped_column(String(120), default="", server_default="")
    branch: Mapped[str] = mapped_column(String(120), default="", server_default="")
    finding: Mapped[str] = mapped_column(String(24), index=True)
    action_taken: Mapped[str] = mapped_column(String(24), default="pending",
                                              server_default="pending")
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    recorded_by: Mapped[str] = mapped_column(String(255))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now())
