# FMR/STR Implementation Roadmap
## 4–6 Week Plan to Automate Regulatory Reporting in Current FRMS

**Objective:** Build automated FMR & STR generation for RBI and FIU-IND compliance  
**Timeline:** 4–6 weeks from start to production  
**Scope:** Interim automation while Fraud360 MVP is being built  
**Owner:** Platform/Development Team  
**Status:** Ready to start (Task BR-504 / BR-505)

---

## Executive Summary

| Item | Current State | Target State | Effort | Timeline |
|---|---|---|---|---|
| **FMR Generation** | Manual Excel | Automated PDF + RBI Portal API | 80 hours | Week 1–3 |
| **STR Generation** | Manual XML | Automated XML + FINnet Gateway API | 100 hours | Week 2–4 |
| **Dashboard** | None | Real-time compliance dashboard | 40 hours | Week 3–5 |
| **Testing & QA** | N/A | End-to-end testing with test portals | 60 hours | Week 4–5 |
| **Deployment & Training** | N/A | Production rollout + team training | 40 hours | Week 5–6 |
| **Total Effort** | — | — | **320 hours (~2 FTE)** | **6 weeks** |

---

## Phase 1: Requirements & Design (Week 1)

### **1.1 Requirements Gathering**

**Deliverable:** FMR/STR Requirements Document

**Tasks:**
- [ ] Interview CRO/CCO for current fraud/AML reporting process
- [ ] Document current manual FMR workflow (how frauds are reported today)
- [ ] Document current manual STR workflow (if any)
- [ ] Collect sample FMR/STR filings from past 12 months
- [ ] Identify data sources (fraud alerts, AML alerts, customer data, transaction data)
- [ ] Map FRMS alert types to FMR/STR classification
- [ ] Document SLA requirements:
  - [ ] FMR: 3 weeks for individual cases, quarterly consolidation
  - [ ] STR: 7 days from suspicion detection

**Owner:** Business Analyst + Compliance Officer  
**Effort:** 16 hours  
**Output:** Requirements document (1–2 pages)

---

### **1.2 Technical Design**

**Deliverable:** Technical Architecture Document

**Tasks:**
- [ ] Design database schema for FMR/STR tracking
- [ ] Design API for portal submissions (RBI & FINnet)
- [ ] Design audit trail table (7-year immutable logs)
- [ ] Map FRMS database tables → FMR/STR template fields
- [ ] Document data masking rules (PAN, Aadhaar, account, customer name)
- [ ] Design encryption/digital signature approach
- [ ] Design SLA monitoring dashboard

**Database Schema Overview:**

```sql
-- Tables to create
1. fmr_alerts
   - fraud_id, detection_date, fraud_type, amount, account_id, status
   - modus_operandi, action_taken, reported_date, fmr_receipt_id

2. str_alerts
   - aml_alert_id, detection_date, suspicion_type, amount, account_id
   - beneficiary_details, narrative, filed_date, str_ack_id

3. fmr_str_audit_trail
   - report_id, report_type, submission_date, submitted_by
   - receipt_id, ack_id, authority_response, hash_value (immutable)
   - expires_at (7-year retention)
```

**Owner:** Platform Architect + Database Admin  
**Effort:** 24 hours  
**Output:** Architecture document + ER diagram

---

## Phase 2: Development (Weeks 2–4)

### **2.1 FMR Module Development (Week 2–3)**

**Deliverable:** FMR Generation Engine + API

**Backend Tasks (Python/FastAPI):**

```python
# Core FMR Module Structure
├── fmr/
│   ├── models.py              # FMR data models
│   ├── extractor.py           # Extract fraud data from FRMS
│   ├── formatter.py           # Format as RBI Excel/PDF
│   ├── validator.py           # Validate against RBI rules
│   ├── masking.py             # Data masking (PAN, Aadhaar, etc.)
│   ├── portal_api.py          # RBI portal API integration
│   └── scheduler.py           # Schedule daily FMR generation

# Endpoints
POST /fmr/generate             # Trigger FMR generation
GET  /fmr/pending              # List pending FMR cases
POST /fmr/submit-to-rbi        # Submit to RBI portal
GET  /fmr/receipt/{receipt_id} # Get RBI acknowledgment
```

