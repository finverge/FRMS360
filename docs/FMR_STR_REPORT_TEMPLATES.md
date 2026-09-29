# FMR & STR Report Templates
## RBI & FIU-IND Prescribed Formats (Ready to Use)

**Purpose:** Ready-to-fill templates aligned with RBI and FIU-IND compliance requirements.

---

## Template 1: FMR (Fraud Monitoring Return) - RBI Format

### **FMR IMMEDIATE FILING TEMPLATE**
**For frauds detected within past 3 weeks**

```
═══════════════════════════════════════════════════════════════════════════════
                    FRAUD MONITORING RETURN (FMR) - IMMEDIATE
                         Reserve Bank of India
═══════════════════════════════════════════════════════════════════════════════

PART A: REPORTING ENTITY DETAILS
───────────────────────────────────────────────────────────────────────────────

Name of Bank/NBFC/PACS:        _________________________________
Registration Number (RBI):      _________________________________
Reporting Period:               From: ________  To: ________
Return Submission Date:         _________________________________
Authorized Signatory:           CRO Name: _____________________ Signature: _____

PART B: FRAUD CASE DETAILS (One row per case)
───────────────────────────────────────────────────────────────────────────────

S.No. | Branch | Detection Date | Fraud Type | Amount (₹) | Account | Customer | 
      | Code   | (DD-MMM-YYYY)  |            |            | (masked)| (masked) |
──────┼────────┼────────────────┼────────────┼────────────┼─────────┼──────────┤
  1   | 00123  | 05-AUG-2026    | Digital    | 200,000.00 | ****5678| A.M., S  |
      |        |                |            |            |         |          |
      | MODUS OPERANDI: Unauthorized UPI transfer from dormant account         |
      | ACTION TAKEN: Account blocked; FIR filed; investigation ongoing        |
      | STATUS: UNDER INVESTIGATION                                           |
──────┴────────┴────────────────┴────────────┴────────────┴─────────┴──────────┤

  2   | 00124  | 08-AUG-2026    | Cheque     | 500,000.00 | ****9012| R.K., P  |
      |        |                |            |            |         |          |
      | MODUS OPERANDI: Cheque fraud with forged signature; clearing fraud     |
      | ACTION TAKEN: Legal proceedings filed; amount recovered 400k           |
      | STATUS: RESOLVED                                                      |
──────┴────────┴────────────────┴────────────┴────────────┴─────────┴──────────┤

  3   | 00125  | 09-AUG-2026    | Card       | 150,000.00 | ****3456| N.V., M  |
      |        |                |            |            |         |          |
      | MODUS OPERANDI: Unauthorized card transaction abroad; CNP fraud        |
      | ACTION TAKEN: Card cancelled; chargeback issued; full refund           |
      | STATUS: RESOLVED                                                      |
──────┴────────┴────────────────┴────────────┴────────────┴─────────┴──────────┘

PART C: FRAUD CLASSIFICATION BREAKDOWN
───────────────────────────────────────────────────────────────────────────────

Fraud Type         | Count | Total Amount (₹) | Recovery (₹) | % Recovered
───────────────────┼───────┼──────────────────┼──────────────┼─────────────
Digital Fraud      |   1   |      200,000     |       0      |     0%
Cheque Fraud       |   1   |      500,000     |     400,000  |    80%
Card Fraud         |   1   |      150,000     |     150,000  |   100%
Loan Fraud         |   -   |         -        |       -      |     -
Transfer Fraud     |   -   |         -        |       -      |     -
─────────────────────────────────────────────────────────────────────────────
TOTAL              |   3   |      850,000     |     550,000  |   65%

PART D: SUMMARY & COMPLIANCE STATUS
───────────────────────────────────────────────────────────────────────────────

Total Fraud Cases Reported:         3
Total Fraud Amount (₹):             850,000.00
Average Fraud per Case (₹):         283,333.33
Total Recovery Amount (₹):          550,000.00
Recovery Rate (%):                  64.71%

Cases Resolved:                     2
Cases Under Investigation:          1
Cases Pending Action:               0

Fraud Detection Method (% breakdown):
  - Real-time fraud detection (Lane A):    100%
  - Manual reporting:                      0%
  
Average Detection Latency:          < 5 minutes (Lane A real-time)
Fastest Detection:                  3 minutes
Slowest Detection:                  15 minutes

PART E: ACTION TAKEN BY BANK
───────────────────────────────────────────────────────────────────────────────

Account Actions:
  ☑ Account frozen/blocked:         3 cases
  ☐ Account suspended:              - cases
  ☐ Account monitored:              - cases
  
Legal Actions:
  ☑ FIR filed with Police:          2 cases (fraud amounts > ₹500K)
  ☑ Case filed with Court:          1 case
  ☑ Amount recovered:               1 case (₹400K cheque fraud)
  
Preventive Actions:
  ☑ Enhanced monitoring activated:  3 cases
  ☑ Customer contacted:             3 cases
  ☑ PAN-level monitoring:           3 cases

PART F: REGULATORY REPORTING STATUS
───────────────────────────────────────────────────────────────────────────────

All cases reported to RBI within 3-week timeline:  ☑ YES  ☐ NO
FIR filed cases:                                    2
Cases referred to Police:                           2
STR filed to FIU-IND (if AML-related):             0 (all are pure fraud)

PART G: CERTIFICATIONS & APPROVALS
───────────────────────────────────────────────────────────────────────────────

I hereby certify that:
  ☑ All information provided is accurate and complete
  ☑ All fraud cases detected have been reported within 3-week timeline
  ☑ All customer data has been properly masked per RBI guidelines
  ☑ All legal proceedings are ongoing as stated
  ☑ The bank has taken appropriate preventive measures

Chief Risk Officer (CRO):
  Name: _____________________________
  Signature: ________________________  Date: ______________

Chief Compliance Officer (CCO):
  Name: _____________________________
  Signature: ________________________  Date: ______________

Board Audit Committee Member (Optional):
  Name: _____________________________
  Signature: ________________________  Date: ______________

═══════════════════════════════════════════════════════════════════════════════
                            CONFIDENTIAL - RBI ONLY
═══════════════════════════════════════════════════════════════════════════════
```

