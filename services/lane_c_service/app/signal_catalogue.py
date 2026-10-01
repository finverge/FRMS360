"""The Lane C signal catalogue - LNC-01 through LNC-23.

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

Started as the design doc's "Financial Statement & Accounting Indicators" (#1-8) - the
ones computable from a borrower's own filed numbers. LNC-09 (a local-LLM read for #38/#39,
off by default), LNC-10/LNC-11 (a reference-feed check for #18, plus a rating-downgrade
signal beyond RBI's own list), LNC-12/13/14 (three more ratios against figures the
extraction pipeline already parsed but nothing read until now, closing #31/#34/#35),
LNC-15/16 (manual-entry findings for #12/#25 - a physical inspection fact no document
or feed can carry), LNC-17/18/19/20 (four more reference-feed checks - insurance
coverage #7, a stock-audit report #17, promoter shareholding #41, and enforcement
action #40 - reusing the same mechanism LNC-10/11 proved rather than a fifth one-off),
LNC-21 (invoice compliance #8 - the same manual-entry mechanism LNC-15/16 proved,
reused a third time), LNC-23 (management/KMP changes #42 - a sixth reuse of the
reference-feed mechanism), and LNC-02/LNC-22 (project scope creep #3 and cost variance
#33, both newly measurable now that project_appraisals/project_progress - models.py -
give Lane C an actual baseline to compare against; LNC-02 existed in this catalogue
since the start but, until this pair of tables, always reported unmeasurable) have
since joined - the last of RBI's 42 illustrative signals this platform can reach without
a genuinely new data category (see docs/rbi_ews_mapping.py's own notes on #9/#16/#36/#37,
which stay partial by design, not by gap). See that file for exactly which signal each
of these closes.
"""
from __future__ import annotations

#: signal_code -> definition. Mirrors ews_catalogue.py's shape closely enough to read at
#: a glance, without inheriting a threshold structure built for a different problem.
_SIGNALS = [
    ("LNC-01", "Statutory dues default",
     "Unpaid statutory dues (tax, government agency) disclosed as a contingent "
     "liability", "text-pattern", 1, "notes"),
    ("LNC-02", "Project scope creep",
     "Project-finance timeline slipping beyond the sanctioned completion date - needs "
     "a project-appraisal baseline and a period's progress submission to be on file; "
     "unmeasurable until both are", "ratio", 35, "project appraisal"),
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
    ("LNC-09", "Qualitative red flags",
     "Material discrepancies within the statement, or poor disclosure of adverse "
     "information with no auditor qualification - read from the statement's own text "
     "by a local LLM, off by default (settings.lane_c_llm_enabled)",
     "llm-qualitative", 1, "notes"),
    ("LNC-10", "Undisclosed ROC/MCA liability",
     "A liability in the borrower's Registrar-of-Companies search report that its own "
     "financial statement does not disclose - needs an mca_roc reference feed to be "
     "configured; unmeasurable until one is", "mca_roc feed", 50, "mca_roc feed"),
    ("LNC-11", "Negative rating action",
     "A rating downgrade, or outlook turned negative, since the borrower's last Lane C "
     "review - not an RBI 2016 signal, added value beyond the illustrative list. Needs "
     "a rating_action reference feed to be configured; unmeasurable until one is",
     "rating feed", 55, "rating_action feed"),
    ("LNC-12", "Borrowings rising despite large cash balances",
     "Working-capital borrowing growing while cash on hand is already large relative "
     "to revenue - a borrower taking on debt it does not appear to need",
     "ratio", 65, "structured"),
    ("LNC-13", "Contingent liabilities high relative to net worth",
     "Claims not acknowledged as debt, sized against the borrower's own equity - a "
     "level judgment from a single filing, not a period-over-period trend",
     "ratio", 50, "structured"),
    ("LNC-14", "Unbilled revenue growing faster than turnover",
     "Unbilled revenue rising while real revenue does not - revenue recognised ahead "
     "of being earned or collected", "ratio", 55, "structured"),
    ("LNC-15", "Godown inspection postponed for flimsy reasons",
     "A physical, qualitative fact no document or feed can carry - a credit/inspection "
     "officer records this directly; unmeasurable until one does", "manual", 45,
     "manual"),
    ("LNC-16", "Original bills not produced for verification",
     "Same manual-entry shape as LNC-15 - a credit officer's own finding, not something "
     "inferred from a statement or feed", "manual", 45, "manual"),
    ("LNC-17", "Inventory under- or over-insured",
     "Insured value of stock, cross-checked against the borrower's own extracted "
     "inventory figure - needs an insurance_coverage reference feed to be configured; "
     "unmeasurable until one is", "ratio", 35, "insurance_coverage feed"),
    ("LNC-18", "Critical stock audit finding",
     "A third-party stock audit report flags a critical issue - needs a stock_audit "
     "reference feed to be configured; unmeasurable until one is", "stock feed", 45,
     "stock_audit feed"),
    ("LNC-19", "Promoter stake reduction or share encumbrance",
     "Encumbered promoter shares above half the holding, or a meaningful drop in "
     "promoter stake since the last review - needs a shareholding reference feed to be "
     "configured; unmeasurable until one is", "shareholding", 30, "shareholding feed"),
    ("LNC-20", "Regulatory enforcement action",
     "A raid or enforcement action by Income Tax/GST/TDS/central excise authorities - "
     "needs an enforcement_action reference feed to be configured; unmeasurable until "
     "one is", "enforcement", 50, "enforcement_action feed"),
    ("LNC-21", "Invoices devoid of TAN or other particulars",
     "Same manual-entry shape as LNC-15/16 - a credit officer's own sampled-invoice "
     "finding, not something inferable from a filed statement", "manual", 45, "manual"),
    ("LNC-22", "Project cost variance",
     "Cost incurred against a project-finance loan running wide of the sanctioned "
     "appraised cost - needs a project-appraisal baseline and a period's progress "
     "submission to be on file; unmeasurable until both are", "ratio", 30,
     "project appraisal"),
    ("LNC-23", "Frequent change in management / KMP resignation",
     "Director/KMP changes from MCA's own filing history - needs a management_changes "
     "reference feed to be configured; unmeasurable until one is", "mca_roc feed", 40,
     "management_changes feed"),
]

#: Signals read from unstructured notes/auditor-report text rather than a clean
#: structured figure. Lower confidence by construction - flagged on the alert
#: (``evidence_basis``) so a credit analyst knows to verify before acting, not treated
#: as equivalent to a ratio computed from two balance-sheet numbers.
TEXT_PATTERN_SIGNALS = {"LNC-01"}

#: Physical/qualitative facts no document or feed can carry automatically - a human
#: records these directly via POST /lane-c/{tenant_id}/borrowers/{account}/manual-finding
#: (routes/lane_c.py). Unmeasurable until someone does, the same honesty every other
#: unconfigured Lane C signal already practices.
MANUAL_SIGNALS = {"LNC-15", "LNC-16", "LNC-21"}

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
