"""DTO tier — config contracts."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ConfigCreate(BaseModel):
    # policy is board-approved FRM policy (entity classification, thresholds, the PMLA
    # Principal Officer designation) - gated at activation by attestation.requires_
    # attestation(), not here. Creation stays as open as every other kind, deliberately:
    # see attestation.py's note on why retuning is not pushed behind a board minute.
    #
    # model is a candidate ML model version (BR-807/808) - gated at activation by
    # attestation.requires_attestation() (committee approval), challenger.
    # requires_evaluation() (the champion/challenger comparison), and
    # model_artifact.requires_artifact() (a loadable trained artifact reference -
    # AI/ML roadmap Phase 2, HLD AD-14). A version that clears all three and confirms
    # activation is genuinely loaded and scoring VEL-04 in analytics-service's
    # detection worker - this is no longer only a governance floor built ahead of a
    # model; Phase 2 has landed. Phases 3 and 4 (AI/ML Roadmap, OD-06) have not.
    #
    # decision_policy is Lane A's per-rail routing (mode, budget, fail policy, and
    # whether the rail is enforced or shadow) - gated at activation by graduation.
    # requires_graduation(): any rail the body marks enforced must carry a per-rail
    # graduation record (BR-316), not a tenant-wide attestation, because a rail leaves
    # shadow mode on its own evidence, not the whole policy's.
    kind: str = Field(pattern=r"^(rule|typology|network_map|policy|model|decision_policy)$")
    name: str = Field(max_length=128, examples=["mule-layering"])
    version: str = Field(default="1.0.0", examples=["1.0.0"])
    body: dict[str, Any]


class ConfigOut(BaseModel):
    id: str
    tenant_id: str
    kind: str
    name: str
    version: str
    status: str
    body: dict[str, Any]
    created_at: datetime

    # BR-715: maker-checker on activation. Populated once a proposal exists; all None
    # for a config that has never had an activation proposed against it.
    proposed_by: str | None = None
    proposed_by_role: str | None = None
    proposed_at: datetime | None = None
    approved_by: str | None = None
    approved_by_role: str | None = None
    approved_at: datetime | None = None

    model_config = {"from_attributes": True}


class SeedRequest(BaseModel):
    tenant_id: str
    # Which RBI Master Direction governs this entity - drives the policy defaults.
    entity_type: str = "commercial_bank"
    ucb_tier: int | None = None
