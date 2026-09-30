"""GET /internal/entity-types/{entity_type} - the single source of truth for
ENTITY_TYPES' can_extend_credit flag, read by Lane C's entity_gate.py instead of
duplicating the RBI-Direction mapping into a second service."""
from cp_common.settings import settings


def _get(config_client, entity_type):
    return config_client.get(f"/internal/entity-types/{entity_type}",
                             headers={"x-internal-key": settings.internal_api_key})


def test_a_credit_extending_entity_type_reports_true(config_client):
    r = _get(config_client, "commercial_bank")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["can_extend_credit"] is True
    assert body["label"] == "Commercial Bank"


def test_payments_bank_reports_false(config_client):
    r = _get(config_client, "payments_bank")
    assert r.status_code == 200, r.text
    assert r.json()["can_extend_credit"] is False


def test_an_unknown_entity_type_is_refused_not_guessed(config_client):
    r = _get(config_client, "not_a_real_entity_type")
    assert r.status_code == 404, r.text


def test_the_endpoint_requires_the_internal_key(config_client):
    r = config_client.get("/internal/entity-types/commercial_bank")
    assert r.status_code in (401, 403), r.text
