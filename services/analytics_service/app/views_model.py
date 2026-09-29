"""Saved dashboard views.

Views lived in browser localStorage, which meant they were lost on a cache clear, invisible
on another machine, and impossible to hand to a colleague. A view is a working artefact -
"the queue I check every morning", "the evidence set for case C-1188" - so it belongs on
the server, scoped to the tenant and owned by a person.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from cp_common import Base
from cp_common.schemas_db import ANALYTICS


class SavedView(Base):
    __tablename__ = "saved_views"
    # A person may not have two views of the same name; two people may.
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner", "name", name="uq_saved_view_name"),
        Index("ix_saved_view_tenant_owner", "tenant_id", "owner"),
        {"schema": ANALYTICS},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    owner: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(120))
    dashboard: Mapped[str] = mapped_column(String(32), default="")
    # The filter set as a query string - the same representation the URL carries, so a
    # saved view and a shared link are interchangeable.
    query: Mapped[str] = mapped_column(Text, default="")
    # Shared views are visible inside the tenant but still editable only by their owner
    # (or a tenant admin). Sharing never crosses a tenant boundary - see
    # test_a_shared_view_never_crosses_into_another_tenant.
    shared: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Audience within the tenant. Empty means everyone; otherwise a comma-delimited role
    # list stored with leading and trailing commas (",analyst,board,") so an exact role
    # can be matched with LIKE '%,analyst,%' without "analyst" also matching some future
    # "analyst_l2". At HDFC scale "shared" cannot mean "every L1 sees the AML principal
    # officer's queue", so the audience is narrowable.
    shared_roles: Mapped[str] = mapped_column(String(512), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
