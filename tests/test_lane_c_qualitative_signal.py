"""LNC-09 - a real local-LLM call over real statement text, same posture as
test_ai_insights.py: the happy-path test needs an actual model running and is skipped,
not mocked, when one isn't reachable, so this file stays honest about being an
integration test. The always-run tests cover the two paths that don't need a live
model: off-by-default, and the quote-verification gate that stops a hallucinated
finding from ever being scored.
"""
import httpx
import pytest

from cp_common import settings
from services.lane_c_service.app.signals import qualitative_red_flags as qrf


def _llm_reachable() -> bool:
    try:
        httpx.get(settings.lane_c_llm_base_url.replace("/v1", "/api/tags"), timeout=2)
        return True
    except httpx.HTTPError:
        return False


needs_llm = pytest.mark.skipif(not _llm_reachable(), reason="no local LLM reachable at lane_c_llm_base_url")


def test_off_by_default_is_always_unmeasurable(monkeypatch):
    monkeypatch.setattr(settings, "lane_c_llm_enabled", False)
    result = qrf.compute_qualitative_red_flags("Some statement text with issues.")
    assert result.status == "unmeasurable"
    assert "not enabled" in result.evidence


def test_empty_text_is_unmeasurable_even_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "lane_c_llm_enabled", True)
    result = qrf.compute_qualitative_red_flags("")
    assert result.status == "unmeasurable"


def test_unreachable_llm_is_unmeasurable_not_fabricated(monkeypatch):
    monkeypatch.setattr(settings, "lane_c_llm_enabled", True)
    monkeypatch.setattr(settings, "lane_c_llm_base_url", "http://127.0.0.1:1")
    result = qrf.compute_qualitative_red_flags("Some statement text with issues.")
    assert result.status == "unmeasurable"
    assert "LLM read failed" in result.evidence


def test_a_finding_whose_quote_is_not_in_the_source_text_is_dropped(monkeypatch):
    """The hard gate: even if the model returns a well-formed finding, it is discarded
    unless its quoted sentence is an actual substring of the statement text - a model
    that invents a quote must never have that invention repeated as Lane C evidence."""
    notes = "Revenue grew steadily. Inventory levels were stable throughout the year."

    def _fake_call_llm(_notes_text):
        return {
            "findings": [{
                "category": "discrepancy",
                "quote": "The auditor found undisclosed related-party loans.",  # not in notes
                "reason": "This sentence does not actually appear in the source text.",
            }],
            "confidence": 80,
        }

    monkeypatch.setattr(settings, "lane_c_llm_enabled", True)
    monkeypatch.setattr(qrf, "_call_llm", _fake_call_llm)
    result = qrf.compute_qualitative_red_flags(notes)
    assert result.status == "pass"
    assert "No qualitative red flags" in result.evidence


def test_a_quote_that_differs_only_by_a_pdf_line_wrap_still_verifies(monkeypatch):
    """Reproduces a real failure found while verifying this signal against an actual
    extracted PDF: pdfplumber preserves the source document's own visual line breaks as
    literal newlines mid-sentence, but a model's returned quote naturally uses a plain
    space there. That must not read as a hallucinated quote."""
    notes = ("The total inventory stated on the balance sheet does not reconcile with\n"
             "the sum of the individually listed stock categories in Note 14.")

    def _fake_call_llm(_notes_text):
        return {
            "findings": [{
                "category": "discrepancy",
                "quote": ("The total inventory stated on the balance sheet does not "
                          "reconcile with the sum of the individually listed stock "
                          "categories in Note 14."),  # same words, no line-wrap newline
                "reason": "Inventory figures do not reconcile.",
            }],
            "confidence": 90,
        }

    monkeypatch.setattr(settings, "lane_c_llm_enabled", True)
    monkeypatch.setattr(qrf, "_call_llm", _fake_call_llm)
    result = qrf.compute_qualitative_red_flags(notes)
    assert result.status != "pass"
    assert result.status != "unmeasurable"


def test_a_verified_finding_is_kept_and_scored(monkeypatch):
    notes = ("Revenue grew steadily. The auditor's report notes a material discrepancy "
             "between the stated inventory total and the sum of its own line items.")

    def _fake_call_llm(_notes_text):
        return {
            "findings": [{
                "category": "discrepancy",
                "quote": ("The auditor's report notes a material discrepancy between "
                          "the stated inventory total and the sum of its own line items."),
                "reason": "Inventory total does not reconcile with its own line items.",
            }],
            "confidence": 85,
        }

    monkeypatch.setattr(settings, "lane_c_llm_enabled", True)
    monkeypatch.setattr(qrf, "_call_llm", _fake_call_llm)
    result = qrf.compute_qualitative_red_flags(notes)
    assert result.status != "pass"
    assert result.status != "unmeasurable"
    assert result.evidence_basis == "llm-qualitative"
    assert "discrepancy" in result.evidence


@needs_llm
def test_a_real_local_model_returns_the_expected_shape(monkeypatch):
    notes = ("Total Revenue 10,000,000. Inventories 2,000,000. Notes to Accounts: "
             "The company has no unpaid statutory dues outstanding.")
    monkeypatch.setattr(settings, "lane_c_llm_enabled", True)
    result = qrf.compute_qualitative_red_flags(notes)
    assert result.signal_code == "LNC-09"
    assert result.status in ("pass", "low", "medium", "high", "critical")
    assert result.evidence_basis == "llm-qualitative"
