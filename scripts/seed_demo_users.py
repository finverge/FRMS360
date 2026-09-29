"""Create one demo user per persona role on a tenant, with usable credentials.

Users are created through the real API, so the invite flow (temporary password +
forced reset) genuinely runs. The script then completes the password change for each
user, which both exercises the reset path and leaves you with known credentials.

Usage:  python scripts/seed_demo_users.py [--tenant hdfc-demo]
"""
import argparse
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.environ.get("GATEWAY_URL", "http://localhost:8080")
ADMIN_EMAIL = os.environ.get("SEED_ADMIN_EMAIL", "admin@finverge.local")
ADMIN_PW = os.environ.get("SEED_ADMIN_PASSWORD", "ChangeMe123!")

# Passwords deliberately avoid the email local-part (the policy rejects that) and
# satisfy: 12+ chars, upper, lower, digit, symbol.
DEMO_USERS = [
    ("fraud.ops@meridian.example.com",   "analyst",          "Kestrel#8823Qm"),
    ("investigations@meridian.example.com", "investigator",  "Falcon#7742Wd"),
    ("frm.head@meridian.example.com",    "risk_manager",     "Compass#6614Nj"),
    ("po.aml@meridian.example.com",      "principal_officer","Beacon#9927Rt"),
    ("compliance@meridian.example.com",  "supervisor",       "Lantern#3317Zb"),
    ("exec.risk@meridian.example.com",   "board",            "Harbour#5590Vt"),
    ("cro.office@meridian.example.com",  "cro",              "Summit#2286Lp"),
    ("model.risk@meridian.example.com",  "data_scientist",   "Quartz#4438Yh"),
    ("audit.rbi@meridian.example.com",   "rbi_inspector",    "Ledger#1175Gx"),
    ("bank.admin@meridian.example.com",  "tenant_admin",     "Meridian#4471Kx"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="hdfc-demo")
    args = ap.parse_args()

    c = httpx.Client(base_url=BASE, timeout=30.0)
    tok = c.post("/api/auth/login",
                 json={"email": ADMIN_EMAIL, "password": ADMIN_PW}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    tenants = c.get("/api/tenants", headers=h).json()
    tenant = next((t for t in tenants if t["slug"] == args.tenant), None)
    if not tenant:
        print(f"Tenant '{args.tenant}' not found. Available: {[t['slug'] for t in tenants]}")
        return
    tid = tenant["id"]
    print(f"tenant: {tenant['display_name']} ({tenant['slug']})  id={tid}\n")

    existing = {u["email"] for u in c.get(f"/api/tenants/{tid}/users", headers=h).json()}
    results = []

    for email, role, final_pw in DEMO_USERS:
        if email in existing:
            print(f"  {email:<34} already exists - skipped")
            results.append((email, role, "(unchanged)"))
            continue

        r = c.post(f"/api/tenants/{tid}/users", headers=h, json={"email": email, "role": role})
        if r.status_code != 201:
            print(f"  {email:<34} FAILED {r.status_code}: {r.text[:120]}")
            continue
        temp_pw = r.json()["temp_password"]

        # Log in with the temp password -> reset-scoped token -> change password.
        login = c.post("/api/auth/login", json={"email": email, "password": temp_pw}).json()
        assert login.get("must_change_password") is True, "expected forced reset"
        rt = {"Authorization": f"Bearer {login['access_token']}"}
        ch = c.post("/api/auth/change-password", headers=rt,
                    json={"current_password": temp_pw, "new_password": final_pw})
        if ch.status_code != 200:
            print(f"  {email:<34} password set FAILED: {ch.text[:160]}")
            continue

        verify = c.post("/api/auth/login", json={"email": email, "password": final_pw})
        ok = verify.status_code == 200 and verify.json()["must_change_password"] is False
        print(f"  {email:<34} role={role:<13} {'ready' if ok else 'CHECK'}")
        results.append((email, role, final_pw))

    print("\n" + "=" * 78)
    print(f"DEMO CREDENTIALS  -  tenant '{tenant['slug']}'   console: {BASE}")
    print("=" * 78)
    print(f"  {'email':<34}{'role':<15}{'password'}")
    for email, role, pw in results:
        print(f"  {email:<34}{role:<15}{pw}")
    print(f"\n  {ADMIN_EMAIL:<34}{'platform_admin':<15}{ADMIN_PW}")
    print("=" * 78)


if __name__ == "__main__":
    main()
