# Lane C Implementation Playbook
## Technical Implementation Guide for Engineering & IT Teams

**Version:** 1.0  
**Status:** DRAFT (for Q1 2025 MVP)  
**Audience:** Platform engineers, data engineers, DBAs, IT architects  
**Duration:** 8–12 weeks from kickoff to production

---

## Executive Summary for Tech Team

Lane C is a **quarterly batch-processing system** that detects borrower financial distress and fraud by analyzing audited financial statements. Unlike Lanes A/B (real-time transaction processing), Lane C operates on a scheduled review cycle.

**Key differences from Lanes A/B:**
- **Asynchronous:** Runs weekly/monthly, not per-transaction
- **File-based:** Ingests financial PDFs, not event streams
- **CPU-intensive:** Document parsing, OCR, numerical analysis
- **Latency-tolerant:** 7–30 days acceptable (vs. 80ms for Lane A)

**MVP Scope (Q1 2025):**
- 8 of 15 RBI signals (phase 1: receivables, inventory, scope creep, accounting changes, scope creep)
- Corporate borrowers ₹10+ crore exposure
- Manual workflow (no auto-escalation yet)
- Dashboard for credit team review

---

## Architecture Overview

```
Borrower Financial Statements (PDF, Excel, uploaded)
    ↓
Document Ingestion Layer
    ├─ PDF/Excel parser → Structured data
    ├─ OCR (if scanned) → Text extraction
    └─ Validation → Reject malformed files
    ↓
Data Normalization
    ├─ Map GL codes → Standard P&L/B/S structure
    ├─ Currency conversion (if needed)
    └─ Data quality checks
    ↓
Signal Computation Engine
    ├─ 8 MVP algorithms (inventory, receivables, etc.)
    ├─ Peer benchmarking (cohort comparison)
    └─ Trend analysis (QoQ, YoY)
    ↓
Scoring & Alert Generation
    ├─ Credit Health Score (0–100)
    ├─ Signal-by-signal flags (Critical/High/Med/Low)
    └─ Narrative summary for credit team
    ↓
Credit Team Dashboard
    ├─ Borrower-level score card
    ├─ Alert drill-down (audit trail)
    └─ Investigation workflow
```

---

## Phase 1: Technical Design (Week 1–2)

### **1.1 Data Model Design**

**Core Tables:**

```sql
-- Borrower financial statements (source data)
CREATE TABLE lane_c.financial_statements (
    statement_id UUID PRIMARY KEY,
    borrower_id VARCHAR,
    reporting_date DATE,
    filing_type ENUM('annual', 'quarterly', 'interim'),
    submission_date TIMESTAMP,
    document_source ENUM('uploaded', 'cbs_api', 'mca_registry'),
    document_path VARCHAR,
    extraction_status ENUM('pending', 'extracted', 'validated', 'failed'),
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- Parsed financial data (extracted)
CREATE TABLE lane_c.parsed_financials (
    parsed_id UUID PRIMARY KEY,
    statement_id UUID REFERENCES financial_statements,
    metric_code VARCHAR (e.g., 'REVENUE', 'INVENTORY', 'AR'),
    metric_value DECIMAL(15, 2),
    metric_currency VARCHAR,
    line_item_index INT (row number in statement),
    confidence_score FLOAT (OCR confidence if scanned),
    validated_by VARCHAR (which module validated),
    extracted_at TIMESTAMP
);

-- Computed signals (Lane C scores)
CREATE TABLE lane_c.computed_signals (
    signal_id UUID PRIMARY KEY,
    borrower_id VARCHAR,
    reporting_date DATE,
    signal_code VARCHAR (e.g., 'INV_MOV', 'AR_MOV'),
    signal_name VARCHAR,
    observed_value DECIMAL(10, 4),
    baseline_value DECIMAL(10, 4),
    peer_median DECIMAL(10, 4),
    threshold VARCHAR,
    status ENUM('pass', 'warning', 'critical'),
    severity INT (0–100),
    evidence TEXT (why it triggered),
    computed_at TIMESTAMP
);

-- Credit health scores (aggregate)
CREATE TABLE lane_c.credit_health_scores (
    score_id UUID PRIMARY KEY,
    borrower_id VARCHAR,
    reporting_date DATE,
    score_value INT (0–100),
    score_trend VARCHAR ('improving', 'stable', 'deteriorating'),
    signal_count INT (how many signals triggered),
    critical_count INT (how many critical),
    peer_percentile INT (rank vs. peer group),
    recommendation VARCHAR ('continue', 'monitor', 'investigate', 'escalate'),
    scored_at TIMESTAMP
);

-- Alert tracking
CREATE TABLE lane_c.alerts (
    alert_id UUID PRIMARY KEY,
    borrower_id VARCHAR,
    reporting_date DATE,
    signal_id UUID REFERENCES computed_signals,
    alert_type ENUM ('inventory', 'receivables', 'scope_creep', etc.),
    severity ENUM ('critical', 'high', 'medium', 'low'),
    status ENUM ('new', 'reviewed', 'escalated', 'dismissed'),
    assigned_to VARCHAR (credit analyst),
    investigation_notes TEXT,
    created_at TIMESTAMP,
    closed_at TIMESTAMP
);
```

