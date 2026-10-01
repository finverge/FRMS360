"""LNC-09 — a local-LLM read of a statement's extracted text for qualitative red flags
RBI 2016 lists but no ratio or regex can catch: material discrepancies within the annual
report (#38) and poor disclosure of adverse information with no auditor qualification
(#39). Both are narrative judgments, not numbers - the two other text-driven signals in
this catalogue (LNC-01, LNC-08) work because they're looking for one specific, narrow
phrasing; #38/#39 are "does this paragraph read as evasive or inconsistent", which no
fixed regex can express.

Off by default (``settings.lane_c_llm_enabled``) - see cp_common/settings.py. When off,
this always reports unmeasurable, never clean; a bank that hasn't turned this on gets
the same honest gap Lane C already gives LNC-02 for a missing project-appraisal feed.

**The one hard rule, mirrored from filings/narrative_polish.py**: this signal never
trusts the model's word alone. Every red flag it reports must quote the exact source
sentence it is based on, and that quote is verified as an actual substring of the
statement text before the finding is kept - a "finding" whose quote doesn't verify is
dropped silently from the result, not scored, not surfaced. A model that hallucinates a
red flag either says so honestly (nothing to quote) or gets caught by this check; it
never gets to invent a fact and have Lane C repeat it as evidence.

**Verification is whitespace-normalised, not byte-exact.** A real PDF's extracted text
carries the source document's own line-wrap points as literal newlines mid-sentence
(pdfplumber preserves visual line breaks) - confirmed directly against a real filing
during this feature's own verification, where a model's faithful, correctly-quoted
finding was silently and wrongly dropped because its quote used a plain space where the
extracted text had a line-wrap newline. Collapsing runs of whitespace before comparing
closes that gap without weakening the actual safety property: the check still fails a
quote whose *words* were invented, only not one that differs by incidental line-wrapping.
"""
from __future__ import annotations

import json
import re

import httpx

from cp_common import settings

from .signal_engine import SignalResult, _unmeasurable, band

_SYSTEM_PROMPT = """You are reviewing the extracted text of a borrower's financial \
statement filing for a bank's credit-monitoring team. You are looking for exactly two \
things RBI's early-warning-signal guidance names:

1. Material discrepancies or internal inconsistencies within the statement itself \
(e.g. figures that don't reconcile, a stated total that contradicts its own line \
items, contradictory dates or descriptions).
2. Poor disclosure of adverse information, or an auditor's report that omits a \
qualification a reasonable reader would expect given what else is disclosed.

Base your answer ONLY on the text given - never infer, assume, or add information not \
present in it. If you find nothing, say so; do not manufacture a finding to have \
something to report.

Respond with ONLY a JSON object of this exact shape, no prose outside it:
{"findings": [{"category": "discrepancy" or "poor_disclosure", \
"quote": string (the exact sentence from the source text this finding is based on, \
verbatim, unmodified), "reason": string (1-2 sentences explaining why this is a red \
flag)}], "confidence": integer 0-100 (how much the given text actually supports these \
findings - low if the text is thin or ambiguous)}"""

_REQUIRED_KEYS = {"findings", "confidence"}
_VALID_CATEGORIES = {"discrepancy", "poor_disclosure"}


def _normalise_ws(s: str) -> str:
    """Collapse any run of whitespace (including a PDF's own mid-sentence line-wrap
    newlines) to a single space, so quote verification compares content, not incidental
    line breaks neither the model nor a human would consider a different sentence."""
    return re.sub(r"\s+", " ", s).strip()


class LLMUnavailableError(RuntimeError):
    """The local model host could not be reached at all."""


class LLMResponseError(RuntimeError):
    """The host answered, but not with the shape this signal requires."""


def _call_llm(notes_text: str) -> dict:
    try:
        response = httpx.post(
            f"{settings.lane_c_llm_base_url}/chat/completions",
            json={
                "model": settings.lane_c_llm_model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": notes_text},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
            },
            timeout=settings.lane_c_llm_timeout_s,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMUnavailableError(
            f"Could not reach the local LLM at {settings.lane_c_llm_base_url} "
            f"(model {settings.lane_c_llm_model}): {exc}") from exc

    try:
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMResponseError(f"Model response was not valid JSON: {exc}") from exc

    if not _REQUIRED_KEYS.issubset(parsed):
        raise LLMResponseError(f"Model response missing required fields: {parsed}")
    return parsed


def compute_qualitative_red_flags(notes_text: str) -> SignalResult:
    code = "LNC-09"
    if not settings.lane_c_llm_enabled:
        return _unmeasurable(code, "LLM qualitative read is not enabled for this deployment")
    if not notes_text:
        return _unmeasurable(code, "no statement text available")

    try:
        parsed = _call_llm(notes_text)
    except (LLMUnavailableError, LLMResponseError) as exc:
        return _unmeasurable(code, f"LLM read failed: {exc}")

    raw_findings = parsed.get("findings") or []
    verified = []
    for f in raw_findings:
        if not isinstance(f, dict):
            continue
        category = f.get("category")
        quote = f.get("quote", "")
        if category not in _VALID_CATEGORIES or not quote:
            continue
        # The one check that matters more than the prompt: a finding whose quoted
        # sentence isn't actually in the source text is dropped, not trusted -
        # mirrors narrative_polish.py's "verify against the source, don't take the
        # model's word for it" discipline. Whitespace-normalised on both sides - see
        # module docstring for the real PDF line-wrap case this was found against.
        if _normalise_ws(quote) not in _normalise_ws(notes_text):
            continue
        verified.append({
            "category": category, "quote": quote,
            "reason": str(f.get("reason", ""))[:300],
        })

    if not verified:
        return SignalResult(code, 0.0, None, "pass", 0,
                            "No qualitative red flags found in the statement text.",
                            evidence_basis="llm-qualitative")

    has_discrepancy = any(f["category"] == "discrepancy" for f in verified)
    has_poor_disclosure = any(f["category"] == "poor_disclosure" for f in verified)
    severity = (30 if has_discrepancy else 0) + (25 if has_poor_disclosure else 0)
    severity += 5 * max(0, len(verified) - 1)  # multiple independent findings compound

    status = band(severity)
    summary = "; ".join(f"{f['category']}: {f['reason']}" for f in verified)[:600]
    return SignalResult(code, float(len(verified)), None, status, severity, summary,
                        evidence_basis="llm-qualitative")
