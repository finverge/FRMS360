"""Point a tenant at the demo identity provider, for a federated-sign-in demonstration.

    python scripts/seed_demo_idp.py --tenant demo-bank

Then start the demo provider and sign in with the organisation short name:

    DEMO_IDP_ENABLED=true python -m uvicorn services.demo_idp.app.main:app --port 8086

The group-to-role mapping below is the interesting part of the demo. It is what a bank
fills in for its own directory, and it is why an IdP is trusted to say *who* someone is
without being trusted to say *what they may do here*.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from cp_common.db import SessionLocal  # noqa: E402
from services.tenant_service.app.idp_models import IdentityProvider  # noqa: E402
from services.tenant_service.app.models import Tenant  # noqa: E402

SLUG = "demo-directory"

#: What a bank's Active Directory / Entra groups would be, mapped to platform roles.
ROLE_MAPPING = {
    "FRAUD-ANALYSTS": "analyst",
    "FRAUD-INVESTIGATORS": "investigator",
    "FRAUD-RISK-MANAGERS": "risk_manager",
    "AML-PRINCIPAL-OFFICER": "principal_officer",
    "COMPLIANCE-SUPERVISORS": "supervisor",
    "BOARD": "board",
    "MODEL-RISK": "data_scientist",
    "INTERNAL-AUDIT": "rbi_inspector",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True, help="tenant slug, e.g. demo-bank")
    ap.add_argument("--issuer", default="http://127.0.0.1:8086")
    ap.add_argument("--client-id", default="frms-console")
    ap.add_argument("--client-secret", default="demo-secret")
    ap.add_argument("--disable", action="store_true",
                    help="Remove the demo provider from this tenant.")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == args.tenant))
        if tenant is None:
            print(f"No tenant with slug '{args.tenant}'.")
            return 1

        existing = db.scalar(select(IdentityProvider).where(
            IdentityProvider.tenant_id == tenant.id, IdentityProvider.slug == SLUG))

        if args.disable:
            if existing:
                db.delete(existing)
                db.commit()
                print(f"Removed the demo provider from {tenant.display_name}.")
            else:
                print("Nothing to remove.")
            return 0

        fields = dict(
            display_name="Demo Directory (test IdP)",
            protocol="oidc",
            issuer=args.issuer,
            client_id=args.client_id,
            client_secret=args.client_secret,
            discovery_url=f"{args.issuer}/.well-known/openid-configuration",
            scopes="openid email profile groups",
            email_claim="email",
            groups_claim="groups",
            role_mapping=ROLE_MAPPING,
            # Least privilege when no group matches - a directory group nobody has
            # mapped must not confer authority by accident.
            default_role="analyst",
            jit_provisioning=True,
            enabled=True,
        )
        if existing:
            for k, v in fields.items():
                setattr(existing, k, v)
            action = "Updated"
        else:
            db.add(IdentityProvider(tenant_id=tenant.id, slug=SLUG, **fields))
            action = "Configured"
        db.commit()

        print(f"{action} federated sign-in for {tenant.display_name} ({tenant.slug}).\n")
        print("  1. Start the demo provider:")
        print("       DEMO_IDP_ENABLED=true python -m uvicorn "
              "services.demo_idp.app.main:app --port 8086")
        print("  2. On the sign-in page, enter the organisation short name:")
        print(f"       {tenant.slug}")
        print("  3. Choose 'Continue with Demo Directory', then pick a persona.\n")
        print("  Group mapping in force:")
        for group, role in ROLE_MAPPING.items():
            print(f"       {group:<26} -> {role}")
        print("\n  The demo provider checks no credentials. It exists to exercise the "
              "real\n  OIDC path; a bank's own provider is configured the same way, "
              "with its own\n  issuer, client and groups.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
