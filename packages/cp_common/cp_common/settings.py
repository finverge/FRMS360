"""Centralised, env-driven configuration shared by every service."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    platform_name: str = "Fraud360"

    database_url: str = "postgresql+psycopg2://cp:cp_password@localhost:5432/controlplane"
    # Schemas this service resolves unqualified table names against, most specific first.
    # The default spans every schema so a single-process dev run and the test suite work;
    # each service narrows it in run_local.ps1, and the database roles are what actually
    # enforce the boundary. See cp_common.schemas_db.
    db_search_path: str = "analytics,cases,tenant,branding,config,platform,public"

    jwt_secret: str = "dev-secret-change-me"
    # Access tokens are unrevocable by design, so they are short. Revocation happens on
    # the session (see cp_common.mfa); this number is how long a revoked user can still
    # act, and 8 hours was far too long for that.
    jwt_expire_minutes: int = 30
    # Absolute lifetime of a session. A refresh beyond this needs a fresh sign-in.
    refresh_token_days: int = 7
    # Idle timeout, applied on refresh. Distinct from absolute expiry: a session unused
    # over a long weekend should end even though its 7 days have not run out.
    session_idle_minutes: int = 240
    # Consecutive failures before an account is locked, and for how long. Slows credential
    # stuffing without giving an attacker a cheap way to lock out a named user for a day.
    max_failed_attempts: int = 5
    lockout_minutes: int = 15
    # Label shown in authenticator apps.
    mfa_issuer: str = "Fraud360"
    jwt_algorithm: str = "HS256"

    internal_api_key: str = "dev-internal-key"

    # AES-256-GCM key material for secrets sealed at rest (BR-712), as
    # "version:base64-of-32-bytes" entries. Empty means no key is configured, and
    # writing a secret then raises unless CP_ALLOW_PLAINTEXT_SECRETS is set.
    cp_encryption_keys: str = ""

    tenant_service_url: str = "http://localhost:8081"
    branding_service_url: str = "http://localhost:8082"
    config_service_url: str = "http://localhost:8083"
    analytics_service_url: str = "http://localhost:8084"
    ingestion_service_url: str = "http://127.0.0.1:8085"
    notification_service_url: str = "http://127.0.0.1:8087"
    # Lane A. Empty disables counter publishing entirely, which is a valid
    # deployment: a tenant with no inline rails needs no counters.
    decision_service_url: str = "http://127.0.0.1:8086"
    lane_c_service_url: str = "http://127.0.0.1:8088"
    #: Where uploaded financial-statement PDFs are archived, named by SHA-256 so a
    #: resubmission overwrites nothing and is trivially recognised. Disk, not a Postgres
    #: blob - the same reason ingestion's own batch files (file_model.py) live on disk
    #: with only metadata in the database.
    lane_c_storage_dir: str = "./data/lane_c_statements"
    # Where a browser reaches the console. Federated sign-in has to send the user back
    # to this origin, not to whichever internal address happened to serve the request:
    # request.url_for() resolves to the service's own host, so behind the gateway the
    # browser was redirected to tenant-service directly and never reached the console.
    console_base_url: str = "http://127.0.0.1:8080"
    # Analytical store backend. 'postgres' today; a ClickHouse engine slots in behind
    # the same QueryEngine interface without touching dashboards or metrics.
    analytics_engine: str = "postgres"

    # Cross-replica configuration cache. 'database' is correct behind a load balancer;
    # 'memory' is process-local and is refused at startup when app_replicas > 1.
    cache_backend: str = "database"
    # How many replicas of this service are being run. Used to reject a cache backend
    # that cannot keep them consistent - see cp_common.cache.verify_backend_supports.
    app_replicas: int = 1
    # How long a replica may reuse a previously-read generation token before checking
    # again. ZERO IS THE CORRECT DEFAULT: any non-zero value re-opens exactly the bug this
    # mechanism exists to close, just with a shorter window - for that many seconds two
    # replicas can still be serving different generations of a tenant's configuration.
    # The read it saves is a primary-key lookup on a single-row table, which is noise next
    # to the analytical queries it guards. Raise it only under measured read pressure, and
    # only knowing the divergence window you are buying.
    cache_generation_ttl_seconds: float = 0.0

    # Edge rate limit, per caller, enforced in each gateway replica (see
    # services.gateway.app.resilience.RateLimiter for why it is not shared).
    rate_limit_per_second: float = 20.0
    rate_limit_burst: int = 40

    auto_create_tables: bool = True
    seed_admin_email: str = "admin@finverge.local"
    seed_admin_password: str = "ChangeMe123!"
    # Force the seeded admin to change the published default password at first login.
    # Set false only for throwaway local dev / automated tests.
    seed_admin_must_change_password: bool = True
    seed_demo_tenant: bool = True

    # --- ISO 8583 gateway (Lane A protocol adapter, docs/ISO8583_LANE_A_SCOPING.md) ---
    # Proposal-stage component: a TCP front door that maps ISO 8583 authorisation
    # messages onto the same POST /decide contract every JSON Lane A integrator uses. It
    # authenticates as an ordinary machine credential - see §3.2 of the integration spec
    # - not through any special internal path.
    iso8583_gateway_host: str = "0.0.0.0"
    iso8583_gateway_port: int = 8588
    iso8583_client_id: str = ""
    iso8583_client_secret: str = ""
    iso8583_tenant_id: str = ""
    # DE7 (transmission date & time) never carries a year or an offset - see
    # mapper._resolve_transmission_ts. This is *this gateway's* fixed interpretation of
    # every DE7 it receives, not a protocol default; +330 (IST) is correct only because
    # every pilot switch is presumed India-based. A multi-region deployment would need
    # this per connection, not process-wide - out of scope until that need is real.
    iso8583_tz_offset_minutes: int = 330
    # No safe default, deliberately - mirrors RailPolicy.fail's "" meaning "must be set
    # explicitly" (decision_service/app/policy.py). Whether a switch that gets no answer
    # from /decide should treat that as approve or as issuer-inoperative is the bank's
    # call, not this gateway's, and is called out as an explicit go-live decision in
    # docs/ISO8583_LANE_A_SCOPING.md §6.
    iso8583_fail_open: bool | None = None

    # Optional, off-by-default LLM rephrasing of the already-evidence-built regulatory
    # filing narrative (analytics_service/app/filings/narrative_polish.py). Points at a
    # local Ollama instance by default - no vendor key required, nothing leaves the
    # tenant's own infrastructure. See that module's docstring for why an LLM is allowed
    # near a regulatory filing at all: it only ever rephrases, never adds a fact, and a
    # human always reviews before anything is stored.
    narrative_polish_enabled: bool = False
    narrative_llm_base_url: str = "http://localhost:11434/v1"
    narrative_llm_model: str = "oria-qwen2.5vl-12k"

    # Optional, off-by-default LLM read of a Lane C statement's extracted text, looking
    # for qualitative red flags (RBI #38/#39) no ratio or regex can catch - see
    # lane_c_service/app/signals/qualitative_red_flags.py. Same local-model, no-vendor-key
    # posture as narrative_polish above, and the same fact-verification discipline: a
    # finding is kept only if its quoted source sentence is actually present in the
    # statement text, never trusted on the model's word alone.
    lane_c_llm_enabled: bool = False
    lane_c_llm_base_url: str = "http://localhost:11434/v1"
    lane_c_llm_model: str = "oria-qwen2.5vl-12k"
    lane_c_llm_timeout_s: float = 60.0


settings = Settings()
