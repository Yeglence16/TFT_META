"""Trait-based strong/weak matchup analysis per cluster (SPEC.md section 12).

For each real cluster A (off-meta excluded — not a coherent comp): compare
A's placement in matches where an opponent (any other participant in the
same lobby, regardless of that opponent's own cluster) has trait T active,
against A's own overall baseline placement. delta = exposed_mean -
baseline_mean. Same sign convention as augments.json's lift (SPEC-WEB.md
3.6): negative = A does BETTER facing that trait (comfortable, since lower
placement is better in TFT), positive = A does WORSE (struggles).

Tier distinction (SPEC.md: "en azından 'aktif' ve 'yüksek tier' ayrımı
yap" — 2-Sorcerer isn't 4/6/8-Sorcerer):
  active:    opponent's trait tier_current >= 1 (any breakpoint reached)
  high tier: opponent's trait tier_current == that trait's max breakpoint
             (maxed out), from static_data.py's traits.json breakpoints

Confidence interval: a normal-approximation CI on the EXPOSED group's mean
placement — NOT Wilson, which is for binomial proportions (matchups.py's
head-to-head rate). Placement here is a ~1-8 continuous outcome.

**Cells with n < MIN_N are excluded from the ranked top-5 lists entirely**
(not merely flagged) — a single-observation extreme delta could otherwise
dominate a "top 5" ranking purely by chance, which would be actively
misleading rather than just imprecise. This differs from matchups.py, which
shows the *complete* matrix and can afford to flag-and-keep low-n cells;
here the output is specifically a curated top-5, so low-confidence entries
are dropped from consideration and the drop count is logged instead.

Output: data/processed/comp_traits.json, SPEC-WEB.md section 3.7 shape
(`trait` is the raw apiName id, matching comps.json's convention — the web
side joins against traits.json for display names), extended with a `ci`
field per SPEC.md's explicit requirement, and a high-tier variant of each
list per the tier-distinction requirement above (SPEC-WEB's shown schema is
illustrative; extra fields are forward-compatible, the web side ignores
what it doesn't read):
    {"c_07": {
        "strong_against": [{"trait": "...", "delta": -0.41, "n": 1904, "ci": [lo, hi]}],
        "weak_against": [...],
        "strong_against_high_tier": [...],
        "weak_against_high_tier": [...]
    }, ...}
"""

from __future__ import annotations

import collections
import json
import logging
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import DATA_PROCESSED, DATA_STATIC, ensure_dirs

logger = logging.getLogger(__name__)

META_ROWS_PATH = DATA_PROCESSED / "features_meta.jsonl"
CLUSTER_LABELS_PATH = DATA_PROCESSED / "cluster_labels.npy"
STATIC_TRAITS_PATH = DATA_STATIC / "traits.json"
COMP_TRAITS_PATH = DATA_PROCESSED / "comp_traits.json"

OFF_META_CODE = -2
MIN_N = 100
Z_95 = 1.959963984540054
TOP_K = 5


def mean_ci(values: list[int], z: float = Z_95) -> tuple[float, float, float]:
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return mean, mean, mean
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    se = math.sqrt(variance / n)
    margin = z * se
    return mean, mean - margin, mean + margin


