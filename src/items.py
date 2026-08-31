"""Item lift analysis per (cluster, unit, item) (SPEC.md section 15).

Item is NOT part of the clustering vector (SPEC.md sections 8-9) — comp
identity stays item-independent, since two players on the same comp
routinely carry different items. This module is a separate analysis layer
answering a different question than clustering does: "given this comp,
which item on which of its core units correlates with a better placement?"

Same two corrections planned for augments.py (SPEC.md section 11, still
unimplemented — Riot's API returns no augment data at all right now, see
features.py/README):

1. **Lift**: a cell's average placement minus the CLUSTER's own average
   placement (comps.json's avg_placement, computed over the full labeled
   population per the survivorship-bias fix — see clustering.py). Negative
   = better than the comp's own baseline (lower placement is better in
   TFT), same sign convention as comp_traits.json's delta.
2. **Empirical Bayes shrinkage**: the cell mean is pulled toward that
   item's OWN global average placement (across every cluster/unit, not
   just this cell) before computing lift:
   `shrunk = (n*cell_mean + k*global_mean) / (n+k)`,
   k = config.yaml's `augment_shrinkage_k` (shared with the still-
   unimplemented augments.py — same shrinkage concept, same knob, no need
   for a second near-identical config key).

Only COMPLETED items are reported (SPEC.md: "component'leri değil,
tamamlanmış item'ları raporla") — static_data.py's items.json `type` field
says which is which; components sitting unbuilt on a board are excluded
from consideration entirely, not just from the output.

**Checked, not an issue here: duplicate-name items splitting cell counts.**
static_data.py's 502-item pool has 163 groups of items sharing the same
in-game display name under different apiNames — most are dead "other"-type
reward-track duplicates (0 real usage on either id), but two are real:
"Flora Fatalis Emblem" (`DA_18_EmblemFloraFatalis` + `...Augment`, ~548 +
~774 real occurrences) and "Phantom Emblem" (both ids unused). If this
module aggregated by id across a duplicate-name completed item, a single
real item's data could get split across two ids and each half could fall
below MIN_N on its own. Checked directly: zero duplicate-name groups touch
the `completed` (or `component`) type — every one of the 163 groups is
pure `other` or pure `emblem`, neither of which this module counts. So this
concern, real as the underlying phenomenon is, has zero effect on any
(cluster, unit, item) cell here. Not fixed because there's nothing to fix.

For each cluster, for each of its core_units (comps.json's list — now a
>=50%-of-boards frequency threshold, bounded to [5, 12] units; see
clustering.py's `select_core_units`, no longer a fixed top-8), the best 3
items by lift are reported. Cells with n < 50 are excluded from that
ranking entirely (not flagged-and-kept, same reasoning as traits.py's
top-5 lists: a single lucky/unlucky observation could otherwise dominate a
"best 3" ranking).

**Known limitation — confounding (not fixed, documented instead).** This
lift measures a mix of two things it cannot separate: the item's own
contribution, and the fact that reaching a completed item at all — let
alone on the intended carry — correlates with a better game already in
progress (enough gold/rolls to hit it, no early elimination, etc.). Augment
selection is comparatively exogenous (offered from a small random set,
independent of how the game is going), so this same worry is much weaker in
augments.json. It's much stronger here because item completion is itself
downstream of doing well. Not something shrinkage or lift-vs-baseline
fixes — it's a selection effect in *which boards have this cell at all*,
not a noise problem in the cell's mean. Flagged via
`_metadata.known_limitation` in the output JSON so a frontend can surface
it, not silently presented as a clean causal effect.

**Coverage.** `n < 50` operates on a 3-way combination (cluster × unit ×
item), so it can suppress a lot before ever reaching the top-3 step —
`_metadata.coverage` in the output reports, and this module logs, how many
(cluster, core_unit) pairs got 0 vs. some vs. the full 3 items, and what
fraction of raw cells were suppressed.

**Static item pool has a lot of dead weight — measured, not filtered.**
static_data.py's 502-item pool (DA_-prefix filter) is 72.1% never seen in
real match data at all (mostly the `other` bucket: 360/430, 83.7%). Decided
NOT to switch to a usage-based filter: the DA_ filter produces zero false
negatives (every one of the 140 distinct item ids actually seen in real
matches is in the 502-item pool), the one bucket this module actually reads
(`completed`) is 100% clean (39/39 seen commonly, 0 dead), and every dead
record lives in `other`, which this module never touches anyway. Switching
to usage-based filtering would also make static_data.py's output depend on
which matches happen to be in `data/processed/` at the time — it's
currently independent of the scrape/sample size, and that independence is
worth more than trimming a bucket nothing reads from. `_metadata.
item_pool_usage` below records the dead-record counts per bucket as an
observation, not a filter.

Output: data/processed/items.json, SPEC-WEB.md section 3.8 shape:
    {"_metadata": {"known_limitation": "...", "coverage": {...}, "item_pool_usage": {...}},
     "c_07": {"DA_18_Ahri": [{"id": ..., "name": ..., "lift": ..., "n": ...}]}}
"""

