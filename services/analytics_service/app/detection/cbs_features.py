"""Observations computed from CBS and loan-system events (BR-211).

Nine indicators, each measured per borrowal account over the window. The rule owns the
threshold; this owns the measurement, exactly as for the payment indicators.

**The invariant that matters here is the denominator.** Five of these are proportions, and
a proportion with no denominator is not zero - it is undefined. If no disbursement has
been reported for an account, ``cash_ratio`` is not 0.0 (which reads as "no cash was
taken", a clean bill of health); it is unmeasurable, and the indicator is left out of the
result entirely. Getting this backwards would report perfect conduct on every account the
CBS feed does not cover, which is the same failure mode as an unloaded sanctions list.

The sixth, ``cheque_return_count`` (CBS-04), is a straight count rather than a ratio and
so has no such denominator - but it keeps the same "omit unless this account's feed
coverage is known" discipline for consistency (see observe_loan). The seventh,
``od_breach_ratio`` (CBS-05), is a proportion again (peak utilisation over the sanctioned
limit) and follows the same denominator rule as the first five.

The eighth and ninth, ``bg_lc_event_count`` (CBS-06) and ``interest_funding_count``
(CBS-07), are straight counts like CBS-04 - "frequent invocation" and "sanctioning a
fresh facility to fund interest" are both about how often something happened, not a
ratio of anything. Same "omit unless the feed is known to cover this account" gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

#: Which event kind each indicator depends on. Used to report honestly why an indicator
#: is unmeasurable for a tenant, rather than leaving it silently dormant.
FEEDS = {
    "BEH-02": ("loan_repayment",),
    "BEH-03": ("sale_proceeds",),
    "CBS-01": ("loan_disbursement", "loan_utilisation"),
    "CBS-02": ("loan_disbursement", "cash_withdrawal"),
    "CBS-03": ("loan_utilisation",),
    "CBS-04": ("cheque_return",),
    "CBS-05": ("od_position",),
    "CBS-06": ("bg_lc_event",),
    "CBS-07": ("facility_sanction",),
    # loan_repayment already feeds BEH-02; CBS-08 needs the same event to additionally
    # carry a due_date, a second, independent dependency the same way CPT-03 has two.
    "CBS-08": ("loan_repayment",),
    # Not a ratio needing a denominator like the others - CPT-03 needs a
    # collateral_valuation event to carry a matchable collateral_id at all, which is a
    # second, independent dependency beyond "is a CERSAI list loaded" (see
    # detection/features.py's NEEDS_REFERENCE_DATA and screen_collateral()).
    "CPT-03": ("collateral_valuation",),
}

#: How far back the ratios look. A loan behaves over quarters, not hours - the payment
#: windows are useless here.
WINDOW_DAYS = 180

#: A repayment counts as delayed (CBS-08) only past this many days beyond its due_date -
#: a same-week settlement lag is normal banking friction, not the conduct RBI's #5 names.
DELAY_GRACE_DAYS = 7

#: The per-account marker load_loan_context() adds to ``kinds`` only when a loan_repayment
#: event actually carried a due_date - not a real CBS event kind, so it is never confused
#: with one in FEEDS/unmeasurable()'s tenant-wide "is this feed connected" check. It exists
#: purely for observe_loan()'s finer-grained "does CBS-08 have what it needs for THIS
#: account" gate, the same distinction CPT-03's second dependency needs.
_DUE_DATE_SEEN = "loan_repayment_with_due_date"


@dataclass
class LoanContext:
    disbursed_paise: int = 0
    utilised_paise: int = 0
    diverted_paise: int = 0
    cash_paise: int = 0
    repaid_paise: int = 0
    external_repaid_paise: int = 0
    proceeds_paise: int = 0
    unrouted_paise: int = 0
    group_exposure_paise: int = 0
    #: A straight count, not a ratio - unlike everything else on this dataclass, zero is
    #: itself a real, measured value rather than an absent one. See observe_loan.
    cheque_return_count: int = 0
    #: BG invocations plus LC devolvements against this account (CBS-06) - same
    #: straight-count shape as cheque_return_count.
    bg_lc_event_count: int = 0
    #: facility_sanction events explicitly flagged funds_interest=True (CBS-07) - not
    #: every sanction, only the ones the CBS marked as funding interest rather than
    #: business need.
    interest_funding_count: int = 0
    #: loan_repayment events whose due_date was more than DELAY_GRACE_DAYS before the
    #: repayment's own timestamp (CBS-08). Only counted for events that carried a
    #: due_date at all - see _DUE_DATE_SEEN.
    delayed_repayment_count: int = 0
    #: Peak (utilised / sanctioned limit) seen across this account's od_position events.
    #: Paired per event, not max-of-utilised over max-of-limit, so a high balance on one
    #: day is never compared against a limit reported on a different day.
    od_breach_ratio: float = 0.0
    #: >0 once any od_position event has reported a positive sanctioned limit - the
    #: presence gate for CBS-05, same role disbursed_paise plays for CBS-01/02.
    od_limit_paise: int = 0
    #: The most recent collateral_id a collateral_valuation event reported for this
    #: account, if any - most CBS/LOS extracts don't send this yet (see cbs_adapter.py),
    #: so None is the normal case, not a defect. Feeds CPT-03's collateral_ref; absent
    #: means CPT-03 stays unmeasured exactly as it already is without a CERSAI feed.
    collateral_id: str | None = None
    #: Kinds actually seen for this account, so a missing feed is distinguishable from a
    #: feed that reported nothing of interest.
    kinds: set[str] = field(default_factory=set)


def load_loan_context(db: Session, tenant_id: str, accounts: list[str], now: datetime,
                      *, group_accounts: set[str] | None = None,
                      window_days: int = WINDOW_DAYS) -> dict[str, LoanContext]:
    """One pass over the CBS events for the whole batch."""
    ctx: dict[str, LoanContext] = {a: LoanContext() for a in accounts}
    if not accounts:
        return ctx
    since = now - timedelta(days=window_days)
    group_accounts = group_accounts or set()

    rows = db.execute(text("""
        SELECT account, kind, amount_paise, funding_source, routed_through_lender,
               within_sanctioned_purpose, counterparty_account, attributes, ts
          FROM ingestion.cbs_events
         WHERE tenant_id = :t AND account = ANY(:accts) AND ts >= :since
    """), {"t": tenant_id, "accts": accounts, "since": since}).mappings()

    for r in rows:
        c = ctx.get(r["account"])
        if c is None:
            continue
        c.kinds.add(r["kind"])
        amount = int(r["amount_paise"] or 0)
        kind = r["kind"]

        if kind == "cheque_return":
            c.cheque_return_count += 1
        elif kind == "bg_lc_event":
            c.bg_lc_event_count += 1
        elif kind == "facility_sanction":
            if (r["attributes"] or {}).get("funds_interest") is True:
                c.interest_funding_count += 1
        elif kind == "od_position":
            # The adapter refuses an od_position event with no positive sanctioned
            # limit (see cbs_adapter.py), so a limit here is always > 0 when present.
            limit = int((r["attributes"] or {}).get("sanctioned_limit_paise") or 0)
            if limit > 0:
                c.od_limit_paise = limit
                ratio = amount / limit
                if ratio > c.od_breach_ratio:
                    c.od_breach_ratio = ratio
        elif kind == "loan_disbursement":
            c.disbursed_paise += amount
        elif kind == "cash_withdrawal":
            c.cash_paise += amount
        elif kind == "loan_utilisation":
            c.utilised_paise += amount
            # Only an explicit False counts as diverted. None means the CBS did not say,
            # and an unknown payee is not evidence of diversion.
            if r["within_sanctioned_purpose"] is False:
                c.diverted_paise += amount
            if r["counterparty_account"] and r["counterparty_account"] in group_accounts:
                c.group_exposure_paise += amount
        elif kind == "loan_repayment":
            c.repaid_paise += amount
            if r["funding_source"] == "external_bank":
                c.external_repaid_paise += amount
            due_raw = (r["attributes"] or {}).get("due_date")
            if due_raw:
                c.kinds.add(_DUE_DATE_SEEN)
                due = date.fromisoformat(due_raw)
                if (r["ts"].date() - due).days > DELAY_GRACE_DAYS:
                    c.delayed_repayment_count += 1
        elif kind == "sale_proceeds":
            c.proceeds_paise += amount
            if r["routed_through_lender"] is False:
                c.unrouted_paise += amount
        elif kind == "collateral_valuation":
            cid = (r["attributes"] or {}).get("collateral_id")
            if cid:
                c.collateral_id = str(cid)

    return ctx


def observe_loan(ctx: LoanContext) -> dict[str, float]:
    """The five ratios, omitting any whose denominator is absent.

    An omitted key means "not measurable for this account", which the engine reports
    rather than scoring. It never means zero.
    """
    out: dict[str, float] = {}

    if ctx.repaid_paise > 0:
        out["BEH-02"] = ctx.external_repaid_paise / ctx.repaid_paise
    if ctx.proceeds_paise > 0:
        out["BEH-03"] = ctx.unrouted_paise / ctx.proceeds_paise
    if ctx.disbursed_paise > 0:
        # Diversion is measured against what was released, not against what was spent:
        # money released and never accounted for is the case CBS-01 is named for.
        out["CBS-01"] = ctx.diverted_paise / ctx.disbursed_paise
        out["CBS-02"] = ctx.cash_paise / ctx.disbursed_paise
    if ctx.utilised_paise > 0:
        out["CBS-03"] = ctx.group_exposure_paise / ctx.utilised_paise
    # A count, not a ratio, but the same "only if this account actually has the feed"
    # gate as everything above: an account this window never saw a cheque_return event
    # for stays out of `out` entirely, same as an account with no reported disbursement.
    # (Zero returns *with* the feed present would be a real "clean" - it just cannot be
    # told apart from "the feed never covered this account" from ctx alone.)
    if "cheque_return" in ctx.kinds:
        out["CBS-04"] = float(ctx.cheque_return_count)
    if ctx.od_limit_paise > 0:
        out["CBS-05"] = ctx.od_breach_ratio
    if "bg_lc_event" in ctx.kinds:
        out["CBS-06"] = float(ctx.bg_lc_event_count)
    if "facility_sanction" in ctx.kinds:
        out["CBS-07"] = float(ctx.interest_funding_count)
    if _DUE_DATE_SEEN in ctx.kinds:
        out["CBS-08"] = float(ctx.delayed_repayment_count)
    return out


def group_accounts(db: Session, tenant_id: str) -> set[str]:
    """Accounts belonging to connected group companies, from the reference register.

    Empty when no register is loaded - and CBS-03 then measures zero exposure against a
    real denominator, which is wrong. The engine checks list availability separately and
    leaves CBS-03 unmeasured in that case.
    """
    rows = db.execute(text(
        "SELECT e.match_key, e.attributes FROM analytics.reference_entries e "
        "  JOIN analytics.reference_lists l ON l.id = e.list_id "
        " WHERE e.tenant_id = :t AND l.active IS TRUE AND e.kind = 'group_register'"),
        {"t": tenant_id}).mappings().all()
    out: set[str] = set()
    for r in rows:
        attrs = r["attributes"] or {}
        for acct in (attrs.get("accounts") or []):
            out.add(str(acct))
        if attrs.get("account"):
            out.add(str(attrs["account"]))
    return out


def unmeasurable(available_kinds: set[str], has_group_register: bool) -> dict[str, str]:
    """Why each CBS-fed indicator cannot be measured, for the dormant register."""
    out: dict[str, str] = {}
    for rule, needed in FEEDS.items():
        missing = [k for k in needed if k not in available_kinds]
        if missing:
            out[rule] = ("the core banking feed has not sent "
                         + " or ".join(sorted(missing)) + " events")
    if not has_group_register and "CBS-03" not in out:
        out["CBS-03"] = "no connected-group register has been loaded for this tenant"
    return out
