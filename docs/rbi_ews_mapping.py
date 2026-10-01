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

**Lane C, a second and separate catalogue, now answers twenty-three of these.** Rows
marked ``LNC-xx`` below are read from a borrower's own filed financial statements on a
quarterly review cadence, not from a transaction — see ``services/lane_c_service``.
LNC-10, LNC-17, LNC-18, LNC-19, LNC-20 and LNC-23 are built but unmeasurable until a
tenant configures the matching reference feed — the engine, not the vendor contract, is
the gap left. LNC-15, LNC-16 and LNC-21 are built but unmeasurable until a credit
officer actually records a finding for the period — a human-input gap, not a feed one.
LNC-02 and LNC-22 are built but unmeasurable until a borrower has both a
project-appraisal baseline and a period's progress submission on file — the same
honest gap, fed by a direct submission (``routes/lane_c.py``) rather than a feed or a
human checkbox. LNC-02 is marked ``partial`` here regardless of whether it is
measurable for a given borrower, because even when it is, it checks a single period's
slip against the original baseline, not the repeated-revision count RBI's own word
"frequent" asks for.

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
     "partial:LNC-02", "Lane C - a project-finance timeline slip against the "
     "sanctioned appraisal baseline; needs project_appraisals/project_progress on "
     "file for this borrower (routes/lane_c.py), unmeasurable until both are "
     "submitted. Partial because it is a level check against the original baseline "
     "from a single period, not yet the count of repeated revisions across periods "
     "RBI's own word 'frequent' asks for"),
    (4, "Foreign bills outstanding with the bank for a long time / overdue",
     "TBM-03", "days_outstanding, threshold 270"),
    (5, "Delay in payment of outstanding dues",
     "CBS-08", "delayed_repayment_count, threshold 2 - closed via an optional due_date "
     "on the existing loan_repayment CBS event; unmeasurable for a tenant whose CBS "
     "extract does not send it"),
    (6, "Frequent invocation of BGs and devolvement of LCs",
     "CBS-06", "bg_lc_event_count, threshold 2 - closed via the CBS bg_lc_event event kind"),
    (7, "Under-insured or over-insured inventory",
     "LNC-17", "Lane C - needs an insurance_coverage reference feed configured for the "
     "tenant; unmeasurable until one is - the engine is built, no vendor is wired up yet"),
    (8, "Invoices devoid of TAN and other details",
     "LNC-21", "Lane C - manual entry by a credit officer sampling invoices, same "
     "declared-but-not-auto-scored shape as LNC-15/16; unmeasurable until someone "
     "records a finding for the period (also blocks SME-03, a separate transaction-"
     "level indicator this does not close)"),
    (9, "Dispute on title of collateral securities",
     "partial:CPT-03", "CPT-03 detects multiple charges, not a title dispute"),
    (10, "Funds from other banks to liquidate the outstanding loan",
     "BEH-02", "external_funding_pct, threshold 0.6"),
    (11, "In merchanting trade, import leg not revealed to the bank",
     "TBM-01", "undisclosed_legs"),
    (12, "Request to postpone godown inspection for flimsy reasons",
     "LNC-15", "Lane C - manual entry by a credit/inspection officer, same "
     "declared-but-not-auto-scored shape Lane B's QUAL-01/02/03 use; unmeasurable "
     "until someone records a finding for the period"),
    (13, "Funding of interest by sanctioning additional facilities",
     "CBS-07", "interest_funding_count, threshold 1 - closed via the CBS "
     "facility_sanction event kind, flagged funds_interest"),
    (14, "Exclusive collateral charged to several lenders without NOC",
     "CPT-03", "lender_count, threshold 2 — needs the CERSAI feed"),
    (15, "Concealment of vital documents (master agreement, insurance)",
     "QUAL-03", "concealment_findings — in the catalogue, manual input"),
    (16, "Floating front / associate companies by investing borrowed money",
     "partial:CBS-01,CBS-03", "Diversion and group exposure are proxies, not the signal"),
    (17, "Critical issues highlighted in the stock audit report",
     "LNC-18", "Lane C - needs a stock_audit reference feed configured for the tenant; "
     "unmeasurable until one is - the engine is built, no vendor is wired up yet"),
    (18, "Liabilities in the ROC search report not reported by the borrower",
     "LNC-10", "Lane C - needs an mca_roc reference feed configured for the tenant "
     "(services/lane_c_service/app/reference_fetch.py); unmeasurable until one is - "
     "the engine is built, no vendor is wired up yet"),
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
     "LNC-16", "Lane C - same manual-entry shape as LNC-15"),
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
     "LNC-12", "Lane C - ratio, WC borrowing growth vs. cash-to-revenue"),
    (32, "Frequent change in accounting period or accounting policies",
     "LNC-08", "Lane C - fiscal year-end and depreciation-method language, "
     "compared across filings"),
    (33, "Project cost at wide variance with the standard cost",
     "LNC-22", "Lane C - cost incurred so far vs. the sanctioned appraisal baseline; "
     "needs project_appraisals/project_progress on file for this borrower "
     "(routes/lane_c.py), unmeasurable until both are submitted"),
    (34, "Claims not acknowledged as debt are high",
     "LNC-13", "Lane C - contingent liabilities vs. net worth, a level check from a "
     "single filing"),
    (35, "Substantial increase in unbilled revenue year after year",
     "LNC-14", "Lane C - ratio, unbilled-revenue growth vs. turnover"),
    (36, "Many transactions with inter-connected companies, large outstandings",
     "CBS-03", "group_exposure_pct, threshold 0.35"),
    (37, "Substantial related party transactions",
     "partial:CBS-03", "Group exposure is a proxy; related-party status is not held"),
    (38, "Material discrepancies / inconsistencies within the annual report",
     "partial:LNC-09", "Lane C - local-LLM read of the statement text, off by default "
     "(settings.lane_c_llm_enabled); a probabilistic read, never counted as a full "
     "cover the way a ratio or regex match is"),
    (39, "Poor disclosure of adverse information, no auditor qualification",
     "partial:LNC-09", "Lane C - same LLM read as #38, off by default; a finding is "
     "kept only if its quoted source sentence verifies against the statement text"),
    (40, "Raid by Income Tax / GST / TDS / central excise officials",
     "LNC-20", "Lane C - needs an enforcement_action reference feed configured for the "
     "tenant; unmeasurable until one is - the engine is built, no vendor is wired up yet"),
    (41, "Reduction in promoter stake or increase in encumbered shares",
     "LNC-19", "Lane C - needs a shareholding reference feed configured for the tenant; "
     "unmeasurable until one is - the engine is built, no vendor is wired up yet"),
    (42, "Resignation of key personnel, frequent management change",
     "LNC-23", "Lane C - needs a management_changes reference feed configured for the "
     "tenant (MCA's own DIR-12/KMP filing history); unmeasurable until one is"),
]

