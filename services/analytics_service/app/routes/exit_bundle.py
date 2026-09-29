"""The fraud record, for return to a bank on exit (BR-107 / REG-06).

The outsourcing directions require data return "in a usable format" when a service
provider relationship ends. The existing bundle returned the tenant's configuration —
which is ours as much as theirs — and none of the fraud record, which is the part the
bank is legally obliged to keep and the part it cannot reconstruct from anywhere else.

**What this returns.** The case file: every case with its alerts, the transactions those
alerts were raised on, the transition log, the accountability examination, referrals and
recoveries, the filings, and a manifest of the evidence documents.

**What it deliberately does not return.** The full transaction history. A tenant with
thirty million rows does not want them in a JSON document, and pretending otherwise
produces a bundle nobody can open. Those are counted here and pointed at the CSV export,
so what is absent is stated rather than quietly dropped.

**Documents are a manifest, not bytes.** Each carries its SHA-256, so the bank can verify
what it downloads matches what we held. Streaming megabytes of PDF through a JSON payload
would make the bundle unusable in the name of completeness.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from cp_common import get_session, require_internal_key

router = APIRouter(tags=["internal"])

#: A guard rather than a page size. A bundle this large is a signal to use the bulk
#: export path, and truncating silently would be the one unacceptable outcome.
MAX_CASES = 50_000


def _rows(db: Session, sql: str, **params) -> list[dict]:
    return [dict(r) for r in db.execute(text(sql), params).mappings().all()]


def _iso(v):
    """JSON-safe. ``date`` is not a ``datetime`` subclass in the direction that matters,
    and a referral date silently serialising as ``null`` would be a quiet data loss."""
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (UUID, bytes)):
        return str(v)
    return v


def _clean(rows: list[dict]) -> list[dict]:
    return [{k: _iso(v) for k, v in r.items()} for r in rows]


@router.get("/internal/export/{tenant_id}",
            dependencies=[Depends(require_internal_key)])
def export_fraud_record(tenant_id: str, db: Session = Depends(get_session)) -> dict:
    """Everything the bank needs to carry its regulatory obligations elsewhere."""
    sections: dict = {}
    problems: list[str] = []

    def section(name: str, sql: str, **params):
        try:
            sections[name] = _clean(_rows(db, sql, t=tenant_id, **params))
        except Exception as exc:  # noqa: BLE001
            # Recorded, never swallowed. A bundle missing a section must not look whole.
            problems.append(f"{name}: {str(exc)[:200]}")
            sections[name] = None

    section("cases", """
        SELECT case_id, opened_ts, state, severity, fmr_category, amount_paise,
               recovered_paise, rfa_flag, rail, region, product, customer_segment,
               assignee, source, show_cause_ts, response_due_ts, decision_ts,
               fmr_due_ts, fmr_filed_ts, str_due_ts, str_filed_ts,
               accountability_due_ts, closed_ts
          FROM analytics.fact_case WHERE tenant_id = :t ORDER BY opened_ts""")

    section("alerts", """
        SELECT a.alert_id, a.case_id, a.txn_id, a.ts, a.rule_family, a.rule_id,
               a.typology, a.score, a.severity, a.disposition, a.analyst,
               a.config_version, a.sub_rule_ref, a.matched_reason, a.observed_value,
               a.threshold_value, a.observed_unit, a.first_touch_ts, a.disposition_ts,
               a.source
          FROM analytics.fact_alert a
         WHERE a.tenant_id = :t AND a.case_id IS NOT NULL ORDER BY a.ts""")

    # Only the transactions that are evidence on a case. The rest are a bulk extract.
    section("evidence_transactions", """
        SELECT DISTINCT t.txn_id, t.ts, t.rail, t.amount_paise, t.debtor_account,
               t.creditor_account, t.branch, t.region, t.product, t.customer_segment,
               t.channel, t.device_id, t.ip_addr, t.status, t.source
          FROM analytics.fact_transaction t
          JOIN analytics.fact_alert a
            ON a.txn_id = t.txn_id AND a.tenant_id = t.tenant_id
         WHERE t.tenant_id = :t AND a.case_id IS NOT NULL ORDER BY t.ts""")

    # The refusal record matters as much as the approvals: a bank defending a
    # classification needs to show what was refused and on what ground.
    section("case_transitions", """
        SELECT case_id, action, from_state, to_state, actor, actor_role, status,
               refusal_code, reason, breached_policy, breach_days_allowed,
               approves_id, created_at
          FROM cases.case_transitions WHERE tenant_id = :t ORDER BY created_at""")

    # Metadata and hash only; ``content`` is deliberately not selected.
    section("documents_manifest", """
        SELECT case_id, doc_type, filename, content_type, size_bytes, sha256, note,
               uploaded_by, uploaded_at
          FROM cases.case_documents WHERE tenant_id = :t ORDER BY uploaded_at""")

    # The filed FMR/STR payload itself, not just the fact that one was filed.
    section("filings", """
        SELECT id, case_id, kind, schema_version, status, revision, payload,
               validation, content_hash, generated_at, generated_by, submitted_at,
               submitted_by, reference_number, ack_note
          FROM cases.regulatory_filings WHERE tenant_id = :t ORDER BY generated_at""")

    section("staff_accountability", """
        SELECT id, case_id, status, opened_by, opened_at, examiner, conclusion,
               systemic_lapse, systemic_note, concluded_by, concluded_at,
               breached_policy, breach_days_allowed
          FROM cases.staff_accountability WHERE tenant_id = :t ORDER BY opened_at""")

    section("accountability_findings", """
        SELECT case_id, examination_id, staff_ref, staff_name, role_at_time, branch,
               finding, action_taken, note, recorded_by, recorded_at
          FROM cases.accountability_findings WHERE tenant_id = :t ORDER BY recorded_at""")

    section("lea_referrals", """
        SELECT case_id, agency, agency_office, state, complaint_ref, fir_number,
               referred_on, fir_date, last_update, referred_by, created_at, updated_at
          FROM cases.lea_referrals WHERE tenant_id = :t ORDER BY referred_on""")

    section("recovery_entries", """
        SELECT case_id, amount_paise, mode, reference, recovered_on, note,
               recorded_by, recorded_at
          FROM cases.recovery_entries WHERE tenant_id = :t ORDER BY recovered_on""")

    # What is deliberately absent, counted so it is a stated omission not a silent one.
    try:
        not_included = _rows(db, """
            SELECT
              (SELECT COUNT(*) FROM analytics.fact_transaction WHERE tenant_id = :t)
                AS transactions_total,
              (SELECT COUNT(*) FROM analytics.fact_alert
                WHERE tenant_id = :t AND case_id IS NULL) AS alerts_without_a_case""",
                             t=tenant_id)[0]
    except Exception as exc:  # noqa: BLE001
        problems.append(f"not_included counts: {str(exc)[:200]}")
        not_included = None

    cases = sections.get("cases")
    truncated = bool(cases and len(cases) >= MAX_CASES)
    if truncated:
        problems.append(
            f"more than {MAX_CASES} cases; this bundle is not the complete record. "
            "Use the bulk CSV export for a tenant this size.")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tenant_id": tenant_id,
        "sections": sections,
        "counts": {k: (len(v) if isinstance(v, list) else None)
                   for k, v in sections.items()},
        "not_included": {
            "why": "Full transaction history and uncased alerts are a bulk extract, not "
                   "a JSON bundle. Documents are listed with their SHA-256; the bytes are "
                   "downloaded separately and can be verified against these hashes.",
            "counts": not_included,
            "bulk_export": f"/analytics/{tenant_id}/export/transaction",
        },
        # The whole point: a caller must be able to tell a complete bundle from a partial
        # one without inspecting every section.
        "complete": not problems,
        "problems": problems,
    }
