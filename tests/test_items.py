import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.items import build_items_report, compute_item_cells, compute_item_global_means, compute_item_pool_usage


def _row(cluster_label, placement, raw_units):
    return {"cluster_label": cluster_label, "placement": placement, "raw_units": raw_units}


def test_compute_item_cells_excludes_off_meta():
    rows = [
        _row(-2, 1, {"u1": {"items": ["item_a"]}}),  # off-meta, excluded
        _row(0, 2, {"u1": {"items": ["item_a"]}}),
    ]
    cells = compute_item_cells(rows, completed_item_ids={"item_a"})
    assert (-2, "u1", "item_a") not in cells
    assert cells[(0, "u1", "item_a")] == [2]


def test_compute_item_cells_excludes_non_completed_items():
    rows = [_row(0, 3, {"u1": {"items": ["component_x", "item_a"]}})]
    cells = compute_item_cells(rows, completed_item_ids={"item_a"})
    assert (0, "u1", "component_x") not in cells
    assert cells[(0, "u1", "item_a")] == [3]


def test_compute_item_global_means():
    cells = {(0, "u1", "item_a"): [1, 3], (1, "u2", "item_a"): [5]}
    means = compute_item_global_means(cells)
    assert means["item_a"] == pytest.approx((1 + 3 + 5) / 3)


def test_lift_is_negative_when_item_beats_cluster_baseline():
    # Cluster 0's baseline is 5.0; every observation of item_a on unit u1 is
    # placement 1 -> should be a strongly negative lift.
    cells = {(0, "u1", "item_a"): [1] * 60}
    global_means = {"item_a": 1.0}
    baselines = {0: 5.0}
    core_units = {0: ["u1"]}
    static_items = {"item_a": {"name": "Item A"}}

    report, coverage = build_items_report(cells, global_means, baselines, core_units, static_items, shrinkage_k=50)
    best = report["c_00"]["u1"][0]
    assert best["id"] == "item_a"
    assert best["lift"] < 0
    assert best["n"] == 60


def test_shrinkage_pulls_small_n_cell_toward_item_global_mean():
    # item_a: only 55 observations here (just above MIN_N=50), cell mean=1,
    # but the item's global mean (across all clusters/units) is 6 -> shrunk
    # mean should sit between 1 and 6, not stay at the raw cell mean of 1.
    cells = {(0, "u1", "item_a"): [1] * 55}
    global_means = {"item_a": 6.0}
    baselines = {0: 0.0}  # baseline 0 so lift == shrunk_mean directly, easy to inspect
    core_units = {0: ["u1"]}
    static_items = {"item_a": {"name": "Item A"}}

    report, coverage = build_items_report(cells, global_means, baselines, core_units, static_items, shrinkage_k=50)
    shrunk_mean = report["c_00"]["u1"][0]["lift"]  # baseline=0, so lift == shrunk mean
    expected = (55 * 1.0 + 50 * 6.0) / (55 + 50)
    assert shrunk_mean == pytest.approx(expected, abs=1e-3)
    assert 1.0 < shrunk_mean < 6.0


def test_cells_below_min_n_excluded_from_top_k():
    cells = {(0, "u1", "item_a"): [1] * 10}  # n=10, below MIN_N=50
    global_means = {"item_a": 1.0}
    baselines = {0: 5.0}
    core_units = {0: ["u1"]}
    static_items = {"item_a": {"name": "Item A"}}

    report, coverage = build_items_report(cells, global_means, baselines, core_units, static_items, shrinkage_k=50)
    assert report["c_00"]["u1"] == []


def test_only_units_in_core_units_list_appear_in_report():
    cells = {(0, "u1", "item_a"): [1] * 60, (0, "u2", "item_a"): [1] * 60}
    global_means = {"item_a": 1.0}
    baselines = {0: 5.0}
    core_units = {0: ["u1"]}  # u2 deliberately not a core unit
    static_items = {"item_a": {"name": "Item A"}}

    report, coverage = build_items_report(cells, global_means, baselines, core_units, static_items, shrinkage_k=50)
    assert "u2" not in report["c_00"]