---

### **FMR QUARTERLY CONSOLIDATION TEMPLATE**
**For consolidated quarterly submission (Mar 31, Jun 30, Sep 30, Dec 31)**

```
═══════════════════════════════════════════════════════════════════════════════
               FRAUD MONITORING RETURN - QUARTERLY CONSOLIDATION
                         Reserve Bank of India
                          Quarter: Q3 2026 (Jul-Sep)
═══════════════════════════════════════════════════════════════════════════════

PART A: REPORTING ENTITY & PERIOD
───────────────────────────────────────────────────────────────────────────────

Name of Bank/NBFC:              Finverge Bank Ltd
RBI Registration Number:         RBI-BANKING-2023-00123
Reporting Quarter:               Q3 2026 (01-JUL-2026 to 30-SEP-2026)
Quarter End Date:                30-SEP-2026
Return Submission Date:          10-OCT-2026 (within 2 weeks post quarter-end)

PART B: QUARTERLY FRAUD STATISTICS
───────────────────────────────────────────────────────────────────────────────

Total Fraud Cases Detected:                 45
Total Fraud Amount (₹):                     4,50,00,000 (4.5 crores)
Average Fraud per Case (₹):                 10,00,000

Cases Resolved:                             38 (84%)
Cases Under Investigation:                  5 (11%)
Cases Pending Action:                       2 (5%)

Total Recovery Amount (₹):                  3,60,00,000 (3.6 crores)
Recovery Rate (%):                          80%

PART C: FRAUD TYPE BREAKDOWN
───────────────────────────────────────────────────────────────────────────────

Fraud Type         | Count | Amount (₹)    | % of Total | Recovery (₹) | % Recovered
──────────────────┼───────┼───────────────┼────────────┼──────────────┼─────────────
Digital Fraud      |  18   | 1,20,00,000   |   26.7%    | 1,15,00,000  |   95.8%
Cheque Fraud       |  12   | 1,50,00,000   |   33.3%    | 1,40,00,000  |   93.3%
Card Fraud         |   8   |  75,00,000    |   16.7%    |  60,00,000   |   80.0%
Loan Fraud         |   5   |  85,00,000    |   18.9%    |  35,00,000   |   41.2%
Transfer Fraud     |   2   |  20,00,000    |    4.4%    |  10,00,000   |   50.0%
──────────────────┼───────┼───────────────┼────────────┼──────────────┼─────────────
TOTAL              |  45   | 4,50,00,000   |  100.0%    | 3,60,00,000  |   80.0%

PART D: DETECTION & RESPONSE METRICS
───────────────────────────────────────────────────────────────────────────────

Average Detection Latency:              < 5 minutes (Lane A real-time system)
Fastest Detection:                      2 minutes
Slowest Detection:                      45 minutes

Detection Method Breakdown:
  ☑ Real-time fraud detection (Lane A):  42 cases (93%)
  ☑ Near-real-time monitoring (Lane B):  2 cases (4%)
  ☐ Manual discovery:                    1 case (2%)

Average Investigation Duration:        18 days (from detection to action)
Median Investigation Duration:          14 days

PART E: BRANCH-WISE FRAUD DISTRIBUTION
───────────────────────────────────────────────────────────────────────────────

Branch Code | Branch Name           | Cases | Amount (₹)   | Recovery Rate
────────────┼──────────────────────┼───────┼──────────────┼────────────────
   00123    | Mumbai Fort Branch    |  12   | 1,20,00,000  |      82%
   00124    | Delhi Connaught Place |   8   |  85,00,000   |      75%
   00125    | Bangalore Koramangala |   7   |  70,00,000   |      88%
   00126    | Hyderabad Banjara     |   6   |  60,00,000   |      80%
   00127    | Kolkata Shakespeare   |   5   |  50,00,000   |      78%
   00128    | Chennai Mount Road    |   4   |  40,00,000   |      85%
   Others (14 branches)               |   3   |  30,00,000   |      70%
────────────┼──────────────────────┼───────┼──────────────┼────────────────
TOTAL       |                      |  45   | 4,50,00,000  |      80%

PART F: ACTIONS TAKEN BY BANK
───────────────────────────────────────────────────────────────────────────────

Account Actions:
  ☑ Accounts frozen:                 38 cases
  ☑ Accounts monitored:              7 cases
  ☑ FIR filed with Police:          28 cases (high-value frauds >₹5L)
  
Legal Actions:
  ☑ Criminal cases filed:            20
  ☑ Civil recovery suits:            8
  ☑ SARFAESI cases:                 2 (loan fraud)
  ☑ Cybercrime complaints:          5

Recovery Actions:
  ☑ Direct recovery:                 ₹2,80,00,000 (78% of amount recovered)
  ☑ Insurance reimbursement:         ₹60,00,000 (17% of amount recovered)
  ☑ Police recovery:                 ₹20,00,000 (5% of amount recovered)

PART G: REGULATORY COMPLIANCE
───────────────────────────────────────────────────────────────────────────────

All Fraud Cases Filed with RBI:        45/45 (100%)
  - Within 3-week timeline:            44 cases (97.8%)
  - Post 3-week (with justification):  1 case (2.2%)

STR Filed to FIU-IND (AML-related):   3 cases
  - Filed within 7 days:               3/3 (100%)

FIR Filed with Police:                28 cases
  - Filed within 7 days of FMR:        27 cases (96%)
  - Delayed (with justification):      1 case (4%)

Board Audit Committee Review:         ☑ YES (Reviewed in quarterly meeting)
Risk Committee Briefing:              ☑ YES (Presented with trend analysis)

PART H: SUMMARY & TRENDS
───────────────────────────────────────────────────────────────────────────────

Compared to Previous Quarter (Q2 2026):
  Fraud Cases:        45 (Q3) vs. 38 (Q2) → +18% increase
  Fraud Amount:       ₹4.5Cr (Q3) vs. ₹4.2Cr (Q2) → +7% increase
  Recovery Rate:      80% (Q3) vs. 75% (Q2) → +5% improvement

Year-to-Date Performance (Jan-Sep 2026):
  Total Frauds:       112 cases
  Total Loss:         ₹11.5 crores
  Total Recovery:     ₹9.2 crores (80% recovery rate)
  Avg per quarter:    37 frauds, ₹3.8 Cr loss

Risk Trend Analysis:
  ☑ Digital fraud increasing (trend: +12% YoY)
  ☑ Detection latency improving (trend: -40% vs. previous year)
  ☑ Recovery rate improving (trend: +15% vs. previous year)
  ☐ Customer complaints: 2 cases (related to false positives)

PART I: CERTIFICATIONS & APPROVALS
───────────────────────────────────────────────────────────────────────────────

Certified by:

Chief Risk Officer (CRO):
  Name: _____________________________
  Signature: ________________________  Date: ______________

Chief Compliance Officer (CCO):
  Name: _____________________________
  Signature: ________________________  Date: ______________

Chief Executive Officer (CEO):
  Name: _____________________________
  Signature: ________________________  Date: ______________

Board Audit Committee Approval:
  Committee Chair: ____________________  Date: ______________

═══════════════════════════════════════════════════════════════════════════════
                            CONFIDENTIAL - RBI ONLY
═══════════════════════════════════════════════════════════════════════════════
```

