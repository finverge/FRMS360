"""The eight LNC signal algorithms.

Five (LNC-03..07) are financial ratios computed from two consecutive statements' own
figures - no external data needed, the same "computable from what we already have"
category ``features.py``'s ``COMPUTABLE`` set is for Lane A/B. Two (LNC-01, LNC-08) read
notes/auditor-report text rather than a structured figure, and are flagged
``evidence_basis="text-pattern"`` accordingly - a genuinely lower-confidence read, not
hidden as if it were a clean ratio. One (LNC-02) needs a project-appraisal baseline this
platform does not ingest yet and is always reported unmeasurable - never silently
skipped, never scored as if the borrower were clean.

**The severity band is one function, applied the same way after every signal accumulates
its own points.** The playbook this was designed from used two different vocabularies in
the same document (per-signal "critical/warning/pass", the aggregate scorer's
"critical/high/medium/low") that do not actually line up - copying that inconsistency
forward would silently misprice every signal's contribution to the aggregate score. One
banding function, one vocabulary, used everywhere.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Every signal accumulates a 0-100-ish severity score by its own rules, then this one
#: function turns it into the vocabulary the scorer (credit_health_scorer.py) deducts
#: points against. Same shape as ews_catalogue.py's comparator_for() - one small pure
#: function everything else reads, rather than each caller inventing its own cutoffs.
def band(severity: int) -> str:
    if severity >= 50:
        return "critical"
    if severity >= 30:
        return "high"
    if severity >= 15:
        return "medium"
    if severity >= 5:
        return "low"
    return "pass"


@dataclass
class SignalResult:
    signal_code: str
    observed_value: float | None
    baseline_value: float | None
    status: str  # pass | low | medium | high | critical | unmeasurable
    severity: int
    evidence: str
    evidence_basis: str = "ratio"


def _growth(current: int | None, prior: int | None) -> float | None:
    """(current - prior) / prior, or None if either side is missing or prior is zero -
    a growth rate against zero is undefined, not infinite or zero."""
    if current is None or prior is None or prior == 0:
        return None
    return (current - prior) / prior


def _unmeasurable(code: str, reason: str) -> SignalResult:
    return SignalResult(code, None, None, "unmeasurable", 0, reason)


# ------------------------------------------------------------------ LNC-03..07: ratios
def compute_inventory_movement(cur: dict[str, int], prior: dict[str, int] | None,
                                peer_median_growth: float | None = None) -> SignalResult:
    code = "LNC-03"
    if prior is None:
        return _unmeasurable(code, "no prior-period statement to compare against")
    inv_growth = _growth(cur.get("INVENTORY"), prior.get("INVENTORY"))
    rev_growth = _growth(cur.get("REVENUE"), prior.get("REVENUE"))
    if inv_growth is None or rev_growth is None:
        return _unmeasurable(code, "inventory or revenue not found in one of the two statements")

    severity = 0
    if rev_growth < 0 and inv_growth > 0.1:
        severity += 30
    if inv_growth > 0.5:
        severity += 25
    if peer_median_growth is not None and inv_growth > peer_median_growth + 0.2:
        severity += 15

    status = band(severity)
    evidence = (f"Inventory grew {inv_growth:.1%} while revenue changed {rev_growth:.1%}.")
    return SignalResult(code, inv_growth, rev_growth, status, severity, evidence)


def compute_receivables_movement(cur: dict[str, int], prior: dict[str, int] | None
                                  ) -> SignalResult:
    code = "LNC-04"
    if prior is None:
        return _unmeasurable(code, "no prior-period statement to compare against")
    ar_growth = _growth(cur.get("AR"), prior.get("AR"))
    rev_growth = _growth(cur.get("REVENUE"), prior.get("REVENUE"))
    if ar_growth is None or rev_growth is None:
        return _unmeasurable(code, "receivables or revenue not found in one of the two statements")

    rev_cur, rev_prior = cur.get("REVENUE"), prior.get("REVENUE")
    ar_cur = cur.get("AR")
    dso_cur = (ar_cur / rev_cur * 365) if ar_cur is not None and rev_cur else None
    ar_prior_v = prior.get("AR")
    dso_prior = (ar_prior_v / rev_prior * 365) if ar_prior_v is not None and rev_prior else None

    severity = 0
    if ar_growth > 0.3 and rev_growth < 0.1:
        severity += 35
    if dso_cur is not None and dso_prior is not None and dso_cur > dso_prior + 20:
        severity += 25
    if ar_cur is not None and rev_cur:
        if ar_cur / rev_cur > 0.4:
            severity += 15

    status = band(severity)
    dso_note = (f" DSO: {dso_prior:.0f}→{dso_cur:.0f} days."
                if dso_cur is not None and dso_prior is not None else "")
    evidence = f"AR grew {ar_growth:.1%} vs. revenue {rev_growth:.1%}.{dso_note}"
    return SignalResult(code, ar_growth, rev_growth, status, severity, evidence)


def compute_oca_change(cur: dict[str, int], prior: dict[str, int] | None) -> SignalResult:
    code = "LNC-05"
    if prior is None:
        return _unmeasurable(code, "no prior-period statement to compare against")
    oca_growth = _growth(cur.get("OCA"), prior.get("OCA"))
    if oca_growth is None:
        return _unmeasurable(code, "other-current-assets not found in one of the two statements")

    severity = 0
    if oca_growth > 1.0:
        severity += 30
    elif oca_growth > 0.5:
        severity += 15
    oca_cur, rev_cur = cur.get("OCA"), cur.get("REVENUE")
    if oca_cur is not None and rev_cur:
        if oca_cur / rev_cur > 0.15:
            severity += 15

    status = band(severity)
    evidence = f"Other current assets grew {oca_growth:.1%} year over year."
    return SignalResult(code, oca_growth, None, status, severity, evidence)


def compute_working_capital_bloat(cur: dict[str, int], prior: dict[str, int] | None
                                   ) -> SignalResult:
    code = "LNC-06"
    if prior is None:
        return _unmeasurable(code, "no prior-period statement to compare against")
    wc_growth = _growth(cur.get("WC_BORROWING"), prior.get("WC_BORROWING"))
    rev_growth = _growth(cur.get("REVENUE"), prior.get("REVENUE"))
    if wc_growth is None or rev_growth is None:
        return _unmeasurable(code, "WC borrowing or revenue not found in one of the two statements")
    ebitda_growth = _growth(cur.get("EBITDA"), prior.get("EBITDA"))

    wc_cur, rev_cur = cur.get("WC_BORROWING"), cur.get("REVENUE")
    wc_prior_v, rev_prior_v = prior.get("WC_BORROWING"), prior.get("REVENUE")
    wc_pct_cur = (wc_cur / rev_cur) if rev_cur else None
    wc_pct_prior = (wc_prior_v / rev_prior_v) if rev_prior_v else None

    severity = 0
    # EBITDA is often absent (not every filer reports it directly) - degrade this one
    # sub-check gracefully rather than making the whole signal unmeasurable for it.
    if wc_growth > 0.2 and rev_growth < 0.1 and (ebitda_growth is None or ebitda_growth < 0.05):
        severity += 40
    if wc_pct_cur is not None and wc_pct_prior is not None and wc_pct_cur > wc_pct_prior + 0.1:
        severity += 20
    if wc_pct_cur is not None and wc_pct_cur > 0.3:
        severity += 15

    status = band(severity)
    evidence = f"WC borrowing grew {wc_growth:.1%} while revenue grew {rev_growth:.1%}."
    return SignalResult(code, wc_growth, rev_growth, status, severity, evidence)


def compute_fixed_asset_funding(cur: dict[str, int], prior: dict[str, int] | None
                                 ) -> SignalResult:
    code = "LNC-07"
    if prior is None:
        return _unmeasurable(code, "no prior-period statement to compare against")
    fa_cur, fa_prior = cur.get("FIXED_ASSETS"), prior.get("FIXED_ASSETS")
    if fa_cur is None or fa_prior is None:
        return _unmeasurable(code, "fixed assets not found in one of the two statements")
    capex = fa_cur - fa_prior
    if capex <= 0:
        return SignalResult(code, 0.0, 0.0, "pass", 0,
                            "Fixed assets did not increase this period.")

    lt_debt_delta = (cur.get("LT_DEBT", 0) or 0) - (prior.get("LT_DEBT", 0) or 0)
    equity_delta = (cur.get("EQUITY", 0) or 0) - (prior.get("EQUITY", 0) or 0)
    funding_raised = lt_debt_delta + equity_delta
    funded_ratio = funding_raised / capex if capex else 1.0

    severity = 0
    if funded_ratio < 0.5:
        severity += 40
    elif funded_ratio < 0.9:
        severity += 20
    if funding_raised <= 0:
        severity += 15

    status = band(severity)
    evidence = (f"Capex of {capex/100:,.0f} rupees; only {funding_raised/100:,.0f} rupees "
                f"in new long-term debt/equity raised to fund it.")
    return SignalResult(code, funded_ratio, 1.0, status, severity, evidence)


def compute_borrowing_despite_cash(cur: dict[str, int], prior: dict[str, int] | None
                                    ) -> SignalResult:
    """RBI #31 - the extraction pipeline has parsed CASH since day one (gl_mapper.py),
    but until now nothing cross-checked it against WC_BORROWING. Two facts alone are
    each unremarkable - rising borrowing happens, and sitting on cash happens - the red
    flag is the two together: a borrower who doesn't need the extra debt taking it
    anyway, the classic shape of diversion or window-dressing."""
    code = "LNC-12"
    if prior is None:
        return _unmeasurable(code, "no prior-period statement to compare against")
    wc_growth = _growth(cur.get("WC_BORROWING"), prior.get("WC_BORROWING"))
    if wc_growth is None:
        return _unmeasurable(code, "WC borrowing not found in one of the two statements")
    cash_cur, rev_cur = cur.get("CASH"), cur.get("REVENUE")
    if cash_cur is None or not rev_cur:
        return _unmeasurable(code, "cash or revenue not found for this period")

    cash_to_rev = cash_cur / rev_cur
    severity = 0
    if wc_growth > 0.2:
        severity += 25
    if cash_to_rev > 0.15:
        severity += 25
    if wc_growth > 0.2 and cash_to_rev > 0.15:
        # Compounding, not just additive: this exact combination is the signal RBI
        # names, not two independent observations that happen to co-occur.
        severity += 15

    status = band(severity)
    evidence = (f"Working-capital borrowing grew {wc_growth:.1%} while cash on hand is "
                f"{cash_to_rev:.1%} of revenue.")
    return SignalResult(code, wc_growth, cash_to_rev, status, severity, evidence)


def compute_contingent_liabilities_high(cur: dict[str, int]) -> SignalResult:
    """RBI #34 - a level judgment, not a trend, so this is the one ratio signal here
    that never needs a prior period: "claims not acknowledged as debt are high" is
    answerable from a single filing, the same way LNC-01's text-pattern read is."""
    code = "LNC-13"
    cl, equity = cur.get("CONTINGENT_LIABILITIES"), cur.get("EQUITY")
    if cl is None or not equity:
        return _unmeasurable(code, "contingent liabilities or net worth not found in this statement")

    ratio = cl / equity
    severity = 0
    if ratio > 1.0:
        severity = 50
    elif ratio > 0.5:
        severity = 30
    elif ratio > 0.25:
        severity = 15

    status = band(severity)
    evidence = f"Contingent liabilities are {ratio:.1%} of net worth."
    return SignalResult(code, ratio, None, status, severity, evidence)


