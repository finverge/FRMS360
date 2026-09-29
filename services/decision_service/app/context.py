"""Rebuild an AccountContext from stored counters.

This is the join between the two lanes. The near-real-time engine builds context with
SQL; here the same object is reconstructed from precomputed counters and handed to the
*same* ``observe`` function. Neither lane holds its own idea of what an indicator
measures, so they cannot drift apart — which matters because they see different traffic
and a drift would otherwise be invisible.

What cannot be reconstructed is stated rather than faked. ``first_seen_pair`` and
``known_devices`` are per-counterparty and per-device sets; storing them as account
counters would mean a row per pair, which is a different data structure and a later
increment. Until then the indicators that need them are reported unmeasured — never
scored zero, which would read as "this beneficiary is not new".
"""
from __future__ import annotations

from datetime import datetime, timedelta

from cp_common.observations import AccountContext

#: Counters that map straight onto an AccountContext field.
DIRECT = (
    "baseline_mean_paise",
    "inflow_paise",
    "outflow_paise",
    "distinct_counterparties",
    "small_credit_count",
    "sub_ctr_count",
    "near_upi_limit_count",
    "device_cluster_size",
)

#: Observations that need a per-pair or per-device set the counter store does not hold.
#: Listed so the lane can report them as unmeasured with a reason instead of scoring 0.
NEEDS_SET_STORE = {
    "CPT-01": "beneficiary age needs the first-seen pair set, not an account counter",
    "VEL-02": "beneficiary age needs the first-seen pair set, not an account counter",
}

#: Every counter a rebuild can use, in one place so the decision path fetches them in a
#: single round trip rather than one per rule. Must stay in step with what
#: analytics' counter_feed publishes.
COUNTER_FIELDS = DIRECT + ("last_seen_age_seconds",)

INT_FIELDS = {"inflow_paise", "outflow_paise", "distinct_counterparties",
              "small_credit_count", "sub_ctr_count", "near_upi_limit_count",
              "device_cluster_size"}


def rebuild(counters: dict[str, float], *, now: datetime) -> AccountContext:
    """An AccountContext equivalent to the one detection would have built.

    ``last_seen`` is stored as an age because the store holds numbers; it is converted
    back relative to the payment being decided, which is the only reading correct at
    decision time.
    """
    ctx = AccountContext()
    for field in DIRECT:
        if field in counters:
            value = counters[field]
            setattr(ctx, field, int(value) if field in INT_FIELDS else float(value))
    age = counters.get("last_seen_age_seconds")
    if age is not None:
        ctx.last_seen = now - timedelta(seconds=float(age))
    return ctx


def unmeasurable(rule_id: str) -> str | None:
    """Why an otherwise-inline rule cannot be scored from counters alone."""
    return NEEDS_SET_STORE.get(rule_id)
