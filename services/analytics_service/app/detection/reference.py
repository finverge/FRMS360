"""Screening the payment stream against loaded reference data.

This is what turns CPT-02 and CPT-03 from documented gaps into measurements. The one
invariant that matters: **if the list is not loaded, the indicator is unmeasurable, not
zero.** ``availability()`` reports that per tenant, and ``observe_reference`` returns no
observation at all rather than a benign one.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..reference_model import LIST_KINDS

#: Ratio at or above which two normalised names are treated as the same party. The
#: catalogue's CPT-02 threshold is a *score*, so the comparison lives in the rule and this
#: only produces the score.
_TOKEN_MIN = 2

#: Corporate and honorific noise that carries no identifying signal. Stripped before
#: comparison so "M/S RAVI TRADERS PVT LTD" and "Ravi Traders" score as the same party.
_NOISE = {
    "M/S", "MS", "MR", "MRS", "SHRI", "SMT", "DR", "PVT", "PRIVATE", "LTD", "LIMITED",
    "LLP", "INC", "CORP", "CO", "COMPANY", "AND", "THE", "OF",
}


def normalise(name: str) -> str:
    """Upper-case, unaccented, punctuation-free. Applied to both sides identically."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", s).upper()
    return re.sub(r"\s+", " ", s).strip()


def _tokens(name: str) -> set[str]:
    return {t for t in normalise(name).split() if t and t not in _NOISE}


def name_score(a: str, b: str) -> float:
    """How strongly two party names denote the same party, 0..1.

    Deliberately a token-overlap measure rather than an edit distance. Sanctions data is
    full of reordered and partially transliterated names ("Mohammed Ali Hassan" vs "Hassan
    Mohammed Ali"), where edit distance is close to useless and token overlap is not.
    Nothing here is a substitute for a screening vendor; it is honest, explainable, and it
    reports its score so a human decides.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    if not inter:
        return 0.0
    # Containment rather than Jaccard: a two-token designated name fully contained in a
    # longer account name is a hit, and Jaccard would dilute it toward zero.
    score = len(inter) / min(len(ta), len(tb))
    if len(inter) < _TOKEN_MIN and min(len(ta), len(tb)) > 1:
        # A single shared common token ("KUMAR") is not a match.
        return 0.0
    return round(min(1.0, score), 4)


@dataclass
class ListStatus:
    kind: str
    loaded: bool
    version: str = ""
    entry_count: int = 0
    why_unavailable: str = ""


def availability(db: Session, tenant_id: str) -> dict[str, ListStatus]:
    """Which reference lists this tenant actually has. Drives the dormant register."""
    rows = db.execute(text(
        "SELECT kind, version, entry_count FROM analytics.reference_lists "
        "WHERE tenant_id = :t AND active IS TRUE"), {"t": tenant_id}).mappings().all()
    have = {r["kind"]: r for r in rows}
    out = {}
    for kind, meta in LIST_KINDS.items():
        r = have.get(kind)
        out[kind] = ListStatus(
            kind=kind, loaded=r is not None,
            version=r["version"] if r else "",
            entry_count=int(r["entry_count"]) if r else 0,
            why_unavailable="" if r else
            f"No {meta['label']} has been loaded for this tenant.")
    return out


def load_screening_set(db: Session, tenant_id: str) -> dict:
    """The active name lists and charge registry, pulled once per batch.

    Sanctions lists run to tens of thousands of rows and the batch is screened against all
    of them, so this is one query per run rather than one per transaction.
    """
    names: list[tuple[str, str, str, str]] = []   # (kind, match_key, display, version)
    for r in db.execute(text(
        "SELECT e.kind, e.match_key, e.display_name, l.version "
        "  FROM analytics.reference_entries e "
        "  JOIN analytics.reference_lists l ON l.id = e.list_id "
        " WHERE e.tenant_id = :t AND l.active IS TRUE "
        "   AND e.kind = ANY(:kinds)"),
        {"t": tenant_id,
         "kinds": [k for k, v in LIST_KINDS.items() if v["match"] == "name"]}).mappings():
        names.append((r["kind"], r["match_key"], r["display_name"], r["version"]))

    charges: dict[str, dict] = {}
    for r in db.execute(text(
        "SELECT e.match_key, e.attributes, l.version "
        "  FROM analytics.reference_entries e "
        "  JOIN analytics.reference_lists l ON l.id = e.list_id "
        " WHERE e.tenant_id = :t AND l.active IS TRUE AND e.kind = 'cersai_charges'"),
        {"t": tenant_id}).mappings():
        charges[r["match_key"]] = {**(r["attributes"] or {}), "_version": r["version"]}

    return {"names": names, "charges": charges,
            "availability": availability(db, tenant_id)}


def screen_name(screening: dict, candidate: str) -> tuple[float, dict] | None:
    """Best sanctions/negative-list match for a counterparty name, or None.

    Returns ``None`` when no name list is loaded - the caller must then leave CPT-02
    unmeasured rather than score it zero.
    """
    avail = screening["availability"]
    if not any(avail[k].loaded for k, v in LIST_KINDS.items() if v["match"] == "name"):
        return None
    best, best_meta = 0.0, {}
    for kind, key, display, version in screening["names"]:
        s = name_score(candidate, key)
        if s > best:
            best, best_meta = s, {"list": kind, "matched": display, "version": version}
            if s >= 1.0:
                break
    return best, best_meta


def screen_collateral(screening: dict, asset_key: str) -> tuple[float, dict] | None:
    """How many lenders hold a charge over this asset, or None if no registry loaded."""
    if not screening["availability"]["cersai_charges"].loaded:
        return None
    row = screening["charges"].get(normalise(asset_key))
    if row is None:
        return 0.0, {}
    lenders = row.get("lenders") or []
    n = len(lenders) if isinstance(lenders, list) else int(lenders or 0)
    return float(n), {"lenders": lenders, "version": row.get("_version", "")}
