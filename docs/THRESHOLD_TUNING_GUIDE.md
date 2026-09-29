# Threshold Tuning Guide
## Risk/Operations Manual for Fraud360 Signal Calibration

**Version:** 1.0  
**Audience:** Risk managers, operations teams, fraud analysts  
**Review Cycle:** Monthly (first 3 months); quarterly (thereafter)  
**Objective:** Calibrate detection thresholds to match bank's risk appetite and fraud profile

---

## Executive Summary

A threshold is the **trigger point** for a signal. Too low → false positives block legitimate customers. Too high → fraud slips through.

**Example:**
- **VEL-01 (Value Velocity):** Baseline ₹5,000/transaction
  - Threshold: 5× → blocks txns >₹25,000 → might be too aggressive (many legitimate spikes)
  - Threshold: 10× → blocks txns >₹50,000 → might be too lenient (catches obvious mules)
  - **Sweet spot:** 8× for this account type
  
This guide shows you how to find that sweet spot for all 28 rules.

---

## Methodology: Finding the Right Threshold

### **Step 1: Understand Your Baseline**

**What is a baseline?** The "normal" behavior of an account against which deviations are measured.

| **Rule** | **Baseline Metric** | **Source** | **Calculation** |
|---|---|---|---|
| **VEL-01** | Account's 30-day average transaction value | Historical counters | Sum of last 30 days / 30 txns |
| **VEL-03** | Account's typical transaction count | Historical counters | Average daily count |
| **SME-01** | Sub-CTR transaction frequency | Historical counters | Count of txns just under ₹10L in 30 days |
| **LAY-01** | Account's inflow/outflow ratio | Historical counters | Sum of outflows / sum of inflows |
| **LAY-02** | Typical number of counterparties | Historical counters | Distinct payee count in 30 days |
| **AR** | Days Sales Outstanding | Financial statements | (AR / Revenue) × 365 |

### **Step 2: Analyze Your Fraud History**

Collect data on **confirmed frauds** from the last 2–3 years:
- How many transactions before fraud was detected?
- What was the average value of fraudulent transactions?
- How many counterparties were involved?
- How long did the scheme run?

**Example fraud profile:**

| **Metric** | **Mule Account #1** | **Mule Account #2** | **Typical Range** |
|---|---|---|---|
| Fraud duration (days) | 14 | 21 | 7–30 days |
| Total value transferred | ₹85 lakh | ₹62 lakh | ₹50–100 lakh |
| Avg transaction value | ₹15,000 | ₹18,000 | ₹10,000–25,000 |
| Distinct counterparties | 28 | 34 | 20–40 |
| Outflow ratio | 92% | 88% | 80–95% |
| Days to detection (legacy) | 18 | 22 | 15–25 days |
| **Days to detection (Lane A)** | 2 | 3 | <5 days (target) |

### **Step 3: Calculate Recommended Thresholds**

**Formula:** `Threshold = Baseline × Sensitivity Factor`

**Sensitivity Factor** depends on your risk appetite:
- **Conservative** (catch all fraud, accept false positives): 3–5× baseline
- **Balanced** (catch most fraud, minimize false positives): 6–10× baseline
- **Aggressive** (minimal false positives, miss some fraud): 10–15× baseline

**Example: VEL-01 (Velocity)**

Assume:
- Account baseline: ₹5,000/transaction
- Your risk appetite: **Balanced**
- Sensitivity factor: 8×

**Recommended threshold:** ₹5,000 × 8 = **₹40,000**

Interpretation: Flag any transaction >₹40,000 on this account.

---

## Threshold Tuning by Signal Family

### **Velocity Signals (VEL)**

**Purpose:** Detect sudden spike in transaction value or frequency.

#### **VEL-01: Value Velocity Against Baseline**

