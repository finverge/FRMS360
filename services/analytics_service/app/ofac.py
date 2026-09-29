"""OFAC sanctions-list screening.

Screens a name against the real US Treasury Specially Designated Nationals (SDN) list -
loaded/refreshed by scripts/load_ofac_sdn.py, not synthetic data - using Postgres
trigram similarity (pg_trgm), the same fuzzy-matching approach a real screening tool
uses, since a transliterated or reordered name will rarely match a query exactly.

This is deliberately an on-demand, investigator-driven lookup rather than an automatic
per-transaction check: Fraud360's fact tables carry account numbers, not customer/
counterparty names (see the BRD gap this closes - there's no name field to screen
automatically). An investigator types a name they've obtained from elsewhere (a case
note, a KYC document, a call transcript) and screens it here.
"""
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

#: Below this trigram similarity, a "match" is noise, not a lead - 0.3 is Postgres's own
#: pg_trgm default threshold for the ``%`` similarity operator.
MIN_SIMILARITY = 0.3


def screen(db: Session, name: str, limit: int = 10) -> dict[str, Any]:
    """Fuzzy-match ``name`` against SDN primary names and aliases.

    Returns the best (highest-similarity) row per SDN entity, deduplicated so an
    entity with a matching alias isn't also listed under its primary name.
    """
    query = name.strip()
    if not query:
        return {"query": name, "matches": [], "list_size": _list_size(db)}

    sql = text(
        """
        WITH candidates AS (
            SELECT e.ent_num, e.sdn_name, e.sdn_type, e.program, e.remarks,
                   e.sdn_name AS matched_on, 'primary' AS matched_via,
                   similarity(e.sdn_name, :q) AS score
            FROM analytics.ofac_sdn_entry e
            WHERE e.sdn_name % :q
            UNION ALL
            SELECT e.ent_num, e.sdn_name, e.sdn_type, e.program, e.remarks,
                   a.alt_name AS matched_on, 'alias' AS matched_via,
                   similarity(a.alt_name, :q) AS score
            FROM analytics.ofac_sdn_alias a
            JOIN analytics.ofac_sdn_entry e ON e.ent_num = a.ent_num
            WHERE a.alt_name % :q
        ),
        best_per_entity AS (
            SELECT DISTINCT ON (ent_num) *
            FROM candidates
            ORDER BY ent_num, score DESC
        )
        SELECT ent_num, sdn_name, sdn_type, program, remarks, matched_on, matched_via, score
        FROM best_per_entity
        WHERE score >= :min_sim
        ORDER BY score DESC
        LIMIT :lim
        """
    )
    rows = db.execute(sql, {"q": query, "min_sim": MIN_SIMILARITY, "lim": limit})
    matches = [
        {
            "ent_num": r.ent_num,
            "name": r.sdn_name,
            "type": r.sdn_type or "entity",
            "program": r.program,
            "remarks": r.remarks,
            "matched_on": r.matched_on,
            "matched_via": r.matched_via,
            "score": round(float(r.score), 3),
        }
        for r in rows
    ]
    return {"query": query, "matches": matches, "list_size": _list_size(db)}


def _list_size(db: Session) -> int:
    return int(db.execute(text("SELECT COUNT(*) FROM analytics.ofac_sdn_entry")).scalar() or 0)


def last_refreshed(db: Session) -> str | None:
    row = db.execute(text("SELECT MAX(updated_at) FROM analytics.ofac_sdn_entry")).scalar()
    return row.isoformat() if row else None
