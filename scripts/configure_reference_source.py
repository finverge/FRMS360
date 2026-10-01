"""Create or update a reference-feed source, for either mechanism (Lane B's
transaction-screening feeds, or Lane C's own periodic-borrower feeds).

No script anywhere in this codebase did this before - every ReferenceSource/
LaneCReferenceSource row that existed prior to this was created directly via SQLAlchemy,
by a test. This is the actual "config change, not a rebuild" step: the day a CERSAI, MCA,
or ratings vendor contract is signed, this is the one command that turns a set of
credentials into a working feed.

Idempotent by (tenant, kind): re-running with new values updates the existing source
rather than erroring, so rotating a token or changing a URL is the same command as
creating the source in the first place.

    # Lane B - e.g. CERSAI, once production access exists
    python scripts/configure_reference_source.py --service lane-b --tenant <tid> \\
        --kind cersai_charges --url https://cersai.example/charges.json --fmt json \\
        --key-field asset_id --auth-header Authorization --auth-value "Bearer ..." \\
        --cadence-hours 24

    # Lane C - e.g. MCA/ROC, once a data vendor is chosen
    python scripts/configure_reference_source.py --service lane-c --tenant <tid> \\
        --kind mca_roc --url https://vendor.example/roc-liabilities.json --fmt json \\
        --key-field cin

    # Disable a source without deleting its history
    python scripts/configure_reference_source.py --service lane-c --tenant <tid> \\
        --kind rating_action --disable
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cp_common import SessionLocal  # noqa: E402


def _lane_b(db, args) -> None:
    from services.analytics_service.app.reference_model import LIST_KINDS
    from services.analytics_service.app.reference_source_model import (
        FORMATS, ReferenceSource,
    )
    _configure(db, ReferenceSource, LIST_KINDS, FORMATS, args)


def _lane_c(db, args) -> None:
    from services.lane_c_service.app.reference_model import LANE_C_LIST_KINDS
    from services.lane_c_service.app.reference_source_model import (
        FORMATS, LaneCReferenceSource,
    )
    _configure(db, LaneCReferenceSource, LANE_C_LIST_KINDS, FORMATS, args)


def _configure(db, model, list_kinds: dict, formats: tuple, args) -> None:
    if args.kind not in list_kinds:
        raise SystemExit(
            f"unknown kind '{args.kind}' for this service - known kinds: "
            f"{', '.join(sorted(list_kinds))}")

    existing = db.query(model).filter(
        model.tenant_id == args.tenant, model.kind == args.kind).one_or_none()

    if args.disable:
        if existing is None:
            raise SystemExit(f"no source configured for ({args.tenant}, {args.kind}) "
                             "to disable")
        existing.enabled = False
        db.commit()
        print(f"disabled {args.kind} for tenant {args.tenant[:8]}")
        return

    if not args.url:
        raise SystemExit("--url is required unless --disable is given")

    if existing is None:
        existing = model(tenant_id=args.tenant, kind=args.kind)
        db.add(existing)
        action = "created"
    else:
        action = "updated"

    existing.url = args.url
    existing.fmt = args.fmt
    existing.key_field = args.key_field or ""
    existing.auth_header = args.auth_header or ""
    existing.auth_value = args.auth_value or ""
    existing.cadence_hours = args.cadence_hours
    existing.max_shrink = args.max_shrink
    existing.enabled = True
    db.commit()

    print(f"{action} source for tenant {args.tenant[:8]}: {args.kind} -> {args.url} "
          f"(fmt={args.fmt}, cadence={args.cadence_hours}h)")
    if args.auth_value:
        print("  auth_value sealed at rest - not echoed back.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--service", required=True, choices=["lane-b", "lane-c"])
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--kind", required=True)
    ap.add_argument("--url", default="")
    ap.add_argument("--fmt", default="lines", choices=["lines", "csv", "json"])
    ap.add_argument("--key-field", default="")
    ap.add_argument("--auth-header", default="")
    ap.add_argument("--auth-value", default="")
    ap.add_argument("--cadence-hours", type=int, default=24)
    ap.add_argument("--max-shrink", type=float, default=0.10)
    ap.add_argument("--disable", action="store_true",
                    help="turn the source off without deleting its history")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        (_lane_b if args.service == "lane-b" else _lane_c)(db, args)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
