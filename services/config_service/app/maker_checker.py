"""Maker-checker on privileged configuration changes (BR-715).

Until now, activating a tenant's fraud policy or a rule threshold - control-plane
configuration that decides what Fraud360 actually blocks - took one person's word for
it. The RFA case lifecycle already refuses to let one person both declare and confirm a
fraud finding (see ``analytics_service.app.workflow``, ``declare_fraud``); a threshold
that decides which of a tenant's transactions get held is no less consequential, and
BR-715 asks for the same discipline here.

Config kinds don't share the case's lifecycle, so this is not a state machine - it is a
narrow two-step gate over ``TenantConfig.status``:

    draft --propose--> pending_activation --confirm--> active
                              |
                              +--reject--> draft

The first eligible actor to call activate *proposes* it; a second, different eligible
actor must *confirm* before it takes effect and reaches live decisioning. Either the
proposer (withdrawing) or another eligible reviewer (declining) may *reject* a pending
proposal back to draft - unlike confirmation, that carries no risk of self-approval.
"""
from __future__ import annotations

from datetime import datetime, timezone

class MakerCheckerRefused(Exception):
    """A proposal, confirmation or rejection this gate does not permit."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code


def ensure_eligible(tenant_id: str, role: str) -> None:
    from cp_common.dynamic_roles import can_activate_config
    if can_activate_config(tenant_id, role):
        return
    raise MakerCheckerRefused(
        "Your role may not activate a configuration. Activation needs a role this "
        "tenant has granted that capability.", "role_not_permitted")


def propose(tenant_id: str, row, actor: str, role: str, *, now: datetime | None = None) -> None:
    """First call against a draft: records the proposal. Does not activate."""
    ensure_eligible(tenant_id, role)
    row.status = "pending_activation"
    row.proposed_by = actor
    row.proposed_by_role = role
    row.proposed_at = now or datetime.now(timezone.utc)
    row.approved_by = None
    row.approved_by_role = None
    row.approved_at = None


def confirm(tenant_id: str, row, actor: str, role: str, *, now: datetime | None = None) -> None:
    """Second call against a pending proposal: the actual activation guard.

    Only sets ``approved_*`` - the caller still has to move status to 'active' and
    archive siblings, same as the previous single-step activation did.
    """
    ensure_eligible(tenant_id, role)
    if row.proposed_by == actor:
        raise MakerCheckerRefused(
            "You proposed this activation, so you may not also confirm it. A different "
            "person holding the activation capability must confirm.", "self_approval")
    row.approved_by = actor
    row.approved_by_role = role
    row.approved_at = now or datetime.now(timezone.utc)


def reject(tenant_id: str, row, actor: str, role: str) -> None:
    """Decline a pending proposal back to draft. The proposer may withdraw their own;
    that is not self-approval, since nothing takes effect."""
    ensure_eligible(tenant_id, role)
    row.status = "draft"
    row.proposed_by = None
    row.proposed_by_role = None
    row.proposed_at = None
    row.approved_by = None
    row.approved_by_role = None
    row.approved_at = None
