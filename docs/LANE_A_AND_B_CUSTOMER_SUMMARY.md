# Fraud360: Lane A and Lane B Detection Signals
## Customer Discussion Guide

---

## Executive Summary

Fraud360 uses a **two-lane detection architecture** to balance speed with accuracy:

- **Lane A (Inline):** 9 high-confidence fraud signals answered in 50–150 milliseconds at payment authorization
- **Lane B (Near-real-time):** All 28 signals answered in ~2.65 seconds after settlement, including the full context

**Key insight for your bank:** You don't choose between speed and coverage. Lane A runs inside the payment window; Lane B includes everything Lane A detects *plus* 19 additional checks. A bank using Lane A loses no coverage—it only gains faster answers on the nine highest-confidence attacks.

---

## Lane A: The 9 Real-Time Detection Signals

Each signal detects a specific fraud pattern using **account counters** (pre-computed statistics about the account) or **channel data** (information the mobile app or teller already has). No external lookups. No waiting for settlement.

### 1. VEL-01 · Value Velocity Against Baseline
**What it catches:** A single transaction that is dramatically larger than the customer's own historical pattern.

**Example:** A salary account normally averaging ₹5,000–₹15,000 per transaction suddenly initiates a ₹200,000 transfer (13× the baseline). Fraud360 flags it immediately.

**How it works:** Maintains a rolling 30-day average for each account and flags any payment exceeding a configurable multiple (typically 5–10×). Legitimate spending spike? The customer can be challenged or the rule adjusted by your risk team.

**Customer benefit:** Catches money-mule accounts and account takeovers where large sums are suddenly withdrawn.

---

### 2. VEL-03 · Small-Credit Frequency Spike
**What it catches:** Sudden explosion of micro-transactions below a threshold, often used to test accounts or evade detection.

**Example:** An account that has never transferred more than twice per day suddenly sends 15 transfers of ₹9,999 each within a single hour.

**How it works:** Counts rolling small transfers (below ₹10,000) in a time window and flags when the rate spikes abnormally.

**Customer benefit:** Detects structuring attempts and automated account-draining attacks that rely on high-volume low-value transfers.

---

### 3. SME-01 · Sub-CTR Structuring Count
**What it catches:** Multiple transfers just below the Currency Transaction Report (CTR) filing threshold (₹10 lakh), the classic AML structuring pattern.

**Example:** A borrower makes seven separate ₹9.99 lakh transfers on the same day, totalling ₹69.93 lakh—textbook structuring to evade regulatory reporting.

**How it works:** Counts transactions just under the CTR threshold in a rolling window and flags when the count exceeds a limit (typically 3–5 in 24h).

**Customer benefit:** Catches deliberate attempts to circumvent AML thresholds and triggers mandatory STR filing if suspicious.

---

### 4. SME-02 · PIN-less UPI Structuring
**What it catches:** Rapid-fire micro-UPI transfers below the PIN-less limit (often ₹5,000) that collectively drain a large amount without repeated authentication.

**Example:** A compromised phone sends 20 UPI transfers of ₹5,000 each to different beneficiaries in 10 minutes—₹1 lakh gone without a PIN for each transfer.

**How it works:** Tracks the count and volume of UPI transfers below the PIN-less threshold in a rolling window. A spike indicates account compromise or deliberate fraud.

**Customer benefit:** Closes the UPI micro-transaction attack vector; detects SIM-swap fraud where attackers exploit PIN-less rules.

---

### 5. LAY-01 · Outflow Drain (Layering)
**What it catches:** Nearly all money flowing into an account is immediately funneled out—the hallmark of a money mule or layering account.

**Example:** An account receives ₹10 lakh over a week and within 3 days sends ₹9.8 lakh to 12 different beneficiaries. The outflow ratio is 98%—far above the normal 40% for similar accounts.

**How it works:** Compares outbound transaction volume to inbound volume in a rolling window (typically 7 days) and flags when the ratio exceeds a threshold (usually 80–90%).

**Customer benefit:** Identifies money-mule networks and layering schemes before the account disappears; reduces your exposure to remittance fraud.

---

