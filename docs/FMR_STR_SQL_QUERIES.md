# FMR/STR: SQL Queries for Compliance Reporting
## Extract Suspicious Transactions & Generate Reports

**Purpose:** Ready-to-run SQL queries to extract data for FMR/STR filing from your core banking system.

---

## Part 1: FMR (Fraud Monitoring Return) Queries

### Query 1: Extract Fraud Cases for Immediate FMR Filing (within 3 weeks)

```sql
-- ============================================================================
-- FMR Immediate Reporting: Frauds detected in last 3 weeks, pending filing
-- ============================================================================

SELECT
  f.fraud_id,
  f.detection_date,
  f.report_date,
  b.branch_code,
  b.branch_name,
  b.city,
  f.fraud_type,
  CASE 
    WHEN f.fraud_type = 'CHEQUE' THEN 'Cheque fraud'
    WHEN f.fraud_type = 'DIGITAL' THEN 'Digital/UPI fraud'
    WHEN f.fraud_type = 'CARD' THEN 'Card fraud'
    WHEN f.fraud_type = 'LOAN' THEN 'Loan fraud'
    WHEN f.fraud_type = 'TRANSFER' THEN 'Fund transfer fraud'
    ELSE 'Other fraud'
  END as fraud_classification,
  
  -- Lane A signal that triggered this fraud alert
  f.signal_code as primary_signal,
  CASE
    WHEN f.signal_code = 'VEL-01' THEN 'Value velocity spike (baseline × multiplier)'
    WHEN f.signal_code = 'VEL-03' THEN 'Frequency spike (unusual transaction count)'
    WHEN f.signal_code = 'SME-01' THEN 'Sub-CTR structuring (multiple < ₹10L transfers)'
    WHEN f.signal_code = 'SME-02' THEN 'PIN-less UPI structuring'
    WHEN f.signal_code = 'LAY-01' THEN 'Outflow drain (money mule account)'
    WHEN f.signal_code = 'LAY-02' THEN 'Fan-out to multiple payees (hub account)'
    WHEN f.signal_code = 'CHN-01' THEN 'Device + payee + value spike'
    ELSE 'Other signal'
  END as detection_reason,
  
  -- Customer details (masked for privacy)
  CONCAT(
    SUBSTRING(c.customer_name, 1, 1), '***', 
    SUBSTRING(c.customer_name, POSITION(' ' IN c.customer_name))
  ) as customer_name_masked,
  
  -- Account details (masked)
  CONCAT(
    SUBSTRING(a.account_number, 1, 4), '-****-****-', 
    RIGHT(a.account_number, 4)
  ) as account_number_masked,
  
  CONCAT(
    SUBSTRING(a.account_number, 1, 4), '-****-****-', 
    RIGHT(a.account_number, 4)
  ) as account_for_rbi,
  
  -- Fraud amount
  FORMAT(f.fraud_amount, 2) as amount_inr,
  
  -- Modus operandi
  f.modus_operandi,
  
  -- Action taken
  CASE 
    WHEN f.action_status = 'BLOCKED' THEN 'Account blocked; monitoring initiated'
    WHEN f.action_status = 'FIR_FILED' THEN 'FIR filed with Police; account frozen'
    WHEN f.action_status = 'RECOVERED' THEN 'Fraud amount recovered; account restored'
    WHEN f.action_status = 'UNDER_INVESTIGATION' THEN 'Investigation in progress; account restricted'
    ELSE f.action_status
  END as action_taken,
  
  -- Status
  UPPER(f.status) as case_status,
  
  -- Days since detection (SLA tracking)
  DATEDIFF(DAY, f.detection_date, GETDATE()) as days_since_detection,
  CASE
    WHEN DATEDIFF(DAY, f.detection_date, GETDATE()) <= 21 THEN 'ON TIME'
    ELSE 'OVERDUE'
  END as fmr_sla_status,
  
  -- Reporting metadata
  f.reported_to_rbi_date,
  f.fmr_receipt_number

FROM fraud_alerts f
JOIN customers c ON f.customer_id = c.customer_id
JOIN accounts a ON f.account_id = a.account_id
JOIN branches b ON c.branch_id = b.branch_id

WHERE
  -- Fraud detected within 3 weeks
  DATEDIFF(DAY, f.detection_date, GETDATE()) <= 21
  
  -- Not yet reported to RBI
  AND (f.reported_to_rbi_date IS NULL OR f.fmr_status = 'PENDING')
  
  -- Confirmed fraud only (not under investigation)
  AND f.confirmation_status = 'CONFIRMED'
  
  -- Lane A signals only (fraud, not AML)
  AND f.signal_code IN ('VEL-01', 'VEL-03', 'SME-01', 'SME-02', 'BEH-01', 'LAY-01', 'LAY-02', 'LAY-04', 'CHN-01')

ORDER BY f.detection_date ASC;
```

