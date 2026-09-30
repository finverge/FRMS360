"""The Lane C signal catalogue - LNC-01 through LNC-08.

Deliberately not part of ``ews_catalogue.py``. That catalogue's shape - a Tazama band,
``observed >= threshold``, scored per payment transaction and summed by family - fits
Lane A/B because every one of their indicators answers a yes/no question about a single
transaction. Lane C answers a different question over a much longer window: "is this
borrower's financial condition deteriorating, on a 0-100 scale, across a whole quarter's
statement." Forcing that into the transaction-scoring shape would mean either fabricating
a fake "transaction" to hang the score on, or bending the band abstraction until it no
longer means what it means everywhere else it is used. A second, smaller catalogue in the
same declarative spirit - one place a threshold lives, read by the engine rather than
hard-coded into it - is the honest fit.

Covers the design doc's "Financial Statement & Accounting Indicators" (#1-8) - the ones
computable from a borrower's own filed numbers. The remaining seven ("Borrower Conduct &
Compliance", #9-15) need inputs Lane C does not ingest at all yet (inspection logs,
document-request tracking) and are out of scope for this catalogue - the same
"computable vs. needs a feed" honesty NEEDS_EXTERNAL_DATA already practices for Lane A/B.
"""
from __future__ import annotations

#: signal_code -> definition. Mirrors ews_catalogue.py's shape closely enough to read at
#: a glance, without inheriting a threshold structure built for a different problem.
_SIGNALS = [
    ("LNC-01", "Statutory dues default",
     "Unpaid statutory dues (tax, government agency) disclosed as a contingent "
     "liability", "text-pattern", 1, "notes"),
    ("LNC-02", "Project scope creep",
     "Project-finance cost/timeline expansion beyond the appraised baseline",
     "ratio", 0.20, "structured"),
    ("LNC-03", "Inventory movement vs. turnover",
     "Inventory growing while revenue is flat or falling",
     "ratio", 50, "structured"),
    ("LNC-04", "Receivables movement vs. turnover",
     "Receivables (and days-sales-outstanding) growing faster than revenue",
     "ratio", 60, "structured"),
    ("LNC-05", "Other current assets, disproportionate change",
     "A sharp rise in other current assets - often related-party advances in "
     "disguise", "ratio", 60, "structured"),
    ("LNC-06", "Working-capital borrowing rising vs. turnover",
     "Short-term borrowing growing faster than revenue - cash-flow stress",
     "ratio", 60, "structured"),
    ("LNC-07", "Fixed assets growing without long-term funding",
     "Capex not matched by new long-term debt or equity - diverted cash or "
     "capitalised opex", "ratio", 55, "structured"),
    ("LNC-08", "Accounting policy or period change",
     "A change in fiscal year-end, depreciation method, or other accounting policy "
     "between consecutive filings", "text-pattern", 1, "structured+notes"),
]

#: Signals read from unstructured notes/auditor-report text rather than a clean
#: structured figure. Lower confidence by construction - flagged on the alert
#: (``evidence_basis``) so a credit analyst knows to verify before acting, not treated
#: as equivalent to a ratio computed from two balance-sheet numbers.
TEXT_PATTERN_SIGNALS = {"LNC-01"}

#: LNC-02 (scope creep) needs a stored project-appraisal baseline that Lane C does not
#: yet ingest for any borrower. Reported unmeasurable until that feed exists - the same
#: honest gap CPT-03 is in without a loaded CERSAI registry - never silently skipped and
#: never scored as clean.
NEEDS_PROJECT_BASELINE = {"LNC-02"}

ALL_SIGNAL_IDS = [s[0] for s in _SIGNALS]


def signal_definitions() -> dict[str, dict]:
    """signal_code -> {name, description, evidence_basis, severity_weight, data_source}."""
    return {
        code: {
            "signal_code": code, "name": name, "description": desc,
            "evidence_basis": basis, "severity_weight": weight, "data_source": source,
        }
        for code, name, desc, basis, weight, source in _SIGNALS
    }
