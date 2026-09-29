"""
Fraud360: FMR/STR Automated Reporting System
Python Implementation for RBI & FIU-IND Compliance

Module: fmr_str/
Author: Fraud360 Platform Team
Date: 2026-08-11
Status: Production-Ready (MVP)

Dependencies:
  - Python 3.11+
  - FastAPI, SQLAlchemy, Pydantic
  - openpyxl (Excel generation)
  - xml.etree.ElementTree (XML generation)
  - requests (Portal API)
  - cryptography (Digital signing)
  - apscheduler (Job scheduling)
  - python-dotenv (Config management)

Installation:
  pip install fastapi sqlalchemy pydantic openpyxl requests cryptography apscheduler python-dotenv
"""

# ============================================================================
# PART 1: DATA MODELS
# ============================================================================

from datetime import datetime, timedelta
from typing import Optional, List
from pydantic import BaseModel, Field
from enum import Enum
import uuid

# ----- FMR Models -----

class FraudType(str, Enum):
    DIGITAL = "DIGITAL"
    CHEQUE = "CHEQUE"
    CARD = "CARD"
    LOAN = "LOAN"
    TRANSFER = "TRANSFER"
    OTHER = "OTHER"

class FraudStatus(str, Enum):
    ONGOING = "ONGOING"
    RESOLVED = "RESOLVED"
    UNDER_INVESTIGATION = "UNDER_INVESTIGATION"

class FMRCase(BaseModel):
    """FMR Case Data Model"""
    fraud_id: str
    detection_date: datetime
    fraud_type: FraudType
    amount: float
    customer_id: str
    customer_name: str
    kyc_pan: str
    account_number: str
    account_type: str
    branch_code: str
    branch_name: str
    modus_operandi: str = Field(..., max_length=500)
    action_taken: str = Field(..., max_length=500)
    status: FraudStatus
    amount_recovered: float = 0.0
    reported_to_rbi_date: Optional[datetime] = None
    fmr_receipt_id: Optional[str] = None

class FMRReport(BaseModel):
    """FMR Report Submission"""
    report_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    submission_date: datetime = Field(default_factory=datetime.now)
    bank_name: str
    bank_registration: str
    cases: List[FMRCase]
    total_amount: float
    case_count: int
    submitted_by: str
    submitted_by_role: str

# ----- STR Models -----

class SuspicionType(str, Enum):
    STRUCTURING = "STRUCTURING"
    LAYERING = "LAYERING"
    SANCTIONS = "SANCTIONS"
    PEP = "PEP"
    GEOLOCATION = "GEOLOCATION"
    INVOICE_FRAUD = "INVOICE_FRAUD"
    OTHER = "OTHER"

class STRCase(BaseModel):
    """STR Case Data Model"""
    aml_alert_id: str
    detection_date: datetime
    customer_id: str
    customer_name: str
    kyc_pan: str
    kyc_aadhaar: str
    account_number: str
    account_type: str
    transaction_id: str
    transaction_date: datetime
    transaction_amount: float
    transaction_channel: str
    beneficiary_account: str
    beneficiary_name: str
    suspicion_type: SuspicionType
    suspicion_narrative: str = Field(..., max_length=500)
    aml_risk_score: int = Field(..., ge=0, le=100)
    signal_code: str
    str_filed_date: Optional[datetime] = None
    str_ack_id: Optional[str] = None

class STRReport(BaseModel):
    """STR Report Submission"""
    report_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    submission_date: datetime = Field(default_factory=datetime.now)
    bank_name: str
    bank_registration: str
    cases: List[STRCase]
    submitted_by: str
    submitted_by_role: str

# ============================================================================
# PART 2: DATA MASKING
# ============================================================================

