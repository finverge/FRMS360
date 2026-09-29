"""Alembic environment. Aggregates the metadata from every service so a single
`alembic revision --autogenerate` covers the whole control-plane schema.

Microservice note: these tables live in one shared PostgreSQL database with
logical tenant_id scoping. There are deliberately NO cross-service foreign keys
(e.g. branding.tenant_id does not FK to tenants.id) so services stay independently
deployable — referential integrity is enforced in the application layer.
"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Every service shares the single declarative Base from cp_common, so importing each
# service's models module is what registers its tables on that shared metadata. The audit
# table is owned by cp_common itself rather than by any one service.
from cp_common import Base  # noqa: E402
from cp_common import audit  # noqa: F401,E402  (registers audit_logs)
from services.tenant_service.app import models as tenant_models  # noqa: F401,E402
from services.branding_service.app import models as branding_models  # noqa: F401,E402
from services.config_service.app import models as config_models  # noqa: F401,E402
from services.analytics_service.app import models as analytics_models  # noqa: F401,E402
from services.analytics_service.app import views_model as analytics_views  # noqa: F401,E402
from services.analytics_service.app import documents_model as analytics_docs  # noqa: F401,E402
from services.analytics_service.app import subscriptions_model as analytics_subs  # noqa: F401,E402
from services.analytics_service.app import workflow_model as analytics_workflow  # noqa: F401,E402
from cp_common import cache_model as platform_cache  # noqa: F401,E402
from cp_common import idempotency as platform_idem  # noqa: F401,E402
from services.ingestion_service.app import models as ingestion_models  # noqa: F401,E402
from services.tenant_service.app import auth_models as tenant_auth  # noqa: F401,E402
from services.tenant_service.app import idp_models as tenant_idp  # noqa: F401,E402
from services.notification_service.app import models as notify_models  # noqa: F401,E402
from services.analytics_service.app import filings_model as analytics_filings  # noqa: F401,E402
from services.analytics_service.app import accountability_model as analytics_accountability  # noqa: F401,E402
from services.analytics_service.app import board_pack_model as analytics_board_pack  # noqa: F401,E402
from services.analytics_service.app import lea_model as analytics_lea  # noqa: F401,E402
from services.decision_service.app import models as decision_models  # noqa: F401,E402
from services.tenant_service.app import service_credentials as tenant_svc_creds  # noqa: F401,E402
from services.analytics_service.app import reference_model as analytics_reference  # noqa: F401,E402
from services.ingestion_service.app import file_model as ingestion_files  # noqa: F401,E402
from services.ingestion_service.app import cbs_models as ingestion_cbs  # noqa: F401,E402
from services.analytics_service.app import reference_source_model as analytics_refsrc  # noqa: F401,E402
from services.analytics_service.app import usage_model as analytics_usage  # noqa: F401,E402
from services.analytics_service.app import ctr_model as analytics_ctr  # noqa: F401,E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option(
    "sqlalchemy.url",
    os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg2://cp:cp_password@localhost:5432/controlplane",
    ),
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
            # Each service owns a schema; autogenerate must look in all of them or it
            # would propose dropping every table it cannot see.
            include_schemas=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
