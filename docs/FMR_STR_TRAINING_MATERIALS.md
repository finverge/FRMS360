# FMR/STR Training Materials
## Compliance Team Onboarding & Ongoing Training

**Objective:** Train staff on FMR/STR processes, regulatory requirements, and system usage  
**Audience:** Fraud analysts, compliance officers, operations team  
**Duration:** 2-day training program (or self-paced online)  
**Certification:** Mandatory for all staff handling FMR/STR

---

## Training Program Overview

### **Module 1: Regulatory Framework (90 minutes)**
- What is FMR and why it matters?
- What is STR and why it matters?
- RBI vs. FIU-IND requirements
- PMLA compliance and criminal liability

### **Module 2: FMR Process (2 hours)**
- FMR workflow step-by-step
- Data extraction from FRMS
- FMR Excel template completion
- RBI portal submission
- Receipt tracking and audit trail

### **Module 3: STR Process (2 hours)**
- STR workflow step-by-step
- AML alert classification
- STR XML generation
- FIU-IND FINnet submission
- Acknowledgment tracking and compliance

### **Module 4: Data Privacy & Masking (1 hour)**
- PII masking requirements (PAN, Aadhaar, account, name)
- What NOT to include in FMR/STR
- Data security best practices
- Audit trail maintenance

### **Module 5: Tools & Systems (1.5 hours)**
- Dashboard navigation
- SLA monitoring
- Portal access and login
- Escalation procedures
- System troubleshooting

### **Module 6: Compliance & Audit (1 hour)**
- SLA deadlines and penalties
- What RBI/FIU-IND audits
- How to prepare for audit
- Common audit findings and how to avoid them

---

## Module 1: Regulatory Framework

### **Lesson 1.1: What is FMR?**

**Definition:**
> FMR (Fraud Monitoring Return) is a mandatory report filed to RBI whenever a fraud is detected. It documents the fraud details, action taken, and recovery status.

**Why Required:**
- RBI needs visibility into fraud across banking system
- Helps RBI identify fraud trends
- Enables RBI to take preventive actions
- Regulatory compliance requirement

**Who Must File:**
- Banks
- NBFCs (Non-Banking Financial Companies)
- PACS (Primary Agricultural Credit Societies)

**Deadline:**
- **Individual cases:** Within 3 weeks (21 days) of detection
- **Quarterly consolidation:** Within 2 weeks post quarter-end

**Penalty for Non-Compliance:**
- Audit finding
- RBI enforcement action (monetary penalty)
- License suspension risk (extreme case)

---

### **Lesson 1.2: What is STR?**

**Definition:**
> STR (Suspicious Transaction Report) is a mandatory report filed to FIU-IND whenever an AML (money laundering) or CFT (terrorist financing) suspicion is detected.

**Why Required:**
- PMLA 2002 Section 29 mandates STR filing
- FIU-IND needs data for financial crime investigation
- Helps identify money laundering networks
- Criminal compliance requirement (not just regulatory)

**Who Must File:**
- Banks
- NBFCs
- Payment processors
- Money changers
- Real estate dealers

**Deadline:**
- **Within 7 days** of detecting suspicion (PMLA Section 29)
- No extension possible
- Violation = criminal liability for CCO/officers

**Penalty for Non-Compliance:**
- PMLA criminal prosecution
- Fine: ₹1 lakh – ₹5 lakhs per violation
- Imprisonment: Up to 10 years (Section 3, PMLA)
- Bank can be prosecuted as entity + individual officers

---

### **Lesson 1.3: RBI vs. FIU-IND Requirements**

| Aspect | FMR (RBI) | STR (FIU-IND) |
|---|---|---|
| **Report Type** | Fraud cases | AML/CFT suspicions |
| **Authority** | Reserve Bank of India | Financial Intelligence Unit - India |
| **Regulation** | RBI Master Directions 2024-25 | PMLA 2002 Section 29 |
| **Deadline** | 21 days | 7 days |
| **Portal** | RBI Fraud Reporting Portal | FINnet Gateway |
| **Format** | Excel/Online form | XML (digitally signed) |
| **Penalty** | Regulatory (RBI enforcement) | Criminal (CBI/ED prosecution) |
| **Severity** | High | CRITICAL (Criminal) |

