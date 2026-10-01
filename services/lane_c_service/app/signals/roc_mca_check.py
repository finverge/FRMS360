"""LNC-10 — liabilities in a borrower's ROC search report its own financial statement
doesn't disclose (RBI 2016 illustrative signal #18), otherwise entirely uncovered.

A periodic borrower fact, not a payment-transaction one - read from Lane C's own
reference feed (reference_fetch.py, kind "mca_roc"), never Lane B's. Stays pure like
every other ``compute_*`` function here: the caller (``runner.py``) looks up the active
feed entry via ``reference_fetch.active_entry()`` and passes the result in - this module
does no I/O of its own.

**Feed contract** for the ``mca_roc`` kind's entry attributes (documented here since no
real vendor is wired up yet - this is the slot a vendor integration fills in):
``{"has_undisclosed_liability": bool, "detail": str}``. An entry missing
``has_undisclosed_liability`` entirely is treated as a malformed feed row, not a false
value - the same "don't guess" discipline as everywhere else in this catalogue.
"""
from __future__ import annotations

from .signal_engine import SignalResult, _unmeasurable, band


def compute_roc_mca_check(company_identifier: str | None, roc_entry: dict | None) -> SignalResult:
    code = "LNC-10"
    if not company_identifier:
        return _unmeasurable(code, "no CIN/PAN on file for this borrower")
    if roc_entry is None:
        return _unmeasurable(code, "no active MCA/ROC feed loaded, or this borrower "
                             "is not in the loaded list")

    attrs = roc_entry.get("attributes") or {}
    if "has_undisclosed_liability" not in attrs:
        return _unmeasurable(code, "feed entry is missing has_undisclosed_liability")

    if not attrs["has_undisclosed_liability"]:
        return SignalResult(code, 0.0, None, "pass", 0,
                            "No undisclosed ROC liabilities found for this borrower.",
                            evidence_basis="mca_roc feed")

    severity = 50  # critical - an undisclosed liability found by a third-party registry
                   # search, on a fact the borrower's own filing was silent about, is the
                   # same weight this catalogue gives LNC-03/06/07's worst-case findings.
    detail = str(attrs.get("detail", ""))[:300]
    evidence = f"ROC search report shows an undisclosed liability. {detail}".strip()
    return SignalResult(code, 1.0, None, band(severity), severity, evidence,
                        evidence_basis="mca_roc feed")
