"""Head-to-head placement rate matrix (SPEC.md section 10).

Terminology (SPEC.md section 0): this is a **head-to-head placement rate**
— "aynı lobide her iki comp da varken A'nın B'den yukarıda bitirme oranı."
Riot's match API only gives final boards and final placement, never
turn-by-turn combat, so nothing about *why* one comp beat another can be
inferred — never call this a "counter" or a "win rate" in code or output.

Only participants clustering.py assigned to a real comp count — off-meta
(`cluster_labels.npy` value -2) isn't a coherent comp to compare against
and is excluded entirely.

For every match, for every ordered pair of participants (i, j) with i != j,
both in a real cluster: record whether i finished above j (lower placement
number = better). Same-cluster pairs (A vs A, a "mirror" lobby — two
players independently landed on the same comp) are kept, not skipped —
SPEC.md is explicit that this is meaningful, not noise.

Wilson score interval (95% confidence) — not the normal approximation,
which breaks down near 0 or 1 (SPEC.md is explicit about this). Cells with
n < 100 are kept in the output (so the pair is still visible) but marked
`"insufficient_data": true` instead of a rate/ci — SPEC.md: "Sayı ve aralık
olmadan hiçbir oran gösterilmeyecek."

Output: data/processed/matchups.json — SPEC-WEB.md section 3.5 shape:
    {"c_07": {"c_12": {"rate": ..., "n": ..., "ci": [lo, hi]}, ...}, ...}
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

from src.paths import DATA_PROCESSED, ensure_dirs

logger = logging.getLogger(__name__)

META_ROWS_PATH = DATA_PROCESSED / "features_meta.jsonl"
CLUSTER_LABELS_PATH = DATA_PROCESSED / "cluster_labels.npy"
MATCHUPS_PATH = DATA_PROCESSED / "matchups.json"

OFF_META_CODE = -2
MIN_N = 100
Z_95 = 1.959963984540054  # scipy.stats.norm.ppf(0.975), hardcoded to avoid a scipy dependency for one constant


def wilson_ci(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion. Correct near 0/1,
    unlike the normal approximation (SPEC.md is explicit this matters)."""
    if n == 0:
        return (0.0, 0.0)
    p_hat = successes / n
    denom = 1 + z * z / n
    center = (p_hat + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _load_labeled_participants() -> list[dict]:
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


def compute_matchup_counts(rows: list[dict]) -> dict[tuple[int, int], list[int]]:
    """Returns {(cluster_a, cluster_b): [n, above]} across every match,
    considering only participants with a real cluster label."""
    by_match: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        if row["cluster_label"] == OFF_META_CODE:
            continue
        by_match[row["match_id"]].append(row)

    counts: dict[tuple[int, int], list[int]] = collections.defaultdict(lambda: [0, 0])
    for participants in by_match.values():
        for i, p_i in enumerate(participants):
            for j, p_j in enumerate(participants):
                if i == j:
                    continue
                key = (p_i["cluster_label"], p_j["cluster_label"])
                counts[key][0] += 1
                if p_i["placement"] < p_j["placement"]:
                    counts[key][1] += 1

    return counts


def build_matchup_matrix(counts: dict[tuple[int, int], list[int]]) -> dict[str, dict]:
    matrix: dict[str, dict] = {}
    suppressed = 0
    for (cluster_a, cluster_b), (n, above) in counts.items():
        comp_a = f"c_{cluster_a:02d}"
        comp_b = f"c_{cluster_b:02d}"
        matrix.setdefault(comp_a, {})
        if n < MIN_N:
            matrix[comp_a][comp_b] = {"n": n, "insufficient_data": True}
            suppressed += 1
            continue
        rate = above / n
        lo, hi = wilson_ci(above, n)
        matrix[comp_a][comp_b] = {"rate": round(rate, 3), "n": n, "ci": [round(lo, 3), round(hi, 3)]}

    logger.info(
        "Matchup matrix: %d comp pairs total, %d suppressed as insufficient data (n < %d)",
        sum(len(v) for v in matrix.values()), suppressed, MIN_N,
    )
    return matrix


def run() -> None:
    ensure_dirs()
    rows = _load_labeled_participants()
    counts = compute_matchup_counts(rows)
    matrix = build_matchup_matrix(counts)
    MATCHUPS_PATH.write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    logger.info("Wrote %s", MATCHUPS_PATH)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()


if __name__ == "__main__":
    main()
