import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.traits import build_comp_traits, compute_trait_exposure, mean_ci


def test_mean_ci_single_value_returns_degenerate_interval():
    assert mean_ci([5]) == (5.0, 5.0, 5.0)


def test_mean_ci_matches_hand_computed_value():
    # values [1,2,3,4,5]: mean=3, sample std=sqrt(2.5)=1.5811, se=1.5811/sqrt(5)=0.7071
    mean, lo, hi = mean_ci([1, 2, 3, 4, 5])
    assert mean == pytest.approx(3.0)
    margin = 1.959963984540054 * (1.5811388300841898 / (5**0.5))
    assert lo == pytest.approx(3.0 - margin)
    assert hi == pytest.approx(3.0 + margin)


def _row(match_id, cluster_label, placement, raw_traits):
    return {"match_id": match_id, "cluster_label": cluster_label, "placement": placement, "raw_traits": raw_traits}


def test_negative_delta_when_cluster_does_better_facing_a_trait():
    # Cluster 1's baseline placement averages 4.5 (2,3,4,7); when facing
    # trait "T" it only ever placed 1st or 2nd -> should be a large negative
    # delta (comfortable / "strong_against").
    rows = [
        _row("m1", 1, 1, {}) ,
        _row("m1", 99, 8, {"T": 2}),  # opponent has T active
    ] * 60  # n=60 (below MIN_N=100 by design, bumped below)
    rows = []
    for i in range(150):
        rows.append(_row(f"m{i}", 1, 1, {}))       # cluster 1 always 1st when facing T
        rows.append(_row(f"m{i}", 99, 8, {"T": 2}))  # opponent always has T active
    # Also give cluster 1 some baseline games without trait T, placing worse,
    # so overall baseline mean is higher than the T-exposed mean.
    for i in range(150):
        rows.append(_row(f"n{i}", 1, 8, {}))
        rows.append(_row(f"n{i}", 98, 1, {}))

    high_thresholds = {"T": 4}  # tier_current=2 is not maxed, so only "active" bucket applies
    baseline, exposure = compute_trait_exposure(rows, high_thresholds)
    comp_traits = build_comp_traits(baseline, exposure)

    assert comp_traits["c_01"]["strong_against"][0]["trait"] == "T"
    assert comp_traits["c_01"]["strong_against"][0]["delta"] < 0
    assert comp_traits["c_01"]["weak_against_high_tier"] == []  # tier_current never reached max


def test_off_meta_excluded_as_subject_but_still_counts_as_opponent():
    rows = [_row("m1", -2, 1, {"T": 5}), _row("m1", 1, 2, {})]
    high_thresholds = {"T": 5}
    baseline, exposure = compute_trait_exposure(rows, high_thresholds)
    assert -2 not in baseline  # off-meta never a subject
    assert exposure[(1, "T", "active")] == [2]  # cluster 1 faced T from the off-meta opponent
    assert exposure[(1, "T", "high")] == [2]  # tier_current(5) >= max(5) -> maxed


def test_cells_below_min_n_excluded_from_ranked_lists():
    rows = [_row("m1", 1, 3, {"T": 1}), _row("m1", 2, 5, {})]
    baseline, exposure = compute_trait_exposure(rows, {"T": 10})
    comp_traits = build_comp_traits(baseline, exposure)
    # n=1 for cluster 1 facing T -> below MIN_N=100, must not appear at all
    assert comp_traits["c_01"]["strong_against"] == []
    assert comp_traits["c_01"]["weak_against"] == []


def test_tied_delta_tie_break_is_order_independent_and_by_trait_id():
    # Regression (2026-08-30): compute_trait_exposure() builds `exposure` by
    # iterating active_traits/high_traits, which are Python sets of strings —
    # hash-randomized per process, so which of two exactly-tied traits was
    # inserted first (and therefore sorted first by the old delta-only sort
    # key) varied between runs on identical input. build_comp_traits() now
    # sorts by (delta, trait), so the two possible insertion orders below
    # must produce byte-identical output regardless of which trait's cell
    # happened to land in `exposure` first.
    baseline = {1: [4] * 100}  # baseline mean = 4.0
    exposure_a_first = {
        (1, "A", "active"): [6] * 100,
        (1, "Z", "active"): [6] * 100,
    }
    exposure_z_first = {
        (1, "Z", "active"): [6] * 100,
        (1, "A", "active"): [6] * 100,
    }

    result_a_first = build_comp_traits(baseline, exposure_a_first)
    result_z_first = build_comp_traits(baseline, exposure_z_first)

    assert result_a_first == result_z_first
    assert [e["trait"] for e in result_a_first["c_01"]["strong_against"]] == ["A", "Z"]
    assert [e["trait"] for e in result_a_first["c_01"]["weak_against"]] == ["Z", "A"]