### 6. LAY-02 · Fan-in/Fan-out Hub (Distinct Counterparties)
**What it catches:** An account with an abnormally high number of unique transacting partners—the signature of a money-collection or distribution hub.

**Example:** A new account suddenly interacts with 47 different beneficiaries in a single week, whereas the peer group average is 3–4. Money flows in from some, out to others, in rapid succession.

**How it works:** Counts distinct counterparties (unique beneficiary accounts) and compares to the account's own baseline or a peer baseline. A sharp rise to 40+ unique payees raises an alert.

**Customer benefit:** Catches collect-and-forward money-laundering hubs before they accumulate critical mass; prevents your accounts from being used as distribution points.

---

### 7. LAY-04 · Device Fingerprint Clustering
**What it catches:** Multiple accounts being accessed from the same device fingerprint (MAC address, device ID, etc.), suggesting fraud orchestration or mass account takeover.

**Example:** A compromised phone accesses 8 different customer accounts within 30 minutes, each initiating a transfer. The device fingerprint is identical across all 8.

**How it works:** Maps device fingerprints to account numbers and counts unique accounts per device. A rise from 1 account to 5+ indicates either fraud or a shared-device scenario (family phone).

**Customer benefit:** Detects botnets and organized fraud rings that compromise multiple accounts; protects your entire customer base from coordinated attacks.

---

### 8. BEH-01 · Dormancy on Large Payment
**What it catches:** An account that has been inactive suddenly makes a high-value payment—often the account has been compromised after months of sitting idle.

**Example:** A salaried customer's account sees no transactions for 8 months, then suddenly initiates a ₹50,000 transfer. An attacker just activated it.

**How it works:** Measures the time gap since the account's last transaction and flags high-value payments arriving after extended dormancy (typically 3+ months).

**Customer benefit:** Catches old, neglected accounts being reactivated by fraudsters; reduces your fraud exposure from zombie accounts.

---

### 9. CHN-01 · New Device + New Payee + High Value
**What it catches:** A payment from an unfamiliar device to an unfamiliar beneficiary, in a large amount—the classic account-takeover signature.

**Example:** A customer's phone is stolen. The thief immediately sends ₹30,000 to a new account never seen before. Three red flags at once: device is new, payee is new, amount is high.