---

### **Lesson 1.4: Criminal Liability Under PMLA**

**Who is Liable?**
- Bank (as entity)
- Chief Compliance Officer (CCO)
- Chief Risk Officer (CRO)
- Any officer involved in non-compliance

**What is the Crime?**
- Failing to file STR within 7 days (Section 29 violation)
- Tipping off customer (warning them about STR)
- Destroying evidence

**Penalty:**
- **Minimum:** ₹1 lakh fine + 3-year imprisonment
- **Maximum:** ₹5 lakh fine + 10-year imprisonment
- **Attachment:** Bank assets can be seized
- **Prosecution:** By CBI or Enforcement Directorate (ED)

**Important:** This is NOT just a regulatory penalty—it's criminal prosecution.

---

### **Quiz 1: Regulatory Framework**

**Q1:** What is the deadline for filing FMR?  
**A1:** 3 weeks (21 days) from fraud detection

**Q2:** What is the deadline for filing STR?  
**A2:** 7 days from AML suspicion (PMLA Section 29)

**Q3:** Who faces criminal liability if STR is not filed?  
**A3:** CCO, CRO, and any officer involved in non-compliance

**Q4:** Which authority receives FMR?  
**A4:** RBI (Reserve Bank of India)

**Q5:** Which authority receives STR?  
**A5:** FIU-IND (Financial Intelligence Unit - India)

---

## Module 2: FMR Process

### **Lesson 2.1: FMR Workflow Step-by-Step**

```
Step 1: Fraud Detection
  ↓ (Fraud alert created in FRMS)
Step 2: Fraud Confirmation
  ↓ (Fraud analyst investigates, confirms fraud)
Step 3: Data Extraction
  ↓ (Query FRMS for fraud details, customer info)
Step 4: FMR Excel Generation
  ↓ (Populate RBI template with fraud data)
Step 5: Data Masking Verification
  ↓ (Verify PAN, Aadhaar, account are masked)
Step 6: CRO Signature
  ↓ (CRO reviews and signs FMR)
Step 7: RBI Portal Submission
  ↓ (Upload FMR Excel to RBI portal)
Step 8: Receipt Tracking
  ↓ (RBI sends receipt ID within 2 hours)
Step 9: Audit Trail Logging
  ↓ (Log receipt ID in immutable audit trail)
Step 10: Database Update
  ↓ (Mark fraud as "reported_to_rbi" in FRMS)
```

---

### **Lesson 2.2: Data Extraction**

**What Data to Extract:**

From `fraud_alerts` table:
- Fraud ID
- Detection date
- Fraud type (Digital, Cheque, Card, Loan, Transfer)
- Amount
- Status (Ongoing, Resolved, Under Investigation)
- Modus operandi
- Action taken

From `customers` table:
- Customer name (masked for report)
- PAN (masked: last 4 digits only)
- Aadhaar (masked: last 4 digits only)

From `accounts` table:
- Account number (masked: last 4 digits only)
- Account type
- Branch code

**SQL Query:**

```sql
-- Fraud data extraction for FMR
SELECT
  f.fraud_id, f.detection_date, f.fraud_type, f.fraud_amount,
  c.customer_name, c.kyc_pan, c.kyc_aadhaar,
  a.account_number, a.account_type,
  b.branch_code, b.branch_name,
  f.modus_operandi, f.action_taken, f.status,
  f.amount_recovered
FROM fraud_alerts f
JOIN customers c ON f.customer_id = c.customer_id
JOIN accounts a ON f.account_id = a.account_id
JOIN branches b ON c.branch_id = b.branch_id
WHERE DATEDIFF(DAY, f.detection_date, GETDATE()) <= 21
  AND f.reported_to_rbi_date IS NULL
  AND f.confirmation_status = 'CONFIRMED'
ORDER BY f.detection_date ASC;
```

