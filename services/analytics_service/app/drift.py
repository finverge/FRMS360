"""Model drift: PSI and KS between a reference window and the current one.

FREE-AI expects a regulated entity to monitor its models through their lifecycle, not
merely at approval. Precision alone will not show that: a model can hold its hit-rate
while the population it scores moves underneath it, and the first visible symptom is a
quiet collapse months later.

Two complementary measures:

* **PSI** (Population Stability Index) over binned scores - how much the *shape* of the
  score distribution has moved. Conventional reading, and the one supervisors recognise:
  < 0.10 stable, 0.10-0.25 moderate shift, > 0.25 significant.
* **KS** (Kolmogorov-Smirnov) - the largest gap between the two cumulative
  distributions. Sensitive to a shift concentrated in one part of the range, which PSI
  can average away.

Both are computed over the same bins so the two numbers describe one comparison.
"""
import math

# Conventional PSI reading. Stated here rather than buried in a UI colour so the
# interpretation travels with the number.
PSI_STABLE = 0.10
PSI_SIGNIFICANT = 0.25

# Guards against a division by zero when a bin is empty in one window. Standard practice
# is a small epsilon; it slightly understates PSI rather than returning infinity.
_EPS = 1e-6


def _proportions(values: list[float], edges: list[float]) -> list[float]:
    counts = [0] * (len(edges) - 1)
    for v in values:
        for i in range(len(edges) - 1):
            # Last bin is closed so the maximum value is not dropped.
            if edges[i] <= v < edges[i + 1] or (i == len(counts) - 1 and v == edges[-1]):
                counts[i] += 1
                break
    total = sum(counts) or 1
    return [c / total for c in counts]


def _edges(reference: list[float], bins: int) -> list[float]:
    """Bin on the REFERENCE window, then apply those edges to the current one.

    Re-binning each window separately would compare two different rulers and hide the
    very movement being measured.
    """
    lo, hi = min(reference), max(reference)
    if hi <= lo:
        hi = lo + 1.0
    step = (hi - lo) / bins
    return [lo + step * i for i in range(bins + 1)]


def psi(reference: list[float], current: list[float], bins: int = 10) -> dict:
    if len(reference) < 2 or not current:
        return {"available": False, "reason": "not enough data in one of the windows"}
    edges = _edges(reference, bins)
    ref_p = _proportions(reference, edges)
    cur_p = _proportions(current, edges)

    contributions, total = [], 0.0
    for i, (r, c) in enumerate(zip(ref_p, cur_p)):
        r_adj, c_adj = max(r, _EPS), max(c, _EPS)
        part = (c_adj - r_adj) * math.log(c_adj / r_adj)
        total += part
        contributions.append({
            "bin": f"{edges[i]:.0f}–{edges[i + 1]:.0f}",
            "reference_pct": round(r * 100, 2),
            "current_pct": round(c * 100, 2),
            "contribution": round(part, 4),
        })

    band = ("stable" if total < PSI_STABLE
            else "moderate" if total < PSI_SIGNIFICANT else "significant")
    return {
        "available": True, "psi": round(total, 4), "band": band,
        "bins": contributions,
        "thresholds": {"stable_below": PSI_STABLE, "significant_above": PSI_SIGNIFICANT},
    }


def ks(reference: list[float], current: list[float]) -> dict:
    """Largest gap between the two cumulative distributions."""
    if len(reference) < 2 or not current:
        return {"available": False}
    combined = sorted(set(reference) | set(current))
    ref_sorted, cur_sorted = sorted(reference), sorted(current)

    def _cdf(sorted_values: list[float], x: float) -> float:
        lo, hi = 0, len(sorted_values)
        while lo < hi:
            mid = (lo + hi) // 2
            if sorted_values[mid] <= x:
                lo = mid + 1
            else:
                hi = mid
        return lo / len(sorted_values)

    stat, at = 0.0, None
    for x in combined:
        gap = abs(_cdf(ref_sorted, x) - _cdf(cur_sorted, x))
        if gap > stat:
            stat, at = gap, x
    return {"available": True, "ks": round(stat, 4), "at_value": at}