---

## Template 2: STR (Suspicious Transaction Report) - FIU-IND Format

### **STR FILING TEMPLATE (XML/FINnet Format)**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<SuspiciousTransactionReport>
  
  <!-- ===== REPORTING ENTITY INFORMATION ===== -->
  <ReportingEntity>
    <EntityName>Finverge Bank Ltd</EntityName>
    <EntityType>SCHEDULED_BANK</EntityType>
    <EntityID>BANK123456</EntityID>
    <RBIRegistrationNumber>RBI-BANKING-2023-00123</RBIRegistrationNumber>
    <CRN>Not Applicable (Banks)</CRN>
    <ReportingOfficerName>Chief Compliance Officer</ReportingOfficerName>
    <ReportingOfficerEmail>compliance@finverge.com</ReportingOfficerEmail>
    <ReportingOfficerPhone>+91-22-XXXX-XXXX</ReportingOfficerPhone>
  </ReportingEntity>

  <!-- ===== STR FILING DETAILS ===== -->
  <STRDetails>
    <UniqueSTRID>f47ac10b-58cc-4372-a567-0e02b2c3d479</UniqueSTRID>
    <STRReferenceNumber>STR-2026-08-00001</STRReferenceNumber>
    <FilingDate>2026-08-10</FilingDate>
    <ReportingPeriod>Q3-2026</ReportingPeriod>
    <SuspicionDetectionDate>2026-08-09</SuspicionDetectionDate>
    <DaysSinceSuspicion>1</DaysSinceSuspicion>
  </STRDetails>

  <!-- ===== CUSTOMER IDENTIFICATION (MASKED) ===== -->
  <Customer>
    <CustomerID>CUST-2026-00123</CustomerID>
    <CustomerType>INDIVIDUAL</CustomerType>
    <CustomerName>S******, A.M.</CustomerName>
    <DateOfBirth>XXXX-XX-XX</DateOfBirth>
    <Gender>M</Gender>
    
    <KYCDetails>
      <KYCStatus>VERIFIED</KYCStatus>
      <KYCVerificationDate>2022-03-15</KYCVerificationDate>
      <KYCExpiry>2027-03-14</KYCExpiry>
      <PAN>****1234</PAN>
      <Aadhaar>****5678</Aadhaar>
      <Passport>Not Provided</Passport>
    </KYCDetails>
    
    <OccupationDetails>
      <Occupation>Business Owner</Occupation>
      <BusinessType>Trading/Import-Export</BusinessType>
      <IncomeLevel>High (>25 Lac/year)</IncomeLevel>
    </OccupationDetails>
    
    <ResidentialAddress>
      <AddressLine1>XXXXXX, Mumbai</AddressLine1>
      <State>Maharashtra</State>
      <Country>India</Country>
      <PostalCode>XXXXXX</PostalCode>
    </ResidentialAddress>
    
    <ContactDetails>
      <MobileNumber>+91-XXXXXX5678</MobileNumber>
      <Email>MASKED@finverge.com</Email>
    </ContactDetails>
  </Customer>

  <!-- ===== ACCOUNT DETAILS ===== -->
  <Account>
    <AccountNumber>****9012</AccountNumber>
    <AccountType>SAVINGS</AccountType>
    <AccountOpenDate>2020-06-01</AccountOpenDate>
    <AccountStatus>ACTIVE</AccountStatus>
    <Branch>
      <BranchCode>00123</BranchCode>
      <BranchName>Mumbai Fort Branch</BranchName>
      <City>Mumbai</City>
      <State>Maharashtra</State>
    </Branch>
    <AccountBalance>2,50,000.00</AccountBalance>
    <AverageMonthlyDebit>1,50,000.00</AverageMonthlyDebit>
    <AverageMonthlyCredit>2,00,000.00</AverageMonthlyCredit>
  </Account>

  <!-- ===== TRANSACTION DETAILS ===== -->
  <Transaction>
    <TransactionID>TXN-2026-00456</TransactionID>
    <TransactionDate>2026-08-09</TransactionDate>
    <TransactionTime>10:45:23</TransactionTime>
    <TransactionAmount>9,99,000.00</TransactionAmount>
    <Currency>INR</Currency>
    <TransactionType>DEBIT</TransactionType>
    <TransactionChannel>UPI</TransactionChannel>
    
    <BeneficiaryDetails>
      <BeneficiaryType>INDIVIDUAL</BeneficiaryType>
      <BeneficiaryName>B***, R.V.</BeneficiaryName>
      <BeneficiaryAccountNumber>****3456</BeneficiaryAccountNumber>
      <BeneficiaryBank>HDFC Bank Ltd</BeneficiaryBank>
      <BeneficiaryBankCode>HDFC0001234</BeneficiaryBankCode>
      <BeneficiaryIFSC>HDFC0001234</BeneficiaryIFSC>
      <BeneficiaryUPI>MASKED@hdfc</BeneficiaryUPI>
    </BeneficiaryDetails>
    
    <TransactionDetails>
      <TransactionPurpose>Payment for goods</TransactionPurpose>
      <TransactionNarrative>UPI payment for supply of materials</TransactionNarrative>
      <Deviation>Transaction just below CTR reporting threshold (₹10L)</Deviation>
    </TransactionDetails>
  </Transaction>

  <!-- ===== RELATED TRANSACTIONS (Pattern Analysis) ===== -->
  <RelatedTransactions>
    <TransactionPattern>
      <PatternDescription>Multiple structuring transfers within 24 hours</PatternDescription>
      <Count>5</Count>
      <PeriodAnalyzed>24 hours (09-AUG-2026)</PeriodAnalyzed>
      <TransactionSequence>
        <Transaction1>₹9,99,000 to Account A (10:45 AM)</Transaction1>
        <Transaction2>₹9,50,000 to Account B (11:20 AM)</Transaction2>
        <Transaction3>₹9,75,000 to Account C (02:15 PM)</Transaction3>
        <Transaction4>₹9,90,000 to Account D (03:45 PM)</Transaction4>
        <Transaction5>₹9,85,000 to Account E (06:00 PM)</Transaction5>
      </TransactionSequence>
      <TotalAmount>49,99,000.00 (below 5×10L threshold)</TotalAmount>
    </TransactionPattern>
  </RelatedTransactions>

  <!-- ===== SUSPICION DETAILS ===== -->
  <SuspicionDetails>
    <PrimarySuspicionType>STRUCTURING</PrimarySuspicionType>
    <SuspicionLevel>HIGH</SuspicionLevel>
    <SuspicionScore>82/100</SuspicionScore>
    
    <SuspicionIndicators>
      <Indicator>
        <Code>SME-01</Code>
        <Description>Sub-CTR Structuring: Multiple transfers just below ₹10L reporting threshold</Description>
        <Weight>Critical</Weight>
        <Match>Confirmed</Match>
      </Indicator>
      
      <Indicator>
        <Code>LAY-01</Code>
        <Description>Outflow Drain: 95% of funds transferred out within 24 hours</Description>
        <Weight>Critical</Weight>
        <Match>Confirmed</Match>
      </Indicator>
      
      <Indicator>
        <Code>BEH-01</Code>
        <Description>Dormancy: Account inactive for 180 days before this activity</Description>
        <Weight>High</Weight>
        <Match>Confirmed</Match>
      </Indicator>
      
      <Indicator>
        <Code>LAY-02</Code>
        <Description>Multiple Counterparties: Transfers to 5 different accounts (fan-out pattern)</Description>
        <Weight>High</Weight>
        <Match>Confirmed</Match>
      </Indicator>
    </SuspicionIndicators>
    
    <NarrativeDescription>
      Account holder with 180+ days of dormancy suddenly received ₹9.99L transfer 
      on 09-AUG-2026 at 10:45 AM via UPI. Within 6 hours, this amount was transferred 
      out to 5 different beneficiary accounts in structurally fragmented amounts 
      (₹9.5L-₹9.99L each), each just below the ₹10 lakh CTR (Currency Transaction Report) 
      threshold. This pattern is consistent with structuring to evade regulatory 
      reporting obligations and indicators of money laundering/layering activity.
      
      Risk Assessment: HIGH - Recommended for immediate STR filing and monitoring.
    </NarrativeDescription>
    
    <RegulatoryBasis>
      <PMLASection>Section 29 of Prevention of Money Laundering Act, 2002</PMLASection>
      <RBIDirection>RBI Master Directions 2024-25, Section 12.2.2 (AML/CFT Surveillance)</RBIDirection>
      <FraudSignal>Lane B Signal: SME-01 (Structuring Detection)</FraudSignal>
    </RegulatoryBasis>
  </SuspicionDetails>

  <!-- ===== RISK ASSESSMENT ===== -->
  <RiskAssessment>
    <OverallRisk>HIGH</OverallRisk>
    <MoneyLaunderingRisk>HIGH</MoneyLaunderingRisk>
    <TerroristFinancingRisk>MEDIUM</TerroristFinancingRisk>
    <FinancialCrimeRisk>HIGH</FinancialCrimeRisk>
    
    <PEPStatus>NOT MATCHED</PEPStatus>
    <SanctionsStatus>NOT MATCHED TO OFAC/UNSC</SanctionsStatus>
    <PreviousAMLHistory>No prior STR history</PreviousAMLHistory>
    <CustomerRiskRating>MEDIUM (Business type: Trading; High transaction volume)</CustomerRiskRating>
  </RiskAssessment>

  <!-- ===== INTERNAL TRACKING & APPROVAL ===== -->
  <InternalTracking>
    <InternalReferenceNumber>STR-2026-08-00001</InternalReferenceNumber>
    <CreatedDate>2026-08-10</CreatedDate>
    <CreatedBy>Fraud Detection System (Lane B)</CreatedBy>
    
    <ApprovalChain>
      <Level1Reviewer>
        <Title>Fraud Analyst</Title>
        <Name>John Smith</Name>
        <ReviewDate>2026-08-10</ReviewDate>
        <Decision>APPROVED</Decision>
      </Level1Reviewer>
      
      <Level2Reviewer>
        <Title>Head of Compliance</Title>
        <Name>Jane Doe</Name>
        <ReviewDate>2026-08-10</ReviewDate>
        <Decision>APPROVED FOR FILING</Decision>
      </Level2Reviewer>
      
      <FinalApprovalBy>Chief Compliance Officer</FinalApprovalBy>
      <FinalApprovalDate>2026-08-10</FinalApprovalDate>
      <DigitalSignatureValue>SHA256:A8F2E9D4C7B1...</DigitalSignatureValue>
      <CertificateAuthority>India Post CA</CertificateAuthority>
    </ApprovalChain>
  </InternalTracking>

  <!-- ===== COMPLIANCE & FILING STATUS ===== -->
  <ComplianceStatus>
    <FilingStatus>SUBMITTED</FilingStatus>
    <SubmissionDate>2026-08-10</SubmissionDate>
    <FINnetGatewayReceived>YES</FINnetGatewayReceived>
    <FINnetAcknowledgmentID>ACK-FIU-2026-08-00001</FINnetAcknowledgmentID>
    <FINnetAcknowledgmentDate>2026-08-10</FINnetAcknowledgmentDate>
    <FINnetAcknowledgmentTime>10:55 AM IST</FINnetAcknowledgmentTime>
    <DaysSinceDetection>1 day (Well within 7-day SLA)</DaysSinceDetection>
    <ComplianceStatus>COMPLIANT</ComplianceStatus>
  </ComplianceStatus>

  <!-- ===== DATA PRIVACY & SECURITY CERTIFICATIONS ===== -->
  <SecurityCertifications>
    <DataMaskingCompliance>VERIFIED</DataMaskingCompliance>
    <EncryptionMethod>AES-256</EncryptionMethod>
    <DigitalSignature>VALID (CA-signed)</DigitalSignature>
    <AuditTrailRetention>7 years (Immutable)</AuditTrailRetention>
    <PersonalDataCompliance>PII masked per RBI/FIU guidelines</PersonalDataCompliance>
  </SecurityCertifications>