**Development Tasks:**
- [ ] Create FMR data models (Pydantic)
- [ ] Extract fraud cases from FRMS database
  - Query: `SELECT * FROM fraud_alerts WHERE detected_date > 21 days ago AND reported_to_rbi = FALSE`
- [ ] Implement data masking for PAN, Aadhaar, account, customer name
- [ ] Create FMR Excel formatter (openpyxl library)
  - Populate RBI template with fraud data
  - Add charts (fraud type breakdown, amount trend)
- [ ] Create FMR PDF generator (reportlab)
- [ ] Implement FMR validator
  - Validate mandatory fields
  - Validate date ranges
  - Validate amount formatting
- [ ] Create RBI portal API integration
  - Authentication (API key + digital signature)
  - HTTPS submission
  - Receipt tracking
- [ ] Create daily scheduler (APScheduler)
  - Run at 2 AM daily
  - Generate FMR for cases within 3 weeks
  - Alert if any case exceeds 3-week SLA

**Code Example:**

```python
# fmr/extractor.py
from datetime import datetime, timedelta
from sqlalchemy import select

class FMRExtractor:
    def get_pending_fraud_cases(self):
        """Extract frauds detected within 3 weeks, pending FMR filing"""
        
        three_weeks_ago = datetime.now() - timedelta(days=21)
        
        query = select([fraud_alerts]).where(
            (fraud_alerts.c.detection_date >= three_weeks_ago) &
            (fraud_alerts.c.reported_to_rbi_date == None) &
            (fraud_alerts.c.confirmation_status == 'CONFIRMED')
        )
        
        cases = db.execute(query).fetchall()
        return [self._map_to_fmr_case(c) for c in cases]
    
    def _map_to_fmr_case(self, fraud_row):
        """Map database row to FMRCase object"""
        
        return FMRCase(
            fraud_id=fraud_row.fraud_id,
            detection_date=fraud_row.detection_date,
            fraud_type=fraud_row.fraud_type,
            amount=fraud_row.fraud_amount,
            customer_name_masked=self.mask_customer_name(fraud_row.customer_name),
            account_masked=self.mask_account(fraud_row.account_number),
            modus_operandi=fraud_row.modus_operandi,
            action_taken=fraud_row.action_taken,
            status=fraud_row.status
        )

# fmr/masking.py
class DataMasking:
    @staticmethod
    def mask_pan(pan):
        """Mask PAN: Show only last 4 digits"""
        return f"****{pan[-4:]}"
    
    @staticmethod
    def mask_aadhaar(aadhaar):
        """Mask Aadhaar: Show only last 4 digits"""
        return f"****{aadhaar[-4:]}"
    
    @staticmethod
    def mask_account(account):
        """Mask Account: Show first 4 and last 4"""
        return f"{account[:4]}****{account[-4:]}"
    
    @staticmethod
    def mask_customer_name(name):
        """Mask Name: Surname + First initial"""
        parts = name.split()
        return f"{parts[0][:1]}****** {' '.join(parts[1:])}"

# fmr/api.py
from fastapi import APIRouter

router = APIRouter(prefix="/fmr", tags=["FMR"])

@router.post("/generate")
async def generate_fmr():
    """Trigger FMR generation for pending cases"""
    
    extractor = FMRExtractor()
    pending_cases = extractor.get_pending_fraud_cases()
    
    if not pending_cases:
        return {"message": "No pending FMR cases"}
    
    # Generate FMR Excel
    formatter = FMRFormatter()
    excel_file = formatter.generate_excel(pending_cases)
    
    # Log generation
    audit_log.create({
        'report_type': 'FMR',
        'case_count': len(pending_cases),
        'generated_at': datetime.now(),
        'generated_by': 'automation'
    })
    
    return {
        "status": "Generated",
        "cases": len(pending_cases),
        "file": excel_file
    }

@router.post("/submit-to-rbi")
async def submit_fmr_to_rbi(fmr_file_path: str):
    """Submit FMR to RBI portal"""
    
    # Read file
    with open(fmr_file_path, 'rb') as f:
        file_content = f.read()
    
    # Encrypt with RBI certificate
    encrypted_content = encrypt_with_rbi_cert(file_content)
    
    # Submit to RBI portal
    response = requests.post(
        url="https://rbi-fraud-portal.rbi.org.in/api/fmr/submit",
        data=encrypted_content,
        headers={
            "Authorization": f"Bearer {RBI_API_KEY}",
            "Content-Type": "application/octet-stream"
        }
    )
    
    # Log submission
    receipt_id = response.json()['receipt_id']
    audit_log.create({
        'report_type': 'FMR',
        'submission_date': datetime.now(),
        'receipt_id': receipt_id,
        'status': 'SUBMITTED'
    })
    
    return {"receipt_id": receipt_id, "status": "SUBMITTED"}
```

