"""Render a stored board pack.

Rendering reads the stored payload and nothing else. That is the point of the whole
feature: opening last year's Q2 pack must show what the committee saw in Q2, not what
today's database would say about Q2.
"""
from __future__ import annotations

import html
import json
from datetime import datetime

DISCLAIMER = (
    "Prepared by the Fraud Risk Management platform from the metric registry. "
    "Figures are a point-in-time snapshot of the period stated and are reproduced from "
    "the record made when the pack was generated."
)


def _rupees(paise) -> str:
    if paise is None:
        return "—"
    return f"₹{int(paise) / 100:,.2f}"


def _fmt_value(m: dict) -> str:
    if not m.get("available", True):
        return f"not available — {html.escape(str(m.get('why', 'unknown')))}"
    v = m.get("value")
    if v is None:
        return "—"
    # The registry's own unit vocabulary; see metrics.Metric.unit. Getting this wrong is
    # not cosmetic - it printed ₹235 crore as a bare "2,350,250,027" in a board document.
    unit = m.get("unit", "")
    if unit == "inr_paise":
        return _rupees(v)
    if unit == "ratio":
        return f"{float(v) * 100:.1f}%"
    if unit == "seconds":
        return f"{float(v) / 3600:,.1f} h"
    if unit in ("hours", "days"):
        return f"{float(v):,.1f} {unit}"
    return f"{int(v):,}" if float(v).is_integer() else f"{float(v):,.2f}"


def _date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y")
    except Exception:  # noqa: BLE001
        return iso


