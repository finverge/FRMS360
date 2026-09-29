# FMR/STR Immediate Action Plan
## Week 1–2: Emergency Compliance Process (Manual Filing)

**Status:** URGENT - Regulatory Compliance Crisis  
**Timeline:** This Week (2 weeks max)  
**Objective:** Establish manual FMR/STR filing process to prevent regulatory breach  
**SLAs:** FMR within 3 weeks, STR within 7 days

---

## Executive Summary: Why This Matters

**If NOT done immediately:**
- ❌ RBI audit finds unfiledfrauds → enforcement action
- ❌ FIU-IND finds unfiled AML alerts → PMLA violation
- ❌ Criminal liability for bank + individual officers (CRO, CCO)
- ❌ Potential license suspension

**If done correctly:**
- ✅ Compliant with all RBI/FIU-IND requirements
- ✅ RBI inspection ready
- ✅ Audit trail established for 7-year retention
- ✅ Foundation for automated system (Phase 2)

---

## Day-by-Day Action Plan (Week 1)

### **Day 1 (Monday): Emergency Kick-off**

**Morning (9 AM):**

**Action 1.1: CRO/CCO Emergency Meeting (30 min)**
- [ ] Call CRO + CCO + Compliance Head
- [ ] Share this action plan
- [ ] Get verbal approval to proceed
- [ ] Assign FMR/STR coordinator (1 person)

**Action 1.2: FMR Data Extraction (1 hour)**

Use SQL Query 1 from `FMR_STR_SQL_QUERIES.md`:

```sql
-- Extract all frauds from past 3 weeks that aren't yet reported to RBI
SELECT fraud_id, detection_date, fraud_type, amount, customer_name, account_number
FROM fraud_alerts
WHERE DATEDIFF(DAY, detection_date, GETDATE()) <= 21
  AND reported_to_rbi_date IS NULL
  AND confirmation_status = 'CONFIRMED'
ORDER BY detection_date ASC;
```

**What to do with results:**
- [ ] Export to Excel
- [ ] Count total frauds (note this number)
- [ ] Note ages (any >21 days? → URGENT escalation to CRO)
- [ ] Save file: `FMR_Pending_Cases_Week1.xlsx`

**Action 1.3: STR Data Extraction (1 hour)**

Use SQL Query 3 from `FMR_STR_SQL_QUERIES.md`:

```sql
-- Extract all AML alerts from past 7 days that aren't yet filed to FIU
SELECT aml_alert_id, detection_date, suspicion_type, amount, customer_name
FROM aml_alerts
WHERE DATEDIFF(DAY, detection_date, GETDATE()) <= 7
  AND str_filed_date IS NULL
  AND confirmation_status = 'CONFIRMED'
ORDER BY detection_date ASC;
```

**What to do with results:**
- [ ] Export to Excel
- [ ] Count total AML alerts (note this number)
- [ ] Note ages (any >7 days? → CRITICAL - PMLA VIOLATION)
- [ ] Save file: `STR_Pending_Cases_Week1.xlsx`

**Action 1.4: Risk Assessment (30 min)**

Create a simple status table:

```
FRAUD REPORTING STATUS (As of Today)
──────────────────────────────────────────────────────────────
Frauds detected in past 3 weeks:      X cases
Frauds already reported to RBI:       Y cases
Frauds PENDING FMR filing:            X-Y cases (URGENT IF >0)
Frauds >21 days old without FMR:      Z cases (CRITICAL - OVERDUE)

AML ALERTS STATUS
──────────────────────────────────────────────────────────────
AML alerts in past 7 days:            A cases
AML alerts already filed to FIU:      B cases
AML alerts PENDING STR filing:        A-B cases (URGENT IF >0)
AML alerts >7 days old without STR:   C cases (CRITICAL - PMLA BREACH)

REGULATORY RISK LEVEL: [GREEN/YELLOW/RED]
```

**End of Day 1 Deliverables:**
- [ ] Excel files with pending frauds/AML alerts
- [ ] Risk assessment table
- [ ] CRO/CCO approval to proceed

