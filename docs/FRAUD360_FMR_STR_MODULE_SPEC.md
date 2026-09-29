# Fraud360: FMR/STR Module Specification
## Regulatory Reporting for RBI & FIU-IND Compliance

**Version:** 1.0  
**Module:** FMR/STR Reporting Engine  
**Audience:** Platform engineers, compliance teams, risk managers  
**Status:** Design phase (for MVP Phase 3, Weeks 7–8)

---

## Executive Summary

Fraud360's **FMR/STR Module** automates generation of:
- **FMR (Fraud Monitoring Return)** → RBI portal (3 weeks + quarterly)
- **STR (Suspicious Transaction Report)** → FIU-IND FINnet (within 7 days)

**Key capability:** Convert Lane A/B/C alerts → compliant RBI/FIU-IND reports with zero manual work.

**Compliance:** 100% aligned with RBI Master Directions 2024-25 (Section 12.2.3 & 12.4.3) and PMLA 2002.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                  Fraud360 Alert Pipeline                     │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  Lane A Alerts        Lane B Alerts        Lane C Alerts     │
│  (Fraud)              (AML/CFT)            (Credit Risk)     │
│      │                    │                     │             │
│      └────────────────────┼─────────────────────┘             │
│                           ↓                                   │
│                  ┌─────────────────┐                          │
│                  │ Alert Enrichment│                          │
│                  │ (add context,   │                          │
│                  │  customer, txn) │                          │
│                  └────────┬────────┘                          │
│                           ↓                                   │
│          ┌────────────────────────────────────┐              │
│          │  FMR/STR Classification Engine     │              │
│          │  (determine report type)           │              │
│          └────────┬────────────────────────┬──┘              │
│                   │                        │                 │
│          ┌────────▼─────────┐   ┌─────────▼────────┐        │
│          │  FMR Module      │   │  STR Module      │        │
│          │  (Fraud reports) │   │  (AML reports)   │        │
│          └────────┬─────────┘   └─────────┬────────┘        │
│                   │                        │                 │
│          ┌────────▼─────────┐   ┌─────────▼────────┐        │
│          │ RBI Portal       │   │ FINnet Gateway   │        │
│          │ Submission       │   │ Submission       │        │
│          └──────────────────┘   └──────────────────┘        │
│                   │                        │                 │
│          ┌────────▼────────────────────────▼──────┐         │
│          │   Audit Trail & Compliance Log          │         │
│          │   (7-year immutable retention)          │         │
│          └───────────────────────────────────────┘         │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

---

## Data Flow: Alert → Report

### **Step 1: Alert Detection**

**Lane A (Fraud):**
```
VEL-01 (velocity spike) → ₹200K transaction on ₹5K baseline account
→ Create Alert: { type: "FRAUD", signal: "VEL-01", severity: "HIGH" }
```

**Lane B (AML/CFT):**
```
CPT-01 (sanctioned entity) → Payment to OFAC-listed entity
→ Create Alert: { type: "AML", signal: "CPT-01", severity: "CRITICAL" }
```

**Lane C (Credit Risk):**
```
EWS-05 (covenant breach) → Inventory up 40%, revenue flat
→ Create Alert: { type: "CREDIT", signal: "EWS-05", severity: "MEDIUM" }
```

### **Step 2: Alert Enrichment**

```sql
SELECT
  a.alert_id,
  a.signal_type,
  a.severity,
  c.customer_id,
  c.customer_name,
  c.kyc_pan (MASKED),
  c.kyc_aadhaar (MASKED),
  t.transaction_id,
  t.transaction_date,
  t.amount,
  t.channel,
  t.account_from,
  t.account_to (MASKED),
  br.branch_code,
  br.branch_name
FROM
  alerts a
  JOIN customers c ON a.customer_id = c.customer_id
  JOIN transactions t ON a.transaction_id = t.transaction_id
  JOIN branches br ON c.branch_id = br.branch_id
WHERE
  a.created_at >= NOW() - INTERVAL '7 days'
  AND a.status = 'PENDING_REPORT';
```

### **Step 3: Classification**