### **1.2 Technology Stack**

| **Component** | **MVP Choice** | **Rationale** |
|---|---|---|
| **Language** | Python 3.11+ | Data science libraries (pandas, scipy); rapid iteration |
| **Web framework** | FastAPI | Lightweight; async-ready; built-in Swagger docs |
| **Database** | PostgreSQL 14+ | JSON support; time-series; audit log integration |
| **Document parsing** | pdfplumber + Tesseract OCR | Open-source; good for Indian financial PDFs |
| **Data processing** | Apache Spark (PySpark) | Scalable; handles peer benchmarking at scale |
| **Orchestration** | Apache Airflow | Workflow scheduling; retry logic; monitoring |
| **Storage** | S3 + PostgreSQL | PDFs in S3; structured data in DB |
| **Monitoring** | Prometheus + Grafana | Extraction success rate; signal computation time |
| **Testing** | pytest + pytest-cov | Unit + integration tests; >80% coverage target |

### **1.3 API Design**

**Primary endpoints (for credit team dashboard):**

```
POST   /lane-c/ingest
       └─ Upload financial statement PDF
       └─ Returns: statement_id, extraction_status

GET    /lane-c/borrowers/{borrower_id}/score
       └─ Fetch latest credit health score
       └─ Returns: score_value, trend, signals triggered, recommendations

GET    /lane-c/borrowers/{borrower_id}/alerts
       └─ Fetch all alerts for a borrower
       └─ Returns: alert_id, signal_name, severity, status

POST   /lane-c/alerts/{alert_id}/review
       └─ Log credit team's review (dismissed, escalated, etc.)
       └─ Returns: alert_status, notes

GET    /lane-c/cohorts/{industry_code}/benchmarks
       └─ Peer metrics for a given industry/size cohort
       └─ Returns: median score, metric ranges, sample size
```

---

## Phase 2: Development (Week 3–6)

### **2.1 Module 1: Document Ingestion & Parsing**

**Responsibility:** Extract structured financial data from PDFs.

**Input:** Borrower uploads annual/quarterly financial statements (PDF).

**Output:** Structured P&L, B/S, Cash Flow (in `parsed_financials` table).

**Implementation:**