---

### Query 2: FMR Quarterly Consolidation Report

```sql
-- ============================================================================
-- FMR Quarterly Consolidation: Summary for RBI quarterly return
-- ============================================================================

DECLARE @quarter_start DATE = DATEFROMPARTS(YEAR(GETDATE()), (MONTH(GETDATE())-1)/3*3+1, 1);
DECLARE @quarter_end DATE = EOMONTH(DATEFROMPARTS(YEAR(GETDATE()), (MONTH(GETDATE())-1)/3*3+3, 1));

SELECT
  'Q' + CAST((MONTH(GETDATE())-1)/3+1 AS VARCHAR) + ' ' + 
  CAST(YEAR(GETDATE()) AS VARCHAR) as reporting_quarter,
  
  @quarter_start as quarter_start_date,
  @quarter_end as quarter_end_date,
  
  -- Fraud statistics
  COUNT(DISTINCT f.fraud_id) as total_fraud_cases,
  COUNT(DISTINCT CASE WHEN f.status = 'RESOLVED' THEN f.fraud_id END) as resolved_cases,
  COUNT(DISTINCT CASE WHEN f.status = 'UNDER_INVESTIGATION' THEN f.fraud_id END) as pending_cases,
  COUNT(DISTINCT CASE WHEN f.status = 'RECOVERED' THEN f.fraud_id END) as recovered_cases,
  
  -- Financial impact
  FORMAT(SUM(f.fraud_amount), 2) as total_fraud_amount,
  FORMAT(AVG(f.fraud_amount), 2) as avg_fraud_per_case,
  FORMAT(MIN(f.fraud_amount), 2) as min_fraud_amount,
  FORMAT(MAX(f.fraud_amount), 2) as max_fraud_amount,
  FORMAT(SUM(f.amount_recovered), 2) as total_recovery_amount,
  CONCAT(
    FORMAT(100.0 * SUM(f.amount_recovered) / SUM(f.fraud_amount), 1), 
    '%'
  ) as recovery_rate,
  
  -- Fraud type breakdown
  COUNT(DISTINCT CASE WHEN f.fraud_type = 'DIGITAL' THEN f.fraud_id END) as digital_frauds,
  COUNT(DISTINCT CASE WHEN f.fraud_type = 'CHEQUE' THEN f.fraud_id END) as cheque_frauds,
  COUNT(DISTINCT CASE WHEN f.fraud_type = 'CARD' THEN f.fraud_id END) as card_frauds,
  COUNT(DISTINCT CASE WHEN f.fraud_type = 'LOAN' THEN f.fraud_id END) as loan_frauds,
  
  -- Detection speed
  CONCAT(
    FORMAT(AVG(DATEDIFF(HOUR, t.transaction_date, f.detection_date)), 1), 
    ' hours'
  ) as avg_detection_latency,
  
  -- Customer demographics (for risk profiling)
  COUNT(DISTINCT CASE WHEN c.customer_segment = 'RETAIL' THEN c.customer_id END) as retail_customers_affected,
  COUNT(DISTINCT CASE WHEN c.customer_segment = 'CORPORATE' THEN c.customer_id END) as corporate_customers_affected,
  
  -- Reporting compliance
  COUNT(DISTINCT CASE WHEN f.reported_to_rbi_date IS NOT NULL THEN f.fraud_id END) as fmr_submitted,
  COUNT(DISTINCT CASE WHEN f.reported_to_rbi_date IS NULL THEN f.fraud_id END) as fmr_pending,
  
  -- Audit trail
  GETDATE() as report_generated_date

FROM fraud_alerts f
LEFT JOIN customers c ON f.customer_id = c.customer_id
LEFT JOIN accounts a ON f.account_id = a.account_id
LEFT JOIN transactions t ON f.transaction_id = t.transaction_id

WHERE
  -- This quarter's frauds
  f.detection_date >= @quarter_start
  AND f.detection_date <= @quarter_end
  
  -- Confirmed fraud
  AND f.confirmation_status = 'CONFIRMED'

GROUP BY 
  QUARTER(GETDATE()), 
  YEAR(GETDATE());
```

---

## Part 2: STR (Suspicious Transaction Report) Queries

### Query 3: Extract AML Alerts for STR Filing (within 7 days)

