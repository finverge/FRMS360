# BR-309 — detection latency under sustained load

Measured with `scripts/loadtest.py`, which is checked in so this can be re-run rather
than quoted. Every figure below is `received_at → processed_at` on
`ingestion.raw_transaction`: the platform receiving a transaction, to detection having
scored it. Both timestamps are recorded on every row in normal operation, so this is the
real pipeline and not a harness imitating one.

**Run:** 5 August 2026 · tenant `b3b90261…` (hdfc-demo, 28 active rules) · 20 s of load
per rate · single detection worker · PostgreSQL 16 on the same host · `--mode direct`.

## Results

| Rate | p50 | p95 | **p99** | max | Backlog growth under load | Sustained |
|---:|---:|---:|---:|---:|---:|:--|
| 50/s | 0.75 s | 1.22 s | **1.42 s** | 1.42 s | ~0 | **yes** |
| 150/s | 1.18 s | 1.72 s | **1.92 s** | 1.99 s | 25 | **yes** |
| 200/s | 1.68 s | 2.45 s | **2.65 s** | 2.75 s | 33 | **yes** |
| 250/s | 2.63 s | 3.79 s | 3.99 s | 4.00 s | 208 | no |
| 400/s | 9.34 s | 19.8 s | 20.4 s | 20.6 s | 2,517 | no |

**Ceiling: ~200 transactions/second per detection worker, p99 2.65 s.**

BR-309 asks for detection "within seconds of receipt, sustained, at each tenant's peak
transaction rate". At 200/s that is met with room to spare.

## What "sustained" means here, and why it had to be tightened

The first version of this test called 400/s a pass, because the backlog drained *after*
the producer stopped. It hadn't kept up — the queue grew to 3,440 rows while load was
applied and p99 reached 16 s. Draining afterwards only proves the queue is finite.

The criterion now compares the first third of the producer window against the last third
and fails the run if the backlog is still climbing. That is what separates 200/s
(growth 33) from 250/s (growth 208), and it is the difference between a steady-state
latency and a queueing artefact. A run that is not sustained prints its latency but
refuses to call it contractable.

## What these numbers do not cover

Stated plainly, because a latency figure quoted beyond its conditions is worse than none:

- **One detection worker.** Workers claim disjoint slices with `FOR UPDATE SKIP LOCKED`,
  so throughput should scale close to linearly with worker count — but that is an
  expectation, not a measurement. Multi-worker scaling is untested.
- **`--mode direct`.** The HTTP intake path is excluded. It could not be included because
  the intake endpoint authenticates as a human user and every role that can ingest
  requires a TOTP second factor, which a payment switch cannot complete. See the
  machine-to-machine credential task; until that exists, `--mode api` cannot be driven at
  load.
- **Local database, no network.** A managed instance across an availability zone will be
  slower. Re-run against the target deployment before contracting a number.
- **One tenant, one catalogue.** 28 active rules. A tenant with a larger catalogue or
  wider account fan-out will differ.
- **Warm cache, no concurrent dashboard load.** Analytics queries compete for the same
  database.

## Re-running

```
python scripts/loadtest.py --tenant <tenant-uuid> --mode direct --rate 200 \
    --duration 60 --drain 120 --json evidence.json
```

The tenant argument is the tenant **id**, not the slug — configuration is keyed by id, and
passing a slug produces an empty rule catalogue and a run that scores nothing. Exit status
is 0 only for a sustained run, so this can gate a pipeline.
