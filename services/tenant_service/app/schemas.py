"""DTO tier — request/response contracts (Pydantic)."""
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class TenantCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{1,62}$", examples=["hdfc-demo"])
    legal_name: str = Field(examples=["HDFC Bank Ltd."])
    display_name: str = Field(examples=["HDFC Bank"])
    region: str = Field(default="in-mumbai")
    plan: str = Field(default="standard")
    entity_type: str = Field(
        default="commercial_bank",
        pattern=r"^(commercial_bank|aifi|urban_cooperative|state_cooperative|"
                r"central_cooperative|rrb|local_area_bank|small_finance_bank|"
                r"payments_bank|nbfc|hfc)$",
        description="Determines which RBI Master Direction governs this tenant. "
                    "Kept in sync with config_service/app/policy.py:ENTITY_TYPES by "
                    "hand - tenant_service does not import config_service's code "
                    "across the service boundary.")
    ucb_tier: int | None = Field(default=None, ge=1, le=4,
                                 description="RBI four-tier framework, UCBs only")
    admin_email: EmailStr = Field(examples=["ciso@hdfc.example"])
    admin_password: str = Field(min_length=8)
    # Optional initial branding applied during onboarding.
    primary_color: str | None = Field(default=None, examples=["#004C8F"])
    accent_color: str | None = Field(default=None, examples=["#ED232A"])


class TenantOut(BaseModel):
    id: str
    slug: str
    legal_name: str
    display_name: str
    region: str
    plan: str
    entity_type: str
    ucb_tier: int | None
    status: str
    is_sandbox: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    # Plain str, not EmailStr: login identifiers must accept internal/dev domains
    # (e.g. *.local) that strict email validation rejects.
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    tenant_id: str | None = None
    # When true the token is password_reset-scoped: the client must call
    # /auth/change-password before anything else will work.
    must_change_password: bool = False
    # Present once, at the moment a session opens. Stored by the client and exchanged for
    # a new access token; never returned again.
    refresh_token: str | None = None
    expires_in: int | None = None
    # The token is mfa_pending-scoped: the client must call /auth/mfa/verify next.
    mfa_required: bool = False
    # The user must enrol a second factor before they can be let in at all.
    mfa_enrolment_required: bool = False


class MfaVerify(BaseModel):
    # Either a six-digit authenticator code or a recovery code.
    code: str


class MfaActivate(BaseModel):
    code: str


class MfaDisable(BaseModel):
    password: str
    code: str


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class UserInvite(BaseModel):
    email: str = Field(examples=["ops@bank.example"])
    # Any of the ten fixed roles, or (BR-113) a custom role this tenant has created.
    # The schema only enforces the slug shape; the route checks the name is actually
    # real for this tenant and is never "platform_admin" - see roles.assignable_names.
    role: str = Field(default="analyst", pattern=r"^[a-z][a-z0-9_]{1,31}$",
                      examples=["analyst"])


class UserOut(BaseModel):
    id: str
    email: str
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class UserInviteResult(UserOut):
    """Returned once on invite.

    ``emailed`` tells the inviting admin what actually happened: if the tenant has a
    working email channel configured, the credential goes to the invitee directly and
    ``temp_password`` is withheld here - no reason to show it twice, once by mail and
    once on someone else's screen. If there is no channel configured, or the send
    failed, the fallback is the original behaviour: the password is returned once so
    the admin can relay it another way, and ``emailed`` says why.
    """
    temp_password: str | None
    emailed: bool


class UserUpdate(BaseModel):
    """Reassign a user's role. Deliberately the only mutable field: email is a login
    identifier, not something to silently swap under a session, and for an
    SSO-federated user the directory already re-syncs role on every login (see
    routes/sso.py::_complete_sso) - this exists for local-password users and for
    changing a federated user's role between logins, not to compete with that sync."""
    role: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$", examples=["risk_manager"])