```python
def classify_alert_for_reporting(alert):
    """Determine if alert requires FMR, STR, or both"""
    
    if alert.signal_type in ['VEL-01', 'VEL-03', 'SME-01', 'LAY-01', 'CHN-01']:
        # Lane A fraud signals
        return 'FMR'
    
    elif alert.signal_type in ['CPT-01', 'CPT-02', 'LAY-03', 'TBM-02']:
        # Lane B AML/CTF signals
        return 'STR'
    
    elif alert.signal_type in ['CPT-03', 'SME-03']:
        # Fraud + AML risk
        return 'FMR_AND_STR'
    
    elif alert.signal_type in ['EWS-05', 'EWS-12']:
        # Lane C credit risk (no immediate reporting, but track for quarterly)
        return 'CREDIT_ONLY'
    
    return 'UNCLASSIFIED'
```

---

## Module 1: FMR (Fraud Monitoring Return) Engine

### **Purpose**
Generate RBI-compliant fraud reports for immediate (3-week) and quarterly submission.

### **Input Data**
```python
class FraudAlert:
    alert_id: str                    # Unique ID
    detection_date: datetime         # When fraud detected
    fraud_type: str                  # "cheque", "digital", "card", "loan", "transfer"
    amount: float                    # In INR
    customer_id: str                 # Masked in report
    customer_name: str               # Masked in report (surname + first initial)
    account_number: str              # Masked (last 4 digits only)
    modus_operandi: str              # How fraud was committed (50–200 chars)
    action_taken: str                # Legal/disciplinary/recovery (100–300 chars)
    status: str                      # "ONGOING", "RESOLVED", "UNDER_INVESTIGATION"
    branch_code: str
    branch_name: str
    bank_name: str
```

### **FMR Template Mapping**

| FMR Field | Source | Data Transformation | Example |
|---|---|---|---|
| Name of bank/NBFC/PACS | Config | As is | "Finverge Bank Ltd" |
| Branch code | alert.branch_code | As is | "00123" |
| Branch name | alert.branch_name | As is | "Mumbai – Fort Branch" |
| Date of fraud detection | alert.detection_date | Format: DD-MMM-YYYY | "10-AUG-2026" |
| Fraud classification | alert.fraud_type | Map to RBI categories | "Digital fraud" |
| Nature of fraud | Lane A signal | Signal description | "Velocity spike (VEL-01)" |
| Amount involved | alert.amount | Format: ₹XXX,XXX.00 | "₹200,000.00" |
| Account/customer details | alert.customer_id | Masked: Sur******, Initials | "S******, A.M." |
| Account number | alert.account_number | Masked: XXXX-XXXX-XXXX-1234 | "**** **** **** 5678" |
| Modus operandi | alert.modus_operandi | As is (validated length) | "Unauthorized UPI transfer from dormant account using stolen credentials" |
| Action taken | alert.action_taken | As is | "Account blocked; FIR filed; recovery in progress" |
| Date of reporting | NOW() | Format: DD-MMM-YYYY | "10-AUG-2026" |
| Status | alert.status | Upper case | "UNDER INVESTIGATION" |

### **FMR Excel Template (RBI Format)**

```
═══════════════════════════════════════════════════════════════
                    FRAUD MONITORING RETURN (FMR)
                     Reserve Bank of India
═══════════════════════════════════════════════════════════════

Reporting Period: August 2026
Submitted by: [Bank Name]
Authorized by: [CRO/CCO Name & Signature]
Date of Submission: 10-AUG-2026

───────────────────────────────────────────────────────────────
FRAUD CASE DETAILS
───────────────────────────────────────────────────────────────

S.No. | Branch | Detection Date | Fraud Type | Amount (₹) | Account | Customer | Modus Operandi | Action Taken | Status
───────────────────────────────────────────────────────────────
  1   | 00123  | 05-AUG-2026   | Digital    | 200,000    | ****5678 | S***, A.M. | Unauthorized UPI transfer from dormant account | Account blocked, FIR filed | Under Investigation
  2   | 00124  | 08-AUG-2026   | Cheque     | 500,000    | ****9012 | P***, R.K. | Cheque fraud with forged signature | Legal proceedings initiated | Resolved
  3   | 00125  | 09-AUG-2026   | Card       | 150,000    | ****3456 | M***, N.V. | Unauthorized card transaction abroad | Card blocked, refund issued | Resolved

───────────────────────────────────────────────────────────────
SUMMARY
───────────────────────────────────────────────────────────────
Total Fraud Cases: 3
Total Fraud Amount: ₹850,000
Average Fraud Amount: ₹283,333
Status: All cases reported to RBI within 3 weeks

Certified by: [CRO Signature] Date: 10-AUG-2026
```