</SuspiciousTransactionReport>
```

---

### **STR FILING TEMPLATE (Excel Format - Simplified)**

```
═══════════════════════════════════════════════════════════════════════════════
              SUSPICIOUS TRANSACTION REPORT (STR) - FIU-IND SUBMISSION
                            Excel Format Template
═══════════════════════════════════════════════════════════════════════════════

PART A: REPORTING ENTITY
───────────────────────────────────────────────────────────────────────────────
Reporting Entity Name:          Finverge Bank Ltd
Entity Type:                    Scheduled Bank
Entity ID:                      BANK123456
RBI Registration Number:        RBI-BANKING-2023-00123
Compliance Officer:             [CCO Name]
Contact Email:                  compliance@finverge.com
Filing Portal:                  FINnet Gateway (FIU-IND)

PART B: SUSPICIOUS TRANSACTION DETAILS
───────────────────────────────────────────────────────────────────────────────

STR Reference ID:               STR-2026-08-00001
Detection Date:                 09-AUG-2026
Filing Date:                    10-AUG-2026
Days to File:                   1 (Within 7-day SLA) ✓

CUSTOMER INFORMATION (Masked)
─────────────────────────────
Customer Name (Masked):         S******, A.M.
Customer ID:                    CUST-2026-00123
PAN (Masked):                   ****1234
Aadhaar (Masked):               ****5678
Date of Birth:                  [MASKED]
Occupation:                     Business Owner
KYC Status:                     VERIFIED (01-MAR-2022)

