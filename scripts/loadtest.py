"""Sustained-load evidence for detection latency (BR-309 / BR-909).

"Sub-second detection" is worth nothing in a contract until someone has measured it at a
stated rate for a stated duration and written down what happened. This produces that
evidence, repeatably, and refuses to produce a number it cannot stand behind.

**What is measured.** ``received_at`` to ``processed_at`` on ``ingestion.raw_transaction``
— the platform receiving a transaction, to detection having scored it. Both timestamps are
already recorded on every row in normal operation, so this measures the real pipeline
rather than a test harness pretending to be one.

**Why the backlog matters as much as the latency.** A pipeline that reports a 200 ms p99
while its queue grows by a thousand rows a second is not keeping up; it is falling behind
with good manners. The run therefore samples queue depth throughout and reports whether
the backlog drained. A latency figure from a run whose backlog never drained is not a
sustained figure, and this says so rather than printing the percentile alone.

**Two modes, because they measure different things.** ``--mode api`` drives the real
intake endpoint and therefore measures intake *and* detection; it needs a credential that
can authenticate, which today means an MFA-enrolled human (see task: machine-to-machine
credential). ``--mode direct`` writes rows as received and measures detection alone. The
second is not a cheat — BR-309 asks for detection within seconds *of receipt*, and
``received_at`` is receipt — but the report always says which was used, because a number
that silently excluded the intake path would be quoted as though it had not.

**It refuses rather than approximates.** If the services are not reachable, or the
producer could not hold the requested rate, the run fails loudly. A load test that
quietly measures something easier than what was asked is worse than no load test.

    python scripts/loadtest.py --tenant hdfc-demo --rate 200 --duration 60
    python scripts/loadtest.py --tenant hdfc-demo --rate 500 --duration 120 --json out.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from cp_common import settings  # noqa: E402
from cp_common.db import SessionLocal  # noqa: E402

RAILS = ("UPI", "IMPS", "NEFT", "RTGS", "CARD")
#: Marker on every row this run creates, so the measurement query cannot accidentally
#: include traffic from a previous run or from the demo corpus.
SOURCE = "loadtest"


class Stopped(Exception):
    pass


# --------------------------------------------------------------------- producer
def _batch(tenant_id: str, n: int, seq: int) -> dict:
    now = datetime.now(timezone.utc)
    items = []
    for i in range(n):
        ref = f"LT-{seq:06d}-{i:04d}-{uuid.uuid4().hex[:8]}"
        items.append({"payload": {
            "txn_id": ref,
            "ts": (now - timedelta(seconds=1)).isoformat(),
            "amount": round(500 + (i * 37) % 250_000, 2),
            "debtor_account": f"AC-LT-{(seq * n + i) % 1400:05d}",
            "creditor_account": f"AC-LT-{(seq * n + i * 7 + 11) % 1400:05d}",
            "channel": "mobile",
            "device_id": f"D-LT-{(seq + i) % 300:04d}",
            "branch": "BR1", "region": "south", "product": "savings",
            "customer_segment": "retail", "status": "settled",
        }})
    rail = RAILS[seq % len(RAILS)]
    return {"source": SOURCE,
            "transactions": [{"rail": rail, "payload": it["payload"]} for it in items]}


def login(base_gateway: str, email: str, password: str) -> str:
    """A bearer token for the intake API.

    The batch endpoint is authenticated as a user rather than by internal key, because
    ingesting a bank's transactions is a tenant-scoped act. The load test therefore
    authenticates like any other caller instead of reaching around the front door.
    """
    with httpx.Client(timeout=20.0) as c:
        r = c.post(f"{base_gateway}/api/auth/login",
                   json={"email": email, "password": password})
        r.raise_for_status()
        body = r.json()
        token = body.get("access_token") or body.get("token")
        if not token:
            raise SystemExit(f"login returned no token: {list(body)}")
        return token


def produce_direct(tenant_id: str, rate: int, duration: int, stop: threading.Event,
                   out: dict) -> None:
    """Write rows as received, at a paced rate, bypassing the HTTP intake.

    Measures detection alone. Uses the same adapters the API path uses, so the canonical
    shape is produced identically - only the transport differs.
    """
    from services.ingestion_service.app.adapters import adapt
    from services.ingestion_service.app.models import RawTransaction

    per_batch = max(1, min(200, rate // 5 or 1))
    interval = per_batch / rate
    sent = rejected = 0
    seq = 0
    started = time.perf_counter()
    deadline = started + duration
    next_at = started
    while time.perf_counter() < deadline and not stop.is_set():
        now = time.perf_counter()
        if now < next_at:
            time.sleep(min(next_at - now, 0.05))
            continue
        next_at += interval
        body = _batch(tenant_id, per_batch, seq)
        db = SessionLocal()
        try:
            for item in body["transactions"]:
                try:
                    canonical = adapt(item["rail"], item["payload"])
                except Exception:  # noqa: BLE001
                    rejected += 1
                    continue
                db.add(RawTransaction(
                    tenant_id=tenant_id, rail=item["rail"],
                    source_txn_id=canonical["txn_id"], payload=item["payload"],
                    canonical={k: (v.isoformat() if hasattr(v, "isoformat") else v)
                               for k, v in canonical.items()},
                    ts=canonical["ts"], amount_paise=canonical["amount_paise"],
                    state="pending", source=SOURCE))
                sent += 1
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            out.setdefault("errors", []).append(str(exc)[:160])
            rejected += per_batch
        finally:
            db.close()
        seq += 1
    elapsed = time.perf_counter() - started
    out.update({"sent": sent, "accepted": sent, "rejected": rejected,
                "elapsed_s": round(elapsed, 2),
                "achieved_rate": round(sent / elapsed, 1) if elapsed else 0.0,
                "target_rate": rate, "batch_size": per_batch, "mode": "direct"})


def produce(tenant_id: str, rate: int, duration: int, key: str, base: str,
            stop: threading.Event, out: dict) -> None:
    """Post at a paced rate. Records what was actually achieved, not what was asked."""
    per_batch = max(1, min(200, rate // 5 or 1))
    interval = per_batch / rate
    sent = accepted = rejected = 0
    seq = 0
    started = time.perf_counter()
    deadline = started + duration
    next_at = started
    with httpx.Client(timeout=20.0) as client:
        while time.perf_counter() < deadline and not stop.is_set():
            now = time.perf_counter()
            if now < next_at:
                time.sleep(min(next_at - now, 0.05))
                continue
            next_at += interval
            try:
                r = client.post(f"{base}/ingest/{tenant_id}/transactions",
                                json=_batch(tenant_id, per_batch, seq),
                                headers={"Authorization": f"Bearer {key}"})
                sent += per_batch
                if r.status_code < 300:
                    body = r.json()
                    accepted += int(body.get("accepted", 0))
                    rejected += int(body.get("rejected", 0))
                else:
                    rejected += per_batch
                    if len(out.setdefault("http_errors", [])) < 3:
                        out["http_errors"].append(f"{r.status_code}: {r.text[:180]}")
            except Exception as exc:  # noqa: BLE001
                out.setdefault("errors", []).append(str(exc)[:120])
                rejected += per_batch
            seq += 1
    elapsed = time.perf_counter() - started
    out.update({"sent": sent, "accepted": accepted, "rejected": rejected,
                "elapsed_s": round(elapsed, 2),
                "achieved_rate": round(sent / elapsed, 1) if elapsed else 0.0,
                "target_rate": rate, "batch_size": per_batch})


# --------------------------------------------------------------------- consumer
def consume(tenant_id: str, stop: threading.Event, out: dict) -> None:
    """Drain the queue continuously, the way a production worker would."""
    from services.analytics_service.app.detection import engine

    batches = alerts = 0
    while not stop.is_set():
        db = SessionLocal()
        try:
            rep = engine.run_until_empty(db, tenant_id, batch=500, max_batches=4)
            batches += 1
            alerts += rep.alerts
            if rep.claimed == 0:
                time.sleep(0.2)
        except Exception as exc:  # noqa: BLE001
            out.setdefault("errors", []).append(str(exc)[:160])
            time.sleep(0.5)
        finally:
            db.close()
    out.update({"detection_passes": batches, "alerts": alerts})


# ---------------------------------------------------------------------- sampler
def sample_backlog(tenant_id: str, stop: threading.Event, out: dict) -> None:
    """Queue depth once a second. This is what turns a latency number into a claim."""
    depths: list[int] = []
    while not stop.is_set():
        db = SessionLocal()
        try:
            depths.append(int(db.execute(text(
                "SELECT COUNT(*) FROM ingestion.raw_transaction "
                "WHERE tenant_id = :t AND state = 'pending' AND source = :s"),
                {"t": tenant_id, "s": SOURCE}).scalar() or 0))
        except Exception:  # noqa: BLE001
            pass
        finally:
            db.close()
        time.sleep(1.0)
    out["backlog_samples"] = depths


# ----------------------------------------------------------------- measurement
def measure(tenant_id: str, since: datetime) -> dict:
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT EXTRACT(EPOCH FROM (processed_at - received_at)) AS secs "
            "FROM ingestion.raw_transaction "
            "WHERE tenant_id = :t AND source = :s AND received_at >= :since "
            "  AND processed_at IS NOT NULL"),
            {"t": tenant_id, "s": SOURCE, "since": since}).scalars().all()
        pending = int(db.execute(text(
            "SELECT COUNT(*) FROM ingestion.raw_transaction "
            "WHERE tenant_id = :t AND source = :s AND received_at >= :since "
            "  AND processed_at IS NULL"),
            {"t": tenant_id, "s": SOURCE, "since": since}).scalar() or 0)
    finally:
        db.close()
    lat = sorted(float(x) for x in rows if x is not None)
    if not lat:
        return {"measured": 0, "unprocessed": pending}

    def pct(p):
        k = max(0, min(len(lat) - 1, int(round((p / 100) * (len(lat) - 1)))))
        return round(lat[k], 3)

    return {"measured": len(lat), "unprocessed": pending,
            "p50_s": pct(50), "p90_s": pct(90), "p95_s": pct(95), "p99_s": pct(99),
            "max_s": round(lat[-1], 3), "mean_s": round(statistics.fmean(lat), 3)}


def cleanup(tenant_id: str) -> int:
    db = SessionLocal()
    try:
        n = db.execute(text(
            "DELETE FROM ingestion.raw_transaction WHERE tenant_id = :t AND source = :s"),
            {"t": tenant_id, "s": SOURCE}).rowcount
        db.execute(text("DELETE FROM analytics.fact_alert WHERE tenant_id = :t "
                        "AND source = :s"), {"t": tenant_id, "s": SOURCE})
        db.execute(text("DELETE FROM analytics.fact_transaction WHERE tenant_id = :t "
                        "AND source = :s"), {"t": tenant_id, "s": SOURCE})
        db.commit()
        return n or 0
    finally:
        db.close()


def preflight(base: str, key: str, tenant_id: str) -> None:
    """Refuse to run rather than measure the wrong thing."""
    try:
        r = httpx.get(f"{base}/health", timeout=5.0)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"ingestion-service is not reachable at {base} ({exc}).\n"
            "This test measures the real intake path, so it will not fall back to "
            "inserting rows directly - that would measure a pipeline nobody runs.\n"
            "Start the stack with .\\run_local.ps1 and try again.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--rate", type=int, default=100, help="transactions per second")
    ap.add_argument("--duration", type=int, default=60, help="seconds of sustained load")
    ap.add_argument("--drain", type=int, default=60,
                    help="seconds to keep draining after the producer stops")
    ap.add_argument("--json", help="write the report here")
    ap.add_argument("--keep", action="store_true", help="do not delete the test rows")
    ap.add_argument("--email", default="admin@finverge.local")
    ap.add_argument("--password", default="ChangeMe123!")
    ap.add_argument("--gateway", default="http://127.0.0.1:8080")
    ap.add_argument("--mode", choices=("api", "direct"), default="api",
                    help="api = intake + detection; direct = detection only")
    args = ap.parse_args()

    base = settings.ingestion_service_url.rstrip("/")
    preflight(base, settings.internal_api_key, args.tenant)
    token = (login(args.gateway.rstrip("/"), args.email, args.password)
             if args.mode == "api" else "")
    cleanup(args.tenant)

    since = datetime.now(timezone.utc)
    stop = threading.Event()
    prod: dict = {}
    cons: dict = {}
    samp: dict = {}

    threads = [
        (threading.Thread(target=produce,
                          args=(args.tenant, args.rate, args.duration, token, base,
                                stop, prod), daemon=True)
         if args.mode == "api" else
         threading.Thread(target=produce_direct,
                          args=(args.tenant, args.rate, args.duration, stop, prod),
                          daemon=True)),
        threading.Thread(target=consume, args=(args.tenant, stop, cons), daemon=True),
        threading.Thread(target=sample_backlog, args=(args.tenant, stop, samp),
                         daemon=True),
    ]
    print(f"driving {args.rate}/s for {args.duration}s into {args.tenant} ...")
    for t in threads:
        t.start()
    threads[0].join()
    print(f"producer done; draining for up to {args.drain}s ...")

    # Let detection finish what the producer queued.
    deadline = time.perf_counter() + args.drain
    while time.perf_counter() < deadline:
        depth = samp.get("backlog_samples") or [1]
        if depth and depth[-1] == 0:
            break
        time.sleep(1.0)
    stop.set()
    for t in threads[1:]:
        t.join(timeout=10)

    stats = measure(args.tenant, since)
    depths = samp.get("backlog_samples") or []
    drained = bool(depths) and depths[-1] == 0
    rate_ok = prod.get("achieved_rate", 0) >= args.rate * 0.9

    # Draining *after* the producer stops proves only that the queue is finite. Keeping
    # up means the backlog is not still climbing while load is applied, so the test looks
    # at the producer window alone and compares its first third with its last third.
    # Without this, 400/s "passed" while the queue grew to 3,440 and p99 reached 16s.
    window = depths[:args.duration] if len(depths) > args.duration else depths
    keeping_up = True
    growth = None
    if len(window) >= 6:
        third = max(1, len(window) // 3)
        early = statistics.fmean(window[:third])
        late = statistics.fmean(window[-third:])
        growth = round(late - early, 1)
        # Allow one batch of slack; anything beyond that is a queue that is losing ground.
        keeping_up = late <= max(early * 1.5, early + prod.get("batch_size", 100))

    report = {
        "tenant": args.tenant, "at": since.isoformat(), "mode": args.mode,
        "measures": ("intake + detection" if args.mode == "api"
                     else "detection only (intake path excluded)"),
        "target_rate_tps": args.rate, "duration_s": args.duration,
        "producer": prod, "consumer": cons,
        "latency_receipt_to_scored": stats,
        "backlog": {"max": max(depths) if depths else 0,
                    "final": depths[-1] if depths else None,
                    "drained": drained, "samples": len(depths),
                    "growth_during_load": growth, "keeping_up": keeping_up},
        "sustained": bool(drained and rate_ok and keeping_up
                          and stats.get("measured", 0) > 0),
    }

    print("\n" + "=" * 72)
    print(f"  mode {args.mode} - {report['measures']}")
    print(f"  target {args.rate}/s   achieved {prod.get('achieved_rate')}/s   "
          f"accepted {prod.get('accepted')}   rejected {prod.get('rejected')}")
    if stats.get("measured"):
        print(f"  receipt -> scored   p50 {stats['p50_s']}s   p95 {stats['p95_s']}s   "
              f"p99 {stats['p99_s']}s   max {stats['max_s']}s   n={stats['measured']}")
    else:
        print("  no transactions were scored - nothing to report")
    print(f"  backlog             max {report['backlog']['max']}   "
          f"final {report['backlog']['final']}   drained={drained}   "
          f"growth under load {growth}")
    print(f"  alerts raised       {cons.get('alerts')}")
    if not rate_ok:
        print("\n  NOT SUSTAINED: the producer could not hold the requested rate, so "
              "the latency above understates real load.")
    if not drained:
        print("\n  NOT SUSTAINED: the backlog never drained. Detection is falling "
              "behind at this rate, and the percentile above is not a steady-state "
              "figure.")
    elif not keeping_up:
        print(f"\n  NOT SUSTAINED: the backlog grew by {growth} rows while load was "
              f"applied and only cleared once the producer stopped. Detection is behind "
              f"at this rate — the latency above is a queueing figure, not a "
              f"steady-state one.")
    if report["sustained"]:
        print(f"\n  SUSTAINED at {args.rate}/s. This figure is contractable.")
    print("=" * 72)

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    if not args.keep:
        print(f"cleaned up {cleanup(args.tenant)} test row(s)")
    return 0 if report["sustained"] else 1


if __name__ == "__main__":
    sys.exit(main())