### **FMR Generation SQL**

```sql
-- Generate FMR for immediate reporting (within 3 weeks of detection)
SELECT
  ROW_NUMBER() OVER (ORDER BY a.detection_date) as case_number,
  b.branch_code,
  b.branch_name,
  TO_CHAR(a.detection_date, 'DD-MON-YYYY') as detection_date,
  a.fraud_type,
  TO_CHAR(a.amount, '₹999,999.00') as amount,
  CONCAT(
    SUBSTR(c.customer_name, 1, 1), '****** ', 
    SUBSTR(c.customer_name, -3)
  ) as customer_masked,
  CONCAT(SUBSTR(a.account_number, 1, 4), '-****-****-', RIGHT(a.account_number, 4)) as account_masked,
  a.modus_operandi,
  a.action_taken,
  UPPER(a.status) as status,
  TO_CHAR(NOW(), 'DD-MON-YYYY') as reporting_date
FROM
  fraud_alerts a
  JOIN customers c ON a.customer_id = c.customer_id
  JOIN branches b ON c.branch_id = b.branch_id
WHERE
  DATEDIFF(day, a.detection_date, NOW()) <= 21  -- Within 3 weeks
  AND a.report_status = 'PENDING'
  AND a.signal_type IN ('VEL-01', 'VEL-03', 'SME-01', 'LAY-01', 'CHN-01')
ORDER BY
  a.detection_date ASC;
```

### **FMR Quarterly Consolidation**

```sql
-- Generate quarterly FMR summary
SELECT
  EXTRACT(QUARTER FROM a.detection_date) as quarter,
  EXTRACT(YEAR FROM a.detection_date) as year,
  COUNT(*) as total_fraud_cases,
  SUM(a.amount) as total_fraud_amount,
  AVG(a.amount) as avg_fraud_amount,
  MIN(a.amount) as min_fraud_amount,
  MAX(a.amount) as max_fraud_amount,
  COUNT(CASE WHEN a.status = 'RESOLVED' THEN 1 END) as resolved_cases,
  COUNT(CASE WHEN a.status = 'UNDER_INVESTIGATION' THEN 1 END) as pending_cases,
  SUM(CASE WHEN a.amount_recovered > 0 THEN a.amount_recovered ELSE 0 END) as total_recovery
FROM
  fraud_alerts a
WHERE
  a.report_status = 'SUBMITTED'
GROUP BY
  EXTRACT(QUARTER FROM a.detection_date),
  EXTRACT(YEAR FROM a.detection_date)
ORDER BY
  year DESC, quarter DESC;
```

---

## Module 2: STR (Suspicious Transaction Report) Engine

### **Purpose**
Generate FIU-IND-compliant AML/CFT reports within 7 days of suspicion.

### **Input Data**

```python
class SuspiciousTransactionAlert:
    alert_id: str                      # Unique ID
    detection_date: datetime           # When suspicion established
    customer_id: str
    customer_name: str
    customer_kyc_pan: str              # Masked in report
    customer_kyc_aadhaar: str          # Masked in report
    account_number: str                # Masked in report
    transaction_id: str
    transaction_date: datetime
    transaction_amount: float          # In INR
    transaction_channel: str           # "UPI", "NEFT", "RTGS", "BRANCH", "ATM", "CARD"
    beneficiary_account: str           # Masked in report
    beneficiary_name: str              # Masked in report
    suspicion_type: str                # "STRUCTURING", "LAYERING", "SANCTIONS", "PEP", "GEOLOCATION"
    suspicion_narrative: str           # Why it's suspicious (max 500 chars)
    internal_reference_id: str         # For tracking
    regulatory_basis: str              # Which rule triggered (e.g., "CPT-01", "LAY-03")
```

### **STR Template Mapping**

