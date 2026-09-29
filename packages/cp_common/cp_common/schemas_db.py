"""Which service owns which tables.

Until now every service shared one Postgres schema. That is the shared-database
anti-pattern: any service could read - and write - any other service's tables, so nothing
but convention stopped analytics from reaching into the tenant register, and a migration
belonging to one service altered a schema four others depend on. Services deployed
separately but were not independent in the only place that matters.

Each service now owns a schema, and a database role that can reach its own schema and
nothing else. Ownership is enforced by Postgres rather than by good intentions.

``PLATFORM`` is the deliberate exception: cross-cutting infrastructure that genuinely
belongs to everyone (the audit trail, the shared cache generation counter). It is the role
Redis or a message bus would play. Keeping it small and explicit is what stops it from
quietly becoming the shared database again.

``CASES`` marks a boundary that is real but not yet a separate deployable. Case management
is a distinct bounded context - write-heavy and transactional, where analytics is
read-heavy and scale-out - and it runs inside ``analytics_service`` today only because
``fact_case`` lives there. Giving it its own schema now makes the seam explicit and
single, so the eventual split is a deployment change rather than an excavation.
"""

PLATFORM = "platform"
TENANT = "tenant"
BRANDING = "branding"
CONFIG = "config"
ANALYTICS = "analytics"
CASES = "cases"
INGESTION = "ingestion"
NOTIFY = "notify"
DECISION = "decision"

ALL = (PLATFORM, TENANT, BRANDING, CONFIG, ANALYTICS, CASES, INGESTION, NOTIFY,
       DECISION)

#: schema -> the service that owns it (and may migrate it).
OWNER = {
    PLATFORM: "platform",           # shared infrastructure, no single owner
    TENANT: "tenant-service",
    BRANDING: "branding-service",
    CONFIG: "config-service",
    ANALYTICS: "analytics-service",
    CASES: "analytics-service",     # until case management is its own deployable
    INGESTION: "ingestion-service",
    NOTIFY: "notification-service",
    DECISION: "decision-service",
}

#: Every service needs the platform schema; beyond that, only its own.
GRANTS = {
    "tenant-service": (TENANT, PLATFORM),
    "branding-service": (BRANDING, PLATFORM),
    "config-service": (CONFIG, PLATFORM),
    "ingestion-service": (INGESTION, PLATFORM),
    "notification-service": (NOTIFY, PLATFORM),
    # The inline lane owns its counters and its decision log and reaches nothing
    # else. Keeping it off the analytics schemas is deliberate: a service in the
    # payment path must not be able to run an expensive query by accident.
    "decision-service": (DECISION, PLATFORM),
    # Analytics reads the inbound queue to project it into facts. This is the one
    # deliberate cross-service grant, and it is the seam a message bus would replace:
    # ingestion writes, analytics consumes, and nothing else touches either side.
    "analytics-service": (ANALYTICS, CASES, INGESTION, PLATFORM),
}


def table_args(schema: str, *args):
    """``__table_args__`` for a model, preserving any indexes or constraints given."""
    return tuple(args) + ({"schema": schema},)
