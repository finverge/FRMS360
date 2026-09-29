"""Configuration the hot path needs, held in memory and refreshed off the path.

The catalogue and the routing policy both live in the control plane, and both change
when someone edits a config — not per request. Fetching either inside a payment window
would put an HTTP call on the critical path, which is exactly the thing this lane exists
to avoid.

They are therefore cached against the shared generation token, the same mechanism the
analytics tier uses: every replica reads one counter, config-service bumps it on change,
and a replica holding a stale generation discards it. A per-process TTL would let two
replicas decide the same payment differently, which is intolerable when the output is
whether a customer's money moves.

A tenant with no activated ``decision_policy`` config (BR-316) runs the starter policy,
and the fact is logged rather than hidden — an unconfigured tenant running platform
defaults is something an operator must be able to see.
"""
from __future__ import annotations

import logging
import threading

import httpx

from cp_common import cache as cache_mod, settings

from . import policy as policy_mod

log = logging.getLogger("decision.store")

#: Populated at start-up and on invalidation. Never read through to the network on the
#: decision path.
_POLICIES: dict[str, policy_mod.DecisionPolicy] = {}
_CATALOGUES: dict[str, list] = {}

_WARNED: set[str] = set()

#: Generation each cached catalogue was loaded at, so staleness is detectable without
#: a fetch.
_GENERATIONS: dict[str, int] = {}
_REFRESHING: set[str] = set()
_REFRESH_LOCK = threading.Lock()

#: Short enough that a stuck config-service does not hold start-up, long enough for a
#: cold query. Never on the decision path - see catalogue_for.
FETCH_TIMEOUT_SECONDS = 5.0

_CACHE = None


def _catalogue_cache():
    """The shared-generation cache, built lazily so importing this module needs no DB."""
    global _CACHE
    if _CACHE is None:
        try:
            backend = cache_mod.build_backend(settings.cache_backend)
            # "rules" is the namespace config-service bumps when a threshold changes.
            # Getting this wrong would cache the catalogue forever and a retuned
            # threshold would never reach the payment path.
            _CACHE = cache_mod.VersionedCache(backend, "rules")
        except Exception as exc:  # noqa: BLE001
            log.warning("no cache backend for the rule catalogue (%s); falling back to "
                        "whatever was set directly", str(exc)[:120])
            return None
    return _CACHE


#: The tenant's decision_policy fetched from config-service, cached against the shared
#: "policy" generation - the same namespace config-service bumps on every activation
#: (config_service's _publish_config_change bumps "rules" and "policy" together, kind-
#: agnostic, so a decision_policy activation invalidates this exactly as a rule or FRM
#: policy change already does). ``None`` means "checked, nothing active" - distinct from
#: "not yet checked", which is simply absent from this dict.
_STORED_POLICIES: dict[str, policy_mod.DecisionPolicy | None] = {}
_POLICY_GENERATIONS: dict[str, int] = {}
_POLICY_REFRESHING: set[str] = set()
_POLICY_REFRESH_LOCK = threading.Lock()
_POLICY_CACHE = None


def _policy_gen_cache():
    global _POLICY_CACHE
    if _POLICY_CACHE is None:
        try:
            backend = cache_mod.build_backend(settings.cache_backend)
            _POLICY_CACHE = cache_mod.VersionedCache(backend, "policy")
        except Exception as exc:  # noqa: BLE001
            log.warning("no cache backend for the decision policy (%s); falling back "
                        "to whatever was set directly", str(exc)[:120])
            return None
    return _POLICY_CACHE


def _fetch_decision_policy(tenant_id: str) -> policy_mod.DecisionPolicy | None:
    """The tenant's active decision_policy config, from config-service. ``None`` when
    none is active - not an error, the starter policy is the correct fallback then."""
    r = httpx.get(f"{settings.config_service_url}/internal/decision-policy/{tenant_id}",
                  headers={"x-internal-key": settings.internal_api_key},
                  timeout=FETCH_TIMEOUT_SECONDS)
    r.raise_for_status()
    body = r.json()
    if not body.get("available"):
        return None
    return policy_mod.parse(tenant_id, body["body"])