```python
# Module: lane_c/ingestion/document_parser.py

import pdfplumber
import pytesseract
from typing import Dict, List

class FinancialDocumentParser:
    def __init__(self):
        self.supported_formats = ['pdf', 'xlsx']
        self.tesseract_config = '--psm 6'  # assume single block of text
    
    def extract_tables(self, pdf_path: str) -> List[Dict]:
        """Extract tables from PDF (P&L, B/S, etc.)"""
        with pdfplumber.open(pdf_path) as pdf:
            tables = []
            for page in pdf.pages:
                page_tables = page.extract_tables()
                if page_tables:
                    for table in page_tables:
                        # Validate table: expect headers + data rows
                        if len(table) > 1:
                            tables.append(self._normalize_table(table))
        return tables
    
    def _normalize_table(self, table: List[List]) -> Dict:
        """Convert table to standardized format"""
        headers = table[0]
        rows = table[1:]
        return {
            'headers': headers,
            'rows': rows,
            'row_count': len(rows),
            'col_count': len(headers)
        }
    
    def extract_line_items(self, tables: List[Dict]) -> Dict[str, float]:
        """
        Map extracted tables to standard GL codes.
        Example: "Revenue" or "Total Income" → GL code "REVENUE"
        """
        mapping = {
            r'total\s+income|revenue': 'REVENUE',
            r'inventory': 'INVENTORY',
            r'accounts\s+receivable|trade.*receivable': 'AR',
            r'current\s+liabilities': 'CL',
            r'total\s+debt|borrowing': 'DEBT',
            # ... 20+ more patterns
        }
        
        metrics = {}
        for table in tables:
            for row in table['rows']:
                line_item = row[0].lower()
                value = row[-1]  # assume last column is value
                
                for pattern, gl_code in mapping.items():
                    if re.search(pattern, line_item):
                        try:
                            metrics[gl_code] = float(value.replace(',', ''))
                        except ValueError:
                            pass
        
        return metrics
    
    def extract_notes(self, pdf_path: str) -> Dict[str, str]:
        """Extract qualitative notes from PDF (contingent liabilities, etc.)"""
        notes = {}
        with pdfplumber.open(pdf_path) as pdf:
            full_text = ''.join(page.extract_text() for page in pdf.pages)
            
            # Look for note sections
            note_patterns = {
                'contingent_liabilities': r'contingent\s+liabilities.*?(?=\n\n|\Z)',
                'related_party': r'related\s+party.*?(?=\n\n|\Z)',
                'auditor_notes': r'auditor\'?s?\s+report.*?(?=\n\n|\Z)',
            }
            
            for note_type, pattern in note_patterns.items():
                match = re.search(pattern, full_text, re.IGNORECASE | re.DOTALL)
                if match:
                    notes[note_type] = match.group(0)[:500]  # first 500 chars
        
        return notes
```

**Testing:**

```python
# tests/test_document_parser.py

def test_extract_tables_from_sample_pdf():
    parser = FinancialDocumentParser()
    tables = parser.extract_tables('tests/fixtures/sample_annual_report.pdf')
    assert len(tables) > 0
    assert 'headers' in tables[0]
    assert 'rows' in tables[0]

def test_normalize_p_and_l():
    parser = FinancialDocumentParser()
    metrics = parser.extract_line_items([
        {
            'headers': ['Line Item', '2023', '2024'],
            'rows': [['Revenue', '100', '110']]
        }
    ])
    assert metrics['REVENUE'] == 110
```

### **2.2 Module 2: Signal Computation Engine**

**Responsibility:** Calculate the 8 MVP signals.

**Input:** Normalized financial data (P&L, B/S).

**Output:** Signal status (pass/warning/critical) + evidence.

**Implementation (Sample Signals):**

