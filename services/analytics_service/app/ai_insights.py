"""AI Insights: an LLM's read of one alert/case/transaction's evidence.

Calls a **locally self-hosted** model over the OpenAI-compatible chat-completions API -
Ollama by default, but the same request shape is what vLLM's own OpenAI-compatible
server speaks, so pointing LLM_BASE_URL at a vLLM deployment later is a config change,
not a rewrite (same reasoning as this service's other swap-the-backend-not-the-caller
boundaries). Deliberately not a paid hosted API: no OpenAI/Anthropic key is configured
or called from here.

The model only ever sees the same evidence payload the caller is already entitled to
(masked/unmasked exactly as routes/dashboards.py's evidence() would return it) and is
instructed not to invent facts beyond it. If the model is unreachable or returns
something that doesn't parse as the expected shape, this raises rather than fabricating
a plausible-looking result - "AI Insights are unavailable" is the honest failure mode.
"""
import json
import os
from datetime import datetime, timezone
from typing import Any

import requests

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5-coder:7b")
#: A small local model can take 30s+ just to swap into memory on a cold start or under
#: contention from another request (observed directly on this host) before it even
#: begins generating - 60s was tight enough to time out on that alone, never mind the
#: generation itself. 180s gives real headroom without masking a genuinely dead host,
#: which still fails - just later.
LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "180"))

_SYSTEM_PROMPT = (
    "You are a fraud/AML analyst's assistant reviewing one flagged record inside a "
    "bank's fraud-monitoring console. You will be given the record's real fields as "
    "JSON. Base your assessment ONLY on those fields - never invent account numbers, "
    "names, amounts, or events not present in the data. If the data is sparse, say so "
    "in the narrative rather than filling gaps with assumptions.\n\n"
    "Respond with ONLY a JSON object of this exact shape, no prose outside it:\n"
    '{"narrative": string (2-4 sentences explaining the risk picture), '
    '"decision": one of "ALLOW", "HOLD", "BLOCK", '
    '"risk_score": integer 0-100, '
    '"confidence": integer 0-100 (how much the given data actually supports the '
    'decision - low if the data is thin), '
    '"recommendations": array of 2-4 short, concrete next-step strings}'
)

_REQUIRED_KEYS = {"narrative", "decision", "risk_score", "confidence", "recommendations"}
_VALID_DECISIONS = {"ALLOW", "HOLD", "BLOCK"}


class LLMUnavailableError(RuntimeError):
    """The local model host could not be reached or returned no usable response."""


class LLMResponseError(RuntimeError):
    """The model responded, but not with the JSON shape this feature requires."""


def _clamp(v: Any, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return lo


def generate(evidence_payload: dict, entity: str, ident: str) -> dict:
    user_content = json.dumps({"entity": entity, "id": ident, "record": evidence_payload}, default=str)

    try:
        resp = requests.post(
            f"{LLM_BASE_URL}/chat/completions",
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            },
            timeout=LLM_TIMEOUT_S,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise LLMUnavailableError(
            f"Could not reach the local LLM at {LLM_BASE_URL} (model {LLM_MODEL}): {exc}"
        ) from exc

    body = resp.json()
    try:
        content = body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMResponseError(f"Model response was not valid JSON: {exc}") from exc

    if not _REQUIRED_KEYS.issubset(parsed):
        raise LLMResponseError(f"Model response missing required fields: {parsed}")

    decision = str(parsed["decision"]).upper()
    if decision not in _VALID_DECISIONS:
        decision = "HOLD"
    recommendations = parsed["recommendations"]
    if not isinstance(recommendations, list):
        recommendations = [str(recommendations)]

    return {
        "narrative": str(parsed["narrative"]).strip(),
        "decision": decision,
        "risk_score": _clamp(parsed["risk_score"], 0, 100),
        "confidence": _clamp(parsed["confidence"], 0, 100),
        "recommendations": [str(r) for r in recommendations][:6],
        "model": LLM_MODEL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
