"""Idempotent startup seed: ensure a platform admin exists, and optionally a demo tenant."""
import logging

from cp_common import SessionLocal, hash_password, settings

from .models import PlatformUser
from .repositories import TenantRepository
from .schemas import TenantCreate
from .services import TenantService

log = logging.getLogger("tenant-service")


def run_seed() -> None:
    db = SessionLocal()
    try:
        repo = TenantRepository(db)
        if not repo.find_platform_user(settings.seed_admin_email):
            db.add(
                PlatformUser(
                    email=settings.seed_admin_email,
                    password_hash=hash_password(settings.seed_admin_password),
                    role="platform_admin",
                    # The seeded password is a published default — force a change at first
                    # login. Existing installs are untouched (this branch only runs once).
                    must_change_password=settings.seed_admin_must_change_password,
                )
            )
            db.commit()
            log.info("seeded platform admin %s", settings.seed_admin_email)

        if settings.seed_demo_tenant and not repo.get_by_slug("demo-bank"):
            try:
                TenantService(db).onboard(
                    TenantCreate(
                        slug="demo-bank",
                        legal_name="Demo Bank Ltd.",
                        display_name="Demo Bank",
                        admin_email="admin@demo-bank.example.com",
                        admin_password="DemoBank123!",
                        primary_color="#1F4E79",
                        accent_color="#E8A32C",
                    )
                )
                log.info("seeded demo tenant 'demo-bank'")
            except Exception as exc:  # noqa: BLE001
                log.warning("demo tenant seed skipped: %s", exc)
    finally:
        db.close()
