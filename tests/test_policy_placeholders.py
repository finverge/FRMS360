"""Every policy placeholder a metric uses must exist in both places that define policy.

Policy values are defined twice: authoritatively in config-service, and as a fallback in
analytics for when the control plane is unreachable. That duplication is deliberate —
failing every dashboard query because config-service blinked would be worse — but it is
also a standing invitation to drift.

The drift is not theoretical. Adding a metric that reads ``{ews_examination_days}``
raised ``KeyError`` at query time on every tenant, because the placeholder existed in
config-service and not in the fallback. It failed loudly, which was lucky: a metric added
with a placeholder that happens to exist in the fallback but *not* in config-service would
silently serve the platform default while claiming to be the bank's board-approved figure.

These tests make both directions a build failure instead.
"""
import re

import pytest

from services.analytics_service.app.metrics import REGISTRY
from services.analytics_service.app.rules import _FALLBACK_POLICY
from services.config_service.app.policy import _base

#: ``{b}`` is the table alias the engine substitutes, not a policy value.
NOT_POLICY = {"b"}

PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def placeholders_used() -> dict[str, set[str]]:
    """Every policy placeholder in the registry, and which metrics use it."""
    used: dict[str, set[str]] = {}
    for name, m in REGISTRY.items():
        for fragment in (m.expr or "", m.where or ""):
            for key in PLACEHOLDER.findall(fragment):
                if key not in NOT_POLICY:
                    used.setdefault(key, set()).add(name)
    return used


def test_every_metric_placeholder_exists_in_the_fallback():
    """Otherwise the metric raises KeyError the moment config-service is unreachable —
    which is exactly when the dashboards matter most."""
    used = placeholders_used()
    missing = {k: sorted(v) for k, v in used.items() if k not in _FALLBACK_POLICY}
    assert not missing, (
        "metric placeholders absent from analytics' fallback policy: "
        f"{missing}. Add them to _FALLBACK_POLICY in rules.py.")


def test_every_metric_placeholder_exists_in_the_authoritative_policy():
    """The more dangerous direction. A placeholder present only in the fallback resolves
    fine and quietly serves a platform default as though it were the bank's own
    board-approved threshold."""
    used = placeholders_used()
    authoritative = _base()
    missing = {k: sorted(v) for k, v in used.items() if k not in authoritative}
    assert not missing, (
        "metric placeholders absent from config-service's policy: "
        f"{missing}. A metric reading one of these would silently use the platform "
        "default and present it as the tenant's approved figure.")


def test_the_fallback_does_not_drift_from_the_authoritative_policy():
    """Every fallback key must be a real policy key. A stale one is a value nobody can
    change through the console but that still governs behaviour when config is down."""
    authoritative = set(_base())
    orphans = sorted(set(_FALLBACK_POLICY) - authoritative)
    assert not orphans, (
        f"fallback policy keys no longer defined in config-service: {orphans}")


def test_the_fallback_agrees_with_the_authoritative_default():
    """Where both define a key, they must define the same number — otherwise a
    config-service outage silently changes what counts as a breach."""
    authoritative = _base()
    disagreements = {
        k: (v, authoritative[k])
        for k, v in _FALLBACK_POLICY.items()
        if k in authoritative and not isinstance(v, dict) and v != authoritative[k]
    }
    assert not disagreements, (
        "fallback and authoritative policy disagree (fallback, authoritative): "
        f"{disagreements}")


@pytest.mark.parametrize("metric_name", [
    "ews_examination_overdue", "ews_examined_late_count", "ews_examination_p95_days"])
def test_the_ews_examination_clock_is_registered(metric_name):
    """Not more than 30 days to examine an EWS alert - set from the 2024 Directions
    originally, carried forward in the 2026 successors (see policy.py's module
    docstring on the 31 July 2026 restructuring)."""
    assert metric_name in REGISTRY
    assert _base()["ews_examination_days"] == 30