| STR Field | Source | Data Transformation | Example |
|---|---|---|---|
| Reporting entity ID | Config | As is | "BANK123456" |
| Reporting entity name | Config | As is | "Finverge Bank Ltd" |
| STR filing date | NOW() | Format: DD-MMM-YYYY | "10-AUG-2026" |
| Unique STR ID | Generate | UUIDv4 | "f47ac10b-58cc-4372-a567-0e02b2c3d479" |
| Customer name | alert.customer_name | Masked: Sur******, Initials | "S******, A.M." |
| Customer PAN | alert.kyc_pan | Masked: Last 4 digits | "****1234" |
| Customer Aadhaar | alert.kyc_aadhaar | Masked: Last 4 digits | "****5678" |
| Account number | alert.account_number | Masked: Last 4 digits | "****9012" |
| Transaction date | alert.transaction_date | Format: DD-MMM-YYYY | "09-AUG-2026" |
| Transaction amount | alert.amount | Format: INR with decimals | "200000.00" |
| Transaction channel | alert.channel | Upper case | "UPI" |
| Beneficiary account | alert.beneficiary | Masked: Last 4 digits | "****3456" |
| Beneficiary name | alert.beneficiary_name | Masked | "B***, R.V." |
| Suspicion indicator | alert.suspicion_type | Map to PMLA categories | "STRUCTURING" |
| Reason for suspicion | alert.narrative | Validated length (max 500) | "Account received ₹9.99L transfer; immediately transferred out to 5 different accounts. Pattern consistent with structuring to evade CTR reporting." |
| Internal reference | alert.internal_ref | As is | "STR-2026-08-00001" |
| Regulatory basis | alert.signal | Signal code | "SME-01" |

### **STR XML Template (FIU-IND FINnet Format)**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<STR>
  <ReportingEntity>
    <Name>Finverge Bank Ltd</Name>
    <EntityID>BANK123456</EntityID>
    <RegistrationNumber>RBI-BANKING-2023-00123</RegistrationNumber>
  </ReportingEntity>
  
  <STRDetails>
    <STRFileID>f47ac10b-58cc-4372-a567-0e02b2c3d479</STRFileID>
    <FilingDate>2026-08-10</FilingDate>
    <Period>Q3-2026</Period>
  </STRDetails>
  
  <Customer>
    <CustomerID>CUST-2026-00123</CustomerID>
    <CustomerName>S******, A.M.</CustomerName>
    <KYCStatus>VERIFIED</KYCStatus>
    <KYCDate>2022-03-15</KYCDate>
    <PAN>****1234</PAN>
    <Aadhaar>****5678</Aadhaar>
    <AccountNumber>****9012</AccountNumber>
    <AccountType>SAVINGS</AccountType>
    <AccountOpenDate>2020-06-01</AccountOpenDate>
  </Customer>
  
  <Transaction>
    <TransactionID>TXN-2026-00456</TransactionID>
    <TransactionDate>2026-08-09</TransactionDate>
    <Amount>200000.00</Amount>
    <Currency>INR</Currency>
    <Channel>UPI</Channel>
    <BeneficiaryAccount>****3456</BeneficiaryAccount>
    <BeneficiaryName>B***, R.V.</BeneficiaryName>
    <BeneficiaryBank>HDFC0001234</BeneficiaryBank>
  </Transaction>
  
  <SuspicionDetails>
    <SuspicionType>STRUCTURING</SuspicionType>
    <SuspicionIndicators>
      <Indicator>Sub-CTR Multiple Transfers</Indicator>
      <Indicator>Unusual Account Activity</Indicator>
      <Indicator>New Payee Activation</Indicator>
    </SuspicionIndicators>
    <SuspicionNarrative>
      Account received ₹9.99L transfer on 09-AUG-2026; 
      immediately transferred out to 5 different accounts within 2 hours. 
      Pattern is consistent with structuring to evade CTR reporting threshold. 
      Account had zero activity for 180 days prior. Risk assessment: HIGH.
    </SuspicionNarrative>
    <RegulatoryBasis>PMLA-2002-Section-29; RBI-Master-Directions-2024-25-Section-12.2</RegulatoryBasis>
    <FraudScore>Signal: SME-01 (Sub-CTR Structuring) triggered</FraudScore>
  </SuspicionDetails>
  
  <InternalTracking>
    <InternalRefNumber>STR-2026-08-00001</InternalRefNumber>
    <CreatedDate>2026-08-10</CreatedDate>
    <ApprovedBy>CRO-NAME</ApprovedBy>
    <ApprovalDate>2026-08-10</ApprovalDate>
    <DigitalSignature>XXXXX...XXXXX</DigitalSignature>
  </InternalTracking>
  
  <ComplianceNotes>
    <Filed>true</Filed>
    <FilingDate>2026-08-10</FilingDate>
    <FilingChannel>FINnet-Gateway</FilingChannel>
    <AcknowledgmentID>ACK-FIU-2026-08-00001</AcknowledgmentID>
  </ComplianceNotes>
