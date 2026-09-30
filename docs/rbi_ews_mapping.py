"""RBI's illustrative Early Warning Signals, mapped to the Fraud360 catalogue.

**Source, and its status — read this before quoting any number here.**

The 42-signal list below is annexed to the RBI *Master Directions on Frauds —
Classification and Reporting by commercial banks and select FIs* (RBI/DBS/2016-17/28,
DBS.CO.CFMC.BC.No.1/23.04.001/2016-17, 1 July 2016). Secondary sources describe it as 45
signals; the enumerations differ in how a few compound items are split.

**Those Directions are superseded.** The *Master Directions on Fraud Risk Management in
Commercial Banks (including RRBs) and AIFIs* (RBI/DOS/2024-25/118, 15 July 2024) state
expressly that they supersede the 2016 Directions — and, checked against the current text,
**the 2024 Directions do not enumerate an EWS list at all**. Chapter III requires an EWS
framework whose indicators "may illustratively capture" transactional data *and financial
performance*, leaving the set to the regulated entity.

Three consequences, and they cut in different directions:

* There is **no official checklist to be scored against**, so "we cover N of 45" is not a
  compliance statement and should never be presented as one. The 2016 list remains a
  useful sanity check on breadth because banks still recognise it — nothing more.
* The 2024 text naming **financial performance** alongside transactional data means the
  gap this file measures is real on its own terms, not merely an artefact of an old
  annexure. Roughly two thirds of the uncovered signals need financial statements or the
  annual report.
* Two facts from the 2024 Directions matter more than the list itself: the EWS/RFA
  framework attaches at the **CRILC reporting threshold of ₹3 crore** of aggregate
  fund- and non-fund-based exposure — far lower than the ₹500 million that applied under
  the older consortium framework — and alerts carry a prescribed turnaround of **not more
  than 30 days for examination**. The platform has no such clock today; ``stalled_case_days``
  is a different measure.

**Why this matters.** The catalogue was built around payment-transaction typologies:
velocity, structuring, layering, channel and device. RBI's illustrative list is about
something else — a *corporate borrower* whose financial statements, documents and conduct
are drifting. The two overlap far less than the shared phrase "early warning signals"
suggests, and a bank scoring an RFP line by line against the RBI annexure will find that
out before we do.

**Lane C, a second and separate catalogue, now answers eight of these.** Rows marked
``LNC-xx`` below are read from a borrower's own filed financial statements on a quarterly
review cadence, not from a transaction — see ``services/lane_c_service``. LNC-02 is built
but always reports unmeasurable (a project-appraisal baseline this platform does not
ingest), so it is marked ``partial`` here rather than counted as covered.

Running this file prints the coverage table and the gap analysis.
"""
from __future__ import annotations