def to_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def to_html(payload: dict, *, pack: dict | None = None) -> str:
    e = payload.get("entity", {}) or {}
    p = payload.get("period", {}) or {}
    pol = payload.get("policy", {}) or {}
    pack = pack or {}
    esc = html.escape

    head = f"""<h1>Fraud Risk Management — Board / Audit Committee Pack</h1>
<table class="meta">
  <tr><th>Institution</th><td>{esc(str(e.get('legal_name') or e.get('display_name') or '—'))}</td></tr>
  <tr><th>Entity type</th><td>{esc(str(e.get('entity_label') or '—'))}</td></tr>
  <tr><th>Governing direction</th><td>{esc(str(e.get('governing_direction') or '—'))}</td></tr>
  <tr><th>Period</th><td><b>{esc(str(p.get('label', '')))}</b> —
      {_date(str(p.get('start', '')))} to {_date(str(p.get('end_inclusive', p.get('end', ''))))}</td></tr>
  <tr><th>Basis</th><td>{esc(str(p.get('basis', '')))}</td></tr>
  <tr><th>Status</th><td>{esc(str(pack.get('status', 'draft')))}
      {'· revision ' + str(pack['revision']) if pack.get('revision') else ''}</td></tr>
  <tr><th>Generated</th><td>{esc(str(pack.get('generated_at', payload.get('built_at', ''))))}
      by {esc(str(pack.get('generated_by', '—')))}</td></tr>
  {'<tr><th>Issued</th><td>' + esc(str(pack.get('issued_at'))) + ' by ' + esc(str(pack.get('issued_by'))) + '</td></tr>' if pack.get('issued_at') else ''}
  {'<tr><th>Content hash</th><td class="mono">' + esc(str(pack.get('content_hash', ''))) + '</td></tr>' if pack.get('content_hash') else ''}
</table>"""

    if pack.get("note"):
        head += f"<div class='note'><b>Chair's note.</b> {esc(str(pack['note']))}</div>"

    body = []
    for s in payload.get("sections", []):
        body.append(f"<h2>{esc(str(s.get('title', '')))}</h2>")
        body.append(f"<p class='why'>{esc(str(s.get('why', '')))}</p>")

        if s.get("metrics"):
            rows = "".join(
                f"<tr><th>{esc(str(m.get('label', m.get('name'))))}</th>"
                f"<td class='num{'' if m.get('available', True) else ' unavailable'}'>"
                f"{_fmt_value(m)}</td></tr>"
                for m in s["metrics"])
            body.append(f"<table class='metrics'>{rows}</table>")

        for b in s.get("breakdowns", []):
            title = (f"{esc(str(b.get('label') or b.get('metric')))} "
                     f"by {esc(str(b.get('dimension')))}")
            if not b.get("available", True):
                body.append(f"<p class='unavailable'>{title}: not available — "
                            f"{esc(str(b.get('why', '')))}</p>")
                continue
            if not b.get("rows"):
                body.append(f"<p class='empty'>{title}: nothing in this period.</p>")
                continue
            cells = "".join(
                f"<tr><td>{esc(str(r.get('key') or '—'))}</td>"
                f"<td class='num'>{_fmt_value({'value': r.get('value'), 'unit': b.get('unit', '')})}</td>"
                "</tr>" for r in b["rows"])
            body.append(f"<h3>{title}</h3><table class='breakdown'>"
                        f"<thead><tr><th>{esc(str(b.get('dimension')))}</th>"
                        f"<th>Value</th></tr></thead><tbody>{cells}</tbody></table>")

        if s.get("key") == "accountability":
            a = s.get("accountability", {}) or {}
            body.append(
                "<table class='metrics'>"
                f"<tr><th>Frauds declared in period</th><td class='num'>{a.get('declared', 0):,}</td></tr>"
                f"<tr><th>Examinations concluded</th><td class='num'>{a.get('concluded', 0):,}</td></tr>"
                f"<tr><th>Examinations in progress</th><td class='num'>{a.get('in_progress', 0):,}</td></tr>"
                f"<tr><th>Not started</th><td class='num'>{a.get('not_started', 0):,}</td></tr>"
                f"<tr><th>Overdue</th><td class='num'>{a.get('overdue', 0):,}</td></tr>"
                f"<tr><th>Concluded outside the window</th><td class='num'>{a.get('concluded_late', 0):,}</td></tr>"
                f"<tr><th>Staff with an adverse finding</th><td class='num'>{a.get('staff_implicated', 0):,}</td></tr>"
                "</table>")
            if a.get("adverse_findings"):
                cells = "".join(
                    f"<tr><td>{esc(str(r.get('finding')))}</td>"
                    f"<td class='num'>{int(r.get('n', 0)):,}</td></tr>"
                    for r in a["adverse_findings"])
                body.append("<h3>Adverse findings by type</h3>"
                            f"<table class='breakdown'><tbody>{cells}</tbody></table>")
            elif a.get("declared"):
                body.append("<p class='empty'>No adverse finding was recorded against "
                            "any member of staff for frauds declared in this period.</p>")

        if s.get("key") == "materiality":
            floor = s.get("floor_paise") or 0
            body.append(f"<p class='why'>Board-reporting floor: "
                        f"<b>{_rupees(floor)}</b> (this institution's approved policy).</p>")
            cases = s.get("cases") or []
            if not cases:
                body.append("<p class='empty'>No case in this period reached the "
                            "board-reporting floor.</p>")
            else:
                cells = "".join(
                    f"<tr><td class='mono'>{esc(str(c.get('case_id')))}</td>"
                    f"<td>{esc(str(c.get('fmr_category') or '—'))}</td>"
                    f"<td>{esc(str(c.get('state') or '—'))}</td>"
                    f"<td class='num'>{_rupees(c.get('amount_paise'))}</td>"
                    f"<td class='num'>{_rupees(c.get('recovered_paise'))}</td>"
                    f"<td>{'<b class=bad>unreported</b>' if c.get('unreported') else ('<b class=bad>late</b>' if c.get('reported_late') else 'on time')}</td>"
                    "</tr>" for c in cases)
                body.append(
                    "<table class='breakdown'><thead><tr><th>Case</th><th>Category</th>"
                    "<th>State</th><th>Amount</th><th>Recovered</th><th>FMR</th></tr>"
                    f"</thead><tbody>{cells}</tbody></table>")

        if s.get("key") == "detection":
            d = s.get("detection", {}) or {}
            body.append(f"<p class='why'>Distinct indicators that fired in this period: "
                        f"<b>{d.get('distinct_rules_fired', 0)}</b>.</p>")

    css = """
    body { font-family: Georgia, 'Times New Roman', serif; margin: 34px auto; max-width: 900px;
           color: #1a1a1a; line-height: 1.5; }
    h1 { font-size: 21px; border-bottom: 2px solid #1a1a1a; padding-bottom: 8px; }
    h2 { font-size: 15px; margin-top: 26px; border-bottom: 1px solid #bbb; padding-bottom: 4px; }
    h3 { font-size: 12.5px; margin: 16px 0 5px; color: #444; }
    p.why { font-size: 11.5px; color: #555; font-style: italic; margin: 5px 0 10px; }
    p.empty { font-size: 11.5px; color: #666; margin: 6px 0; }
    p.unavailable, td.unavailable { color: #a32218; font-size: 11.5px; }
    table { border-collapse: collapse; width: 100%; margin: 8px 0 14px; font-size: 12px; }
    table.meta th { width: 190px; }
    th, td { border: 1px solid #ccc; padding: 5px 8px; text-align: left; vertical-align: top; }
    th { background: #f4f4f4; font-weight: bold; }
    td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
    .mono { font-family: 'Courier New', monospace; font-size: 11px; }
    .bad { color: #a32218; }
    .note { border-left: 3px solid #444; padding: 8px 12px; margin: 14px 0;
            background: #fafafa; font-size: 12px; }
    .disclaimer { margin-top: 30px; padding-top: 10px; border-top: 1px solid #bbb;
                  font-size: 10.5px; color: #666; }
    """
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>Board pack — {esc(str(p.get('label', '')))}</title>"
            f"<style>{css}</style></head><body>{head}{''.join(body)}"
            f"<div class='disclaimer'>{DISCLAIMER}</div></body></html>")