---

### **Day 2 (Tuesday): FMR Manual Process Setup**

**Morning (9 AM):**

**Action 2.1: Gather Fraud Details (2 hours)**

For EACH pending fraud case, collect:

From `fraud_alerts` table:
- Fraud ID
- Detection date
- Fraud type (Digital, Cheque, Card, Loan, Transfer)
- Amount
- Customer ID
- Account number
- Branch code/name
- Modus operandi description
- Action taken (blocked, FIR filed, etc.)
- Current status (Ongoing, Resolved, Under Investigation)

**Create master FMR spreadsheet:**
```
fraud_id | detection_date | fraud_type | amount | customer_masked | account_masked | modus_operandi | action_taken | status
```

**Action 2.2: Create FMR Excel File (1 hour)**

Use `FMR_STR_REPORT_TEMPLATES.md` → **FMR IMMEDIATE FILING TEMPLATE**

**Steps:**
1. [ ] Open RBI FMR template (copy from markdown)
2. [ ] Fill in bank details (name, registration, branch)
3. [ ] For each fraud case:
   - [ ] Enter in row (see PART B of template)
   - [ ] Mask customer name (A***, S***)
   - [ ] Mask account (****5678)
   - [ ] Describe modus operandi (50–200 chars)
   - [ ] Describe action taken (100–300 chars)
4. [ ] Add summary stats (PART C)
   - Count by fraud type
   - Total amount
   - Recovery amount
5. [ ] Obtain signatures (PART G)
   - CRO signature + date
   - CCO signature + date

**Output file:** `FMR_Filing_Week1_YYYY-MM-DD.xlsx`

**Action 2.3: Create RBI Submission Package (1 hour)**

**Prepare for RBI portal submission:**
- [ ] Convert Excel to PDF (for archival)
- [ ] Note RBI portal URL: https://rbi-fraud-portal.rbi.org.in
- [ ] Get RBI login credentials (ask Compliance Officer)
- [ ] Print FMR + get signed copies (physical backup)
- [ ] Create submission checklist (see below)

**FMR RBI Submission Checklist:**
- [ ] All mandatory fields filled
- [ ] All amounts in proper format (₹ with commas)
- [ ] All dates in DD-MMM-YYYY format
- [ ] All customer data masked (no full PAN, Aadhaar, account visible)
- [ ] CRO signature present (original or digital)
- [ ] CCO signature present (original or digital)
- [ ] No sensitive info in modus operandi field
- [ ] Total count matches bank's records
- [ ] Quarterly flag set (if quarter-end)

**End of Day 2 Deliverables:**
- [ ] Master FMR Excel file with all pending cases
- [ ] FMR PDF for submission
- [ ] RBI submission package (ready to upload)

---

### **Day 3 (Wednesday): STR Manual Process Setup**

**Morning (9 AM):**

**Action 3.1: Gather AML Alert Details (2 hours)**

For EACH pending AML alert, collect:

From `aml_alerts` table:
- Alert ID
- Detection date
- Suspicion type (Structuring, Sanctions, PEP, etc.)
- Customer ID & name
- Customer KYC PAN & Aadhaar
- Account number
- Transaction ID & date
- Transaction amount
- Transaction channel (UPI, NEFT, RTGS, etc.)
- Beneficiary details
- Suspicion narrative
- Lane B signal code

**Create master STR spreadsheet:**
```
alert_id | detection_date | suspicion_type | customer_masked | pan_masked | amount | channel | narrative
```

**Action 3.2: Create STR XML File (2 hours)**

Use `FMR_STR_REPORT_TEMPLATES.md` → **STR FILING TEMPLATE (XML/FINnet Format)**

