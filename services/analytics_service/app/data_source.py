"""Where a fact came from, and what that entitles it to (BR-611).

Three kinds of row sit in the same tables:

* ``live``      - real traffic the bank sent.
* ``synthetic`` - the generated demonstration corpus.
* ``replay``    - alerts written by a backfill or replay run rather than by the live pass.

Keeping them together is deliberate: a demo and a pilot often run on the same instance,
and deleting the demo corpus to make a figure trustworthy is a worse answer than being
able to say which rows produced it.

Two rules follow, and the second one is the load-bearing one:

1. **A figure must disclose its mix.** Analytical views may include any source, but the
   response says what went into them, so a number can never be quoted without knowing
   whether it is real.

2. **A regulatory artefact may only be built from live rows.** An FMR assembled from the
   demonstration corpus is a fabricated return submitted to the Reserve Bank. There is no
   filter default, no configuration flag and no override that makes that acceptable, so
   the check is not a filter - it is a refusal, in the builder, close to the artefact.
"""
from __future__ import annotations

LIVE = "live"
SYNTHETIC = "synthetic"
REPLAY = "replay"

#: Everything the platform writes. Unknown values are treated as not-live rather than
#: rejected: a row whose provenance we cannot vouch for is exactly the row that must not
#: reach a return.
KNOWN = (LIVE, SYNTHETIC, REPLAY)

LABELS = {
    LIVE: "Live traffic",
    SYNTHETIC: "Demonstration data",
    REPLAY: "Replay / backfill",
}


def is_live(source: str | None) -> bool:
    """True only for real traffic. Absent or unrecognised counts as not live."""
    return (source or "").strip().lower() == LIVE


def non_live(sources) -> list[str]:
    """The distinct non-live sources in an iterable, in a stable order."""
    seen = {(s or "").strip().lower() for s in sources}
    return [s for s in KNOWN if s in seen and s != LIVE] + sorted(
        s for s in seen if s and s not in KNOWN)


def describe(source: str | None) -> str:
    key = (source or "").strip().lower()
    return LABELS.get(key, key or "unknown")


class NotLiveData(Exception):
    """Raised when a regulatory artefact would be built from non-live rows.

    Carries the offending sources so the message can name them rather than saying
    'not permitted' and leaving the user to guess which of their data is the problem.
    """

    def __init__(self, what: str, sources: list[str]):
        self.what = what
        self.sources = sources
        pretty = ", ".join(describe(s) for s in sources) or "unknown provenance"
        super().__init__(
            f"{what} cannot be built from {pretty}. A regulatory return must be "
            f"assembled from live traffic only — filing demonstration data with the "
            f"regulator is a false submission. Re-run this against a case built from "
            f"the bank's own transactions.")
