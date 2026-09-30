"""End-to-end: ingest -> parse -> signals -> score -> alert, against the real database.

Complements test_lane_c_signals.py/test_lane_c_scoring.py/test_lane_c_ingestion.py, which
each test one layer in isolation with no database at all. This is the one test that
proves the wiring between them - the SKIP LOCKED claim, the prior-period lookup, the
persisted rows - actually works, the same role test_events_land_and_produce_the_ratios
plays for the CBS/loan-conduct side.
"""
import uuid
from datetime import date

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from sqlalchemy import select

from cp_common.db import SessionLocal
from services.lane_c_service.app import runner
from services.lane_c_service.app.models import (
    ComputedSignal, CreditHealthScore, FinancialStatement, LaneCAlert, ParsedFinancial,
)

ACC = "AC-LANEC-BR214"
ACC_2 = "AC-LANEC-BR214-B"
ACC_3 = "AC-LANEC-BR214-C"


def _write_statement_pdf(path, revenue, inventory, ar, notes):
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    styles = getSampleStyleSheet()
    table = Table([["Line Item", "Amount (Rs)"],
                   ["Total Revenue", f"{revenue:,.2f}"],
                   ["Inventories", f"{inventory:,.2f}"],
                   ["Trade Receivables", f"{ar:,.2f}"]])
    # GRID lines are what makes pdfplumber's default table-detection strategy recognise
    # this as a table at all - without them extract_tables() returns nothing, the same
    # gap test_lane_c_ingestion.py's own PDF builder already accounts for.
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
    story = [
        Paragraph("Balance Sheet", styles["Heading1"]),
        table,
        Paragraph("Notes to Accounts", styles["Heading2"]),
        Paragraph(notes, styles["Normal"]),
    ]
    doc.build(story)


@pytest.fixture(autouse=True)
def _clean(tid):
    yield
    db = SessionLocal()
    try:
        for model in (LaneCAlert, ComputedSignal, CreditHealthScore, ParsedFinancial):
            db.query(model).filter(model.tenant_id == tid).delete()
        db.query(FinancialStatement).filter(FinancialStatement.tenant_id == tid).delete()
        db.commit()
    finally:
        db.close()


def test_first_statement_scores_clean_with_no_prior_period(tmp_path, tid):
    pdf = tmp_path / "q1.pdf"
    _write_statement_pdf(pdf, revenue=10_000_000, inventory=2_000_000, ar=1_000_000,
                         notes="No statutory dues outstanding.")
    db = SessionLocal()
    try:
        db.add(FinancialStatement(
            tenant_id=tid, account=ACC, reporting_date=date(2023, 3, 31),
            filing_type="annual", document_path=str(pdf), sha256="a" * 64,
        ))
        db.commit()

        rep = runner.run_once(db, tid)
        assert rep.claimed == 1
        assert rep.parsed == 1
        assert rep.scored == 1
        assert rep.alerts == 0  # nothing to compare against yet - all ratios unmeasurable

        stmt = db.scalars(select(FinancialStatement).where(
            FinancialStatement.tenant_id == tid, FinancialStatement.account == ACC)).one()
        assert stmt.extraction_status == "scored"
        assert stmt.notes_text  # persisted for the next period's LNC-08 comparison

        score = db.scalars(select(CreditHealthScore).where(
            CreditHealthScore.tenant_id == tid, CreditHealthScore.account == ACC)).one()
        assert score.score_value == 100
        assert score.trend == "new"
    finally:
        db.close()


def test_second_statement_compares_against_the_first_and_fires_alerts(tmp_path, tid):
    db = SessionLocal()
    try:
        pdf1 = tmp_path / "q1.pdf"
        _write_statement_pdf(pdf1, revenue=100_000_000, inventory=20_000_000,
                             ar=8_000_000, notes="No statutory dues outstanding.")
        db.add(FinancialStatement(
            tenant_id=tid, account=ACC_2, reporting_date=date(2023, 3, 31),
            filing_type="annual", document_path=str(pdf1), sha256="b" * 64,
        ))
        db.commit()
        runner.run_once(db, tid)

        # Second period: inventory up sharply while revenue falls - the design doc's
        # own Use Case worked example (test_lane_c_signals.py's inventory tests).
        pdf2 = tmp_path / "q2.pdf"
        _write_statement_pdf(pdf2, revenue=95_000_000, inventory=35_000_000,
                             ar=8_500_000, notes="No statutory dues outstanding.")
        db.add(FinancialStatement(
            tenant_id=tid, account=ACC_2, reporting_date=date(2024, 3, 31),
            filing_type="annual", document_path=str(pdf2), sha256="c" * 64,
        ))
        db.commit()

        rep = runner.run_once(db, tid)
        assert rep.claimed == 1
        assert rep.scored == 1
        assert "LNC-03" in rep.fired_signals  # inventory-vs-turnover, critical per prior tests

        signal = db.scalars(select(ComputedSignal).where(
            ComputedSignal.tenant_id == tid, ComputedSignal.account == ACC_2,
            ComputedSignal.signal_code == "LNC-03")).one()
        assert signal.status == "critical"

        score = db.scalars(select(CreditHealthScore).where(
            CreditHealthScore.tenant_id == tid, CreditHealthScore.account == ACC_2,
            CreditHealthScore.reporting_date == date(2024, 3, 31))).one()
        assert score.score_value < 100
        assert score.trend in ("deteriorating", "stable")  # depends on exact deduction

        alert = db.scalars(select(LaneCAlert).where(
            LaneCAlert.tenant_id == tid, LaneCAlert.account == ACC_2,
            LaneCAlert.signal_code == "LNC-03")).one()
        assert alert.status == "new"
        assert alert.severity == "critical"
    finally:
        db.close()


def test_a_pending_statement_for_another_tenant_is_never_claimed(tmp_path, tid):
    db = SessionLocal()
    other_tenant = str(uuid.uuid4())
    try:
        pdf = tmp_path / "other.pdf"
        _write_statement_pdf(pdf, revenue=1_000_000, inventory=100_000, ar=50_000,
                             notes="Clean.")
        db.add(FinancialStatement(
            tenant_id=other_tenant, account=ACC_3,
            reporting_date=date(2023, 3, 31), document_path=str(pdf), sha256="d" * 64,
        ))
        db.commit()

        rep = runner.run_once(db, tid)
        assert rep.claimed == 0

        stmt = db.scalars(select(FinancialStatement).where(
            FinancialStatement.tenant_id == other_tenant)).one()
        assert stmt.extraction_status == "pending"
        db.delete(stmt)
        db.commit()
    finally:
        db.close()