def compute_unbilled_revenue_growth(cur: dict[str, int], prior: dict[str, int] | None
                                     ) -> SignalResult:
    """RBI #35 - same shape as compute_inventory_movement (LNC-03): unbilled revenue
    growing while real revenue does not is the same "the top line is being propped up"
    pattern, just on a different balance-sheet line the extraction pipeline already
    parses (UNBILLED_REVENUE, gl_mapper.py) and nothing read until now."""
    code = "LNC-14"
    if prior is None:
        return _unmeasurable(code, "no prior-period statement to compare against")
    ub_growth = _growth(cur.get("UNBILLED_REVENUE"), prior.get("UNBILLED_REVENUE"))
    rev_growth = _growth(cur.get("REVENUE"), prior.get("REVENUE"))
    if ub_growth is None or rev_growth is None:
        return _unmeasurable(code, "unbilled revenue or revenue not found in one of the two statements")

    severity = 0
    if ub_growth > 0.5:
        severity += 30
    if ub_growth > 0.5 and rev_growth < 0.1:
        severity += 25
    ub_cur, rev_cur = cur.get("UNBILLED_REVENUE"), cur.get("REVENUE")
    if ub_cur is not None and rev_cur and ub_cur / rev_cur > 0.2:
        severity += 15

    status = band(severity)
    evidence = f"Unbilled revenue grew {ub_growth:.1%} while revenue grew {rev_growth:.1%}."
    return SignalResult(code, ub_growth, rev_growth, status, severity, evidence)


