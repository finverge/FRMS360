"""Observations for loan-origination fraud, computed from CBS events (BR-214).

Same discipline as ``cbs_features.py``: the catalogue owns the threshold, this owns the
measurement, and an indicator with no denominator is reported unmeasurable rather than
scored zero. Three findings, matching BR-214's own three named examples:

* **LOS-01** a falsified application - declared income wildly out of line with what the
  applicant's own account activity would support. Needs the applicant to already hold an
  account with real transaction history; a genuinely new-to-bank applicant has no
  denominator, and that is reported rather than guessed at.
* **LOS-02** a collusive valuation - one valuer's assessment of an asset running high
  against every other valuer's assessments of comparable assets. Needs enough historical
  valuations of the same asset type to mean something; a new tenant or an unusual asset
  type stays unmeasured until there is a real peer set to compare against.
* **LOS-03** a straw borrower - the same applicant identity behind more than one loan
  application. A legitimate customer applying for two products is not the pattern; several
  distinct applications from one identity in a short window is.

**Why these are computed per event, not per account-in-a-payment-batch.** BEH-02/03 and
CBS-01/02/03 are folded into whichever payment transaction happens to touch the same
account in the current batch - see ``engine.py``'s main loop. An application has no
payment transaction: it may predate the account's first one, or the account may never
transact again if the application is refused. So these are evaluated directly against
pending ``loan_application`` / ``collateral_valuation`` events - see ``evaluate_los`` in
``engine.py``.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

#: How far back an applicant's own account activity is examined for LOS-01. A salary or
#: business income pattern needs months to show, not the payment engine's hours-to-days
#: windows.
INCOME_WINDOW_DAYS = 180

#: How far back a repeat-identity search runs for LOS-03. Long enough to catch a
#: straw-borrower ring working through a quarter, short enough that two genuinely
#: unrelated applications years apart do not collide.
IDENTITY_WINDOW_DAYS = 90

#: How far back comparable valuations are pooled for LOS-02's peer benchmark.
VALUATION_WINDOW_DAYS = 365

#: Fewer peer valuations than this and a ratio is a coincidence, not a pattern. Matches
#: the same reasoning as a 30-day velocity baseline needing enough transactions to be a
#: baseline at all.
MIN_PEER_VALUATIONS = 5

#: Which CBS event kind each indicator depends on. Used to report honestly why an
#: indicator is unmeasurable for a tenant, rather than leaving it silently dormant.
FEEDS = {
    "LOS-01": ("loan_application",),
    "LOS-02": ("collateral_valuation",),
    "LOS-03": ("loan_application",),
}


def observe_application(db: Session, tenant_id: str, event: dict, *,
                        now: datetime) -> dict[str, float]:
    """LOS-01 and LOS-03 for one ``loan_application`` event.

    ``event`` carries at least ``account`` and ``attributes`` (declared_income_paise,
    applicant_id), as adapted by ``cbs_adapter.adapt_cbs``.
    """
    out: dict[str, float] = {}
    attrs = event.get("attributes") or {}
    account = event["account"]

    declared = attrs.get("declared_income_paise")
    if declared not in (None, "", 0):
        since = now - timedelta(days=INCOME_WINDOW_DAYS)
        row = db.execute(text(
            "SELECT COALESCE(SUM(amount_paise), 0) AS credit_paise "
            "  FROM analytics.fact_transaction "
            " WHERE tenant_id = :t AND creditor_account = :a AND ts >= :since"),
            {"t": tenant_id, "a": account, "since": since}).mappings().first()
        credit_paise = int(row["credit_paise"] or 0)
        # No observed credit activity at all is not "income confirmed as zero" - it is
        # an applicant this account has no history to check the claim against.
        if credit_paise > 0:
            avg_monthly_paise = credit_paise / (INCOME_WINDOW_DAYS / 30.0)
            out["LOS-01"] = float(declared) / avg_monthly_paise

    applicant_id = str(attrs.get("applicant_id") or "").strip()
    if applicant_id:
        since = now - timedelta(days=IDENTITY_WINDOW_DAYS)
        n = db.execute(text(
            "SELECT COUNT(DISTINCT account) FROM ingestion.cbs_events "
            " WHERE tenant_id = :t AND kind = 'loan_application' "
            "   AND attributes->>'applicant_id' = :aid AND account <> :acct "
            "   AND ts >= :since"),
            {"t": tenant_id, "aid": applicant_id, "acct": account,
             "since": since}).scalar()
        out["LOS-03"] = float(n or 0)

    return out


def observe_valuation(db: Session, tenant_id: str, event: dict, *,
                      now: datetime) -> dict[str, float]:
    """LOS-02 for one ``collateral_valuation`` event.

    ``asset_type`` and ``valuer_id`` are mandatory on the event (refused at ingestion if
    absent - see ``cbs_adapter.py``), so both are always present here.
    """
    attrs = event.get("attributes") or {}
    asset_type = attrs["asset_type"]
    valuer_id = attrs["valuer_id"]

    since = now - timedelta(days=VALUATION_WINDOW_DAYS)
    row = db.execute(text(
        "SELECT AVG(amount_paise) AS avg_paise, COUNT(*) AS n "
        "  FROM ingestion.cbs_events "
        " WHERE tenant_id = :t AND kind = 'collateral_valuation' "
        "   AND attributes->>'asset_type' = :at AND attributes->>'valuer_id' <> :vid "
        "   AND ts >= :since"),
        {"t": tenant_id, "at": asset_type, "vid": valuer_id,
         "since": since}).mappings().first()

    n = int(row["n"] or 0)
    if n < MIN_PEER_VALUATIONS:
        # Not enough other valuers' assessments of this asset type yet to say what
        # "in line" even means. Unmeasurable, not a pass.
        return {}
    peer_avg_paise = float(row["avg_paise"] or 0)
    if peer_avg_paise <= 0:
        return {}
    return {"LOS-02": event["amount_paise"] / peer_avg_paise}


def unmeasurable(available_kinds: set[str]) -> dict[str, str]:
    """Why each LOS-fed indicator cannot be measured, for the dormant register."""
    out: dict[str, str] = {}
    for rule, needed in FEEDS.items():
        missing = [k for k in needed if k not in available_kinds]
        if missing:
            out[rule] = ("the core banking / LOS feed has not sent "
                         + " or ".join(sorted(missing)) + " events")
    return out