```python
# Module: lane_c/signals/signal_engine.py

class SignalComputer:
    def __init__(self, peer_benchmarks: Dict):
        self.peer_benchmarks = peer_benchmarks
    
    # Signal #3: Inventory Misalignment
    def compute_inventory_movement(
        self,
        inventory_current: float,
        inventory_prior: float,
        revenue_current: float,
        revenue_prior: float,
        borrower_id: str
    ) -> Dict:
        """Flags if inventory grows while revenue shrinks or stays flat."""
        
        inv_growth = (inventory_current - inventory_prior) / inventory_prior
        revenue_growth = (revenue_current - revenue_prior) / revenue_prior
        peer_median_inv_growth = self.peer_benchmarks['inventory_growth_median']
        
        severity = 0
        if revenue_growth < 0 and inv_growth > 0.1:
            # Revenue down, inventory up = bad signal
            severity += 30
        
        if inv_growth > 0.5:
            # Inventory up more than 50% = warning
            severity += 25
        
        if inv_growth > peer_median_inv_growth + 0.2:
            # Inventory growing faster than peers = warning
            severity += 15
        
        return {
            'signal_code': 'INV_MOV',
            'signal_name': 'Inventory Movement vs. Revenue',
            'observed_value': inv_growth,
            'baseline_value': revenue_growth,
            'peer_median': peer_median_inv_growth,
            'status': 'critical' if severity >= 50 else 'warning' if severity >= 20 else 'pass',
            'severity': severity,
            'evidence': f'Inventory grew {inv_growth:.1%} while revenue changed {revenue_growth:.1%}. Peer median: {peer_median_inv_growth:.1%}.'
        }
    
    # Signal #4: Receivables Aging
    def compute_receivables_aging(
        self,
        ar_current: float,
        ar_prior: float,
        revenue_current: float,
        revenue_prior: float,
        borrower_id: str
    ) -> Dict:
        """Flags if receivables grow disproportionate to revenue."""
        
        ar_growth = (ar_current - ar_prior) / ar_prior
        revenue_growth = (revenue_current - revenue_prior) / revenue_prior
        dso_current = (ar_current / revenue_current) * 365  # Days Sales Outstanding
        dso_prior = (ar_prior / revenue_prior) * 365
        
        severity = 0
        if ar_growth > 0.3 and revenue_growth < 0.1:
            # AR growing significantly, revenue flat = bad signal
            severity += 35
        
        if dso_current > dso_prior + 20:
            # DSO increased >20 days = collection problem
            severity += 25
        
        if ar_current / revenue_current > 0.4:
            # AR > 40% of revenue = high receivables = liquidity risk
            severity += 15
        
        return {
            'signal_code': 'AR_MOV',
            'signal_name': 'Receivables Growth vs. Revenue',
            'observed_value': ar_growth,
            'baseline_value': revenue_growth,
            'dso_current': dso_current,
            'dso_prior': dso_prior,
            'status': 'critical' if severity >= 60 else 'warning' if severity >= 25 else 'pass',
            'severity': severity,
            'evidence': f'AR grew {ar_growth:.1%} vs. revenue {revenue_growth:.1%}. DSO: {dso_prior:.0f}→{dso_current:.0f} days.'
        }
    
    # Signal #6: Working Capital Bloat
    def compute_working_capital_bloat(
        self,
        wc_borrowing_current: float,
        wc_borrowing_prior: float,
        revenue_current: float,
        revenue_prior: float,
        ebitda_current: float,
        ebitda_prior: float,
        borrower_id: str
    ) -> Dict:
        """Flags if WC borrowing rises despite stable/growing EBITDA."""
        
        wc_growth = (wc_borrowing_current - wc_borrowing_prior) / wc_borrowing_prior
        revenue_growth = (revenue_current - revenue_prior) / revenue_prior
        ebitda_growth = (ebitda_current - ebitda_prior) / ebitda_prior
        
        wc_pct_revenue_current = wc_borrowing_current / revenue_current
        wc_pct_revenue_prior = wc_borrowing_prior / revenue_prior
        
        severity = 0
        if wc_growth > 0.2 and revenue_growth < 0.1 and ebitda_growth < 0.05:
            # WC growing while revenue/EBITDA stagnate = cash crisis
            severity += 40
        
        if wc_pct_revenue_current > wc_pct_revenue_prior + 0.1:
            # WC as % of revenue increased = higher dependency on credit
            severity += 20
        
        if wc_pct_revenue_current > 0.3:
            # WC > 30% of revenue = high leverage
            severity += 15
        
        return {
            'signal_code': 'WC_BLOAT',
            'signal_name': 'Working Capital Borrowing Rising',
            'observed_value': wc_growth,
            'baseline_value': revenue_growth,
            'wc_pct_revenue': wc_pct_revenue_current,
            'status': 'critical' if severity >= 60 else 'warning' if severity >= 25 else 'pass',
            'severity': severity,
            'evidence': f'WC borrowing grew {wc_growth:.1%} while revenue grew {revenue_growth:.1%} and EBITDA grew {ebitda_growth:.1%}. High cash-flow stress.'
        }
```

