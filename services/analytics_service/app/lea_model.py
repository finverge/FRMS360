"""Law-enforcement referral and recovery (BR-415).

Two things a bank has to be able to answer about a fraud after it has been declared, and
which nothing in the platform could answer until now:

* **Was it reported to the police, and what happened?** Above the tenant's own referral
  floor the Directions expect a complaint to law enforcement. "Referred" and "an FIR was
  registered" are different facts, and the gap between them is often months.

* **What was actually recovered, and how do we know?** ``fact_case.recovered_paise`` was a
  number nobody had to substantiate. Every dashboard, the board pack and the net-loss
  figure all read it. So recoveries are now *entries* - an amount, a mode and a reference -
  and the case column becomes their sum.

**On the cases that already carry a figure.** Demo and historical data have a recovered
amount with no entries behind it. That is not corrected silently and it is not wiped: the
reconciliation view reports it as **unevidenced**, which is the honest description and
exactly what an inspection would want flagged. A recovery nobody can produce a reference
for is a finding, not a rounding difference.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Date, DateTime, Index, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import CASES

#: Who the complaint went to. India-specific on purpose - "law enforcement" is not a
#: useful field value when the escalation path differs by agency.
AGENCIES = {
    "local_police": "Local police / cyber cell",
    "eow": "Economic Offences Wing",
    "cbi": "Central Bureau of Investigation",
    "sfio": "Serious Fraud Investigation Office",
    "ed": "Enforcement Directorate",
    "other": "Other agency",
}

#: Where the referral has got to. Ordered by progression.
REFERRAL_STATES = {
    "referred": "Complaint lodged",
    "fir_registered": "FIR registered",
    "under_investigation": "Under investigation",
    "chargesheet_filed": "Chargesheet filed",
    "closed": "Case closed by the agency",
    "declined": "Agency declined to register",
}

#: States meaning the agency has taken it up. Used to report referrals that were lodged
#: and then went nowhere, which is the pattern worth surfacing to a committee.
PROGRESSED = ("fir_registered", "under_investigation", "chargesheet_filed", "closed")

#: How money came back. ``write_off`` is deliberately *not* here - writing a loss off is
#: not a recovery, and letting it be recorded as one would flatter the net-loss figure.
RECOVERY_MODES = {
    "customer_repayment": "Recovered from the customer",
    "insurance": "Insurance claim settled",
    "account_freeze": "Frozen / lien-marked funds released back",
    "beneficiary_reversal": "Reversed by the beneficiary bank",
    "court_order": "Recovered under a court order",
    "asset_disposal": "Realised from disposal of security",
    "staff_recovery": "Recovered from staff",
    "other": "Other",
}


class LeaReferral(Base):
    """One referral per case. A second complaint to a different agency is an amendment."""

    __tablename__ = "lea_referrals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id", name="uq_lea_referral_case"),
        Index("ix_lea_referral_tenant_state", "tenant_id", "state"),
        {"schema": CASES},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    agency: Mapped[str] = mapped_column(String(24))
    agency_office: Mapped[str] = mapped_column(String(200), default="",
                                               server_default="")
    state: Mapped[str] = mapped_column(String(24), default="referred", index=True)
    #: The bank's own complaint reference, then the agency's FIR number once registered.
    complaint_ref: Mapped[str] = mapped_column(String(120), default="", server_default="")
    fir_number: Mapped[str] = mapped_column(String(120), default="", server_default="")
    referred_on: Mapped[date] = mapped_column(Date)
    fir_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_update: Mapped[str] = mapped_column(Text, default="", server_default="")
    referred_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now(),
                                                 onupdate=func.now())


class RecoveryEntry(Base):
    """One substantiated recovery. The case's recovered total is the sum of these."""

    __tablename__ = "recovery_entries"
    __table_args__ = (
        Index("ix_recovery_tenant_case", "tenant_id", "case_id"),
        {"schema": CASES},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    #: Integer paise, like every other amount in the platform. Never a float.
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    mode: Mapped[str] = mapped_column(String(32), index=True)
    #: What evidences it - a credit reference, claim number or court order number.
    reference: Mapped[str] = mapped_column(String(200), default="", server_default="")
    recovered_on: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    recorded_by: Mapped[str] = mapped_column(String(255))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now())
