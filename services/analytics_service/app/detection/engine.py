"""The detection runtime.

Claims a batch of inbound transactions from ingestion-service, projects them into the
analytical facts, evaluates the tenant's *configured* EWS catalogue against them, and
raises alerts and cases from what crosses a threshold.

Two properties this is built to preserve, both of which are easy to lose:

**The control plane must actually control detection.** Thresholds come from
config-service, the same catalogue the dormant-indicator register reports on. Nothing here
hard-codes a band. Retuning LAY-02 from 40 counterparties to 12 in the console changes what
fires on the next run - if it did not, the console would be decoration.

**A threshold that is set too high must miss real fraud.** Nothing is force-fired to make
a demo look lively. If a tenant's bands are badly tuned, this produces fewer alerts, which
is the honest outcome and the thing the coverage report is for.

Alerts record the trace - which band matched, on what observation, against which
threshold, under which config version - so a case reopened years later is explained by the
rules that actually ran rather than by today's.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from cp_common import scoring, settings

from ..models import FactAlert, FactCase, FactTransaction
from ..rules import active_model, active_rules, policy_values
from . import (
    cbs_features, counter_feed, features, graph_features, los_features,
    model_scoring, reference,
)

log = logging.getLogger("detection")

#: Family -> the typology label an analyst sees on the alert.
TYPOLOGY = {
    "VEL": "Velocity / value anomaly", "SME": "Structuring", "BEH": "Behavioural shift",
    "LAY": "Layering / mule network", "CPT": "Counterparty risk",
    "CHN": "Channel & device risk", "TBM": "Trade-based money laundering",
    "CBS": "Loan account misuse", "QUAL": "Qualitative indicator",
    "LOS": "Loan origination fraud",
}

#: CBS event kinds evaluate_los() claims and scores directly - BR-214. Distinct from
#: every other CBS kind, which is read fresh on every run and never marked "consumed"
#: (see cbs_features.py) because a loan-conduct ratio is a live property of the account
#: that can legitimately change; a loan-origination finding is a property of one
#: application, evaluated once.
LOS_EVENT_KINDS = ("loan_application", "collateral_valuation")

#: Contribution of one fired rule to the alert score, by family. Layering and structuring
#: weigh more because they indicate deliberate concealment rather than a noisy customer.
#: Re-exported so existing callers and tests keep working; the definition now lives in
#: cp_common.scoring because the inline lane needs the same one.
FAMILY_WEIGHT = scoring.FAMILY_WEIGHT


@dataclass
class RunReport:
    claimed: int = 0
    projected: int = 0
    duplicates: int = 0
    alerts: int = 0
    cases: int = 0
    failed: list[dict] = field(default_factory=list)
    fired_rules: dict[str, int] = field(default_factory=dict)
    unmeasurable: dict[str, str] = field(default_factory=dict)
    #: Whether the inline lane's counters were refreshed by this run. Reported rather
    #: than silent: a decision lane running on stale counters is something an operator
    #: has to be able to see from the batch report.
    counters_published: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"claimed": self.claimed, "projected": self.projected,
                "duplicates": self.duplicates, "alerts": self.alerts,
                "cases": self.cases, "failed": len(self.failed),
                "fired_rules": self.fired_rules, "unmeasurable": self.unmeasurable,
                "counters_published": self.counters_published}


def _internal(path: str, body: dict) -> dict:
    r = httpx.post(f"{settings.ingestion_service_url}{path}",
                   headers={"x-internal-key": settings.internal_api_key},
                   json=body, timeout=30.0)
    r.raise_for_status()
    return r.json()


def _severity(score: float, policy: dict) -> str:
    """Kept as a name this module already used; the rule lives in cp_common.scoring."""
    return scoring.severity_for(score, policy)


def run_once(db: Session, tenant_id: str, *, limit: int = 500) -> RunReport:
    rep = RunReport()
    catalogue = active_rules(tenant_id)
    policy = policy_values(tenant_id)
    if not catalogue:
        # Scoring against an unknown catalogue would invent a rule set. Leave the work
        # queued; it will be picked up once the control plane is reachable again.
        log.warning("no rule catalogue for %s; leaving the queue untouched", tenant_id)
        return rep

    # Loan-origination findings (BR-214) run whether or not a payment batch is pending -
    # an application arrives on its own schedule, not the payment queue's. Done first,
    # not folded into the tail of this function, because the payment claim below returns
    # early when its own queue is empty, and that must never skip this. Best-effort: a
    # fault here must not stop the payment batch that follows from being claimed.
    try:
        los_rep = evaluate_los(db, tenant_id, catalogue=catalogue, policy=policy)
        rep.alerts += los_rep.alerts
        rep.cases += los_rep.cases
        for rule_id, n in los_rep.fired_rules.items():
            rep.fired_rules[rule_id] = rep.fired_rules.get(rule_id, 0) + n
        rep.unmeasurable.update(los_rep.unmeasurable)
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("evaluate_los failed for %s", tenant_id)

    claim = _internal(f"/internal/ingest/{tenant_id}/claim", {"limit": limit})
    token, rows = claim["claim_token"], claim["rows"]
    rep.claimed = len(rows)
    if not rows:
        return rep

    txns, ids_by_txn = [], {}
    for row in rows:
        c = dict(row["canonical"])
        c["ts"] = datetime.fromisoformat(c["ts"])
        c["source"] = row.get("source") or "live"
        c["_raw_id"] = row["id"]
        txns.append(c)
        ids_by_txn[c["txn_id"]] = row["id"]

    now = max(t["ts"] for t in txns)
    batch_start = min(t["ts"] for t in txns)

    # ---- 1. project the whole batch before measuring anything --------------------
    # Structuring is seven transfers in one file; a fan-in hub is forty payers in one
    # file. Measuring each row against history that excludes its own batch made every
    # such burst invisible - each transaction looked lonely because its accomplices had
    # not been written yet. Project first, then measure.
    projected: list[str] = []
    scorable: list[dict] = []
    for t in txns:
        try:
            if db.get(FactTransaction, t["txn_id"]) is not None:
                rep.duplicates += 1
                projected.append(t["_raw_id"])
                continue
            db.add(FactTransaction(
                txn_id=t["txn_id"], tenant_id=tenant_id, ts=t["ts"], rail=t["rail"],
                amount_paise=t["amount_paise"], debtor_account=t["debtor_account"],
                creditor_account=t["creditor_account"], branch=t.get("branch", ""),
                region=t.get("region", ""), product=t.get("product", "savings"),
                customer_segment=t.get("customer_segment", "retail"),
                channel=t.get("channel", ""), device_id=t.get("device_id", ""),
                ip_addr=t.get("ip_addr", ""), status=t.get("status", "settled"),
                device_risk_score=t.get("device_risk_score"),
                behavior_anomaly_score=t.get("behavior_anomaly_score"),
                source=t["source"]))
            db.flush()
            rep.projected += 1
            projected.append(t["_raw_id"])
            scorable.append(t)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            rep.failed.append({"id": t["_raw_id"], "error": str(exc)[:500]})

    # ---- 2. measure, with the batch visible to itself ----------------------------
    # The long baseline still excludes the batch: a burst must not dilute the normality
    # it is being compared against.
    accounts = sorted({t["debtor_account"] for t in scorable} |
                      {t["creditor_account"] for t in scorable})
    ctxs = features.load_context(db, tenant_id, accounts, now,
                                 baseline_before=batch_start)
    clusters = features.device_clusters(
        db, tenant_id, sorted({t.get("device_id") or "" for t in scorable} - {""}), now)
    # Computed once for the whole batch, like every other aggregate here. The traversal
    # and the hour profiles are both far too expensive to do per row.
    cycles = graph_features.circular_flows(db, tenant_id, accounts, now)
    profiles = graph_features.hour_profiles(db, tenant_id, accounts, now,
                                            before=batch_start)
    # Reference lists are pulled once; screening a batch against a 30k-row sanctions list
    # one transaction at a time would be one query per row.
    screening = reference.load_screening_set(db, tenant_id)
    # AI/ML roadmap Phase 2 (HLD AD-14). Loaded once per batch, not once per transaction -
    # same reasoning as every other per-batch aggregate above. `loaded_model` stays None,
    # and VEL-04 correctly unmeasurable, for a tenant with no active model version, one
    # config-service cannot reach, or an artifact that fails to load or verify. The name
    # comes from features.NEEDS_MODEL, not a literal here - VEL-04 is the only rule
    # backed by a live-scored (per-transaction) model; LAY-05 (Phase 3) is backed by a
    # periodically-refreshed table instead, read separately below, not through this path.
    loaded_model = None
    model_unmeasurable_reason = "no active model config version for this tenant"
    model_info = active_model(tenant_id, features.NEEDS_MODEL["VEL-04"])
    if model_info.get("available"):
        artifact = (model_info.get("body") or {}).get("model_artifact") or {}
        try:
            loaded_model = model_scoring.load(
                artifact["uri"], artifact["sha256"],
                format=artifact.get("format", "sklearn-joblib"))
        except (KeyError, model_scoring.ModelLoadError) as exc:
            model_unmeasurable_reason = f"active model version could not be loaded: {exc}"
            log.warning("model artifact unavailable for %s: %s", tenant_id, exc)
    # AI/ML roadmap Phase 3 (HLD AD-15). Unlike VEL-04, never scored live here - a
    # GraphSAGE forward pass over the whole tenant graph runs on its own schedule
    # (scripts/refresh_ring_scores.py) and writes analytics.account_ring_score; this is
    # a plain primary-key read against that table, the same shape as
    # `clusters` above. `ring_scores` can be legitimately sparse (an account the last
    # refresh never saw) without that meaning "no model" - the tenant-level
    # unmeasurable check below is separate from an individual account's absence.
    ring_model_info = active_model(tenant_id, features.NEEDS_MODEL["LAY-05"], force=False)
    ring_scores: dict[str, float] = {}
    if ring_model_info.get("available") and accounts:
        ring_scores = {r[0]: float(r[1]) for r in db.execute(text(
            "SELECT account, score FROM analytics.account_ring_score "
            "WHERE tenant_id = :t AND account = ANY(:accts)"),
            {"t": tenant_id, "accts": accounts}).all()}
    # CBS / loan-system events (BR-211). Loaded per batch like every other aggregate.
    group_accts = cbs_features.group_accounts(db, tenant_id)
    loan_ctx = cbs_features.load_loan_context(db, tenant_id, accounts, now,
                                              group_accounts=group_accts)

    # ---- 3. score -----------------------------------------------------------------
    for t in scorable:
        ctx = ctxs.get(t["debtor_account"], features.AccountContext())
        # Looked up before observe(), not after: CPT-03's screen_collateral() reads
        # t["collateral_ref"] inside observe() itself (cp_common/observations.py), so
        # the loan-context join that carries it has to happen first. Reused below for
        # observe_loan() too - one lookup, not two.
        loan = loan_ctx.get(t["debtor_account"])
        if loan is not None and loan.collateral_id:
            t["collateral_ref"] = loan.collateral_id
        observations = features.observe(
            t, ctx, clusters, now, cycles=cycles, screening=screening,
            hour_profile=profiles.get(t["debtor_account"]))

        # Score the receiving account too. A collection account may never send anything,
        # so a debtor-only view never scores the hub of a fan-in - the single account a
        # mule investigation is actually about. Where both sides produce a value for the
        # same rule, the riskier one stands.
        # Loan-conduct ratios describe the borrowal account, so they are observed once
        # per account rather than per side of the payment.
        if loan is not None:
            for rule_id, value in cbs_features.observe_loan(loan).items():
                observations[rule_id] = max(observations.get(rule_id, 0.0), value)

        cred_ctx = ctxs.get(t["creditor_account"])
        if cred_ctx is not None:
            for rule_id, value in features.observe(
                    t, cred_ctx, clusters, now, subject="creditor",
                    cycles=cycles).items():
                observations[rule_id] = max(observations.get(rule_id, 0.0), value)

        # AI/ML roadmap Phase 2. Same both-sides-scored convention as every rule above -
        # a mule collection account may never pay anybody, so a debtor-only view would
        # never score the account a fan-in investigation is actually about.
        if loaded_model is not None:
            debtor_score = model_scoring.score(
                loaded_model, model_scoring.feature_vector(t, ctx))
            observations["VEL-04"] = debtor_score
            if cred_ctx is not None:
                credit_score = model_scoring.score(
                    loaded_model, model_scoring.feature_vector(t, cred_ctx))
                observations["VEL-04"] = max(debtor_score, credit_score)

        # AI/ML roadmap Phase 3 (HLD AD-15). A lookup, not a computation - the score
        # was already produced by the last scheduled refresh. Same both-sides
        # convention as every other rule; an account absent from `ring_scores` (new
        # since the last refresh, or the refresh has never run) contributes nothing,
        # which is correct - it is not the same as a confirmed-typical score of 0.0.
        debtor_ring = ring_scores.get(t["debtor_account"])
        creditor_ring = ring_scores.get(t["creditor_account"])
        if debtor_ring is not None or creditor_ring is not None:
            observations["LAY-05"] = max(debtor_ring or 0.0, creditor_ring or 0.0)

        fired = []
        for rule_id, observed in observations.items():
            rule = catalogue.get(rule_id)
            if not rule or rule.get("threshold") is None:
                continue
            threshold = float(rule["threshold"])
            # The whole point: the tenant's own configured band decides, not this code -
            # including which way the comparison runs. "Beneficiary added within 24h"
            # fires below its threshold; everything else fires at or above.
            fires = (observed <= threshold if rule.get("comparator") == "lte"
                     else observed >= threshold)
            if not fires:
                continue
            fired.append((rule_id, rule, observed, threshold))

        if not fired:
            continue

        score = scoring.score_of(r[1].get("family", "") for r in fired)
        severity = _severity(score, policy)
        version = next((r[1].get("version") for r in fired if r[1].get("version")), "1.0.0")

        case_id = None
        # A case is the unit an investigator works and a regulator asks about. Opening one
        # per alert would bury them; the severity bands are the tenant's own.
        if severity in ("critical", "high"):
            case_id = _attach_case(db, tenant_id, t, severity, score, fired, now)
            if case_id:
                rep.cases += 1

        for rule_id, rule, observed, threshold in fired:
            db.add(FactAlert(
                alert_id="A" + uuid.uuid4().hex[:16], tenant_id=tenant_id,
                txn_id=t["txn_id"], ts=t["ts"],
                rule_family=rule.get("family", ""), rule_id=rule_id,
                typology=TYPOLOGY.get(rule.get("family", ""), "Unclassified"),
                score=float(score), severity=severity, disposition="pending",
                case_id=case_id, config_version=rule.get("version") or version,
                sub_rule_ref=rule.get("sub_rule_ref") or "",
                matched_reason=rule.get("reason") or "Threshold exceeded",
                observed_value=float(observed), threshold_value=threshold,
                observed_unit=rule.get("observed_unit") or "",
                source=t["source"]))
            rep.alerts += 1
            rep.fired_rules[rule_id] = rep.fired_rules.get(rule_id, 0) + 1

    db.commit()

    # Report what could not be measured at all, so "dormant" is never mistaken for
    # "broken detection" by whoever reads the coverage panel.
    rep.unmeasurable = {r: why for r, why in features.NEEDS_EXTERNAL_DATA.items()
                        if r in catalogue}
    # A reference-fed indicator is unmeasurable too, until its list is loaded. Leaving it
    # out because it *could* be measured would report a sanctions rule as working on a
    # tenant that has never uploaded a list.
    # Which CBS feeds this tenant has actually sent, so a missing one is named.
    seen_kinds = {
        r[0] for r in db.execute(text(
            "SELECT DISTINCT kind FROM ingestion.cbs_events WHERE tenant_id = :t"),
            {"t": tenant_id}).all()}
    for rule_id, why in cbs_features.unmeasurable(
            seen_kinds, bool(group_accts)).items():
        if rule_id in catalogue:
            rep.unmeasurable[rule_id] = why

    for rule_id, kind in features.NEEDS_REFERENCE_DATA.items():
        if rule_id not in catalogue or rule_id in rep.unmeasurable:
            # Already reported - e.g. CPT-03 can be dormant for either or both of two
            # independent reasons (no collateral_valuation feed, no CERSAI list loaded);
            # the first one found is reported rather than the second silently
            # overwriting it, the same "don't clobber an existing reason" discipline
            # CBS-03's own two-reason case already follows in cbs_features.unmeasurable.
            continue
        st = screening["availability"].get(kind)
        if st is not None and not st.loaded:
            rep.unmeasurable[rule_id] = st.why_unavailable

    # AI/ML roadmap Phase 2. VEL-04 is scored above whenever loaded_model is set; the
    # same condition, inverted, is exactly when it must report unmeasurable rather than
    # silently vanish from the coverage panel like a rule that simply never fired.
    if "VEL-04" in catalogue and loaded_model is None:
        rep.unmeasurable["VEL-04"] = model_unmeasurable_reason
    # AI/ML roadmap Phase 3. LAY-05's unmeasurable condition is the tenant having no
    # active graph-ring-score model at all - not the same test as loaded_model, which
    # only tracks VEL-04's model. An account simply missing from `ring_scores` (no
    # refresh has scored it yet) is not this: that is a per-transaction absence, not a
    # tenant-wide coverage gap, so it is deliberately not reported here.
    if "LAY-05" in catalogue and not ring_model_info.get("available"):
        rep.unmeasurable["LAY-05"] = (
            "no active 'graph-ring-score' model config version for this tenant")

    _internal(f"/internal/ingest/{tenant_id}/ack",
              {"claim_token": token, "projected": projected, "failed": rep.failed})

    # Feed the inline lane from the context just computed, so it never has to aggregate
    # on the payment path. Best-effort by design: a decision-service outage must not fail
    # a batch that has already scored correctly (see counter_feed).
    rep.counters_published = counter_feed.publish(tenant_id, ctxs, now=now)
    return rep


@dataclass
class LosReport:
    evaluated: int = 0
    alerts: int = 0
    cases: int = 0
    fired_rules: dict[str, int] = field(default_factory=dict)
    unmeasurable: dict[str, str] = field(default_factory=dict)


def evaluate_los(db: Session, tenant_id: str, *, limit: int = 500,
                 catalogue: dict | None = None, policy: dict | None = None) -> LosReport:
    """Score pending loan_application / collateral_valuation events directly (BR-214).

    Unlike every other CBS-fed rule, these cannot be folded into a payment
    transaction's evaluation - an application has none, and may never have one if it is
    refused. So this claims pending events for itself (``FOR UPDATE SKIP LOCKED``, same
    concurrency guard a queue needs), scores each once, and marks it ``projected`` so a
    second run does not re-alert on the same application.

    Callable on its own - a LOS feed can arrive on a schedule with nothing to do with
    the payment batch cadence - and called from the tail of ``run_once`` so the common
    test/ops path (``run_until_empty``) exercises it without extra wiring.
    """
    rep = LosReport()
    catalogue = catalogue if catalogue is not None else active_rules(tenant_id)
    policy = policy if policy is not None else policy_values(tenant_id)
    if not catalogue:
        return rep

    now = datetime.now(timezone.utc)
    rows = db.execute(text(
        "SELECT id, kind, ts, account, amount_paise, attributes, source "
        "  FROM ingestion.cbs_events "
        " WHERE tenant_id = :t AND kind = ANY(:kinds) AND state = 'pending' "
        " ORDER BY ts LIMIT :lim FOR UPDATE SKIP LOCKED"),
        {"t": tenant_id, "kinds": list(LOS_EVENT_KINDS), "lim": limit}).mappings().all()
    if not rows:
        return rep

    processed_ids = []
    for row in rows:
        event = dict(row)
        processed_ids.append(event["id"])
        rep.evaluated += 1

        if event["kind"] == "loan_application":
            observations = los_features.observe_application(db, tenant_id, event, now=now)
        else:
            observations = los_features.observe_valuation(db, tenant_id, event, now=now)

        fired = []
        for rule_id, observed in observations.items():
            rule = catalogue.get(rule_id)
            if not rule or rule.get("threshold") is None:
                continue
            threshold = float(rule["threshold"])
            fires = (observed <= threshold if rule.get("comparator") == "lte"
                     else observed >= threshold)
            if fires:
                fired.append((rule_id, rule, observed, threshold))

        if fired:
            score = scoring.score_of(r[1].get("family", "") for r in fired)
            severity = _severity(score, policy)
            version = next((r[1].get("version") for r in fired if r[1].get("version")),
                           "1.0.0")

            # No payment transaction exists for an application - may never exist, if it
            # is refused. Built with exactly the keys _attach_case and FactAlert read;
            # neither requires a matching fact_transaction row to work correctly. Known,
            # accepted boundary: _attach_case's own-account lookup joins through
            # fact_transaction, so a later *payment*-sourced alert on this same account
            # will not auto-merge into a case opened from this application alone.
            pseudo_txn = {
                "txn_id": "APP-" + event["id"], "debtor_account": event["account"],
                "amount_paise": int(event["amount_paise"] or 0), "rail": "LOS",
                "region": "", "product": "loan", "customer_segment": "retail",
                "source": event["source"] or "live",
            }
            case_id = None
            if severity in ("critical", "high"):
                case_id = _attach_case(db, tenant_id, pseudo_txn, severity, score,
                                       fired, now)
                if case_id:
                    rep.cases += 1

            for rule_id, rule, observed, threshold in fired:
                db.add(FactAlert(
                    alert_id="A" + uuid.uuid4().hex[:16], tenant_id=tenant_id,
                    txn_id=pseudo_txn["txn_id"], ts=event["ts"],
                    rule_family=rule.get("family", ""), rule_id=rule_id,
                    typology=TYPOLOGY.get(rule.get("family", ""), "Unclassified"),
                    score=float(score), severity=severity, disposition="pending",
                    case_id=case_id, config_version=rule.get("version") or version,
                    sub_rule_ref=rule.get("sub_rule_ref") or "",
                    matched_reason=rule.get("reason") or "Threshold exceeded",
                    observed_value=float(observed), threshold_value=threshold,
                    observed_unit=rule.get("observed_unit") or "",
                    source=pseudo_txn["source"]))
                rep.alerts += 1
                rep.fired_rules[rule_id] = rep.fired_rules.get(rule_id, 0) + 1

    db.execute(text(
        "UPDATE ingestion.cbs_events SET state = 'projected' WHERE id = ANY(:ids)"),
        {"ids": processed_ids})
    db.commit()

    seen_kinds = {r[0] for r in db.execute(text(
        "SELECT DISTINCT kind FROM ingestion.cbs_events WHERE tenant_id = :t"),
        {"t": tenant_id}).all()}
    for rule_id, why in los_features.unmeasurable(seen_kinds).items():
        if rule_id in catalogue:
            rep.unmeasurable[rule_id] = why

    return rep


def _attach_case(db: Session, tenant_id: str, txn: dict, severity: str, score: float,
                 fired: list, now: datetime) -> str | None:
    """Join an open case for this account if one exists, else open one.

    Grouping by account rather than by transaction is what makes a mule ring one
    investigation instead of forty unrelated alerts.
    """
    existing = db.execute(text(
        "SELECT c.case_id FROM analytics.fact_case c "
        "WHERE c.tenant_id = :t AND c.state NOT IN ('closed_fraud', 'exonerated') "
        "  AND EXISTS (SELECT 1 FROM analytics.fact_alert a "
        "              JOIN analytics.fact_transaction x ON x.txn_id = a.txn_id "
        "              WHERE a.case_id = c.case_id AND a.tenant_id = :t "
        "                AND x.debtor_account = :acct) "
        "ORDER BY c.opened_ts DESC LIMIT 1"),
        {"t": tenant_id, "acct": txn["debtor_account"]}).first()
    if existing:
        case_id = existing[0]
        db.execute(text(
            "UPDATE analytics.fact_case SET amount_paise = amount_paise + :amt "
            "WHERE case_id = :c AND tenant_id = :t"),
            {"amt": int(txn["amount_paise"]), "c": case_id, "t": tenant_id})
        return case_id

    case_id = "C" + uuid.uuid4().hex[:16]
    db.add(FactCase(
        case_id=case_id, tenant_id=tenant_id, opened_ts=now, state="under_review",
        severity=severity, fmr_category="others",
        amount_paise=int(txn["amount_paise"]), recovered_paise=0, rfa_flag=False,
        rail=txn["rail"], region=txn.get("region", ""),
        product=txn.get("product", "savings"),
        customer_segment=txn.get("customer_segment", "retail"), assignee="",
        # A case is only as live as the traffic behind it (BR-611). Inherited here so a
        # return can be refused on the case without re-deriving it from the alerts.
        source=txn.get("source") or "live"))
    db.flush()
    return case_id


def run_until_empty(db: Session, tenant_id: str, *, batch: int = 500,
                    max_batches: int = 50) -> RunReport:
    """Drain the queue, bounded. An unbounded loop on a bad batch never terminates."""
    total = RunReport()
    for _ in range(max_batches):
        rep = run_once(db, tenant_id, limit=batch)
        total.claimed += rep.claimed
        total.projected += rep.projected
        total.duplicates += rep.duplicates
        total.alerts += rep.alerts
        total.cases += rep.cases
        total.failed.extend(rep.failed)
        for k, v in rep.fired_rules.items():
            total.fired_rules[k] = total.fired_rules.get(k, 0) + v
        total.unmeasurable = rep.unmeasurable or total.unmeasurable
        if rep.claimed == 0:
            break
    return total