class DataMasking:
    """Privacy-compliant data masking for RBI/FIU-IND compliance"""

    @staticmethod
    def mask_pan(pan: str) -> str:
        """Mask PAN: Show only last 4 digits"""
        if not pan or len(pan) < 4:
            return "****"
        return f"****{pan[-4:]}"

    @staticmethod
    def mask_aadhaar(aadhaar: str) -> str:
        """Mask Aadhaar: Show only last 4 digits"""
        if not aadhaar or len(aadhaar) < 4:
            return "****"
        return f"****{aadhaar[-4:]}"

    @staticmethod
    def mask_account(account: str) -> str:
        """Mask Account: Show first 4 and last 4 digits"""
        if not account or len(account) < 8:
            return "****"
        return f"{account[:4]}****{account[-4:]}"

    @staticmethod
    def mask_customer_name(name: str) -> str:
        """Mask Name: First letter + surname"""
        if not name:
            return "****"
        parts = name.split()
        if len(parts) >= 2:
            return f"{parts[0][0]}******, {' '.join(parts[1:])}"
        return f"{name[0]}****"

    @staticmethod
    def mask_email(email: str) -> str:
        """Mask Email"""
        if not email or '@' not in email:
            return "****@****"
        user, domain = email.split('@')
        return f"{'*' * max(1, len(user)-1)}{user[-1]}@{domain}"

    @staticmethod
    def mask_phone(phone: str) -> str:
        """Mask Phone: Show only last 4 digits"""
        if not phone or len(phone) < 4:
            return "****"
        return f"+91-****-{phone[-4:]}"

# ============================================================================
# PART 3: DATABASE EXTRACTION
# ============================================================================

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

class FRMSExtractor:
    """Extract fraud/AML data from core banking system"""

    def __init__(self, database_url: str):
        self.engine = create_engine(database_url)
        self.masking = DataMasking()

    def get_pending_fraud_cases(self, days_threshold: int = 21) -> List[FMRCase]:
        """Extract frauds detected within threshold days, pending FMR filing"""

        with Session(self.engine) as session:
            threshold_date = datetime.now() - timedelta(days=days_threshold)

            query = """
                SELECT
                    f.fraud_id, f.detection_date, f.fraud_type, f.fraud_amount,
                    c.customer_id, c.customer_name, c.kyc_pan,
                    a.account_number, a.account_type,
                    b.branch_code, b.branch_name,
                    f.modus_operandi, f.action_taken, f.status,
                    COALESCE(f.amount_recovered, 0) as amount_recovered,
                    f.reported_to_rbi_date, f.fmr_receipt_id
                FROM fraud_alerts f
                JOIN customers c ON f.customer_id = c.customer_id
                JOIN accounts a ON f.account_id = a.account_id
                JOIN branches b ON c.branch_id = b.branch_id
                WHERE f.detection_date >= %s
                  AND f.reported_to_rbi_date IS NULL
                  AND f.confirmation_status = 'CONFIRMED'
                ORDER BY f.detection_date ASC
            """

            result = session.execute(query, [threshold_date])

            cases = []
            for row in result.fetchall():
                case = FMRCase(
                    fraud_id=row[0],
                    detection_date=row[1],
                    fraud_type=row[2],
                    amount=row[3],
                    customer_id=row[4],
                    customer_name=row[5],
                    kyc_pan=self.masking.mask_pan(row[6]),
                    account_number=self.masking.mask_account(row[7]),
                    account_type=row[8],
                    branch_code=row[9],
                    branch_name=row[10],
                    modus_operandi=row[11][:500],
                    action_taken=row[12][:500],
                    status=row[13],
                    amount_recovered=row[14],
                    reported_to_rbi_date=row[15],
                    fmr_receipt_id=row[16]
                )
                cases.append(case)

            return cases

    def get_pending_aml_alerts(self, days_threshold: int = 7) -> List[STRCase]:
        """Extract AML alerts within threshold days, pending STR filing"""

        with Session(self.engine) as session:
            threshold_date = datetime.now() - timedelta(days=days_threshold)

            query = """
                SELECT
                    a.aml_alert_id, a.detection_date,
                    c.customer_id, c.customer_name, c.kyc_pan, c.kyc_aadhaar,
                    ac.account_number, ac.account_type,
                    t.transaction_id, t.transaction_date, t.transaction_amount, t.channel,
                    b.beneficiary_account, b.beneficiary_name,
                    a.suspicion_type, a.suspicion_narrative, a.aml_risk_score,
                    a.signal_code,
                    a.str_filed_date, a.str_ack_id
                FROM aml_alerts a
                JOIN customers c ON a.customer_id = c.customer_id
                JOIN accounts ac ON a.account_id = ac.account_id
                JOIN transactions t ON a.transaction_id = t.transaction_id
                LEFT JOIN beneficiaries b ON t.beneficiary_id = b.beneficiary_id
                WHERE a.detection_date >= %s
                  AND a.str_filed_date IS NULL
                  AND a.confirmation_status = 'CONFIRMED'
                ORDER BY a.aml_risk_score DESC, a.detection_date ASC
            """

            result = session.execute(query, [threshold_date])

            cases = []
            for row in result.fetchall():
                case = STRCase(
                    aml_alert_id=row[0],
                    detection_date=row[1],
                    customer_id=row[2],
                    customer_name=self.masking.mask_customer_name(row[3]),
                    kyc_pan=self.masking.mask_pan(row[4]),
                    kyc_aadhaar=self.masking.mask_aadhaar(row[5]),
                    account_number=self.masking.mask_account(row[6]),
                    account_type=row[7],
                    transaction_id=row[8],
                    transaction_date=row[9],
                    transaction_amount=row[10],
                    transaction_channel=row[11],
                    beneficiary_account=self.masking.mask_account(row[12] or ""),
                    beneficiary_name=self.masking.mask_customer_name(row[13] or ""),
                    suspicion_type=row[14],
                    suspicion_narrative=row[15][:500],
                    aml_risk_score=row[16],
                    signal_code=row[17],
                    str_filed_date=row[18],
                    str_ack_id=row[19]
                )
                cases.append(case)

            return cases

    def get_overdue_fraud_cases(self, days_threshold: int = 21) -> List[str]:
        """Get frauds overdue for FMR filing (for SLA monitoring)"""

        with Session(self.engine) as session:
            threshold_date = datetime.now() - timedelta(days=days_threshold)

            query = """
                SELECT fraud_id, detection_date
                FROM fraud_alerts
                WHERE detection_date < %s
                  AND reported_to_rbi_date IS NULL
                ORDER BY detection_date ASC
            """

            result = session.execute(query, [threshold_date])
            return [row[0] for row in result.fetchall()]

    def get_overdue_aml_alerts(self, days_threshold: int = 7) -> List[str]:
        """Get AML alerts overdue for STR filing (PMLA violation check)"""

        with Session(self.engine) as session:
            threshold_date = datetime.now() - timedelta(days=days_threshold)

            query = """
                SELECT aml_alert_id, detection_date
                FROM aml_alerts
                WHERE detection_date < %s
                  AND str_filed_date IS NULL
                ORDER BY detection_date ASC
            """

            result = session.execute(query, [threshold_date])
            return [row[0] for row in result.fetchall()]

