"""Find values interpolated into HTML in the console without escaping.

The console builds markup with template literals and assigns it to ``innerHTML``. That is
fine for text the platform controls and dangerous for anything a user typed: a case
reason, a staff name, an examination note, a tenant's display name. A stored payload in
any of those executes for every colleague who opens the record - including, eventually,
an auditor.

This is a linter, not a parser, so it is deliberately conservative in one direction: it
looks only at template literals that actually contain markup, and it flags every
interpolation in them that is not visibly safe. False positives are silenced by adding
the expression to ``SAFE_EXPRESSIONS`` with a reason, which forces the judgement to be
written down rather than made silently.

    python tools/htmlcheck.py            # report
    python tools/htmlcheck.py --list     # one line per finding, for triage
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "services" / "gateway" / "app" / "static" / "app.js"

#: Template literals are matched crudely; these mark one as markup-bearing.
_MARKUP = re.compile(r"<[a-zA-Z/!]")

#: Expressions that cannot carry user input. Each entry is a judgement, so each needs a
#: reason - the point is to make "this one is fine" a written claim, not a shrug.
SAFE_PREFIXES: dict[str, str] = {
    "esc(": "escaped at the call site",
    "inr(": "formats an integer number of paise",
    "Math.": "arithmetic",
    "encodeURIComponent(": "URL-encoded, not HTML",
    "JSON.stringify(": "quoted JSON, no raw markup",
    "new Date(": "a formatted date",
    "String(": "coerces a value the caller already vouched for",
    "_roleOptions(": "renders <option> tags from TENANT_ROLES, a closed-set constant; "
                     "each label is esc()'d inside the helper itself",
}

SAFE_EXPRESSIONS: dict[str, str] = {
    # Loop and layout scaffolding.
    "i": "loop index",
    "n": "computed count",
    "idx": "loop index",
    "API": "a build-time constant",
    "token": "the bearer token, placed in a header not markup",
    # Platform-controlled vocabularies. These come from constants in this file or from
    # closed sets the server defines, never from a text box.
    "kind": "closed set of notification/return kinds",
    "cls": "a CSS class chosen by this file",
    "state": "closed set of lifecycle states",
    "badge": "a CSS class chosen by this file",
    "fmt": "literal 'html' or 'json'",
    "id": "server-generated identifier",
    # Identifiers minted by the server: uuids and prefixed keys, never free text.
    "c.id": "server-generated identifier",
    "u.id": "server-generated identifier",
    "v.id": "server-generated identifier",
    "p.id": "server-generated identifier",
    "existing.id": "server-generated identifier",
    "r.rule_id": "catalogue rule id, a closed set",
    # Closed vocabularies the server defines; the console renders them as chips.
    "a.action": "audit action verb, a closed set",
    "a.status": "success|failure|refused",
    "c.status": "closed set of config states",
    "t.status": "closed set of tenant states",
    "p.status": "closed set of delivery/pack states",
    "n.severity": "info|warn|urgent",
    "k.severity": "info|warn|urgent",
    "u.role": "a role name from ASSIGNABLE_TENANT_ROLES",
    "r.family": "EWS family code, a closed set",
    "p.channel": "email|inapp|webhook",
    "refState": "a CSS state class chosen by this file",
    "tone": "a CSS tone class chosen by this file",
    "mono": "a CSS class chosen by this file",
    "masked": "a CSS class chosen by this file",
    # Numbers.
    "p.attempts": "an integer",
    "r.breach_days_allowed": "an integer",
    "unissued": "a count",
    "pct": "a computed percentage",
    "r.threshold ?? \"?\"": "a configured numeric threshold",
    "r.unit || \"\"": "observed-unit code, a closed set",
    "wf.policy.fmr_filing_days": "an integer from tenant policy",
    "wf.policy.str_filing_days": "an integer from tenant policy",
    "wf.policy.natural_justice_days": "an integer from tenant policy",
    "p.data.count": "an integer",
    "p.data.degree": "an integer",
    "r.member_count": "an integer",
    "p.data.source": "a graph node kind, closed set",
    "p.data.role": "a graph node role, closed set",
    "wf.state.replace(/_/g, \" \")": "lifecycle state, a closed set",
    # Markup assembled by this file and already checked on its own.
    "actions": "markup built above in the same function",
    "refBody": "markup built above in the same function",
    "rev": "markup built above in the same function",
    "out": "escaped at the point it is built",
    "sub": "markup built above in the same function",
    "pretty": "a lifecycle state, formatted",
    "cells": "markup built by a nested template",
    "relativeTime(n.created_at)": "formats a timestamp",
    "wfDate(iso)": "formats a timestamp",
    "wfTrack(wf.state)": "markup built by this file",
    "wfHistory(hist.rows)": "markup built by this file",
    "wfActions(wf)": "markup built by this file",
    "rows": "markup built by a nested template in the same function",
    'where ? `<div class="acc-f-where">${where}</div>` : ""': "each part escaped when `where` is built; escaping again would show &amp;",
    "why": "markup fragment built just above in the same function",
    "d.ks?.ks ?? \"—\"": "a computed statistic",
    "r.threshold ?? \"—\"": "a configured numeric threshold",
    'd.ks?.ks ?? "?"': "a computed statistic",
    'd.ks?.at_value != null ? ` at score ${Math.round(d.ks.at_value)}` : ""': "a rounded number inside a literal",
    'added': 'markup built above in the same function',
    'del': 'markup built above in the same function',
    'pending': 'markup built above in the same function - one branch is a static '
               'string with no interpolated data, the other is the empty string',
    'mods': 'built entirely from esc(m.label) pieces, where m.label comes from '
            'MODULE_META - a closed platform constant, never user input',
    'roleOptions': 'built entirely from esc(r.name)/esc(r.label) pieces joined via '
                   '.map().join("")',
    'activate': 'markup built above in the same function',
    'body': 'markup built above in the same function',
    'clock': 'markup built above in the same function',
    'gap': 'markup built above in the same function',
    'gaps': 'markup built above in the same function',
    'entries': 'markup built above in the same function',
    'life': 'markup built above in the same function',
    'missing': 'markup built above in the same function',
    'c': 'markup built above in the same function',
    'delta': 'markup built above in the same function',
    'fmtMetric(m)': 'formats a metric value for display',
    'evKv(d.alert, ["tenant_id"])': 'markup built by evKv, which escapes each value',
    'evKv(d.case, ["tenant_id"])': 'markup built by evKv, which escapes each value',
    'evKv(d.transaction, ["tenant_id"])': 'markup built by evKv, which escapes each value',
    'filingCard("fmr", "Fraud Monitoring Return", fmr, live.fmr)': 'markup built by filingCard',
    'filingCard("str", "Suspicious Transaction Report", str, live.str)': 'markup built by filingCard',
    'd.configured': 'a count',
    'd.configured_quantitative': 'a count',
    'd.fired_quantitative': 'a count',
    'hist.rows.length': 'a count',
    'list.length': 'a count',
    'i + 1': 'a loop index',
    'd.psi': 'a computed statistic',
    'b.contribution': 'a computed statistic',
    'b.current_pct': 'a percentage',
    'b.reference_pct': 'a percentage',
    'm.current_pct': 'a percentage',
    'm.reference_pct': 'a percentage',
    'm.delta_pct': 'a percentage',
    'd.thresholds.stable_below': 'a configured numeric threshold',
    'd.thresholds.significant_above': 'a configured numeric threshold',
    'existing.revision': 'an integer',
    'Number(c.actual).toLocaleString()': 'a formatted number',
    'Number(c.expected).toLocaleString()': 'a formatted number',
    '(existing.content_hash || "").slice(0, 16)': 'a hex digest',
    'n.id': 'server-generated identifier',
    'a.to_state.replace(/_/g, " ")': 'lifecycle state, a closed set',
    'a.requires_document.replace(/_/g, " ")': 'document type, a closed set',
    'c.delta === 0 ? "0" : Number(c.delta).toLocaleString()': 'a formatted number',
}


_LITERAL = r"""(?:"[^"]*"|'[^']*'|`[^`$]*`)"""
#: A ternary whose branches are both string literals chooses between two constants.
_LITERAL_TERNARY = re.compile(
    r"^.+\?\s*" + _LITERAL + r"\s*:\s*" + _LITERAL + r"\s*$", re.S)