**Owner:** Backend Engineer (1 FTE)  
**Effort:** 60 hours  
**Output:** FMR module ready for testing

---

### **2.2 STR Module Development (Week 2–4)**

**Deliverable:** STR Generation Engine + FINnet API

**Backend Tasks:**

```python
# Core STR Module Structure
├── str/
│   ├── models.py              # STR data models
│   ├── extractor.py           # Extract AML alerts from FRMS
│   ├── classifier.py          # Classify suspicion type
│   ├── xml_generator.py       # Generate FIU-IND XML
│   ├── validator.py           # Validate against FIU rules
│   ├── masking.py             # Customer data masking
│   ├── signer.py              # Digital signature (CA cert)
│   ├── finnet_api.py          # FINnet Gateway integration
│   └── scheduler.py           # Schedule STR generation

# Endpoints
POST /str/generate             # Trigger STR generation
GET  /str/pending              # List pending STR alerts
POST /str/submit-to-fiu        # Submit to FINnet Gateway
GET  /str/ack/{ack_id}         # Get FIU acknowledgment
GET  /str/overdue              # Check 7-day SLA violations
```

**Development Tasks:**
- [ ] Create STR data models (Pydantic)
- [ ] Extract AML alerts from FRMS database
  - Query: `SELECT * FROM aml_alerts WHERE detected_date <= 7 days ago AND filed_to_fiu = FALSE`
- [ ] Implement suspicion classification logic
  - Map Lane B signals to FIU suspicion types (Structuring, Layering, Sanctions, PEP)
- [ ] Implement data masking (same as FMR)
- [ ] Create STR XML generator (xml.etree.ElementTree)
  - FIU-IND prescribed format
  - Populate all required fields
- [ ] Implement STR validator
  - Validate XML against FIU XSD schema
  - Validate mandatory fields
  - Validate narrative length (<500 chars)
- [ ] Create digital signature module
  - Sign with CRO/CCO certificate (CA-issued)
  - SHA-256 hash
- [ ] Create FINnet Gateway API integration
  - Authentication (OAuth + certificate)
  - XML submission
  - Acknowledgment tracking
- [ ] Create event-triggered STR generation (listener)
  - Trigger when AML alert created
  - 7-day SLA monitoring
  - Escalation if not filed within 7 days

**Code Example:**

