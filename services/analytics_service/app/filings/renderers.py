"""Turn a built return into something that can be handed over.

Three outputs, for three different readers:

``json``  the record itself, for a bank's own systems and for the eventual mapping into
          the regulator's format.
``xml``   the same content in a structured document, which is the shape most supervisory
          channels expect and the easiest thing to transform.
``html``  a submission pack a person reads before filing - the part that is actually used
          today, because someone has to check a fraud declaration before it goes to RBI.

Every rendering carries the schema version, the generation time, and a banner saying what
the document is and is not. That banner is deliberate: the most damaging outcome here
would be someone treating the pack as the filed return.
"""
from __future__ import annotations

import json
from xml.etree import ElementTree as ET

from . import schema

NOT_A_SUBMISSION = (
    "Prepared by the platform for review and onward submission. This is not the "
    "regulator's own return format - see the mapping note at the end."
)


def to_json(payload: dict, validation: dict) -> str:
    return json.dumps({"return": payload, "validation": validation,
                       "disclaimer": NOT_A_SUBMISSION}, indent=2, default=str)


def _el(parent, tag: str, text=None, **attrs):
    node = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        node.text = str(text)
    return node


def to_xml(payload: dict, validation: dict) -> str:
    root = ET.Element("RegulatoryReturn", {
        "kind": payload.get("return_kind", ""),
        "schemaVersion": payload.get("schema_version", ""),
        "generatedAt": payload.get("generated_at", ""),
    })
    _el(root, "Disclaimer", NOT_A_SUBMISSION)

    entity = _el(root, "ReportingEntity")
    for tag, key in (("Name", "entity_name"), ("Category", "entity_type"),
                     ("GoverningDirection", "governing_direction"),
                     ("ReportedTo", "reported_to")):
        _el(entity, tag, payload.get(key, ""))

    case = _el(root, "Case", reference=payload.get("case_reference", ""))
    for tag, key in (("DetectionDate", "detection_date"),
                     ("OccurrenceDate", "occurrence_date"),
                     ("DeclarationDate", "declaration_date"),
                     ("Category", "fmr_category"),
                     ("AmountInvolved", "amount_involved"),
                     ("AmountRecovered", "amount_recovered"),
                     ("TransactionCount", "transaction_count"),
                     ("PeriodCovered", "period_covered"),
                     ("ModusOperandi", "modus_operandi"),
                     ("SuspicionGrounds", "suspicion_grounds"),
                     ("ApprovedBy", "approved_by"),
                     ("ProposedBy", "proposed_by"),
                     ("PrincipalOfficer", "principal_officer")):
        if payload.get(key) not in (None, ""):
            _el(case, tag, payload[key])

    accounts = _el(root, "AccountsInvolved")
    for acct in payload.get("accounts_involved", []):
        _el(accounts, "Account", acct)

    indicators = _el(root, "IndicatorsTriggered")
    for ind in payload.get("indicators_triggered", []):
        node = _el(indicators, "Indicator", ruleId=ind.get("rule_id", ""),
                   configVersion=ind.get("config_version", ""))
        _el(node, "Reason", ind.get("reason", ""))
        if ind.get("observed") is not None:
            _el(node, "Observed", ind["observed"], unit=ind.get("unit") or "")
            _el(node, "Threshold", ind.get("threshold"))

    ev = payload.get("evidence", {})
    docs = _el(root, "Documents")
    for d in ev.get("documents", []):
        _el(docs, "Document", d.get("filename", ""), type=d.get("type", ""),
            sha256=d.get("sha256", ""))

    hist = _el(root, "CaseHistory")
    for h in ev.get("case_history", []):
        _el(hist, "Event", h.get("reason") or "", action=h.get("action", ""),
            actor=h.get("actor", ""), role=h.get("role", ""), at=h.get("at", ""))

    txns = _el(root, "Transactions")
    for t in ev.get("transactions", []):
        _el(txns, "Transaction", "", id=t.get("txn_id", ""), at=t.get("at", ""),
            rail=t.get("rail", ""), amount=t.get("amount", ""),
            debtor=t.get("debtor", ""), creditor=t.get("creditor", ""))

    val = _el(root, "Validation", ready=str(validation.get("ready", False)).lower())
    for g in validation.get("blocking", []) + validation.get("advisory", []):
        _el(val, "Gap", g.get("why", ""), field=g.get("field", ""),
            mandatory=str(g.get("mandatory", False)).lower())

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + \
        ET.tostring(root, encoding="unicode")