# ------------------------------------------------------------------ LNC-01, 08: text
_STATUTORY_DUES_RE = None  # compiled lazily below to keep imports light at module load


def compute_statutory_dues_default(notes_text: str) -> SignalResult:
    import re
    global _STATUTORY_DUES_RE
    if _STATUTORY_DUES_RE is None:
        _STATUTORY_DUES_RE = re.compile(
            r"(unpaid|outstanding|overdue)\s+statutory\s+dues|"
            r"income\s+tax\s+(demand|dues)\s+(under\s+dispute|payable)|"
            r"(dues|demand)\s+to\s+(the\s+)?(income\s+tax|gst|statutory)\s+(department|"
            r"authorit(y|ies))", re.IGNORECASE)
    code = "LNC-01"
    if not notes_text:
        return _unmeasurable(code, "no notes/auditor-report text available")
    m = _STATUTORY_DUES_RE.search(notes_text)
    if not m:
        return SignalResult(code, 0.0, None, "pass", 0,
                            "No statutory-dues default disclosed in notes.",
                            evidence_basis="text-pattern")
    excerpt = notes_text[max(0, m.start() - 40):m.end() + 80].strip()
    return SignalResult(code, 1.0, None, "high", 35,
                        f"Statutory-dues default language found in notes: …{excerpt}…",
                        evidence_basis="text-pattern")