```python
# str/extractor.py
from datetime import datetime, timedelta

class STRExtractor:
    def get_pending_aml_alerts(self):
        """Extract AML alerts within 7-day STR filing window"""
        
        seven_days_ago = datetime.now() - timedelta(days=7)
        
        query = select([aml_alerts]).where(
            (aml_alerts.c.detection_date >= seven_days_ago) &
            (aml_alerts.c.str_filed_date == None) &
            (aml_alerts.c.confirmation_status == 'CONFIRMED')
        )
        
        alerts = db.execute(query).fetchall()
        return [self._map_to_str_alert(a) for a in alerts]
    
    def get_overdue_str_alerts(self):
        """Get STR alerts overdue for filing (>7 days)"""
        
        query = select([aml_alerts]).where(
            (datetime.now() - aml_alerts.c.detection_date > timedelta(days=7)) &
            (aml_alerts.c.str_filed_date == None)
        )
        
        overdue = db.execute(query).fetchall()
        return overdue

# str/classifier.py
class SuspicionClassifier:
    SIGNAL_TO_SUSPICION = {
        'SME-01': 'STRUCTURING',
        'SME-03': 'INVOICE_FRAUD',
        'CPT-01': 'SANCTIONS',
        'CPT-02': 'PEP',
        'LAY-03': 'LAYERING',
        'TBM-02': 'TRADE_FINANCE_FRAUD'
    }
    
    def classify_suspicion(self, aml_alert):
        """Map Lane B signal to FIU suspicion type"""
        
        signal = aml_alert.signal_code
        return self.SIGNAL_TO_SUSPICION.get(signal, 'OTHER')

# str/xml_generator.py
import xml.etree.ElementTree as ET

class STRXMLGenerator:
    def generate_str_xml(self, str_alert):
        """Generate FIU-IND compliant XML"""
        
        # Root element
        root = ET.Element('SuspiciousTransactionReport')
        
        # Reporting entity
        reporting_entity = ET.SubElement(root, 'ReportingEntity')
        ET.SubElement(reporting_entity, 'EntityName').text = 'Finverge Bank Ltd'
        ET.SubElement(reporting_entity, 'EntityID').text = 'BANK123456'
        
        # STR details
        str_details = ET.SubElement(root, 'STRDetails')
        ET.SubElement(str_details, 'UniqueSTRID').text = str(uuid.uuid4())
        ET.SubElement(str_details, 'FilingDate').text = datetime.now().isoformat()
        
        # Customer (masked)
        customer = ET.SubElement(root, 'Customer')
        ET.SubElement(customer, 'CustomerName').text = self.mask_name(str_alert.customer_name)
        ET.SubElement(customer, 'PAN').text = self.mask_pan(str_alert.kyc_pan)
        ET.SubElement(customer, 'Aadhaar').text = self.mask_aadhaar(str_alert.kyc_aadhaar)
        
        # Transaction
        transaction = ET.SubElement(root, 'Transaction')
        ET.SubElement(transaction, 'TransactionID').text = str_alert.transaction_id
        ET.SubElement(transaction, 'Amount').text = str(str_alert.amount)
        ET.SubElement(transaction, 'Channel').text = str_alert.channel
        
        # Suspicion details
        suspicion = ET.SubElement(root, 'SuspicionDetails')
        ET.SubElement(suspicion, 'SuspicionType').text = str_alert.suspicion_type
        ET.SubElement(suspicion, 'Narrative').text = str_alert.narrative[:500]
        
        # Convert to string
        return ET.tostring(root, encoding='utf-8')

# str/signer.py
import hashlib
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

class DigitalSigner:
    def __init__(self, cert_path, key_path):
        self.cert = self.load_certificate(cert_path)
        self.key = self.load_private_key(key_path)
    
    def sign_str_xml(self, xml_content):
        """Sign STR XML with CA certificate"""
        
        # Hash the XML
        hash_digest = hashlib.sha256(xml_content).digest()
        
        # Sign with private key
        signature = self.key.sign(
            hash_digest,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        
        return signature

# str/finnet_api.py
class FINnetGateway:
    def submit_str_to_fiu(self, str_xml, signature):
        """Submit STR to FIU-IND via FINnet Gateway"""
        
        # Prepare submission
        submission = {
            'xml': str_xml,
            'signature': signature,
            'timestamp': datetime.now().isoformat()
        }
        
        # Submit to FINnet
        response = requests.post(
            url="https://finnet.fiu.gov.in/api/str/submit",
            json=submission,
            headers={
                "Authorization": f"Bearer {FINNET_API_KEY}",
                "Content-Type": "application/json"
            }
        )
        
        # Parse response
        ack_id = response.json()['acknowledgment_id']
        
        # Log submission
        audit_log.create({
            'report_type': 'STR',
            'submission_date': datetime.now(),
            'ack_id': ack_id,
            'status': 'SUBMITTED'
        })
        
        return ack_id

# str/scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler

class STRScheduler:
    def start_scheduling(self):
        scheduler = BackgroundScheduler()
        
        # Daily STR generation check (1 AM)
        scheduler.add_job(
            func=self.generate_pending_strs,
            trigger="cron",
            hour=1,
            minute=0,
            id="str_daily_generation"
        )
        
        # 7-day SLA monitoring (6 AM daily)
        scheduler.add_job(
            func=self.check_str_7day_sla,
            trigger="cron",
            hour=6,
            minute=0,
            id="str_sla_monitoring"
        )
        
        scheduler.start()
    
    def check_str_7day_sla(self):
        """Check for overdue STRs and escalate"""
        
        overdue = STRExtractor().get_overdue_str_alerts()
        
        if overdue:
            # Send CRITICAL alert
            send_alert(
                severity="CRITICAL",
                subject=f"STR Reporting SLA Breach: {len(overdue)} cases overdue",
                recipients=["CRO", "CCO", "Legal"],
                message=f"PMLA Compliance Risk: {len(overdue)} AML cases exceed 7-day STR filing deadline"
            )
```

