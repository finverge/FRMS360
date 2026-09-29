"""Load/refresh the real OFAC Specially Designated Nationals (SDN) list.

    python scripts/load_ofac_sdn.py

Downloads the two files the US Treasury publishes free and public - SDN.CSV (the
primary-name list) and ALT.CSV (alternate names/aliases for those same entities) - and
does a full replace of analytics.ofac_sdn_entry / analytics.ofac_sdn_alias. Real data,
not synthetic: this is the actual current sanctions list, refreshed by re-running this
script (OFAC updates it most business days). A full replace rather than an upsert diff
is deliberate - the list is small (~19k entries) and a delisted name must disappear, not
linger with a stale row.

Intended to run on a schedule (cron / Task Scheduler), same shape as
refresh_ring_scores.py and run_retention.py - not a daemon, not a new service.
"""
import argparse
import csv
import io
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402
from sqlalchemy import text  # noqa: E402

from cp_common import SessionLocal, record_audit  # noqa: E402

SDN_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV"
ALT_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ALT.CSV"

# OFAC's own sentinel for "no value" in this fixed-width-by-convention CSV format.
_BLANK = "-0-"

# Column order per OFAC's published SDN.CSV / ALT.CSV layout - no header row.
SDN_COLS = ["ent_num", "sdn_name", "sdn_type", "program", "title", "call_sign",
            "vess_type", "tonnage", "grt", "vess_flag", "vess_owner", "remarks"]
ALT_COLS = ["ent_num", "alt_num", "alt_type", "alt_name", "alt_remarks"]


def _clean(v: str) -> str:
    v = (v or "").strip()
    return "" if v == _BLANK else v


def _fetch_rows(url: str, cols: list[str]) -> list[dict]:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    reader = csv.reader(io.StringIO(resp.text))
    rows = []
    for raw in reader:
        if not raw or not raw[0].strip():
            continue
        raw = (raw + [""] * len(cols))[:len(cols)]
        rows.append({c: _clean(v) for c, v in zip(cols, raw)})
    return rows


def load(sdn_url: str = SDN_URL, alt_url: str = ALT_URL) -> dict:
    entries = _fetch_rows(sdn_url, SDN_COLS)
    aliases = _fetch_rows(alt_url, ALT_COLS)
    valid_ent_nums = {int(e["ent_num"]) for e in entries if e["ent_num"].isdigit()}

    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM analytics.ofac_sdn_alias"))
        db.execute(text("DELETE FROM analytics.ofac_sdn_entry"))

        db.execute(
            text(
                "INSERT INTO analytics.ofac_sdn_entry "
                "(ent_num, sdn_name, sdn_type, program, remarks) "
                "VALUES (:ent_num, :sdn_name, :sdn_type, :program, :remarks)"
            ),
            [
                {"ent_num": int(e["ent_num"]), "sdn_name": e["sdn_name"][:350],
                 "sdn_type": e["sdn_type"][:50], "program": e["program"][:200],
                 "remarks": e["remarks"]}
                for e in entries if e["ent_num"].isdigit()
            ],
        )
        alias_rows = [
            {"ent_num": int(a["ent_num"]), "alt_type": a["alt_type"][:20], "alt_name": a["alt_name"][:350]}
            for a in aliases
            if a["ent_num"].isdigit() and a["alt_name"] and int(a["ent_num"]) in valid_ent_nums
        ]
        if alias_rows:
            db.execute(
                text(
                    "INSERT INTO analytics.ofac_sdn_alias (ent_num, alt_type, alt_name) "
                    "VALUES (:ent_num, :alt_type, :alt_name)"
                ),
                alias_rows,
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    summary = {"entries": len(valid_ent_nums), "aliases": len(alias_rows),
               "refreshed_at": datetime.now(timezone.utc).isoformat()}
    record_audit(
        service="analytics-service", action="ofac.sdn_refresh", actor="system:ofac-sdn-load",
        tenant_id="", target_type="ofac_sdn_list", target_id="global",
        status="success", detail=summary,
    )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdn-url", default=SDN_URL)
    parser.add_argument("--alt-url", default=ALT_URL)
    args = parser.parse_args()
    result = load(args.sdn_url, args.alt_url)
    print(f"Loaded {result['entries']} SDN entries, {result['aliases']} aliases "
          f"at {result['refreshed_at']}")
