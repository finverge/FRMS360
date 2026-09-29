"""PostgreSQL implementation of the query engine.

All predicates are bound parameters - filter values never reach the SQL string. The
only interpolated fragments are the metric's own ``expr``/``where``, which come from the
in-code registry, not from user input.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..metrics import get_metric
from .. import rules as _rules
from .base import Filters, MetricResult, QueryEngine


def _num(v: Any) -> int | float:
    """Normalise a DB aggregate to a JSON-safe number.

    Postgres SUM() over BIGINT returns NUMERIC, which arrives as Decimal and would be
    serialised as a *string* by Pydantic. Integral values are returned as int so money
    (stored in paise) stays exact — converting paise to float would reintroduce the
    rounding error the integer-paise design exists to avoid.
    """
    if v is None:
        return 0
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v

# fact table + the column each filter dimension maps to, per base
_BASES = {
    "case": {
        "table": "fact_case", "ts": "opened_ts", "amount": "amount_paise",
        "dims": {"rail": "rail", "region": "region", "product": "product",
                 "segment": "customer_segment", "severity": "severity",
                 "state": "state", "fmr_category": "fmr_category",
                 "assignee": "assignee", "source": "source"},
    },
    "alert": {
        "table": "fact_alert", "ts": "ts", "amount": None,
        "dims": {"family": "rule_family", "severity": "severity",
                 "disposition": "disposition", "rule": "rule_id",
                 "analyst": "analyst", "config_version": "config_version",
                 "typology": "typology", "source": "source"},
    },
    "transaction": {
        "table": "fact_transaction", "ts": "ts", "amount": "amount_paise",
        "dims": {"rail": "rail", "region": "region", "product": "product",
                 "segment": "customer_segment", "status": "status", "device": "device_id",
                 "branch": "branch", "source": "source"},
    },
}

# Alert rows carry no rail/region/product of their own; join through the transaction
# when those filters are supplied so an alert figure filtered by rail still partitions.
_ALERT_JOIN = (
    " JOIN fact_transaction t ON t.txn_id = a.txn_id AND t.tenant_id = a.tenant_id"
)


class PostgresEngine(QueryEngine):
    def __init__(self, session: Session):
        self.db = session

    # ---------- predicate construction ----------
    def _where(self, base: str, f: Filters, alias: str) -> tuple[list[str], dict]:
        spec = _BASES[base]
        clauses = [f"{alias}.tenant_id = :tenant_id"]
        params: dict[str, Any] = {"tenant_id": f.tenant_id}

        if f.date_from:
            clauses.append(f"{alias}.{spec['ts']} >= :date_from"); params["date_from"] = f.date_from
        if f.date_to:
            clauses.append(f"{alias}.{spec['ts']} < :date_to"); params["date_to"] = f.date_to

        def _in(values: list[str], column: str, key: str) -> None:
            if values:
                keys = [f"{key}{i}" for i in range(len(values))]
                clauses.append(f"{column} IN ({', '.join(':' + k for k in keys)})")
                params.update(dict(zip(keys, values)))

        dims = spec["dims"]
        # Provenance (BR-611). Every fact carries its own source column, so this needs no
        # join and applies identically to a metric, a breakdown and a drill row - which is
        # what stops a filtered dashboard and its drill-down disagreeing about the mix.
        _in(f.sources, f"{alias}.source", "src")

        # Dimensions the fact owns directly.
        if "severity" in dims: _in(f.severities, f"{alias}.{dims['severity']}", "sev")
        if "state" in dims: _in(f.states, f"{alias}.{dims['state']}", "st")
        if "fmr_category" in dims: _in(f.fmr_categories, f"{alias}.{dims['fmr_category']}", "fmr")
        if "family" in dims: _in(f.families, f"{alias}.{dims['family']}", "fam")
        if "disposition" in dims: _in(f.dispositions, f"{alias}.{dims['disposition']}", "disp")

        # Signal-level filters (alert fact only).
        if "rule" in dims: _in(f.rules, f"{alias}.rule_id", "rul")
        if "typology" in dims: _in(f.typologies, f"{alias}.typology", "typ")
        if "analyst" in dims: _in(f.analysts, f"{alias}.analyst", "ana")
        if "config_version" in dims: _in(f.config_versions, f"{alias}.config_version", "cfg")
        # Case-level filters.
        if "assignee" in dims: _in(f.assignees, f"{alias}.assignee", "asg")
        if base == "case":
            if f.rfa_only:
                clauses.append(f"{alias}.rfa_flag = true")
            if f.nj_breach_only:
                clauses.append(f"({alias}.response_due_ts IS NOT NULL "
                               f"AND {alias}.decision_ts IS NOT NULL "
                               f"AND {alias}.decision_ts > {alias}.response_due_ts)")
            for col, status in (("fmr", f.fmr_status), ("str", f.str_status)):
                if status == "filed":
                    clauses.append(f"{alias}.{col}_filed_ts IS NOT NULL")
                elif status == "due":
                    clauses.append(f"{alias}.{col}_due_ts IS NOT NULL "
                                   f"AND {alias}.{col}_filed_ts IS NULL")
                elif status == "overdue":
                    clauses.append(f"{alias}.{col}_due_ts IS NOT NULL "
                                   f"AND {alias}.{col}_filed_ts IS NULL "
                                   f"AND {alias}.{col}_due_ts < NOW()")

        # Branch lives on the transaction; for alerts reach it through the join.
        if f.branches:
            bcol = "t.branch" if base == "alert" else (f"{alias}.branch" if base == "transaction" else None)
            if bcol:
                keys = [f"br{i}" for i in range(len(f.branches))]
                clauses.append(f"{bcol} IN ({', '.join(':' + k for k in keys)})")
                params.update(dict(zip(keys, f.branches)))

        if f.account:
            acct_col = "t" if base == "alert" else alias
            if base != "case":   # fact_case has no account column
                clauses.append(f"({acct_col}.debtor_account = :acct "
                               f"OR {acct_col}.creditor_account = :acct)")
                params["acct"] = f.account

        if f.id_search:
            params["idq"] = f"%{f.id_search.strip()}%"
            if base == "case":
                clauses.append(f"{alias}.case_id ILIKE :idq")
            elif base == "alert":
                clauses.append(f"({alias}.alert_id ILIKE :idq OR {alias}.rule_id ILIKE :idq "
                               f"OR t.debtor_account ILIKE :idq OR t.creditor_account ILIKE :idq)")
            else:  # transaction
                clauses.append(f"({alias}.txn_id ILIKE :idq OR {alias}.debtor_account ILIKE :idq "
                               f"OR {alias}.creditor_account ILIKE :idq)")

        if base == "alert":
            # rail/region/product/segment live on the joined transaction
            _in(f.rails, "t.rail", "rail"); _in(f.regions, "t.region", "reg")
            _in(f.products, "t.product", "prod"); _in(f.segments, "t.customer_segment", "seg")
            if f.min_amount_paise is not None:
                clauses.append("t.amount_paise >= :min_amt"); params["min_amt"] = f.min_amount_paise
            if f.max_amount_paise is not None:
                clauses.append("t.amount_paise <= :max_amt"); params["max_amt"] = f.max_amount_paise
        else:
            if "rail" in dims: _in(f.rails, f"{alias}.rail", "rail")
            if "region" in dims: _in(f.regions, f"{alias}.region", "reg")
            if "product" in dims: _in(f.products, f"{alias}.product", "prod")
            if "segment" in dims: _in(f.segments, f"{alias}.customer_segment", "seg")
            amt = spec["amount"]
            if amt and f.min_amount_paise is not None:
                clauses.append(f"{alias}.{amt} >= :min_amt"); params["min_amt"] = f.min_amount_paise
            if amt and f.max_amount_paise is not None:
                clauses.append(f"{alias}.{amt} <= :max_amt"); params["max_amt"] = f.max_amount_paise

        return clauses, params

    def _from(self, base: str, alias: str) -> str:
        table = _BASES[base]["table"]
        if base == "alert":
            return f"{table} {alias}{_ALERT_JOIN}"
        return f"{table} {alias}"

    # ---------- interface ----------
    def _fmt(self, fragment: str, alias: str, tenant_id: str) -> str:
        """Substitute the table alias and this tenant's policy knobs.

        Metric SQL carries ``{b}`` for the alias and ``{sla_breach_hours}``-style
        placeholders for policy values, so one board-approved number drives detection,
        the compliance clocks and the dashboards alike.
        """
        return fragment.format(b=alias, **_rules.policy_values(tenant_id))

    def metric(self, name: str, filters: Filters) -> MetricResult:
        m = get_metric(name)
        alias = "a" if m.base == "alert" else "x"
        clauses, params = self._where(m.base, filters, alias)
        if m.where:
            clauses.append(f"({self._fmt(m.where, alias, filters.tenant_id)})")
        expr = self._fmt(m.expr, alias, filters.tenant_id)
        sql = f"SELECT {expr} FROM {self._from(m.base, alias)} WHERE {' AND '.join(clauses)}"
        value = self.db.execute(text(sql), params).scalar()
        return MetricResult(m.name, m.label, _num(value), m.unit, m.volatile)

    def breakdown(self, name: str, dimension: str, filters: Filters) -> list[dict[str, Any]]:
        m = get_metric(name)
        spec = _BASES[m.base]
        alias = "a" if m.base == "alert" else "x"
        if dimension in spec["dims"]:
            col = f"{alias}.{spec['dims'][dimension]}"
        elif m.base == "alert" and dimension in ("rail", "region", "product", "segment"):
            col = "t." + ("customer_segment" if dimension == "segment" else dimension)
        else:
            raise KeyError(f"Cannot break '{name}' down by '{dimension}'")

        clauses, params = self._where(m.base, filters, alias)
        if m.where:
            clauses.append(f"({self._fmt(m.where, alias, filters.tenant_id)})")
        expr = self._fmt(m.expr, alias, filters.tenant_id)
        sql = (f"SELECT {col} AS k, {expr} AS v FROM {self._from(m.base, alias)} "
               f"WHERE {' AND '.join(clauses)} GROUP BY {col} ORDER BY v DESC")
        return [{"key": r[0], "value": _num(r[1])} for r in self.db.execute(text(sql), params)]

    def score_sample(self, filters: Filters, limit: int = 20000) -> list[float]:
        """Alert scores for the filtered window - the population drift is measured on."""
        alias = "a"
        clauses, params = self._where("alert", filters, alias)
        params["lim"] = limit
        sql = (f"SELECT {alias}.score FROM {self._from('alert', alias)} "
               f"WHERE {' AND '.join(clauses)} ORDER BY {alias}.ts DESC LIMIT :lim")
        return [float(r[0]) for r in self.db.execute(text(sql), params) if r[0] is not None]

    def count(self, entity: str, filters: Filters) -> int:
        """How many rows match, ignoring paging.

        Needed so the console can say "51-100 of 1,234" rather than implying the first
        page is the whole result - which is what a 40-row cap silently did.
        """
        alias = "a" if entity == "alert" else "x"
        clauses, params = self._where(entity, filters, alias)
        sql = (f"SELECT COUNT(*) FROM {self._from(entity, alias)} "
               f"WHERE {' AND '.join(clauses)}")
        return int(_num(self.db.execute(text(sql), params).scalar()))

    def rows(self, entity: str, filters: Filters, limit: int, offset: int) -> list[dict[str, Any]]:
        alias = "a" if entity == "alert" else "x"
        clauses, params = self._where(entity, filters, alias)
        params.update({"lim": limit, "off": offset})
        order = _BASES[entity]["ts"]
        cols = {
            "alert": (f"{alias}.alert_id, {alias}.ts, {alias}.rule_family, {alias}.rule_id, "
                      f"{alias}.sub_rule_ref, {alias}.severity, {alias}.score, "
                      f"{alias}.matched_reason, {alias}.observed_value, "
                      f"{alias}.threshold_value, {alias}.observed_unit, "
                      f"{alias}.disposition, {alias}.case_id, "
                      f"{alias}.config_version, t.rail, t.amount_paise, t.debtor_account"),
            "case": (f"{alias}.case_id, {alias}.opened_ts, {alias}.state, {alias}.severity, "
                     f"{alias}.fmr_category, {alias}.amount_paise, {alias}.recovered_paise, "
                     f"{alias}.rfa_flag, {alias}.rail, {alias}.region, {alias}.assignee, "
                     f"{alias}.response_due_ts, {alias}.decision_ts, {alias}.fmr_due_ts, "
                     f"{alias}.fmr_filed_ts"),
            "transaction": (f"{alias}.txn_id, {alias}.ts, {alias}.rail, {alias}.amount_paise, "
                            f"{alias}.debtor_account, {alias}.creditor_account, {alias}.region, "
                            f"{alias}.product, {alias}.customer_segment, {alias}.status, "
                            f"{alias}.device_id, {alias}.ip_addr"),
        }[entity]
        sql = (f"SELECT {cols} FROM {self._from(entity, alias)} WHERE {' AND '.join(clauses)} "
               f"ORDER BY {alias}.{order} DESC LIMIT :lim OFFSET :off")
        res = self.db.execute(text(sql), params)
        keys = list(res.keys())
        return [dict(zip(keys, row)) for row in res]

    def evidence(self, tenant_id: str, entity: str, ident: str) -> dict[str, Any]:
        """Stage 6: one record plus everything needed to defend the decision.

        For an alert this returns the alert, the transaction it scored, the case it
        belongs to, and the sibling alerts on that case — with the config version that
        was in force when the score was produced.
        """
        p = {"tenant_id": tenant_id, "id": ident}

        def one(sql: str) -> dict | None:
            res = self.db.execute(text(sql), p)
            keys = list(res.keys())
            row = res.first()
            return dict(zip(keys, row)) if row else None

        def many(sql: str, extra: dict | None = None) -> list[dict]:
            res = self.db.execute(text(sql), {**p, **(extra or {})})
            keys = list(res.keys())
            return [dict(zip(keys, r)) for r in res]

        out: dict[str, Any] = {"entity": entity, "id": ident}

        if entity == "alert":
            alert = one("SELECT * FROM fact_alert WHERE tenant_id=:tenant_id AND alert_id=:id")
            if not alert:
                return {"error": "not found"}
            out["alert"] = alert
            out["transaction"] = one(
                "SELECT * FROM fact_transaction WHERE tenant_id=:tenant_id "
                "AND txn_id=(SELECT txn_id FROM fact_alert WHERE tenant_id=:tenant_id AND alert_id=:id)")
            if alert.get("case_id"):
                out["case"] = one(
                    "SELECT * FROM fact_case WHERE tenant_id=:tenant_id "
                    "AND case_id=(SELECT case_id FROM fact_alert WHERE tenant_id=:tenant_id AND alert_id=:id)")
                out["sibling_alerts"] = many(
                    "SELECT alert_id, rule_id, rule_family, severity, score, disposition "
                    "FROM fact_alert WHERE tenant_id=:tenant_id AND case_id="
                    "(SELECT case_id FROM fact_alert WHERE tenant_id=:tenant_id AND alert_id=:id)")

        elif entity == "case":
            case = one("SELECT * FROM fact_case WHERE tenant_id=:tenant_id AND case_id=:id")
            if not case:
                return {"error": "not found"}
            out["case"] = case
            out["alerts"] = many(
                "SELECT alert_id, txn_id, rule_id, sub_rule_ref, rule_family, severity, score, "
                "matched_reason, observed_value, threshold_value, observed_unit, disposition, "
                "config_version FROM fact_alert WHERE tenant_id=:tenant_id AND case_id=:id")
            # DISTINCT matters: one transaction can carry several alerts when it trips
            # several rules, and joining through them would list it several times and
            # count its value once per alert. Same trap as R-03.
            out["transactions"] = many(
                "SELECT DISTINCT ON (t.txn_id) t.* FROM fact_transaction t "
                "JOIN fact_alert a ON a.txn_id=t.txn_id AND a.tenant_id=t.tenant_id "
                "WHERE t.tenant_id=:tenant_id AND a.case_id=:id ORDER BY t.txn_id")
            # The R-03 invariant, shown for this single case so the figure is defensible.
            derived = sum(t["amount_paise"] for t in out["transactions"])
            out["value_check"] = {
                "stated_paise": case["amount_paise"], "derived_paise": derived,
                "reconciled": case["amount_paise"] == derived,
            }

        elif entity == "transaction":
            txn = one("SELECT * FROM fact_transaction WHERE tenant_id=:tenant_id AND txn_id=:id")
            if not txn:
                return {"error": "not found"}
            out["transaction"] = txn
            out["alerts"] = many(
                "SELECT alert_id, rule_id, sub_rule_ref, rule_family, severity, score, "
                "matched_reason, observed_value, threshold_value, observed_unit, disposition, "
                "config_version, case_id FROM fact_alert "
                "WHERE tenant_id=:tenant_id AND txn_id=:id")
        return out

    def timeseries(self, name: str, bucket: str, filters: Filters) -> list[dict[str, Any]]:
        """One metric bucketed over time. Uses the metric's own fact timestamp, so a
        trend line is the same definition as the headline number, just grouped by date."""
        if bucket not in ("day", "week", "month"):
            raise KeyError("bucket must be day | week | month")
        m = get_metric(name)
        alias = "a" if m.base == "alert" else "x"
        ts_col = f"{alias}.{_BASES[m.base]['ts']}"
        clauses, params = self._where(m.base, filters, alias)
        if m.where:
            clauses.append(f"({self._fmt(m.where, alias, filters.tenant_id)})")
        expr = self._fmt(m.expr, alias, filters.tenant_id)
        sql = (f"SELECT date_trunc('{bucket}', {ts_col}) AS k, {expr} AS v "
               f"FROM {self._from(m.base, alias)} WHERE {' AND '.join(clauses)} "
               f"GROUP BY 1 ORDER BY 1")
        return [{"key": r[0].isoformat() if r[0] else None, "value": _num(r[1])}
                for r in self.db.execute(text(sql), params)]

    def network(self, tenant_id: str, filters: Filters, case_id: str | None = None,
                account: str | None = None, limit: int = 300) -> dict[str, Any]:
        """Account-to-account graph behind a case or an account.

        Edges are aggregated per account pair (count + value) rather than one edge per
        transaction, because a mule hub with 200 payments should read as one thick edge
        per counterparty, not 200 hairlines.
        """
        params: dict[str, Any] = {"tenant_id": tenant_id, "lim": limit}
        joins, where = "", ["t.tenant_id = :tenant_id"]

        if case_id:
            joins = (" JOIN fact_alert a ON a.txn_id = t.txn_id "
                     "AND a.tenant_id = t.tenant_id")
            where.append("a.case_id = :case_id")
            params["case_id"] = case_id
        elif account:
            where.append("(t.debtor_account = :acct OR t.creditor_account = :acct)")
            params["acct"] = account
        else:
            # Unscoped: show the busiest structure rather than the whole book.
            where.append(
                "t.txn_id IN (SELECT txn_id FROM fact_alert "
                "WHERE tenant_id = :tenant_id AND rule_family = 'LAY')")

        if filters.date_from:
            where.append("t.ts >= :date_from"); params["date_from"] = filters.date_from
        if filters.date_to:
            where.append("t.ts < :date_to"); params["date_to"] = filters.date_to

        sql = (f"SELECT t.debtor_account AS src, t.creditor_account AS dst, "
               f"COUNT(*) AS n, SUM(t.amount_paise) AS value "
               f"FROM fact_transaction t{joins} WHERE {' AND '.join(where)} "
               f"GROUP BY 1, 2 ORDER BY value DESC LIMIT :lim")

        edges, degree, flow_in, flow_out = [], {}, {}, {}
        for src, dst, n, value in self.db.execute(text(sql), params):
            edges.append({"source": src, "target": dst,
                          "count": _num(n), "value_paise": _num(value)})
            for acct in (src, dst):
                degree[acct] = degree.get(acct, 0) + 1
            flow_in[dst] = flow_in.get(dst, 0) + _num(value)
            flow_out[src] = flow_out.get(src, 0) + _num(value)

        nodes = []
        for acct, deg in degree.items():
            inflow, outflow = flow_in.get(acct, 0), flow_out.get(acct, 0)
            # A hub both collects and disburses across many counterparties - the
            # fan-in/fan-out signature.
            role = ("hub" if inflow and outflow and deg >= 6
                    else "collector" if inflow > outflow
                    else "disburser")
            nodes.append({"id": acct, "degree": deg, "role": role,
                          "in_paise": inflow, "out_paise": outflow})
        nodes.sort(key=lambda x: -x["degree"])
        return {"nodes": nodes, "edges": edges,
                "scope": {"case_id": case_id, "account": account}}

    def mule_risk_indicators(self, tenant_id: str, account: str) -> dict[str, Any]:
        """Behavioural, network and velocity signals for one account - the quick read
        an investigator does before deciding whether to widen into the full graph.

        Every figure here is a real aggregate over fact_transaction. This product has
        no sanctions/adverse-media screening and no geolocation capture, so this does
        not attempt a "risk score" or a location list - distinct branches/regions
        stand in for the latter, honestly labelled, rather than a fabricated map pin.
        """
        now = datetime.now(timezone.utc)
        params = {
            "tenant_id": tenant_id, "acct": account,
            "w1d": now - timedelta(days=1), "w1w": now - timedelta(days=7),
            "w1m": now - timedelta(days=30),
        }
        sql = """
            SELECT
              COUNT(DISTINCT device_id) AS linked_devices,
              COUNT(DISTINCT ip_addr) AS linked_ips,
              COUNT(DISTINCT branch) AS distinct_branches,
              COUNT(DISTINCT region) AS distinct_regions,
              COUNT(DISTINCT CASE WHEN debtor_account = :acct THEN creditor_account
                                   WHEN creditor_account = :acct THEN debtor_account END) AS linked_accounts,
              COUNT(DISTINCT CASE WHEN creditor_account = :acct THEN debtor_account END) AS funds_in_counterparties,
              COUNT(DISTINCT CASE WHEN debtor_account = :acct THEN creditor_account END) AS funds_out_counterparties,
              COUNT(*) FILTER (WHERE creditor_account = :acct AND ts >= :w1d) AS funds_in_1d,
              COUNT(*) FILTER (WHERE creditor_account = :acct AND ts >= :w1w) AS funds_in_1w,
              COUNT(*) FILTER (WHERE creditor_account = :acct AND ts >= :w1m) AS funds_in_1m,
              COUNT(*) FILTER (WHERE debtor_account = :acct AND ts >= :w1d) AS funds_out_1d,
              COUNT(*) FILTER (WHERE debtor_account = :acct AND ts >= :w1w) AS funds_out_1w,
              COUNT(*) FILTER (WHERE debtor_account = :acct AND ts >= :w1m) AS funds_out_1m,
              COALESCE(SUM(amount_paise) FILTER (WHERE creditor_account = :acct), 0) AS funds_in_paise,
              COALESCE(SUM(amount_paise) FILTER (WHERE debtor_account = :acct), 0) AS funds_out_paise,
              COALESCE(AVG(amount_paise), 0) AS avg_txn_paise,
              COALESCE(MAX(amount_paise), 0) AS max_txn_paise,
              COUNT(*) AS total_txn_count
            FROM fact_transaction
            WHERE tenant_id = :tenant_id AND (debtor_account = :acct OR creditor_account = :acct)
        """
        row = self.db.execute(text(sql), params).mappings().first()
        funds_in = _num(row["funds_in_paise"])
        funds_out = _num(row["funds_out_paise"])
        return {
            "account": account,
            "total_txn_count": _num(row["total_txn_count"]),
            "network_linkage": {
                "linked_devices": _num(row["linked_devices"]),
                "linked_accounts": _num(row["linked_accounts"]),
                "linked_ips": _num(row["linked_ips"]),
                "distinct_branches": _num(row["distinct_branches"]),
                "distinct_regions": _num(row["distinct_regions"]),
            },
            "transaction_flow": {
                "funds_in_counterparties": _num(row["funds_in_counterparties"]),
                "funds_out_counterparties": _num(row["funds_out_counterparties"]),
                "funds_in_txn_count": {
                    "1d": _num(row["funds_in_1d"]), "1w": _num(row["funds_in_1w"]), "1m": _num(row["funds_in_1m"]),
                },
                "funds_out_txn_count": {
                    "1d": _num(row["funds_out_1d"]), "1w": _num(row["funds_out_1w"]), "1m": _num(row["funds_out_1m"]),
                },
                "max_txn_paise": _num(row["max_txn_paise"]),
            },
            "velocity": {
                "funds_in_paise": funds_in,
                "funds_out_paise": funds_out,
                "net_flow_paise": funds_in - funds_out,
                "avg_txn_paise": _num(row["avg_txn_paise"]),
            },
        }

    def filtered_scalar(self, base: str, expr: str, filters: Filters,
                        extra_where: str = "") -> Any:
        """Run a raw aggregate under the SAME filter predicates the metrics use.

        Reconciliation compares a metric against an independently-written query; that
        comparison is only meaningful if both see the same slice of data. Using
        ``scalar_sql`` here (tenant-only) made every consistency check fail as soon as a
        user applied a filter.
        """
        alias = "a" if base == "alert" else "x"
        clauses, params = self._where(base, filters, alias)
        if extra_where:
            clauses.append(f"({self._fmt(extra_where, alias, filters.tenant_id)})")
        sql = (f"SELECT {self._fmt(expr, alias, filters.tenant_id)} FROM {self._from(base, alias)} "
               f"WHERE {' AND '.join(clauses)}")
        return _num(self.db.execute(text(sql), params).scalar())

    def scalar_sql(self, sql: str, params: dict) -> Any:
        return self.db.execute(text(sql), params).scalar()

    def all_sql(self, sql: str, params: dict) -> list[tuple]:
        return list(self.db.execute(text(sql), params))
