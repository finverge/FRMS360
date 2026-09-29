"""ISO 20022 credit-transfer messages (BR-209).

Banks with a SWIFT or NPCI ISO estate deliver ``pacs.008`` (FI-to-FI customer credit
transfer) and ``pain.001`` (customer credit transfer initiation) rather than CSV. Both
carry the same three things detection needs - who paid whom, how much, and when - inside
different element names.

**The group header is not decoration.** Every ISO 20022 message states ``NbOfTxs`` and
usually ``CtrlSum``: how many transactions it contains and what they add up to. Most
implementations ignore both, which is how a truncated file lands as a short but
plausible-looking batch. Here they are verified, and a mismatch fails the whole file
rather than quietly ingesting the part that arrived. A payment file that half-lands is
worse than one that does not land at all, because nothing looks wrong afterwards.

**Namespaces are matched by local name.** The version suffix moves - ``pacs.008.001.08``,
``.09``, ``.10`` - and pinning one namespace means a bank's upgrade silently stops the
feed. Structure is stable across versions; the URN is not.

**Currency is checked, never assumed.** ``to_paise`` on a USD amount would produce a
number that is wrong by a factor of the exchange rate and indistinguishable from a
correct one.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from lxml import etree

from .adapters import AdapterError, to_paise, to_utc

#: The platform holds amounts in paise. A message in another currency is refused rather
#: than converted: there is no rate here, and a wrong amount is worse than a rejected one.
ACCEPTED_CURRENCIES = {"INR"}

#: Rail is not an ISO concept. The delivering bank names it in the filename or supplies it
#: explicitly; this is the fallback for an ISO file that says nothing.
DEFAULT_RAIL = "NEFT"


def _parser() -> etree.XMLParser:
    """Same hardening as the SAML verifier: no DTDs, no entities, no network."""
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                           dtd_validation=False, huge_tree=False, recover=False)


def _local(tag) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) or tag is not None else ""


def _find(el, *names):
    """First descendant whose local name matches, ignoring namespace and version."""
    for node in el.iter():
        if isinstance(node.tag, str) and etree.QName(node).localname in names:
            return node
    return None


def _findall(el, *names):
    return [n for n in el.iter()
            if isinstance(n.tag, str) and etree.QName(n).localname in names]


def _text(el, *names, default=""):
    n = _find(el, *names) if el is not None else None
    return (n.text or "").strip() if n is not None and n.text else default


def _account(party_acct) -> str:
    """Account identifier from an ``*Acct`` element.

    ISO offers IBAN or a generic ``Othr/Id``; Indian banks use the latter. Whichever is
    present is the account, and an element with neither is not usable.
    """
    if party_acct is None:
        return ""
    iban = _find(party_acct, "IBAN")
    if iban is not None and iban.text:
        return iban.text.strip()
    othr = _find(party_acct, "Othr")
    if othr is not None:
        ident = _find(othr, "Id")
        if ident is not None and ident.text:
            return ident.text.strip()
    ident = _find(party_acct, "Id")
    return (ident.text or "").strip() if ident is not None and ident.text else ""



def _when(value: str) -> tuple[str, str]:
    """Return (iso timestamp, precision).

    ISO settlement fields are often a bare *date* - ``IntrBkSttlmDt`` has no time at all -
    and ``to_utc`` rightly refuses a naive value, because a timestamp with no offset
    cannot be placed on the clock the velocity rules measure against.

    A date is nonetheless a real fact, so it is placed at the start of that day in UTC
    and the row records that its precision is a date rather than an instant. The
    alternative - borrowing the time-of-day from the message header - would invent an
    instant that looks precise and is not, which is worse than a known approximation.
    Detection windows are 24 hours and longer, so a same-day placement does not move any
    observation.
    """
    v = (value or "").strip()
    if not v:
        raise AdapterError("no settlement date and no message creation time")
    if "T" not in v and len(v) == 10:
        return to_utc(v + "T00:00:00+00:00").isoformat(), "date"
    return to_utc(v).isoformat(), "instant"


def detect(data: bytes) -> str:
    """Which ISO message this is, or "" if it is not one we handle."""
    try:
        root = etree.fromstring(data, parser=_parser())
    except etree.XMLSyntaxError:
        return ""
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        name = etree.QName(node).localname
        if name == "FIToFICstmrCdtTrf":
            return "pacs.008"
        if name == "CstmrCdtTrfInitn":
            return "pain.001"
    return ""


def parse(data: bytes, *, rail: str = "") -> tuple[list[dict], dict]:
    """Return (rows, header). Rows are payload dicts the payment adapters understand.

    Raises ``AdapterError`` for anything that makes the file as a whole untrustworthy -
    a control-sum mismatch, an unhandled message type, a foreign currency.
    """
    try:
        root = etree.fromstring(data, parser=_parser())
    except etree.XMLSyntaxError as exc:
        raise AdapterError(f"not well-formed XML: {exc}")
    if root.getroottree().docinfo.doctype:
        raise AdapterError("XML carries a DOCTYPE, which is refused")

    kind = detect(data)
    if not kind:
        raise AdapterError(
            "not a recognised ISO 20022 credit transfer (expected pacs.008 or pain.001)")

    grp = _find(root, "GrpHdr")
    header = {
        "message_type": kind,
        "message_id": _text(grp, "MsgId"),
        "created": _text(grp, "CreDtTm"),
        "declared_count": _text(grp, "NbOfTxs"),
        "control_sum": _text(grp, "CtrlSum"),
    }

    # pacs.008 puts each transaction in CdtTrfTxInf; pain.001 nests them under PmtInf but
    # uses the same element name for the transaction itself.
    txs = _findall(root, "CdtTrfTxInf")
    rows: list[dict] = []
    total = Decimal("0")

    for tx in txs:
        amt_el = _find(tx, "IntrBkSttlmAmt", "InstdAmt", "Amt")
        if amt_el is None or not (amt_el.text or "").strip():
            raise AdapterError("a transaction carries no amount")
        ccy = (amt_el.get("Ccy") or "").upper()
        if ccy and ccy not in ACCEPTED_CURRENCIES:
            raise AdapterError(
                f"amount is in {ccy}; this platform holds paise and will not convert")
        try:
            total += Decimal((amt_el.text or "0").strip())
        except InvalidOperation:
            raise AdapterError(f"unparseable amount {amt_el.text!r}")

        pmt_id = _find(tx, "PmtId")
        txn_id = (_text(pmt_id, "TxId") or _text(pmt_id, "EndToEndId")
                  or _text(pmt_id, "InstrId"))
        if not txn_id:
            raise AdapterError("a transaction carries no identifier")

        # Settlement date if present, else the message creation time. Never "now": a file
        # processed a day late would otherwise time-shift a whole batch.
        when = (_text(tx, "IntrBkSttlmDt") or _text(tx, "ReqdExctnDt")
                or header["created"])
        ts_iso, precision = _when(when)

        debtor_acct = _account(_find(tx, "DbtrAcct")) or _account(
            _find(root, "DbtrAcct"))
        creditor_acct = _account(_find(tx, "CdtrAcct"))
        if not debtor_acct or not creditor_acct:
            raise AdapterError("a transaction is missing the debtor or creditor account")

        rows.append({
            "txn_id": txn_id,
            "ts": ts_iso,
            "ts_precision": precision,
            "amount": (amt_el.text or "").strip(),
            "debtor_account": debtor_acct,
            "creditor_account": creditor_acct,
            "debtor_name": _text(_find(tx, "Dbtr"), "Nm"),
            "creditor_name": _text(_find(tx, "Cdtr"), "Nm"),
            "remittance_info": _text(_find(tx, "RmtInf"), "Ustrd"),
            "channel": "iso20022",
        })

    # ---- the group header, verified ------------------------------------------------
    if header["declared_count"]:
        try:
            declared = int(header["declared_count"])
        except ValueError:
            raise AdapterError(f"NbOfTxs is not a number: {header['declared_count']!r}")
        if declared != len(rows):
            raise AdapterError(
                f"the message declares {declared} transaction(s) but contains "
                f"{len(rows)}. The file is truncated or malformed; a payment file that "
                "half-lands is worse than one that does not land at all.")
    if header["control_sum"]:
        try:
            declared_sum = Decimal(header["control_sum"])
        except InvalidOperation:
            raise AdapterError(f"CtrlSum is not a number: {header['control_sum']!r}")
        if declared_sum != total:
            raise AdapterError(
                f"the message declares a control sum of {declared_sum} but its "
                f"transactions total {total}.")

    header["rail"] = (rail or DEFAULT_RAIL).upper()
    header["parsed_count"] = len(rows)
    header["total"] = str(total)
    return rows, header