**Steps:**
1. [ ] Open XML template
2. [ ] Fill reporting entity (bank name, ID, registration)
3. [ ] For each AML alert:
   - [ ] Fill customer details (masked: S******, A.M.)
   - [ ] Mask KYC (PAN: ****1234, Aadhaar: ****5678)
   - [ ] Mask account (****9012)
   - [ ] Fill transaction details
   - [ ] Mask beneficiary account (****3456)
   - [ ] Write suspicion narrative (max 500 chars)
   - [ ] Set risk score (0–100)
4. [ ] Add regulatory basis (PMLA Section 29, RBI Direction, Signal Code)
5. [ ] Obtain CCO approval + digital signature

**Output file:** `STR_Filing_Week1_YYYY-MM-DD.xml`

**Action 3.3: Create FIU-IND Submission Package (1 hour)**

**Prepare for FINnet Gateway submission:**
- [ ] Verify XML format (validate against FIU XSD schema)
- [ ] Note FINnet Gateway URL: https://finnet.fiu.gov.in
- [ ] Get FINnet login credentials (ask Compliance Officer)
- [ ] Get CCO's digital certificate (CA-issued for signing)
- [ ] Create submission checklist (see below)

**STR FIU-IND Submission Checklist:**
- [ ] XML is well-formed (valid XML structure)
- [ ] All mandatory fields present
- [ ] All dates in ISO format (YYYY-MM-DD)
- [ ] All amounts numeric (no currency symbols)
- [ ] All customer data masked (no full PAN, Aadhaar, account)
- [ ] Suspicion narrative ≤500 chars
- [ ] Digital signature valid (CCO certificate)
- [ ] Narrative explains WHY suspicious (not just data)
- [ ] Lane B signal code referenced
- [ ] Risk score set (40–100 for filing)

**End of Day 3 Deliverables:**
- [ ] Master STR Excel file with all pending cases
- [ ] STR XML file (ready to submit)
- [ ] FIU-IND submission package (FINnet portal ready)
- [ ] Digital signature verification

---

### **Day 4 (Thursday): Portal Submissions**

**Morning (9 AM):**

**Action 4.1: Submit FMR to RBI Portal (1 hour)**

**Steps:**
1. [ ] Log in to RBI fraud reporting portal
   - URL: https://rbi-fraud-portal.rbi.org.in
   - Credentials: [Compliance Officer provides]
2. [ ] Navigate to "Submit FMR" section
3. [ ] Upload FMR Excel/PDF file
4. [ ] Verify data loads correctly
5. [ ] Review submission summary
6. [ ] Click "SUBMIT"
7. [ ] **Take screenshot of receipt ID** (critical!)
8. [ ] Save receipt email

**Critical Note:** RBI will send confirmation email with receipt ID within 2 hours.

**What to do with receipt:**
- [ ] Note receipt ID: `RBI-FMR-XXXXXX`
- [ ] Log in audit trail table:
  ```
  INSERT INTO fmr_str_audit_trail (
    report_type, submission_date, submitted_by,
    submission_portal, receipt_id, status
  ) VALUES (
    'FMR', NOW(), 'CRO Name',
    'RBI_PORTAL', 'RBI-FMR-XXXXXX', 'SUBMITTED'
  );
  ```

**Action 4.2: Submit STR to FIU-IND FINnet Gateway (1 hour)**

**Steps:**
1. [ ] Log in to FINnet Gateway
   - URL: https://finnet.fiu.gov.in
   - Credentials: [Compliance Officer provides]
2. [ ] Navigate to "File STR" section
3. [ ] Upload STR XML file
4. [ ] System validates XML format
5. [ ] Review beneficiary/transaction details one more time
6. [ ] Verify digital signature (CCO certificate)
7. [ ] Click "SUBMIT"
8. [ ] **Take screenshot of acknowledgment ID** (critical!)
9. [ ] Save acknowledgment receipt

**Critical Note:** FIU-IND will send acknowledgment ID within 30 minutes (sometimes instantly).

