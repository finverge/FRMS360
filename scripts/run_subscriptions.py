"""Run due report subscriptions.

Renders each due subscription and hands it to its delivery driver. Intended to be run on
a cron / Task Scheduler tick:

    python scripts/run_subscriptions.py --once
    python scripts/run_subscriptions.py --force      # ignore cadence, useful for a demo

**On delivery (BR-609).** The `spool` driver writes the artefact to disk and records it -
the local, no-relay-needed path for a demo tenant. The `email` driver hands the export to
each named recipient through notification-service's `/internal/channels/send`, which sends
it as an attachment over that *tenant's own* configured SMTP relay (the same channel
configuration a tenant admin sets up for board packs and EWS alerts). A tenant that has not
configured one gets an honest "NOT SENT - no email channel configured", not a report that
silently went nowhere - a governance control that fails silently is worse than one that is
visibly absent.
"""
import argparse
import base64
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from cp_common import SessionLocal, record_audit, settings  # noqa: E402
from services.analytics_service.app import export as export_mod  # noqa: E402
from services.analytics_service.app.engine.base import Filters  # noqa: E402
from services.analytics_service.app.engine.postgres import PostgresEngine  # noqa: E402
from services.analytics_service.app.privacy import mask_rows  # noqa: E402
from services.analytics_service.app.subscriptions_model import ReportSubscription  # noqa: E402
from services.tenant_service.app.models import Tenant  # noqa: E402

SPOOL = Path(os.environ.get("REPORT_SPOOL_DIR", "spool"))

_LIST_FIELDS = {"rails", "families", "severities", "regions", "products", "segments",
                "states", "fmr_categories", "dispositions", "branches", "rules",
                "typologies", "analysts", "config_versions", "assignees"}


def filters_from_query(tenant_id: str, query: str) -> Filters:
    """Rebuild the filter set a subscription stored, so the report is exactly the view."""
    raw = parse_qs(query or "")
    kwargs: dict = {"tenant_id": tenant_id}
    for key, values in raw.items():
        if key in _LIST_FIELDS:
            kwargs[key] = values
        elif key in ("date_from", "date_to"):
            try:
                kwargs[key] = datetime.fromisoformat(values[0])
            except ValueError:
                pass
        elif key in ("min_amount_paise", "max_amount_paise"):
            kwargs[key] = int(values[0])
        elif key in ("rfa_only", "nj_breach_only"):
            kwargs[key] = values[0] == "true"
        elif key in ("account", "fmr_status", "str_status"):
            kwargs[key] = values[0]
    return Filters(**kwargs)


def _named_recipients(raw: str) -> list[str]:
    """A subscription's recipients field is free text - comma, semicolon or newline
    delimited, matching how someone would actually type it into a form field."""
    seen: list[str] = []
    for r in re.split(r"[,;\n]", raw or ""):
        r = r.strip()
        if r and r not in seen:
            seen.append(r)
    return seen


def deliver(driver: str, recipients: str, filename: str, body: str, *,
           tenant_id: str = "") -> str:
    """Hand the artefact to its channel. Returns a status recorded against the run."""
    SPOOL.mkdir(parents=True, exist_ok=True)
    path = SPOOL / filename
    path.write_text(body, encoding="utf-8")

    if driver == "spool":
        return f"spooled to {path}"

    if driver == "email":
        targets = _named_recipients(recipients)
        if not targets:
            return f"NOT SENT - no recipients named; artefact retained at {path}"

        sent, failed = [], []
        attachment_b64 = base64.b64encode(body.encode("utf-8")).decode("ascii")
        for to in targets:
            try:
                resp = httpx.post(
                    f"{settings.notification_service_url}/internal/channels/send",
                    headers={"x-internal-key": settings.internal_api_key}, timeout=15.0,
                    json={"tenant_id": tenant_id, "channel": "email", "to": to,
                          "subject": f"Scheduled Fraud360 report: {filename}",
                          "body": "Your scheduled Fraud360 export is attached.",
                          "attachment_filename": filename,
                          "attachment_content_b64": attachment_b64,
                          "attachment_mime": "text/csv"})
                resp.raise_for_status()
                (sent if resp.json().get("ok") else failed).append(to)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{to} ({type(exc).__name__})")

        if sent and not failed:
            return f"sent to {', '.join(sent)} (retained at {path})"
        if sent and failed:
            return (f"sent to {', '.join(sent)}; NOT SENT to {', '.join(failed)}; "
                    f"artefact retained at {path}")
        # Nobody reached - most commonly, the tenant has not configured an email relay
        # yet. Not pretending: the row must not claim a delivery that did not happen.
        return f"NOT SENT to {', '.join(failed)}; artefact retained at {path}"

    return f"unknown driver '{driver}'; artefact retained at {path}"


def run_due(force: bool = False, only_tenant: str | None = None) -> int:
    db = SessionLocal()
    ran = 0
    try:
        tenant_by_id = {t.id: t for t in db.scalars(select(Tenant))}
        subs = list(db.scalars(select(ReportSubscription)))
        for sub in subs:
            tenant = tenant_by_id.get(sub.tenant_id)
            if not tenant or (only_tenant and tenant.slug != only_tenant):
                continue
            if not force and not sub.is_due():
                continue

            try:
                engine = PostgresEngine(db)
                f = filters_from_query(sub.tenant_id, sub.query)
                total = engine.count(sub.entity, f)
                rows = mask_rows(engine.rows(sub.entity, f, limit=5000, offset=0), False)
                body = export_mod.to_csv(
                    rows, tenant=tenant.slug, entity=sub.entity, actor=sub.owner,
                    role="scheduled-report", filters=f.as_dict(), total=total,
                    masked=True)
                name = export_mod.filename(f"{tenant.slug}_{sub.name[:20]}", sub.entity)
                status = deliver(sub.driver, sub.recipients, name, body,
                                 tenant_id=sub.tenant_id)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                status = f"FAILED: {exc}"

            sub.last_run_at = datetime.now(timezone.utc)
            sub.last_status = status[:240]
            sub.run_count = (sub.run_count or 0) + 1
            db.commit()
            ran += 1
            print(f"  {tenant.slug:<12} {sub.name:<28} {status}")

            record_audit(
                service="analytics-service", action="subscription.run",
                actor=sub.owner, actor_role="scheduled-report", tenant_id=sub.tenant_id,
                target_type="subscription", target_id=sub.id,
                status="success" if not status.startswith("FAILED") else "failure",
                detail={"name": sub.name, "driver": sub.driver, "result": status[:200]})
    finally:
        db.close()
    return ran


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run and exit (default)")
    ap.add_argument("--force", action="store_true", help="ignore cadence")
    ap.add_argument("--tenant", default=None)
    args = ap.parse_args()
    count = run_due(force=args.force, only_tenant=args.tenant)
    print(f"\n{count} subscription(s) run. Spool: {SPOOL.resolve()}")


if __name__ == "__main__":
    main()
