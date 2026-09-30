"""Document parsing: a synthetic two-quarter financial-statement PDF, built with
reportlab in the test itself rather than a committed binary fixture - the numbers in the
table are the assertion, so generating them here keeps the test self-describing.

Only the text-layer path is exercised here - it is what pdfplumber reads directly, no
Tesseract binary required to run this suite. The OCR fallback (document_parser.py's
``_try_ocr_page``) degrades to "" when pytesseract or the tesseract binary is missing,
which is exercised separately and does not need a real scanned PDF to test.
"""
import pytest
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

from services.lane_c_service.app.ingestion import document_parser as dp
from services.lane_c_service.app.ingestion.gl_mapper import metric_code_for, to_paise


def _build_statement_pdf(path, rows, notes_paragraphs):
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    styles = getSampleStyleSheet()
    story = [Paragraph("Balance Sheet", styles["Heading1"])]
    table = Table([["Line Item", "Amount (Rs)"]] + rows)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
    ]))
    story.append(table)
    story.append(Spacer(1, 24))
    story.append(Paragraph("Notes to Accounts", styles["Heading2"]))
    for p in notes_paragraphs:
        story.append(Paragraph(p, styles["Normal"]))
    doc.build(story)


# ------------------------------------------------------------------ gl_mapper
def test_gl_mapper_recognises_common_line_item_wordings():
    assert metric_code_for("Total Revenue") == "REVENUE"
    assert metric_code_for("Trade Receivables") == "AR"
    assert metric_code_for("Sundry Debtors") == "AR"
    assert metric_code_for("Inventories") == "INVENTORY"
    assert metric_code_for("Some Unrelated Line") is None


def test_to_paise_handles_commas_and_parentheses():
    assert to_paise("1,234.56") == 123456
    assert to_paise("(500.00)") == -50000
    assert to_paise("not a number") is None


# ------------------------------------------------------------------ document_parser
def test_extracts_line_items_from_a_born_digital_pdf(tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    _build_statement_pdf(pdf_path, rows=[
        ["Total Revenue", "10,000,000.00"],
        ["Inventories", "2,000,000.00"],
        ["Trade Receivables", "1,500,000.00"],
    ], notes_paragraphs=[
        "The company has no unpaid statutory dues outstanding as of the reporting date.",
    ])

    result = dp.extract(str(pdf_path))
    assert result.ok, result.error
    by_code = {m.metric_code: m for m in result.metrics}
    assert by_code["REVENUE"].value_paise == 10_000_000 * 100
    assert by_code["INVENTORY"].value_paise == 2_000_000 * 100
    assert by_code["AR"].value_paise == 1_500_000 * 100
    assert all(m.confidence == 1.0 for m in result.metrics)  # text-layer, full confidence
    assert result.pages_ocred == 0
    assert "statutory dues" in result.notes_text.lower()


def test_a_corrupt_file_is_reported_not_raised(tmp_path):
    bad = tmp_path / "not_a_pdf.pdf"
    bad.write_bytes(b"this is not a pdf file")
    result = dp.extract(str(bad))
    assert not result.ok
    assert result.error


def test_an_unrecognised_layout_reports_no_line_items_found(tmp_path):
    pdf_path = tmp_path / "empty.pdf"
    _build_statement_pdf(pdf_path, rows=[["Some Unrelated Line", "999.00"]],
                         notes_paragraphs=["Nothing relevant here."])
    result = dp.extract(str(pdf_path))
    assert not result.ok
    assert "no recognisable" in result.error
