"""BR-104: a fraud risk policy takes effect on the board's authority, not the operator's.

The requirement is not "thresholds can be edited" — that was already true. It is that a
threshold in force is attributable to a board decision. Two behaviours follow, and they
are deliberately different in strength:

* **Activating** a policy version is a human act, so it is *refused* without an
  attestation naming the approving body, the date and the minute reference.
* **Reading** a policy is not, so an unattested one is *disclosed* rather than blocked.
  A tenant running seeded defaults still needs working clocks; what must never happen is
  those defaults looking like the bank's own approved figures.

The tests below are weighted to the refusals and the disclosure, because a blank
attestation that quietly passes is the whole failure mode.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from services.config_service.app import attestation
from services.config_service.app.defaults import default_configs_for

GOOD = {"approved_by": "board", "approved_on": "2026-03-14",
        "reference": "Board minute 2026/03/14 item 7"}


def body(**over):
    att = {**GOOD, **over}
    return {"thresholds": {"fmr_filing_days": 7}, "attestation": att}


# --------------------------------------------------------------- it is required
def test_a_policy_without_an_attestation_is_refused():
    with pytest.raises(attestation.AttestationError) as exc:
        attestation.validate({"thresholds": {}})
    assert "no board approval recorded" in str(exc.value)


def test_an_empty_attestation_is_refused_not_treated_as_absent_but_fine():
    with pytest.raises(attestation.AttestationError):
        attestation.validate({"attestation": {}})


@pytest.mark.parametrize("field", ["approved_by", "approved_on", "reference"])
def test_each_required_field_is_named_when_missing(field):
    """'Invalid attestation' tells an administrator nothing they can act on."""
    with pytest.raises(attestation.AttestationError) as exc:
        attestation.validate(body(**{field: ""}))
    assert field in str(exc.value)


def test_a_whitespace_only_reference_does_not_satisfy_the_requirement():
    with pytest.raises(attestation.AttestationError):
        attestation.validate(body(reference="   "))


# ------------------------------------------------------------- it is meaningful
def test_an_unrecognised_approving_body_is_refused():
    with pytest.raises(attestation.AttestationError) as exc:
        attestation.validate(body(approved_by="the ops team"))
    assert "not a recognised approving body" in str(exc.value)


def test_a_future_approval_date_is_refused():
    """A policy cannot be in force under an approval that has not happened yet."""
    future = (date.today() + timedelta(days=30)).isoformat()
    with pytest.raises(attestation.AttestationError) as exc:
        attestation.validate(body(approved_on=future))
    assert "in the future" in str(exc.value)


def test_a_reference_too_short_to_follow_is_refused():
    """This is the pointer an inspector follows back to the decision."""
    with pytest.raises(attestation.AttestationError) as exc:
        attestation.validate(body(reference="ok"))
    assert "identify a minute" in str(exc.value)


def test_a_complete_attestation_is_normalised():
    got = attestation.validate(body())
    assert got["approved_by"] == "board"
    assert got["approved_by_label"] == "Board of Directors"
    assert got["approved_on"] == "2026-03-14"
    assert got["reference"].startswith("Board minute")


@pytest.mark.parametrize("key", sorted(attestation.APPROVING_BODIES))
def test_every_approving_body_is_accepted(key):
    """A UCB has a Board of Management where a commercial bank has an SCBMF; refusing a
    tenant's actual approving body would push the record off-platform."""
    assert attestation.validate(body(approved_by=key))["approved_by"] == key


# --------------------------------------------------- it applies to policy and model
def test_only_policy_and_model_kinds_need_an_attestation():
    assert attestation.requires_attestation("policy")
    assert attestation.requires_attestation("model")  # BR-808
    for kind in ("rule", "typology", "network_map", "decision_policy"):
        assert not attestation.requires_attestation(kind), (
            f"{kind} must not need a board minute - gating continuous rule tuning "
            "behind board approval pushes the tuning off-platform")


# ------------------------------------------- the seeded default is honest about itself
def test_the_seeded_policy_carries_no_attestation():
    """The platform's own defaults are a starting point, not the bank's approved policy.
    If this ever starts shipping an attestation, the platform would be asserting a board
    decision that never happened."""
    policy = next(c for c in default_configs_for("commercial_bank")
                  if c["kind"] == "policy")
    assert "attestation" not in policy["body"]
    with pytest.raises(attestation.AttestationError):
        attestation.validate(policy["body"])


def test_the_seeded_policy_still_says_its_numbers_need_approval():
    policy = next(c for c in default_configs_for("commercial_bank")
                  if c["kind"] == "policy")
    assert policy["body"].get("_note"), \
        "the seeded policy must tell the reader its figures are placeholders"
