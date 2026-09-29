"""Cash Transaction Report: aggregation and the return itself (BR-508).

PMLA Rule 3 requires a reporting entity to report cash transactions - deposits and
withdrawals both - once an account's total for a calendar month reaches Rs 10,00,000,
whether that arrives as one transaction or as several smaller ones that add up. The
platform aggregates from ``cash_transaction`` CBS events (BR-211's intake path, extended -
see ``ingestion_service.app.cbs_models``), not from the payment rails: cash movement does
not traverse UPI/NEFT/RTGS/CARD at all.

**This is not shaped like an FMR or STR, on purpose.** Those are about a case - a specific
investigated fraud. A CTR is not: it is owed whether or not anything suspicious happened,
for any account whose cash crossed the threshold. So this is account-and-month centric,
with its own model (``ctr_model.CtrFiling``) rather than a variant squeezed into
``RegulatoryFiling``'s case-shaped one - see that module's docstring for why.

Same honesty as ``filings/schema.py`` about the format binding: FIU-IND's own FINnet CTR
schema is not reproduced here. This assembles and validates a submission-ready record in
the platform's own structured form; turning it into FIU-IND's exact filing format is a
mapping this platform declares rather than guesses at.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import data_source
from .filings.schema import Field, Gap, Validation

SCHEMA_VERSION = "1.0.0"

#: PMLA Rule 3: cash transactions, or connected cash transactions within a calendar
#: month, totalling Rs 10,00,000 (ten lakh) or more.
CTR_THRESHOLD_PAISE = 100_000_000

RETURN_LABEL = "Cash Transaction Report"
RETURN_RECIPIENT = "FIU-IND"

FORMAT_BINDING = {
    "channel": "FIU-IND FINnet",
    "status": "not_bound",
    "needs": "The current FINnet CTR schema and the entity's reporting credentials.",
}

FIELDS = (
    Field("entity_name", "Reporting entity"),
    Field("entity_type", "Entity category"),
    Field("account", "Account number"),
    Field("period", "Reporting period (calendar month)"),
    Field("transaction_count", "Number of cash transactions in the period"),
    Field("total_amount", "Total cash transaction value in the period"),
    Field("deposit_amount", "Of which, deposits", mandatory=False),
    Field("withdrawal_amount", "Of which, withdrawals", mandatory=False),
    Field("first_transaction_date", "Date of first cash transaction in the period"),
    Field("last_transaction_date", "Date of last cash transaction in the period"),
)


def _rupees(paise: int | None) -> str | None:
    if paise is None:
        return None
    return f"{int(paise) / 100:,.2f}"


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat() if value.tzinfo \
            else value.replace(tzinfo=timezone.utc).isoformat()
    return str(value)


def month_bounds(period: str) -> tuple[datetime, datetime]:
    """[start, end) for a "YYYY-MM" period, in UTC."""
    try:
        year, month = (int(x) for x in period.split("-", 1))
        if not 1 <= month <= 12:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"period must be 'YYYY-MM', got {period!r}") from exc
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = (datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12
           else datetime(year, month + 1, 1, tzinfo=timezone.utc))
    return start, end


#: Shared aggregate columns. Standalone SELECT-list text, not a runnable query on its
#: own - each caller supplies its own FROM/WHERE/GROUP BY so account-scoped and
#: month-wide aggregation stay two plain, readable queries rather than one built by
#: string surgery on the other.
_AGG_COLUMNS = """
           COUNT(*) AS txn_count,
           SUM(amount_paise) AS total_paise,
           SUM(amount_paise) FILTER (WHERE attributes->>'direction' = 'deposit')
               AS deposit_paise,
           SUM(amount_paise) FILTER (WHERE attributes->>'direction' = 'withdrawal')
               AS withdrawal_paise,
           MIN(ts) AS first_ts, MAX(ts) AS last_ts
