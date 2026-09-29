# SLA Monitoring Dashboard
## Manual Excel Tracker for FMR/STR Compliance

**Purpose:** Track FMR/STR filing status and SLA compliance  
**Update Frequency:** Daily (automated query) or manually every day  
**Audience:** CRO, CCO, Compliance Manager  
**Format:** Excel spreadsheet (instructions below)

---

## Dashboard Overview

The dashboard tracks 2 key SLAs:

1. **FMR (Fraud Monitoring Return)** → RBI  
   - Deadline: 3 weeks (21 days) from fraud detection
   - Target: 100% on-time filing

2. **STR (Suspicious Transaction Report)** → FIU-IND  
   - Deadline: 7 days from AML suspicion detection
   - Target: 100% on-time filing

---

## How to Create the Dashboard (Manual)

### **Step 1: Create Excel File**

```
File name: FMR_STR_SLA_Dashboard_YYYY-MM.xlsx
Save location: \\[shared drive]\Compliance\FMR_STR\
Update frequency: Daily
Owner: [Compliance Manager Name]
```

### **Step 2: Create Worksheet 1 - FMR Tracker**

**Column Headers:**

| Column | Header | Format | Example |
|---|---|---|---|
| A | Date | MM-DD-YYYY | 08-10-2026 |
| B | Fraud ID | Text | FRAUD-001 |
| C | Detection Date | MM-DD-YYYY | 07-20-2026 |
| D | Days Since Detection | Number | 21 |
| E | Days Until Deadline | Number | 0 |
| F | Amount (₹) | Currency | 250000 |
| G | Status | Dropdown | Detected / Filed / Overdue |
| H | RBI Receipt ID | Text | RBI-FMR-20260810-001 |
| I | Filed Date | MM-DD-YYYY | 08-10-2026 |
| J | Days Early/Late | Number | +2 (early) / -1 (late) |
| K | Notes | Text | Standard case / High value / Customer dispute |

**Sample Data:**

```
Date      | Fraud ID  | Detection Date | Days Old | Days Left | Amount | Status | Receipt ID | Filed Date | Days E/L | Notes
──────────┼───────────┼────────────────┼──────────┼───────────┼────────┼────────┼────────────┼────────────┼──────────┼─────
08-10-2026| FRAUD-001 | 07-20-2026     | 21       | 0         | 250K   | Filed  | RBI-20260810-001 | 08-09-2026 | +1 | On time
08-10-2026| FRAUD-002 | 07-25-2026     | 16       | 5         | 500K   | Detected | —     | —          | —  | Pending filing
08-10-2026| FRAUD-003 | 08-05-2026     | 5        | 16        | 150K   | Detected | —     | —          | —  | New case
```

**Conditional Formatting:**

- **Status = "Detected" & Days Until Deadline < 0:** 🔴 RED (Overdue)
- **Status = "Detected" & Days Until Deadline 1-3:** 🟠 ORANGE (Alert)
- **Status = "Detected" & Days Until Deadline > 3:** 🟡 YELLOW (Normal)
- **Status = "Filed":** 🟢 GREEN (Compliant)
- **Status = "Overdue":** 🔴 RED (Non-compliant)

---

### **Step 3: Create Worksheet 2 - STR Tracker**

**Column Headers:**

| Column | Header | Format | Example |
|---|---|---|---|
| A | Date | MM-DD-YYYY | 08-10-2026 |
| B | AML Alert ID | Text | AML-001 |
| C | Detection Date | MM-DD-YYYY | 08-03-2026 |
| D | Days Since Detection | Number | 7 |
| E | Days Until Deadline | Number | 0 |
| F | Suspicion Type | Dropdown | Structuring / Sanctions / PEP / Layering |
| G | Amount (₹) | Currency | 999000 |
| H | Risk Score | Number (0-100) | 85 |
| I | Status | Dropdown | Detected / Filed / Overdue |
| J | FIU Ack ID | Text | ACK-FIU-20260810-001 |
| K | Filed Date | MM-DD-YYYY | 08-10-2026 |
| L | Days Early/Late | Number | 0 (on time) |
| M | Notes | Text | High-risk structuring / Sanctions match |

**Sample Data:**