from __future__ import annotations

import collections
import json
import logging
import sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import DATA_PROCESSED, DATA_STATIC, PROJECT_ROOT, ensure_dirs

logger = logging.getLogger(__name__)

CONFIG_PATH = PROJECT_ROOT / "config.yaml"
META_ROWS_PATH = DATA_PROCESSED / "features_meta.jsonl"
CLUSTER_LABELS_PATH = DATA_PROCESSED / "cluster_labels.npy"
COMPS_PATH = DATA_PROCESSED / "comps.json"
STATIC_ITEMS_PATH = DATA_STATIC / "items.json"
ITEMS_OUTPUT_PATH = DATA_PROCESSED / "items.json"

OFF_META_CODE = -2
MIN_N = 50
TOP_K = 3

KNOWN_LIMITATION = (
    "This lift is confounded: reaching a completed item at all (let alone on "
    "the intended carry) correlates with a game that was already going well "
    "(enough gold/rolls, no early elimination) — the lift measures a mix of "
    "the item's own effect and 'was able to build this item at all', and the "
    "two can't be separated from placement data alone. Augments are "
    "comparatively exogenous (offered from a small random set), so this "
    "concern is much weaker in augments.json than it is here."
)


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


def _load_static_items() -> dict[str, dict]:
    if not STATIC_ITEMS_PATH.exists():
        raise SystemExit(f"{STATIC_ITEMS_PATH} not found — run static_data.py first")
    return json.loads(STATIC_ITEMS_PATH.read_text(encoding="utf-8"))


def _load_comp_baselines() -> dict[int, float]:
    if not COMPS_PATH.exists():
        raise SystemExit(f"{COMPS_PATH} not found — run clustering.py first")
    comps = json.loads(COMPS_PATH.read_text(encoding="utf-8"))
    baselines = {}
    for comp_id, info in comps.items():
        if not comp_id.startswith("c_"):
            continue  # skips "off_meta" (no coherent comp) and "_metadata" (2026-09-01, avg_placement_quartiles — not a comp)
        cluster_id = int(comp_id.split("_")[1])
        baselines[cluster_id] = info["avg_placement"]
    return baselines


def compute_item_cells(
    rows: list[dict], completed_item_ids: set[str]
) -> dict[tuple[int, str, str], list[int]]:
    """(cluster_id, unit_id, item_id) -> placements. Off-meta excluded
    (not a coherent comp); non-completed items excluded entirely."""
    cells: dict[tuple[int, str, str], list[int]] = collections.defaultdict(list)
    for row in rows:
        if row["cluster_label"] == OFF_META_CODE:
            continue
        for unit_id, info in row["raw_units"].items():
            for item_id in info.get("items", []):
                if item_id not in completed_item_ids:
                    continue
                cells[(row["cluster_label"], unit_id, item_id)].append(row["placement"])
    return cells


def compute_item_global_means(cells: dict[tuple[int, str, str], list[int]]) -> dict[str, float]:
    totals: dict[str, list[int]] = collections.defaultdict(list)
    for (_cluster_id, _unit_id, item_id), placements in cells.items():
        totals[item_id].extend(placements)
    return {item_id: sum(p) / len(p) for item_id, p in totals.items()}


def compute_item_pool_usage(rows: list[dict], static_items: dict[str, dict]) -> dict[str, dict]:
    """How much of static_data.py's item pool is dead weight, per bucket —
    an observation recorded in the output, not a filter (see module
    docstring for why: the DA_ filter has zero false negatives against real
    usage, and the one bucket this module reads is already 100% clean)."""
    usage: collections.Counter = collections.Counter()
    for row in rows:
        for info in row["raw_units"].values():
            for item_id in info.get("items", []):
                usage[item_id] += 1

    by_type: dict[str, dict] = {}
    for item_id, info in static_items.items():
        bucket = by_type.setdefault(info["type"], {"total": 0, "never_used": 0, "rare": 0, "common": 0})
        bucket["total"] += 1
        count = usage.get(item_id, 0)
        if count == 0:
            bucket["never_used"] += 1
        elif count < MIN_N:
            bucket["rare"] += 1
        else:
            bucket["common"] += 1
    return by_type


