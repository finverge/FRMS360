"""Bridge from the control plane's rule catalogue into analytics.

Detection quality cannot be judged from firings alone. An indicator that is *configured
but never fires* is invisible in every alert-based chart, yet it is exactly what an RBI
inspection looks for: an EWS framework where indicators exist on paper and never produce
a signal. Answering that needs the configured set, which lives in the control plane.

The catalogue is cached - it changes when someone edits a config, not per request, and the
dashboards would otherwise call config-service several times per page load. The cache is
keyed by a **shared generation token**, not by wall-clock TTL: with several replicas
behind a load balancer, per-process TTLs would let two replicas serve two different
generations of the same tenant's configuration, and the answer a user got would depend on
which replica the balancer picked. See cp_common.cache.
"""
import logging

import httpx

from cp_common import settings
from cp_common.cache import VersionedCache, build_backend, verify_backend_supports

log = logging.getLogger("analytics.rules")

_BACKEND = build_backend(settings.cache_backend)
verify_backend_supports(_BACKEND, settings.app_replicas)

_TTL = settings.cache_generation_ttl_seconds
_RULES_CACHE = VersionedCache(_BACKEND, "rules", _TTL)
_POLICY_CACHE_V = VersionedCache(_BACKEND, "policy", _TTL)
_MODEL_CACHE_V = VersionedCache(_BACKEND, "model", _TTL)


def invalidate(tenant_id: str = "") -> dict[str, int]:
    """Drop cached configuration on every replica at once."""
    return {"rules": _RULES_CACHE.invalidate(), "policy": _POLICY_CACHE_V.invalidate(),
            "model": _MODEL_CACHE_V.invalidate()}


def cache_status() -> dict:
    """Exposed on /health so a misconfigured deployment is visible, not guessed at."""
    return {
        "backend": _BACKEND.name,
        "shared_across_replicas": _BACKEND.shared_across_replicas,
        "replicas": settings.app_replicas,
        "rules_generation": _RULES_CACHE.current_generation(),
        "policy_generation": _POLICY_CACHE_V.current_generation(),
        "model_generation": _MODEL_CACHE_V.current_generation(),
    }

# Used only when the control plane cannot be reached. Keeping dashboards up on documented
# defaults beats failing every query, but these must never silently masquerade as the
# tenant's own board-approved policy - callers get ``available: False``.
_FALLBACK_POLICY = {
    "natural_justice_days": 21, "show_cause_within_days": 7,
    "fmr_filing_days": 7, "str_filing_days": 7,
    "staff_accountability_days": 180,
    # Rs 1 crore, Rs 1 crore, Rs 10 lakh - in paise. These were an order of magnitude
    # below the authoritative defaults, so a config-service outage silently counted ten
    # times as many cases as breaching the LEA referral and board reporting floors.
    # test_policy_placeholders now fails the build if the two ever disagree again.
    "lea_referral_paise": 1_000_000_000, "board_reporting_paise": 1_000_000_000,
    "material_fraud_paise": 100_000_000,
    "severity_critical_score": 380, "severity_high_score": 260,
    "severity_medium_score": 150,
    "sla_breach_hours": 24, "stalled_case_days": 30,
    "ews_examination_days": 30,
}