ACCOUNT INFORMATION
─────────────────
Account Number (Masked):        ****9012
Account Type:                   SAVINGS
Account Opening Date:           01-JUN-2020
Account Status:                 ACTIVE
Branch Code:                    00123
Branch Name:                    Mumbai Fort Branch

TRANSACTION DETAILS
──────────────────
Transaction ID:                 TXN-2026-00456
Transaction Date:               09-AUG-2026
Transaction Time:               10:45:23 AM
Transaction Amount:             ₹9,99,000.00
Currency:                       INR
Transaction Channel:            UPI
Transaction Type:               Debit/Transfer
Purpose:                        Payment for goods

BENEFICIARY INFORMATION (Masked)
─────────────────────────────────
Beneficiary Name (Masked):      B***, R.V.
Beneficiary Account (Masked):   ****3456
Beneficiary Bank:               HDFC Bank Ltd
Beneficiary IFSC:               HDFC0001234
Beneficiary UPI:                [MASKED]

SUSPICION INDICATORS & ANALYSIS
───────────────────────────────

Primary Suspicion Type:         STRUCTURING

Suspicion Details:
  ✓ Sub-CTR Structuring (SME-01):      Amount just below ₹10L threshold
  ✓ Multiple Transfers (24h):          5 transfers in fan-out pattern
  ✓ Dormant Account:                   180+ days inactive before this
  ✓ Outflow Pattern:                   95% of funds transferred out
  ✓ Multiple Counterparties:           5 different beneficiary accounts