</STR>
```

### **STR Generation SQL**

```sql
-- Generate STR for AML/CFT suspicious transactions (within 7 days)
SELECT
  a.alert_id,
  CONCAT('STR-', TO_CHAR(NOW(), 'YYYY-MM'), '-', LPAD(ROW_NUMBER() OVER (ORDER BY a.detection_date), 5, '0')) as str_id,
  c.customer_id,
  CONCAT(SUBSTR(c.customer_name, 1, 1), '******, ', SUBSTR(c.customer_name, -3)) as customer_masked,
  SUBSTR(c.kyc_pan, 1, 4) || '****' as pan_masked,
  SUBSTR(c.kyc_aadhaar, 1, 4) || '****' as aadhaar_masked,
  CONCAT(SUBSTR(a.account_number, 1, 4), '-****-****-', RIGHT(a.account_number, 4)) as account_masked,
  t.transaction_id,
  TO_CHAR(t.transaction_date, 'DD-MON-YYYY') as transaction_date,
  t.amount,
  t.channel,
  CONCAT(SUBSTR(t.beneficiary_account, 1, 4), '-****-****-', RIGHT(t.beneficiary_account, 4)) as beneficiary_masked,
  CONCAT(SUBSTR(t.beneficiary_name, 1, 1), '******, ', SUBSTR(t.beneficiary_name, -3)) as beneficiary_name_masked,
  a.suspicion_type,
  a.suspicion_narrative,
  a.signal_code as regulatory_basis,
  TO_CHAR(NOW(), 'DD-MON-YYYY') as filing_date,
  DATEDIFF(day, a.detection_date, NOW()) as days_to_file
FROM
  aml_alerts a
  JOIN customers c ON a.customer_id = c.customer_id
  JOIN transactions t ON a.transaction_id = t.transaction_id
WHERE
  DATEDIFF(day, a.detection_date, NOW()) <= 7  -- Within 7 days
  AND a.report_status = 'PENDING'
  AND a.signal_type IN ('CPT-01', 'CPT-02', 'CPT-03', 'LAY-03', 'TBM-02', 'SME-03', 'CHN-02')
ORDER BY
  a.detection_date ASC;
```

---

## Module 3: Report Generation & Filing

### **3.1 Automated Report Scheduler**

```python
class ReportScheduler:
    def schedule_fmr_reports(self):
        """Schedule FMR generation and filing"""
        # Immediate: Daily check for new fraud cases
        daily_job = cron_job(
            job_id="fmr_immediate_daily",
            schedule="0 2 * * *",  # 2 AM daily
            function=self.generate_fmr_immediate,
            description="Generate FMR for fraud detected in last 24h"
        )
        
        # Quarterly: Generate consolidated report
        quarterly_job = cron_job(
            job_id="fmr_quarterly",
            schedule="0 3 1 1,4,7,10 *",  # 1st of Jan, Apr, Jul, Oct
            function=self.generate_fmr_quarterly,
            description="Generate quarterly FMR consolidation"
        )
        
        return [daily_job, quarterly_job]
    
    def schedule_str_reports(self):
        """Schedule STR generation and filing (7-day SLA)"""
        # Event-triggered: Generate STR within 7 days of suspicion
        event_job = listener(
            event_id="aml_alert_created",
            handler=self.generate_str_for_alert,
            timeout_days=7,
            description="Generate STR within 7 days of AML alert"
        )
        
        # Monitoring: Escalate if STR not filed within 7 days
        monitoring_job = cron_job(
            job_id="str_7day_sla_monitor",
            schedule="0 1 * * *",  # 1 AM daily
            function=self.check_str_7day_sla,
            description="Alert if any STR exceeds 7-day SLA"
        )
        
        return [event_job, monitoring_job]
