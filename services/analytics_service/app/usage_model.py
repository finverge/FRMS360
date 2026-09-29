"""Per-tenant usage metering for billing (BR-110).

A shared service cannot invoice without counting what each tenant used. The counting is
easy; the property that makes it billable is harder and is the whole design here:

**A closed day never changes.** Once a day has been rolled up and the day is over, that
figure is frozen. Recomputing it later and getting a different number - because a late
file arrived, because a case was purged, because the query changed - means an invoice
cannot be defended when a customer queries it. So the rollup is computed once per day,
recorded with the moment it was computed, and thereafter treated as evidence rather than
as a derived value. Only the current day is recomputed.

That is also why the meters are counted from *immutable* facts. Transactions ingested and
alerts scored only ever accumulate. Seats are different - a user can be removed - so
"seats" is a high-water mark within the day rather than a closing count, because a bank
that adds ten users on the 3rd and removes them on the 4th has used ten seats.
"""
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Index, String, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import ANALYTICS


@dataclass(frozen=True)
class Meter:
    key: str
    label: str
    unit: str
    #: How it is counted, in words. This appears on the usage report, because a customer
    #: querying an invoice line asks exactly this.
    basis: str


METERS: tuple[Meter, ...] = (
    Meter("transactions_ingested", "Transactions ingested", "count",
          "Rows accepted into the inbound queue, counted on the day the platform "
          "received them - not the day they settled, so a backdated file is billed when "
          "it arrived."),
    Meter("alerts_scored", "Alerts raised", "count",
          "Alerts written by detection, counted on the alert's own timestamp."),
    Meter("cases_opened", "Cases opened", "count",
          "Cases created on the day, whatever their eventual outcome."),
    Meter("seats", "Named users", "high_water",
          "The largest number of enabled users seen during the day. A user added and "
          "removed inside one day still counts, because the seat was used."),
    Meter("cbs_events_ingested", "Core-banking events ingested", "count",
          "Non-payment events accepted from the CBS and loan systems."),
)

BY_KEY = {m.key: m for m in METERS}


class UsageDaily(Base):
    """One tenant, one day, one meter. Immutable once the day has closed."""

    __tablename__ = "usage_daily"
    __table_args__ = (
        UniqueConstraint("tenant_id", "day", "meter", name="uq_usage_daily"),
        Index("ix_usage_tenant_day", "tenant_id", "day"),
        {"schema": ANALYTICS},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    meter: Mapped[str] = mapped_column(String(40), index=True)
    quantity: Mapped[int] = mapped_column(BigInteger, default=0)
    #: Set when the day is over and the figure was recomputed for the last time. A row
    #: with this set is never touched again; the reporting endpoint says so.
    finalised: Mapped[bool] = mapped_column(Boolean, default=False,
                                            server_default="false")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now())