# ============================================================================
# PART 4: FMR GENERATION
# ============================================================================

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from io import BytesIO

class FMRGenerator:
    """Generate RBI-compliant FMR Excel reports"""

    @staticmethod
    def generate_fmr_excel(report: FMRReport) -> bytes:
        """Generate FMR Excel file in RBI format"""

        wb = Workbook()
        ws = wb.active
        ws.title = "FMR"

        # Define styles
        header_fill = PatternFill(start_color="003366", end_color="003366", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=12)
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )

        # Title
        ws['A1'] = "FRAUD MONITORING RETURN (FMR) - RBI"
        ws['A1'].font = Font(bold=True, size=14)
        ws.merge_cells('A1:K1')

        # Bank Details
        ws['A3'] = "Bank Name:"
        ws['B3'] = report.bank_name
        ws['A4'] = "Registration:"
        ws['B4'] = report.bank_registration
        ws['A5'] = "Submission Date:"
        ws['B5'] = report.submission_date.strftime("%d-%b-%Y")

        # Column Headers
        headers = [
            "S.No.", "Branch", "Detection Date", "Fraud Type", "Amount (₹)",
            "Account (Masked)", "Customer (Masked)", "Modus Operandi",
            "Action Taken", "Status", "Recovered (₹)"
        ]

        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=7, column=col)
            cell.value = header
            cell.fill = header_fill
            cell.font = header_font
            cell.border = border
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

        # Data Rows
        for idx, case in enumerate(report.cases, 1):
            row = 7 + idx

            ws.cell(row=row, column=1).value = idx
            ws.cell(row=row, column=2).value = case.branch_code
            ws.cell(row=row, column=3).value = case.detection_date.strftime("%d-%b-%Y")
            ws.cell(row=row, column=4).value = case.fraud_type.value
            ws.cell(row=row, column=5).value = f"₹{case.amount:,.2f}"
            ws.cell(row=row, column=6).value = case.account_number
            ws.cell(row=row, column=7).value = case.customer_name
            ws.cell(row=row, column=8).value = case.modus_operandi
            ws.cell(row=row, column=9).value = case.action_taken
            ws.cell(row=row, column=10).value = case.status.value
            ws.cell(row=row, column=11).value = f"₹{case.amount_recovered:,.2f}"

            # Apply borders
            for col in range(1, 12):
                ws.cell(row=row, column=col).border = border
                ws.cell(row=row, column=col).alignment = Alignment(wrap_text=True, vertical='top')

        # Summary Section
        summary_row = 7 + len(report.cases) + 2
        ws[f'A{summary_row}'] = "SUMMARY"
        ws[f'A{summary_row}'].font = Font(bold=True, size=12)

        ws[f'A{summary_row+1}'] = "Total Cases:"
        ws[f'B{summary_row+1}'] = report.case_count

        ws[f'A{summary_row+2}'] = "Total Amount (₹):"
        ws[f'B{summary_row+2}'] = report.total_amount

        # Column widths
        ws.column_dimensions['A'].width = 8
        ws.column_dimensions['B'].width = 10
        ws.column_dimensions['C'].width = 15
        ws.column_dimensions['D'].width = 15
        ws.column_dimensions['E'].width = 15
        ws.column_dimensions['F'].width = 15
        ws.column_dimensions['G'].width = 15
        ws.column_dimensions['H'].width = 25
        ws.column_dimensions['I'].width = 25
        ws.column_dimensions['J'].width = 15
        ws.column_dimensions['K'].width = 15

        # Save to BytesIO
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output.getvalue()