| **Account Type** | **Baseline** | **Conservative** | **Balanced** | **Aggressive** | **Recommendation** |
|---|---|---|---|---|---|
| **Salaried** | ₹10K–20K avg | 4× | 7× | 12× | **6–8×** (balanced) |
| **Trader/SME** | ₹50K–100K avg | 3× | 6× | 10× | **5–7×** (balanced) |
| **Institutional** | ₹1L–5L avg | 2× | 4× | 8× | **3–5×** (balanced) |
| **E-commerce** | ₹500K–1M avg | 1.5× | 2.5× | 5× | **2–3×** (balanced) |

**Tuning approach:**
1. Run historical data through Lane A with threshold = 4× → count false positives
2. Increase threshold to 5× → recount false positives
3. Target: <3% false positive rate
4. If FP >3%, raise threshold; if FP <1%, lower threshold

**Common mistakes:**
- ❌ Setting threshold too low (₹10K for salaried account → too many blocks)
- ✅ Setting per-account-type (different baselines for different profiles)

---

#### **VEL-03: Small-Credit Frequency Spike**

| **Account Type** | **Normal Daily Txn Count** | **Spike Threshold** | **Recommendation** |
|---|---|---|---|
| **Salaried** | 0–1 txns/day | >5 txns/day (2× normal × 5) | Flag if >4 txns in 1 hour |
| **Trader/Business** | 5–10 txns/day | >30 txns/day (3× normal) | Flag if >20 txns in 1 hour |
| **E-commerce** | 50–100 txns/day | >250 txns/day | Flag if >150 txns in 1 hour |

**Tuning:**
- Collect 30-day transaction count distribution for account type
- Calculate 95th percentile (what is "normal spike"?)
- Set threshold at 2–3× the 95th percentile
- Test for 2 weeks; adjust if false positives >5%

---

### **Structuring Signals (SME)**

**Purpose:** Detect CTR evasion or PIN-less UPI/card fragmentation.

#### **SME-01: Sub-CTR Structuring Count**

**Fixed thresholds (set by regulation):**
- CTR threshold: ₹10 lakh (fixed by RBI)
- Structuring flag: 3–5 transactions just below ₹10L in 24 hours
- Recommended setting: **Flag if ≥4 txns between ₹9L–₹10L in 24h**

**Risk appetite dial:**
- **Conservative:** 3 txns (catch more)
- **Balanced:** 4 txns (RBI recommendation)
- **Aggressive:** 5 txns (fewer false positives)

**Do NOT adjust CTR threshold itself** — that's regulatory. Adjust only the count.

---

#### **SME-02: PIN-less UPI Structuring**

| **Account Type** | **Normal Daily UPI Count** | **Flag Threshold** | **Amount per Txn** | **Recommendation** |
|---|---|---|---|---|
| **Salaried** | 1–3 UPI/day | >10 in 1 hour | ₹5K each | Flag if >10 txns ₹5K each in 1h |
| **Business** | 10–20 UPI/day | >50 in 1 hour | ₹5K each | Flag if >40 txns ₹5K each in 1h |
| **E-commerce** | 100+ UPI/day | >200 in 1 hour | ₹5K each | Flag if >150 txns ₹5K each in 1h |