**What to do with acknowledgment:**
- [ ] Note acknowledgment ID: `ACK-FIU-2026-08-XXXXXX`
- [ ] Log in audit trail table:
  ```
  INSERT INTO fmr_str_audit_trail (
    report_type, submission_date, submitted_by,
    submission_portal, ack_id, status
  ) VALUES (
    'STR', NOW(), 'CCO Name',
    'FINNET_GATEWAY', 'ACK-FIU-2026-08-XXXXXX', 'SUBMITTED'
  );
  ```

**Action 4.3: Update FRMS Database (1 hour)**

For each filed FMR/STR, update your fraud/AML alert tables:

```sql
-- Mark fraud as reported to RBI
UPDATE fraud_alerts
SET reported_to_rbi_date = GETDATE(),
    fmr_receipt_id = 'RBI-FMR-XXXXXX',
    report_status = 'SUBMITTED'
WHERE fraud_id IN (SELECT fraud_id FROM <submitted_frauds>);

-- Mark AML as filed to FIU
UPDATE aml_alerts
SET str_filed_date = GETDATE(),
    str_ack_id = 'ACK-FIU-2026-08-XXXXXX',
    str_filing_status = 'SUBMITTED'
WHERE aml_alert_id IN (SELECT aml_alert_id FROM <submitted_alerts>);
```

**End of Day 4 Deliverables:**
- [ ] FMR submitted to RBI (receipt ID obtained)
- [ ] STR submitted to FIU-IND (ack ID obtained)
- [ ] FRMS database updated with submission details
- [ ] Audit trail logged (immutable records)

---

### **Day 5 (Friday): Verification & SLA Setup**

**Morning (9 AM):**

**Action 5.1: Verify RBI Acknowledgment (1 hour)**

**Steps:**
1. [ ] Check email for RBI receipt confirmation
2. [ ] Log back into RBI portal → "My Submissions"
3. [ ] Verify FMR status shows "RECEIVED" or "ACCEPTED"
4. [ ] Note any rejection reasons (if applicable)
5. [ ] If rejected: Correct data → Resubmit within 3 days

**Action 5.2: Verify FIU-IND Acknowledgment (1 hour)**

**Steps:**
1. [ ] Check email for FIU acknowledgment
2. [ ] Log back into FINnet → "My STRs"
3. [ ] Verify STR status shows "RECEIVED" or "ACKNOWLEDGED"
4. [ ] Note FIU reference number for future queries
5. [ ] If rejected: Correct issues → Resubmit within 7 days

**Action 5.3: Create Ongoing Monitoring Process (2 hours)**

**Set up weekly SLA checks:**

```sql
-- Weekly FMR SLA Check (run every Friday)
SELECT COUNT(*) as overdue_frauds
FROM fraud_alerts
WHERE DATEDIFF(DAY, detection_date, GETDATE()) > 21
  AND reported_to_rbi_date IS NULL;

-- If overdue_frauds > 0 → ESCALATE to CRO immediately

-- Weekly STR SLA Check (run every Monday, Wednesday, Friday)
SELECT COUNT(*) as overdue_aml_alerts
FROM aml_alerts
WHERE DATEDIFF(DAY, detection_date, GETDATE()) > 7
  AND str_filed_date IS NULL;

-- If overdue_aml_alerts > 0 → ESCALATE to CCO immediately (PMLA VIOLATION)
```

**Action 5.4: Create SLA Dashboard (manual version) (1 hour)**

Until automated system is built, maintain a spreadsheet:

```
FMR/STR SLA TRACKER
─────────────────────────────────────────────
Date    | Type | Cases Pending | Days Old | SLA Status | Action
───────────────────────────────────────────────────────
2026-08-10 | FMR  | 3 cases | 10 days  | ON TIME    | Monitor
2026-08-10 | STR  | 2 cases | 5 days   | ON TIME    | Monitor
```

**Update this daily.**

**End of Day 5 Deliverables:**
- [ ] RBI acknowledgment verified
- [ ] FIU-IND acknowledgment verified
- [ ] Weekly SLA monitoring SQL queries ready
- [ ] Manual SLA tracker spreadsheet created

---

## Week 2: Sustained Operations & Automation Prep

### **Day 6–7 (Monday–Tuesday): Document & Train Team**