def build_items_report(
    cells: dict[tuple[int, str, str], list[int]],
    item_global_means: dict[str, float],
    cluster_baselines: dict[int, float],
    core_units_by_cluster: dict[int, list[str]],
    static_items: dict[str, dict],
    shrinkage_k: float,
) -> tuple[dict[str, dict], dict]:
    # (cluster, unit) -> list of {item_id, lift, n}, only cells with n >= MIN_N
    scored_by_cluster_unit: dict[tuple[int, str], list[dict]] = collections.defaultdict(list)
    total_cells = 0
    suppressed = 0

    for (cluster_id, unit_id, item_id), placements in cells.items():
        total_cells += 1
        n = len(placements)
        if n < MIN_N:
            suppressed += 1
            continue
        cell_mean = sum(placements) / n
        global_mean = item_global_means[item_id]
        shrunk_mean = (n * cell_mean + shrinkage_k * global_mean) / (n + shrinkage_k)
        baseline = cluster_baselines.get(cluster_id)
        if baseline is None:
            continue
        lift = shrunk_mean - baseline
        scored_by_cluster_unit[(cluster_id, unit_id)].append(
            {"id": item_id, "name": static_items.get(item_id, {}).get("name", item_id), "lift": round(lift, 3), "n": n}
        )

    report: dict[str, dict] = {}
    pair_count = 0
    pairs_with_any_item = 0
    pairs_with_full_3 = 0
    for cluster_id, core_units in core_units_by_cluster.items():
        comp_id = f"c_{cluster_id:02d}"
        report[comp_id] = {}
        for unit_id in core_units:
            pair_count += 1
            candidates = scored_by_cluster_unit.get((cluster_id, unit_id), [])
            best = sorted(candidates, key=lambda c: c["lift"])[:TOP_K]
            report[comp_id][unit_id] = best
            if len(best) >= 1:
                pairs_with_any_item += 1
            if len(best) == TOP_K:
                pairs_with_full_3 += 1

    coverage = {
        "total_cluster_unit_pairs": pair_count,
        "pairs_with_at_least_1_item": pairs_with_any_item,
        "pairs_with_full_3_items": pairs_with_full_3,
        "cells_below_min_n": suppressed,
        "cells_total": total_cells,
        "cells_below_min_n_fraction": round(suppressed / total_cells, 3) if total_cells else 0.0,
    }

    logger.info(
        "Item cells: %d (cluster, unit, item) combos total, %d suppressed as insufficient data (n < %d, %.1f%%)",
        total_cells, suppressed, MIN_N, 100 * coverage["cells_below_min_n_fraction"],
    )
    logger.info(
        "Coverage: %d (cluster, core_unit) pairs total, %d with >=1 item, %d with the full 3",
        pair_count, pairs_with_any_item, pairs_with_full_3,
    )

    return report, coverage


def build_output(report: dict[str, dict], coverage: dict, item_pool_usage: dict) -> dict:
    output = {
        "_metadata": {
            "known_limitation": KNOWN_LIMITATION,
            "coverage": coverage,
            "item_pool_usage": item_pool_usage,
        }
    }
    output.update(report)
    return output


def run(config: dict) -> None:
    ensure_dirs()
    shrinkage_k = config["augment_shrinkage_k"]

    rows = _load_rows_with_labels()
    static_items = _load_static_items()
    completed_item_ids = {item_id for item_id, info in static_items.items() if info["type"] == "completed"}
    logger.info("Static items: %d completed items considered (components/emblems/other excluded)", len(completed_item_ids))

    comps = json.loads(COMPS_PATH.read_text(encoding="utf-8"))
    cluster_baselines = _load_comp_baselines()
    core_units_by_cluster = {
        int(comp_id.split("_")[1]): info["core_units"]
        for comp_id, info in comps.items()
        if comp_id.startswith("c_")  # skips "off_meta" and "_metadata" (2026-09-01), same as _load_comp_baselines()
    }

    cells = compute_item_cells(rows, completed_item_ids)
    item_global_means = compute_item_global_means(cells)

    report, coverage = build_items_report(
        cells, item_global_means, cluster_baselines, core_units_by_cluster, static_items, shrinkage_k
    )
    item_pool_usage = compute_item_pool_usage(rows, static_items)
    logger.info("Item pool usage by bucket (observation, not a filter): %s", item_pool_usage)
    output = build_output(report, coverage, item_pool_usage)

    ITEMS_OUTPUT_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
    logger.info("Wrote %s", ITEMS_OUTPUT_PATH)

    for comp_id, by_unit in report.items():
        for unit_id, best in by_unit.items():
            if best:
                top = best[0]
                logger.info("%s / %s: best item %s (lift=%+.3f, n=%d)", comp_id, unit_id, top["name"], top["lift"], top["n"])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    run(config)


if __name__ == "__main__":
    main()
