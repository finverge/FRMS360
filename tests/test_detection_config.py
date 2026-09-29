"""Detection reads the control plane.

The point of a configurable platform is that configuration changes behaviour. These
tests exist because the two sides were once entirely disconnected: config stored
'018@1.0.0', detection emitted 'LAY-02', and retuning a threshold in the console changed
nothing at all.
"""
import pytest

from scripts.generate_synthetic_data import (
    OBSERVATION_RANGE, evaluate, load_tenant_rules, observe, severity_for,
)


def test_generator_reads_the_tenants_catalogue(tid):
    catalogue, from_control_plane = load_tenant_rules(tid)
    assert from_control_plane, "detection fell back to built-in defaults"
    assert catalogue, "no rules returned"
    for rule_id, rule in catalogue.items():
        assert rule["threshold"] is not None, f"{rule_id} has no operative threshold"


def test_an_observation_below_the_threshold_does_not_fire():
    catalogue = {"LAY-02": {"threshold": 40, "sub_rule_ref": ".03",
                            "reason": "hub", "observed_unit": "counterparties"}}
    assert evaluate("LAY-02", 39.9, catalogue) is None
    assert evaluate("LAY-02", 40.0, catalogue) is not None


def test_lowering_the_threshold_catches_more_traffic():
    """The load-bearing property. Observations are drawn from a FIXED range, so only the
    configured threshold decides what fires - if the range scaled with the threshold,
    retuning would change nothing."""
    sample = [observe("CHN-02") for _ in range(4000)]
    sensitive = {"CHN-02": {"threshold": 200, "sub_rule_ref": ".02",
                            "reason": "r", "observed_unit": "kmph"}}
    lax = {"CHN-02": {"threshold": 4800, "sub_rule_ref": ".02",
                      "reason": "r", "observed_unit": "kmph"}}
    fired_sensitive = sum(evaluate("CHN-02", o, sensitive) is not None for o in sample)
    fired_lax = sum(evaluate("CHN-02", o, lax) is not None for o in sample)
    assert fired_sensitive > fired_lax * 5, (
        f"threshold barely moved detection: {fired_sensitive} vs {fired_lax}")


def test_observation_ranges_do_not_depend_on_configured_thresholds():
    """A range that tracked the threshold would make retuning a no-op."""
    for rule_id, (lo, hi) in OBSERVATION_RANGE.items():
        assert hi > lo, f"{rule_id} has an empty observation range"
        draws = [observe(rule_id) for _ in range(50)]
        assert min(draws) >= lo - 1e-6 and max(draws) <= hi + 1e-6


def test_strong_signals_still_face_the_threshold():
    """Injected typologies present a pronounced observation, but are NOT force-fired.
    A tenant that sets a threshold too high should genuinely miss real fraud - hiding
    that would make misconfiguration invisible."""
    impossible = {"LAY-02": {"threshold": 10_000, "sub_rule_ref": ".03",
                             "reason": "r", "observed_unit": "c"}}
    strong = [observe("LAY-02", strong=True) for _ in range(200)]
    assert all(evaluate("LAY-02", o, impossible) is None for o in strong)


def test_severity_follows_the_tenant_policy():
    strict = {"severity_critical_score": 100, "severity_high_score": 50,
              "severity_medium_score": 20}
    lenient = {"severity_critical_score": 900, "severity_high_score": 800,
               "severity_medium_score": 700}
    assert severity_for(150, strict) == "critical"
    assert severity_for(150, lenient) == "low"


def test_no_alert_exists_below_its_recorded_threshold(tid):
    """Every stored alert must be defensible: the observation crossed the threshold that
    is recorded against it."""
    from cp_common import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        bad = db.execute(text(
            "SELECT COUNT(*) FROM fact_alert WHERE tenant_id = :t "
            "AND observed_value IS NOT NULL AND threshold_value IS NOT NULL "
            "AND observed_value < threshold_value"), {"t": tid}).scalar()
    finally:
        db.close()
    assert bad == 0, f"{bad} alerts fired below their own threshold"
