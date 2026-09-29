"""Seed realistic multi-tenant test data through the gateway (end-to-end path).

Idempotent: existing tenants (409) are skipped. Exercises onboarding orchestration,
branding provisioning, default config seeding, plus an extra config version + activation
so version history is visible. Every call is audited by the services.

Usage:  python scripts/api_seed.py
Env:    GATEWAY_URL (default http://localhost:8080), SEED_ADMIN_EMAIL, SEED_ADMIN_PASSWORD
"""
import os

import httpx

BASE = os.environ.get("GATEWAY_URL", "http://localhost:8080")
ADMIN_EMAIL = os.environ.get("SEED_ADMIN_EMAIL", "admin@finverge.local")
ADMIN_PW = os.environ.get("SEED_ADMIN_PASSWORD", "ChangeMe123!")

TENANTS = [
    {"slug": "demo-bank", "legal_name": "Demo Bank Ltd.", "display_name": "Demo Bank",
     "admin_email": "admin@demo-bank.example.com", "admin_password": "DemoBank123!",
     "primary_color": "#1F4E79", "accent_color": "#E8A32C"},
    {"slug": "hdfc-demo", "legal_name": "HDFC Bank Ltd. (demo)", "display_name": "HDFC Bank",
     "admin_email": "ciso@hdfc.example.com", "admin_password": "HdfcDemo123!",
     "primary_color": "#004C8F", "accent_color": "#ED232A"},
    {"slug": "icici-demo", "legal_name": "ICICI Bank Ltd. (demo)", "display_name": "ICICI Bank",
     "admin_email": "ciso@icici.example.com", "admin_password": "IciciDemo123!",
     "primary_color": "#B02A30", "accent_color": "#F58220"},
]


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=20.0)

    r = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PW})
    r.raise_for_status()
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    print(f"logged in as {ADMIN_EMAIL}")

    created = {}
    for t in TENANTS:
        resp = c.post("/api/tenants", json=t, headers=h)
        if resp.status_code == 201:
            body = resp.json()
            created[t["slug"]] = body["id"]
            print(f"  onboarded {t['slug']:12s} -> {body['id']}  status={body['status']}")
        elif resp.status_code == 409:
            print(f"  {t['slug']:12s} already exists (skipped)")
        else:
            print(f"  {t['slug']:12s} FAILED {resp.status_code}: {resp.text}")

    # Demonstrate config versioning + activation on the first tenant.
    tenants = c.get("/api/tenants", headers=h).json()
    target = next((x for x in tenants if x["slug"] == "hdfc-demo"), None)
    if target:
        tid = target["id"]
        body = {
            "kind": "typology", "name": "mule-layering", "version": "1.1.0",
            "body": {"id": "mule-layering@1.1.0", "expression": "t018 + t024 + t030 + t044",
                     "workflow": {"alertThreshold": 130, "interdictionThreshold": 280}},
        }
        r = c.post(f"/api/configs/{tid}", json=body, headers=h)
        if r.status_code == 201:
            cid = r.json()["id"]
            # BR-715: activation is maker-checker. This call only *proposes* it - a
            # second, different eligible actor (e.g. hdfc-demo's own tenant_admin, via
            # the console) must confirm before it is actually live. Not chained here:
            # that confirmer normally needs its own MFA step, which is out of scope for
            # a data-seeding script.
            c.post(f"/api/configs/{tid}/{cid}/activate", headers=h)
            print(f"  hdfc-demo: added mule-layering v1.1.0, activation proposed "
                  f"(needs a second eligible actor to confirm)")
        elif r.status_code == 409:
            print("  hdfc-demo: mule-layering v1.1.0 already present")

    print("\nseed complete.")


if __name__ == "__main__":
    main()