**Owner:** Backend Engineer (1 FTE)  
**Effort:** 70 hours  
**Output:** STR module ready for testing

---

### **2.3 Compliance Dashboard Development (Week 3–5)**

**Deliverable:** Real-time FMR/STR Monitoring Dashboard

**Frontend Tasks (React/Vue):**

```
Dashboard Views:
1. Overview
   - FMRs submitted this month (count, amount)
   - STRs submitted this month (count, escalation status)
   - SLA compliance status (% on-time)

2. FMR Tracking
   - Pending FMR cases (within 3 weeks)
   - FMR submission status (Pending, Submitted, Acknowledged)
   - FMR rejection tracking
   - Branch-wise fraud distribution

3. STR Monitoring
   - Pending STR alerts (within 7 days)
   - STR overdue alerts (CRITICAL - 7 days exceeded)
   - STR acknowledgment status
   - FIU-IND response tracking

4. Audit Trail
   - All FMR/STR filings (date, submission portal, receipt)
   - RBI/FIU acknowledgments
   - Approval chain (analyst → compliance → CCO)

5. Analytics
   - Fraud trends (type, amount, recovery)
   - AML trends (suspicion type, geography)
   - Detection latency trends
```

**Development Tasks:**
- [ ] Create FMR/STR data models (backend)
- [ ] Design dashboard UI/UX (Figma mockups)
- [ ] Build React components (or Vue)
  - FMR pending cases table
  - STR overdue alerts
  - SLA compliance gauge
  - Audit trail timeline
- [ ] Implement real-time updates (WebSocket)
- [ ] Add filtering/search (branch, date, status)
- [ ] Create export functionality (PDF, Excel)

**Owner:** Frontend Engineer (1 FTE)  
**Effort:** 40 hours  
**Output:** Dashboard deployed and accessible

---

## Phase 3: Testing & QA (Week 4–5)

### **3.1 Unit Testing**

**Deliverable:** Test coverage >80%

**Tests to Write:**
- [ ] FMR extraction (happy path, edge cases, null handling)
- [ ] Data masking (PAN, Aadhaar, account, name)
- [ ] Excel generation (format, required fields, data accuracy)
- [ ] STR XML generation (valid XML, FIU schema compliance)
- [ ] Digital signature (signature verification)
- [ ] SLA monitoring (7-day threshold, overdue alerts)
- [ ] Audit logging (immutable records, retention)

```python
# test_fmr.py
def test_fmr_extraction():
    """Test FMR extraction from fraud_alerts table"""
    
    # Create mock fraud case
    fraud_case = FraudAlert(
        fraud_id="FRAUD-001",
        detection_date=datetime.now() - timedelta(days=10),
        fraud_type="DIGITAL",
        amount=250000,
        status="CONFIRMED"
    )
    
    # Extract
    extractor = FMRExtractor()
    result = extractor.get_pending_fraud_cases()
    
    # Assert
    assert len(result) >= 1
    assert result[0].fraud_id == "FRAUD-001"
    assert result[0].amount == 250000

def test_data_masking():
    """Test PAN/Aadhaar/account masking"""
    
    assert DataMasking.mask_pan("1234ABCD5678") == "****5678"
    assert DataMasking.mask_aadhaar("123456789012") == "****9012"
    assert DataMasking.mask_account("1234567890123456") == "1234****3456"
    assert "****" in DataMasking.mask_customer_name("John Doe Smith")

def test_str_7day_sla():
    """Test STR 7-day SLA monitoring"""
    
    # Create mock AML alert (8 days old)
    aml_alert = AMLAlert(
        aml_id="AML-001",
        detection_date=datetime.now() - timedelta(days=8),
        str_filed_date=None
    )
    
    # Check SLA
    overdue = STRExtractor().get_overdue_str_alerts()
    
    # Assert
    assert len(overdue) >= 1
    assert overdue[0].aml_id == "AML-001"
```