class IdpCreate(BaseModel):
    slug: str = Field(examples=["entra-corp"])
    display_name: str = Field(examples=["Corporate Entra ID"])
    protocol: str = Field(default="oidc", pattern=r"^(oidc|saml)$")
    issuer: str = Field(default="")
    client_id: str = Field(default="")
    # Write-only in every direction: never returned by the API, so a create always
    # takes it fresh rather than round-tripping something the client never saw.
    client_secret: str = Field(default="")
    discovery_url: str = Field(default="")
    authorize_url: str = Field(default="")
    token_url: str = Field(default="")
    jwks_url: str = Field(default="")
    scopes: str = Field(default="openid email profile")
    saml_sso_url: str = Field(default="")
    saml_certificate: str = Field(default="")
    saml_email_attribute: str = Field(default="")
    saml_groups_attribute: str = Field(default="")
    email_claim: str = Field(default="email")
    groups_claim: str = Field(default="groups")
    # {"FRAUD-ANALYSTS": "analyst", ...}. Validated against ASSIGNABLE_TENANT_ROLES at
    # the route, not here - the schema does not know the role catalogue.
    role_mapping: dict[str, str] = Field(default_factory=dict)
    default_role: str = Field(default="analyst")
    jit_provisioning: bool = Field(default=True)
    enabled: bool = Field(default=True)


class IdpUpdate(BaseModel):
    """Every field optional: only what is set is changed.

    ``client_secret`` and ``saml_certificate`` are the one exception to "set means
    change" - an empty string on those two means *leave as it is*, because the field is
    never pre-filled from the server and the console cannot distinguish "untouched"
    from "cleared" any other way. See the route.
    """
    slug: str | None = None
    display_name: str | None = None
    protocol: str | None = Field(default=None, pattern=r"^(oidc|saml)$")
    issuer: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    discovery_url: str | None = None
    authorize_url: str | None = None
    token_url: str | None = None
    jwks_url: str | None = None
    scopes: str | None = None
    saml_sso_url: str | None = None
    saml_certificate: str | None = None
    saml_email_attribute: str | None = None
    saml_groups_attribute: str | None = None
    email_claim: str | None = None
    groups_claim: str | None = None
    role_mapping: dict[str, str] | None = None
    default_role: str | None = None
    jit_provisioning: bool | None = None
    enabled: bool | None = None


class RoleOut(BaseModel):
    """BR-112/BR-113: a role in this tenant's catalogue - the fixed ten, or one the
    tenant has created or edited itself."""
    name: str
    label: str
    modules: list[dict]
    dashboards: list[dict]
    can_admin_tenant: bool
    can_reveal_pii: bool
    # May propose or confirm a configuration activation (BR-715) - the
    # risk_manager-equivalent grant, narrower than can_admin_tenant.
    can_activate_config: bool = False
    member_count: int
    # "seeded" for an unedited copy of the platform catalogue, "custom" for a
    # tenant-created role or one the tenant has since edited. Absent (None) for a role
    # still served from the hardcoded fallback (no tenant_roles row exists at all).
    source: str | None = None
    # True while a can_admin_tenant/can_reveal_pii/can_activate_config grant is staged
    # awaiting a second, different eligible actor's confirmation (BR-715-style
    # maker-checker).
    elevation_pending: bool = False


class RoleCreate(BaseModel):
    # Deliberately not the fixed-role enum UserInvite.role used to carry — this is
    # what makes a *new* name possible. RESERVED_NAMES (checked at the route) is the
    # actual floor against shadowing a platform role.
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$", examples=["regional_fraud_lead"])
    label: str = Field(min_length=1, max_length=120, examples=["Regional Fraud Lead"])
    modules: list[str] = Field(default_factory=list)
    dashboards: list[str] = Field(default_factory=list)
    can_admin_tenant: bool = False
    can_reveal_pii: bool = False
    can_activate_config: bool = False


class RoleUpdate(BaseModel):
    """Every field optional: only what is set is changed. ``name`` is absent by
    design - see roles.py's module docstring on why renaming is out of scope."""
    label: str | None = None
    modules: list[str] | None = None
    dashboards: list[str] | None = None
    can_admin_tenant: bool | None = None
    can_reveal_pii: bool | None = None
    can_activate_config: bool | None = None


class AuditOut(BaseModel):
    id: str
    ts: datetime
    service: str
    actor: str
    actor_role: str
    tenant_id: str
    action: str
    target_type: str
    target_id: str
    status: str
    detail: dict

    model_config = {"from_attributes": True}