### **2.3 Module 3: Peer Benchmarking & Cohort Analysis**

**Responsibility:** Build peer cohorts; compute medians for context.

**Input:** Historical financials from all borrowers in industry.

**Output:** Peer percentile scores, anomaly flags.

**Implementation:**

```python
# Module: lane_c/benchmarking/cohort_builder.py

class CohortBuilder:
    def build_cohorts(self, borrowers_df: pd.DataFrame) -> Dict:
        """
        Segment borrowers into cohorts by:
        - Industry (NACE code)
        - Size (revenue ₹10-50cr, ₹50-100cr, >100cr)
        - Age (years in operation)
        """
        cohorts = {}
        for industry in borrowers_df['industry'].unique():
            industry_df = borrowers_df[borrowers_df['industry'] == industry]
            
            for size_bucket in ['10-50cr', '50-100cr', '100cr+']:
                if size_bucket == '10-50cr':
                    cohort_df = industry_df[
                        (industry_df['revenue'] >= 10e7) & 
                        (industry_df['revenue'] < 50e7)
                    ]
                # ... similar logic for other buckets
                
                if len(cohort_df) > 5:  # minimum 5 borrowers per cohort
                    cohort_key = f"{industry}_{size_bucket}"
                    cohorts[cohort_key] = {
                        'borrower_count': len(cohort_df),
                        'metrics': {
                            'revenue_median': cohort_df['revenue'].median(),
                            'ar_growth_median': cohort_df['ar_growth'].median(),
                            'inventory_growth_median': cohort_df['inventory_growth'].median(),
                            'wc_growth_median': cohort_df['wc_growth'].median(),
                            'dso_median': cohort_df['dso'].median(),
                        }
                    }
        
        return cohorts
    
    def compute_peer_percentile(
        self,
        borrower_metric_value: float,
        cohort_key: str,
        metric_name: str,
        cohorts: Dict
    ) -> int:
        """Returns percentile rank (0–100) vs. peer group."""
        
        if cohort_key not in cohorts or len(cohort_key) == 0:
            return 50  # neutral if no cohort available
        
        cohort_values = [cohort_key[metric_name]]  # simplified
        percentile = stats.percentileofscore(cohort_values, borrower_metric_value)
        
        return int(percentile)
```

### **2.4 Module 4: Scoring & Alert Generation**

**Responsibility:** Aggregate signals into a credit health score (0–100).

**Input:** All signal results.

**Output:** Score + recommendation.

**Implementation:**

```python
# Module: lane_c/scoring/credit_health_scorer.py

class CreditHealthScorer:
    def compute_score(self, signals: List[Dict], borrower_profile: Dict) -> Dict:
        """
        Aggregate signals into 0–100 score.
        
        Scoring logic:
        - Start at 100 (perfect health)
        - Deduct points for each signal triggered
        - Critical signals: -20 points each
        - High signals: -10 points each
        - Medium signals: -5 points each
        - Low signals: -2 points each
        - Minimum score: 0
        """
        
        score = 100
        signal_summary = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
        
        for signal in signals:
            severity = signal['status']  # 'critical', 'high', etc.
            
            if severity == 'critical':
                score -= 20
                signal_summary['critical'] += 1
            elif severity == 'high':
                score -= 10
                signal_summary['high'] += 1
            elif severity == 'medium':
                score -= 5
                signal_summary['medium'] += 1
            elif severity == 'low':
                score -= 2
                signal_summary['low'] += 1
        
        score = max(0, score)  # clamp to 0
        
        # Determine trend
        prior_score = self._fetch_prior_score(borrower_profile['borrower_id'])
        if prior_score is None:
            trend = 'new'
        elif score < prior_score - 5:
            trend = 'deteriorating'
        elif score > prior_score + 5:
            trend = 'improving'
        else:
            trend = 'stable'
        
        # Compute percentile rank vs. peer cohort
        peer_percentile = self._get_peer_percentile(borrower_profile, score)
        
        # Generate recommendation
        if score >= 80:
            recommendation = 'continue'
        elif score >= 60:
            recommendation = 'monitor'
        elif score >= 40:
            recommendation = 'investigate'
        else:
            recommendation = 'escalate'
        
        return {
            'score_value': score,
            'score_trend': trend,
            'signal_count': sum(signal_summary.values()),
            'critical_count': signal_summary['critical'],
            'peer_percentile': peer_percentile,
            'recommendation': recommendation,
            'signal_breakdown': signal_summary
        }
```