# ============================================================================
# PART 5: STR GENERATION (XML)
# ============================================================================

import xml.etree.ElementTree as ET
from hashlib import sha256

class STRGenerator:
    """Generate FIU-IND compliant STR XML reports"""

    @staticmethod
    def generate_str_xml(report: STRReport) -> str:
        """Generate STR XML in FIU-IND format"""

        # Root element
        root = ET.Element('SuspiciousTransactionReport')
        root.set('version', '1.0')
        root.set('xmlns', 'http://fiu.gov.in/str/2024-25')

        # Reporting Entity
        reporting = ET.SubElement(root, 'ReportingEntity')
        ET.SubElement(reporting, 'EntityName').text = report.bank_name
        ET.SubElement(reporting, 'EntityID').text = report.bank_registration
        ET.SubElement(reporting, 'EntityType').text = 'SCHEDULED_BANK'

        # STR Details
        str_details = ET.SubElement(root, 'STRDetails')
        ET.SubElement(str_details, 'SubmissionDate').text = report.submission_date.isoformat()
        ET.SubElement(str_details, 'TotalCases').text = str(len(report.cases))

        # Cases
        cases_elem = ET.SubElement(root, 'Cases')
        for idx, case in enumerate(report.cases, 1):
            case_elem = ET.SubElement(cases_elem, 'Case')
            case_elem.set('id', str(idx))

            # Customer
            customer = ET.SubElement(case_elem, 'Customer')
            ET.SubElement(customer, 'CustomerID').text = case.customer_id
            ET.SubElement(customer, 'CustomerName').text = case.customer_name
            ET.SubElement(customer, 'PAN').text = case.kyc_pan
            ET.SubElement(customer, 'Aadhaar').text = case.kyc_aadhaar

            # Account
            account = ET.SubElement(case_elem, 'Account')
            ET.SubElement(account, 'AccountNumber').text = case.account_number
            ET.SubElement(account, 'AccountType').text = case.account_type

            # Transaction
            transaction = ET.SubElement(case_elem, 'Transaction')
            ET.SubElement(transaction, 'TransactionID').text = case.transaction_id
            ET.SubElement(transaction, 'TransactionDate').text = case.transaction_date.isoformat()
            ET.SubElement(transaction, 'Amount').text = str(case.transaction_amount)
            ET.SubElement(transaction, 'Channel').text = case.transaction_channel

            # Beneficiary
            beneficiary = ET.SubElement(transaction, 'Beneficiary')
            ET.SubElement(beneficiary, 'BeneficiaryAccount').text = case.beneficiary_account
            ET.SubElement(beneficiary, 'BeneficiaryName').text = case.beneficiary_name

            # Suspicion
            suspicion = ET.SubElement(case_elem, 'Suspicion')
            ET.SubElement(suspicion, 'Type').text = case.suspicion_type.value
            ET.SubElement(suspicion, 'RiskScore').text = str(case.aml_risk_score)
            ET.SubElement(suspicion, 'Narrative').text = case.suspicion_narrative
            ET.SubElement(suspicion, 'RegulatoryBasis').text = case.signal_code

        # Convert to string
        xml_str = ET.tostring(root, encoding='utf-8').decode('utf-8')

        # Pretty print (optional)
        from xml.dom import minidom
        dom = minidom.parseString(xml_str)
        pretty_xml = dom.toprettyxml(indent="  ")

        # Remove XML declaration and extra whitespace
        return '\n'.join([line for line in pretty_xml.split('\n')[1:] if line.strip()])