def test_top_k_limits_to_three_items():
    cells = {(0, "u1", f"item_{i}"): [1] * 60 for i in range(5)}
    global_means = {f"item_{i}": 1.0 for i in range(5)}
    baselines = {0: 5.0}
    core_units = {0: ["u1"]}
    static_items = {f"item_{i}": {"name": f"Item {i}"} for i in range(5)}

    report, coverage = build_items_report(cells, global_means, baselines, core_units, static_items, shrinkage_k=50)
    assert len(report["c_00"]["u1"]) == 3


def test_coverage_counts_pairs_by_how_many_items_they_produced():
    # u1: 5 items all clearing MIN_N -> full 3. u2: 1 item clearing MIN_N -> 1.
    # u3: only a below-threshold cell -> 0 items, still counted in the pair total.
    cells = {
        **{(0, "u1", f"item_{i}"): [1] * 60 for i in range(5)},
        (0, "u2", "item_x"): [1] * 60,
        (0, "u3", "item_y"): [1] * 10,  # below MIN_N=50
    }
    global_means = {f"item_{i}": 1.0 for i in range(5)} | {"item_x": 1.0, "item_y": 1.0}
    baselines = {0: 5.0}
    core_units = {0: ["u1", "u2", "u3"]}
    static_items = {k: {"name": k} for k in ["item_0", "item_1", "item_2", "item_3", "item_4", "item_x", "item_y"]}

    report, coverage = build_items_report(cells, global_means, baselines, core_units, static_items, shrinkage_k=50)

    assert coverage["total_cluster_unit_pairs"] == 3
    assert coverage["pairs_with_at_least_1_item"] == 2  # u1, u2 (u3 produced 0)
    assert coverage["pairs_with_full_3_items"] == 1  # only u1
    assert coverage["cells_total"] == 7  # 5 (u1) + 1 (u2) + 1 (u3)
    assert coverage["cells_below_min_n"] == 1  # only u3's cell
    assert coverage["cells_below_min_n_fraction"] == pytest.approx(1 / 7, abs=1e-3)


def test_build_output_wraps_metadata_and_keeps_comp_keys():
    from src.items import build_output

    report = {"c_00": {"u1": [{"id": "item_a", "name": "Item A", "lift": -0.5, "n": 60}]}}
    coverage = {"total_cluster_unit_pairs": 1, "pairs_with_at_least_1_item": 1, "pairs_with_full_3_items": 0}
    item_pool_usage = {"completed": {"total": 1, "never_used": 0, "rare": 0, "common": 1}}

    output = build_output(report, coverage, item_pool_usage)

    assert "known_limitation" in output["_metadata"]
    assert output["_metadata"]["coverage"] == coverage
    assert output["_metadata"]["item_pool_usage"] == item_pool_usage
    assert output["c_00"] == report["c_00"]
    assert "_metadata" not in report  # build_output doesn't mutate its input


def _usage_row(raw_units):
    return {"raw_units": raw_units}


def test_compute_item_pool_usage_buckets_by_never_used_rare_common():
    # Enough rows so item_common clears MIN_N=50 and item_rare stays under it.
    rows = [_usage_row({"u1": {"items": ["item_common"]}}) for _ in range(60)]
    rows += [_usage_row({"u1": {"items": ["item_rare"]}}) for _ in range(10)]
    # item_dead never appears in any row.

    static_items = {
        "item_common": {"type": "completed"},
        "item_rare": {"type": "completed"},
        "item_dead": {"type": "other"},
    }

    usage = compute_item_pool_usage(rows, static_items)

    assert usage["completed"] == {"total": 2, "never_used": 0, "rare": 1, "common": 1}
    assert usage["other"] == {"total": 1, "never_used": 1, "rare": 0, "common": 0}
