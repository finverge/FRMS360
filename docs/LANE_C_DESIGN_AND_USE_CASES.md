# Lane C: Periodic Borrower Assessment
## Roadmap Architecture & Use Cases

**Status:** Pending — Roadmap (unbuilt)  
**Target Launch:** TBD  
**RBI Compliance:** Covers 15 of 42 RBI 2016 Illustrative Early-Warning Signals

---

## Executive Summary: Why Lane C Is Different

Lane C is **not a real-time detection lane.** It's a fundamentally different architecture:

| **Dimension** | **Lane A** | **Lane B** | **Lane C** |
|---------------|-----------|-----------|-----------|
| **Trigger** | Payment authorization | Transaction settlement | Scheduled review cycle |
| **Data source** | Account counters + channel signals | Settlement events + external feeds | Credit file (financial statements) |
| **Frequency** | Per-transaction (50–150ms) | Per-transaction (2.65s post-settlement) | Per-quarter or per-review-cycle |
| **Scope** | Payment-fraud patterns | Payment fraud + AML + trade fraud | Borrower financial health & loan covenant compliance |
| **Who triggers it?** | Automated, every payment | Automated, every payment | Credit team (scheduled or on-demand) |
| **What it answers** | "Is this payment safe to authorize?" | "Was that payment fraudulent?" | "Is this borrower's financial condition deteriorating?" |

**Lane C operates on a *credit-monitoring lifecycle*, not a *payment-fraud cycle.***

---

## The 15 RBI-2016 Signals Lane C Would Cover

These are from the RBI's 2016 Frauds Directions, Annex II—illustrative early-warning signals for detecting borrower distress and fraud. Lane C would read these directly from audited financial statements, annual reports, and auditor reports.

### **Financial Statement & Accounting Indicators (8 signals)**

#### 1. **Default in Undisputed Payment to Statutory Bodies**
**What it is:** The borrower has missed payments to government agencies, tax authorities, or statutory bodies—a red flag for cash flow crisis.

**Where Lane C reads it:** Auditor's report, notes to financial statements, contingent liabilities section.

**Real example:**
- A manufacturing company's auditor notes: *"As of March 31, 2024, the company has ₹2.3 crore in unpaid statutory dues to the Income Tax department (under dispute but acknowledged as liability)."*
- Lane C flags this in Q1 2024 review: borrower in financial distress.
- Triggers: credit team review, possible loan covenant violation check, PLC (Priority Lending Certificate) escalation.

**Why it matters:** Statutory defaults indicate liquidity crisis or regulatory problems. The borrower is in trouble *before* it shows up in payment defaults.

---

#### 2. **Frequent Change in Scope of the Project** (for project-finance loans)
**What it is:** For project-based lending, significant changes to project scope, timeline, or budget are red flags—often indicate scope creep, cost overruns, or fraud.

**Where Lane C reads it:** Project appraisal documents, quarterly project updates, auditor comments, cost-variation notes.

**Real example:**
- Appraisal: Project cost ₹50 crore, timeline 24 months.
- Q2 2024: Scope expanded to ₹75 crore (+50%), timeline extended to 36 months (+50%).
- Q3 2024: Another ₹20 crore addition for "unforeseen site conditions."
- Lane C flags: excessive scope creep, cost escalation, delay risk, possible kickback/fraud.

**Why it matters:** Scope creep is often a sign of mismanagement, under-estimation, or fraud (where contractors inflate costs and hide kickbacks).

---

#### 3. **Disproportionate Inventory Movements vs. Turnover**
**What it is:** Inventory balance is growing while sales are flat or declining—suggests unsold stock, obsolete inventory, or inventory fraud (ghost stock).

**Where Lane C reads it:** Balance sheet (inventory line) vs. P&L (cost of goods sold / revenue), notes to accounts, inventory aging reports.

**Real example:**
- 2022-23: Revenue ₹100 crore, Inventory ₹20 crore (20% of revenue—normal).
- 2023-24: Revenue ₹95 crore (down 5%), but Inventory ₹35 crore (now 37% of revenue, up 75%).
- Lane C flags: inventory buildup without corresponding sales—possible obsolescence, inventory fraud, or unsaleable stock.

**Why it matters:** Large inventory buildups tie up cash and are often the first sign of a company losing market share or engaged in inventory fraud.

---

