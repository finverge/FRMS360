"""Which catalogue rules can run inside a payment window, and why the rest cannot.

The constraint is not engineering effort. It is whether the signal *exists yet* at
authorisation time. A fan-in hub is invisible until the fan has formed; a circular flow
needs the cycle to close. Those rules are not slow versions of inline rules — they answer
a question the moment of authorisation cannot yet ask.

Every exclusion below carries its reason in the table rather than in a comment somewhere
else, because the first thing anyone will ask when a rule does not fire inline is whether
it was forgotten.

The catalogue remains the single source of thresholds. This module decides only *where*
a rule can run, never *what* it decides — the same rule id, band and threshold governs
both lanes, so the two cannot disagree about what constitutes a match.

**Keyed by rule id.** An earlier version of this module invented a parallel vocabulary of
"observation names" (``value_vs_baseline``, ``credit_burst_count``) that no catalogue and
no config ever produced, so nothing ever matched it and the inline lane silently evaluated
nothing at all. Rule ids are what config-service serves, what ``observations.observe()``
returns, and what an alert records. One vocabulary, or the lanes drift.
"""
from __future__ import annotations

#: rule id -> how the inline lane obtains the observation.
#:   "counter" - rebuilt from precomputed per-account counters maintained on the write path
#:   "request" - supplied by the channel with the payment; no lookup at all
INLINE_SOURCE: dict[str, str] = {
    "VEL-01": "counter",   # value against the account's 30-day baseline
    "VEL-03": "counter",   # rolling small-credit count
    "SME-01": "counter",   # rolling count just under the CTR threshold
    "SME-02": "counter",   # rolling count just under the PIN-less UPI limit
    "BEH-01": "counter",   # dormancy: gap since last activity, on a high-value payment
    "LAY-01": "counter",   # outflow against inflow, short window
    "LAY-02": "counter",   # distinct counterparties
    "LAY-04": "counter",   # accounts sharing the device fingerprint
    # The channel already knows whether the device and payee are new at authorisation
    # time. Deriving it here from a rebuilt context would be wrong, not merely slower:
    # the context carries no device or counterparty *sets*, so every payment would look
    # like a new device to a new payee and the indicator would fire on all of them.
    "CHN-01": "request",
    # Same shape as CHN-01: an integrated device-fingerprinting / behavioural-biometrics
    # provider scores the session before authorisation completes and sends the number
    # with the payment - there is nothing to derive from a rebuilt context, only a
    # threshold to apply. See evaluate.py's request-to-txn handling for the read side.
    "CHN-04": "request",
    "CHN-05": "request",
}

#: rule id -> why it cannot be answered inside the payment window.
NOT_INLINE: dict[str, str] = {
    "CPT-01": ("beneficiary age needs the first-seen-pair set, which is not an account "
               "counter. It is an 'lte' rule, so an unknown pair would read as age zero "
               "and fire on every high-value payment"),
    "VEL-02": ("same first-seen-pair set as CPT-01, and the same 'lte' trap"),
    "CPT-02": "sanctions and PEP screening lists are not loaded in the payment path",
    "CPT-03": "CERSAI charge registry is a lookup, and not on this path",
    "LAY-03": "needs a multi-hop graph traversal; the cycle has not closed yet",
    "CHN-02": "needs a geolocation feed that is not contracted",
    "CHN-03": ("needs the account's hour profile, which detection holds but does not "
               "publish as a counter"),
    "SME-03": "needs invoice-level data, which no feed supplies",
    "BEH-02": "needs the CBS repayment funding source, posted after settlement",
    "BEH-03": "needs end-use tracing across subsequent transactions",
    "CBS-01": "needs the loan account's disbursal-to-withdrawal history from the CBS",
    "CBS-02": "needs security and stock records from the CBS",
    "CBS-03": "needs sale-proceeds routing, known only after the fact",
    "CBS-04": "needs the CBS cheque-clearing outcome, known only after the fact",
    "CBS-05": "needs the CBS's own overdraft/cash-credit position, posted after settlement",
    "TBM-01": "needs trade-finance records",
    "TBM-02": "needs trade-finance records",
    "TBM-03": "needs the foreign-bills register",
    "QUAL-01": "qualitative, entered by credit monitoring",
    "QUAL-02": "qualitative, entered by credit monitoring",
    "QUAL-03": "qualitative, entered by credit monitoring",
}


def rule_id_of(rule) -> str:
    return (rule.get("rule_id") if isinstance(rule, dict)
            else getattr(rule, "rule_id", "")) or ""


def eligible(rule_id: str) -> bool:
    return rule_id in INLINE_SOURCE


def why_not(rule_id: str) -> str:
    """The recorded reason a rule stays in the near-real-time lane."""
    return NOT_INLINE.get(rule_id, "not classified for the inline lane")


def split(rules) -> tuple[list, list]:
    """Partition catalogue rules into (inline, deferred), by rule id."""
    inline, deferred = [], []
    for r in rules:
        (inline if eligible(rule_id_of(r)) else deferred).append(r)
    return inline, deferred


def classify_catalogue(catalogue) -> dict:
    """A reportable view of the split, for the console and for a customer conversation.

    Deliberately returns the *reasons* alongside the counts. "9 of 28 run inline" invites
    the question this answers.
    """
    inline, deferred = split(catalogue)
    return {
        "inline_count": len(inline),
        "deferred_count": len(deferred),
        "inline": [{"rule_id": rule_id_of(r),
                    "source": INLINE_SOURCE[rule_id_of(r)]} for r in inline],
        "deferred": [{"rule_id": rule_id_of(r),
                      "why_not": why_not(rule_id_of(r))} for r in deferred],
    }