**Action 6.1: Create Process Documentation (2 hours)**

Document exactly what you did this week:

```
MANUAL FMR/STR FILING PROCESS
Version 1.0 (2026-08-10)

1. WEEKLY DATA EXTRACTION
   - Every Monday: Run FMR extraction query
   - Every Monday/Wednesday/Friday: Run STR extraction query

2. CASE REVIEW & CLASSIFICATION
   - Verify all cases are confirmed fraud/AML
   - Mask all customer data
   - Prepare modus operandi/narrative

3. FILE GENERATION
   - Generate FMR Excel from template
   - Generate STR XML from template
   - Obtain approvals (CRO for FMR, CCO for STR)

4. PORTAL SUBMISSION
   - Submit FMR to RBI portal by Day 3
   - Submit STR to FINnet by Day 2
   - Track receipt IDs

5. VERIFICATION & LOGGING
   - Verify acknowledgments
   - Update FRMS database
   - Log in audit trail

6. SLA MONITORING
   - Run weekly SLA check
   - Escalate overdue cases immediately
```

**Action 6.2: Train Compliance Team (1 hour)**

**Hold training session:**
- [ ] Invite: CRO, CCO, Compliance Manager, Fraud Analysts
- [ ] Show them:
  - Where to find FMR/STR templates
  - How to extract data
  - How to fill in Excel/XML
  - How to submit to portals
  - What SLA monitoring means
- [ ] Q&A
- [ ] Assign backup person (if primary person unavailable)

**Action 6.3: Set Up Calendar Reminders (30 min)**

Create recurring calendar items:

```
MONDAY @ 9 AM: FMR/STR Weekly Extraction
- Run SQL queries
- Extract pending cases
- Flag any overdue items

WEDNESDAY @ 5 PM: Portal Submissions Check
- Verify RBI portal status
- Verify FINnet status
- Follow up on any rejections

FRIDAY @ 9 AM: SLA Monitoring & Review
- Run SLA check queries
- Update SLA tracker
- Send weekly report to CRO/CCO
```

**End of Week 2 Deliverables:**
- [ ] Process documentation (1-page runbook)
- [ ] Team training completed
- [ ] Calendar reminders set
- [ ] Backup person identified & trained

---

### **Day 8–10 (Wednesday–Friday): First Full Cycle**

**Action 7.1: Execute Full Weekly Cycle (4 hours)**

**Repeat the Week 1 process:**
1. [ ] Extract new frauds & AML alerts
2. [ ] Generate FMR Excel
3. [ ] Generate STR XML
4. [ ] Submit to portals
5. [ ] Verify acknowledgments
6. [ ] Log in database
7. [ ] Update SLA tracker

**This validates your process is repeatable.**

---

## Audit Trail & Compliance Log

**Create this table immediately:**

```sql
CREATE TABLE fmr_str_audit_trail (
    audit_id UUID PRIMARY KEY,
    report_type VARCHAR(10),      -- 'FMR' or 'STR'
    submission_date TIMESTAMP,
    submitted_by_user_id VARCHAR(50),
    submitted_by_role VARCHAR(50),
    submission_portal VARCHAR(50), -- 'RBI_PORTAL' or 'FINNET_GATEWAY'
    receipt_id VARCHAR(100),       -- From RBI or FIU
    ack_id VARCHAR(100),
    authority_response_status VARCHAR(20), -- 'RECEIVED', 'ACCEPTED', 'REJECTED'
    data_classification VARCHAR(100),
    encryption_method VARCHAR(50),
    digital_signature_valid BOOLEAN,
    hash_value VARCHAR(256),       -- SHA-256 for immutability
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP,          -- 7 years after creation
    
    CONSTRAINT immutable_records CHECK (created_at IS NOT NULL)
);

-- Log EVERY FMR/STR filing
INSERT INTO fmr_str_audit_trail VALUES (
    uuid_generate_v4(),
    'FMR',
    NOW(),
    'CRO-NAME',
    'Chief Risk Officer',
    'RBI_PORTAL',
    'RBI-FMR-20260810-001',
    NULL,
    'SUBMITTED',
    'Customer names/accounts masked per RBI guidelines',
    'AES-256',
    TRUE,
    SHA256('fmr_content'),
    NOW(),
    NOW() + INTERVAL '7 years',
    NULL
);
```