```sql
-- ============================================================================
-- STR Reporting: AML/CFT alerts for FIU-IND filing (within 7 days)
-- ============================================================================

SELECT
  a.aml_alert_id,
  CONCAT('STR-', FORMAT(GETDATE(), 'yyyy-MM'), '-', 
    LPAD(ROW_NUMBER() OVER (ORDER BY a.detection_date), 5, '0')
  ) as str_reference_id,
  
  a.detection_date,
  DATEDIFF(DAY, a.detection_date, GETDATE()) as days_since_detection,
  CASE
    WHEN DATEDIFF(DAY, a.detection_date, GETDATE()) <= 7 THEN 'ON TIME'
    ELSE 'OVERDUE - REGULATORY VIOLATION'
  END as str_sla_status,
  
  -- Customer identification (masked for privacy)
  c.customer_id,
  CONCAT(
    SUBSTRING(c.customer_name, 1, 1), '***', 
    SUBSTRING(c.customer_name, POSITION(' ' IN c.customer_name))
  ) as customer_name_masked,
  
  -- KYC details (masked)
  SUBSTRING(c.kyc_pan, 1, 4) + '****' as pan_masked,
  SUBSTRING(c.kyc_aadhaar, 1, 4) + '****' as aadhaar_masked,
  c.kyc_verification_date,
  c.kyc_status,
  
  -- Account details (masked)
  CONCAT(
    SUBSTRING(ac.account_number, 1, 4), '-****-****-', 
    RIGHT(ac.account_number, 4)
  ) as account_masked,
  ac.account_type,
  
  -- Transaction details
  t.transaction_id,
  t.transaction_date,
  FORMAT(t.transaction_amount, 2) as transaction_amount_inr,
  t.transaction_channel,  -- UPI, NEFT, RTGS, BRANCH, CARD, etc.
  
  -- Beneficiary (masked)
  CONCAT(
    SUBSTRING(b.beneficiary_name, 1, 1), '***', 
    SUBSTRING(b.beneficiary_name, POSITION(' ' IN b.beneficiary_name))
  ) as beneficiary_name_masked,
  
  CONCAT(
    SUBSTRING(b.beneficiary_account, 1, 4), '-****-****-', 
    RIGHT(b.beneficiary_account, 4)
  ) as beneficiary_account_masked,
  
  -- Suspicion indicators & reason
  a.suspicion_type,
  CASE
    WHEN a.suspicion_type = 'STRUCTURING' THEN 'Multiple sub-₹10L transfers to evade CTR reporting'
    WHEN a.suspicion_type = 'LAYERING' THEN 'Circular flows and complex fund routing detected'
    WHEN a.suspicion_type = 'SANCTIONS' THEN 'Transaction matches OFAC/UNSC sanctioned entity'
    WHEN a.suspicion_type = 'PEP' THEN 'Politically Exposed Person identified in transaction'
    WHEN a.suspicion_type = 'GEOLOCATION' THEN 'Unusual geographic location for transaction'
    WHEN a.suspicion_type = 'INVOICE_FRAUD' THEN 'Invoice amount inconsistencies detected'
    ELSE a.suspicion_type
  END as suspicion_narrative_short,
  
  a.suspicion_narrative_detailed,
  
  -- Lane B signal that triggered this
  a.signal_code as regulatory_basis,
  CASE
    WHEN a.signal_code = 'CPT-01' THEN 'Counterparty: Sanctions entity matching'
    WHEN a.signal_code = 'CPT-02' THEN 'Counterparty: PEP involvement'
    WHEN a.signal_code = 'LAY-03' THEN 'Layering: Circular flow pattern'
    WHEN a.signal_code = 'SME-03' THEN 'Invoice fraud: Trade-based AML'
    WHEN a.signal_code = 'TBM-02' THEN 'Trade finance: Bill of lading mismatch'
    ELSE a.signal_code
  END as signal_description,
  
  -- Risk scoring
  a.aml_risk_score,
  CASE
    WHEN a.aml_risk_score >= 80 THEN 'CRITICAL - File immediately'
    WHEN a.aml_risk_score >= 60 THEN 'HIGH - File within 3 days'
    WHEN a.aml_risk_score >= 40 THEN 'MEDIUM - File within 7 days'
    ELSE 'LOW - Monitor'
  END as risk_priority,
  
  -- Internal tracking
  a.internal_reference_number,
  a.investigation_status,
  
  -- Reporting compliance
  a.str_filed_date,
  a.str_ack_id,
  a.str_filing_status

FROM aml_alerts a
JOIN customers c ON a.customer_id = c.customer_id
JOIN accounts ac ON a.account_id = ac.account_id
JOIN transactions t ON a.transaction_id = t.transaction_id
LEFT JOIN beneficiaries b ON t.beneficiary_id = b.beneficiary_id

WHERE
  -- Within 7-day STR filing window
  DATEDIFF(DAY, a.detection_date, GETDATE()) <= 7
  
  -- Not yet filed to FIU-IND
  AND (a.str_filed_date IS NULL OR a.str_filing_status = 'PENDING')
  
  -- Confirmed suspicion
  AND a.confirmation_status = 'CONFIRMED'
  
  -- Lane B AML signals only
  AND a.signal_code IN (
    'CPT-01', 'CPT-02', 'CPT-03', 
    'LAY-03', 'CHN-02', 'CHN-03', 
    'SME-03', 'TBM-01', 'TBM-02', 
    'BEH-02', 'BEH-03'
  )

ORDER BY a.aml_risk_score DESC, a.detection_date ASC;
```