def _load_rows_with_labels() -> list[dict]:
    if not META_ROWS_PATH.exists():
        raise SystemExit(f"{META_ROWS_PATH} not found — run features.py first")
    if not CLUSTER_LABELS_PATH.exists():
        raise SystemExit(f"{CLUSTER_LABELS_PATH} not found — run clustering.py first")

    with open(META_ROWS_PATH, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    labels = np.load(CLUSTER_LABELS_PATH)
    if len(rows) != len(labels):
        raise SystemExit(
            f"features_meta.jsonl ({len(rows)} rows) and cluster_labels.npy ({len(labels)} rows) "
            "are out of sync — rerun features.py then clustering.py"
        )
    for row, label in zip(rows, labels):
        row["cluster_label"] = int(label)
    return rows


def _load_high_tier_thresholds() -> dict[str, int]:
    if not STATIC_TRAITS_PATH.exists():
        raise SystemExit(f"{STATIC_TRAITS_PATH} not found — run static_data.py first")
    traits = json.loads(STATIC_TRAITS_PATH.read_text(encoding="utf-8"))
    return {trait_id: len(info["breakpoints"]) for trait_id, info in traits.items()}


def compute_trait_exposure(
    rows: list[dict], high_thresholds: dict[str, int]
) -> tuple[dict[int, list[int]], dict[tuple[int, str, str], list[int]]]:
    """Returns (baseline, exposure):
    baseline[cluster_id] = every placement that cluster ever recorded.
    exposure[(cluster_id, trait_id, bucket)] = placements of that cluster's
    participants in matches where >=1 *other* participant had that trait
    active ("active" bucket) or maxed ("high" bucket)."""
    by_match: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_match[row["match_id"]].append(row)

    baseline: dict[int, list[int]] = collections.defaultdict(list)
    exposure: dict[tuple[int, str, str], list[int]] = collections.defaultdict(list)

    for participants in by_match.values():
        for row in participants:
            if row["cluster_label"] == OFF_META_CODE:
                continue
            baseline[row["cluster_label"]].append(row["placement"])

            active_traits: set[str] = set()
            high_traits: set[str] = set()
            for other in participants:
                if other is row:
                    continue
                for trait_id, tier_current in other["raw_traits"].items():
                    active_traits.add(trait_id)
                    if tier_current >= high_thresholds.get(trait_id, 10**9):
                        high_traits.add(trait_id)

            for trait_id in active_traits:
                exposure[(row["cluster_label"], trait_id, "active")].append(row["placement"])
            for trait_id in high_traits:
                exposure[(row["cluster_label"], trait_id, "high")].append(row["placement"])

    return baseline, exposure


def build_comp_traits(
    baseline: dict[int, list[int]], exposure: dict[tuple[int, str, str], list[int]]
) -> dict[str, dict]:
    result: dict[str, dict] = {}
    total_cells = 0
    suppressed_cells = 0

    for cluster_id in sorted(baseline):
        baseline_values = baseline[cluster_id]
        baseline_mean = sum(baseline_values) / len(baseline_values)
        comp_id = f"c_{cluster_id:02d}"

        entries_by_bucket: dict[str, list[dict]] = {"active": [], "high": []}
        for (cid, trait_id, bucket), placements in exposure.items():
            if cid != cluster_id:
                continue
            total_cells += 1
            n = len(placements)
            if n < MIN_N:
                suppressed_cells += 1
                continue
            mean, lo, hi = mean_ci(placements)
            entries_by_bucket[bucket].append(
                {
                    "trait": trait_id,
                    "delta": round(mean - baseline_mean, 3),
                    "n": n,
                    "ci": [round(lo, 3), round(hi, 3)],
                }
            )

        # (delta, trait) tie-break, not just delta: `trait_id` is unique within
        # a (cluster, bucket) pair, so this makes the sort fully deterministic
        # regardless of entries_by_bucket's insertion order — which otherwise
        # depends on Python's hash-randomized `set[str]` iteration order in
        # compute_trait_exposure() (active_traits/high_traits), a different
        # order every process run. Two runs on identical
        # input produced different comp_traits.json — sorted() alone is a
        # stable sort, but "stable" only preserves whatever pre-sort order a
        # tied delta arrived in, and that order wasn't deterministic. Fixing
        # PYTHONHASHSEED would paper over this for one process's set order
        # without making the sort itself order-independent.
        active_sorted = sorted(entries_by_bucket["active"], key=lambda e: (e["delta"], e["trait"]))
        high_sorted = sorted(entries_by_bucket["high"], key=lambda e: (e["delta"], e["trait"]))

        result[comp_id] = {
            "strong_against": active_sorted[:TOP_K],
            "weak_against": list(reversed(active_sorted[-TOP_K:])) if active_sorted else [],
            "strong_against_high_tier": high_sorted[:TOP_K],
            "weak_against_high_tier": list(reversed(high_sorted[-TOP_K:])) if high_sorted else [],
        }

    logger.info(
        "Trait exposure: %d (cluster, trait, bucket) cells total, %d suppressed as insufficient data (n < %d)",
        total_cells, suppressed_cells, MIN_N,
    )
    return result


def run() -> None:
    ensure_dirs()
    rows = _load_rows_with_labels()
    high_thresholds = _load_high_tier_thresholds()
    baseline, exposure = compute_trait_exposure(rows, high_thresholds)
    comp_traits = build_comp_traits(baseline, exposure)

    COMP_TRAITS_PATH.write_text(json.dumps(comp_traits, indent=2), encoding="utf-8")
    logger.info("Wrote %s", COMP_TRAITS_PATH)

    for comp_id, entry in comp_traits.items():
        if entry["weak_against"]:
            top = entry["weak_against"][0]
            logger.info("%s struggles most against %s (delta=%+.3f, n=%d)", comp_id, top["trait"], top["delta"], top["n"])
        if entry["strong_against"]:
            top = entry["strong_against"][0]
            logger.info("%s is most comfortable against %s (delta=%+.3f, n=%d)", comp_id, top["trait"], top["delta"], top["n"])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()


if __name__ == "__main__":
    main()