#### 4. **Disproportionate Receivables Movements vs. Turnover & Ageing**
**What it is:** Accounts receivable are growing while sales are flat or falling—suggests customers can't pay, channel stuffing, or credit policy degradation.

**Where Lane C reads it:** Balance sheet (receivables), P&L (revenue), aging schedule in notes, days-sales-outstanding (DSO).

**Real example:**
- 2022-23: Revenue ₹100 crore, AR ₹15 crore (54 DSO—normal).
- 2023-24: Revenue ₹90 crore (down 10%), but AR ₹28 crore (now 114 DSO—doubling).
- Lane C flags: receivables bloating 2.5× while sales decline—likely credit-quality crisis, customers not paying.

**Why it matters:** Rising receivables without sales growth signals that the company is funding its customers' operations instead of collecting cash—a leading indicator of default.

---

#### 5. **Disproportionate Change in Other Current Assets**
**What it is:** Current assets category is unusually large or growing—may hide related-party loans, advances to promoters, or other red-flag assets.

**Where Lane C reads it:** Balance sheet detail, notes to accounts, related-party disclosures.

**Real example:**
- OCA (Other Current Assets) on balance sheet: ₹5 crore in 2023, ₹25 crore in 2024 (+400%).
- Notes disclose: "Advances to related parties: ₹20 crore (unsecured, no repayment terms)."
- Lane C flags: promoter siphoning cash via related-party loans—potential fraud or loan covenant violation.

**Why it matters:** OCA ballooning often hides cash diversions. Auditors may flag this, but Lane C automates the detection.

---

#### 6. **Working Capital Borrowing Rising as % of Turnover**
**What it is:** The company's short-term borrowing (working capital lines, overdrafts) is growing faster than sales—suggests cash-flow deterioration.

**Where Lane C reads it:** Balance sheet (borrowings), P&L (revenue), cash flow statement, notes on credit facilities.

**Real example:**
- 2022-23: Revenue ₹100 crore, WC borrowing ₹20 crore (20% of revenue—normal).
- 2023-24: Revenue ₹100 crore (flat), WC borrowing ₹40 crore (now 40% of revenue).
- Lane C flags: reliance on short-term borrowing is doubling without revenue growth—cash-flow crisis, declining EBITDA margins.

**Why it matters:** Companies that are profitable don't need rising WC borrowing. This signals operational stress or fraud.

---

#### 7. **Fixed Assets Increasing Without Corresponding Long-Term Sources**
**What it is:** The company is buying fixed assets (capex) but not raising long-term debt or equity to fund them—suggests overextension or hidden debt.

**Where Lane C reads it:** Balance sheet (FA trend), P&L (depreciation increase), cash flow (investing activities), debt schedule.

**Real example:**
- 2023: FA ₹50 crore, LT debt ₹30 crore, equity ₹20 crore.
- 2024: FA ₹75 crore (+50% capex), but LT debt ₹30 crore (unchanged), equity ₹20 crore.
- Lane C flags: ₹25 crore capex unfunded—either cash was diverted, or capex is over-capitalized (disguised operating expenses).

**Why it matters:** Unfunded capex suggests either fraud (capitalizing expenses to inflate profitability) or cash misuse.

---

#### 8. **Frequent Change in Accounting Period or Policies**
**What it is:** The company changes its financial year-end, depreciation methods, revenue recognition, or other accounting policies—often used to manipulate reported earnings.

**Where Lane C reads it:** Notes to accounts (accounting policy section), auditor's report (qualifications or emphasis-of-matter paragraphs).

**Real example:**
- 2023: Company reports under Ind-AS, depreciation straight-line over 10 years.
- 2024: Changes to written-down-value (WDV) method, accelerating depreciation.
- Simultaneously: Changes financial year-end from March to December (mid-year change unusual).
- Lane C flags: accounting policy changes mid-cycle, auditor qualifications likely—possible earnings management.

**Why it matters:** Frequent accounting changes are a classic fraud red flag (Enron did this). Lane C automates detection.

---

### **Borrower Conduct & Compliance Indicators (4 signals)**

#### 9. **Request to Postpone Godown Inspection for Flimsy Reasons**
**What it is:** The borrower prevents the bank (or insurance company) from inspecting collateral—suggests collateral is missing, damaged, or pledged elsewhere.

**Where Lane C reads it:** Credit team notes, inspection reports, email trail in loan file.