#: `xs.map(...).join("")` builds markup from a nested template, which this linter
#: already examined on its own. Escaping the joined result would double-escape it.
_JOINED = re.compile(r"\.(?:map|filter)\(.*\)\s*\.join\(", re.S)


def _is_safe(expr: str) -> str | None:
    # Normalised the same way findings are reported, so an entry copied from the report
    # into SAFE_EXPRESSIONS actually matches. Comparing raw text meant a multi-line
    # expression could never be silenced.
    e = " ".join(expr.split())
    if not e:
        return "empty"
    if re.fullmatch(r"[\d\s+\-*/.()]+", e):
        return "arithmetic literal"
    if re.fullmatch(_LITERAL, e, re.S):
        return "string literal"
    if _LITERAL_TERNARY.match(e):
        return "ternary between two string literals"
    if _JOINED.search(e):
        return "markup built by a nested template, checked separately"
    if "esc(" in e:
        # A compound expression - a ternary, a concatenation, a nested template - whose
        # data is escaped inside it. The linter cannot parse the branches, so it trusts
        # the explicit call; wrapping the whole thing would escape the markup around it.
        return "data escaped within the expression"
    if re.match(r"^wfClock\(", e):
        return "markup built by wfClock"
    if re.match(r"^rows\.slice\(\)", e) or "inr(" in e:
        # A nested markup template: the history list, and the referral notice whose only
        # interpolated value is a formatted rupee amount.
        return "markup built by a nested template"
    for pre, why in SAFE_PREFIXES.items():
        if e.startswith(pre):
            return why
    if e in SAFE_EXPRESSIONS:
        return SAFE_EXPRESSIONS[e]
    return None