Risk Score:                     82/100 (HIGH RISK)

Narrative Reason for Suspicion:
  Account with 180+ days of dormancy suddenly received ₹9.99L via UPI. 
  Within 6 hours, amount structurally split into 5 transfers 
  (₹9.5L-₹9.99L each) to different accounts, each just below ₹10L 
  CTR threshold. Pattern consistent with structuring for AML evasion.

PART C: RISK INDICATORS
───────────────────────

PEP Status:                     ✓ NOT MATCHED
Sanctions Match (OFAC/UNSC):    ✓ NOT MATCHED
Previous STR Filed:             ✓ NO
Terrorist Financing Risk:       LOW
Money Laundering Risk:          HIGH

PART D: REGULATORY BASIS
───────────────────────

PMLA Section:                   Section 29 (Suspicious Transaction Report)
RBI Direction:                  Master Directions 2024-25, Section 12.2.2
Signal Code:                    SME-01 (Structuring Detection)
Filing Obligation:              Within 7 days (Mandatory)

PART E: INTERNAL APPROVALS
──────────────────────────

Reviewed by (Fraud Analyst):    [Name] _______________  Date: 10-AUG-2026
Approved by (Head Compliance):  [Name] _______________  Date: 10-AUG-2026
Final Approval (CCO):           [Name] _______________  Date: 10-AUG-2026
Digital Signature:              ☑ Applied (CA-signed)