---

## Phase 3: Testing & QA (Week 6–7)

### **3.1 Unit Testing**

**Target:** >80% code coverage

```bash
# Run pytest with coverage
pytest tests/ --cov=lane_c --cov-report=html

# Generate coverage report
# Expected: >80% across all modules
```

**Test data:** Use anonymized real financial statements from existing borrowers.

### **3.2 Integration Testing**

**Test flow:**
1. Upload sample PDF (annual report)
2. Verify document parsing extracts correct P&L/B/S
3. Compute signals against known-good outputs
4. Verify score calculation matches expected range
5. Check alert generation and escalation logic

### **3.3 Accuracy Validation**

**Against known cases:**
- 5 borrowers with known credit deterioration → Verify Lane C detected it
- 5 healthy borrowers → Verify Lane C scored them 70+
- 3 fraud cases (fictional scenario) → Verify Lane C scored them <40

---

## Phase 4: Deployment & Operations (Week 8+)

### **4.1 Infrastructure Setup**

**Requirements:**

| **Component** | **Spec** | **Rationale** |
|---|---|---|
| **Database** | PostgreSQL 14+, 2 TB storage | Store 5 years of historical data |
| **Compute** | 16 CPU / 64 GB RAM | Document parsing + peer benchmarking |
| **Storage** | 1 TB S3-like | Financial PDFs, backups |
| **Monitoring** | Prometheus + Grafana | Track extraction success rate, computation time |
| **Backup** | Daily snapshots, 90-day retention | Audit trail for RBI inspection |

### **4.2 Operational Procedures**

**Daily jobs:**
- `lane_c_ingest_batch`: Process new financial statement uploads
- `lane_c_compute_signals`: Run signal algorithms on all borrowers with new data
- `lane_c_generate_scores`: Aggregate into credit health scores
- `lane_c_alert_generation`: Flag critical/high signals
- `lane_c_dashboard_sync`: Update credit team dashboards

**Weekly jobs:**
- `lane_c_cohort_rebuild`: Recalculate peer medians (new borrowers added)
- `lane_c_data_quality_check`: Validate extracted financials vs. source PDFs

**Monthly jobs:**
- `lane_c_trend_analysis`: Compute QoQ/YoY metrics for all borrowers
- `lane_c_alerts_review`: Summarize alerts closed/escalated

**Quarterly jobs:**
- `lane_c_model_recalibration`: Retrain thresholds based on recent defaults/recoveries
- `lane_c_audit_report`: Generate compliance report for RBI

### **4.3 On-Call & Support**

**First-line support:**
- Credit team (8 AM–6 PM): Review alerts, investigate anomalies
- Platform team (24/7 on-call): Restart services, recover from failures

**Escalation:**
- If extraction fails for >10% of borrowers → page platform engineer
- If scores disagree with prior quarter by >30 points → verify data quality
- If peer benchmarking gives strange results → check for data anomalies

---

## Phase 5: Performance Tuning & Roadmap

### **5.1 MVP Metrics**