```

### **3.2 Portal Integration (RBI & FIU-IND)**

```python
class PortalIntegration:
    def submit_fmr_to_rbi(self, fmr_report):
        """Submit FMR to RBI portal (via secure HTTPS/API)"""
        
        # Step 1: Format as RBI Excel/XML
        formatted_report = self.format_rbi_fmr(fmr_report)
        
        # Step 2: Encrypt with RBI's public certificate
        encrypted_report = self.encrypt_with_rbi_cert(formatted_report)
        
        # Step 3: Submit to RBI portal
        response = requests.post(
            url="https://rbi-fraud-portal.rbi.org.in/api/fmr/submit",
            data=encrypted_report,
            headers={
                "Authorization": f"Bearer {RBI_API_KEY}",
                "Content-Type": "application/octet-stream"
            }
        )
        
        # Step 4: Log submission with receipt
        self.log_fmr_submission({
            'submission_id': response.json()['receipt_id'],
            'submission_date': NOW(),
            'status': 'SUBMITTED',
            'receipt_timestamp': response.json()['timestamp']
        })
        
        return response.json()['receipt_id']
    
    def submit_str_to_finnet(self, str_report):
        """Submit STR to FIU-IND via FINnet Gateway"""
        
        # Step 1: Generate XML (FIU-IND format)
        xml_report = self.format_finnet_str(str_report)
        
        # Step 2: Sign with CRO's digital certificate (CA-approved)
        signed_report = self.sign_with_ca_certificate(xml_report)
        
        # Step 3: Submit to FINnet Gateway (secure portal)
        response = requests.post(
            url="https://finnet.fiu.gov.in/api/str/submit",
            data=signed_report,
            headers={
                "Authorization": f"Bearer {FINNET_API_KEY}",
                "Content-Type": "application/xml"
            }
        )
        
        # Step 4: Log submission with acknowledgment
        ack_id = response.json()['acknowledgment_id']
        self.log_str_submission({
            'ack_id': ack_id,
            'submission_date': NOW(),
            'status': 'SUBMITTED',
            'finnet_timestamp': response.json()['timestamp']
        })
        
        return ack_id
```

### **3.3 Audit Trail & Compliance Log**

```sql
-- Table: fmr_str_audit_trail (immutable, 7-year retention)
CREATE TABLE fmr_str_audit_trail (
    audit_id UUID PRIMARY KEY,
    report_type VARCHAR(10) CHECK (report_type IN ('FMR', 'STR')),
    report_id VARCHAR(50),
    alert_ids TEXT[],  -- Linked fraud/AML alerts
    
    -- Submission details
    submission_date TIMESTAMP NOT NULL,
    submitted_by_user_id VARCHAR(50) NOT NULL,
    submitted_by_role VARCHAR(50) NOT NULL,
    submission_portal VARCHAR(50) NOT NULL,  -- "RBI_PORTAL", "FINNET_GATEWAY"
    
    -- Response from authority
    receipt_id VARCHAR(100),
    ack_id VARCHAR(100),
    authority_timestamp TIMESTAMP,
    authority_response_status VARCHAR(20),  -- "RECEIVED", "ACCEPTED", "REJECTED"
    authority_response_message TEXT,
    
    -- Compliance verification
    data_classification TEXT,  -- Customer data masking verification
    encryption_method VARCHAR(50),
    digital_signature_valid BOOLEAN,
    
    -- Audit trail integrity
    hash_value VARCHAR(256),  -- SHA-256 hash for immutability
    immutable_flag BOOLEAN DEFAULT TRUE,
    
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP,  -- 7 years after creation
    
    -- Constraints
    CONSTRAINT immutable_records CHECK (immutable_flag = TRUE),
    CONSTRAINT retention_check CHECK (expires_at >= created_at + INTERVAL '7 years')
);

