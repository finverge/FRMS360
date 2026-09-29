"""Retention classes and the floors that cannot be configured away.

Two obligations pull in opposite directions and both are binding:

* **The DPDP Act's storage limitation duty.** Personal data is kept for as long as the
  purpose requires and then erased. Holding a decade of transaction detail about people
  who were never suspected of anything is itself the breach.

* **The fraud-record retention floor.** RBI expects fraud case records, the evidence
  behind them and the returns filed to survive long after the case closes - an inspection
  years later asks to see them, and "we purged it under our privacy policy" is not an
  answer anyone accepts.

So retention is per *class*, not per table, and every class has a **floor that a tenant
cannot configure below**. A tenant may keep data longer than the floor; it may never keep
it for less. That asymmetry is the whole design: a misconfigured purge that deletes too
little is an embarrassment, and one that deletes a fraud file is unrecoverable.

Nothing here deletes anything on its own. It computes what is eligible; the caller decides,
and the runner defaults to a dry run.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetentionClass:
    key: str
    label: str
    #: What the tenant's policy key is called, so a bank sets its own figure.
    policy_key: str
    #: Days. A tenant may configure more than this; never fewer.
    floor_days: int
    default_days: int
    why: str
    #: Rows matching this are never eligible, whatever their age. The single most
    #: important field here.
    never_when: str = ""


CLASSES: tuple[RetentionClass, ...] = (
    RetentionClass(
        "auth_sessions", "Sessions and login state", "retain_sessions_days",
        floor_days=7, default_days=90,
        why="Needed to investigate account compromise; of no value after that."),
    RetentionClass(
        "notifications", "Delivered notifications", "retain_notifications_days",
        floor_days=30, default_days=180,
        why="Operational record of what a user was told. Not evidence of a fraud."),
    RetentionClass(
        "raw_ingest", "Raw inbound transactions", "retain_raw_ingest_days",
        floor_days=90, default_days=400,
        why="What the bank sent, byte for byte. Reconstructible from the fact table "
            "once projected, so it is the first thing that can go.",
        never_when="the row has not been projected yet"),
    RetentionClass(
        "transactions", "Transaction facts", "retain_transactions_days",
        floor_days=365 * 3, default_days=365 * 5,
        why="The detection substrate. Purging it destroys the ability to explain why a "
            "past alert fired.",
        never_when="the transaction is evidence on any alert linked to a case"),
    RetentionClass(
        "alerts_unlinked", "Alerts never linked to a case", "retain_alerts_days",
        floor_days=365, default_days=365 * 2,
        why="A dispositioned false positive is model-tuning data, not a fraud record.",
        never_when="the alert is linked to a case"),
    RetentionClass(
        "cases_non_fraud", "Closed cases that were not fraud",
        "retain_closed_cases_days",
        floor_days=365 * 3, default_days=365 * 5,
        why="An exonerated borrower has a strong interest in the allegation not being "
            "kept forever, and no regulation requires it.",
        never_when="the case was declared fraud, or is not closed"),
    RetentionClass(
        # The long floor. Everything about a declared fraud.
        "cases_fraud", "Declared frauds, their evidence and returns",
        "retain_fraud_records_days",
        floor_days=365 * 8, default_days=365 * 10,
        why="Fraud files, reasoned orders, filed returns and the accountability "
            "examination. An inspection reaches back years, and this is what it reads.",
        never_when="any return on the case is unfiled, or the case is not closed"),
    RetentionClass(
        "audit", "Platform audit trail", "retain_audit_days",
        floor_days=365 * 5, default_days=365 * 8,
        why="Who did what. The one log that must outlive the records it describes."),
)

BY_KEY = {c.key: c for c in CLASSES}


def effective_days(cls: RetentionClass, policy: dict) -> tuple[int, bool]:
    """The tenant's figure, raised to the floor if it sits below it.

    Returns ``(days, was_raised)``. A configuration below the floor is not an error to
    reject - the tenant may simply have a shorter privacy policy - but it is silently
    dangerous, so the caller is told the floor was applied and can say so.
    """
    raw = policy.get(cls.policy_key)
    try:
        days = int(raw) if raw is not None else cls.default_days
    except (TypeError, ValueError):
        days = cls.default_days
    if days < cls.floor_days:
        return cls.floor_days, True
    return days, False


def policy_defaults() -> dict:
    return {c.policy_key: c.default_days for c in CLASSES}
