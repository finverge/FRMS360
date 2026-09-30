"""Presentation tier — tenant config CRUD, version activation, and internal seeding."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from cp_common import (
    AppError,
    Principal,
    get_current_principal,
    get_session,
    record_audit,
    require_internal_key,
    resolve_tenant_scope,
    settings,
)

from cp_common.cache import build_backend, verify_backend_supports

from ..defaults import default_configs_for
from ..repositories import ConfigRepository
from ..schemas import ConfigCreate, ConfigOut, SeedRequest
from .. import attestation, challenger, graduation, maker_checker, model_artifact

# Bumping the shared generation is what makes a config change take effect on every
# analytics replica at the same instant, instead of whenever each replica's own timer
# happened to expire.
_CACHE_BACKEND = build_backend(settings.cache_backend)
verify_backend_supports(_CACHE_BACKEND, settings.app_replicas)


def _publish_config_change() -> None:
    """Invalidate the tenant configuration caches across the whole deployment."""
    for namespace in ("rules", "policy"):
        _CACHE_BACKEND.bump(namespace)


router = APIRouter(tags=["configs"])


@router.get("/configs/{tenant_id}", response_model=list[ConfigOut])
def list_configs(
    tenant_id: str,
    kind: str | None = Query(default=None),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> list[ConfigOut]:
    resolve_tenant_scope(principal, tenant_id)
    rows = ConfigRepository(db).list(tenant_id, kind)
    return [ConfigOut.model_validate(r) for r in rows]


@router.post("/configs/{tenant_id}", response_model=ConfigOut, status_code=201)
def create_config(
    tenant_id: str,
    payload: ConfigCreate,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> ConfigOut:
    resolve_tenant_scope(principal, tenant_id)
    repo = ConfigRepository(db)
    if repo.exists(tenant_id, payload.kind, payload.name, payload.version):
        raise AppError("That version already exists", 409, "version_conflict")
    row = repo.create(tenant_id, payload.kind, payload.name, payload.version, payload.body)
    record_audit(
        service="config-service", action="config.create", actor=principal.subject,
        actor_role=principal.role, tenant_id=tenant_id, target_type="config", target_id=row.id,
        status="success", detail={"kind": payload.kind, "name": payload.name, "version": payload.version},
    )
    _publish_config_change()
    return ConfigOut.model_validate(row)


@router.post("/configs/{tenant_id}/{config_id}/activate", response_model=ConfigOut)
def activate_config(
    tenant_id: str,
    config_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> ConfigOut:
    """BR-715: maker-checker. The first eligible caller *proposes* activation (status
    moves to ``pending_activation``, nothing takes effect yet); a second, different
    eligible caller must then call this same endpoint again to *confirm* it before it
    reaches live decisioning. See ``maker_checker.py``."""
    resolve_tenant_scope(principal, tenant_id)
    repo = ConfigRepository(db)
    row = repo.get(tenant_id, config_id)
    if not row:
        raise AppError("Config not found", 404, "not_found")
    if row.status in ("active", "archived"):
        raise AppError(
            f"This version is already {row.status} and cannot be activated again.",
            409, "already_" + row.status)

    try:
        if row.status == "draft":
            # BR-104/BR-808. A fraud risk policy takes effect on the board's authority,
            # a model on its governance committee's - not the operator's - so neither
            # may even be proposed for activation without naming who approved it.
            # Refused rather than defaulted: a blank attestation is indistinguishable
            # from an unapproved policy or model.
            attested = None
            if attestation.requires_attestation(row.kind):
                attested = attestation.validate(row.body or {})
            # BR-807. A candidate model is compared against the one it would replace
            # before it is proposed, not approved on its own numbers alone.
            evaluation = None
            if challenger.requires_evaluation(row.kind):
                evaluation = challenger.validate(row.body or {})
            # AI/ML roadmap Phase 2 (HLD AD-14/§16). A model that cannot be loaded
            # cannot be scored, whatever its evaluation and committee approval say.
            artifact = None
            if model_artifact.requires_artifact(row.kind):
                artifact = model_artifact.validate(row.body or {})
            # BR-316. Any rail this decision_policy body would enforce needs its own
            # graduation record - a different rail's board sign-off says nothing about
            # this one.
            grad = None
            if graduation.requires_graduation(row.kind):
                grad = graduation.validate(row.body or {})
            maker_checker.propose(tenant_id, row, principal.subject, principal.role)
            row = repo.persist(row)
            detail = {"kind": row.kind, "name": row.name, "version": row.version}
            if attested:
                detail["attestation"] = attested
            if evaluation:
                detail["challenger_evaluation"] = evaluation
            if artifact:
                detail["model_artifact"] = artifact
            if grad:
                detail["graduation"] = grad
            record_audit(
                service="config-service", action="config.activate.proposed",
                actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
                target_type="config", target_id=row.id, status="success", detail=detail,
            )
            return ConfigOut.model_validate(row)

        # row.status == "pending_activation": this call is the confirmation.
        maker_checker.confirm(tenant_id, row, principal.subject, principal.role)
        activated = repo.activate(tenant_id, row)
        record_audit(
            service="config-service", action="config.activate", actor=principal.subject,
            actor_role=principal.role, tenant_id=tenant_id, target_type="config",
            target_id=activated.id, status="success",
            detail={"kind": activated.kind, "name": activated.name,
                    "version": activated.version,
                    "proposed_by": activated.proposed_by,
                    "proposed_by_role": activated.proposed_by_role},
        )
        _publish_config_change()
        return ConfigOut.model_validate(activated)
    except (attestation.AttestationError, challenger.ChallengerEvaluationError,
            model_artifact.ModelArtifactError, graduation.GraduationError,
            maker_checker.MakerCheckerRefused) as exc:
        # Each gate's own code/status; maker-checker carries its own on the exception
        # since who-may-act is a 403, not a 422 content problem like the others.
        code, status_code = {
            attestation.AttestationError: ("attestation_required", 422),
            challenger.ChallengerEvaluationError: ("challenger_evaluation_required", 422),
            model_artifact.ModelArtifactError: ("model_artifact_required", 422),
            graduation.GraduationError: ("graduation_required", 422),
        }.get(type(exc), (getattr(exc, "code", "app_error"), 403))
        record_audit(
            service="config-service", action="config.activate",
            actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
            target_type="config", target_id=row.id, status="refused",
            detail={"kind": row.kind, "version": row.version, "reason": str(exc)},
        )
        raise AppError(str(exc), status_code, code)


@router.post("/configs/{tenant_id}/{config_id}/reject-activation", response_model=ConfigOut)
def reject_config_activation(
    tenant_id: str,
    config_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_current_principal),
) -> ConfigOut:
    """BR-715: decline a pending proposal back to 'draft'. The proposer may withdraw
    their own; that is not self-approval, since nothing takes effect either way."""
    resolve_tenant_scope(principal, tenant_id)
    repo = ConfigRepository(db)
    row = repo.get(tenant_id, config_id)
    if not row:
        raise AppError("Config not found", 404, "not_found")
    if row.status != "pending_activation":
        raise AppError(
            "Only a version awaiting confirmation can be rejected.", 409, "not_pending")

    try:
        maker_checker.reject(tenant_id, row, principal.subject, principal.role)
    except maker_checker.MakerCheckerRefused as exc:
        record_audit(
            service="config-service", action="config.activate.rejected",
            actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
            target_type="config", target_id=row.id, status="refused",
            detail={"kind": row.kind, "version": row.version, "reason": exc.message},
        )
        raise AppError(exc.message, 403, exc.code)

    row = repo.persist(row)
    record_audit(
        service="config-service", action="config.activate.rejected",
        actor=principal.subject, actor_role=principal.role, tenant_id=tenant_id,
        target_type="config", target_id=row.id, status="success",
        detail={"kind": row.kind, "version": row.version},
    )
    return ConfigOut.model_validate(row)


@router.get("/internal/policy/{tenant_id}")
def internal_policy(
    tenant_id: str,
    db: Session = Depends(get_session),
    _: None = Depends(require_internal_key),
) -> dict:
    """The tenant's active FRM policy. Detection, the compliance clocks and the metric
    registry all read this, so one board-approved number drives every surface."""
    from ..policy import policy_body
    rows = [r for r in ConfigRepository(db).list(tenant_id, kind="policy")
            if r.status == "active"]
    if not rows:
        return {"tenant_id": tenant_id, "available": False, "board_approved": False,
                "why_not_approved": "no policy configured; platform defaults in use",
                "body": policy_body("commercial_bank")}

    # BR-104. Seeded defaults are a working starting point, not the bank's approved
    # policy, and the two must never look the same. Reported rather than blocked: a
    # tenant with no attested policy still needs working clocks, but every consumer of
    # this endpoint can now see that the thresholds are ours and not theirs.
    body = rows[0].body or {}
    try:
        attested = attestation.validate(body)
        why = ""
    except attestation.AttestationError as exc:
        attested, why = None, str(exc).split(".")[0]
    return {"tenant_id": tenant_id, "available": True,
            "version": rows[0].version, "body": body,
            "board_approved": attested is not None,
            "attestation": attested,
            "why_not_approved": why}


@router.get("/internal/decision-policy/{tenant_id}")
def internal_decision_policy(
    tenant_id: str,
    db: Session = Depends(get_session),
    _: None = Depends(require_internal_key),
) -> dict:
    """The tenant's active Lane A routing policy (BR-316).

    ``available: False`` is not an error - it is every tenant before their first
    decision_policy is activated, and decision-service's own starter policy (every rail
    in shadow, inline rails failing open) is the correct, safe behaviour for that case.
    This endpoint says only what is actually active; the fallback decision lives where
    the consequence of getting it wrong is smallest - decision-service itself, not here.
    """
    rows = [r for r in ConfigRepository(db).list(tenant_id, kind="decision_policy")
            if r.status == "active"]
    if not rows:
        return {"tenant_id": tenant_id, "available": False}
    return {"tenant_id": tenant_id, "available": True,
            "version": rows[0].version, "body": rows[0].body or {}}


@router.get("/internal/model/{tenant_id}/{name}")
def internal_active_model(
    tenant_id: str,
    name: str,
    db: Session = Depends(get_session),
    _: None = Depends(require_internal_key),
) -> dict:
    """The tenant's active model config version *for this named model* (AI/ML roadmap
    Phase 2/3, HLD AD-14/AD-15/§16).

    ``name`` is required - see ``rules.active_model``'s docstring on the analytics side
    for why this is a path segment rather than "the tenant's one active model" the way
    an earlier version of this endpoint read. Two ``model``-kind versions with
    different names (e.g. ``velocity-anomaly`` and ``graph-ring-score``) can be active
    for the same tenant at once; each is looked up by its own name, never by taking
    whichever happens to be returned first.

    ``available: False`` is not an error - it is every tenant before this particular
    named model's first version is activated, and detection's model-scoring step
    correctly reports the indicator it backs as unmeasurable in that case rather than
    inventing a score. The body carries ``model_artifact`` (format, uri, sha256),
    already validated at proposal time by ``model_artifact.validate`` - this endpoint
    returns what was recorded, it does not re-validate it.
    """
    rows = [r for r in ConfigRepository(db).list(tenant_id, kind="model")
            if r.status == "active" and r.name == name]
    if not rows:
        return {"tenant_id": tenant_id, "name": name, "available": False}
    return {"tenant_id": tenant_id, "name": name, "available": True,
            "version": rows[0].version, "body": rows[0].body or {}}


@router.get("/internal/rules/{tenant_id}")
def internal_active_rules(
    tenant_id: str,
    db: Session = Depends(get_session),
    _: None = Depends(require_internal_key),
) -> dict:
    """The tenant's ACTIVE rule configs, keyed by rule id.

    Detection and the dormant-rule register both read this, so the control plane is the
    single source of truth for what is configured - not a hard-coded list in a script.
    """
    rules = {}
    for row in ConfigRepository(db).list(tenant_id, kind="rule"):
        if row.status != "active":
            continue
        body = row.body or {}
        operative = next((b for b in body.get("config", {}).get("bands", [])
                          if b.get("operative")), None)
        rules[row.name] = {
            "rule_id": row.name,
            "version": row.version,
            "family": body.get("family", ""),
            "qualitative": bool(body.get("qualitative")),
            "observed_unit": body.get("config", {}).get("observedUnit", ""),
            "threshold": operative.get("lowerLimit") if operative else None,
            # Older stored configs predate this field; "gte" is the historical behaviour.
            "comparator": body.get("comparator", "gte"),
            "sub_rule_ref": operative.get("subRuleRef") if operative else None,
            "reason": operative.get("reason") if operative else "",
        }
    return {"tenant_id": tenant_id, "count": len(rules), "rules": rules}


@router.get("/internal/entity-types/{entity_type}")
def internal_entity_type(
    entity_type: str,
    _: None = Depends(require_internal_key),
) -> dict:
    """Whether an entity type can extend credit, and the Direction that governs it.

    ``ENTITY_TYPES`` (policy.py) is the single source of truth for this RBI-Direction
    fact - checked against each Direction's own Chapter III text, not assumed. A service
    outside config-service (Lane C's credit-monitoring gate is the first caller) reads it
    here rather than holding its own copy, the same reason ``/internal/rules`` exists
    instead of every service keeping its own rule list.
    """
    from ..policy import ENTITY_TYPES
    info = ENTITY_TYPES.get(entity_type)
    if info is None:
        raise AppError(f"Unknown entity type '{entity_type}'", 404, "unknown_entity_type")
    return {"entity_type": entity_type, "label": info["label"],
            "can_extend_credit": info["can_extend_credit"]}


# --- internal: called by tenant-service (onboarding + export) ---
@router.get("/internal/configs/{tenant_id}", response_model=list[ConfigOut])
def internal_list_configs(
    tenant_id: str,
    db: Session = Depends(get_session),
    _: None = Depends(require_internal_key),
) -> list[ConfigOut]:
    return [ConfigOut.model_validate(r) for r in ConfigRepository(db).list(tenant_id)]


@router.post("/internal/configs/seed", status_code=201)
def seed_defaults(
    payload: SeedRequest,
    db: Session = Depends(get_session),
    _: None = Depends(require_internal_key),
) -> dict:
    repo = ConfigRepository(db)
    created = 0
    for c in default_configs_for(payload.entity_type, payload.ucb_tier):
        if not repo.exists(payload.tenant_id, c["kind"], c["name"], c["version"]):
            repo.create(
                payload.tenant_id, c["kind"], c["name"], c["version"], c["body"], status="active"
            )
            created += 1
    return {"tenant_id": payload.tenant_id, "seeded": created}