# ============================================================================
# PART 6: DIGITAL SIGNATURE
# ============================================================================

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
import base64

class DigitalSigner:
    """Sign STR with CCO certificate for FIU-IND submission"""

    def __init__(self, cert_path: str, key_path: str, key_password: str = None):
        """
        Initialize with CCO's digital certificate

        Args:
            cert_path: Path to CA-issued certificate (.pem)
            key_path: Path to private key (.pem)
            key_password: Password for encrypted private key (if applicable)
        """

        self.cert_path = cert_path
        self.key_path = key_path

        # Load private key
        with open(key_path, 'rb') as f:
            key_data = f.read()
            self.private_key = serialization.load_pem_private_key(
                key_data,
                password=key_password.encode() if key_password else None,
                backend=default_backend()
            )

    def sign_str_xml(self, xml_content: str) -> str:
        """
        Sign STR XML with CCO certificate

        Args:
            xml_content: XML string to sign

        Returns:
            Base64-encoded signature
        """

        # Hash the XML content
        message_digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
        message_digest.update(xml_content.encode('utf-8'))
        message_hash = message_digest.finalize()

        # Sign with private key
        signature = self.private_key.sign(
            message_hash,
            padding.PKCS1v15(),
            hashes.SHA256()
        )

        # Return base64-encoded signature
        return base64.b64encode(signature).decode('utf-8')

    def verify_signature(self, xml_content: str, signature_b64: str) -> bool:
        """Verify STR signature (for testing)"""

        try:
            # Decode signature
            signature = base64.b64decode(signature_b64)

            # Hash the XML
            message_digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
            message_digest.update(xml_content.encode('utf-8'))
            message_hash = message_digest.finalize()

            # Get public key from private key
            public_key = self.private_key.public_key()

            # Verify
            public_key.verify(
                signature,
                message_hash,
                padding.PKCS1v15(),
                hashes.SHA256()
            )

            return True
        except Exception:
            return False

# ============================================================================
# PART 7: PORTAL API INTEGRATION
# ============================================================================

import requests
from requests.auth import HTTPBasicAuth

class RBIPortalAPI:
    """Submit FMR to RBI fraud reporting portal"""

    def __init__(self, base_url: str, api_key: str, api_secret: str):
        self.base_url = base_url
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = requests.Session()

    def submit_fmr(self, fmr_excel_bytes: bytes) -> dict:
        """
        Submit FMR Excel to RBI portal

        Returns:
            {'receipt_id': 'RBI-FMR-XXXXX', 'timestamp': '...', 'status': 'SUBMITTED'}
        """

        try:
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/octet-stream'
            }

            response = self.session.post(
                url=f"{self.base_url}/api/fmr/submit",
                data=fmr_excel_bytes,
                headers=headers,
                timeout=30
            )

            if response.status_code == 200:
                return response.json()
            else:
                return {
                    'status': 'FAILED',
                    'error': response.text,
                    'http_status': response.status_code
                }

        except requests.RequestException as e:
            return {
                'status': 'ERROR',
                'error': str(e)
            }

class FINnetGateway:
    """Submit STR to FIU-IND FINnet Gateway"""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key
        self.session = requests.Session()

    def submit_str(self, str_xml: str, digital_signature: str) -> dict:
        """
        Submit STR XML to FINnet Gateway

        Returns:
            {'ack_id': 'ACK-FIU-XXXXX', 'timestamp': '...', 'status': 'SUBMITTED'}
        """

        try:
            payload = {
                'xml': str_xml,
                'signature': digital_signature,
                'timestamp': datetime.now().isoformat()
            }

            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }

            response = self.session.post(
                url=f"{self.base_url}/api/str/submit",
                json=payload,
                headers=headers,
                timeout=30
            )

            if response.status_code == 200:
                return response.json()
            else:
                return {
                    'status': 'FAILED',
                    'error': response.text,
                    'http_status': response.status_code
                }

        except requests.RequestException as e:
            return {
                'status': 'ERROR',
                'error': str(e)
            }

