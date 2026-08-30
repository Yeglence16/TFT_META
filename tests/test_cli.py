import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.cli import (
    build_board_vector,
    compute_active_traits,
    format_head_to_head,
    fuzzy_match_units,
    nearest_comps,
    predict_placement,
)


UNITS_STATIC = {
    "u_ahri": {"name": "Ahri", "cost": 4, "traits": ["t_sorcerer", "t_wisp"]},
    "u_ornn": {"name": "Ornn", "cost": 1, "traits": ["t_defender"]},
    "u_sett": {"name": "Sett", "cost": 4, "traits": ["t_brawler"]},
}
TRAITS_STATIC = {
    "t_sorcerer": {"breakpoints": [2, 4, 6]},
    "t_wisp": {"breakpoints": [2, 4]},
    "t_defender": {"breakpoints": [2, 4]},
    "t_brawler": {"breakpoints": [2, 4, 6, 8]},
}


def test_fuzzy_match_exact_case_insensitive():
    matched, unmatched = fuzzy_match_units(["ahri", "ORNN"], UNITS_STATIC)
    assert matched == ["u_ahri", "u_ornn"]
    assert unmatched == []


def test_fuzzy_match_typo_still_resolves():
    matched, unmatched = fuzzy_match_units(["Ahr"], UNITS_STATIC)  # missing last letter
    assert matched == ["u_ahri"]
    assert unmatched == []


def test_fuzzy_match_unrecognized_name_reported_not_dropped_silently():
    matched, unmatched = fuzzy_match_units(["Ahri", "Zzzxxqq"], UNITS_STATIC)
    assert matched == ["u_ahri"]
    assert unmatched == ["Zzzxxqq"]


def test_fuzzy_match_deduplicates_repeated_unit():
    matched, unmatched = fuzzy_match_units(["Ahri", "ahri"], UNITS_STATIC)
    assert matched == ["u_ahri"]


def test_compute_active_traits_applies_breakpoints():
    # Ahri (sorcerer, wisp) + Sett (brawler): sorcerer count=1 (below first
    # breakpoint 2) -> inactive; only single sorcerer/wisp/brawler present.
    active = compute_active_traits(["u_ahri", "u_sett"], UNITS_STATIC, TRAITS_STATIC)
    assert active == {}  # every trait has only 1 unit, below every breakpoint


def test_compute_active_traits_reaches_first_breakpoint():
    active = compute_active_traits(["u_ahri", "u_ornn"], UNITS_STATIC, TRAITS_STATIC)
    # sorcerer=1, wisp=1, defender=1 -> none reach breakpoint 2 yet
    assert active == {}


def test_compute_active_traits_two_sorcerers_reaches_tier_one():
    units = dict(UNITS_STATIC)
    units["u_ahri2"] = {"name": "Ahri2", "cost": 4, "traits": ["t_sorcerer"]}
    active = compute_active_traits(["u_ahri", "u_ahri2"], units, TRAITS_STATIC)
    assert active["t_sorcerer"] == 1  # count=2 meets first breakpoint (2), not second (4)


def test_build_board_vector_is_l2_normalized():
    unit_order = ["u_ahri", "u_ornn", "u_sett"]
    trait_order = ["t_sorcerer", "t_wisp", "t_defender", "t_brawler"]
    vector = build_board_vector(["u_ahri", "u_ornn"], 2, unit_order, trait_order, UNITS_STATIC, TRAITS_STATIC)
    assert vector.shape == (7,)
    norm = np.linalg.norm(vector)
    assert norm == pytest.approx(1.0) or norm == pytest.approx(0.0)


def test_build_board_vector_ignores_units_not_in_unit_order():
    unit_order = ["u_ornn"]  # ahri deliberately excluded from the vocabulary
    trait_order = []
    vector = build_board_vector(["u_ahri", "u_ornn"], 2, unit_order, trait_order, UNITS_STATIC, TRAITS_STATIC)
    assert vector.shape == (1,)
    assert vector[0] == pytest.approx(1.0)  # only ornn contributes, then L2-normalized to 1.0


