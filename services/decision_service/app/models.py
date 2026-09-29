"""Data tier for the inline decision lane (Lane A).

Two tables, and both exist to keep a promise the near-real-time lane cannot make: an
answer inside a payment window.

``account_counters`` holds the *precomputed* observations a rule needs. Nothing here is
aggregated at decision time — the whole point is that the hot path performs lookups, not
queries. If a value is not in this table, the rule that needs it does not run inline.

``decision_log`` records every decision the lane produced, including the ones it did not
act on. Shadow mode is the default: the lane computes and records but enforces nothing,
so a bank can measure the false-positive rate against its own traffic before a single
genuine payment is ever declined. That is the same discipline as simulating a threshold
before activating it, applied to the largest change in the platform.
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, Index, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cp_common.db import Base
from cp_common.schemas_db import DECISION, table_args

#: What the lane may answer. ``allow`` and ``decline`` are terminal; ``challenge`` asks
#: the channel for step-up authentication; ``hold`` completes the payment but queues the
#: account for review. Which of these a tenant may actually use is policy, not code —
#: not every rail can carry a step-up.
ACTIONS = ("allow", "challenge", "hold", "decline")

#: Why a decision came out the way it did. Recorded separately from the action because
#: "allowed because nothing matched" and "allowed because we ran out of time" are
#: different facts, and only one of them means the controls were applied.
OUTCOMES = (
    "clean",             # every eligible rule ran, nothing matched
    "matched",           # at least one rule matched
    "budget_exceeded",   # the latency budget expired before evaluation finished
    "store_unavailable",  # counters could not be read
    "no_policy",         # the tenant has no inline policy for this rail
    "not_screened",      # no inline rule was evaluated - an empty or unloaded catalogue
)


class AccountCounter(Base):
    """One account's precomputed observations, maintained on the write path.

    Rows are keyed by (tenant, account, observation) rather than one wide row per
    account: rules are added and retired independently, and a wide row would mean a
    migration every time the catalogue changes.
    """

    __tablename__ = "account_counters"
    __table_args__ = table_args(
        DECISION,
        UniqueConstraint("tenant_id", "account", "observation",
                         name="uq_counter_per_account_observation"),
        Index("ix_counter_lookup", "tenant_id", "account"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    account: Mapped[str] = mapped_column(String(64))
    #: The observation key a rule reads, e.g. ``credit_burst_count``. Matches the
    #: catalogue's own key so a rule needs no translation layer.
    observation: Mapped[str] = mapped_column(String(48))
    value: Mapped[float] = mapped_column(Float, default=0.0)
    #: When the underlying window last moved. A stale counter is not silently used —
    #: see ``counters.py``: past its freshness horizon it is reported absent, because a
    #: velocity count from yesterday is not a velocity count.
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                            server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DecisionLog(Base):
    """Every inline decision, enforced or not.

    This is the evidence base for three separate questions, which is why it carries more
    than the outcome: what would we have done (shadow analysis), how fast were we
    (the latency SLO), and why did we decline this customer's payment (a question that
    arrives months later, from a complaint or an inspection).
    """

    __tablename__ = "decision_log"
    __table_args__ = table_args(
        DECISION,
        Index("ix_decision_tenant_ts", "tenant_id", "decided_at"),
        Index("ix_decision_txn", "tenant_id", "txn_ref"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    #: The caller's reference for the payment. Not a foreign key: the transaction may
    #: never reach the fact tables at all if this lane declines it.
    txn_ref: Mapped[str] = mapped_column(String(64))
    rail: Mapped[str] = mapped_column(String(8), index=True)
    debtor_account: Mapped[str] = mapped_column(String(64))
    creditor_account: Mapped[str] = mapped_column(String(64), default="")
    amount_paise: Mapped[int] = mapped_column(BigInteger, default=0)

    action: Mapped[str] = mapped_column(String(12), index=True)
    outcome: Mapped[str] = mapped_column(String(20), index=True)
    #: False in shadow mode: the decision was computed and recorded, and the caller was
    #: told to allow. The distinction is the whole point of the mode.
    enforced: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    #: Rules that matched, each with its observation, threshold and band — the same
    #: trace the near-real-time lane records, so an inline decision is explainable on
    #: the same terms as any other alert.
    matched: Mapped[list] = mapped_column(JSONB, default=list)
    #: Rules that could not be evaluated, with the reason. Never empty-means-clean: an
    #: unevaluated rule is reported, exactly as an unmeasurable indicator is elsewhere.
    skipped: Mapped[list] = mapped_column(JSONB, default=list)

    #: Per-stage timings in milliseconds, so a latency regression can be attributed
    #: rather than guessed at.
    took_ms: Mapped[float] = mapped_column(Float, default=0.0)
    lookup_ms: Mapped[float] = mapped_column(Float, default=0.0)
    evaluate_ms: Mapped[float] = mapped_column(Float, default=0.0)
    budget_ms: Mapped[int] = mapped_column(Integer, default=0)

    #: Alert-level score and band, from the same definition the other lane uses. On the
    #: record so a decline is explainable in the terms an investigator already knows.
    score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    severity: Mapped[str] = mapped_column(String(12), default="low",
                                          server_default="low", index=True)

    policy_version: Mapped[str] = mapped_column(String(24), default="")
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    error: Mapped[str] = mapped_column(Text, default="", server_default="")
