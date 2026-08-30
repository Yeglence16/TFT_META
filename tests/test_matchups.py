import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.matchups import build_matchup_matrix, compute_matchup_counts, wilson_ci


def test_wilson_ci_matches_known_values():
    # Cross-checked against scipy.stats.norm-based reference computation.
    lo, hi = wilson_ci(60, 100)
    assert lo == pytest.approx(0.5020025867910618)
    assert hi == pytest.approx(0.6905987135675411)


def test_wilson_ci_zero_n_does_not_crash():
    assert wilson_ci(0, 0) == (0.0, 0.0)


def test_wilson_ci_bounds_stay_within_0_and_1():
    lo, hi = wilson_ci(0, 50)
    assert 0.0 <= lo <= hi <= 1.0
    lo, hi = wilson_ci(5, 5)
    assert 0.0 <= lo <= hi <= 1.0


def _row(match_id, cluster_label, placement):
    return {"match_id": match_id, "cluster_label": cluster_label, "placement": placement}


def test_mirror_matchup_rate_is_always_exactly_half():
    # Two different players both in cluster 3, in the same match: whichever
    # finishes above the other, summing over both ordered pairs must give
    # exactly 0.5 — SPEC.md calls this the meaningful "mirror" case.
    rows = [_row("m1", 3, 1), _row("m1", 3, 2)]
    counts = compute_matchup_counts(rows)
    n, above = counts[(3, 3)]
    assert n == 2
    assert above == 1
    matrix = build_matchup_matrix(counts)
    # n=2 is below MIN_N, so it's marked insufficient rather than given a rate.
    assert matrix["c_03"]["c_03"]["insufficient_data"] is True


def test_rate_a_vs_b_and_b_vs_a_sum_to_one():
    rows = [_row("m1", 1, 2), _row("m1", 2, 5)] * 60  # 60 matches worth, n=60 each way (still < MIN_N by design)
    # Build enough observations to clear MIN_N and get real rates.
    rows = []
    for i in range(150):
        rows.append(_row(f"m{i}", 1, 1))  # cluster 1 finishes 1st
        rows.append(_row(f"m{i}", 2, 5))  # cluster 2 finishes 5th
    counts = compute_matchup_counts(rows)
    matrix = build_matchup_matrix(counts)
    rate_ab = matrix["c_01"]["c_02"]["rate"]
    rate_ba = matrix["c_02"]["c_01"]["rate"]
    assert rate_ab + rate_ba == pytest.approx(1.0)
    assert rate_ab == 1.0  # cluster 1 always finished above cluster 2 in this synthetic data


def test_off_meta_participants_excluded_from_matchups():
    rows = [_row("m1", 1, 1), _row("m1", -2, 2)]  # -2 = OFF_META_CODE
    counts = compute_matchup_counts(rows)
    assert counts == {}


def test_cells_below_min_n_are_marked_insufficient_not_given_a_rate():
    rows = [_row("m1", 1, 1), _row("m1", 2, 2)]
    counts = compute_matchup_counts(rows)
    matrix = build_matchup_matrix(counts)
    cell = matrix["c_01"]["c_02"]
    assert cell["insufficient_data"] is True
    assert "rate" not in cell
    assert "ci" not in cell