---

### **Lesson 2.3: FMR Excel Template Completion**

**Steps:**

1. **Open RBI FMR Template** (from `FMR_STR_REPORT_TEMPLATES.md`)

2. **Fill Bank Details:**
   - Bank Name: Finverge Bank Ltd
   - Registration: RBI-BANKING-2023-00123
   - Submission Date: Today

3. **Add Fraud Cases** (one per row):
   - Branch Code: (from database)
   - Detection Date: (formatted as DD-MMM-YYYY)
   - Fraud Type: (Digital / Cheque / Card / Loan / Transfer)
   - Amount: (formatted with ₹ and commas)
   - Account: (masked, e.g., ****5678)
   - Customer: (masked, e.g., A****, S****)
   - Modus Operandi: Explain HOW the fraud happened (50–200 chars)
   - Action Taken: What did we do about it (100–300 chars)
   - Status: (Ongoing / Resolved / Under Investigation)

4. **Calculate Summary:**
   - Count cases by type
   - Sum total amount
   - Sum recovery amount

5. **Obtain Signature:**
   - CRO signature + date (required)
   - CCO signature (optional but recommended)

6. **Save and Archive:**
   - File name: `FMR_Filing_YYYY-MM-DD.xlsx`
   - Location: `\\[shared]\Compliance\FMR\`

---

### **Lesson 2.4: Data Masking (Critical!)**

**Masking Rules (Mandatory):**

| Data | Masking Rule | Example |
|---|---|---|
| **PAN** | Show only last 4 digits | Full: 1234ABCD5678 → ****5678 |
| **Aadhaar** | Show only last 4 digits | Full: 123456789012 → ****9012 |
| **Account Number** | Show first 4 and last 4 | Full: 1234567890123456 → 1234****3456 |
| **Customer Name** | First letter + surname | Full: John Doe Smith → J****, D** |
| **Email** | Mask domain | Full: john@email.com → john****@email.com |
| **Phone** | Show only last 4 | Full: +91-9876543210 → +91-****3210 |
| **Address** | Show state/city only | Show: Mumbai, Maharashtra (no street) |

**Common Mistakes (DON'T DO THIS):**

❌ Including full PAN in FMR  
❌ Including full Aadhaar in FMR  
❌ Including full customer name in FMR  
❌ Including full account number in FMR  
❌ Including customer personal details (DOB, address)  

**Verification Checklist:**

Before submitting FMR:
- [ ] All PANs show only last 4 digits
- [ ] All Aadhaars show only last 4 digits
- [ ] All accounts show only first 4 + last 4
- [ ] All names are surname format only
- [ ] No street addresses visible
- [ ] No personal emails/phones visible

---

### **Lesson 2.5: RBI Portal Submission**

**Step-by-Step:**

1. Log in to RBI Portal:
   - URL: https://rbi-fraud-portal.rbi.org.in
   - Username: [Provided by RBI]
   - Password: [Stored securely]

2. Navigate to "Submit FMR"

3. Upload FMR Excel file

4. Verify data loads correctly

5. Review submission summary

6. Click "SUBMIT"

7. **Take screenshot of receipt ID**

8. Save confirmation email

9. Note receipt ID: Format is RBI-FMR-YYYYMMDD-00001

---

### **Lesson 2.6: Receipt Tracking & Audit Trail**

**What to Do After Submission:**

1. **Receive Receipt ID:** RBI sends within 2 hours
   - Example: RBI-FMR-20260810-001

2. **Log in Database:**
   ```sql
   UPDATE fraud_alerts
   SET reported_to_rbi_date = GETDATE(),
       fmr_receipt_id = 'RBI-FMR-20260810-001',
       report_status = 'SUBMITTED'
   WHERE fraud_id IN (...);
   ```

3. **Log in Audit Trail:**
   ```sql
   INSERT INTO fmr_str_audit_trail (
     report_type, submission_date, receipt_id, status
   ) VALUES ('FMR', GETDATE(), 'RBI-FMR-20260810-001', 'SUBMITTED');
   ```

4. **Archive Receipt:**
   - Save email confirmation
   - Save screenshot
   - Keep for 7-year audit trail

---

### **Quiz 2: FMR Process**

**Q1:** What is the format for FMR files?  
**A1:** Excel (.xlsx), using RBI template

**Q2:** What is the data masking rule for PAN?  
**A2:** Show only last 4 digits (****1234)

**Q3:** Who must sign the FMR?  
**A3:** CRO (Chief Risk Officer)

**Q4:** How long to get receipt from RBI?  
**A4:** Within 2 hours of submission

**Q5:** Where should FMR receipt ID be logged?  
**A5:** In fmr_str_audit_trail table (immutable 7-year retention)

---

## Module 3: STR Process

### **Lesson 3.1: STR Workflow Step-by-Step**

```
Step 1: AML Alert Detection
  ↓ (Lane B signal triggered: CPT-01, CPT-02, etc.)