| **Metric** | **Target** | **Actual (after Week 8)** |
|---|---|---|
| Extraction success rate | >95% | TBD |
| Signal computation time | <10 sec/borrower | TBD |
| False positive rate (signals triggered unnecessarily) | <10% | TBD |
| False negative rate (missed deterioration) | <5% | TBD |
| Alert review SLA (<24h response) | >90% | TBD |

### **5.2 Post-MVP Enhancements (Q2–Q4 2025)**

**Phase 2 (Q2 2025): Expand signal coverage**
- Add 7 more RBI signals (contingent liabilities, accounting changes, etc.)
- Enable for all borrowers (remove ₹10cr minimum)
- Integrate with Lane B (flag unusual payments when Lane C shows distress)

**Phase 3 (Q3 2025): Predictive scoring**
- ML model to predict default probability 2–3 quarters ahead
- Incorporate borrower history, industry trends, macro factors
- Generate early warning scores (separate from cross-sectional scores)

**Phase 4 (Q4 2025): Full automation**
- Auto-pull financials from MCA, auditor databases (APIs)
- Auto-escalate to credit committee if score breaches threshold
- Integration with collateral management system (auto-revalue if red flags)

---

## Troubleshooting Guide

### **Common Issues**

**Issue: PDF parsing fails (unstructured financial statements)**
- *Symptom:* Extracted metrics don't match expected values
- *Root cause:* Custom financial statement format not recognized
- *Fix:* Add template to `document_parser.py`; re-run extraction

**Issue: Peer benchmarking gives 0 or 100 percentile**
- *Symptom:* Borrower metric extremely high/low vs. cohort
- *Root cause:* Cohort size too small (<5 borrowers) or data outlier
- *Fix:* Expand cohort boundaries or flag data quality issue

**Issue: Score changes dramatically quarter-to-quarter**
- *Symptom:* Score drops 40+ points in one quarter
- *Root cause:* Signal thresholds too sensitive OR borrower genuinely deteriorating
- *Fix:* Manual review + audit trail; adjust thresholds if false positive

---

## Code Repository Structure

```
lane-c/
├── README.md
├── requirements.txt (Python dependencies)
├── config/
│   ├── signals_config.yaml (signal thresholds)
│   ├── cohort_definitions.yaml (industry/size buckets)
│   └── alert_rules.yaml (alert severity mapping)
├── src/
│   ├── ingestion/
│   │   ├── document_parser.py
│   │   └── financial_extractor.py
│   ├── signals/
│   │   ├── signal_engine.py
│   │   └── signal_definitions.py (8 MVP signals)
│   ├── benchmarking/
│   │   └── cohort_builder.py
│   ├── scoring/
│   │   └── credit_health_scorer.py
│   └── api/
│       ├── main.py (FastAPI app)
│       └── models.py (Pydantic schemas)
├── tests/
│   ├── test_document_parser.py
│   ├── test_signal_engine.py
│   ├── test_scoring.py
│   └── fixtures/ (sample PDFs, known-good outputs)
├── dags/
│   ├── ingest_dag.py
│   ├── compute_signals_dag.py
│   └── alert_generation_dag.py
└── deployment/
    ├── docker-compose.yml
    ├── kubernetes/ (k8s manifests)
    └── terraform/ (cloud infrastructure)
```

---

## Sign-Off Checklist (Before Production)

- [ ] All 8 MVP signals implemented & tested
- [ ] Document parsing accuracy >95%
- [ ] Peer benchmarking validated against known cohorts
- [ ] Scoring algorithm matches design spec
- [ ] Alerts generated within SLA (<1 hour after data upload)
- [ ] Dashboard UX approved by credit team
- [ ] Airflow DAGs scheduled & tested
- [ ] Disaster recovery plan documented & tested
- [ ] Monitoring/alerting set up (Prometheus + Grafana)
- [ ] On-call procedures documented
- [ ] Audit log retention confirmed (7 years)
- [ ] Security hardening completed (encryption, access control)
- [ ] RBI compliance checklist reviewed
- [ ] Training materials prepared for credit team
- [ ] Go/no-go decision from steering committee