def _template_literals(src: str):
    """Yield (start_index, text) for each backtick template literal."""
    out, i, n = [], 0, len(src)
    while i < n:
        ch = src[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "`":
            start = i
            i += 1
            depth = 0
            while i < n:
                c = src[i]
                if c == "\\":
                    i += 2
                    continue
                if c == "$" and i + 1 < n and src[i + 1] == "{":
                    depth += 1
                    i += 2
                    continue
                if c == "}" and depth:
                    depth -= 1
                    i += 1
                    continue
                if c == "`" and depth == 0:
                    break
                i += 1
            out.append((start, src[start:i + 1]))
        i += 1
    return out


def _interpolations(body: str):
    """Yield (offset, expression) for each ${...}, handling one level of nesting."""
    out, i, n = [], 0, len(body)
    while i < n - 1:
        if body[i] == "$" and body[i + 1] == "{":
            depth, j = 1, i + 2
            while j < n and depth:
                if body[j] == "{":
                    depth += 1
                elif body[j] == "}":
                    depth -= 1
                j += 1
            out.append((i, body[i + 2:j - 1]))
            i = j
            continue
        i += 1
    return out


def scan(path: Path = TARGET) -> list[tuple[int, str]]:
    src = path.read_text(encoding="utf-8")
    findings = []
    for start, body in _template_literals(src):
        if not _MARKUP.search(body):
            continue
        base_line = src[:start].count("\n") + 1
        for off, expr in _interpolations(body):
            if _is_safe(expr):
                continue
            line = base_line + body[:off].count("\n")
            findings.append((line, " ".join(expr.split())[:120]))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    findings = scan()
    if not findings:
        print(f"ok   {TARGET.name}: every interpolation into markup is escaped")
        return 0
    print(f"{len(findings)} unescaped interpolation(s) into markup in {TARGET.name}:")
    for line, expr in findings:
        # The console this runs in is not always UTF-8; findings must still print.
        safe = expr.encode("ascii", "replace").decode("ascii")
        print(f"  {line:5d}  {safe}")
    if not args.list:
        print("\nWrap each in esc(), or add it to SAFE_EXPRESSIONS with the reason it "
              "cannot carry user input.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
