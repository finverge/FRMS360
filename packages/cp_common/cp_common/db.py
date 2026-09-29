"""Database engine, session factory, and a FastAPI session dependency.

Each service defines its own models on the shared ``Base`` re-exported here, and
calls ``init_db(Base.metadata)`` at startup (dev only) to create its own tables.
"""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .settings import settings


class Base(DeclarativeBase):
    """Declarative base. Each service imports and subclasses this."""


def _engine_kwargs() -> dict:
    """Pin the connection's ``search_path`` to the schemas this service owns.

    Each service owns a schema (see cp_common.schemas_db), so an unqualified table name
    has to resolve somewhere. Setting search_path per service means the large body of
    hand-written analytical SQL keeps working unchanged, and - more usefully - a service
    that reaches for a table it does not own gets "relation does not exist" rather than
    silently reading another service's data.

    Note what this is and is not: search_path governs *name resolution*, not permission.
    Isolation is enforced by the per-service database roles and their grants; this makes
    the boundary visible in development, where everything still runs as one role.
    """
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if settings.db_search_path:
        kwargs["connect_args"] = {
            "options": "-csearch_path=" + settings.db_search_path.replace(" ", "")
        }
    return kwargs


engine = create_engine(settings.database_url, **_engine_kwargs())
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session per request."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db(metadata) -> None:
    """Create tables for the given metadata (dev convenience; use Alembic in prod)."""
    if settings.auto_create_tables:
        metadata.create_all(bind=engine)