---

### Query 4: STR Overdue Alert (Regulatory Breach Check)

```sql
-- ============================================================================
-- STR 7-DAY SLA VIOLATION CHECK: Identifies overdue STRs (PMLA compliance risk)
-- ============================================================================

SELECT
  a.aml_alert_id,
  a.detection_date,
  DATEDIFF(DAY, a.detection_date, GETDATE()) as days_overdue,
  CONCAT(
    DATEDIFF(DAY, a.detection_date, GETDATE()) - 7, ' days overdue'
  ) as breach_severity,
  
  c.customer_id,
  c.customer_name,
  ac.account_number,
  a.suspicion_type,
  a.suspicion_narrative_detailed,
  
  FORMAT(t.transaction_amount, 2) as amount_inr,
  a.aml_risk_score,
  
  -- Escalation info
  'CRITICAL - PMLA VIOLATION' as severity,
  'Submit STR to FIU-IND immediately' as action_required,
  'CRO, CCO, Legal' as escalate_to,
  
  GETDATE() as escalation_timestamp

FROM aml_alerts a
JOIN customers c ON a.customer_id = c.customer_id
JOIN accounts ac ON a.account_id = ac.account_id
JOIN transactions t ON a.transaction_id = t.transaction_id

WHERE
  -- Overdue (>7 days)
  DATEDIFF(DAY, a.detection_date, GETDATE()) > 7
  
  -- Not filed
  AND a.str_filed_date IS NULL
  
  -- Confirmed suspicion
  AND a.confirmation_status = 'CONFIRMED'

ORDER BY DATEDIFF(DAY, a.detection_date, GETDATE()) DESC;
```

---

## Part 3: Reconciliation & Audit Queries

### Query 5: FMR/STR Filing Audit Trail

```sql
-- ============================================================================
-- Compliance Audit: Track all FMR/STR filings with RBI/FIU-IND acknowledgments
-- ============================================================================

SELECT
  r.report_id,
  r.report_type,  -- 'FMR' or 'STR'
  r.submission_date,
  
  -- Authority details
  CASE WHEN r.report_type = 'FMR' THEN 'RBI' ELSE 'FIU-IND' END as authority,
  CASE WHEN r.report_type = 'FMR' THEN 'RBI Portal' ELSE 'FINnet Gateway' END as filing_portal,
  
  -- Submission status
  r.submission_status,
  r.receipt_id,
  r.authority_ack_id,
  r.authority_ack_date,
  
  CASE
    WHEN r.submission_status = 'SUBMITTED' AND r.authority_ack_date IS NOT NULL THEN 'ACKNOWLEDGED'
    WHEN r.submission_status = 'SUBMITTED' AND r.authority_ack_date IS NULL THEN 'PENDING ACK'
    WHEN r.submission_status = 'REJECTED' THEN 'REJECTED'
    ELSE r.submission_status
  END as filing_status,
  
  -- Submitted by whom
  r.submitted_by_user_id,
  r.submitted_by_role,
  
  -- Data integrity checks
  r.data_classification_verified,
  r.customer_masking_verified,
  r.digital_signature_valid,
  r.encryption_method,
  
  -- Linked alerts
  COUNT(a.alert_id) as linked_alerts,
  
  -- 7-year retention check
  CASE
    WHEN DATEDIFF(YEAR, r.submission_date, GETDATE()) < 7 THEN 'IN RETENTION'
    ELSE 'ELIGIBLE FOR PURGE'
  END as retention_status

FROM fmr_str_audit_trail r
LEFT JOIN fmr_str_alert_mapping a ON r.report_id = a.report_id

GROUP BY
  r.report_id, r.report_type, r.submission_date, r.submission_status,
  r.receipt_id, r.authority_ack_id, r.authority_ack_date,
  r.submitted_by_user_id, r.submitted_by_role,
  r.data_classification_verified, r.customer_masking_verified,
  r.digital_signature_valid, r.encryption_method

ORDER BY r.submission_date DESC;
```