"""


def aggregate_period(db: Session, tenant_id: str, period: str) -> list[dict]:
    """Every account with cash activity in this calendar month, summed - the working
    list a compliance officer checks against the threshold, not yet a filing."""
    since, until = month_bounds(period)
    rows = db.execute(text(
        f"SELECT account, {_AGG_COLUMNS} "
        "  FROM ingestion.cbs_events "
        " WHERE tenant_id = :t AND kind = 'cash_transaction' "
        "   AND ts >= :since AND ts < :until "
        " GROUP BY account ORDER BY total_paise DESC"),
        {"t": tenant_id, "since": since, "until": until}).mappings().all()
    return [dict(r) for r in rows]


def due_accounts(db: Session, tenant_id: str, period: str) -> list[dict]:
    """Accounts that actually meet PMLA Rule 3's threshold for this month."""
    return [r for r in aggregate_period(db, tenant_id, period)
            if r["total_paise"] and int(r["total_paise"]) >= CTR_THRESHOLD_PAISE]


def build(db: Session, *, tenant_id: str, account: str, period: str, entity: dict) -> dict:
    """Gather the payload for one account's CTR in one calendar month."""
    since, until = month_bounds(period)
    row = db.execute(text(
        f"SELECT {_AGG_COLUMNS} "
        "  FROM ingestion.cbs_events "
        " WHERE tenant_id = :t AND kind = 'cash_transaction' AND account = :a "
        "   AND ts >= :since AND ts < :until"),
        {"t": tenant_id, "a": account, "since": since, "until": until}).mappings().first()
    if not row or not row["txn_count"]:
        raise LookupError(f"no cash transactions for account {account} in {period}")

    events = db.execute(text(
        "SELECT source_event_id, ts, amount_paise, "
        "       attributes->>'direction' AS direction, source "
        "  FROM ingestion.cbs_events "
        " WHERE tenant_id = :t AND kind = 'cash_transaction' AND account = :a "
        "   AND ts >= :since AND ts < :until ORDER BY ts"),
        {"t": tenant_id, "a": account, "since": since, "until": until}).mappings().all()

    # Provenance gate (BR-611), same reasoning as FMR/STR: a return addressed to a
    # regulator must be built from live data, never the demonstration corpus.
    tainted = data_source.non_live([e["source"] for e in events])
    if tainted:
        raise data_source.NotLiveData("A CTR", tainted)

    total_paise = int(row["total_paise"] or 0)
    return {
        "return_kind": "ctr",
        "return_label": RETURN_LABEL,
        "reported_to": RETURN_RECIPIENT,
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(datetime.now(timezone.utc)),

        "entity_name": entity.get("legal_name") or entity.get("display_name"),
        "entity_type": entity.get("entity_label") or entity.get("entity_type"),

        "account": account,
        "period": period,
        "transaction_count": int(row["txn_count"]),
        "total_amount": _rupees(total_paise),
        "total_amount_paise": total_paise,
        "deposit_amount": _rupees(row["deposit_paise"]),
        "withdrawal_amount": _rupees(row["withdrawal_paise"]),
        "first_transaction_date": _iso(row["first_ts"]),
        "last_transaction_date": _iso(row["last_ts"]),
        "threshold_paise": CTR_THRESHOLD_PAISE,
        "threshold_met": total_paise >= CTR_THRESHOLD_PAISE,

        "evidence": {
            "transactions": [{
                "event_id": e["source_event_id"], "at": _iso(e["ts"]),
                "amount": _rupees(e["amount_paise"]), "direction": e["direction"],
            } for e in events],
        },
    }


def validate(payload: dict) -> Validation:
    """Check a built payload against the field set, plus the threshold itself.

    An account below the monthly threshold has no filing obligation under PMLA Rule 3 -
    reported as a blocking gap, the same as a missing field, so generation refuses it
    rather than producing a return nobody was required to file.
    """
    out = Validation(kind="ctr")
    for f in FIELDS:
        value = payload.get(f.key)
        missing = value is None or value == "" or value == [] or value == {}
        if isinstance(value, bool):
            missing = False
        if missing:
            out.gaps.append(Gap(f.key, f.label, f.mandatory, f.note))
    if not payload.get("threshold_met"):
        out.gaps.append(Gap(
            "threshold_met", "Rs 10,00,000 monthly threshold met", True,
            "PMLA Rule 3 requires filing only once this account's cash transactions "
            "for the month total Rs 10,00,000 or more, individually or in aggregate."))
    return out