---

## Critical SLA Deadlines

| Report | Frequency | Deadline | Action If Missed |
|---|---|---|---|
| **FMR** | Individual cases | 21 days from detection | Escalate to CRO (audit finding likely) |
| **FMR** | Quarterly | Quarter-end + 2 weeks | Escalate to Board (regulatory breach) |
| **STR** | Event-triggered | 7 days from detection | Escalate to CCO (PMLA violation - criminal) |

---

## What If You Find Unfiledfrauds/AML Alerts?

**Scenario A: Fraud detected >21 days ago, not yet reported to RBI**

```
Action:
1. Escalate to CRO immediately
2. Prepare catch-up FMR with all historical frauds
3. Include cover letter explaining delay
4. Submit to RBI with explanation
5. Document in audit trail: "Late filing - catch-up submission"
```

**Scenario B: AML alert detected >7 days ago, not yet filed to FIU**

```
Action (URGENT):
1. Escalate to CCO immediately (PMLA VIOLATION)
2. File STR to FIU within next 24 hours
3. Include note: "Delayed STR filing - retroactive submission"
4. Document in audit trail: "PMLA non-compliance rectified"
5. Inform board of breach + remediation
```

---

## Sign-Off & Approval

**Once Week 1 is complete, obtain:**

- [ ] **CRO Sign-off:** FMR process is working, all frauds reported
- [ ] **CCO Sign-off:** STR process is working, all AML alerts filed
- [ ] **Board Audit Committee Note:** Manual process established, automation in progress

---

## Transition to Automated System (Week 3+)

**As FMR/STR automated system is built (Phase 2):**

1. **Week 1–2:** Manual process (this document)
2. **Week 3–6:** Parallel run (manual + automated both running)
3. **Week 7:** Cutover to fully automated system
4. **Week 8+:** Retire manual process

**During parallel run:**
- Compare manual FMR vs. automated FMR → Validate accuracy
- Compare manual STR vs. automated STR → Validate accuracy
- Once 100% match → Shut down manual process

---

## Key Contacts & Escalation

```
FMR Issues:
  Primary: CRO (Chief Risk Officer)
  Escalate: Board Audit Committee
  RBI Contact: [RBI nodal officer email]

STR Issues:
  Primary: CCO (Chief Compliance Officer)
  Escalate: Board Legal Committee
  FIU-IND Contact: [FIU nodal officer email]

Platform Issues:
  Portal access: [IT Support]
  Database issues: [DBA]
  System questions: [Platform Team]
```

---

## Success Criteria

✅ **Week 1 Complete:**
- [ ] All pending frauds (past 3 weeks) reported to RBI
- [ ] All pending AML alerts (past 7 days) filed to FIU-IND
- [ ] All receipts/acks obtained and logged
- [ ] Audit trail created with immutable logs
- [ ] Team trained on manual process

✅ **Week 2 Complete:**
- [ ] Process documentation finalized
- [ ] First full weekly cycle completed
- [ ] SLA monitoring automated (daily/weekly checks)
- [ ] Calendar reminders active
- [ ] Backup person ready

✅ **By Week 3:**
- [ ] Approval secured for automated system build
- [ ] Development team assigned
- [ ] Parallel manual+automated runs started

---

## Next Steps

1. **TODAY:** Print this document → Share with CRO/CCO
2. **TOMORROW:** Begin Day 1 actions
3. **By Friday:** FMR + STR submitted to portals
4. **By Week 2:** Ongoing manual process running smoothly
5. **Week 3+:** Begin automated system development

---

**URGENT: Do not delay this process. Every day without FMR/STR compliance increases regulatory risk.**