def test_nearest_comps_sorted_by_distance_ascending():
    vector = np.array([1.0, 0.0])
    centroids = {
        "c_far": np.array([0.0, 1.0]),  # orthogonal -> distance 1.0
        "c_near": np.array([1.0, 0.0]),  # identical -> distance 0.0
    }
    ranked = nearest_comps(vector, centroids)
    assert [comp_id for comp_id, _ in ranked] == ["c_near", "c_far"]
    assert ranked[0][1] == pytest.approx(0.0)
    assert ranked[1][1] == pytest.approx(1.0)


def _cell(rate: float, n: int = 150) -> dict:
    return {"rate": rate, "n": n, "ci": [max(0.0, rate - 0.05), min(1.0, rate + 0.05)]}


def test_format_head_to_head_few_entries_uses_single_list_no_overlap():
    # 6 valid entries, TOP_K=5 — a naive top-5/bottom-5 split would repeat 4
    # of them on both sides (the real bug found against c_11's real data).
    matchups = {
        "c_a": {
            "c_1": _cell(0.53), "c_2": _cell(0.58), "c_3": _cell(0.58),
            "c_4": _cell(0.61), "c_5": _cell(0.62), "c_6": _cell(0.62),
        }
    }
    comps = {f"c_{i}": {"name": f"Comp{i}"} for i in range(1, 7)}
    lines = format_head_to_head("c_a", matchups, comps)
    text = "\n".join(lines)
    assert "Bilinen head-to-head eşleşmeler" in text
    assert "En yüksek üstünlük" not in text
    assert "En düşük üstünlük" not in text
    for i in range(1, 7):
        assert text.count(f"Comp{i}:") == 1  # each opponent shown exactly once


def test_format_head_to_head_many_entries_splits_without_overlap():
    matchups = {"c_a": {f"c_{i}": _cell(0.30 + i * 0.05) for i in range(1, 13)}}  # 12 valid entries
    comps = {f"c_{i}": {"name": f"Comp{i}"} for i in range(1, 13)}
    lines = format_head_to_head("c_a", matchups, comps)
    text = "\n".join(lines)
    best_section, worst_section = text.split("En düşük üstünlük")

    best_opponents = {i for i in range(1, 13) if f"Comp{i}:" in best_section}
    worst_opponents = {i for i in range(1, 13) if f"Comp{i}:" in worst_section}
    assert best_opponents.isdisjoint(worst_opponents)
    assert len(best_opponents) == 5
    assert len(worst_opponents) == 5


def test_format_head_to_head_notes_when_every_matchup_above_50_percent():
    matchups = {"c_a": {"c_1": _cell(0.55), "c_2": _cell(0.60)}}
    comps = {"c_1": {"name": "C1"}, "c_2": {"name": "C2"}}
    lines = format_head_to_head("c_a", matchups, comps)
    assert any("%50'nin altına düşmüyor" in line for line in lines)


def test_format_head_to_head_notes_when_every_matchup_below_50_percent():
    matchups = {"c_a": {"c_1": _cell(0.40), "c_2": _cell(0.45)}}
    comps = {"c_1": {"name": "C1"}, "c_2": {"name": "C2"}}
    lines = format_head_to_head("c_a", matchups, comps)
    assert any("%50'nin üstüne çıkmıyor" in line for line in lines)


def test_format_head_to_head_no_note_when_matchups_span_50_percent():
    matchups = {"c_a": {"c_1": _cell(0.40), "c_2": _cell(0.60)}}
    comps = {"c_1": {"name": "C1"}, "c_2": {"name": "C2"}}
    lines = format_head_to_head("c_a", matchups, comps)
    assert not any("%50" in line for line in lines)


def test_format_head_to_head_no_valid_data():
    assert format_head_to_head("c_a", {}, {}) == ["  (yeterli veri yok)"]


def test_predict_placement_clips_below_range():
    class _ExtremeLowModel:
        def predict(self, X):
            return np.array([-4.5])

    assert predict_placement(_ExtremeLowModel(), np.zeros(5)) == pytest.approx(1.0)


def test_predict_placement_clips_above_range():
    class _ExtremeHighModel:
        def predict(self, X):
            return np.array([15.0])

    assert predict_placement(_ExtremeHighModel(), np.zeros(5)) == pytest.approx(8.0)