# (rbi_no, signal, mapped_indicator_ids, note)
#   ""      = no indicator covers this
#   partial = an indicator touches it but does not measure the stated signal
MAP: list[tuple[int, str, str, str]] = [
    (1, "Default in undisputed payment to statutory bodies per the annual report",
     "LNC-01", "Lane C - text-pattern read of the notes/auditor-report text"),
    (2, "Bouncing of high value cheques",
     "CBS-04", "cheque_return_count, threshold 3 - closed via the CBS cheque_return "
     "event kind"),
    (3, "Frequent change in the scope of the project",
     "partial:LNC-02", "Lane C - built, but always reports unmeasurable: needs a "
     "project-appraisal baseline this platform does not ingest yet"),
    (4, "Foreign bills outstanding with the bank for a long time / overdue",
     "TBM-03", "days_outstanding, threshold 270"),
    (5, "Delay in payment of outstanding dues",
     "", "Partially inferable from CBS repayment events; not an indicator today"),
    (6, "Frequent invocation of BGs and devolvement of LCs",
     "", "Needs trade-finance / limits data"),
    (7, "Under-insured or over-insured inventory",
     "", "Needs insurance and stock records"),
    (8, "Invoices devoid of TAN and other details",
     "", "Needs invoice-level data (also blocks SME-03)"),
    (9, "Dispute on title of collateral securities",
     "partial:CPT-03", "CPT-03 detects multiple charges, not a title dispute"),
    (10, "Funds from other banks to liquidate the outstanding loan",
     "BEH-02", "external_funding_pct, threshold 0.6"),
    (11, "In merchanting trade, import leg not revealed to the bank",
     "TBM-01", "undisclosed_legs"),
    (12, "Request to postpone godown inspection for flimsy reasons",
     "", "Qualitative, from credit monitoring"),
    (13, "Funding of interest by sanctioning additional facilities",
     "", "Needs limit-sanction history"),
    (14, "Exclusive collateral charged to several lenders without NOC",
     "CPT-03", "lender_count, threshold 2 — needs the CERSAI feed"),
    (15, "Concealment of vital documents (master agreement, insurance)",
     "QUAL-03", "concealment_findings — in the catalogue, manual input"),
    (16, "Floating front / associate companies by investing borrowed money",
     "partial:CBS-01,CBS-03", "Diversion and group exposure are proxies, not the signal"),
    (17, "Critical issues highlighted in the stock audit report",
     "", "Needs stock-audit findings"),
    (18, "Liabilities in the ROC search report not reported by the borrower",
     "", "Needs an ROC / MCA feed"),
    (19, "Frequent requests for general purpose loans",
     "QUAL-02", "adhoc_requests"),
    (20, "Frequent ad hoc sanctions",
     "QUAL-02", "Same indicator as 19 — the two are not separated today"),
    (21, "Sales proceeds not routed through the consortium / lender",
     "BEH-03", "unrouted_pct, threshold 0.5"),
    (22, "LCs for local trade / related party without underlying trade",
     "TBM-02", "related_party_flag"),
    (23, "High value RTGS payment to unrelated parties",
     "CBS-01", "diverted_pct — closest match; VEL-02 covers the value dimension only"),
    (24, "Heavy cash withdrawal in loan accounts",
     "CBS-02", "cash_ratio, threshold 0.3"),
    (25, "Non-production of original bills for verification",
     "", "Qualitative, from credit monitoring"),
    (26, "Inventory movements disproportionate to turnover",
     "LNC-03", "Lane C - ratio vs. the prior filed period"),
    (27, "Receivables movements disproportionate to turnover / ageing",
     "LNC-04", "Lane C - ratio, incl. days-sales-outstanding"),
    (28, "Disproportionate change in other current assets",
     "LNC-05", "Lane C - ratio"),
    (29, "Working capital borrowing rising as a percentage of turnover",
     "LNC-06", "Lane C - ratio, degrades gracefully when EBITDA is not reported"),
    (30, "Fixed assets increasing without corresponding long-term sources",
     "LNC-07", "Lane C - capex vs. new long-term debt/equity raised"),
    (31, "Borrowings increasing despite large cash balances",
     "", "Lane C parses a CASH figure but no signal cross-checks it against "
     "borrowing yet"),
    (32, "Frequent change in accounting period or accounting policies",
     "LNC-08", "Lane C - fiscal year-end and depreciation-method language, "
     "compared across filings"),
    (33, "Project cost at wide variance with the standard cost",
     "", "Needs project appraisal data"),
    (34, "Claims not acknowledged as debt are high",
     "", "Lane C parses a contingent-liabilities figure but no signal reads it"),
    (35, "Substantial increase in unbilled revenue year after year",
     "", "Lane C parses an unbilled-revenue figure but no signal reads it"),
    (36, "Many transactions with inter-connected companies, large outstandings",
     "CBS-03", "group_exposure_pct, threshold 0.35"),
    (37, "Substantial related party transactions",
     "partial:CBS-03", "Group exposure is a proxy; related-party status is not held"),
    (38, "Material discrepancies / inconsistencies within the annual report",
     "", "Needs annual report"),
    (39, "Poor disclosure of adverse information, no auditor qualification",
     "", "Needs auditor's report"),
    (40, "Raid by Income Tax / GST / TDS / central excise officials",
     "", "Needs an external event feed"),
    (41, "Reduction in promoter stake or increase in encumbered shares",
     "", "Needs shareholding / depository data"),
    (42, "Resignation of key personnel, frequent management change",
     "", "Needs MCA / management data"),
]

