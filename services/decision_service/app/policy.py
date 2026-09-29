"""Per-tenant, per-rail routing for the inline lane.

The control plane governs behaviour here as it does everywhere else: which rails go
inline, the latency budget, which actions the channel can carry, and — the one that
matters most — what happens when the check cannot answer in time.

**Fail-open versus fail-closed has no default.** A missing policy is refused, not
guessed. Choosing wrongly on the bank's behalf means either declining genuine payments
or silently not applying a control that everyone believes is running, and RBI will ask
which was chosen. This mirrors the encryption keys refusing to write plaintext rather
than assuming.

Shadow mode is the default state of every new policy: the lane computes and records, and
the caller is always told to allow. A tenant leaves shadow mode deliberately, having seen
what the lane would have done to their own traffic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: How a rail is handled.
MODE_INLINE = "inline"     # synchronous decision inside the payment window
MODE_NRT = "nrt"           # near-real-time only; the lane is not consulted
MODES = (MODE_INLINE, MODE_NRT)

FAIL_OPEN = "open"         # budget expired or store down -> allow, and record it
FAIL_CLOSED = "closed"     # budget expired or store down -> decline
FAIL_MODES = (FAIL_OPEN, FAIL_CLOSED)

#: What each rail can actually *execute*, beyond allowing.
#:
#: This is a property of the payment scheme, not a configuration preference, which is why
#: it is here and not in the tenant's policy. Returning an action the rail cannot carry is
#: the worst failure this lane can produce: the decision log records that the control
#: fired, the switch does not recognise the answer, and the payment settles anyway. The
#: control reads as enforced in every report and is not.
#:
#: * **UPI** — the PIN is captured at the remitter PSP before the request reaches the
#:   bank, so the customer has already authenticated by decision time, and the NPCI
#:   response set is approve or decline with a reason code. There is no step-up code to
#:   return. Binary.
#: * **IMPS** — likewise a real-time push, authenticated upstream. Binary.
#: * **CARD** — 3-D Secure makes a challenge a first-class protocol outcome. The one rail
#:   where stepping up is genuinely available inside the authorisation.
#: * **NEFT / RTGS** — settle in a window, so parking a payment for outward-remittance
#:   screening is a real operation a bank performs today. ``hold`` belongs here and
#:   nowhere else.
#: * **NETBANKING / MOBILE** — bank-owned channels. The bank controls the authentication
#:   step, so it can insert one.
#:
#: A rail absent from this map is refused rather than assumed permissive: a new scheme
#: whose capabilities nobody has established must not inherit the most powerful set.
RAIL_ACTIONS = {
    "UPI": {"decline"},
    "IMPS": {"decline"},
    "CARD": {"challenge", "decline"},
    "NEFT": {"hold", "decline"},
    "RTGS": {"hold", "decline"},
    "NETBANKING": {"challenge", "decline"},
    "MOBILE": {"challenge", "decline"},
}

#: Why a rail cannot carry an action, in the words the refusal should use.
_CANNOT = {
    ("UPI", "challenge"): (
        "UPI collects the PIN at the remitter PSP before the request reaches you, so the "
        "customer has already authenticated and the response set is approve or decline. "
        "A challenge would be returned and ignored"),
    ("IMPS", "challenge"): (
        "IMPS is authenticated upstream in the initiating channel; there is no step-up "
        "the beneficiary-side decision can insert"),
    ("UPI", "hold"): (
        "UPI settles in under a second. There is nothing to hold a payment in, and no "
        "queue for anyone to work"),
    ("IMPS", "hold"): (
        "IMPS settles immediately. A held payment would be a customer's money frozen "
        "with no operational path to release it"),
    ("CARD", "hold"): (
        "A card authorisation cannot be parked; the terminal is waiting"),
}


def why_not(rail: str, action: str) -> str:
    """The reason a rail cannot honour an action. Always a sentence, never a code."""
    return _CANNOT.get((rail.upper(), action), "")


class PolicyError(Exception):
    """A policy that cannot be applied safely. Never resolved by a default."""


@dataclass
class RailPolicy:
    rail: str
    mode: str = MODE_NRT
    budget_ms: int = 100
    fail: str = ""                                    # deliberately empty: must be set
    actions: list[str] = field(default_factory=list)  # subset of models.ACTIONS
    #: Shadow mode computes and records but never enforces. New policies start here.
    shadow: bool = True
    #: A match at or above this severity escalates from challenge to decline. Absent
    #: means the lane never declines - which is a legitimate configuration.
    decline_from_severity: str = ""

    def validate(self) -> None:
        if self.mode not in MODES:
            raise PolicyError(f"rail {self.rail}: mode must be one of {MODES}")
        if self.mode == MODE_NRT:
            return
        if self.budget_ms <= 0 or self.budget_ms > 2000:
            raise PolicyError(
                f"rail {self.rail}: budget_ms {self.budget_ms} is outside 1-2000. A budget "
                "longer than the payment window is not a budget.")
        if self.fail not in FAIL_MODES:
            raise PolicyError(
                f"rail {self.rail}: fail must be explicitly '{FAIL_OPEN}' or "
                f"'{FAIL_CLOSED}'. There is no safe default — failing open silently "
                "stops applying the control, failing closed declines genuine payments, "
                "and the bank must choose which.")
        if not self.actions:
            raise PolicyError(
                f"rail {self.rail}: no actions configured. A lane that can only allow "
                "is not a control; say so by setting mode to 'nrt' instead.")
        from .models import ACTIONS
        bad = [a for a in self.actions if a not in ACTIONS]
        if bad:
            raise PolicyError(f"rail {self.rail}: unknown action(s) {bad}")

        # Knowing the action exists is not the same as knowing this rail can carry it.
        rail = self.rail.upper()
        if rail not in RAIL_ACTIONS:
            raise PolicyError(
                f"rail {self.rail}: no action capabilities are declared for this rail, so "
                f"there is no way to know which answers its switch can execute. Add it to "
                f"RAIL_ACTIONS with what the scheme actually supports - inheriting the "
                f"most powerful set would be the wrong guess to make silently.")
        allowed = RAIL_ACTIONS[rail]
        for action in self.actions:
            if action not in allowed:
                reason = why_not(rail, action)
                raise PolicyError(
                    f"rail {self.rail}: '{action}' is not an answer this rail can execute"
                    + (f". {reason}" if reason else "")
                    + f". Returning it would be recorded as an enforced control while the "
                    f"payment settled anyway. {rail} supports: "
                    f"{', '.join(sorted(allowed)) or 'allow only'}.")


@dataclass
class DecisionPolicy:
    tenant_id: str
    version: str = ""
    rails: dict[str, RailPolicy] = field(default_factory=dict)

    def for_rail(self, rail: str) -> RailPolicy | None:
        return self.rails.get((rail or "").upper())

    def validate(self) -> None:
        for rp in self.rails.values():
            rp.validate()


def parse(tenant_id: str, body: dict) -> DecisionPolicy:
    """Build a policy from a stored config body, refusing anything unsafe."""
    rails: dict[str, RailPolicy] = {}
    for rail, spec in (body.get("rails") or {}).items():
        rails[rail.upper()] = RailPolicy(
            rail=rail.upper(),
            mode=str(spec.get("mode", MODE_NRT)).lower(),
            budget_ms=int(spec.get("budget_ms", 100)),
            fail=str(spec.get("fail", "")).lower(),
            actions=[str(a).lower() for a in (spec.get("actions") or [])],
            shadow=bool(spec.get("shadow", True)),
            decline_from_severity=str(spec.get("decline_from_severity", "")).lower(),
        )
    p = DecisionPolicy(tenant_id=tenant_id, version=str(body.get("version", "")),
                       rails=rails)
    p.validate()
    return p


#: Shipped as the starting point for a new tenant. Every rail is in shadow mode and
#: every inline rail fails open, because a platform that starts by declining a bank's
#: payments does not get a second meeting. The bank moves off these deliberately.
STARTER = {
    "version": "1.0.0",
    "rails": {
        # Binary, because that is what the rail can execute. See RAIL_ACTIONS.
        "UPI": {"mode": "inline", "budget_ms": 80, "fail": "open",
                "actions": ["decline"], "shadow": True,
                "decline_from_severity": "critical"},
        "IMPS": {"mode": "inline", "budget_ms": 150, "fail": "open",
                 "actions": ["decline"], "shadow": True,
                 "decline_from_severity": "critical"},
        "CARD": {"mode": "inline", "budget_ms": 100, "fail": "open",
                 "actions": ["challenge", "decline"], "shadow": True,
                 "decline_from_severity": "critical"},
        # NEFT and RTGS settle in a window that makes an inline check pointless: the
        # near-real-time lane sees them with full context well inside the settlement
        # cycle, and full context finds more.
        "NEFT": {"mode": "nrt"},
        "RTGS": {"mode": "nrt"},
    },
}