**How it works:** The mobile app or channel supplies this observation directly (it's not computed). A combined score of new-device + new-payee + high-value factors triggers a decline or challenge.

**Customer benefit:** Stops account takeover in real time by flagging the suspicious usage pattern immediately, before funds leave the account.

---

## Lane B: The Additional 19 Detection Signals

Lane B runs after settlement and includes all 9 Lane A signals *plus* 19 more. These additional signals require:
- **External data feeds** (sanctions lists, trade records, geolocation)
- **Post-settlement facts** (CBS repayment data, collateral records)
- **Signals that only form over time** (circular money flows, beneficiary history)

### Why Lane B is NOT slower coverage—it's *more comprehensive* coverage

**Lane B adds detection for:**

#### Counterparty & Sanctions Signals (CPT family)
- **CPT-01 & VEL-02:** Beneficiary age (new-payee risk). Uses first-seen timestamps—a set store that cannot exist without false alarms in the 50ms authorization window.
- **CPT-02:** Sanctions/PEP screening. Requires OFAC and PEP list lookups—not loaded in the real-time payment path.
- **CPT-03:** CERSAI charge registry. Requires external registry queries to check for undisclosed liens.

**Customer value:** Stops payments to sanctioned entities, PEPs, and accounts with hidden charges/liens—compliance and reputational protection.

---

#### Channel & Geolocation (CHN-02, CHN-03)
- **CHN-02:** Geolocation anomaly. Detects payments from devices in high-risk territories or inconsistent with account behavior.
- **CHN-03:** Off-hours activity. Flags transactions at times when the account holder never normally transacts.

**Customer value:** Catches geographic anomalies and unusual access patterns that signal SIM swap or account compromise.

---

#### Loan Conduct & CBS Signals (CBS family)
- **CBS-01:** Disbursal-to-withdrawal deviation. Flags loans being immediately repaid or diverted before deployment—sign of loan fraud.
- **CBS-02:** Collateral & security inconsistency. Detects missing or transferred pledged assets not disclosed to the bank.
- **CBS-03:** Sale proceeds misrouting. Catches asset-sale proceeds being sent to unauthorized accounts instead of repayment.

**Customer value:** Protects your credit portfolio from loan fraud, collateral fraud, and proceeds misrouting.

---

#### Layering & Circularity (LAY-03)
- **LAY-03:** Circular/multi-hop money flow. Detects round-tripping where funds move in circles (A → B → C → A) to obscure origin.

**Customer value:** Stops multi-account money-laundering schemes that would pass single-account detection.

---

#### Behavioral & End-Use (BEH-02, BEH-03)
- **BEH-02:** Repayment source mismatch. Flags loan repayments from unauthorized parties—sign of account takeover or fraud.
- **BEH-03:** End-use deviation. Detects loan funds being diverted from stated purpose (working-capital loan going to real-estate investment).

**Customer value:** Enforces loan covenants and detects fraud within the credit life cycle.

---

#### Structuring & Trade (SME-03, TBM family)
- **SME-03:** Invoice-level trade structuring. Detects over/under-invoicing in trade flows to disguise money movement.
- **TBM-01, TBM-02, TBM-03:** Trade-based money laundering. Catches phantom shipments, double-invoicing, LC fraud.

**Customer value:** Protects trade finance operations from invoice manipulation and cross-border AML abuse.

---

#### Qualitative Signals (QUAL family)
- **QUAL-01, QUAL-02, QUAL-03:** Qualitative credit indicators. Credit monitoring team flags adverse media, management concerns, regulatory notices.

**Customer value:** Incorporates human expertise and external intelligence into automated detection.

---

## Comparison: Lane A vs. Lane B vs. Both

| **Metric** | **Lane A Only** | **Lane B Only** | **Lane A + Lane B** |
|-----------|-----------------|-----------------|-------------------|
| **Decision latency** | 50–150 ms | ~2.65 seconds | Both speeds available |
| **Fraud signals covered** | 9 out of 28 | 28 out of 28 | 28 out of 28 |
| **Coverage loss?** | No | No | No |
| **Speed on high-confidence attacks?** | Yes | No | Yes |
| **Sanctions screening?** | No | Yes | Yes |
| **Loan covenant enforcement?** | No | Yes | Yes |
| **Post-settlement AML?** | No | Yes | Yes |

**The key insight:** Using Lane A alone does NOT reduce fraud coverage—it reduces *latency*. You get 9 out of 28 signals in real time, and the 9 you get are the highest-confidence ones. Lane B gives you all 28 later.

---

## Real-World Scenarios: How Lanes A and B Work Together

### Scenario 1: Account Takeover via SIM Swap

**T+0 ms (Authorization):**
- Attacker sends ₹30,000 to new payee from new device.
- **Lane A catches it:** CHN-01 (new device + new payee + high value) = **DECLINE in 80ms**.
- Payment blocked at authorization. Customer kept safe.

**T+2.65 seconds (Settlement):**
- Lane B would also catch this with CHN-02 (geolocation anomaly) and CHN-03 (off-hours access).
- If Lane A had somehow missed it, Lane B would catch it.

---

### Scenario 2: Money Mule Network

**T+0 ms (Authorization):**
- Mule Account A receives a ₹10 lakh transfer.
- **Lane A catches it?** Not yet—we don't know about the pattern yet.

**T+1 hour:**
- Mule Account A sends ₹9.8 lakh to 12 different beneficiaries.
- **Lane A catches it on each outbound payment:** LAY-02 (distinct counterparties) flags 12+ unique payees. Multiple transfers DECLINED or CHALLENGED.

**T+2.65 seconds after each outbound (Lane B):**
- Lane B confirms with LAY-01 (outflow drain—98% ratio, way too high) and LAY-04 (device fingerprint clustering if multiple mule accounts are being accessed from one device).

---

### Scenario 3: Trade Finance Fraud (Over-Invoicing)

**T+0 ms (Authorization):**
- Import LC for ₹10 lakh is received. Payment authorized.
- **Lane A:** No signal—value looks normal for this account.

**T+2.65 seconds (Settlement, Lane B):**
- **TBM-01 catches it:** Invoice details show ₹5 lakh—invoice is 50% of stated LC value. Over-invoicing detected.
- Alert raised, transaction recorded for investigation, possible reversal recommended.

---

### Scenario 4: Structuring (Sub-CTR)

**T+0 ms to T+60 minutes:**
- Structuring fraudster sends 5 transfers of ₹9.99 lakh each.
- **Lane A catches it on transfer #3 or #4:** SME-01 (sub-CTR count) triggers—threshold exceeded. Remaining transfers DECLINED.

**T+2.65 seconds after each (Lane B):**
- Lane B confirms structuring pattern and can trigger mandatory STR (Suspicious Transaction Report) filing.

---

## Implementation Recommendations

### **If you prioritize speed (fintech, card payments):**
Enable Lane A for **UPI, IMPS, and Card**. Decline fraudulent payments in 50–150 ms. Lane B runs in background for secondary investigation and AML reporting.

### **If you prioritize coverage (core banking, high-value transfers):**
Run Lane B for **all rails**. Accept ~2.65 second latency in exchange for all 28 signals and full context.

### **If you want both (recommended for most banks):**
Run Lane A on **UPI, IMPS, Card** (customer-facing, low latency needed).
Run Lane B on **NEFT, RTGS, all inter-bank transfers** (higher latency acceptable, higher fraud risk).

---

## Risk Tiers: Which Signals Fire at Which Severity?

**Critical (decline immediately in Lane A):**
- CHN-01 (new device + new payee + high value) on debit cards
- LAY-02 (40+ counterparties) on any rail
- SME-01 (5+ sub-CTR transfers in 24h)

**High (challenge or review):**
- VEL-01 (value 5–10× baseline)
- LAY-01 (outflow 80–90%)
- BEH-01 (high-value on dormant account)

**Medium (investigate post-settlement):**
- CPT-02 (sanctions screening, Lane B)
- CBS-01 (loan disbursal deviation, Lane B)
- CHN-03 (off-hours activity, Lane B)

**Low/Monitoring:**
- QUAL signals (qualitative inputs from credit team)
- TBM signals (trade finance details, Lane B only)

---

## Summary: Why This Architecture Matters for Your Bank

| **Your Goal** | **Why Fraud360's Two Lanes Win** |
|--------------|----------------------------------|
| Stop fraud before it leaves | Lane A catches 9 high-confidence attacks in 50–150 ms—faster than most competitors. |
| Don't decline legitimate customers | Lane A signals are low-false-positive (account baseline, channel data). Lane B adds 19 more for comprehensive AML. |
| Comply with RBI 2024 EWS rules | We map all 42 RBI illustrative signals. 12 are Lane B signals (post-settlement AML). 15 are Lane C (roadmap, credit-file review). |
| Reduce manual review burden | Lane B runs automatically; alerts go to your risk team pre-scored with evidence. |
| Detect sophisticated fraud | Circularity detection (LAY-03), trade fraud (TBM), loan fraud (CBS), sanctions (CPT-02) all in Lane B. |

---

## Next Steps for Your Bank

1. **Review this document** with your risk, compliance, and operations teams.
2. **Decide on rail configuration:** Lane A for UPI/IMPS/Card? Lane B for NEFT/RTGS?
3. **Discuss threshold customization** with Fraud360. Each rule's thresholds (e.g., "5× baseline" for VEL-01) can be tuned to your risk appetite.
4. **Plan rollout:** Shadow mode for Week 1–2, then gradual enforcement.
5. **Integrate with your STR/FMR process:** Lane B alerts should feed directly into your compliance reporting.

---

## Questions? Key Points to Discuss with Fraud360

- Which Lane A signals are most relevant to your customer base?
- Can we tune thresholds for your specific risk profile?
- How do the signals integrate with your existing AML/CFT systems?
- What's the SLA for alert investigation and case resolution?
- How are false positives handled? (Challenge, review, or soft decline?)