**Real example:**
- Bank inspection scheduled for Q1 2024.
- Borrower repeatedly postpones: "godown under renovation," "staff absence," "monsoon damage risk."
- 5 postponements over 6 months; inspection finally happens and finds 40% of claimed inventory missing.
- Lane C flags: inspection resistance correlates with collateral fraud; triggers immediate collateral revaluation.

**Why it matters:** Collateral dodging is a leading indicator of fraud. Lane C monitors inspection schedules and delays automatically.

---

#### 10. **Non-Production of Original Bills for Verification**
**What it is:** The borrower can't produce original trade bills/invoices to support claimed receivables or inventory—suggests receivables are fictitious or over-invoiced.

**Where Lane C reads it:** Due-diligence notes, KYC files, collateral verification reports.

**Real example:**
- Company claims ₹50 crore in receivables backed by invoices from major customers.
- Bank requests original invoices for audit trail.
- Borrower produces only scanned copies; originals are "with customers for certification."
- After pressure: invoices are from shell entities or dated irregularly.
- Lane C flags: invoice fraud, fabricated receivables.

**Why it matters:** Bill of materials fraud is very common in India. Lane C tracks document-production delays.

---

#### 11. **Borrowings Increasing Despite Large Cash Balances**
**What it is:** The company has surplus cash on the balance sheet but is still borrowing—suggests cash is restricted, pledged to other creditors, or fraud.

**Where Lane C reads it:** Balance sheet (cash vs. debt), cash flow statement, notes on cash restrictions.

**Real example:**
- Balance sheet shows: Cash ₹30 crore, short-term borrowing ₹50 crore.
- Notes disclose: "₹28 crore of cash is pledged as collateral for other facilities."
- But effective free cash is only ₹2 crore—not enough to reduce borrowings.
- Lane C flags: highly leveraged, limited financial flexibility; borrower is dependent on credit lines.

**Why it matters:** Cash-and-debt imbalance signals over-leverage or hidden obligations. Lane C spots it automatically.

---

#### 12. **Material Discrepancies / Inconsistencies Within the Annual Report**
**What it is:** Financial statements, notes, and MD&A tell contradictory stories—suggests negligent accounting or intentional fraud.

**Where Lane C reads it:** Balance sheet vs. notes, P&L vs. cash flow, MD&A vs. reported results, auditor's report.

