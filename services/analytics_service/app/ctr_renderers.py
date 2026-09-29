"""Turn a built CTR into something that can be handed over.

Two outputs - the structured record for the platform's own systems, and a pack a
compliance officer reads before filing. No XML: unlike FMR/STR, no supervisory channel's
CTR schema has been examined yet, so there is nothing concrete to shape one against - see
``ctr.FORMAT_BINDING``.
"""
from __future__ import annotations

import json

NOT_A_SUBMISSION = (
    "Prepared by the platform for review and onward submission. This is not FIU-IND's "
    "own filing format - see the mapping note at the end."
)


def to_json(payload: dict, validation: dict) -> str:
    return json.dumps({"return": payload, "validation": validation,
                       "disclaimer": NOT_A_SUBMISSION}, indent=2, default=str)


def _esc(value) -> str:
    return (str(value) if value is not None else "—") \
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _rows(pairs) -> str:
    return "".join(
        f"<tr><th>{_esc(label)}</th><td>{_esc(value)}</td></tr>"
        for label, value in pairs if value not in (None, "", [], {}))


def to_html(payload: dict, validation: dict) -> str:
    ready = validation.get("ready")
    banner = (
        f'<div class="ok">Every mandatory field is present and the Rs 10,00,000 '
        f'threshold is met. {_esc(NOT_A_SUBMISSION)}</div>' if ready else
        '<div class="bad"><b>Not ready to file.</b> '
        + str(len(validation.get("blocking", []))) +
        ' mandatory item(s) outstanding.</div>')

    gaps = ""
    all_gaps = validation.get("blocking", []) + validation.get("advisory", [])
    if all_gaps:
        gaps = ("<h2>Outstanding information</h2><table>" + "".join(
            f'<tr><th>{_esc(g["label"])}</th>'
            f'<td>{"<b>Required</b>" if g["mandatory"] else "Advisory"} — '
            f'{_esc(g["why"])}</td></tr>' for g in all_gaps) + "</table>")

    ev = payload.get("evidence", {})
    txns = "".join(
        f'<tr><td class="mono">{_esc(t["event_id"])}</td><td>{_esc(t["at"])}</td>'
        f'<td>{_esc(t["direction"])}</td>'
        f'<td class="num">{_esc(t["amount"])}</td></tr>'
        for t in ev.get("transactions", []))

    binding_needs = ("The current FINnet CTR schema and the entity's reporting "
                     "credentials.")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{_esc(payload.get('return_label'))} — {_esc(payload.get('account'))} —
{_esc(payload.get('period'))}</title>
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
<p class="sub">Account {_esc(payload.get('account'))} · {_esc(payload.get('period'))} ·
  {_esc(payload.get('entity_name'))} · for submission to
  {_esc(payload.get('reported_to'))}</p>
{banner}

<h2>Reporting entity</h2>
<table>{_rows([
    ("Entity", payload.get("entity_name")),
    ("Category", payload.get("entity_type")),
])}</table>

<h2>Aggregation</h2>
<table>{_rows([
    ("Account", payload.get("account")),
    ("Period", payload.get("period")),
    ("Transaction count", payload.get("transaction_count")),
    ("Total cash value (INR)", payload.get("total_amount")),
    ("Of which deposits (INR)", payload.get("deposit_amount")),
    ("Of which withdrawals (INR)", payload.get("withdrawal_amount")),
    ("First transaction", payload.get("first_transaction_date")),
    ("Last transaction", payload.get("last_transaction_date")),
    ("Rs 10,00,000 threshold met",
     "Yes" if payload.get("threshold_met") else "No"),
])}</table>

{gaps}

{f'<h2>Underlying cash transactions</h2><table><tr><th>Reference</th><th>When</th>'
 f'<th>Direction</th><th>Amount (INR)</th></tr>{txns}</table>' if txns else ''}

<div class="note"><b>How this is submitted.</b> This pack contains the information PMLA
Rule 3 requires, assembled from cash-transaction events reported by the core banking
system and verifiable against them. It is not FIU-IND's own FINnet file format. To
submit, the structured record (JSON, alongside this document) is mapped to that format —
{_esc(binding_needs)}</div>

<footer>Schema version {_esc(payload.get('schema_version'))} ·
generated {_esc(payload.get('generated_at'))} · every figure here is derived from
reported cash-transaction events and can be reconciled against them.</footer>
</body></html>"""


RENDERERS = {"json": to_json, "html": to_html}
MEDIA_TYPES = {"json": "application/json", "html": "text/html; charset=utf-8"}
