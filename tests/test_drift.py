"""Model drift (PSI / KS).

Precision alone cannot show that the population a model scores has moved underneath it -
the model can hold its hit-rate while the traffic changes shape. FREE-AI expects that to
be monitored across a model's life, so these numbers must be right.
"""
import pytest

from services.analytics_service.app.drift import PSI_SIGNIFICANT, PSI_STABLE, ks, psi


def test_identical_distributions_have_no_drift():
    sample = [float(i % 100) for i in range(1000)]
    out = psi(sample, list(sample))
    assert out["available"]
    assert out["psi"] < 0.001, "identical windows must not report drift"
    assert out["band"] == "stable"


def test_a_shifted_distribution_is_flagged_significant():
    reference = [float(i % 100) for i in range(1000)]
    shifted = [v + 80 for v in reference]          # whole population moves up
    out = psi(reference, shifted)
    assert out["psi"] > PSI_SIGNIFICANT, f"missed an obvious shift (psi={out['psi']})"
    assert out["band"] == "significant"


def test_psi_bands_follow_the_conventional_reading():
    assert PSI_STABLE < PSI_SIGNIFICANT
    stable = psi([float(i % 50) for i in range(500)],
                 [float(i % 50) for i in range(500)])
    assert stable["band"] == "stable"


def test_psi_survives_an_empty_bin():
    """A bin empty on one side must not produce infinity or a divide-by-zero."""
    reference = [1.0] * 200 + [50.0] * 200
    current = [1.0] * 400                       # upper bins now empty
    out = psi(reference, current)
    assert out["available"]
    assert out["psi"] == out["psi"], "PSI is NaN"
    assert out["psi"] != float("inf")


def test_psi_reports_which_bins_moved():
    out = psi([float(i % 100) for i in range(600)],
              [float(i % 100) + 40 for i in range(600)])
    assert out["bins"], "no per-bin contributions returned"
    assert sum(b["reference_pct"] for b in out["bins"]) == pytest.approx(100, abs=1.0)


def test_too_little_data_is_reported_not_guessed():
    assert psi([], [1.0])["available"] is False
    assert psi([1.0, 2.0], [])["available"] is False


def test_ks_detects_a_shift_and_is_zero_when_identical():
    sample = [float(i % 100) for i in range(500)]
    assert ks(sample, list(sample))["ks"] < 0.02
    moved = ks(sample, [v + 60 for v in sample])
    assert moved["ks"] > 0.4, "KS missed a large shift"


def test_drift_endpoint_returns_both_measures(analytics_client, token_for, tid):
    r = analytics_client.get(f"/analytics/{tid}/drift",
                             headers=token_for("data_scientist"),
                             params={"reference_days": 15})
    assert r.status_code == 200, r.text
    d = r.json()
    assert "psi" in d or d.get("available") is False
    assert "windows" in d and "reference" in d["windows"] and "current" in d["windows"]
    assert "mix_shift" in d


def test_drift_is_restricted_to_roles_that_own_model_risk(analytics_client, token_for, tid):
    assert analytics_client.get(f"/analytics/{tid}/drift",
                                headers=token_for("analyst")).status_code == 403