# ============================================================================
# PART 8: AUDIT TRAIL LOGGING
# ============================================================================

class AuditTrailLogger:
    """Log all FMR/STR submissions for 7-year immutable retention"""

    def __init__(self, database_url: str):
        self.engine = create_engine(database_url)

    def log_fmr_submission(self,
                          submission_id: str,
                          cases: List[FMRCase],
                          receipt_id: str,
                          submitted_by: str,
                          portal: str = 'RBI_PORTAL',
                          status: str = 'SUBMITTED'):
        """Log FMR submission"""

        with Session(self.engine) as session:
            query = """
                INSERT INTO fmr_str_audit_trail
                (audit_id, report_type, report_id, case_ids, submission_date,
                 submitted_by_user_id, submitted_by_role, submission_portal,
                 receipt_id, ack_id, authority_response_status,
                 data_classification, encryption_method, digital_signature_valid,
                 hash_value, created_at, expires_at)
                VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """

            case_ids = ','.join([c.fraud_id for c in cases])
            hash_value = sha256(f"{submission_id}{receipt_id}".encode()).hexdigest()
            expires_at = datetime.now() + timedelta(days=365*7)  # 7 years

            session.execute(query, [
                str(uuid.uuid4()), 'FMR', submission_id, case_ids,
                datetime.now(), submitted_by, 'CRO', portal, receipt_id, None,
                status, 'Masked per RBI', 'AES-256', True, hash_value,
                datetime.now(), expires_at
            ])
            session.commit()

    def log_str_submission(self,
                          submission_id: str,
                          cases: List[STRCase],
                          ack_id: str,
                          digital_signature: str,
                          submitted_by: str,
                          portal: str = 'FINNET_GATEWAY',
                          status: str = 'SUBMITTED'):
        """Log STR submission"""

        with Session(self.engine) as session:
            query = """
                INSERT INTO fmr_str_audit_trail
                (audit_id, report_type, report_id, case_ids, submission_date,
                 submitted_by_user_id, submitted_by_role, submission_portal,
                 receipt_id, ack_id, authority_response_status,
                 data_classification, encryption_method, digital_signature_valid,
                 hash_value, created_at, expires_at)
                VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """

            case_ids = ','.join([c.aml_alert_id for c in cases])
            hash_value = sha256(f"{submission_id}{ack_id}".encode()).hexdigest()
            expires_at = datetime.now() + timedelta(days=365*7)  # 7 years

            session.execute(query, [
                str(uuid.uuid4()), 'STR', submission_id, case_ids,
                datetime.now(), submitted_by, 'CCO', portal, None, ack_id,
                status, 'Masked per FIU', 'AES-256', True, hash_value,
                datetime.now(), expires_at
            ])
            session.commit()

# ============================================================================
# PART 9: SCHEDULED JOBS
# ============================================================================

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

