"""Seal secrets already stored in plaintext (BR-712).

Columns became encrypted after these rows were written, so they read through as legacy
plaintext. This rewrites them through the sealed column type.

    python scripts/seal_secrets.py            # report what is still in the clear
    python scripts/seal_secrets.py --commit

Idempotent: a value that is already sealed is left alone, so it is safe to run on a
schedule while a migration is in progress.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from cp_common import SessionLocal, record_audit  # noqa: E402
from cp_common.crypto import PREFIX, available, is_sealed, seal  # noqa: E402

#: (label, table, id column, secret column, AAD) - the AAD must match the column type's
#: context exactly, or the value seals now and fails to read back.
TARGETS = [
    ("platform user MFA seeds", "tenant.platform_users", "id", "mfa_secret",
     "tenant.mfa_secret"),
    ("tenant user MFA seeds", "tenant.tenant_users", "id", "mfa_secret",
     "tenant.mfa_secret"),
    ("IdP client secrets", "tenant.identity_providers", "id", "client_secret",
     "tenant.idp_client_secret"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    if not available():
        raise SystemExit(
            "No encryption key configured. Set CP_ENCRYPTION_KEYS before sealing, or "
            "the rows would be rewritten unchanged and still be plaintext.")

    db = SessionLocal()
    total_plain = total_sealed = 0
    try:
        for label, table, idcol, col, aad in TARGETS:
            try:
                rows = db.execute(text(
                    f"SELECT {idcol}, {col} FROM {table} "
                    f"WHERE {col} IS NOT NULL AND {col} <> ''")).all()
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                print(f"  {label:<26} could not read: {type(exc).__name__}")
                continue

            plain = [(i, v) for i, v in rows if not is_sealed(v)]
            total_plain += len(plain)
            print(f"  {label:<26} {len(rows):>4} rows, {len(plain):>4} in the clear")
            if not args.commit or not plain:
                continue
            for ident, value in plain:
                db.execute(
                    text(f"UPDATE {table} SET {col} = :v WHERE {idcol} = :i"),
                    {"v": seal(str(value), aad=aad), "i": ident})
            db.commit()
            total_sealed += len(plain)
            record_audit(
                service="platform", action="secrets.sealed", actor="system:seal-secrets",
                actor_role="system", tenant_id=None, target_type="column",
                target_id=f"{table}.{col}", status="success",
                detail={"rows_sealed": len(plain)})
    finally:
        db.close()

    if args.commit:
        print(f"\nsealed {total_sealed} value(s).")
    else:
        print(f"\n{total_plain} value(s) still in the clear. "
              f"Re-run with --commit to seal them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
