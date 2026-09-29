"""Non-payment signals from the core banking and loan systems (BR-211).

Five catalogue indicators cannot be answered from the payment rails at any cost, because
the fact they turn on never appears on a payment. A repayment looks identical whether the
money came from the borrower's own deposits or was borrowed from another bank; a transfer
out of a loan account looks the same whether it went to the approved supplier or to the
promoter's brother-in-law. The distinguishing fact lives in the CBS, and until it is sent
the indicator is honestly unmeasurable:

* **BEH-02** borrowal account liquidated with funds from another bank - needs the
  *funding source* of the repayment.
* **BEH-03** sale proceeds not routed through the lender - needs the expected routing.
* **CBS-01** loan funds routed to unrelated third parties - needs disbursement utilisation.
* **CBS-02** heavy cash withdrawal in a loan account - needs cash withdrawals, which do
  not traverse UPI/NEFT/RTGS at all.
* **CBS-03** large transactions with inter-connected group companies - needs the group
  register, which is reference data rather than an event.

One table rather than five. These arrive from the same nightly CBS extract and share a
shape - an account, a time, an amount, a counterparty - and the differences live in
``attributes``. Splitting them would mean five intake paths, five adapters and five sets
of the same reconciliation bug.

A sixth kind, ``cash_transaction``, joined for the same reason (BR-508): a branch cash
deposit or withdrawal also does not traverse UPI/NEFT/RTGS, and also arrives from the CBS
extract rather than a payment rail. It is deliberately **not** the same kind as
``cash_withdrawal`` above - that one is scoped to a borrowal account's cash ratio and its
denominator is the loan's own disbursement; a Cash Transaction Report under PMLA Rule 3
cares about cash movement on *any* account, deposits as much as withdrawals, aggregated
by account across a calendar month regardless of whether a loan is involved at all.
Reusing the narrower kind would either under-report CTR (missing deposits and non-loan
accounts) or corrupt CBS-02's denominator (mixing in cash that never touched a
disbursement). Two kinds, one table, same discipline as the other five.

Two more, ``loan_application`` and ``collateral_valuation`` (BR-214), for the same reason
again: a falsified application, a straw borrower, or a collusive valuation happens *before*
disbursement, so it never appears on a payment rail either - and the five original kinds
above all describe an *existing, disbursed* loan account, which an application is not yet.
Everything else these two need already fits the shared shape: an account (the application
reference, or the applicant's existing account if they have one), an amount (requested, or
valued), a time, and the differences - declared income, applicant identity, valuer,
asset type - in ``attributes``, exactly like every kind before them.

**Why these two cannot be scored the way the other five are.** BEH-02/03 and CBS-01/02/03
are observed *per payment transaction* - the loan context is looked up for whichever
account a payment transaction in the current batch happens to touch, and folded into that
transaction's alert. An application has no payment transaction to attach to; it may
predate the account's first transaction entirely, or the account may never transact again
if the application is refused. So loan-origination findings are evaluated in their own
pass over pending events, not piggybacked on payment scoring - see
``detection/los_features.py`` and ``detection/engine.py``'s ``evaluate_los``.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Index, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import INGESTION

#: What the CBS is telling us. A closed set: an unknown kind is refused at intake rather
#: than stored and silently never read, which is how a feed goes dark unnoticed.
EVENT_KINDS = {
    "loan_disbursement": "Funds released against a sanctioned facility",
    "loan_utilisation": "Where disbursed funds subsequently went",
    "loan_repayment": "A repayment, with the source of the money",
    "cash_withdrawal": "Cash taken from a loan or borrowal account",
    "sale_proceeds": "Proceeds of a financed asset, and whether they reached the lender",
    "account_status": "Opened, closed, classified, or restructured",
    "rm_observation": "A relationship manager's qualitative observation",
    "cash_transaction": "A cash deposit or withdrawal on any account, for CTR "
                        "aggregation (BR-508) - not scoped to loan accounts",
    "loan_application": "A credit application, before sanction - applicant identity "
                        "and declared income (BR-214)",
    "collateral_valuation": "A valuer's assessment of collateral offered against an "
                            "application (BR-214)",
}

#: Valid values for a cash_transaction event's direction. Deposits and withdrawals both
#: count toward the same PMLA Rule 3 account-month aggregate.
CASH_DIRECTIONS = ("deposit", "withdrawal")

#: Where repayment money came from. ``external_bank`` is the one BEH-02 exists to catch:
#: an account liquidated with somebody else's credit is a borrower moving a problem, not
#: solving it.
FUNDING_SOURCES = ("own_deposits", "own_bank_credit", "external_bank", "cash", "unknown")


class CbsEvent(Base):
    __tablename__ = "cbs_events"
    __table_args__ = (
        # The CBS's own identifier is the dedupe key, exactly as the rail's is for a
        # payment. A re-sent nightly extract is normal operations.
        UniqueConstraint("tenant_id", "source_event_id", name="uq_cbs_event_source"),
        Index("ix_cbs_event_account", "tenant_id", "account", "ts"),
        Index("ix_cbs_event_pending", "tenant_id", "state", "received_at"),
        {"schema": INGESTION},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    source_event_id: Mapped[str] = mapped_column(String(80), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    #: The borrowal / loan account the event is about.
    account: Mapped[str] = mapped_column(String(40), index=True)
    #: Where the money went or came from, when the event has a second side.
    counterparty_account: Mapped[str] = mapped_column(String(40), default="",
                                                      server_default="")
    counterparty_name: Mapped[str] = mapped_column(String(200), default="",
                                                   server_default="")
    #: Integer paise, like every other amount in the platform.
    amount_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    #: For a repayment. One of FUNDING_SOURCES.
    funding_source: Mapped[str] = mapped_column(String(24), default="",
                                                server_default="")
    #: For sale proceeds: did the money actually come through the lender?
    routed_through_lender: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: For utilisation: was the payee within the sanctioned purpose?
    within_sanctioned_purpose: Mapped[bool | None] = mapped_column(Boolean,
                                                                   nullable=True)
    #: Everything else the CBS sent, kept verbatim.
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now(), index=True)
    #: pending | projected | rejected. Mirrors the payment queue so one worker drains both.
    state: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    error: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: The batch's own label for where it came from - "cbs" by default. Every other
    #: fact table's "source" is BR-611's live/synthetic/replay provenance marker, and a
    #: caller may set this to those same values ("live", "synthetic"); nothing has
    #: needed to before, because CBS events only ever fed detection indicators, and
    #: scoring demo data is normal. CTR (BR-508) is the first CBS-derived *regulatory
    #: filing*, so it is the first consumer that refuses to build from anything other
    #: than an explicit "live" - the default "cbs" reads as unknown provenance and is
    #: refused, same as an unset source anywhere else BR-611 applies.
    source: Mapped[str] = mapped_column(String(16), default="cbs", server_default="cbs")
