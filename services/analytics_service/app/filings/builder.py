"""Assemble a return from what the platform actually knows about a case.

Everything here is derived from recorded evidence - the case, its alerts, the transactions
behind them, the transition log, the documents on file. Nothing is inferred and nothing is
defaulted into place: a field the platform cannot evidence is left absent so that
validation reports it, because a return that quietly fills its own gaps is a return nobody
can defend.

Customer identifiers are included in full. A regulatory filing is one of the few places
they legitimately belong, and the masking that applies everywhere else would make the
return useless. That makes generating one a privileged, audited act - see the route.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import data_source
from . import schema


def _rupees(paise: int | None) -> str | None:
    if paise is None:
        return None
    return f"{paise / 100:,.2f}"


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat() if value.tzinfo \
            else value.replace(tzinfo=timezone.utc).isoformat()
    return str(value)


def build(db: Session, *, kind: str, tenant_id: str, case_id: str,
          entity: dict, policy: dict) -> dict:
    """Gather the payload for one return. Absent facts stay absent."""
    case = db.execute(text(
        "SELECT * FROM analytics.fact_case WHERE tenant_id = :t AND case_id = :c"),
        {"t": tenant_id, "c": case_id}).mappings().first()
    if case is None:
        raise LookupError(f"case {case_id} not found")

    # DISTINCT: one transaction can carry several alerts when it trips several rules,
    # and counting its value once per alert is the R-03 trap all over again.
    txns = db.execute(text(
        "SELECT DISTINCT ON (t.txn_id) t.* FROM analytics.fact_transaction t "
        "JOIN analytics.fact_alert a ON a.txn_id = t.txn_id AND a.tenant_id = t.tenant_id "
        "WHERE t.tenant_id = :t AND a.case_id = :c ORDER BY t.txn_id"),
        {"t": tenant_id, "c": case_id}).mappings().all()

    # Provenance gate (BR-611). Checked here rather than at the route, because this is the
    # function that turns a case into a document addressed to a regulator, and every path
    # that produces one goes through it. The case *and* its evidence must be live: a case
    # marked live whose transactions are demonstration rows is worse than an obviously
    # synthetic one, because it looks filable.
    tainted = data_source.non_live(
        [case["source"]] + [t["source"] for t in txns])
    if tainted:
        raise data_source.NotLiveData(f"A {kind.upper()} return", tainted)

    alerts = db.execute(text(
        "SELECT rule_id, rule_family, typology, matched_reason, observed_value, "
        "       threshold_value, observed_unit, severity, config_version, ts "
        "FROM analytics.fact_alert WHERE tenant_id = :t AND case_id = :c ORDER BY ts"),
        {"t": tenant_id, "c": case_id}).mappings().all()

    transitions = db.execute(text(
        "SELECT action, from_state, to_state, actor, actor_role, status, reason, "
        "       created_at, approves_id FROM cases.case_transitions "
        "WHERE tenant_id = :t AND case_id = :c AND status IN ('applied', 'approved') "
        "ORDER BY created_at"), {"t": tenant_id, "c": case_id}).mappings().all()

    docs = db.execute(text(
        "SELECT doc_type, filename, sha256, uploaded_by, uploaded_at "
        "FROM cases.case_documents WHERE tenant_id = :t AND case_id = :c"),
        {"t": tenant_id, "c": case_id}).mappings().all()

    accounts = sorted({t["debtor_account"] for t in txns} |
                      {t["creditor_account"] for t in txns})
    occurrence = min((t["ts"] for t in txns), default=None)

    declared = next((tr for tr in transitions if tr["action"] == "declare_fraud"
                     and tr["status"] == "applied"), None)
    proposal = next((tr for tr in transitions if tr["action"] == "declare_fraud"
                     and tr["status"] == "approved"), None)

    payload: dict = {
        "return_kind": kind,
        "return_label": schema.RETURN_LABELS[kind],
        "reported_to": schema.RETURN_RECIPIENTS[kind],
        "schema_version": schema.SCHEMA_VERSION,
        "generated_at": _iso(datetime.now(timezone.utc)),

        "entity_name": entity.get("legal_name") or entity.get("display_name"),
        "entity_type": entity.get("entity_label") or entity.get("entity_type"),
        "governing_direction": entity.get("governing_direction"),

        "case_reference": case["case_id"],
        "detection_date": _iso(case["opened_ts"]),
        "occurrence_date": _iso(occurrence),
        "amount_involved": _rupees(case["amount_paise"]),
        "amount_involved_paise": case["amount_paise"],
        "amount_recovered": _rupees(case["recovered_paise"]) if case["recovered_paise"] else None,
        "accounts_involved": accounts,
        "transaction_count": len(txns),
        "period_covered": (f"{_iso(occurrence)} to {_iso(max((t['ts'] for t in txns), default=None))}"
                           if txns else None),
        "rails_used": sorted({t["rail"] for t in txns}),
        "customer_segment": case["customer_segment"],
        "product": case["product"],
        "branch_region": case["region"],
    }

    # The narrative is assembled from the indicators that actually fired, so it can be
    # traced back to evidence rather than being someone's recollection.
    if alerts:
        reasons = []
        for a in alerts:
            piece = f"{a['rule_id']} ({a['typology']}): {a['matched_reason']}"
            if a["observed_value"] is not None:
                piece += (f" - observed {a['observed_value']:g} "
                          f"{a['observed_unit'] or ''}".rstrip() +
                          f" against a threshold of {a['threshold_value']:g}")
            reasons.append(piece)
        payload["modus_operandi"] = (
            f"{len(txns)} transaction(s) across {', '.join(payload['rails_used'])} "
            f"totalling INR {payload['amount_involved']} triggered "
            f"{len(alerts)} early-warning indicator(s). " + " ".join(reasons))
        payload["indicators_triggered"] = [
            {"rule_id": a["rule_id"], "family": a["rule_family"],
             "typology": a["typology"], "reason": a["matched_reason"],
             "observed": a["observed_value"], "threshold": a["threshold_value"],
             "unit": a["observed_unit"], "config_version": a["config_version"],
             "at": _iso(a["ts"])}
            for a in alerts]
        payload["suspicion_grounds"] = (
            "Automated monitoring under the entity's board-approved early-warning "
            f"framework flagged {len(alerts)} indicator(s) on this account activity: "
            + "; ".join(f"{a['rule_id']} - {a['matched_reason']}" for a in alerts) + ".")

    if kind == "fmr":
        payload.update({
            "fmr_category": case["fmr_category"],
            "declaration_date": _iso(case["decision_ts"]),
            "reasoned_order_on_file": any(d["doc_type"] == "reasoned_order" for d in docs),
            "approved_by": declared["actor"] if declared else None,
            "proposed_by": proposal["actor"] if proposal else None,
            "filing_due": _iso(case["fmr_due_ts"]),
            "natural_justice": {
                "show_cause_issued": _iso(case["show_cause_ts"]),
                "response_due": _iso(case["response_due_ts"]),
                "window_days": policy.get("natural_justice_days"),
            },
            # Above the entity's own referral threshold this stops being optional. The
            # platform states the obligation; it cannot assert the referral happened.
            "lea_referral_required": bool(
                case["amount_paise"] and policy.get("lea_referral_paise")
                and case["amount_paise"] >= policy["lea_referral_paise"]),
        })
    else:
        payload["principal_officer"] = entity.get("principal_officer")

    payload["evidence"] = {
        "documents": [{"type": d["doc_type"], "filename": d["filename"],
                       "sha256": d["sha256"], "uploaded_by": d["uploaded_by"],
                       "uploaded_at": _iso(d["uploaded_at"])} for d in docs],
        "case_history": [{"action": t["action"], "from": t["from_state"],
                          "to": t["to_state"], "actor": t["actor"],
                          "role": t["actor_role"], "status": t["status"],
                          "reason": t["reason"], "at": _iso(t["created_at"])}
                         for t in transitions],
        "transactions": [{"txn_id": t["txn_id"], "at": _iso(t["ts"]), "rail": t["rail"],
                          "amount": _rupees(t["amount_paise"]),
                          "debtor": t["debtor_account"], "creditor": t["creditor_account"],
                          "channel": t["channel"], "branch": t["branch"]}
                         for t in txns],
    }
    return payload