#: What the platform declares today, for the reverse view.
DECLARED = [
    "VEL-01", "VEL-02", "VEL-03", "SME-01", "SME-02", "SME-03",
    "BEH-01", "BEH-02", "BEH-03", "LAY-01", "LAY-02", "LAY-03", "LAY-04",
    "CPT-01", "CPT-02", "CPT-03", "CHN-01", "CHN-02", "CHN-03",
    "TBM-01", "TBM-02", "TBM-03", "CBS-01", "CBS-02", "CBS-03", "CBS-04", "CBS-05",
    # BR-214, added after this file's own 2016-list cross-check confirmed none of the 42
    # signals below are about the pre-sanction / application stage - they are corporate-
    # borrower monitoring signals, and a falsified application or a straw borrower
    # predates any of them. Genuinely a different question, same as the payment-fraud
    # typologies above - not a gap in this mapping, an absence in the source list.
    "LOS-01", "LOS-02", "LOS-03",
]
CATALOGUE_ONLY = ["QUAL-01", "QUAL-02", "QUAL-03"]

#: Lane C's own catalogue (services/lane_c_service/app/signal_catalogue.py) - a periodic
#: credit-file read, not a payment-transaction rule, so it is kept out of DECLARED rather
#: than blurring the two catalogues together. LNC-02 is built but always reports
#: unmeasurable (see MAP row 3); the other seven compute a real result.
LANE_C_DECLARED = ["LNC-01", "LNC-02", "LNC-03", "LNC-04", "LNC-05", "LNC-06", "LNC-07",
                   "LNC-08"]


def _ids(cell: str) -> list[str]:
    if not cell:
        return []
    return cell.replace("partial:", "").split(",")


def report() -> None:
    full = [r for r in MAP if r[2] and not r[2].startswith("partial:")]
    part = [r for r in MAP if r[2].startswith("partial:")]
    none = [r for r in MAP if not r[2]]

    print(f"RBI illustrative EWS signals: {len(MAP)}")
    print(f"  covered by an indicator   : {len(full)}")
    print(f"  partially / by proxy      : {len(part)}")
    print(f"  not covered               : {len(none)}")
    print()

    print("COVERED")
    for n, sig, ids, note in full:
        print(f"  {n:>2}. {sig[:64]:<64} -> {ids:<16} {note[:44]}")
    print("\nPARTIAL")
    for n, sig, ids, note in part:
        print(f"  {n:>2}. {sig[:64]:<64} -> {ids:<16} {note[:44]}")

    # What blocks the uncovered ones, grouped - this is the buy-vs-build question.
    print("\nNOT COVERED, grouped by the feed that would unlock them")
    buckets: dict[str, list[int]] = {}
    for n, sig, _ids_, note in none:
        buckets.setdefault(note, []).append(n)
    for note, nums in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(nums):>2} signal(s)  {note:<52} {nums}")

    # Reverse view: our indicators that answer nothing on RBI's list.
    used = {i for _, _, cell, _ in MAP for i in _ids(cell)}
    unused = [i for i in DECLARED if i not in used]
    print(f"\nDeclared indicators with no RBI-list counterpart: {len(unused)} of "
          f"{len(DECLARED)}")
    print(f"  {', '.join(unused)}")
    print("  These are payment-fraud typologies. They are not wasted - they are simply\n"
          "  answering a different question from the one this annexure asks.")


if __name__ == "__main__":
    report()