**Owner:** QA Engineer (1 FTE)  
**Effort:** 40 hours  
**Output:** Test report (>80% coverage)

---

### **3.2 Integration Testing**

**Deliverable:** E2E testing with RBI/FINnet test portals

**Test Scenarios:**
- [ ] End-to-end FMR generation & RBI submission
  - Generate FMR → Encrypt → Submit to RBI test portal → Verify receipt
- [ ] End-to-end STR generation & FIU submission
  - Generate STR → Sign → Submit to FINnet test → Verify acknowledgment
- [ ] Data masking verification
  - Submit report → Verify PAN/Aadhaar masked in RBI/FIU systems
- [ ] SLA monitoring
  - Create old fraud case → Check if escalated after 3 weeks
  - Create old AML alert → Check if escalated after 7 days
- [ ] Audit trail integrity
  - Submit report → Verify immutable log entry
  - Check 7-year retention flag

**Owner:** QA Engineer + DevOps  
**Effort:** 40 hours  
**Output:** Integration test report

---

## Phase 4: Deployment & Training (Week 5–6)

### **4.1 Production Deployment**

**Deliverable:** FMR/STR module live in production

**Deployment Steps:**
- [ ] Set up production database (fmr_alerts, str_alerts, audit_trail tables)
- [ ] Deploy FMR module to production
  - API endpoints `/fmr/*`
  - Database migrations
  - Scheduler jobs
- [ ] Deploy STR module to production
  - API endpoints `/str/*`
  - FINnet Gateway credentials
  - Scheduler jobs
- [ ] Deploy dashboard
  - Frontend deployment
  - WebSocket for real-time updates
- [ ] Set up monitoring
  - Alert on 7-day SLA violations
  - Alert on portal submission failures
  - Monitor API latency
- [ ] Verify data backups
  - 7-year audit trail backup strategy
  - Immutable log archival

**Owner:** DevOps + Platform Team  
**Effort:** 20 hours

---

### **4.2 Training & Documentation**

**Deliverable:** Team trained on FMR/STR system

**Training Materials:**
- [ ] How to generate FMR (manual + automated)
- [ ] How to generate STR (manual + automated)
- [ ] Dashboard walkthrough
- [ ] SLA monitoring & escalation procedures
- [ ] Audit trail & compliance verification
- [ ] Troubleshooting guide

**Training Plan:**
- [ ] Week 5: Live training session (2 hours)
  - CRO, CCO, Compliance team, Fraud analysts
  - Demo FMR generation
  - Demo STR generation
  - Q&A
- [ ] Week 6: Documentation & self-study
  - Written runbook
  - Video tutorials
  - FAQ

**Owner:** Compliance Officer + Platform Team  
**Effort:** 20 hours

---

## Detailed Timeline

```
WEEK 1: Requirements & Design
├── Day 1–2: Requirements gathering (CRO/CCO interviews)
├── Day 3–5: Technical design (architecture, database schema)
└── Deliverable: Requirements + Architecture document

WEEK 2: FMR Module (Phase 1)
├── Day 1–2: FMR extractor (database queries, data extraction)
├── Day 3–4: FMR formatter (Excel/PDF generation)
├── Day 5: FMR validator + RBI API setup
└── Deliverable: FMR module 80% complete

WEEK 3: STR Module (Phase 1) + FMR Completion
├── Day 1–3: STR extractor, classifier, XML generator
├── Day 4–5: STR validator, digital signer, FINnet API
└── Deliverable: STR module 80% complete, FMR done

WEEK 4: Unit Testing + Dashboard Start
├── Day 1–3: Unit tests (FMR, STR, masking, SLA)
├── Day 4–5: Dashboard UI/UX design + frontend setup
└── Deliverable: Test coverage >80%, Dashboard 50% complete

WEEK 5: Integration Testing + Dashboard + Go-Live Prep
├── Day 1–2: E2E testing (RBI/FINnet test portals)
├── Day 3–4: Dashboard completion + real-time updates
├── Day 5: Production deployment prep, monitoring setup
└── Deliverable: All modules tested, Dashboard live

WEEK 6: Deployment + Training
├── Day 1–2: Production deployment + verification
├── Day 3–4: Team training (CRO, CCO, compliance)
├── Day 5: Troubleshooting, documentation finalization
└── Deliverable: FMR/STR live + team trained
```