-- Index for compliance audits
CREATE INDEX idx_audit_report_type ON fmr_str_audit_trail(report_type, submission_date);
CREATE INDEX idx_audit_authority ON fmr_str_audit_trail(submission_portal, authority_response_status);
```

---

## Module 4: Compliance Monitoring & Dashboards

### **SLA Monitoring**

```python
class ComplianceMonitoring:
    def check_fmr_reporting_sla(self):
        """Alert if FMR not filed within 3 weeks"""
        
        overdue_frauds = query("""
            SELECT alert_id, detection_date, fraud_type, amount
            FROM fraud_alerts
            WHERE DATEDIFF(day, detection_date, NOW()) > 21
            AND report_status = 'PENDING'
        """)
        
        if overdue_frauds:
            send_alert(
                severity="CRITICAL",
                title="FMR Reporting SLA Breach",
                recipients=["CRO", "CCO", "Compliance"],
                message=f"{len(overdue_frauds)} fraud cases overdue for FMR reporting"
            )
    
    def check_str_reporting_sla(self):
        """Alert if STR not filed within 7 days"""
        
        overdue_aml = query("""
            SELECT alert_id, detection_date, suspicion_type, amount
            FROM aml_alerts
            WHERE DATEDIFF(day, detection_date, NOW()) > 7
            AND report_status = 'PENDING'
        """)
        
        if overdue_aml:
            send_alert(
                severity="CRITICAL",
                title="STR Reporting SLA Breach - FIU-IND Compliance Risk",
                recipients=["CRO", "CCO", "Compliance", "Legal"],
                message=f"{len(overdue_aml)} AML cases overdue for STR filing (PMLA violation)"
            )
```

### **Dashboard Metrics**

```python
class ComplianceDashboard:
    metrics = {
        "FMR Metrics": {
            "Total Frauds Detected (YTD)": "COUNT(*) from fraud_alerts",
            "FMRs Submitted (On-time)": "% within 3 weeks",
            "FMR Rejection Rate": "REJECTED / SUBMITTED",
            "Fraud Loss (₹)": "SUM(amount) from fraud_alerts",
            "Fraud Recovery (₹)": "SUM(amount_recovered)",
        },
        
        "STR Metrics": {
            "Total AML Alerts (YTD)": "COUNT(*) from aml_alerts",
            "STRs Submitted (On-time)": "% within 7 days",
            "STR Rejection Rate": "REJECTED / SUBMITTED",
            "STRs Under Investigation": "COUNT(status = 'PENDING')",
            "FIU-IND Acknowledgment Rate": "ACK_RECEIVED / SUBMITTED",
        },
        
        "Audit Readiness": {
            "7-Year Audit Trail Completeness": "% of records retained",
            "Digital Signature Validity": "% of signed reports",
            "Data Masking Compliance": "% of reports with correct masking",
            "Portal Integration Uptime": "% availability (RBI + FINnet)",
        }
    }
```

---

## Implementation Timeline

| Phase | Week | Deliverable | Owner |
|---|---|---|---|
| **Design** | 1–2 | FMR/STR specification (this doc), schema design, API design | Platform Architect |
| **Development** | 3–5 | FMR module, STR module, portal integration | Backend engineers |
| **Testing** | 6–7 | Unit tests, integration tests, end-to-end FMR/STR filing test | QA team |
| **Pre-deployment** | 8 | Dry-run with RBI test portal, FINnet sandbox | Compliance + Platform |
| **Deployment** | 9+ | Production rollout, monitoring setup | DevOps + Compliance |

**Go-Live:** Week 9 (MVP Phase 3, Week 8 deployment + Week 9 production)

---

## Sign-Off Checklist

- [ ] CRO: FMR/STR module aligns with RBI compliance requirements
- [ ] CCO: Data masking and privacy controls verified
- [ ] Compliance: Portal integration tested with RBI/FIU-IND
- [ ] Legal: PMLA Section 29 compliance confirmed
- [ ] Platform: API design, database schema, deployment infrastructure ready
- [ ] Audit: 7-year retention and immutable audit trail tested
- [ ] Security: Encryption, digital signatures, portal authentication verified

---

## Conclusion

Fraud360's FMR/STR module transforms fraud/AML detection into **automatic, compliant regulatory reporting** within RBI's 3-week and FIU-IND's 7-day SLAs.

**Key deliverable:** Zero-touch conversion of Lane A/B alerts → RBI FMR + FIU-IND STR, with immutable audit trail and 100% regulatory compliance.