```
Date      | AML ID  | Detection Date | Days Old | Days Left | Type | Amount | Risk | Status | Ack ID | Filed Date | Days E/L | Notes
──────────┼─────────┼────────────────┼──────────┼───────────┼──────┼────────┼──────┼────────┼────────┼────────────┼──────────┼──────
08-10-2026| AML-001 | 08-03-2026     | 7        | 0         | Struct. | 999K | 85   | Filed  | ACK-20260810-001 | 08-09-2026 | 0 | Reported
08-10-2026| AML-002 | 08-04-2026     | 6        | 1         | Sanctions | 500K | 92   | Detected | — | — | — | Pending filing - HIGH PRIORITY
08-10-2026| AML-003 | 08-07-2026     | 3        | 4         | PEP | 750K | 78   | Detected | — | — | — | New case
```

**Conditional Formatting:**

- **Status = "Detected" & Days Until Deadline < 0:** 🔴 RED (PMLA VIOLATION)
- **Status = "Detected" & Days Until Deadline 1-2:** 🔴 RED (Urgent escalation)
- **Status = "Detected" & Days Until Deadline 3-7:** 🟠 ORANGE (Alert)
- **Status = "Filed":** 🟢 GREEN (Compliant)
- **Status = "Overdue":** 🔴 RED (Criminal breach)

---

### **Step 4: Create Worksheet 3 - Summary Dashboard**

**Overview Metrics:**

| Metric | Formula | Target | Status |
|---|---|---|---|
| **FMR Metrics** | | | |
| Total Frauds Detected (YTD) | =COUNTA(FMR!B:B) | [Number] | [Count] |
| Frauds Filed to RBI | =COUNTIF(FMR!G:G,"Filed") | 100% | [%] |
| FMR On-Time Compliance | =COUNTIF(FMR!J:J,">=0")/COUNTA(FMR!B:B) | 100% | [%] |
| Frauds Overdue for Filing | =COUNTIF(FMR!G:G,"Overdue") | 0 | [Count] |
| Avg Filing Time (days) | =AVERAGE(FMR!J:J) | ≤14 | [Days] |
| **STR Metrics** | | | |
| Total AML Alerts (YTD) | =COUNTA(STR!B:B) | [Number] | [Count] |
| Alerts Filed to FIU | =COUNTIF(STR!I:I,"Filed") | 100% | [%] |
| STR 7-Day SLA Compliance | =COUNTIF(STR!L:L,"<=0")/COUNTA(STR!B:B) | 100% | [%] |
| Alerts Overdue (PMLA Violation) | =COUNTIF(STR!I:I,"Overdue") | 0 | [Count] |
| Avg Filing Time (days) | =AVERAGE(STR!L:L) | ≤3 | [Days] |
| **Overall Status** | | | |
| System Compliance Score | See formula below | 100% | [%] |

**System Compliance Score Formula:**

```
= (FMR Compliance × 50%) + (STR Compliance × 50%)

Target: 100% (both on track)
Green: ≥95% compliance
Yellow: 80–95% compliance  
Red: <80% compliance (immediate action needed)
```

**Example Summary Dashboard:**

```
═══════════════════════════════════════════════════════════════════════
                        SLA MONITORING SUMMARY
                       As of August 10, 2026
═══════════════════════════════════════════════════════════════════════

FMR METRICS (Fraud Monitoring Return - RBI)
───────────────────────────────────────────────────────────────────────
Total Frauds Detected (YTD):                           127 cases
Frauds Filed to RBI:                                   125 cases (98%)
Frauds Pending Filing:                                 2 cases
Frauds Overdue (>21 days):                             0 cases ✅
FMR Compliance Score:                                  98% 🟢

STR METRICS (Suspicious Transaction Report - FIU-IND)
───────────────────────────────────────────────────────────────────────
Total AML Alerts (YTD):                                18 cases
Alerts Filed to FIU:                                   18 cases (100%)
Alerts Pending Filing:                                 0 cases
Alerts Overdue (>7 days - PMLA VIOLATION):             0 cases ✅
STR Compliance Score:                                  100% 🟢

OVERALL SYSTEM COMPLIANCE SCORE:                       99% 🟢
───────────────────────────────────────────────────────────────────────

Status:                                                ✅ COMPLIANT
Last Update:                                           08-10-2026 5:00 PM
Next Review:                                           08-11-2026 9:00 AM

═══════════════════════════════════════════════════════════════════════
```

---

## Automated Dashboard (Using SQL + Python)

### **Daily Automated Update Script**

If you want to automate the dashboard update, use this Python script:

```python
# dashboard_updater.py
import pandas as pd
from sqlalchemy import create_engine
import openpyxl
from openpyxl.styles import PatternFill, Font
from datetime import datetime, timedelta

def update_sla_dashboard(database_url, output_file):
    """Automatically update SLA dashboard from database"""
    
    engine = create_engine(database_url)
    
    # Extract FMR data
    fmr_query = """
        SELECT 
            GETDATE() as date,
            fraud_id,
            detection_date,
            DATEDIFF(DAY, detection_date, GETDATE()) as days_old,
            21 - DATEDIFF(DAY, detection_date, GETDATE()) as days_left,
            fraud_amount,
            CASE
                WHEN reported_to_rbi_date IS NULL AND DATEDIFF(DAY, detection_date, GETDATE()) > 21 THEN 'Overdue'
                WHEN reported_to_rbi_date IS NULL THEN 'Detected'
                ELSE 'Filed'
            END as status,
            fmr_receipt_id,
            reported_to_rbi_date,
            DATEDIFF(DAY, detection_date, COALESCE(reported_to_rbi_date, GETDATE())) - 21 as days_early_late,
            'See notes' as notes
        FROM fraud_alerts
        ORDER BY detection_date DESC
    """
    
    # Extract STR data
    str_query = """
        SELECT
            GETDATE() as date,
            aml_alert_id,
            detection_date,
            DATEDIFF(DAY, detection_date, GETDATE()) as days_old,
            7 - DATEDIFF(DAY, detection_date, GETDATE()) as days_left,
            suspicion_type,
            transaction_amount,
            aml_risk_score,
            CASE
                WHEN str_filed_date IS NULL AND DATEDIFF(DAY, detection_date, GETDATE()) > 7 THEN 'Overdue'
                WHEN str_filed_date IS NULL THEN 'Detected'
                ELSE 'Filed'
            END as status,
            str_ack_id,
            str_filed_date,
            DATEDIFF(DAY, detection_date, COALESCE(str_filed_date, GETDATE())) - 7 as days_early_late,
            'See notes' as notes
        FROM aml_alerts
        ORDER BY detection_date DESC
    """
    
    # Load data
    fmr_df = pd.read_sql(fmr_query, engine)
    str_df = pd.read_sql(str_query, engine)
    
    # Create Excel workbook
    with pd.ExcelWriter(output_file) as writer:
        fmr_df.to_excel(writer, sheet_name='FMR Tracker', index=False)
        str_df.to_excel(writer, sheet_name='STR Tracker', index=False)
        
        # Add summary sheet
        summary_data = {
            'Metric': [
                'Total Frauds Detected',
                'Frauds Filed to RBI',
                'Frauds Pending',
                'Frauds Overdue',
                'FMR Compliance %',
                'Total AML Alerts',
                'Alerts Filed to FIU',
                'Alerts Pending',
                'Alerts Overdue (PMLA Violation)',
                'STR Compliance %',
                'Overall System Compliance %'
            ],
            'Value': [
                len(fmr_df),
                len(fmr_df[fmr_df['status'] == 'Filed']),
                len(fmr_df[fmr_df['status'] == 'Detected']),
                len(fmr_df[fmr_df['status'] == 'Overdue']),
                f"{len(fmr_df[fmr_df['status'] != 'Overdue']) / len(fmr_df) * 100:.1f}%",
                len(str_df),
                len(str_df[str_df['status'] == 'Filed']),
                len(str_df[str_df['status'] == 'Detected']),
                len(str_df[str_df['status'] == 'Overdue']),
                f"{len(str_df[str_df['status'] != 'Overdue']) / len(str_df) * 100:.1f}%",
                "✅ Compliant" if len(fmr_df[fmr_df['status'] == 'Overdue']) == 0 and len(str_df[str_df['status'] == 'Overdue']) == 0 else "🔴 Action Required"
            ]
        }
        summary_df = pd.DataFrame(summary_data)
        summary_df.to_excel(writer, sheet_name='Summary', index=False)
    
    print(f"✅ Dashboard updated: {output_file}")

# Run daily
if __name__ == "__main__":
    update_sla_dashboard(
        database_url='postgresql://user:pass@localhost/frms',
        output_file='FMR_STR_SLA_Dashboard_YYYY-MM.xlsx'
    )
```

### **Schedule This Script**

**Windows Task Scheduler:**
```
Trigger: Daily at 6:00 AM
Action: python C:\scripts\dashboard_updater.py
Notification: Email summary to CRO/CCO if any red flags
```

**Linux Cron:**
```bash
0 6 * * * python /scripts/dashboard_updater.py >> /logs/dashboard.log 2>&1
```

---