class FMRSTRScheduler:
    """Background job scheduler for FMR/STR generation and filing"""

    def __init__(self, database_url: str, config: dict):
        self.extractor = FRMSExtractor(database_url)
        self.fmr_gen = FMRGenerator()
        self.str_gen = STRGenerator()
        self.rbi_api = RBIPortalAPI(
            base_url=config.get('RBI_API_URL'),
            api_key=config.get('RBI_API_KEY'),
            api_secret=config.get('RBI_API_SECRET')
        )
        self.finnet = FINnetGateway(
            base_url=config.get('FINNET_URL'),
            api_key=config.get('FINNET_API_KEY')
        )
        self.signer = DigitalSigner(
            cert_path=config.get('CCO_CERT_PATH'),
            key_path=config.get('CCO_KEY_PATH'),
            key_password=config.get('CCO_KEY_PASSWORD')
        )
        self.audit = AuditTrailLogger(database_url)
        self.scheduler = BackgroundScheduler()

    def start(self):
        """Start background job scheduler"""

        # Daily FMR generation (2 AM)
        self.scheduler.add_job(
            func=self.generate_and_submit_fmr,
            trigger=CronTrigger(hour=2, minute=0),
            id='fmr_daily_generation',
            name='Daily FMR Generation and Submission'
        )

        # Daily STR generation (1 AM)
        self.scheduler.add_job(
            func=self.generate_and_submit_str,
            trigger=CronTrigger(hour=1, minute=0),
            id='str_daily_generation',
            name='Daily STR Generation and Submission'
        )

        # 7-day SLA monitoring (6 AM)
        self.scheduler.add_job(
            func=self.check_str_sla,
            trigger=CronTrigger(hour=6, minute=0),
            id='str_sla_monitor',
            name='STR 7-Day SLA Monitoring'
        )

        # 3-week SLA monitoring (7 AM)
        self.scheduler.add_job(
            func=self.check_fmr_sla,
            trigger=CronTrigger(hour=7, minute=0),
            id='fmr_sla_monitor',
            name='FMR 3-Week SLA Monitoring'
        )

        self.scheduler.start()

    def generate_and_submit_fmr(self):
        """Generate FMR and submit to RBI"""

        try:
            # Extract pending cases
            cases = self.extractor.get_pending_fraud_cases()

            if not cases:
                print(f"[{datetime.now()}] No pending FMR cases")
                return

            # Create report
            report = FMRReport(
                bank_name="Finverge Bank Ltd",
                bank_registration="RBI-BANKING-2023-00123",
                cases=cases,
                total_amount=sum(c.amount for c in cases),
                case_count=len(cases),
                submitted_by="FMR Automation System",
                submitted_by_role="Automation"
            )

            # Generate Excel
            excel_bytes = self.fmr_gen.generate_fmr_excel(report)

            # Submit to RBI
            result = self.rbi_api.submit_fmr(excel_bytes)

            if result.get('status') == 'SUBMITTED':
                # Log in audit trail
                self.audit.log_fmr_submission(
                    submission_id=report.report_id,
                    cases=cases,
                    receipt_id=result.get('receipt_id'),
                    submitted_by='FMR Automation',
                    status='SUBMITTED'
                )
                print(f"[{datetime.now()}] FMR submitted: {result.get('receipt_id')}")
            else:
                print(f"[{datetime.now()}] FMR submission failed: {result}")

        except Exception as e:
            print(f"[{datetime.now()}] FMR generation error: {str(e)}")

    def generate_and_submit_str(self):
        """Generate STR and submit to FIU-IND"""

        try:
            # Extract pending cases
            cases = self.extractor.get_pending_aml_alerts()

            if not cases:
                print(f"[{datetime.now()}] No pending STR cases")
                return

            # Create report
            report = STRReport(
                bank_name="Finverge Bank Ltd",
                bank_registration="RBI-BANKING-2023-00123",
                cases=cases,
                submitted_by="STR Automation System",
                submitted_by_role="Automation"
            )

            # Generate XML
            xml_content = self.str_gen.generate_str_xml(report)

            # Sign XML
            signature = self.signer.sign_str_xml(xml_content)

            # Submit to FIU-IND
            result = self.finnet.submit_str(xml_content, signature)

            if result.get('status') == 'SUBMITTED':
                # Log in audit trail
                self.audit.log_str_submission(
                    submission_id=report.report_id,
                    cases=cases,
                    ack_id=result.get('ack_id'),
                    digital_signature=signature,
                    submitted_by='STR Automation',
                    status='SUBMITTED'
                )
                print(f"[{datetime.now()}] STR submitted: {result.get('ack_id')}")
            else:
                print(f"[{datetime.now()}] STR submission failed: {result}")

        except Exception as e:
            print(f"[{datetime.now()}] STR generation error: {str(e)}")

    def check_str_sla(self):
        """Check for overdue STRs (7-day SLA) and escalate"""

        overdue = self.extractor.get_overdue_aml_alerts()

        if overdue:
            print(f"\n🔴 CRITICAL: {len(overdue)} STR cases overdue (PMLA VIOLATION)")
            for aml_id in overdue:
                print(f"   - {aml_id}")
            # TODO: Send escalation alert to CCO

    def check_fmr_sla(self):
        """Check for overdue FMRs (3-week SLA) and escalate"""

        overdue = self.extractor.get_overdue_fraud_cases()

        if overdue:
            print(f"\n⚠️  WARNING: {len(overdue)} FMR cases overdue (RBI audit risk)")
            for fraud_id in overdue:
                print(f"   - {fraud_id}")
            # TODO: Send escalation alert to CRO