**Real example:**
- P&L reports revenue of ₹100 crore.
- Cash flow statement shows cash collected of only ₹60 crore.
- Notes disclose credit sales of ₹80 crore (should account for the gap, but don't).
- MD&A claims "strong cash realization" but cash flow is negative.
- Lane C flags: inconsistencies suggest earnings manipulation or fraud.

**Why it matters:** Sophisticated frauds often show up as internal inconsistencies in financial statements. Lane C performs cross-statement reconciliation.

---

#### 13. **Poor Disclosure of Adverse Information / Auditor Qualification**
**What it is:** The auditor has raised a qualification (caveat) on the financial statements, or critical adverse information is buried in footnotes.

**Where Lane C reads it:** Auditor's report (any qualification, emphasis-of-matter, or adverse opinion), notes on contingent liabilities, related-party transactions.

**Real example:**
- Auditor's report: *"We were unable to obtain evidence regarding the existence of inventory valued at ₹15 crore (10% of total assets)."* (Qualified opinion.)
- Lane C flags: auditor doubt on 10% of assets—high risk, probable inventory fraud.

**Why it matters:** Auditor qualifications are rare and serious. Lane C automates escalation of qualified audits.

---

#### 14. **Claims Not Acknowledged as Debt Are High**
**What it is:** The company has large contingent liabilities (lawsuits, assessments, claims) that could become debt if the claims succeed.

**Where Lane C reads it:** Notes on contingent liabilities, legal opinions, MD&A discussion.

**Real example:**
- Balance sheet: Total debt ₹100 crore.
- Contingent liabilities note: Income tax assessments under dispute ₹30 crore, litigation with customers ₹20 crore.
- If claims materialize, effective debt is ₹150 crore (+50%).
- Lane C flags: contingent liabilities ratio too high; loan covenant may be at risk if claims succeed.

**Why it matters:** Contingent liabilities can blow up into actual debt. Lane C monitors them as part of debt-servicing capacity.

---

#### 15. **Substantial Increase in Unbilled Revenue Year-over-Year**
**What it is:** The company recognizes revenue but hasn't billed the customer yet—suggests aggressive revenue recognition or fictional sales.

**Where Lane C reads it:** Balance sheet (unbilled revenue or contract assets), notes on revenue policy, P&L trend.

**Real example:**
- 2023: Revenue ₹100 crore, unbilled revenue ₹5 crore (normal for service contracts).
- 2024: Revenue ₹120 crore, unbilled revenue ₹35 crore (now 29% of revenue—abnormal).
- Lane C flags: revenue recognition becoming more aggressive; customer disputes or write-offs likely ahead.

**Why it matters:** Unbilled-revenue spikes often precede revenue write-downs (when customers refuse to pay because services weren't delivered).

---

## How Lane C Works: The Review Cycle

Lane C operates on a **scheduled review cycle**, not triggered by transactions:

### **Quarterly Review Process**

**T+0 (End of quarter):**
- Borrower submits quarterly financials, auditor reports (if applicable), project updates.
- Lane C system ingests the data.

**T+1 to T+7 days (Automated Lane C Analysis):**
1. **Financial extraction:** Parse P&L, balance sheet, cash flow statement.
2. **Signal detection:** Run 15 RBI algorithms against the numbers.
3. **Peer benchmarking:** Compare borrower's metrics to peer group (same industry, same size).
4. **Trend analysis:** Compare quarter-over-quarter and year-over-year.
5. **Score generation:** Assign overall "credit health score" (0–100).
6. **Alert prioritization:** Flag critical issues for credit team review.

**T+7 to T+30 days (Credit Team Review):**
- Credit analyst reviews flagged signals with borrower.
- Escalate to loan committee if covenant violations detected.
- Possible actions:
  - Increase monitoring frequency.
  - Request financial covenants review.
  - Initiate collateral revaluation.
  - Trigger early warning / downgrade procedures.
  - Escalate to RBI for possible CRILC (Consortium for Supervisory Information at ₹3 crore+ threshold).

---

## Real-World Lane C Use Cases

### **Use Case 1: Manufacturing Borrower - Receivables Deterioration & Early Default Warning**

**Borrower:** Textiles manufacturer, ₹500 crore facility, 3-year history.

**Q1 2024 (Lane C Review):**
- Revenue: ₹45 crore (flat vs. Q1 2023: ₹45 crore).
- Accounts Receivable: ₹8 crore in Q1 2023 → **₹16 crore in Q1 2024** (+100%).
- DSO: 65 days → **130 days** (doubling).
- Working capital borrowing: ₹10 crore → **₹22 crore** (+120%).
- Cash conversion cycle: 30 days → **60 days**.

**Lane C Signals:**
1. ✅ **LAY-01 equivalent (credit-side):** Receivables building without revenue growth.
2. ✅ **Signal #4 (RBI):** Disproportionate AR movement vs. turnover.
3. ✅ **Signal #6:** Working capital borrowing rising as % of turnover.

**Lane C Score:** 35/100 (Critical deterioration).

**Credit Team Action:**
- Contact borrower: "What's causing the collection delay?"
- Borrower admits: Major customer (30% of revenue) is in cash-flow crisis; payment delayed 60+ days.
- Bank decision: Tighten covenants, increase monitoring to monthly, freeze additional credit lines.
- Result: Bank exits early before default; saves ₹100+ crore in provisions.

**Without Lane C:** Bank would have discovered this only in Month 4 after a payment default on the facility.

---

### **Use Case 2: Project Finance Loan - Scope Creep & Fraud Detection**

**Borrower:** Infrastructure contractor, ₹200 crore project finance facility, buildout of 24 months.

**Appraisal (M=0):**
- Project cost: ₹200 crore, funded by ₹100 crore debt + ₹100 crore equity.
- Timeline: 24 months.
- Collateral: Equipment, land, contractor's personal guarantee.

**Q2 2024 (M=6):**

Lane C ingests:
- Auditor's report on project status.
- Quarterly project update from PMC (Project Management Consultant).
- Budget vs. actual expenditure.

**Findings:**
- Expenditure to date: ₹70 crore (vs. planned ₹66 crore—on pace).
- But: Project scope expanded to ₹250 crore (+25%); timeline extended to 30 months (+25%).
- Cost overrun attributed to: "Unforeseen site conditions," "regulatory delays," "sub-contractor issues."

**Lane C Signals:**
1. ✅ **Signal #2 (RBI):** Frequent change in project scope.
2. ✅ **Signal #7:** Fixed assets increasing without corresponding LT funding (equity not increased).

**Lane C Score:** 42/100 (High risk).

**Credit Team Investigation:**
- Interviews reveal: The PMC is controlled by the contractor's brother-in-law (related party).
- Site visit finds: No evidence of "unforeseen conditions"; some of the claimed work appears over-invoiced.
- Budget details show: ₹30 crore "contingency" not in original appraisal, now being claimed.

**Bank Action:**
- Replace PMC with independent monitor.
- Tighten approval process for change orders.
- Reduce future disbursements by 20% holdback.
- Escalate to internal audit and fraud investigation team.

**Result:** Avoids ₹30–50 crore in fraudulent cost overrun claims.

---

### **Use Case 3: MSME Borrower - Accounting Manipulation & Earnings Fraud**

**Borrower:** Electronic components MSME, ₹10 crore facility, 2-year history.

**Annual Financials 2023:**
- Revenue: ₹30 crore.
- EBITDA: ₹6 crore (20% margin—normal for sector).
- Debt: ₹8 crore, Equity: ₹3 crore.
- Debt-to-equity: 2.67 (within covenants: <3.0).

**Annual Financials 2024 (submitted for Lane C review):**
- Revenue: ₹35 crore (+17%).
- EBITDA: ₹8.4 crore (+40% margin jump to 24%).
- Debt: ₹8 crore (unchanged), Equity: ₹3 crore (unchanged).

**Lane C Signals Fired:**
1. ✅ **Signal #8:** Accounting policy change mid-cycle.
   - Notes disclose: Depreciation method changed from SL (straight-line) to WDV (written-down-value).
   - Impact on EBITDA: ~₹1.2 crore (lower depreciation charges).
2. ✅ **Signal #6:** Working capital borrowing rose ₹3 crore despite EBITDA growth.
   - Paradox: If EBITDA grew by ₹2.4 crore, cash should improve, not worsen.
3. ✅ **Signal #12:** Internal inconsistencies in financials.
   - P&L reports revenue ₹35 crore, but cash flow shows collections of only ₹25 crore.
   - Notes disclose credit sales of ₹28 crore (+50% from 2023), but AR only grew ₹2 crore (inconsistency).

**Lane C Score:** 28/100 (Critical—likely fraud).

**Auditor's Report Review:**
- Auditor has issued a "qualified opinion" on revenue recognition: *"We were unable to observe year-end inventory due to logistical issues; revenue cannot be fully verified."*
- Red flag: Qualified audit + accounting changes + revenue mismatches = earnings fraud.

**Credit Team Action:**
- Request original customer invoices and delivery confirmations—borrower cannot produce originals.
- On-site verification: Inventory claimed as ₹8 crore appears only ₹4 crore when audited.
- Conclusion: ₹3–4 crore of revenue is fictitious; EBITDA is likely ₹4–5 crore, not ₹8.4 crore.
- Debt-to-equity covenant is actually breached (8/3 = 2.67, but adjusted EBITDA of ₹4.5 crore suggests worse leverage).

**Bank Action:**
- Trigger covenant default clause.
- Accelerate the loan.
- Initiate fraud investigation and possible FIR.
- Reserve for potential loss on facility.

**Without Lane C:** Bank would have approved further credit expansion; loss could reach ₹2+ crore when default occurs months later.

---

### **Use Case 4: Corporate Borrower - Contingent Liability Blow-Up**

**Borrower:** Pharmaceutical company, ₹500 crore facility, 5-year relationship.

**2023 Annual Report (Submitted to Lane C):**
- Debt: ₹200 crore (per balance sheet).
- **Contingent liabilities note:** Income tax assessments under dispute ₹60 crore; litigation with competitors re: patent infringement ₹40 crore.
- Note disclosure: *"Management believes these assessments will be successfully contested."*

**Q2 2024 (Lane C Review + External News):**
- Income tax department **issues final order** upholding ₹60 crore assessment.
- Supreme Court **rejects patent infringement appeal**; company now liable for ₹40 crore damages + ₹15 crore interest.

**Lane C Signals:**
1. ✅ **Signal #14 (RBI):** Claims not acknowledged as debt are high.
   - Total contingent liabilities: ₹100 crore (50% of reported debt).
   - After finalization: Effective debt is now ₹300 crore (₹200 + ₹100), not ₹200 crore.

**Lane C Score:** 25/100 (Distress).

**Financial Covenant Trigger:**
- Debt-to-equity covenant was 2.0 (₹200 crore debt / ₹100 crore equity); within limit.
- Post-contingency: Effective D/E is 3.0 (₹300 crore / ₹100 crore); **covenant breached**.
- Interest coverage covenant: EBITDA ₹50 crore / interest ₹15 crore = 3.3× (within 3.5× limit).
- Post-contingency, after-tax cost of ₹100 crore hit reduces EBITDA to ₹50 crore (no change in cash loss), but interest coverage still holds.

**Bank Action:**
- Issue notice of covenant breach.
- Require borrower to raise additional ₹50 crore equity or repay ₹100 crore of debt within 90 days.
- Tighten monitoring; credit rating downgrade.

**Result:** Bank forces early remediation and reduces credit exposure before a full-blown distress event.

---

### **Use Case 5: Collateral Fraud - Missing Inventory**

**Borrower:** FMCG distributor, ₹50 crore facility backed by inventory collateral.

**Q1 2024 Collateral Appraisal:**
- Claimed inventory: ₹15 crore.
- Bank appraisal report: Inventory valued at ₹15 crore, LTV (loan-to-value) 80%.
- Collateral coverage ratio: 80% (acceptable).

**Q2 2024 (Lane C + Collateral Monitoring):**

Lane C ingests Q2 inventory data:
- Revenue: ₹20 crore (Q2 vs. Q1: ₹20 crore—flat).
- Inventory on balance sheet: **₹3 crore** (down 80% from ₹15 crore in Q1).
- Cost of goods sold: ₹16 crore (implies inventory turnover should have fallen; instead it collapsed).

**Lane C Signals:**
1. ✅ **Signal #3 (RBI):** Inventory movements disproportionate to turnover.
   - Normal turnover: ₹20 crore revenue, ₹15 crore inventory = 1.3× (turnover).
   - Q2 turnover: ₹20 crore / ₹3 crore = 6.7× (impossible without stock-out situation).

**Credit Team Response:**
- Request collateral re-inspection.
- Re-appraisal finds: Only ₹4 crore of inventory actually exists; ₹11 crore of collateral was fictitious.

**Bank Action:**
- Collateral coverage drops to 20% (4/20)—**covenant breach**.
- Demand additional collateral or loan reduction.
- Trigger fraud investigation; borrower has been using inflated inventory to draw excess credit.
- Initiate recovery proceedings.

**Result:** Lane C's automatic inventory-monitoring catches the fraud before the collateral vanishes entirely.

---

## Lane C Integration with Lanes A & B

### **Scenario: Borrower Committing All Three Types of Fraud**

**Borrower:** A composite company operating both a trading business and a manufacturing facility.

**Payment Fraud (Lane A/B Catch):**
- Money mule account on the trading side (LAY-01, LAY-02 triggered by Lane B).
- Fake invoices being used to justify transfers (TBM signals, Lane B).

**Credit Fraud (Lane C Catches):**
- Manufacturing side's Q1 financials show ₹20 crore in unbilled revenue.
- Auditor has qualified the revenue ("unable to verify shipments").
- Working capital borrowing doubled despite flat revenue.

**Lane C + Lane B Correlation:**
1. **Lane A/B:** Detects fraudulent payment flows and invoice manipulation on the trading side within hours of occurrence.
2. **Lane C:** Detects financial deterioration and contingent liabilities on the manufacturing side within weeks of quarter-end.
3. **Combined signal:** Bank realizes the borrower is committing organized fraud across multiple business lines—not a one-off incident.

**Bank Action:** Escalate to Central Fraud Registry, consider recovery of entire facility, refer to law enforcement.

---

## Lane C Implementation: Phased Approach

### **Phase 1: MVP (Minimum Viable Product) — Q1 2025**
- Focus: The 8 most actionable signals (inventory, receivables, scope creep, accounting changes).
- Data intake: Quarterly financial statements (PDF, uploaded by borrower or pulled from MCA/regulatory filings).
- Alerts: Dashboard for credit team showing critical flags per borrower.
- Scope: Large corporate borrowers (₹10+ crore facilities).

### **Phase 2: Expansion — Q2-Q3 2025**
- Add 7 more RBI signals (contingent liabilities, statutory defaults, asset-liability mismatches).
- Integrate with CBS: Auto-pull financial data from borrower's auditor / regulator (if available).
- Peer benchmarking: Compare borrower to peer group (same industry, size).
- Predictive scoring: ML model to predict distress/fraud 2–3 quarters ahead.

### **Phase 3: Full Integration — Q4 2025**
- Connect Lane C alerts to Lane A/B transaction monitoring.
- Correlation engine: When Lane B detects unusual transactions, check if Lane C shows financial stress.
- Risk dashboard: Real-time borrower health score combining all three lanes.
- Regulatory reporting: Auto-populate credit-quality migration and early warning metrics for RBI reporting.

---

## Regulatory & Compliance Alignment

### **RBI Master Directions 2024-25: Early Warning Systems**
Lane C directly supports RBI's EWS framework:
- **Section 12.3:** Banks must have documented early-warning systems for credit deterioration.
- **Lane C Coverage:** Detects all 15 RBI-2016 illustrative signals + additional credit-quality metrics.
- **Compliance artifact:** Lane C reports feed directly into RBI Basel III credit-risk disclosures and CRILC (Consortium Lending Risk Classification).

### **CRILC Threshold Management (₹3 Crore+)**
For facilities ≥₹3 crore:
- **Lane C Role:** Trigger CRILC escalation when any borrower hits critical signals.
- **Auto-reporting:** Lane C can auto-generate CRILC submissions based on financial metrics.
- **Example:** Unbilled revenue spike (Signal #15) triggers auto-escalation to RBI if borrower has ₹3+ crore exposure.

### **Stress Testing & Capital Adequacy**
- **Lane C as input:** Include Lane C-detected credit-quality migrations in stress-testing models.
- **Example:** If Lane C detects 20 borrowers with high contingent liabilities, model their probability of default (PD) uplift in stress scenarios.

---

## Technical Architecture: How Lane C Works

### **Data Flow**

```
Borrower Financial Statements (Quarterly)
    ↓
Document Upload / CBS Pull / MCA Registry API
    ↓
PDF/Excel Parsing → Extract P&L, B/S, Cash Flow
    ↓
15 RBI Signal Algorithms
    ↓
Peer Benchmarking (Industry / Size cohort)
    ↓
Trend Analysis (QoQ, YoY)
    ↓
Lane C Score (0–100)
    ↓
Alert Generation (Critical / High / Medium / Low)
    ↓
Credit Team Dashboard
    ↓
Lane A/B Feedback Loop (for transaction correlation)
```

### **Key Algorithms**

1. **Inventory-to-Revenue Ratio Checker:**
   - Flags if inventory % of revenue exceeds peer average by >50%.
   - Alerts on inventory growth with revenue decline.

2. **Receivables Aging Tracker:**
   - Computes DSO (Days Sales Outstanding).
   - Alerts if DSO increases by >20 days QoQ.
   - Flags if COGS/Revenue doesn't reconcile with AR movement.

3. **Debt-to-EBITDA Covenant Monitor:**
   - Auto-checks if borrower is within agreed covenants.
   - Alerts to potential breach based on latest financials.

4. **Contingent Liability Aggregator:**
   - Sums all contingent liabilities from notes.
   - Recalculates effective debt-to-equity with contingencies included.
   - Alerts if effective D/E exceeds threshold by >10%.

5. **Accounting Change Detector:**
   - Compares accounting policies YoY.
   - Flags policy changes + quantifies impact on EBITDA/NI.
   - Alerts if policy changes + auditor qualifications co-occur.

6. **Cash Flow vs. Earnings Reconciliation:**
   - Validates that revenue increase translates to cash collection increase.
   - Flags large variances (CFO vs. NI) as potential earnings quality issue.

---

## Risk Scoring: How Lane C Rates Borrower Health

### **Lane C Credit Health Score: 0–100**

| **Score Range** | **Interpretation** | **Loan Action** |
|-----------------|-------------------|-----------------|
| **80–100** | Excellent / No deterioration | Continue normal monitoring. |
| **60–79** | Acceptable / Minor stress | Increase monitoring to semi-annual; review covenants. |
| **40–59** | Concerning / Significant deterioration | Increase monitoring to quarterly; request management explanation; consider covenant tightening. |
| **20–39** | Critical / Imminent risk | Escalate to credit committee; consider acceleration; tighten covenants severely. |
| **0–19** | Distress / Fraud / Near-default | Trigger covenant default procedures; initiate recovery; refer to fraud team. |

**Scoring Inputs:**
- Signal count (how many of 15 RBI signals triggered): -5 points per signal.
- Trend severity (how fast is deterioration): -3 to -15 points depending on velocity.
- Peer position (relative to cohort): -0 to -10 points if below peer median.
- Auditor opinion (qualified audit): -20 points.
- Contingent liabilities ratio: -0 to -20 points based on % of debt.
- Management quality (history of transparent reporting): +0 to +10 bonus points.

---

## Use Case Taxonomy: When Lane C Detects What

| **RBI Signal** | **Early Detection Window** | **Typical Borrower Action** | **Bank Response** |
|---|---|---|---|
| Receivables deterioration | Q1 → visible in Q2 financials | Borrower delays payment collection; customers in distress. | Tighten WC covenants; increase LTV haircut. |
| Inventory buildup | Q1 → visible in Q2 financials | Borrower loses market share; unsold stock accumulates. | Trigger inventory audit; reduce facility size. |
| Scope creep (project) | M=6 → visible in Q2 update | Contractor underestimated costs; inflating claims. | Replace PMC; mandate change-order controls. |
| Accounting changes | Year-end → visible in annual report | Borrower manipulating earnings pre-decline announcement. | Escalate to audit committee; fraud investigation. |
| Contingent liability spike | Disclosed in annual report or auditor report | Legal/regulatory risks materializing. | Stress-test covenant compliance; demand equity injection. |
| Working capital bloat | Q1 → visible in Q2 financials | Borrower's cash-conversion cycle worsening. | Trigger covenant review; possible breach action. |
| Statutory defaults | Auditor discloses in report | Borrower in liquidity crisis. | Escalate to RBI; classify as stressed asset. |
| Unbilled revenue jump | Year-end or Q-end report | Aggressive revenue recognition; customer disputes ahead. | Request revenue verification; reduce EBITDA assumptions. |

---

## Examples of Lane C Catching Fraud Before Payment Fraud Occurs

### **Example 1: Ponzi-Scheme Borrower**
- Lane C detects (Q1): Rising receivables + flat revenue + rising WC borrowing + accounting changes.
- Signals: Borrower is using loans to pay dividends to promoters (not expanding operations).
- Lane C score drops to 30/100.
- Bank investigates; discovers borrower is a Ponzi scheme.
- Bank avoids ₹100+ crore loss by early escalation.

### **Example 2: Shell Company**
- Lane C detects (Q1): Unbilled revenue spiking + contingent liabilities buried in notes + related-party transactions soaring.
- Lane C score: 15/100 (critical).
- Bank performs site visit; discovers the company has no real operations—it's a shell used to launder money.
- Bank exits the facility before money-mule transactions start on Lane A/B.

### **Example 3: Misapplication of Loan Proceeds**
- Loan approved for working capital (inventory, receivables financing).
- Lane C detects (Q1): Inventory DOWN, receivables DOWN, but WC borrowing UP.
- Paradox: Borrower is withdrawing WC credit but not using it for WC—it's going elsewhere.
- Lane C score: 35/100.
- Bank demands proof of WC deployment; discovers funds were diverted to real-estate investment (covenant breach).
- Bank accelerates the loan.

---

## Conclusion: Lane C as the "Credit Lifecycle" Guardian

While **Lane A/B** catch payment-level fraud (individual transactions), **Lane C** catches credit-level fraud and deterioration (the borrower's overall health):

| **Fraud Level** | **Detection Lane** | **Time to Detection** |
|---|---|---|
| **Payment-level** (individual transaction) | Lane A/B | Milliseconds to hours |
| **Account-level** (pattern across transactions) | Lane A/B | Hours to days |
| **Borrower-level** (financial deterioration, covenant breach) | **Lane C** | Weeks (quarterly review) |
| **Portfolio-level** (systemic credit risk) | Lane C + RBI reporting | Months |

**Lane C roadmap addresses a critical gap:** Today, banks rely on annual audits to catch financial fraud. By the time an auditor qualifies revenue or flags inventory issues, the damage is often done. **Lane C automates this detection and compresses the time-to-escalation from months to weeks.**