## Dashboard Alerts & Thresholds

### **Auto-Alert Rules**

```
IF FMR Frauds Overdue > 0:
  THEN send RED ALERT to CRO, Compliance Manager
  SUBJECT: "FMR SLA BREACH: X frauds overdue for RBI filing"
  ACTION: File within 24 hours

IF STR Alerts Overdue > 0:
  THEN send RED ALERT to CCO, Legal, Compliance Manager
  SUBJECT: "STR SLA BREACH: X AML alerts overdue for FIU filing (PMLA VIOLATION)"
  ACTION: File within 24 hours, notify board

IF FMR Compliance < 95%:
  THEN send ORANGE ALERT to CRO
  SUBJECT: "FMR Compliance Declining: X% (target 100%)"
  ACTION: Review cases pending filing, expedite

IF STR Compliance < 95%:
  THEN send ORANGE ALERT to CCO
  SUBJECT: "STR Compliance Declining: X% (target 100%)"
  ACTION: Review pending alerts, expedite
```

---

## Weekly Review Checklist

**Every Friday @ 5 PM:**

- [ ] Download latest FMR Tracker data
- [ ] Download latest STR Tracker data
- [ ] Update Summary Dashboard
- [ ] Check for any red flags (overdue cases)
- [ ] Generate auto-alerts (if threshold breached)
- [ ] Send weekly status email to CRO/CCO
- [ ] Archive previous week's file
- [ ] Note any issues/remediation actions

**Monthly Review (First Friday of month):**

- [ ] Review YTD compliance trends
- [ ] Identify any systemic issues
- [ ] Prepare board report
- [ ] Update SLA targets (if needed)
- [ ] Brief Audit Committee

---

## Dashboard KPIs & Targets

### **FMR KPIs**

| KPI | Current | Target | Action If Missing |
|---|---|---|---|
| % Frauds filed on-time (≤21 days) | X% | 100% | Review process, expedite |
| Average filing time | X days | ≤14 days | Improve efficiency |
| % High-value frauds (>500K) filed on-time | X% | 100% | Priority processing |
| RBI receipt/ack rate | X% | 100% | Follow up with RBI |
| Data masking compliance | X% | 100% | Audit all FMRs |

### **STR KPIs**

| KPI | Current | Target | Action If Missing |
|---|---|---|---|
| % AML alerts filed on-time (≤7 days) | X% | 100% | PMLA violation risk |
| Average filing time | X days | ≤3 days | Improve process |
| % High-risk alerts (score >80) filed on-time | X% | 100% | Priority processing |
| FIU ack rate | X% | 100% | Follow up with FIU |
| Digital signature validity | X% | 100% | Audit all STRs |

---

## Troubleshooting Common Issues

### **Issue 1: Dashboard shows RED (Frauds Overdue)**

```
Problem:  X frauds >21 days old, not filed to RBI
Severity: 🔴 CRITICAL
Action:
  1. Identify which frauds are overdue
  2. Prepare catch-up FMR Excel
  3. Obtain CRO signature
  4. File to RBI portal immediately
  5. Log receipt ID in dashboard
  6. Send explanation letter to RBI
```

### **Issue 2: Dashboard shows RED (STR Overdue)**

```
Problem:  Y AML alerts >7 days old, not filed to FIU
Severity: 🔴 CRITICAL (PMLA VIOLATION)
Action:
  1. Identify which alerts are overdue
  2. Prepare catch-up STR XML
  3. Obtain CCO signature
  4. File to FINnet immediately
  5. Log ack ID in dashboard
  6. Notify Legal team
  7. Brief board on breach + remediation
```

### **Issue 3: Dashboard won't update**

```
Problem:  Database connectivity lost, or query failed
Severity: 🟠 HIGH
Action:
  1. Check database connectivity
  2. Verify SQL query syntax
  3. Manually extract data from database
  4. Update dashboard Excel manually
  5. Escalate to IT if ongoing
```

---

## Dashboard Retention & Archival

**Retention:** Keep dashboard files for 7 years (audit trail)  
**Archive:** End of month, save as:  
```
FMR_STR_SLA_Dashboard_YYYY-MM_ARCHIVE.xlsx
Location: \\[shared drive]\Compliance\FMR_STR\Archive\
```

**Access Control:** Dashboard contains sensitive info  
- Read: CRO, CCO, Compliance Manager, Audit Committee
- Write: Compliance Manager only
- Share: Use secure email only (no cloud storage)

---

**End of Dashboard Documentation**

