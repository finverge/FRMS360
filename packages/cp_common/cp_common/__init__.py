"""Shared building blocks for the control-plane microservices."""
from .settings import settings
from .db import Base, get_session, engine, SessionLocal, init_db
from .security import (
    hash_password,
    verify_password,
    create_access_token,
    decode_token,
)
from .auth import (
    Principal,
    get_current_principal,
    get_principal_any_scope,
    require_role,
    require_internal_key,
    require_machine_scope,
    is_machine,
    machine_may,
    MACHINE_SCOPES,
)
from .passwords import validate_password, describe_policy, MIN_LENGTH
from .rbac import ASSIGNABLE_TENANT_ROLES, DASHBOARDS, MODULE_META, ROLES
from . import tenant_status
from .tenancy import resolve_tenant_scope
from .errors import install_error_handlers, AppError
from .audit import AuditLog, record_audit
from .health import database_probe, install_health
from . import saml  # noqa: F401
from .observability import (
    PIPELINE_LAG, QUEUE_DEPTH, REGISTRY, install_observability, request_id_var,
)
from .mfa import (
    generate_backup_codes, hash_backup_code, hash_refresh_token, is_expired,
    mfa_required_for, new_refresh_token, new_totp_secret, normalise_backup_code,
    provisioning_uri, qr_svg, verify_totp,
)
from .idempotency import IdempotencyMiddleware

__all__ = [
    "settings",
    "Base",
    "get_session",
    "engine",
    "SessionLocal",
    "init_db",
    "hash_password",
    "verify_password",
    "create_access_token",
    "decode_token",
    "Principal",
    "get_current_principal",
    "get_principal_any_scope",
    "validate_password",
    "describe_policy",
    "MIN_LENGTH",
    "require_role",
    "require_internal_key",
    "resolve_tenant_scope",
    "tenant_status",
    "install_error_handlers",
    "AppError",
    "AuditLog",
    "install_health",
    "install_observability",
    "saml",
    "REGISTRY",
    "PIPELINE_LAG",
    "QUEUE_DEPTH",
    "request_id_var",
    "new_totp_secret", "provisioning_uri", "qr_svg", "verify_totp",
    "generate_backup_codes", "hash_backup_code", "normalise_backup_code",
    "new_refresh_token", "hash_refresh_token", "is_expired", "mfa_required_for",
    "IdempotencyMiddleware",
    "database_probe",
    "record_audit",
    "ROLES",
    "DASHBOARDS",
    "MODULE_META",
    "ASSIGNABLE_TENANT_ROLES",
]