def refresh_policy(tenant_id: str) -> policy_mod.DecisionPolicy | None:
    """Load this tenant's stored decision policy now. Never called from the decision
    path - same discipline as ``refresh`` below, and for the same reason: what a rail
    enforces matters at least as much as which rules run, so it earns the same
    never-block-the-payment-window guarantee, not the lighter fetch-on-miss pattern
    ``frm_policy_for`` uses for severity bands alone."""
    cache = _policy_gen_cache()
    generation = cache.current_generation() if cache else 0
    try:
        pol = _fetch_decision_policy(tenant_id)
    except Exception as exc:  # noqa: BLE001
        log.error("could not load the decision policy for tenant %s: %s. The lane "
                  "keeps using the copy it holds.", tenant_id, str(exc)[:200])
        return _STORED_POLICIES.get(tenant_id)
    _STORED_POLICIES[tenant_id] = pol
    _POLICY_GENERATIONS[tenant_id] = generation
    return pol


def _schedule_policy_refresh(tenant_id: str) -> None:
    """Reload off the payment path, one refresh per tenant at a time."""
    with _POLICY_REFRESH_LOCK:
        if tenant_id in _POLICY_REFRESHING:
            return
        _POLICY_REFRESHING.add(tenant_id)

    def run():
        try:
            refresh_policy(tenant_id)
        finally:
            with _POLICY_REFRESH_LOCK:
                _POLICY_REFRESHING.discard(tenant_id)

    threading.Thread(target=run, name=f"policy-refresh-{tenant_id[:8]}",
                     daemon=True).start()


def policy_for(tenant_id: str) -> policy_mod.DecisionPolicy:
    """Never fetches on this path - same invariant as ``catalogue_for``. Returns what
    is held (an explicit override, then a stored policy, then the starter policy) and
    schedules a reload in the background when the shared generation has moved."""
    overridden = _POLICIES.get(tenant_id)
    if overridden is not None:
        return overridden

    cache = _policy_gen_cache()
    if cache is not None and (
            cache.current_generation() != _POLICY_GENERATIONS.get(tenant_id)
            or tenant_id not in _STORED_POLICIES):
        _schedule_policy_refresh(tenant_id)

    stored = _STORED_POLICIES.get(tenant_id)
    if stored is not None:
        return stored

    if tenant_id not in _WARNED:
        log.warning(
            "tenant %s has no active decision policy; using the starter policy "
            "(every rail in shadow mode, inline rails failing open). This is a "
            "configuration gap, not a decision.", tenant_id)
        _WARNED.add(tenant_id)
    return policy_mod.parse(tenant_id, policy_mod.STARTER)


def set_policy(tenant_id: str, body: dict) -> policy_mod.DecisionPolicy:
    """Install a policy directly, bypassing config-service. Refuses anything unsafe
    rather than degrading. For tests and break-glass operator use - see policy_for's
    docstring: an explicit override here always wins over whatever is stored."""
    p = policy_mod.parse(tenant_id, body)
    _POLICIES[tenant_id] = p
    _WARNED.discard(tenant_id)
    return p


def clear_override(tenant_id: str) -> None:
    """Undo set_policy, so the tenant's actual stored (or starter) policy governs
    again."""
    _POLICIES.pop(tenant_id, None)


_FRM_CACHE = None


def _frm_cache():
    global _FRM_CACHE
    if _FRM_CACHE is None:
        try:
            backend = cache_mod.build_backend(settings.cache_backend)
            # "policy" is the other namespace config-service bumps.
            _FRM_CACHE = cache_mod.VersionedCache(backend, "policy")
        except Exception:  # noqa: BLE001
            return None
    return _FRM_CACHE


def frm_policy_for(tenant_id: str) -> dict:
    """The tenant's FRM policy — needed here only for the severity bands.

    Lane A must band a score exactly as the near-real-time lane does, or the same account
    would be 'critical' on a dashboard and 'high' at the point of payment. An unreachable
    config-service yields ``{}``, and cp_common.scoring then applies the documented
    platform defaults rather than inventing a band.
    """
    cache = _frm_cache()
    if cache is None:
        return {}
    cached, generation = cache.get(tenant_id)
    if cached is not None:
        return cached
    try:
        r = httpx.get(f"{settings.config_service_url}/internal/policy/{tenant_id}",
                      headers={"x-internal-key": settings.internal_api_key},
                      timeout=FETCH_TIMEOUT_SECONDS)
        r.raise_for_status()
        body = r.json() or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("could not load the FRM policy for tenant %s: %s. Severity bands fall "
                    "back to platform defaults.", tenant_id, str(exc)[:160])
        body = {}
    cache.put(tenant_id, body, generation)
    return body