Step 2: Alert Confirmation
  ↓ (Compliance team verifies AML suspicion)
Step 3: Data Extraction
  ↓ (Query FRMS for AML alert details, customer, transaction)
Step 4: STR XML Generation
  ↓ (Populate FIU-IND XML template with AML data)
Step 5: Data Masking Verification
  ↓ (Verify PAN, Aadhaar, account are masked)
Step 6: Digital Signature
  ↓ (CCO signs STR XML with digital certificate)
Step 7: FINnet Gateway Submission
  ↓ (Upload STR XML to FIU-IND FINnet portal)
Step 8: Acknowledgment Tracking
  ↓ (FIU-IND sends ack ID within 30 minutes)
Step 9: Audit Trail Logging
  ↓ (Log ack ID in immutable audit trail)
Step 10: Database Update
  ↓ (Mark AML alert as "str_filed" in FRMS)
```

---

### **Lesson 3.2: AML Alert Classification**

**Suspicion Types (Map Lane B Signals to STR Type):**

| Lane B Signal | Suspicion Type | Example |
|---|---|---|
| CPT-01 | SANCTIONS | Payment to OFAC-listed entity |
| CPT-02 | PEP | Transaction with Politically Exposed Person |
| CPT-03 | SANCTIONS + PEP | Combined risk |
| SME-01 | STRUCTURING | Multiple sub-₹10L transfers in 24h |
| SME-03 | INVOICE_FRAUD | Invoice amount mismatches in trade finance |
| LAY-03 | LAYERING | Circular flows between linked accounts |
| TBM-02 | TRADE_FINANCE | Trade-based money laundering |
| CHN-02 | GEOLOCATION | Transaction from unusual country |
| CHN-03 | GEOLOCATION | Transaction at unusual time of day |

---

### **Lesson 3.3: STR XML Template Completion**

**Manual Process:**

1. Open XML template (from `FMR_STR_REPORT_TEMPLATES.md`)

2. Fill Reporting Entity:
   ```xml
   <ReportingEntity>
     <EntityName>Finverge Bank Ltd</EntityName>
     <EntityID>BANK123456</EntityID>
   </ReportingEntity>
   ```

3. Fill Customer (MASKED):
   ```xml
   <Customer>
     <CustomerName>A****, S***</CustomerName>
     <PAN>****1234</PAN>
     <Aadhaar>****5678</Aadhaar>
   </Customer>
   ```

4. Fill Transaction:
   ```xml
   <Transaction>
     <TransactionID>TXN-2026-00456</TransactionID>
     <Amount>999000.00</Amount>
     <Channel>UPI</Channel>
   </Transaction>
   ```

5. Fill Suspicion:
   ```xml
   <Suspicion>
     <Type>STRUCTURING</Type>
     <Narrative>Account received ₹9.99L; immediately transferred to 5 different accounts. Pattern consistent with CTR evasion.</Narrative>
     <RegulatoryBasis>SME-01</RegulatoryBasis>
   </Suspicion>
   ```

6. Validate XML format (well-formed, no errors)

7. Obtain CCO signature

---

### **Lesson 3.4: Digital Signature (Critical for FIU-IND)**

**Why Digital Signature Required?**
- FIU-IND needs authentication (STR is legally binding)
- Prevents tampering or forgery
- CCO takes legal responsibility for STR filing

**How to Sign STR XML:**

1. **Procure CCO Certificate:**
   - Get CA-issued digital certificate
   - Valid for 3+ years
   - Stored securely in HSM (Hardware Security Module)

2. **Sign XML:**
   - Use cryptographic tool (or Python module provided)
   - Sign entire XML content with SHA-256 hash
   - Output: Base64-encoded signature

3. **Embed Signature:**
   - Add signature block to XML
   - Verify signature before submission

4. **Store Certificate:**
   - Keep certificate for audit trail
   - Renew before expiry

---

### **Lesson 3.5: FINnet Gateway Submission**

**Step-by-Step:**

1. Log in to FINnet Gateway:
   - URL: https://finnet.fiu.gov.in
   - Username: [Provided by FIU-IND]
   - Password: [Stored securely]

2. Navigate to "File STR"

3. Upload STR XML file

4. System validates XML format

5. Verify digital signature

6. Review case details one more time

7. Click "SUBMIT"

8. **Take screenshot of acknowledgment ID**

9. Save confirmation email

10. Note ack ID: Format is ACK-FIU-2026-08-00001

---

### **Lesson 3.6: Acknowledgment Tracking & Audit Trail**

**What to Do After Submission:**

1. **Receive Acknowledgment ID:** FIU-IND sends within 30 minutes
   - Example: ACK-FIU-2026-08-00001

2. **Log in Database:**
   ```sql
   UPDATE aml_alerts
   SET str_filed_date = GETDATE(),
       str_ack_id = 'ACK-FIU-2026-08-00001',
       str_filing_status = 'SUBMITTED'
   WHERE aml_alert_id IN (...);
   ```

3. **Log in Audit Trail:**
   ```sql
   INSERT INTO fmr_str_audit_trail (
     report_type, submission_date, ack_id, status
   ) VALUES ('STR', GETDATE(), 'ACK-FIU-2026-08-00001', 'SUBMITTED');
   ```

4. **Archive Acknowledgment:**
   - Save email confirmation
   - Save screenshot
   - Keep for 7-year audit trail

---

### **Lesson 3.7: Handling Rejected STRs**

**If FIU-IND Rejects STR:**

1. **Read rejection message carefully**

2. **Common Reasons:**
   - Invalid XML format (malformed)
   - Missing mandatory fields
   - Incorrect data types
   - Invalid digital signature

3. **Corrective Steps:**
   - Fix identified issues
   - Revalidate XML
   - Re-sign if necessary
   - Resubmit within 24 hours

4. **Document Rejection:**
   - Note rejection reason
   - Log in audit trail
   - Escalate to CCO if repeated rejections

---

### **Quiz 3: STR Process**

**Q1:** What is the deadline for STR filing?  
**A1:** 7 days from AML suspicion (PMLA Section 29)

**Q2:** What format is STR filed in?  
**A2:** XML (Extensible Markup Language), digitally signed

**Q3:** Who must sign the STR?  
**A3:** CCO (Chief Compliance Officer) with digital certificate

**Q4:** Which portal is used for STR submission?  
**A4:** FINnet Gateway (https://finnet.fiu.gov.in)

**Q5:** How long to get acknowledgment from FIU-IND?  
**A5:** Within 30 minutes (sometimes instantly)

---

## Module 4: Data Privacy & Masking

### **Masking Best Practices**

**MUST Mask:**
- PAN (last 4 only)
- Aadhaar (last 4 only)
- Account number (first 4 + last 4)
- Customer name (surname format)
- Email addresses
- Phone numbers
- Residential addresses
- DOB

**DO NOT Include:**
- Customer religion/caste
- Marital status
- Medical information
- Criminal records
- Private family info
- Internal bank notes (sensitive)

---

### **Quiz 4: Data Privacy**

**Q1:** How should PAN be masked?  
**A1:** Show only last 4 digits (****1234)

**Q2:** Can we include customer's date of birth in FMR/STR?  
**A2:** No, it must be masked/excluded

**Q3:** Is it OK to include branch manager's notes?  
**A3:** No, only facts (detection, modus operandi, action taken)

---

## Module 5: Tools & Systems

### **Lesson 5.1: Dashboard Navigation**

**Dashboard Access:**
- Location: `\\[shared]\Compliance\FMR_STR\SLA_Dashboard.xlsx`
- Update frequency: Daily (automated)
- Read access: CRO, CCO, Compliance, Audit Committee
- Write access: Compliance Manager only

**What to Look For:**

1. **FMR Tracker Sheet:**
   - Green = Filed on time ✅
   - Yellow = Pending (≤3 days to deadline) ⚠️
   - Red = Overdue (>deadline) 🔴

2. **STR Tracker Sheet:**
   - Green = Filed on time ✅
   - Orange = Pending (1–2 days to deadline) ⚠️
   - Red = Overdue (>7 days) 🔴 PMLA VIOLATION

3. **Summary Dashboard:**
   - Compliance score (target: 100%)
   - Overdue cases count (target: 0)
   - Filing trends

---

### **Lesson 5.2: SLA Monitoring & Alerts**

**What are SLA Violations?**

| Violation | What | Deadline | Penalty |
|---|---|---|---|
| **FMR Overdue** | Fraud not filed >21 days | 21 days | RBI audit finding |
| **STR Overdue** | AML alert not filed >7 days | 7 days | PMLA criminal liability |

**How to Handle Overdue Cases:**

1. **Check Dashboard Alerts**
   - Automated alerts sent to CRO/CCO
   - Subject: "SLA VIOLATION: X cases overdue"

2. **Immediate Action (within 24 hours):**
   - Generate FMR/STR for overdue cases
   - Obtain signatures
   - File to RBI/FIU-IND
   - Send explanation letter
   - Log in audit trail

3. **Escalation:**
   - Notify board if breach is significant
   - Consider self-disclosure to RBI/FIU-IND
   - Implement preventive measures

---

### **Lesson 5.3: Portal Access**

**RBI Fraud Portal:**
- URL: https://rbi-fraud-portal.rbi.org.in
- Access: CRO, Compliance Manager
- Credentials: [Stored securely - ask IT]
- First-time setup: 2FA (two-factor auth required)

**FINnet Gateway:**
- URL: https://finnet.fiu.gov.in
- Access: CCO, Compliance Manager
- Credentials: [Stored securely - ask IT]
- First-time setup: Digital certificate enrollment required
- Technical support: fiu@fiu.gov.in

**Troubleshooting Portal Access:**

| Problem | Solution |
|---|---|
| Can't log in | Reset password (email recovery link) |
| Portal down | Check status page, escalate to RBI IT |
| Upload fails | Check file format (.xlsx for RBI, .xml for FIU) |
| Receipt not received | Wait 2 hours, check spam email, escalate to RBI |

---

### **Lesson 5.4: System Troubleshooting**

**Common Issues & Fixes:**

| Issue | Cause | Fix |
|---|---|---|
| **Database connection error** | DB offline or credentials wrong | Contact DBA, check connection string |
| **FMR Excel generation fails** | Template missing or corrupted | Re-download template from shared drive |
| **XML validation fails** | Malformed XML (missing tags) | Use XML validator tool, check against schema |
| **Digital signature invalid** | Certificate expired | Renew CCO certificate with CA |
| **Dashboard won't update** | Scheduled job failed | Check APScheduler logs, restart service |

---

## Module 6: Compliance & Audit

### **Lesson 6.1: What RBI Audits Check**

**RBI Inspection Checklist:**

During RBI inspection, auditors will verify:

- [ ] All frauds >₹500K have FMR filed within 3 weeks
- [ ] Quarterly FMR consolidations submitted
- [ ] RBI receipts/acknowledgments retained for all FMRs
- [ ] Sample FMRs show proper data masking
- [ ] Process documentation available
- [ ] CRO signature on all FMRs
- [ ] No frauds older than 3 months without filing
- [ ] Audit trail logs exist for 7-year retention

---

### **Lesson 6.2: What FIU-IND Audits Check**

**FIU-IND Inspection Checklist:**

During FIU-IND inspection, auditors will verify:

- [ ] All AML alerts have STR filed within 7 days
- [ ] FIU acknowledgments retained for all STRs
- [ ] Sample STRs show proper data masking
- [ ] Digital signatures are valid
- [ ] No AML alert older than 30 days without filing
- [ ] STR quality: narrative explains suspicion clearly
- [ ] Process documentation available
- [ ] CCO signature and certificate on record

---

### **Lesson 6.3: How to Prepare for Audit**

**3 Months Before Audit:**

- [ ] Compile all FMR filings (past 2 years)
- [ ] Compile all STR filings (past 2 years)
- [ ] Verify all receipts/acks on file
- [ ] Test audit trail queries
- [ ] Train staff on audit procedures
- [ ] Document process changes
- [ ] Prepare sample reports for auditors

**1 Month Before:**

- [ ] Conduct internal audit
- [ ] Fix any gaps found
- [ ] Prepare audit presentation
- [ ] Identify potential questions

**1 Week Before:**

- [ ] Organize all documentation
- [ ] Assign audit coordinator
- [ ] Ensure portal access works
- [ ] Brief CRO/CCO on audit process

---

### **Lesson 6.4: Common Audit Findings & Prevention**

| Finding | Why It Happens | Prevention |
|---|---|---|
| **Frauds not filed within 3 weeks** | Manual process delays | Automate FMR, daily SLA monitoring |
| **AML alerts not filed within 7 days** | Backlog, manual process | Automate STR, daily SLA alerts |
| **Data masking failures** | PII accidentally included | Masking validation layer before submit |
| **Missing audit trail** | No logging implemented | Create immutable audit trail table |
| **Invalid digital signatures** | Certificate expired | Renew certificates annually |
| **Receipt IDs not retained** | Poor documentation | Archive all receipts/acks |
| **No process documentation** | Processes not formalized | Document and update regularly |
| **Staff not trained** | Onboarding gaps | Mandatory training + certification |

---

## Final Exam & Certification

### **Knowledge Check Questions**

**Section 1: Regulatory Framework (Q1–Q5)**

Q1: What is the deadline for FMR filing?  
A) 7 days  
B) 14 days  
C) **21 days** ✓  
D) 30 days

Q2: What is the penalty for STR non-compliance?  
A) Audit finding  
B) Regulatory penalty  
C) **Criminal prosecution + imprisonment** ✓  
D) Warning only

Q3: Who must sign FMR?  
A) Compliance Officer  
B) **Chief Risk Officer (CRO)** ✓  
C) Board Member  
D) External Auditor

Q4: Who must sign STR?  
A) Compliance Manager  
B) **Chief Compliance Officer (CCO)** ✓  
C) CRO  
D) Legal Team

Q5: Which authority receives STR?  
A) RBI  
B) Ministry of Finance  
C) **FIU-IND** ✓  
D) Police

**Section 2: FMR Process (Q6–Q10)**

Q6: What is the correct masking for PAN 1234ABCD5678?  
A) 1234****  
B) ****ABCD  
C) ******5678** ✓  
D) No masking needed

Q7: What data source is used for FMR extraction?  
A) Customer complaints  
B) **fraud_alerts table in FRMS** ✓  
C) RBI database  
D) Media reports

Q8: What format is FMR filed in?  
A) PDF only  
B) **Excel (.xlsx)** ✓  
C) XML  
D) CSV

Q9: How long does RBI take to send receipt?  
A) Instantly  
B) **Within 2 hours** ✓  
C) Within 1 day  
D) Within 5 days

Q10: Where is receipt ID logged for audit trail?  
A) Fraud analyst's notes  
B) Email archive  
C) **fmr_str_audit_trail immutable table** ✓  
D) No logging required

**Section 3: STR Process (Q11–Q15)**

Q11: What is the maximum length of STR narrative?  
A) 100 characters  
B) 250 characters  
C) **500 characters** ✓  
D) 1000 characters

Q12: What type of suspicion is CPT-01 (Sanctions)?  
A) Structuring  
B) Layering  
C) **SANCTIONS** ✓  
D) Geolocation

Q13: What format is STR filed in?  
A) Excel  
B) PDF  
C) **XML (digitally signed)** ✓  
D) Word document

Q14: How long does FIU-IND take to send acknowledgment?  
A) Within 1 hour  
B) **Within 30 minutes** ✓  
C) Instantly  
D) Within 1 day

Q15: Which portal is used for STR submission?  
A) RBI Fraud Portal  
B) **FINnet Gateway** ✓  
C) Bank's internal system  
D) Email to FIU-IND

**Section 4: Data Privacy (Q16–Q20)**

Q16: How should Aadhaar be masked?  
A) Full masking (****)  
B) First 4 + last 4  
C) **Last 4 digits only (****5678)** ✓  
D) No masking

Q17: Can we include customer's DOB in FMR?  
A) **No** ✓  
B) Yes, if customer consents  
C) Yes, but masked  
D) Only if fraud is identity theft

Q18: What is the masking rule for account number?  
A) First 4 only  
B) Last 4 only  
C) **First 4 + last 4 (1234****3456)** ✓  
D) Full account number

Q19: Can we include customer's residential address?  
A) Yes, always  
B) **No** ✓  
C) Only state/city  
D) Only zip code

Q20: What happens if we submit unmasked PII to RBI/FIU?  
A) Nothing, it's automatically masked  
B) Warning email from authority  
C) **Data privacy breach + regulatory penalty** ✓  
D) Automatic retry with masking

---

### **Certification Criteria**

**To Pass FMR/STR Training:**

- Score ≥80% on final exam (16/20 questions correct)
- Attend all 6 modules (or pass online tests)
- Pass practical scenario exercise:
  - Generate sample FMR ✓
  - Generate sample STR ✓
  - Submit to test portal ✓
- Obtain CRO/CCO sign-off

**Recertification:**

- Annually (once per year)
- After system changes
- After regulatory updates

---

### **Training Sign-Off**

**Employee Name:** ___________________  
**Job Title:** ___________________  
**Date:** ___________________  

**Training Completed:**
- [ ] Module 1: Regulatory Framework (90 min)
- [ ] Module 2: FMR Process (2 hrs)
- [ ] Module 3: STR Process (2 hrs)
- [ ] Module 4: Data Privacy & Masking (1 hr)
- [ ] Module 5: Tools & Systems (1.5 hrs)
- [ ] Module 6: Compliance & Audit (1 hr)

**Exam Score:** _____ / 20 (Pass: ≥16)

**Practical Exercise Status:**
- [ ] Sample FMR: ✅ PASS / ❌ FAIL
- [ ] Sample STR: ✅ PASS / ❌ FAIL
- [ ] Portal Submission: ✅ PASS / ❌ FAIL

**Certification Status:**
- [ ] ✅ CERTIFIED (Passed all requirements)
- [ ] ❌ NOT CERTIFIED (Failed: __________________)

**Trainer Name:** ___________________  
**Trainer Signature:** _________________________

**CRO/CCO Approval:** _________________________

---

**End of Training Materials**