def policy(tenant_id: str, *, force: bool = False) -> dict:
    """The tenant's board-approved FRM policy thresholds.

    A Tier-1 co-operative bank and a large commercial bank are governed by different
    Master Directions, so a hard-coded 24-hour SLA or 21-day response window cannot be
    correct for both.
    """
    if not force:
        hit, gen = _POLICY_CACHE_V.get(tenant_id)
        if hit is not None:
            return hit
    else:
        gen = _POLICY_CACHE_V.current_generation()
    try:
        r = httpx.get(
            f"{settings.config_service_url}/internal/policy/{tenant_id}",
            headers={"x-internal-key": settings.internal_api_key}, timeout=5.0)
        r.raise_for_status()
        j = r.json()
        body = j.get("body", {}) or {}
        out = {
            "available": bool(j.get("available")),
            "entity_type": body.get("entity_type"),
            "entity_label": body.get("entity_label"),
            "governing_direction": body.get("governing_direction"),
            "principal_officer_name": body.get("principal_officer_name"),
            "reporting_to": body.get("reporting_to", []),
            "ucb_tier": body.get("ucb_tier"),
            "values": {**_FALLBACK_POLICY, **(body.get("thresholds") or {})},
            # BR-104: whose thresholds these are. False means the platform's seeded
            # defaults are governing a bank's fraud reporting, which is a finding.
            "board_approved": bool(j.get("board_approved")),
            "attestation": j.get("attestation"),
            "why_not_approved": j.get("why_not_approved", ""),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("FRM policy unavailable for %s: %s", tenant_id, exc)
        out = {"available": False, "entity_type": None, "entity_label": None,
               "governing_direction": None, "principal_officer_name": None,
               "reporting_to": [], "ucb_tier": None,
               "values": dict(_FALLBACK_POLICY), "board_approved": False,
               "attestation": None,
               "why_not_approved": "control plane unreachable; platform defaults in use"}
    _POLICY_CACHE_V.put(tenant_id, out, gen)
    return out


def policy_values(tenant_id: str) -> dict:
    """Flat numeric knobs, for substitution into metric SQL."""
    return policy(tenant_id)["values"]


def active_rules(tenant_id: str, *, force: bool = False) -> dict[str, dict]:
    """The tenant's active rule catalogue, keyed by rule id.

    Returns ``{}`` if config-service is unreachable. That degrades the dormant register
    to "unknown" rather than reporting every rule as dormant, which would be a false
    compliance alarm.
    """
    if not force:
        hit, gen = _RULES_CACHE.get(tenant_id)
        if hit is not None:
            return hit
    else:
        gen = _RULES_CACHE.current_generation()
    try:
        r = httpx.get(
            f"{settings.config_service_url}/internal/rules/{tenant_id}",
            headers={"x-internal-key": settings.internal_api_key}, timeout=5.0)
        r.raise_for_status()
        rules = r.json().get("rules", {})
    except Exception as exc:  # noqa: BLE001
        log.warning("rule catalogue unavailable for %s: %s", tenant_id, exc)
        return {}
    _RULES_CACHE.put(tenant_id, rules, gen)
    return rules


def active_model(tenant_id: str, name: str, *, force: bool = False) -> dict:
    """The tenant's active ``model`` config version *for this named model* (AI/ML
    roadmap Phase 2/3, HLD AD-14/AD-15).

    ``name`` is required, not optional with a default - Phase 2 shipped with exactly
    one model in existence (``velocity-anomaly``, backing VEL-04) and this endpoint
    originally returned "the tenant's active model" full stop, which was only ever
    correct because there was nothing to disambiguate. Phase 3 activates a second,
    differently-named model (``graph-ring-score``, backing LAY-05) alongside it - the
    config data model already supports two same-kind, differently-named versions being
    simultaneously active (``ConfigRepository.activate()`` archives a sibling by
    tenant+kind+name, not tenant+kind alone), so this was a query-layer gap, not a data
    model one. See ``features.NEEDS_MODEL`` for the rule-id -> model-name mapping every
    caller should read ``name`` from, rather than hard-coding it a second time.

    ``available: False`` is not an error - it is every tenant before this particular
    named model's first version is activated, or config-service being unreachable, and
    both must report the same "cannot score" outcome to the caller. The indicator this
    name backs is then correctly unmeasurable rather than scored on a fallback the
    tenant never approved - there is no safe fallback model the way there is a safe
    fallback policy.
    """
    key = f"{tenant_id}::{name}"
    if not force:
        hit, gen = _MODEL_CACHE_V.get(key)
        if hit is not None:
            return hit
    else:
        gen = _MODEL_CACHE_V.current_generation()
    try:
        r = httpx.get(
            f"{settings.config_service_url}/internal/model/{tenant_id}/{name}",
            headers={"x-internal-key": settings.internal_api_key}, timeout=5.0)
        r.raise_for_status()
        out = r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("model config '%s' unavailable for %s: %s", name, tenant_id, exc)
        return {"tenant_id": tenant_id, "name": name, "available": False}
    _MODEL_CACHE_V.put(key, out, gen)
    return out


def _blocked_by(rule_id: str, reference_status: dict | None) -> str:
    """The unloaded list stopping this rule, or "" if nothing is."""
    if not reference_status:
        return ""
    from .detection.features import NEEDS_REFERENCE_DATA

    kind = NEEDS_REFERENCE_DATA.get(rule_id)
    if not kind:
        return ""
    st = reference_status.get(kind)
    if st is None or getattr(st, "loaded", False):
        return ""
    return getattr(st, "why_unavailable", f"No '{kind}' reference list loaded.")


def dormant_report(tenant_id: str, fired_rule_ids: set[str],
                   reference_status: dict | None = None) -> dict:
    """Configured rules that produced nothing in the current filter window.

    Qualitative indicators are reported separately: they are fed from CBS events and
    relationship-manager input, not the payment stream, so silence there is expected and
    lumping them in would cry wolf.

    ``reference_status`` lets a dormant rule say *why* it is silent. "CPT-02 never fired"
    and "CPT-02 cannot fire because no sanctions list has been loaded" are different
    findings, and only the second one tells an operator what to do about it.
    """
    catalogue = active_rules(tenant_id)
    if not catalogue:
        return {"available": False, "configured": 0, "fired": len(fired_rule_ids),
                "dormant": [], "dormant_qualitative": [], "coverage": None}

    quantitative = {rid: r for rid, r in catalogue.items() if not r.get("qualitative")}
    dormant = sorted(rid for rid in quantitative if rid not in fired_rule_ids)
    dormant_qual = sorted(rid for rid, r in catalogue.items()
                          if r.get("qualitative") and rid not in fired_rule_ids)
    covered = len(quantitative) - len(dormant)
    return {
        "available": True,
        "configured": len(catalogue),
        "configured_quantitative": len(quantitative),
        # Counted over the same population as the denominator it is shown against -
        # mixing the two produced "28 of 25 fired".
        "fired": len(fired_rule_ids & set(catalogue)),
        "fired_quantitative": covered,
        "dormant": [
            {"rule_id": rid, "family": quantitative[rid]["family"],
             "reason": quantitative[rid]["reason"],
             "threshold": quantitative[rid]["threshold"],
             "unit": quantitative[rid]["observed_unit"],
             "blocked_by": _blocked_by(rid, reference_status)}
            for rid in dormant
        ],
        "blocked_count": sum(1 for rid in dormant
                             if _blocked_by(rid, reference_status)),
        "dormant_qualitative": dormant_qual,
        "coverage": round(covered / len(quantitative), 4) if quantitative else None,
        # A rule firing that is not in the catalogue means detection and configuration
        # have drifted apart - the failure this bridge exists to make visible.
        "uncatalogued": sorted(fired_rule_ids - set(catalogue)),
    }


def approved_config_versions(tenant_id: str) -> set[str]:
    """Versions the control plane currently has ACTIVE.

    A config version scoring live traffic that is not in this set is an unapproved model
    in production - the governance gap FREE-AI is concerned with.
    """
    catalogue = active_rules(tenant_id)
    versions = {r.get("version") for r in catalogue.values() if r.get("version")}
    # A tenant with no catalogue reachable should not have every version flagged as
    # unapproved; that would be a false alarm, not a finding.
    return versions
