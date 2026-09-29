"""Materialise the role catalogue for tenants onboarded before BR-113 (phase 2a).

Every tenant onboarded from here on gets its own ``tenant_roles`` rows automatically
(services/tenant_service/app/services.py). A tenant onboarded before this change has
none, and BR-112's viewer falls back to the hardcoded catalogue for it - correct, but
it means that tenant is not yet on the dynamic path this script exists to migrate it
onto.

Idempotent: seed_default_roles() skips any role name already present, so running this
twice - or running it, then having a tenant reach onboarding's own seeding call again
some other way - never duplicates a row or clobbers one a tenant has since edited.

    python scripts/backfill_tenant_roles.py [--commit]

Runs as a dry run unless --commit is passed.
"""
import argparse
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from cp_common.db import SessionLocal  # noqa: E402
from services.tenant_service.app.models import Tenant  # noqa: E402
from services.tenant_service.app.roles import seed_default_roles  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true",
                    help="write the changes (otherwise a dry run)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        tenants = list(db.scalars(select(Tenant)))
        total_created = 0
        for tenant in tenants:
            created = seed_default_roles(db, tenant.id)
            if created:
                total_created += len(created)
                print(f"  {tenant.slug}: {len(created)} role(s) materialised "
                      f"({', '.join(r.name for r in created)})")
            else:
                print(f"  {tenant.slug}: already on the dynamic path - nothing to do")

        if args.commit:
            db.commit()
            print(f"\ncommitted: {total_created} role row(s) created across "
                  f"{len(tenants)} tenant(s)")
        else:
            db.rollback()
            print(f"\ndry run: would create {total_created} role row(s) across "
                  f"{len(tenants)} tenant(s). Re-run with --commit to apply.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