def compute_accounting_change(cur_notes: str, prior_notes: str | None,
                              cur_reporting_month: int, prior_reporting_month: int | None
                              ) -> SignalResult:
    import re
    code = "LNC-08"
    severity = 0
    reasons = []

    if prior_reporting_month is not None and cur_reporting_month != prior_reporting_month:
        severity += 35
        reasons.append(
            f"fiscal year-end month changed ({prior_reporting_month}→{cur_reporting_month})")

    dep_re = re.compile(r"(straight[\s-]line|written[\s-]down\s+value|\bWDV\b|\bSLM\b)",
                        re.IGNORECASE)
    if prior_notes:
        cur_methods = {m.lower() for m in dep_re.findall(cur_notes or "")}
        prior_methods = {m.lower() for m in dep_re.findall(prior_notes)}
        if cur_methods and prior_methods and cur_methods != prior_methods:
            severity += 30
            reasons.append("depreciation method language changed between filings")

    if not reasons:
        return SignalResult(code, 0.0, None, "pass", 0,
                            "No accounting-policy or period change detected.",
                            evidence_basis="structured+notes")
    status = band(severity)
    return SignalResult(code, 1.0, None, status, severity, "; ".join(reasons).capitalize(),
                        evidence_basis="structured+notes")


def compute_all(cur: dict[str, int], prior: dict[str, int] | None,
                cur_notes: str = "", prior_notes: str | None = None,
                cur_reporting_month: int = 0, prior_reporting_month: int | None = None,
                peer_median_inventory_growth: float | None = None,
                company_identifier: str | None = None, roc_entry: dict | None = None,
                rating_entry: dict | None = None, prior_rank: float | None = None,
                godown_entry: dict | None = None, bills_entry: dict | None = None,
                insurance_entry: dict | None = None, stock_audit_entry: dict | None = None,
                shareholding_entry: dict | None = None, prior_promoter_pct: float | None = None,
                enforcement_entry: dict | None = None, enforcement_feed_loaded: bool = False,
                invoice_entry: dict | None = None, management_change_entry: dict | None = None,
                project_baseline: dict | None = None, project_progress: dict | None = None,
                ) -> dict[str, SignalResult]:
    """Every LNC signal for one borrower-quarter. ``prior`` is the immediately preceding
    filed statement's metrics, or None for a borrower's first-ever submission. Every
    ``*_entry`` parameter is the caller's pre-fetched lookup (runner.py's job, via
    reference_fetch.active_entry()/is_loaded() or a direct ManualFinding read) - this
    function stays pure and does no I/O of its own, the same split every other signal
    here already follows for prior_metrics/prior_notes."""
    # Imported here, not at module top, so a compute_all() caller that never enables the
    # LLM signal pays no import cost for httpx - signal_engine.py stays the pure,
    # dependency-light module every other signal function already keeps it as.
    from .enforcement_check import compute_enforcement_action_check
    from .insurance_check import compute_insurance_coverage_check
    from .management_change_check import compute_management_change_check
    from .manual_findings import (
        compute_bill_verification_check, compute_godown_inspection_check,
        compute_invoice_compliance_check,
    )
    from .project_appraisal_check import compute_cost_variance, compute_scope_creep
    from .qualitative_red_flags import compute_qualitative_red_flags
    from .rating_check import compute_rating_check
    from .roc_mca_check import compute_roc_mca_check
    from .shareholding_check import compute_shareholding_check
    from .stock_audit_check import compute_stock_audit_check

    results = [
        compute_statutory_dues_default(cur_notes),
        compute_scope_creep(project_baseline, project_progress),
        compute_inventory_movement(cur, prior, peer_median_inventory_growth),
        compute_receivables_movement(cur, prior),
        compute_oca_change(cur, prior),
        compute_working_capital_bloat(cur, prior),
        compute_fixed_asset_funding(cur, prior),
        compute_accounting_change(cur_notes, prior_notes, cur_reporting_month,
                                  prior_reporting_month),
        compute_qualitative_red_flags(cur_notes),
        compute_roc_mca_check(company_identifier, roc_entry),
        compute_rating_check(company_identifier, rating_entry, prior_rank),
        compute_borrowing_despite_cash(cur, prior),
        compute_contingent_liabilities_high(cur),
        compute_unbilled_revenue_growth(cur, prior),
        compute_godown_inspection_check(godown_entry),
        compute_bill_verification_check(bills_entry),
        compute_insurance_coverage_check(company_identifier, insurance_entry, cur.get("INVENTORY")),
        compute_stock_audit_check(company_identifier, stock_audit_entry),
        compute_shareholding_check(company_identifier, shareholding_entry, prior_promoter_pct),
        compute_enforcement_action_check(company_identifier, enforcement_entry, enforcement_feed_loaded),
        compute_invoice_compliance_check(invoice_entry),
        compute_management_change_check(company_identifier, management_change_entry),
        compute_cost_variance(project_baseline, project_progress),
    ]
    return {r.signal_code: r for r in results}
