"""entity_gate.can_extend_credit - mocked at the httpx boundary, the same way
rules.py's active_rules() would be tested: two hops, both allowed to fail
independently, and both directions of failure must fail open (never refuse ingest on
an availability failure that has nothing to do with the tenant's actual entity type).
"""
from unittest.mock import MagicMock, patch

from services.lane_c_service.app import entity_gate


def _resp(json_body, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body
    r.raise_for_status = MagicMock()
    if status >= 400:
        r.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return r


def test_a_credit_extending_entity_type_is_eligible():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = [
            _resp({"entity_type": "commercial_bank"}),
            _resp({"entity_type": "commercial_bank", "can_extend_credit": True,
                  "label": "Commercial Bank"}),
        ]
        eligible, reason = entity_gate.can_extend_credit("t1")
    assert eligible
    assert reason == ""


def test_a_payments_bank_is_refused():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = [
            _resp({"entity_type": "payments_bank"}),
            _resp({"entity_type": "payments_bank", "can_extend_credit": False,
                  "label": "Payments Bank (PB)"}),
        ]
        eligible, reason = entity_gate.can_extend_credit("t2")
    assert not eligible
    assert "payments_bank" in reason
    assert "cannot extend credit" in reason


def test_tenant_service_unreachable_fails_open():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = Exception("connection refused")
        eligible, reason = entity_gate.can_extend_credit("t3")
    assert eligible
    assert "unavailable" in reason


def test_config_service_unreachable_fails_open():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = [
            _resp({"entity_type": "commercial_bank"}),
            Exception("connection refused"),
        ]
        eligible, reason = entity_gate.can_extend_credit("t4")
    assert eligible
    assert "unavailable" in reason


def test_no_entity_type_on_record_fails_open():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = [_resp({"entity_type": ""})]
        eligible, reason = entity_gate.can_extend_credit("t5")
    assert eligible
    assert "no entity_type" in reason