def _fetch_catalogue(tenant_id: str) -> list:
    """The tenant's ACTIVE rules, from config-service — the single source of thresholds.

    Off the payment path by construction: called on a cache miss, and a miss only happens
    when config-service has bumped the generation because someone changed a threshold.
    """
    r = httpx.get(f"{settings.config_service_url}/internal/rules/{tenant_id}",
                  headers={"x-internal-key": settings.internal_api_key},
                  timeout=FETCH_TIMEOUT_SECONDS)
    r.raise_for_status()
    rules = list((r.json().get("rules") or {}).values())
    # Qualitative rules carry no measurable observation at all; they are entered by credit
    # monitoring. Dropping them here keeps the deferred list about *timing* rather than
    # mixing in rules that no lane computes.
    return [x for x in rules if not x.get("qualitative")]


def refresh(tenant_id: str) -> list:
    """Load this tenant's catalogue now. Never called from the decision path."""
    cache = _catalogue_cache()
    generation = cache.current_generation() if cache else 0
    try:
        rules = _fetch_catalogue(tenant_id)
        if not rules:
            log.error("tenant %s has no active rules; the inline lane will screen nothing "
                      "and every decision is recorded as not_screened", tenant_id)
    except Exception as exc:  # noqa: BLE001
        log.error("could not load the rule catalogue for tenant %s: %s. The lane keeps "
                  "using the copy it holds, and says so on every decision it makes.",
                  tenant_id, str(exc)[:200])
        return _CATALOGUES.get(tenant_id, [])
    if cache:
        cache.put(tenant_id, rules, generation)
    _CATALOGUES[tenant_id] = rules
    _GENERATIONS[tenant_id] = generation
    return rules


def _schedule_refresh(tenant_id: str) -> None:
    """Reload off the payment path, one refresh per tenant at a time."""
    with _REFRESH_LOCK:
        if tenant_id in _REFRESHING:
            return
        _REFRESHING.add(tenant_id)

    def run():
        try:
            refresh(tenant_id)
        finally:
            with _REFRESH_LOCK:
                _REFRESHING.discard(tenant_id)

    threading.Thread(target=run, name=f"catalogue-refresh-{tenant_id[:8]}",
                     daemon=True).start()


def catalogue_for(tenant_id: str) -> list:
    """The tenant's active rule catalogue, in the shape the evaluator reads.

    **Never fetches on this path.** An HTTP call inside a payment window is the one thing
    this lane exists to avoid, and an earlier version did exactly that on a cache miss —
    the first decision after start-up took 542 ms against an 80 ms budget. So this returns
    what is held and schedules a reload in the background.

    The consequence is stated rather than hidden. Until the first load completes the
    catalogue is empty, which produces ``not_screened`` and lets the rail's fail policy
    decide — loudly. After a threshold change, decisions for a moment longer are screened
    against the previous generation; the generation is recorded on the decision so a stale
    run can be identified afterwards rather than assumed away.
    """
    cache = _catalogue_cache()
    if cache is None:                      # no backend configured (unit tests)
        return _CATALOGUES.get(tenant_id, [])
    if cache.current_generation() != _GENERATIONS.get(tenant_id)             or tenant_id not in _CATALOGUES:
        _schedule_refresh(tenant_id)
    return _CATALOGUES.get(tenant_id, [])


def preload(tenant_ids) -> dict:
    """Warm the catalogue for known tenants at start-up, before any payment arrives."""
    loaded = {}
    for tenant_id in tenant_ids:
        loaded[tenant_id] = len(refresh(tenant_id))
    return loaded


def set_catalogue(tenant_id: str, rules: list) -> None:
    _CATALOGUES[tenant_id] = rules


def invalidate(tenant_id: str = "") -> dict:
    if tenant_id:
        _POLICIES.pop(tenant_id, None)
        _CATALOGUES.pop(tenant_id, None)
        _STORED_POLICIES.pop(tenant_id, None)
        _POLICY_GENERATIONS.pop(tenant_id, None)
        return {"tenant": tenant_id}
    n = len(_POLICIES) + len(_CATALOGUES) + len(_STORED_POLICIES)
    _POLICIES.clear()
    _CATALOGUES.clear()
    _STORED_POLICIES.clear()
    _POLICY_GENERATIONS.clear()
    return {"cleared": n}


def status() -> dict:
    """Exposed on /health so an operator can see what the lane is actually running."""
    return {
        "tenants_with_policy_override": sorted(_POLICIES),
        "tenants_with_stored_policy": sorted(t for t, p in _STORED_POLICIES.items()
                                             if p is not None),
        "tenants_on_starter_policy": sorted(_WARNED),
        "tenants_with_catalogue": sorted(_CATALOGUES),
    }