---

### Query 6: Monthly Compliance Dashboard

```sql
-- ============================================================================
-- Monthly Compliance Dashboard: KPIs for CRO/CCO review
-- ============================================================================

SELECT
  FORMAT(GETDATE(), 'MMMM yyyy') as reporting_month,
  
  -- FMR metrics
  COUNT(DISTINCT CASE WHEN f.detection_date >= DATEADD(MONTH, -1, GETDATE()) AND f.fraud_id IS NOT NULL THEN f.fraud_id END) as frauds_detected_this_month,
  COUNT(DISTINCT CASE WHEN f.reported_to_rbi_date >= DATEADD(MONTH, -1, GETDATE()) THEN f.fraud_id END) as fmrs_submitted_this_month,
  COUNT(DISTINCT CASE WHEN f.reported_to_rbi_date IS NULL AND DATEDIFF(DAY, f.detection_date, GETDATE()) > 21 THEN f.fraud_id END) as fmr_overdue_count,
  
  -- STR metrics
  COUNT(DISTINCT CASE WHEN a.detection_date >= DATEADD(MONTH, -1, GETDATE()) AND a.aml_alert_id IS NOT NULL THEN a.aml_alert_id END) as aml_alerts_this_month,
  COUNT(DISTINCT CASE WHEN a.str_filed_date >= DATEADD(MONTH, -1, GETDATE()) THEN a.aml_alert_id END) as strs_submitted_this_month,
  COUNT(DISTINCT CASE WHEN a.str_filed_date IS NULL AND DATEDIFF(DAY, a.detection_date, GETDATE()) > 7 THEN a.aml_alert_id END) as str_overdue_count,
  
  -- Financial impact
  FORMAT(SUM(f.fraud_amount), 2) as total_fraud_loss_this_month,
  FORMAT(SUM(f.amount_recovered), 2) as fraud_recovery_this_month,
  
  -- SLA compliance
  CONCAT(
    CASE
      WHEN COUNT(DISTINCT CASE WHEN f.reported_to_rbi_date IS NULL AND DATEDIFF(DAY, f.detection_date, GETDATE()) > 21 THEN f.fraud_id END) = 0
        AND COUNT(DISTINCT CASE WHEN a.str_filed_date IS NULL AND DATEDIFF(DAY, a.detection_date, GETDATE()) > 7 THEN a.aml_alert_id END) = 0
      THEN '✓ COMPLIANT'
      ELSE '✗ BREACH'
    END
  ) as fmr_str_sla_status

FROM fraud_alerts f
FULL OUTER JOIN aml_alerts a ON 1=1

WHERE
  f.detection_date >= DATEADD(MONTH, -1, GETDATE())
  OR a.detection_date >= DATEADD(MONTH, -1, GETDATE());
```

---

## Usage Instructions

1. **For FMR Immediate Filing (3-week SLA):**
   - Run Query 1 daily → Extract frauds pending FMR filing
   - Populate RBI Excel template
   - Submit via RBI Portal

2. **For FMR Quarterly Consolidation:**
   - Run Query 2 on quarter-end (Mar 31, Jun 30, Sep 30, Dec 31)
   - Verify numbers match individual filings
   - Submit consolidated return to RBI

3. **For STR Filing (7-day SLA):**
   - Run Query 3 daily → Extract AML alerts pending STR filing
   - Generate FIU-IND XML format
   - Submit via FINnet Gateway within 7 days

4. **For Overdue Alert Escalation:**
   - Run Query 4 daily at 1 AM
   - If results exist → Send CRITICAL alert to CRO/CCO/Legal
   - Immediate remediation required

5. **For Compliance Audit (RBI inspection prep):**
   - Run Query 5 to show all FMR/STR filings with acknowledgments
   - Verify 7-year retention on all records
   - Prepare for RBI audit

6. **For Monthly Compliance Dashboard:**
   - Run Query 6 on 1st of each month
   - Share with CRO/CCO/Board
   - Track KPIs: SLA compliance, fraud loss, recovery rate

---

## Notes

- All queries include data masking per RBI/FIU-IND privacy requirements
- Replace table names based on your core banking system (CBS)
- SLA dates: FMR = 21 days (3 weeks), STR = 7 days
- Run Query 4 daily to catch STR violations before they happen