**Tuning:**
- Set threshold = 2× normal hourly average
- PIN-less limit is fixed at ₹5K by banks (don't change)
- Only tune: how many ₹5K txns in one hour before flagging

---

### **Layering Signals (LAY)**

**Purpose:** Detect money-mule networks and hub accounts.

#### **LAY-01: Outflow Drain**

| **Account Type** | **Normal Outflow Ratio** | **Conservative** | **Balanced** | **Aggressive** | **Recommendation** |
|---|---|---|---|---|---|
| **Salaried** | 30–50% | 70% | 85% | 95% | **80–85%** |
| **Trader/Business** | 40–70% | 80% | 85% | 90% | **82–88%** |
| **E-commerce/SME** | 60–90% | 90% | 92% | 96% | **90–92%** |

**How to calculate normal ratio for YOUR account:**
```
For each account in your database:
  Outflow Ratio = Sum(outflows in 7 days) / Sum(inflows in 7 days)

Rank all ratios, find:
  - 25th percentile (conservative)
  - 50th percentile (normal)
  - 75th percentile (aggressive)
  
Then set threshold at: 50th percentile + 1.5×(standard deviation)
```

**Example calculation:**
- Salaried accounts: outflow ratios = [0.30, 0.35, 0.42, 0.48, 0.65, 0.95]
- Median: 0.45; std dev: 0.24
- Recommended threshold: 0.45 + (1.5 × 0.24) = **0.81** (or 81%)

---

#### **LAY-02: Fan-In/Fan-Out Hub (Distinct Counterparties)**

| **Account Type** | **Normal Counterparty Count** | **Flag Threshold** | **Recommendation** |
|---|---|---|---|
| **Salaried** | 2–5 | >15 | Flag if >12 unique payees in 7 days |
| **Trader/Business** | 8–20 | >50 | Flag if >40 unique payees in 7 days |
| **E-commerce/SME** | 30–100 | >200 | Flag if >150 unique payees in 7 days |

**Tuning approach:**
1. Calculate historical max counterparty count for account type
2. Multiply by 1.5–2× for threshold
3. Test: does this flag actual fraud? (check your fraud database)
4. If 0 known frauds flagged → lower threshold; if >20% false positives → raise threshold

---

#### **LAY-04: Device Fingerprint Clustering**

**Fixed threshold (per RBI):**
- Flag if 1 device accesses >5 accounts in 24 hours
- Recommended: **Don't change this** — it's a hard security boundary

**Optional tuning:**
- Whitelist legitimate scenarios: family phone, shared device at office
- Each whitelist entry requires CRO approval + documented justification

---

### **Behavioural Signals (BEH)**

#### **BEH-01: Dormancy on Large Payment**

| **Dormancy Window** | **Amount Threshold** | **Recommendation** |
|---|---|---|
| **6+ months inactive** | ₹50K+ | Flag immediately |
| **3–6 months inactive** | ₹100K+ | Flag if account hasn't received >₹10K in past 3 months |
| **1–3 months inactive** | ₹200K+ | Flag if structured as sub-threshold txns |

**Tuning:**
- Dormancy window: 3–6 months (balanced); can adjust down to 1 month (conservative)
- Amount threshold: baseline × 10 (conservative) to baseline × 20 (aggressive)
- Recommendation: **6 months + ₹50K threshold** (catches reactivated old accounts)

---

### **Channel Signals (CHN)**

#### **CHN-01: New Device + New Payee + High Value**

This signal is **channel-supplied** (not computed by Fraud360). Your bank's mobile app sends:
```json
{
  "risk_signals": 0,  // 0–5 (0=safe, 5=risky)
  "new_device": false,
  "new_payee": false,
  "high_value": false
}
```

**Threshold:** Set at bank-side (in your PSP/app), not Fraud360.

**Recommendation:**
- Use app's risk_signals score (0–5)
- Flag if score ≥3 (balanced risk appetite)
- Flag if score ≥2 (conservative)
- Flag if score ≥4 (aggressive)

---

### **Credit/Financial Signals (Lane C)**

#### **Inventory Movement (LAY/Financial)**

| **Industry** | **Normal Growth** | **Flag Threshold** | **Recommendation** |
|---|---|---|---|
| **Manufacturing** | 5–15% YoY | +30% | Flag if inventory grows 30%+ while revenue flat/down |
| **Retail/Trade** | 10–20% YoY | +40% | Flag if inventory grows 40%+ while revenue flat |
| **Pharma** | 2–8% YoY | +20% | Flag if inventory grows 20%+ while revenue flat |

**Tuning:**
- Use industry peer medians (benchmarking)
- Set threshold at median + 1.5×(std dev)
- Test against known loan defaults: did Lane C flag 3–6 months before default?

---

## Monthly Tuning Cycle: First 3 Months

### **Week 1: Establish Baseline**
```
1. Export 30-day transaction history for sample accounts
2. Calculate baseline metrics (velocity, counterparty count, etc.)
3. Compare to known fraud profiles
4. Document baseline per account type
```

### **Week 2: Run Pilot with Conservative Thresholds**
```
1. Deploy Lane A/B with conservative thresholds
2. Set monitoring dashboard (Grafana/BI tool)
3. Watch false positive rate daily
4. Collect feedback from fraud team
```

### **Week 3: Adjust & Retest**
```
If FP rate >5%:
  - Raise thresholds by 10%
  - Retest
  - Document changes

If FP rate <2%:
  - Lower thresholds by 5%
  - Retest
  - Document changes
  
Target: 2–3% FP rate
```

### **Week 4: Review & Lock**
```
1. Review all changes with CRO
2. Lock thresholds (no more frequent changes)
3. Document final thresholds in policy
4. Train fraud team on new thresholds
```

---

## Tuning Dashboard Setup

### **Recommended Metrics to Monitor**

| **Metric** | **Dashboard** | **Target** | **Action if Out of Range** |
|---|---|---|---|
| **False Positive Rate** | Fraud team dashboard | <3% | Raise thresholds by 5–10% |
| **Detection Rate** | Fraud team dashboard | >90% (vs. known fraud) | Lower thresholds by 5% |
| **Avg Alert Latency** | Operations dashboard | <100ms (Lane A), <3s (Lane B) | Investigate performance |
| **Alert Volume (daily)** | Operations dashboard | 5–50 alerts/day (per 1M txns) | Stable; flag spikes |
| **Customer Complaints** | Support dashboard | <1 per 10K alerts | Investigate false positives |
| **STR/FMR Filing Timeliness** | Compliance dashboard | <5 working days | Process improvement |

### **Sample Dashboard Query (SQL)**

```sql
-- Daily false positive rate
SELECT
  DATE(created_at) as date,
  COUNT(*) as total_alerts,
  SUM(CASE WHEN false_positive THEN 1 ELSE 0 END) as fp_count,
  ROUND(100.0 * SUM(CASE WHEN false_positive THEN 1 ELSE 0 END) / COUNT(*), 2) as fp_rate_pct
FROM alerts
WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY DATE(created_at)
ORDER BY date DESC;
```

---

## Risk Appetite Profiles

### **Profile 1: Conservative Bank**
**Use case:** Retail-focused bank, risk-averse board.

| **Signal** | **Threshold** | **Rationale** |
|---|---|---|
| VEL-01 | 5× baseline | Catch more velocity spikes |
| SME-01 | 3 sub-CTR txns | Catch structuring early |
| LAY-01 | 70% outflow ratio | Catch mule accounts quickly |
| LAY-02 | 15 counterparties | Narrow hub detection |
| CHN-01 | Risk score ≥2 | Aggressive device/payee checks |

**Expected impact:** 5–7% FP rate; >95% detection rate; 30–50 alerts/day (per 1M txns)

---

### **Profile 2: Balanced Bank**
**Use case:** Mid-sized bank, balanced approach.

| **Signal** | **Threshold** | **Rationale** |
|---|---|---|
| VEL-01 | 8× baseline | Sweet spot for most account types |
| SME-01 | 4 sub-CTR txns | RBI recommendation |
| LAY-01 | 85% outflow ratio | Catch obvious mules |
| LAY-02 | 30 counterparties | Catch real hubs, miss noise |
| CHN-01 | Risk score ≥3 | Balanced device/payee checks |

**Expected impact:** 2–3% FP rate; >90% detection rate; 10–20 alerts/day (per 1M txns)

---

### **Profile 3: Aggressive Bank**
**Use case:** Fintech, customer-experience-focused.

| **Signal** | **Threshold** | **Rationale** |
|---|---|---|
| VEL-01 | 12× baseline | Minimize false positives |
| SME-01 | 5 sub-CTR txns | High bar for structuring |
| LAY-01 | 90% outflow ratio | Only catch obvious mules |
| LAY-02 | 50 counterparties | Real hub only |
| CHN-01 | Risk score ≥4 | High device/payee bar |

**Expected impact:** 0.5–1% FP rate; 80–85% detection rate; 2–5 alerts/day (per 1M txns)

---

## Threshold Change Management

### **Approval Process**

1. **Fraud Team observes** issue (e.g., "LAY-02 flagging too many legitimate hubs")
2. **Propose change** to CRO: "Raise LAY-02 threshold from 25 to 35 counterparties"
3. **Model test:** Run proposed threshold on 30-day historical data
   - Calculate impact on FP rate
   - Calculate impact on detection rate (any fraud missed?)
4. **CRO approves** or requests alternative
5. **Deploy & monitor:** Run for 2 weeks, track metrics
6. **Lock or adjust:** If metrics stable, lock threshold; if not, adjust again

### **Documentation Template**

```
Threshold Change Log
====================

Date: 2025-01-15
Rule: LAY-02 (Distinct Counterparties)
Old Threshold: 25
New Threshold: 30
Reason: FP complaints from legitimate traders with >25 payees
Impact (30-day backtest):
  - FP rate: 4.2% → 2.8% ✓
  - Detection rate: 92% → 91% (acceptable)
  - Alerts/day: 15 → 10
Approved by: [CRO Name]
Monitored for: 2 weeks ✓
Status: Locked
```

---

## Quarterly Tuning Review

### **Q1 Review Checklist**

- [ ] FP rate trending down? (target: <3%)
- [ ] Detection rate stable? (target: >90%)
- [ ] Customer complaints decreasing?
- [ ] Any new fraud patterns not covered by current thresholds?
- [ ] Peer benchmarking data updated? (industry trends)
- [ ] Seasonal adjustments needed? (Diwali, year-end spending patterns)
- [ ] Thresholds locked & documented?
- [ ] All staff trained on current thresholds?

### **Decision Tree: When to Re-tune**

```
Is FP rate >5%?
├─ Yes: Raise all thresholds by 10%
├─ No: Is detection rate <85%?
    ├─ Yes: Lower thresholds by 5%
    ├─ No: Are there new fraud patterns?
        ├─ Yes: Add targeted tuning for that pattern
        ├─ No: Is it seasonal (e.g., holiday spending)?
            ├─ Yes: Adjust thresholds for season, lock for rest of year
            ├─ No: Thresholds are stable, no tuning needed
```

---

## Common Mistakes & How to Avoid Them

| **Mistake** | **Impact** | **Prevention** |
|---|---|---|
| **Setting all thresholds to "balanced" without testing** | Mismatched FP/detection rates | Test each rule independently; use historical data backtest |
| **Tuning based on complaints alone** | Over-corrections; hidden patterns | Use data (FP rate, detection rate), not just anecdotes |
| **Not adjusting for seasonal patterns** | Spikes in FP during holidays | Build seasonal models; adjust thresholds by season |
| **Forgetting to lock thresholds** | Continuous drift; lost institutional memory | Document all changes; lock quarterly; require CRO approval for changes |
| **Using the same threshold for all account types** | High FP for retail; low detection for business | Build account-type profiles; use different thresholds per type |
| **Not testing threshold changes on historical data first** | Live deployment fails; customer complaints | Always backtest on 30–90 days of historical data before deploying |

---

## Tuning Request Template

**Use this when requesting threshold changes:**

```
Threshold Tuning Request
========================

Rule: [Signal name, e.g., LAY-02]
Current Threshold: [Current value]
Proposed Threshold: [New value]
Reason: [1–2 sentence explanation]
Business Impact: [Expected change in FP rate, detection rate]
Backtest Results:
  - FP rate change: X% → Y%
  - Detection rate change: X% → Y%
  - Sample size: N transactions
  - Period: [date range]
Risk Mitigation: [How will you monitor this change?]
Requested by: [Name]
Approval: [CRO signature]
Effective Date: [Date]
Review Date: [Date, 2–4 weeks later]
```

---

## Conclusion

**Threshold tuning is not a one-time activity** — it's a continuous improvement process. Start conservative, monitor carefully, and adjust quarterly based on data.

**Key takeaway:** The goal is not 0% false positives or 100% detection — it's the **balance** that matches your bank's risk appetite and customer experience standards.

**Questions?** Contact the Fraud Operations Manager or reach out to Fraud360 support for guidance on specific rules or account types.

