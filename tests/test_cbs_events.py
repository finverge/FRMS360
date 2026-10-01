"""CBS and loan-conduct events, and the five indicators they unblock (BR-211).

The recurring danger in this file is the denominator. Every one of these indicators is a
proportion, and a proportion with no denominator is undefined, not zero. Reporting 0.0 for
an account the CBS feed does not cover would give a clean bill of health to every borrower
the bank has not sent us - the same failure as an unloaded sanctions list.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from cp_common.db import SessionLocal
from services.analytics_service.app.detection import cbs_features as cf
from services.analytics_service.app.detection.features import (
    DECLARED, NEEDS_CBS_FEED,
)
from services.ingestion_service.app.cbs_adapter import adapt_cbs
from services.ingestion_service.app.adapters import AdapterError

ACC = "AC-LOAN-BR211"


@pytest.fixture(autouse=True)
def _clean(tid):
    yield
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM ingestion.cbs_events WHERE tenant_id = :t"),
                   {"t": tid})
        db.execute(text("DELETE FROM analytics.reference_entries "
                        "WHERE tenant_id = :t AND kind = 'group_register'"), {"t": tid})
        db.execute(text("DELETE FROM analytics.reference_lists "
                        "WHERE tenant_id = :t AND kind = 'group_register'"), {"t": tid})
        db.commit()
    finally:
        db.close()


def _ev(kind, amount=None, **extra):
    now = datetime.now(timezone.utc)
    p = {"event_id": f"E-{kind}-{extra.pop('n', 1)}", "kind": kind,
         "ts": (now - timedelta(days=5)).isoformat(), "account": ACC}
    if amount is not None:
        p["amount"] = amount
    p.update(extra)
    return p


def _post(client, token_for, tid, payloads, role="tenant_admin"):
    return client.post(f"/ingest/{tid}/cbs-events", headers=token_for(role),
                       json={"events": [{"payload": p} for p in payloads]})


# ------------------------------------------------------------------ the adapter
def test_an_unknown_event_kind_is_refused(tid):
    with pytest.raises(AdapterError) as exc:
        adapt_cbs({"event_id": "1", "kind": "vibes", "ts": "2026-08-01T00:00:00Z",
                   "account": ACC})
    assert "unknown CBS event kind" in str(exc.value)


def test_an_absent_routing_flag_is_not_read_as_routed(tid):
    """The single fact a sale_proceeds event exists to carry."""
    with pytest.raises(AdapterError) as exc:
        adapt_cbs({"event_id": "1", "kind": "sale_proceeds",
                   "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": "100.00"})
    assert "routed_through_lender" in str(exc.value)


def test_an_unknown_funding_source_is_refused(tid):
    with pytest.raises(AdapterError):
        adapt_cbs({"event_id": "1", "kind": "loan_repayment",
                   "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": "100.00",
                   "funding_source": "magic"})


def test_a_missing_funding_source_becomes_unknown_not_own_funds(tid):
    """"We were not told" must not read as "the borrower paid from their own money"."""
    out = adapt_cbs({"event_id": "1", "kind": "loan_repayment",
                     "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": "100.00"})
    assert out["funding_source"] == "unknown"


def test_amounts_are_integer_paise(tid):
    out = adapt_cbs({"event_id": "1", "kind": "loan_disbursement",
                     "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": "1234.56"})
    assert out["amount_paise"] == 123456 and isinstance(out["amount_paise"], int)


def test_a_negative_amount_is_refused(tid):
    with pytest.raises(AdapterError):
        adapt_cbs({"event_id": "1", "kind": "loan_disbursement",
                   "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": "-5.00"})


def test_an_od_position_with_no_sanctioned_limit_is_refused(tid):
    """The single fact CBS-05 exists to carry - without it the draw cannot be turned
    into a breach ratio at all."""
    with pytest.raises(AdapterError) as exc:
        adapt_cbs({"event_id": "1", "kind": "od_position",
                   "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": "500000.00"})
    assert "sanctioned_limit" in str(exc.value)


def test_an_od_position_with_a_zero_limit_is_also_refused(tid):
    with pytest.raises(AdapterError):
        adapt_cbs({"event_id": "1", "kind": "od_position",
                   "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": "500000.00",
                   "sanctioned_limit": "0"})


def test_an_od_position_with_a_limit_is_accepted(tid):
    out = adapt_cbs({"event_id": "1", "kind": "od_position",
                     "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": "500000.00",
                     "sanctioned_limit": "1000000"})
    assert out["attributes"]["sanctioned_limit_paise"] == 100_000_000


def test_a_cheque_return_needs_no_extra_field(tid):
    """Unlike od_position, CBS-04 is a plain count - no fact beyond the event's own
    existence is required."""
    out = adapt_cbs({"event_id": "1", "kind": "cheque_return",
                     "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": "75000.00"})
    assert out["kind"] == "cheque_return"


def test_a_bg_lc_event_needs_no_extra_field(tid):
    """Same plain-count shape as cheque_return - CBS-06 counts the invocation/
    devolvement itself, not any attribute of it."""
    out = adapt_cbs({"event_id": "1", "kind": "bg_lc_event",
                     "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": "500000.00"})
    assert out["kind"] == "bg_lc_event"


def test_a_facility_sanction_with_no_funds_interest_flag_is_refused(tid):
    """The single fact CBS-07 exists to carry - without it a sanction cannot be told
    apart from an ordinary, unremarkable one."""
    with pytest.raises(AdapterError) as exc:
        adapt_cbs({"event_id": "1", "kind": "facility_sanction",
                   "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": "500000.00"})
    assert "funds_interest" in str(exc.value)


def test_a_facility_sanction_flagged_funds_interest_is_accepted(tid):
    out = adapt_cbs({"event_id": "1", "kind": "facility_sanction",
                     "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": "500000.00",
                     "funds_interest": "true"})
    assert out["attributes"]["funds_interest"] is True


def test_a_facility_sanction_flagged_not_funds_interest_is_accepted(tid):
    out = adapt_cbs({"event_id": "1", "kind": "facility_sanction",
                     "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": "500000.00",
                     "funds_interest": "false"})
    assert out["attributes"]["funds_interest"] is False


def test_a_loan_repayment_with_no_due_date_carries_none(tid):
    """due_date is optional - most CBS extracts today send only the repayment itself."""
    out = adapt_cbs({"event_id": "1", "kind": "loan_repayment",
                     "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": "5000.00"})
    assert "due_date" not in out["attributes"]


def test_a_loan_repayment_due_date_must_be_iso8601(tid):
    with pytest.raises(AdapterError) as exc:
        adapt_cbs({"event_id": "1", "kind": "loan_repayment",
                   "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": "5000.00",
                   "due_date": "01-Aug-2026"})
    assert "due_date" in str(exc.value)


def test_a_loan_repayment_with_a_valid_due_date_is_accepted(tid):
    out = adapt_cbs({"event_id": "1", "kind": "loan_repayment",
                     "ts": "2026-08-01T00:00:00Z", "account": ACC, "amount": "5000.00",
                     "due_date": "2026-07-20"})
    assert out["attributes"]["due_date"] == "2026-07-20"


# ------------------------------------------------------------------ intake
def test_events_are_accepted_and_deduplicated(ingestion_client, token_for, tid):
    events = [_ev("loan_disbursement", "10000.00", n=1),
              _ev("cash_withdrawal", "4000.00", n=2)]
    first = _post(ingestion_client, token_for, tid, events)
    assert first.status_code == 202, first.text
    assert first.json()["accepted"] == 2

    again = _post(ingestion_client, token_for, tid, events)
    assert again.json()["duplicates"] == 2, "a re-sent nightly extract double-counted"
    assert again.json()["accepted"] == 0


def test_a_bad_row_does_not_cost_the_good_rows(ingestion_client, token_for, tid):
    body = _post(ingestion_client, token_for, tid, [
        _ev("loan_disbursement", "100.00", n=1),
        _ev("sale_proceeds", "50.00", n=2),            # no routing flag
        _ev("cash_withdrawal", "25.00", n=3)]).json()
    assert body["accepted"] == 2
    assert body["rejected"] == 1
    assert body["errors"][0]["index"] == 1


def test_coverage_reports_which_feeds_are_missing(ingestion_client, token_for, tid):
    """A feed configured once and then gone quiet looks identical to one never set up."""
    _post(ingestion_client, token_for, tid, [_ev("loan_disbursement", "100.00", n=1)])
    body = ingestion_client.get(f"/ingest/{tid}/cbs-events/coverage",
                                headers=token_for("supervisor")).json()
    kinds = {k["kind"]: k for k in body["kinds"]}
    assert kinds["loan_disbursement"]["received"] == 1
    assert kinds["sale_proceeds"]["received"] == 0
    assert "BEH-03" in kinds["sale_proceeds"]["feeds"]


# ------------------------------------------------------------------ the ratios
def test_no_events_means_unmeasurable_not_clean():
    """The invariant this whole feature turns on."""
    assert cf.observe_loan(cf.LoanContext()) == {}


def test_cash_ratio_needs_a_disbursement_to_divide_by():
    """Cash withdrawn from an account with no reported disbursement is not 'ratio 0'."""
    only_cash = cf.LoanContext(cash_paise=500_000)
    assert "CBS-02" not in cf.observe_loan(only_cash)

    with_disb = cf.LoanContext(cash_paise=500_000, disbursed_paise=1_000_000)
    assert cf.observe_loan(with_disb)["CBS-02"] == 0.5


def test_external_funding_ratio():
    c = cf.LoanContext(repaid_paise=1_000_000, external_repaid_paise=700_000)
    assert cf.observe_loan(c)["BEH-02"] == 0.7


def test_unrouted_sale_proceeds_ratio():
    c = cf.LoanContext(proceeds_paise=200_000, unrouted_paise=150_000)
    assert cf.observe_loan(c)["BEH-03"] == 0.75


def test_diversion_is_measured_against_what_was_released():
    """Money released and never accounted for is the case CBS-01 is named for, so the
    denominator is the disbursement, not the utilisation that was reported."""
    c = cf.LoanContext(disbursed_paise=1_000_000, utilised_paise=400_000,
                       diverted_paise=400_000)
    assert cf.observe_loan(c)["CBS-01"] == 0.4


def test_group_exposure_ratio():
    c = cf.LoanContext(utilised_paise=1_000_000, group_exposure_paise=350_000)
    assert cf.observe_loan(c)["CBS-03"] == 0.35


def test_cheque_return_count_is_omitted_when_the_kind_was_never_seen():
    """No cheque_return events for this account is not the same as zero returns being
    confirmed - it may simply be that the feed never covered this account."""
    c = cf.LoanContext(disbursed_paise=1_000_000)  # active account, other CBS activity
    assert "CBS-04" not in cf.observe_loan(c)


def test_cheque_return_count_reports_zero_as_a_real_clean_value():
    """Once the kind has actually been seen for this account, zero returns is a
    genuine, measured fact - unlike the ratios above, there is no denominator to be
    missing."""
    c = cf.LoanContext(cheque_return_count=0, kinds={"cheque_return"})
    assert cf.observe_loan(c)["CBS-04"] == 0.0


def test_cheque_return_count_above_zero():
    c = cf.LoanContext(cheque_return_count=4, kinds={"cheque_return"})
    assert cf.observe_loan(c)["CBS-04"] == 4.0


def test_bg_lc_event_count_is_omitted_when_the_kind_was_never_seen():
    c = cf.LoanContext(disbursed_paise=1_000_000)
    assert "CBS-06" not in cf.observe_loan(c)


def test_bg_lc_event_count_reports_zero_as_a_real_clean_value():
    c = cf.LoanContext(bg_lc_event_count=0, kinds={"bg_lc_event"})
    assert cf.observe_loan(c)["CBS-06"] == 0.0


def test_bg_lc_event_count_above_zero():
    c = cf.LoanContext(bg_lc_event_count=2, kinds={"bg_lc_event"})
    assert cf.observe_loan(c)["CBS-06"] == 2.0


def test_interest_funding_count_is_omitted_when_the_kind_was_never_seen():
    c = cf.LoanContext(disbursed_paise=1_000_000)
    assert "CBS-07" not in cf.observe_loan(c)


def test_interest_funding_count_only_counts_flagged_sanctions(
        ingestion_client, token_for, tid):
    """A facility_sanction event flagged funds_interest=False must not inflate CBS-07 -
    only sanctions explicitly marked as funding interest count."""
    _post(ingestion_client, token_for, tid, [
        _ev("facility_sanction", "200000.00", n=1, funds_interest="true"),
        _ev("facility_sanction", "300000.00", n=2, funds_interest="false"),
    ])
    db = SessionLocal()
    try:
        ctx = cf.load_loan_context(db, tid, [ACC], datetime.now(timezone.utc))
    finally:
        db.close()
    assert cf.observe_loan(ctx[ACC])["CBS-07"] == 1.0


def test_delayed_repayment_count_is_omitted_when_no_due_date_was_ever_seen():
    """A loan_repayment event with no due_date must not make CBS-08 report a real
    zero - the CBS simply never told us when anything was due."""
    c = cf.LoanContext(repaid_paise=1_000_000, kinds={"loan_repayment"})
    assert "CBS-08" not in cf.observe_loan(c)


def test_delayed_repayment_count_reports_zero_as_a_real_clean_value():
    c = cf.LoanContext(delayed_repayment_count=0, kinds={"loan_repayment_with_due_date"})
    assert cf.observe_loan(c)["CBS-08"] == 0.0


def test_a_repayment_within_the_grace_period_is_not_counted_as_delayed(
        ingestion_client, token_for, tid):
    now = datetime.now(timezone.utc)
    p = _ev("loan_repayment", "5000.00", n=1,
           due_date=(now - timedelta(days=10)).date().isoformat())
    p["ts"] = (now - timedelta(days=5)).isoformat()  # paid 5 days after due - within grace
    _post(ingestion_client, token_for, tid, [p])
    db = SessionLocal()
    try:
        ctx = cf.load_loan_context(db, tid, [ACC], now)
    finally:
        db.close()
    assert cf.observe_loan(ctx[ACC])["CBS-08"] == 0.0


def test_a_repayment_past_the_grace_period_counts_as_delayed(
        ingestion_client, token_for, tid):
    now = datetime.now(timezone.utc)
    p = _ev("loan_repayment", "5000.00", n=1, due_date=(now - timedelta(days=20)).date().isoformat())
    p["ts"] = (now - timedelta(days=5)).isoformat()  # paid 15 days after due
    _post(ingestion_client, token_for, tid, [p])
    db = SessionLocal()
    try:
        ctx = cf.load_loan_context(db, tid, [ACC], now)
    finally:
        db.close()
    assert cf.observe_loan(ctx[ACC])["CBS-08"] == 1.0


def test_od_breach_ratio_needs_a_reported_limit_to_divide_by():
    """A peak-utilised figure with no od_position event carrying a limit is not
    'ratio 0' - the account may not even have an overdraft facility."""
    no_limit = cf.LoanContext(od_breach_ratio=0.9, od_limit_paise=0)
    assert "CBS-05" not in cf.observe_loan(no_limit)

    with_limit = cf.LoanContext(od_breach_ratio=1.35, od_limit_paise=1_000_000)
    assert cf.observe_loan(with_limit)["CBS-05"] == 1.35


def test_od_breach_ratio_pairs_utilisation_with_its_own_event_limit(
        ingestion_client, token_for, tid):
    """A modest draw against a small limit must not be diluted by an unrelated, much
    larger limit reported on another day - each event's ratio is computed against its
    own reported limit, and the peak across events is what CBS-05 reports. Taking
    max(utilised) / max(limit) instead would report this account as clean (300k against
    a 2,000k limit = 0.15) when the true peak breach was 0.9 (450k against a 500k
    limit)."""
    _post(ingestion_client, token_for, tid, [
        _ev("od_position", "4500.00", n=1, sanctioned_limit="5000.00"),
        _ev("od_position", "3000.00", n=2, sanctioned_limit="20000.00"),
    ])
    db = SessionLocal()
    try:
        ctx = cf.load_loan_context(db, tid, [ACC], datetime.now(timezone.utc))
    finally:
        db.close()
    assert cf.observe_loan(ctx[ACC])["CBS-05"] == pytest.approx(0.9)


# ------------------------------------------------------------------ end to end
def test_events_land_and_produce_the_ratios(ingestion_client, token_for, tid):
    _post(ingestion_client, token_for, tid, [
        _ev("loan_disbursement", "100000.00", n=1),
        _ev("cash_withdrawal", "45000.00", n=2),
        _ev("loan_repayment", "20000.00", n=3, funding_source="external_bank"),
        _ev("sale_proceeds", "30000.00", n=4, routed_through_lender="false"),
        _ev("loan_utilisation", "60000.00", n=5, within_sanctioned_purpose="false"),
    ])
    db = SessionLocal()
    try:
        ctx = cf.load_loan_context(db, tid, [ACC], datetime.now(timezone.utc))
    finally:
        db.close()
    obs = cf.observe_loan(ctx[ACC])
    assert obs["CBS-02"] == pytest.approx(0.45)
    assert obs["BEH-02"] == 1.0
    assert obs["BEH-03"] == 1.0
    assert obs["CBS-01"] == pytest.approx(0.6)


def test_an_unknown_purpose_is_not_counted_as_diverted(ingestion_client, token_for, tid):
    """None means the CBS did not say. An unknown payee is not evidence of diversion."""
    _post(ingestion_client, token_for, tid, [
        _ev("loan_disbursement", "100000.00", n=1),
        _ev("loan_utilisation", "50000.00", n=2)])     # no purpose flag
    db = SessionLocal()
    try:
        ctx = cf.load_loan_context(db, tid, [ACC], datetime.now(timezone.utc))
    finally:
        db.close()
    assert cf.observe_loan(ctx[ACC])["CBS-01"] == 0.0


# ------------------------------------------------------------------ honesty
def test_a_missing_feed_is_named_not_left_dormant():
    why = cf.unmeasurable(set(), has_group_register=False)
    assert set(why) == set(NEEDS_CBS_FEED)
    assert "loan_repayment" in why["BEH-02"]
    assert "cash_withdrawal" in why["CBS-02"]


def test_a_present_feed_stops_being_reported_as_missing():
    why = cf.unmeasurable({"loan_repayment"}, has_group_register=False)
    assert "BEH-02" not in why
    assert "CBS-02" in why


def test_group_exposure_needs_the_register_even_with_events():
    """Otherwise CBS-03 measures zero group exposure against a real denominator, which
    reads as a clean result rather than an unloaded list."""
    why = cf.unmeasurable({"loan_utilisation"}, has_group_register=False)
    assert "CBS-03" in why and "register" in why["CBS-03"]
    assert "CBS-03" not in cf.unmeasurable({"loan_utilisation"}, has_group_register=True)


def test_cheque_return_and_od_position_feeds_are_named_when_missing():
    why = cf.unmeasurable(set(), has_group_register=False)
    assert "cheque_return" in why["CBS-04"]
    assert "od_position" in why["CBS-05"]
    assert "CBS-04" not in cf.unmeasurable({"cheque_return"}, has_group_register=False)
    assert "CBS-05" not in cf.unmeasurable({"od_position"}, has_group_register=False)


def test_bg_lc_and_facility_sanction_feeds_are_named_when_missing():
    why = cf.unmeasurable(set(), has_group_register=False)
    assert "bg_lc_event" in why["CBS-06"]
    assert "facility_sanction" in why["CBS-07"]
    assert "CBS-06" not in cf.unmeasurable({"bg_lc_event"}, has_group_register=False)
    assert "CBS-07" not in cf.unmeasurable({"facility_sanction"}, has_group_register=False)


def test_cbs08_feed_is_named_when_missing():
    why = cf.unmeasurable(set(), has_group_register=False)
    assert "loan_repayment" in why["CBS-08"]
    assert "CBS-08" not in cf.unmeasurable({"loan_repayment"}, has_group_register=False)


def test_every_indicator_is_still_declared_exactly_once():
    from services.config_service.app.ews_catalogue import _RULES, QUALITATIVE_FAMILIES

    quantitative = {r[0] for r in _RULES if r[1] not in QUALITATIVE_FAMILIES}
    assert not sorted(quantitative - DECLARED)