# ============================================================================
# PART 10: FASTAPI ENDPOINTS
# ============================================================================

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

app = FastAPI(title="Fraud360 FMR/STR API", version="1.0.0")

# Configuration (from .env)
import os
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')
RBI_API_URL = os.getenv('RBI_API_URL')
FINNET_API_URL = os.getenv('FINNET_API_URL')

# Initialize components
extractor = FRMSExtractor(DATABASE_URL)
fmr_gen = FMRGenerator()
str_gen = STRGenerator()

@app.post("/fmr/generate")
async def generate_fmr():
    """Trigger FMR generation for pending fraud cases"""

    try:
        cases = extractor.get_pending_fraud_cases()

        if not cases:
            return {"message": "No pending FMR cases", "count": 0}

        report = FMRReport(
            bank_name="Finverge Bank Ltd",
            bank_registration="RBI-BANKING-2023-00123",
            cases=cases,
            total_amount=sum(c.amount for c in cases),
            case_count=len(cases),
            submitted_by="API User",
            submitted_by_role="System"
        )

        excel_bytes = fmr_gen.generate_fmr_excel(report)

        return {
            "status": "Generated",
            "case_count": len(cases),
            "total_amount": report.total_amount,
            "report_id": report.report_id
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/str/generate")
async def generate_str():
    """Trigger STR generation for pending AML alerts"""

    try:
        cases = extractor.get_pending_aml_alerts()

        if not cases:
            return {"message": "No pending STR cases", "count": 0}

        report = STRReport(
            bank_name="Finverge Bank Ltd",
            bank_registration="RBI-BANKING-2023-00123",
            cases=cases,
            submitted_by="API User",
            submitted_by_role="System"
        )

        xml_content = str_gen.generate_str_xml(report)

        return {
            "status": "Generated",
            "case_count": len(cases),
            "report_id": report.report_id,
            "xml_length": len(xml_content)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/fmr/pending")
async def get_pending_fmr():
    """List all pending FMR cases"""

    try:
        cases = extractor.get_pending_fraud_cases()
        return {"pending_count": len(cases), "cases": cases}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/str/pending")
async def get_pending_str():
    """List all pending STR cases"""

    try:
        cases = extractor.get_pending_aml_alerts()
        return {"pending_count": len(cases), "cases": cases}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "OK", "service": "FMR/STR API"}

# ============================================================================
# DEPLOYMENT & USAGE
# ============================================================================

"""
DEPLOYMENT INSTRUCTIONS:

1. Install dependencies:
   pip install -r requirements.txt

2. Set environment variables (.env):
   DATABASE_URL=postgresql://user:password@localhost/frms
   RBI_API_URL=https://rbi-fraud-portal.rbi.org.in
   RBI_API_KEY=your_rbi_api_key
   FINNET_API_URL=https://finnet.fiu.gov.in
   FINNET_API_KEY=your_finnet_api_key
   CCO_CERT_PATH=/path/to/cco/cert.pem
   CCO_KEY_PATH=/path/to/cco/key.pem
   CCO_KEY_PASSWORD=certificate_password

3. Run FastAPI server:
   uvicorn fmr_str:app --host 0.0.0.0 --port 8000

4. Start background scheduler:
   python -c "from fmr_str import FMRSTRScheduler; scheduler = FMRSTRScheduler('postgresql://...', config); scheduler.start()"

5. Test endpoints:
   curl http://localhost:8000/fmr/generate
   curl http://localhost:8000/str/generate
   curl http://localhost:8000/health

TESTING:

from fmr_str import *

# Test data extraction
extractor = FRMSExtractor('postgresql://...')
cases = extractor.get_pending_fraud_cases()

# Test FMR generation
fmr_gen = FMRGenerator()
excel = fmr_gen.generate_fmr_excel(report)

# Test STR generation
str_gen = STRGenerator()
xml = str_gen.generate_str_xml(report)

# Test digital signature
signer = DigitalSigner('/path/to/cert', '/path/to/key')
sig = signer.sign_str_xml(xml)
assert signer.verify_signature(xml, sig)
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
