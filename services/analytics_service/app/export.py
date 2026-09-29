"""Export of analytical results.

An export leaves the platform's control, so it is treated as a privileged action rather
than a convenience:

* **masked by default** - the same rule as every other surface; unmasking needs the
  capability plus a justification and is audited,
* **watermarked** - every file carries who exported it, when, for which tenant and under
  exactly which filters, so a spreadsheet found later can be traced back and reproduced,
* **row-capped** - a bulk extract of an entire tenant is not a reporting feature,
* **audited** - the fact of exporting is recorded, not merely changes.
"""
import csv
import io
from datetime import datetime, timezone

MAX_ROWS = 50_000


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def watermark_lines(*, tenant: str, entity: str, actor: str, role: str,
                    filters: dict, total: int, returned: int, masked: bool) -> list[str]:
    """Provenance header. Deliberately part of the file, not the covering email."""
    active = {k: v for k, v in (filters or {}).items()
              if v not in (None, [], "", False) and k != "tenant_id"}
    return [
        "# Fraud360 export",
        f"# tenant: {tenant}",
        f"# dataset: {entity}",
        f"# exported_by: {actor} ({role})",
        f"# exported_at: {datetime.now(timezone.utc).isoformat()}",
        f"# customer_data: {'MASKED' if masked else 'UNMASKED - handle per DPDP obligations'}",
        f"# rows: {returned} of {total} matching",
        f"# filters: {active if active else 'none (all data)'}",
        "# This file is a point-in-time extract. Re-running the filters above reproduces it.",
    ]


def to_csv(rows: list[dict], *, tenant: str, entity: str, actor: str, role: str,
           filters: dict, total: int, masked: bool) -> str:
    buf = io.StringIO()
    for line in watermark_lines(tenant=tenant, entity=entity, actor=actor, role=role,
                                filters=filters, total=total, returned=len(rows),
                                masked=masked):
        buf.write(line + "\n")
    if not rows:
        buf.write("# no matching rows\n")
        return buf.getvalue()

    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()),
                            extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: _fmt(v) for k, v in r.items()})
    return buf.getvalue()


def filename(tenant_slug: str, entity: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"frms_{tenant_slug}_{entity}_{stamp}.csv"
