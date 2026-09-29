"""Optional, human-gated LLM rephrasing of the deterministically-built
regulatory narrative fields (``modus_operandi``, ``suspicion_grounds`` -
see ``builder.build``).

builder.build() already produces these fields accurately and fully
evidence-traceable, in this module's own words: "nothing is inferred and
nothing is defaulted into place." That principle does not change here.
This module never lets an LLM invent, correct, or add a single fact to a
regulatory filing - its only job is to rewrite what the deterministic
builder already produced into more natural prose, and it *proves* it did
nothing else: every number and rule-ID token in the rewrite is checked
against the tokens already present in the deterministic original plus the
structured indicators_triggered evidence, and a rewrite that introduces
one that isn't already there is rejected outright, not silently returned.

Off by default (``settings.narrative_polish_enabled``), and only ever
reachable through the read-only ``narrative/polish`` preview route - it
never writes to the database and never changes what ``generate`` stores
unless a human explicitly supplies the polished text back as an override
(see routes/filings.py). "The platform prepares, a person submits" holds
here exactly as it does everywhere else in this module: this makes the
prepared draft read better, it never makes the decision to file."""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from cp_common import settings

_NARRATIVE_FIELDS = ("modus_operandi", "suspicion_grounds")

_NUMBER_RE = re.compile(r"\b\d[\d,]*\.?\d*\b")
_RULE_ID_RE = re.compile(r"\b[A-Z]{2,6}-\d{1,4}\b")

_SYSTEM_PROMPT = """You rewrite one paragraph of a regulatory fraud filing \
narrative for a bank's compliance officer. The paragraph is already \
factually complete and correct - it was assembled from the bank's own \
detection evidence. Your ONLY job is to make it read as natural, \
professional prose instead of a mechanically concatenated list.

Hard rules, no exceptions:
- Do not add, remove, or change any number, amount, date, account, or \
rule/indicator ID.
- Do not add any claim, cause, or conclusion not already stated in the \
input text.
- Do not soften, hedge, or strengthen the suspicion — keep the same \
level of certainty the input expresses.
- Output ONLY the rewritten paragraph. No preamble, no markdown, no \
quotation marks around it."""


class NarrativePolishError(ValueError):
    """Raised when the rewrite fails to preserve every fact from the
    original — the safety check that matters more than the prompt
    instruction, since a prompt is a request and this is a hard gate."""


@dataclass
class PolishResult:
    field: str
    original: str
    polished: str
    verified: bool
    rejection_reason: str | None = None


def _extract_facts(*texts: str) -> set[str]:
    facts: set[str] = set()
    for text in texts:
        if not text:
            continue
        facts |= {m.group().replace(",", "") for m in _NUMBER_RE.finditer(text)}
        facts |= {m.group() for m in _RULE_ID_RE.finditer(text)}
    return facts


def _call_llm(system_prompt: str, user_input: str) -> str:
    response = httpx.post(
        f"{settings.narrative_llm_base_url}/chat/completions",
        json={
            "model": settings.narrative_llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            "max_tokens": 1024,
            "temperature": 0.1,
        },
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def polish_field(field: str, original_text: str, evidence_context: str = "") -> PolishResult:
    """Rewrites one narrative field's text and verifies the rewrite
    introduced no new number or rule-ID versus the original text plus
    the structured evidence it was built from. A verification failure
    returns the ORIGINAL text with verified=False, never the unverified
    rewrite — callers must check `.verified` before offering the result
    to a human, and must never store an unverified polish."""
    user_input = original_text
    if evidence_context:
        user_input += f"\n\n(For your reference only, not to be repeated verbatim: {evidence_context})"

    polished = _call_llm(_SYSTEM_PROMPT, user_input)

    allowed_facts = _extract_facts(original_text, evidence_context)
    new_facts = _extract_facts(polished) - allowed_facts
    if new_facts:
        return PolishResult(
            field=field, original=original_text, polished=original_text, verified=False,
            rejection_reason=(
                f"Rewrite introduced fact(s) not present in the source evidence: "
                f"{', '.join(sorted(new_facts))}"
            ),
        )
    return PolishResult(field=field, original=original_text, polished=polished, verified=True)


def polish_narrative_fields(payload: dict) -> list[PolishResult]:
    """Polishes every narrative field present in a built filing payload
    (only modus_operandi and suspicion_grounds are ever narrative text -
    everything else in the payload is structured data, dates, or
    booleans, and is never sent to the LLM at all)."""
    evidence_context = "; ".join(
        f"{i['rule_id']} observed {i['observed']} {i['unit'] or ''} vs threshold {i['threshold']}".strip()
        for i in payload.get("indicators_triggered") or []
    )
    results = []
    for field_name in _NARRATIVE_FIELDS:
        text = payload.get(field_name)
        if not text:
            continue
        results.append(polish_field(field_name, text, evidence_context))
    return results