#: What the platform declares today, for the reverse view.
DECLARED = [
    "VEL-01", "VEL-02", "VEL-03", "SME-01", "SME-02", "SME-03",
    "BEH-01", "BEH-02", "BEH-03", "LAY-01", "LAY-02", "LAY-03", "LAY-04",
    "CPT-01", "CPT-02", "CPT-03", "CHN-01", "CHN-02", "CHN-03",
    "TBM-01", "TBM-02", "TBM-03", "CBS-01", "CBS-02", "CBS-03", "CBS-04", "CBS-05",
    "CBS-06", "CBS-07", "CBS-08",
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
#: than blurring the two catalogues together. LNC-02 computes a real result now that
#: project_appraisals/project_progress exist; it is unmeasurable only until a borrower
#: has both submitted, the same honest gap every other reference-feed/manual signal here
#: already has - not permanently unmeasurable the way it used to be (see MAP row 3).
LANE_C_DECLARED = ["LNC-01", "LNC-02", "LNC-03", "LNC-04", "LNC-05", "LNC-06", "LNC-07",
                   "LNC-08", "LNC-09", "LNC-10", "LNC-11", "LNC-12", "LNC-13", "LNC-14",
                   "LNC-15", "LNC-16", "LNC-17", "LNC-18", "LNC-19", "LNC-20", "LNC-21",
                   "LNC-22", "LNC-23"]

#: LNC-11 (negative rating action) answers a question RBI's 2016 illustrative list never
#: asks - it is genuine added capability, not a checklist item, and should never be
#: counted toward "N of 42" the way LNC-01 through LNC-10 legitimately are.
LANE_C_BEYOND_RBI_SCOPE = {"LNC-11"}


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