PART F: FILING STATUS
────────────────────

Filed to FIU-IND:               ✓ YES
Submission Date:                10-AUG-2026
Submission Method:              FINnet Gateway (Secure)
FIU-IND Acknowledgment ID:       ACK-FIU-2026-08-00001
Acknowledgment Date:             10-AUG-2026 (10:55 AM)
Compliance Status:              ✓ COMPLIANT (Filed within 7 days)

═══════════════════════════════════════════════════════════════════════════════
                      CONFIDENTIAL - FIU-IND / BANK ONLY
═══════════════════════════════════════════════════════════════════════════════
```

---

## Usage Instructions

### **For FMR Filing:**
1. Copy the FMR IMMEDIATE FILING template
2. Fill in fraud cases detected in past 3 weeks
3. Calculate summary statistics
4. Obtain CRO/CCO signatures
5. Submit to RBI portal (online or via email)
6. Keep copy for 7-year audit trail

### **For STR Filing:**
1. Copy the STR XML template
2. Fill in customer, account, transaction details (with masking)
3. Describe suspicion narrative (max 500 chars)
4. Obtain CCO approval + digital signature
5. Submit to FINnet Gateway (FIU-IND portal)
6. Keep acknowledgment receipt

### **Mandatory Data Masking Rules:**
- **PAN:** Show only last 4 digits (****1234)
- **Aadhaar:** Show only last 4 digits (****5678)
- **Account:** Show only last 4 digits (****9012)
- **Customer Name:** First letter + surname (A***, S****)
- **Address:** Show state/city only, no street address
- **Email:** Mask most of address (****@finverge.com)
- **Phone:** Show only last 4 digits (+91-****-5678)

### **Filing Timeline:**
- **FMR:** Within 3 weeks of detection; Quarterly consolidation
- **STR:** Within 7 days of suspicion detection (CRITICAL SLA)

### **Audit Trail:**
- Maintain immutable logs of all FMR/STR filings for 7 years
- Track RBI/FIU-IND acknowledgments
- Document all approvals and digital signatures