---

## Resource Requirements

| Role | Effort | Weeks | Availability |
|---|---|---|---|
| Backend Engineer (FMR) | 60 hours | 3 weeks | Full-time |
| Backend Engineer (STR) | 70 hours | 3 weeks | Full-time |
| Frontend Engineer | 40 hours | 2 weeks | Part-time |
| QA Engineer | 80 hours | 3 weeks | Full-time |
| Database Admin | 20 hours | 1 week | Part-time |
| DevOps Engineer | 20 hours | 1 week | Part-time |
| Business Analyst | 16 hours | 1 week | Part-time |
| Compliance Officer | 20 hours | 2 weeks | Part-time |
| **Total** | **326 hours** | **6 weeks** | **~2 FTE** |

---

## Success Criteria

### **For FMR:**
- [ ] 100% of fraud cases reported within 3-week timeline
- [ ] 0 SLA violations (no frauds >21 days without RBI filing)
- [ ] All data properly masked (PAN, Aadhaar, account)
- [ ] Quarterly consolidation report generated automatically
- [ ] All FMR filings logged in immutable audit trail

### **For STR:**
- [ ] 100% of AML alerts reported within 7-day timeline
- [ ] 0 PMLA violations (no AML alerts >7 days without FIU filing)
- [ ] All data properly masked and digitally signed
- [ ] FIU-IND acknowledgments received for all STRs
- [ ] All STR filings logged in immutable audit trail

### **For System:**
- [ ] Dashboard shows real-time FMR/STR status
- [ ] Automated alerts for SLA violations
- [ ] >80% test coverage
- [ ] 99.9% system uptime
- [ ] 7-year retention policy enforced

---

## Risk Mitigation

| Risk | Mitigation |
|---|---|
| **Portal API delays** (RBI/FINnet integration takes longer) | Start API integration in Week 2; use sandbox/test portals first |
| **Data quality issues** (frauds/AML alerts missing from FRMS) | Week 1: Audit data quality; map all fraud/AML sources |
| **Masking errors** (PII leakage to RBI/FIU) | Unit test masking logic extensively; pre-audit all submissions |
| **SLA violations** (not enough time to implement) | Focus on MVP (FMR first), add STR enhancements in Phase 2 |
| **Team capacity** (engineers busy with other work) | Secure full-time commitment; block calendars Week 1–6 |
| **Regulatory changes** (RBI/FIU format updates during dev) | Monitor RBI/FIU communications; design modular formatter |

---

## Post-Implementation (Phase 2–4)

### **Phase 2 (Weeks 7–12):**
- Enhance FMR reporting (financial analytics, trends)
- Add STR multi-case consolidation
- Integrate with Fraud360 Lane B alerts

### **Phase 3 (Weeks 13–16):**
- Add Lane C credit-risk reporting (quarterly credit monitoring)
- Implement predictive alerts (flag cases likely to miss SLA)
- Advanced analytics (fraud loss trend, recovery analysis)

### **Phase 4 (Weeks 17–24):**
- Full Fraud360 platform integration
- Replace interim FMR/STR with Fraud360 native modules
- Retire manual processes

---

## Sign-Off

- [ ] CRO: Approves timeline and resource commitment
- [ ] CCO: Approves compliance approach
- [ ] Platform Lead: Confirms development capacity
- [ ] QA Lead: Confirms testing resources
- [ ] DevOps: Confirms production deployment capability

---

## Next Steps

1. **Immediate (Today):**
   - Get CRO/CCO approval on this roadmap
   - Secure resource commitments
   - Schedule kick-off meeting

2. **Week 1:**
   - Set up project management (Jira, GitHub)
   - Create development environment
   - Conduct requirements workshop with CRO/CCO

3. **Week 2:**
   - Start FMR module development
   - Finalize database schema
   - Begin testing environment setup

---

## Questions?

Contact the Platform Team or Compliance Officer for clarifications.