def _esc(value) -> str:
    return (str(value) if value is not None else "—") \
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _rows(pairs) -> str:
    return "".join(
        f"<tr><th>{_esc(label)}</th><td>{_esc(value)}</td></tr>"
        for label, value in pairs if value not in (None, "", [], {}))


def to_html(payload: dict, validation: dict) -> str:
    """The pack a person checks before filing."""
    kind = payload.get("return_kind", "")
    ready = validation.get("ready")

    banner = (
        f'<div class="ok">Every mandatory field for this return is present. '
        f'{_esc(NOT_A_SUBMISSION)}</div>' if ready else
        '<div class="bad"><b>Not ready to file.</b> '
        + str(len(validation.get("blocking", []))) +
        ' mandatory field(s) cannot be evidenced from the case record.</div>')

    gaps = ""
    all_gaps = validation.get("blocking", []) + validation.get("advisory", [])
    if all_gaps:
        gaps = ("<h2>Outstanding information</h2><table>" + "".join(
            f'<tr><th>{_esc(g["label"])}</th>'
            f'<td>{"<b>Required</b>" if g["mandatory"] else "Advisory"} — '
            f'{_esc(g["why"])}</td></tr>' for g in all_gaps) + "</table>")

    indicators = payload.get("indicators_triggered") or []
    ind_html = ""
    if indicators:
        ind_html = "<h2>Indicators triggered</h2><table><tr><th>Indicator</th>" \
                   "<th>Observed</th><th>Threshold</th><th>Config</th></tr>" + "".join(
            f'<tr><td>{_esc(i["rule_id"])} — {_esc(i["reason"])}</td>'
            f'<td>{_esc(i.get("observed"))} {_esc(i.get("unit") or "")}</td>'
            f'<td>{_esc(i.get("threshold"))}</td>'
            f'<td>{_esc(i.get("config_version"))}</td></tr>' for i in indicators) \
            + "</table>"

    ev = payload.get("evidence", {})
    hist = "".join(
        f'<tr><td>{_esc(h["at"])}</td><td>{_esc(h["action"])}</td>'
        f'<td>{_esc(h["actor"])} ({_esc(h["role"])})</td>'
        f'<td>{_esc(h.get("reason"))}</td></tr>' for h in ev.get("case_history", []))
    docs = "".join(
        f'<tr><td>{_esc(d["type"])}</td><td>{_esc(d["filename"])}</td>'
        f'<td class="mono">{_esc(d["sha256"])[:16]}…</td></tr>'
        for d in ev.get("documents", []))
    txns = "".join(
        f'<tr><td class="mono">{_esc(t["txn_id"])}</td><td>{_esc(t["at"])}</td>'
        f'<td>{_esc(t["rail"])}</td><td class="num">{_esc(t["amount"])}</td>'
        f'<td class="mono">{_esc(t["debtor"])} → {_esc(t["creditor"])}</td></tr>'
        for t in ev.get("transactions", []))

    binding = schema.FORMAT_BINDINGS.get(kind, {})

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{_esc(payload.get('return_label'))} — {_esc(payload.get('case_reference'))}</title>
<style>
 body{{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#101820;
   margin:0;padding:32px;line-height:1.55;font-size:14px;background:#fff}}
 h1{{font-family:Georgia,serif;font-size:24px;margin:0 0 4px}}
 h2{{font-family:Georgia,serif;font-size:16px;margin:26px 0 8px;
   border-bottom:1px solid #dce5e5;padding-bottom:5px}}
 .sub{{color:#64777a;margin:0 0 18px;font-size:13px}}
 table{{border-collapse:collapse;width:100%;font-size:12.5px;margin-bottom:6px}}
 th,td{{border:1px solid #dce5e5;padding:7px 10px;text-align:left;vertical-align:top}}
 th{{background:#f7f9f9;width:230px;font-weight:600}}
 table tr th:only-of-type{{width:230px}}
 .mono{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px}}
 .num{{text-align:right;font-variant-numeric:tabular-nums}}
 .ok{{background:#e4f1e9;border-left:4px solid #17683f;padding:11px 14px;
   border-radius:0 6px 6px 0;margin-bottom:18px;font-size:13px}}
 .bad{{background:#f8e3e1;border-left:4px solid #a32218;padding:11px 14px;
   border-radius:0 6px 6px 0;margin-bottom:18px;font-size:13px}}
 .note{{background:#f7f9f9;border:1px solid #dce5e5;border-radius:8px;padding:13px 15px;
   font-size:12px;color:#35474b;margin-top:26px}}
 footer{{margin-top:22px;padding-top:12px;border-top:1px solid #dce5e5;
   font-size:11.5px;color:#64777a}}
</style></head><body>
<h1>{_esc(payload.get('return_label'))}</h1>
<p class="sub">Case {_esc(payload.get('case_reference'))} ·
  {_esc(payload.get('entity_name'))} · for submission to
  {_esc(payload.get('reported_to'))}</p>
{banner}

<h2>Reporting entity</h2>
<table>{_rows([
    ("Entity", payload.get("entity_name")),
    ("Category", payload.get("entity_type")),
    ("Governing direction", payload.get("governing_direction")),
    ("Principal Officer", payload.get("principal_officer")),
])}</table>

<h2>The fraud</h2>
<table>{_rows([
    ("Case reference", payload.get("case_reference")),
    ("Category", payload.get("fmr_category")),
    ("Date of occurrence", payload.get("occurrence_date")),
    ("Date of detection", payload.get("detection_date")),
    ("Date of declaration", payload.get("declaration_date")),
    ("Filing due", payload.get("filing_due")),
    ("Amount involved (INR)", payload.get("amount_involved")),
    ("Amount recovered (INR)", payload.get("amount_recovered")),
    ("Transactions", payload.get("transaction_count")),
    ("Period covered", payload.get("period_covered")),
    ("Rails used", ", ".join(payload.get("rails_used") or [])),
    ("Accounts involved", ", ".join(payload.get("accounts_involved") or [])),
    ("Referral to law enforcement required",
     "Yes — above the board-approved threshold"
     if payload.get("lea_referral_required") else None),
])}</table>

<h2>Modus operandi</h2>
<p>{_esc(payload.get("modus_operandi"))}</p>
{f'<h2>Grounds for suspicion</h2><p>{_esc(payload.get("suspicion_grounds"))}</p>'
 if payload.get("suspicion_grounds") else ""}

<h2>Due process</h2>
<table>{_rows([
    ("Show-cause issued", (payload.get("natural_justice") or {}).get("show_cause_issued")),
    ("Response due", (payload.get("natural_justice") or {}).get("response_due")),
    ("Window (days)", (payload.get("natural_justice") or {}).get("window_days")),
    ("Reasoned order on file",
     "Yes" if payload.get("reasoned_order_on_file") else
     ("No" if payload.get("reasoned_order_on_file") is False else None)),
    ("Proposed by", payload.get("proposed_by")),
    ("Approved by", payload.get("approved_by")),
])}</table>

{ind_html}
{gaps}

{f'<h2>Supporting documents</h2><table><tr><th>Type</th><th>File</th><th>SHA-256</th></tr>{docs}</table>' if docs else ''}
{f'<h2>Case history</h2><table><tr><th>When</th><th>Action</th><th>By</th><th>Reason</th></tr>{hist}</table>' if hist else ''}
{f'<h2>Underlying transactions</h2><table><tr><th>Reference</th><th>When</th><th>Rail</th><th>Amount (INR)</th><th>Accounts</th></tr>{txns}</table>' if txns else ''}

<div class="note"><b>How this is submitted.</b> This pack contains the information the
Directions require, assembled from the case record and verifiable against it. It is not
the {_esc(binding.get('channel', 'regulator'))} file format. To submit, the structured
record (XML or JSON, alongside this document) is mapped to that format —
{_esc(binding.get('needs', 'the current template is required.'))}</div>

<footer>Schema version {_esc(payload.get('schema_version'))} ·
generated {_esc(payload.get('generated_at'))} · every figure here is derived from the
case record and can be reconciled against it.</footer>
</body></html>"""


RENDERERS = {"json": to_json, "xml": to_xml, "html": to_html}
MEDIA_TYPES = {"json": "application/json", "xml": "application/xml",
               "html": "text/html; charset=utf-8"}
