"""Observations that need more than one transaction to see.

Two catalogue indicators were listed as needing external data and do not:

* **LAY-03, circular flow.** "Value returned to origin within 3 hops" is a property of the
  bank's own payment graph. It needs a traversal, not a feed. It was dormant because it is
  awkward to compute, which is a different reason - and the dormant register said the
  wrong thing about it.

* **CHN-03, transaction outside the account's usual hours.** The catalogue measures
  ``hour_deviation_sigma``, and the hour of a transaction is on the transaction. This was
  described as needing "session/device telemetry from the channel", which is what the
  *rule name* suggests but not what the *measurement* requires.

Both are computed per batch, once, rather than per row.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

#: How far a cycle may run before we stop looking. The catalogue threshold is 3 hops, and
#: an unbounded recursive search over a payment graph is a good way to hang a database.
MAX_HOPS = 4
#: Ignore trivial amounts when looking for round-tripping: a ₹10 test transfer returning
#: to origin is not layering.
MIN_CYCLE_PAISE = 10_000 * 100


def circular_flows(db: Session, tenant_id: str, accounts: list[str], now: datetime,
                   *, window_hours: int = 72) -> dict[str, int]:
    """Shortest cycle length returning value to each account, within the window.

    Returns ``{account: hops}`` only for accounts that actually sit on a cycle. An account
    absent from the result has no detected cycle, which the caller reports as an
    observation of zero - unlike a missing list, absence here is a real measurement.
    """
    if not accounts:
        return {}
    rows = db.execute(text("""
        WITH RECURSIVE edges AS (
            SELECT DISTINCT debtor_account AS src, creditor_account AS dst
              FROM analytics.fact_transaction
             WHERE tenant_id = :t AND ts >= :since AND amount_paise >= :minamt
               AND debtor_account <> creditor_account
        ),
        walk(origin, node, hops) AS (
            SELECT e.src, e.dst, 1
              FROM edges e
             WHERE e.src = ANY(:accts)
            UNION ALL
            SELECT w.origin, e.dst, w.hops + 1
              FROM walk w
              JOIN edges e ON e.src = w.node
             WHERE w.hops < :maxhops AND e.dst <> w.origin
        )
        SELECT w.origin AS account, MIN(w.hops + 1) AS hops
          FROM walk w
          JOIN edges e ON e.src = w.node AND e.dst = w.origin
         GROUP BY w.origin
    """), {"t": tenant_id, "accts": accounts, "since": now - timedelta(hours=window_hours),
           "minamt": MIN_CYCLE_PAISE, "maxhops": MAX_HOPS}).mappings().all()
    return {r["account"]: int(r["hops"]) for r in rows}


def hour_profiles(db: Session, tenant_id: str, accounts: list[str], now: datetime,
                  *, baseline_days: int = 60, before: datetime | None = None
                  ) -> dict[str, tuple[float, float, int]]:
    """Each account's usual transacting hour, as (mean, stdev, n).

    Hours are circular - 23:00 and 01:00 are two hours apart, not twenty-two - so the mean
    is computed as a circular mean over the unit circle. Treating the hour as a plain
    number makes every late-night account look permanently anomalous.
    """
    cutoff = before or now
    rows = db.execute(text("""
        SELECT debtor_account AS a,
               EXTRACT(HOUR FROM ts AT TIME ZONE 'UTC') AS h
          FROM analytics.fact_transaction
         WHERE tenant_id = :t AND debtor_account = ANY(:accts)
           AND ts >= :since AND ts < :cut
    """), {"t": tenant_id, "accts": accounts, "cut": cutoff,
           "since": now - timedelta(days=baseline_days)}).mappings().all()

    by_acct: dict[str, list[float]] = {}
    for r in rows:
        by_acct.setdefault(r["a"], []).append(float(r["h"]))

    out: dict[str, tuple[float, float, int]] = {}
    for acct, hours in by_acct.items():
        n = len(hours)
        if n < 5:
            # Too little history to call anything unusual. Reported as no profile rather
            # than as a profile with a huge variance, which would silently never fire.
            continue
        angles = [h * math.pi / 12.0 for h in hours]
        sin_m = sum(math.sin(a) for a in angles) / n
        cos_m = sum(math.cos(a) for a in angles) / n
        mean_angle = math.atan2(sin_m, cos_m)
        mean_hour = (mean_angle * 12.0 / math.pi) % 24.0
        r_len = math.hypot(sin_m, cos_m)
        # Circular standard deviation, converted back to hours.
        if r_len >= 0.9999:
            sd_hours = 0.0
        else:
            sd_hours = math.sqrt(-2.0 * math.log(r_len)) * 12.0 / math.pi
        out[acct] = (mean_hour, sd_hours, n)
    return out



# hour_deviation moved to cp_common.observations - it is pure arithmetic over the
# profile tuple, and both scoring lanes need it.
from cp_common.observations import hour_deviation  # noqa: E402,F401
