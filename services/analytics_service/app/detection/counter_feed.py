"""Publish the inline lane's counters from context detection has already computed.

The near-real-time engine builds an ``AccountContext`` per account per batch — the
baseline, the rolling counts, the distinct-counterparty count, the last-seen timestamp.
Those are exactly the inputs the inline lane needs, and recomputing them there would be
both wasteful and a second definition of the same numbers.

So the flow is one-way and derived: detection computes context, this module extracts it,
and decision-service stores it. The inline lane then rebuilds an ``AccountContext`` from
those values and calls the *same* ``observe`` function. Neither lane owns a private copy
of what an observation means.

**Publishing is best-effort and never blocks detection.** If decision-service is down,
detection completes normally, the counters go stale, and the inline lane reports the
affected rules as skipped — which is the honest outcome and already handled. Coupling the
batch pipeline's success to the availability of a service in the payment path would be
exactly backwards.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from cp_common import settings
from cp_common.observations import AccountContext

log = logging.getLogger("analytics.counter_feed")

#: How long the publisher waits. Deliberately short: this runs after a batch has been
#: scored, and a slow decision-service must not hold the pipeline open.
TIMEOUT_SECONDS = 5.0

#: Context fields the inline lane needs, and the counter name each is stored under. The
#: names are the AccountContext field names rather than rule ids, because the inline lane
#: rebuilds a context object - not a set of pre-computed observations.
FIELDS = (
    "baseline_mean_paise",
    "inflow_paise",
    "outflow_paise",
    "distinct_counterparties",
    "small_credit_count",
    "sub_ctr_count",
    "near_upi_limit_count",
    "device_cluster_size",
)


def extract(ctx: AccountContext, *, now: datetime | None = None) -> dict[str, float]:
    """One account's context as a flat set of counters.

    ``last_seen`` becomes an age in seconds rather than a timestamp: the store holds
    numbers, and an age is what every dormancy calculation actually wants. The inline
    lane converts it back to a timestamp relative to the payment being decided, which is
    the only reading that is correct at decision time.
    """
    now = now or datetime.now(timezone.utc)
    out = {f: float(getattr(ctx, f, 0) or 0) for f in FIELDS}
    if ctx.last_seen is not None:
        last = ctx.last_seen
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        out["last_seen_age_seconds"] = max(0.0, (now - last).total_seconds())
    return out


def publish(tenant_id: str, contexts: dict[str, AccountContext], *,
            now: datetime | None = None) -> dict:
    """Push counters for a batch of accounts. Never raises into the caller.

    Returns a small report so a detection run can record whether the inline lane was
    fed — silence here would make a stale decision lane hard to attribute later.
    """
    if not contexts:
        return {"published": 0, "skipped": "no accounts"}

    url = getattr(settings, "decision_service_url", "").strip()
    if not url:
        return {"published": 0, "skipped": "no decision_service_url configured"}

    payload = {
        "tenant_id": tenant_id,
        "accounts": [{"account": account, "counters": extract(ctx, now=now)}
                     for account, ctx in contexts.items()],
    }
    try:
        r = httpx.post(
            f"{url}/internal/counters",
            json=payload,
            headers={"X-Internal-Key": settings.internal_api_key},
            timeout=TIMEOUT_SECONDS)
        if r.status_code >= 400:
            log.warning("counter publish rejected (%s): %s", r.status_code, r.text[:200])
            return {"published": 0, "error": f"http {r.status_code}"}
        return {"published": len(payload["accounts"])}
    except Exception as exc:  # noqa: BLE001
        # Deliberately swallowed. A detection batch that has already scored correctly
        # must not be failed because the inline lane could not be updated.
        log.warning("counter publish failed, inline counters will go stale: %s", exc)
        return {"published": 0, "error": str(exc)[:120]}
