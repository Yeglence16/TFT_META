import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.clustering import (
    CORE_UNIT_MAX,
    CORE_UNIT_MIN,
    OFF_META,
    _nearest_centroid_labels,
    build_cluster_report,
    select_core_units,
)


def test_nearest_centroid_labels_empty_input_returns_empty():
    result = _nearest_centroid_labels(np.zeros((0, 4)), {0: np.array([1.0, 0, 0, 0])}, 0.5)
    assert len(result) == 0


def test_nearest_centroid_labels_no_centroids_returns_all_off_meta():
    vectors = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    result = _nearest_centroid_labels(vectors, {}, 0.5)
    assert list(result) == [OFF_META, OFF_META]


def test_nearest_centroid_labels_assigns_within_threshold_and_off_meta_beyond():
    centroids = {0: np.array([1.0, 0.0, 0.0]), 1: np.array([0.0, 1.0, 0.0])}
    vectors = np.array(
        [
            [1.0, 0.0, 0.0],  # identical to centroid 0 -> distance 0
            [0.0, 1.0, 0.0],  # identical to centroid 1 -> distance 0
            [0.0, 0.0, 1.0],  # orthogonal to both -> distance 1 from either, beyond threshold
        ]
    )
    labels = _nearest_centroid_labels(vectors, centroids, distance_threshold=0.5)
    assert list(labels) == [0, 1, OFF_META]


def test_build_cluster_report_separates_n_full_from_n_total():
    # 2 boards from the training subset (n_full) + 1 early-death board
    # post-hoc-assigned to the same cluster (n_full stays 2, n_total becomes 3).
    rows = [
        {"placement": 1, "raw_units": {}, "raw_traits": {}},
        {"placement": 2, "raw_units": {}, "raw_traits": {}},
        {"placement": 8, "raw_units": {}, "raw_traits": {}},  # the early-death board, placed last
    ]
    full_labels = np.array([0, 0, 0], dtype=object)
    centroids = {0: np.array([1.0, 0.0])}
    n_full_by_cluster = {0: 2}  # only the first two were part of training

    comps, review_rows = build_cluster_report(rows, full_labels, centroids, {}, {}, n_full_by_cluster)

    assert comps["c_00"]["n_full"] == 2
    assert comps["c_00"]["n_total"] == 3
    # avg_placement must reflect ALL 3 boards (survivorship-bias fix), not just the 2 training ones.
    assert comps["c_00"]["avg_placement"] == pytest.approx(round((1 + 2 + 8) / 3, 3))


def test_select_core_units_keeps_only_units_at_or_above_threshold():
    # n_total=100: unit_a at 60% (above), unit_b at exactly 50% (above,
    # inclusive), unit_c at 49% (below) -> only a and b qualify. Padded to
    # CORE_UNIT_MIN with the next-highest-frequency units below threshold.
    unit_freq = {"unit_a": 60, "unit_b": 50, "unit_c": 49, "unit_d": 10, "unit_e": 5, "unit_f": 1}
    result = select_core_units(unit_freq, n_total=100)
    ids = [u for u, _ in result]
    assert "unit_a" in ids and "unit_b" in ids
    assert len(result) == CORE_UNIT_MIN  # only 2 clear 50%, floor pads to CORE_UNIT_MIN
    assert ids == sorted(unit_freq, key=lambda u: -unit_freq[u])[:CORE_UNIT_MIN]


def test_select_core_units_floor_fills_with_next_highest_when_too_few_clear_threshold():
    # Nobody clears 50% (gradual decline, like c_09) -> falls back to the
    # CORE_UNIT_MIN highest-frequency units regardless of their percentage.
    unit_freq = {f"unit_{i}": 100 - i * 5 for i in range(10)}  # 100,95,...,55 — all < some n_total making none hit 50%
    n_total = 300  # every unit's fraction is well under 50%
    result = select_core_units(unit_freq, n_total)
    assert len(result) == CORE_UNIT_MIN
    assert [u for u, _ in result] == [f"unit_{i}" for i in range(CORE_UNIT_MIN)]


def test_select_core_units_ceiling_truncates_when_too_many_clear_threshold():
    # 15 units all at 100% frequency -> all clear threshold, truncated to CORE_UNIT_MAX.
    unit_freq = {f"unit_{i}": 100 for i in range(15)}
    result = select_core_units(unit_freq, n_total=100)
    assert len(result) == CORE_UNIT_MAX


def test_select_core_units_exact_count_between_floor_and_ceiling_untouched():
    # 8 units clear threshold, 8 is between CORE_UNIT_MIN(5) and CORE_UNIT_MAX(12)
    # -> returned as-is, no padding or truncation.
    unit_freq = {f"unit_{i}": 80 for i in range(8)} | {"unit_low": 10}
    result = select_core_units(unit_freq, n_total=100)
    assert len(result) == 8
    assert "unit_low" not in [u for u, _ in result]
