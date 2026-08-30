import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.build_web_data import (
    SchemaError,
    build_selftest_cases,
    derive_trait_tiers_from_units,
    enrich_items_with_icons,
    nearest_comp,
    validate_comp_traits,
    validate_comps,
    validate_items,
    validate_matchups,
    validate_meta,
    validate_traits,
    validate_units,
)


def _valid_meta(**overrides):
    meta = {
        "set_number": 18,
        "patch": None,
        "regions": ["euw1"],
        "elo_range": "master+",
        "match_count": 100,
        "board_count": 800,
        "collected_at": "2026-08-30",
        "unit_order": ["u1", "u2"],
        "trait_order": ["t1", "t2"],
        "vector_includes_traits": True,
        "normalization": "l2",
        "match_threshold": 0.35,
    }
    meta.update(overrides)
    return meta


def test_validate_meta_accepts_valid_data():
    validate_meta(_valid_meta())  # must not raise


def test_validate_meta_accepts_null_patch():
    # features.py writes null when game_version is unusable (SPEC.md section 7) — not an error.
    validate_meta(_valid_meta(patch=None))


def test_validate_meta_rejects_non_string_non_null_patch():
    with pytest.raises(SchemaError):
        validate_meta(_valid_meta(patch=1516))


def test_validate_meta_rejects_duplicate_unit_order():
    with pytest.raises(SchemaError):
        validate_meta(_valid_meta(unit_order=["u1", "u1"]))


def test_validate_meta_rejects_empty_trait_order():
    with pytest.raises(SchemaError):
        validate_meta(_valid_meta(trait_order=[]))


def test_validate_meta_rejects_missing_match_threshold():
    meta = _valid_meta()
    del meta["match_threshold"]
    with pytest.raises(SchemaError):
        validate_meta(meta)


def _valid_units():
    return {
        "u1": {"name": "Ahri", "cost": 4, "traits": ["t1"], "icon": "https://example.com/ahri.png"},
    }


def test_validate_units_accepts_valid_data():
    validate_units(_valid_units())


def test_validate_units_rejects_cost_out_of_range():
    units = _valid_units()
    units["u1"]["cost"] = 6
    with pytest.raises(SchemaError):
        validate_units(units)


def test_validate_units_rejects_non_http_icon():
    units = _valid_units()
    units["u1"]["icon"] = "assets/ahri.png"
    with pytest.raises(SchemaError):
        validate_units(units)


def _valid_traits():
    return {"t1": {"name": "Sorcerer", "breakpoints": [2, 4, 6], "icon": "https://example.com/sorc.png"}}


def test_validate_traits_accepts_valid_data():
    validate_traits(_valid_traits())


def test_validate_traits_rejects_non_ascending_breakpoints():
    traits = _valid_traits()
    traits["t1"]["breakpoints"] = [4, 2, 6]
    with pytest.raises(SchemaError):
        validate_traits(traits)


def test_validate_traits_rejects_null_breakpoint():
    # Regression: DA_18_Eclipse shipped breakpoints: [null] before static_data.py's fix.
    traits = _valid_traits()
    traits["t1"]["breakpoints"] = [None]
    with pytest.raises(SchemaError):
        validate_traits(traits)


def _valid_comp(**overrides):
    comp = {
        "name": "Ahri — Sorcerer",
        "centroid": [0.5, 0.5],
        "n_full": 100,
        "n_total": 120,
        "avg_placement": 4.2,
        "top4_rate": 0.5,
        "core_units": ["u1", "u2", "u3", "u4", "u5"],
        "common_traits": [["t1", 80]],
    }
    comp.update(overrides)
    return comp


def test_validate_comps_accepts_valid_data():
    validate_comps({"c_00": _valid_comp()}, vector_dim=2)


def test_validate_comps_accepts_off_meta_without_centroid_or_name():
    # off_meta is a real, documented exception in the pipeline's actual output
    # (see build_web_data.py's module docstring, gap #2) — must not raise.
    data = {
        "c_00": _valid_comp(),
        "off_meta": {"n_full": 10, "n_total": 20, "avg_placement": 5.5, "top4_rate": 0.3},
    }
    validate_comps(data, vector_dim=2)


def test_validate_comps_rejects_centroid_dimension_mismatch():
    with pytest.raises(SchemaError):
        validate_comps({"c_00": _valid_comp(centroid=[0.5, 0.5, 0.5])}, vector_dim=2)


def test_validate_comps_rejects_core_units_below_min():
    with pytest.raises(SchemaError):
        validate_comps({"c_00": _valid_comp(core_units=["u1", "u2"])}, vector_dim=2)


def test_validate_comps_rejects_core_units_above_max():
    with pytest.raises(SchemaError):
        validate_comps({"c_00": _valid_comp(core_units=[f"u{i}" for i in range(13)])}, vector_dim=2)


def test_validate_matchups_accepts_full_rate_cell():
    data = {"c_00": {"c_01": {"rate": 0.6, "n": 200, "ci": [0.5, 0.7]}}}
    validate_matchups(data, known_comp_ids={"c_00", "c_01"})


def test_validate_matchups_accepts_insufficient_data_cell():
    # matchups.py keeps n<100 pairs visible instead of dropping them
    # (SPEC.md section 10) — SPEC-WEB.md section 3.5 doesn't document this
    # shape, but it's real, correct pipeline output (gap #1 in the module
    # docstring).
    data = {"c_00": {"c_01": {"n": 42, "insufficient_data": True}}}
    validate_matchups(data, known_comp_ids={"c_00", "c_01"})


def test_validate_matchups_rejects_unknown_comp_id():
    data = {"c_00": {"c_99": {"rate": 0.5, "n": 200, "ci": [0.4, 0.6]}}}
    with pytest.raises(SchemaError):
        validate_matchups(data, known_comp_ids={"c_00", "c_01"})


def test_validate_matchups_rejects_rate_out_of_range():
    data = {"c_00": {"c_01": {"rate": 1.5, "n": 200, "ci": [0.4, 0.6]}}}
    with pytest.raises(SchemaError):
        validate_matchups(data, known_comp_ids={"c_00", "c_01"})


def test_validate_comp_traits_accepts_valid_data():
    data = {"c_00": {"strong_against": [{"trait": "t1", "delta": -0.3, "n": 150}], "weak_against": []}}
    validate_comp_traits(data, known_comp_ids={"c_00"}, known_trait_ids={"t1"})


def test_validate_comp_traits_rejects_unknown_trait_id():
    data = {"c_00": {"strong_against": [{"trait": "t_ghost", "delta": -0.3, "n": 150}], "weak_against": []}}
    with pytest.raises(SchemaError):
        validate_comp_traits(data, known_comp_ids={"c_00"}, known_trait_ids={"t1"})


def _valid_items():
    return {
        "_metadata": {"known_limitation": "confounded", "coverage": {}},
        "c_00": {"u1": [{"id": "i1", "name": "Item", "lift": -0.3, "n": 60, "icon": "https://example.com/i1.png"}]},
    }


def test_validate_items_accepts_valid_data():
    validate_items(_valid_items(), known_comp_ids={"c_00"})


def test_validate_items_rejects_missing_metadata():
    data = _valid_items()
    del data["_metadata"]
    with pytest.raises(SchemaError):
        validate_items(data, known_comp_ids={"c_00"})


def test_validate_items_rejects_n_below_50():
    data = _valid_items()
    data["c_00"]["u1"][0]["n"] = 49
    with pytest.raises(SchemaError):
        validate_items(data, known_comp_ids={"c_00"})


def test_validate_items_rejects_more_than_3_entries():
    data = _valid_items()
    data["c_00"]["u1"] = [{"id": f"i{i}", "name": "Item", "lift": -0.1, "n": 60} for i in range(4)]
    with pytest.raises(SchemaError):
        validate_items(data, known_comp_ids={"c_00"})


def test_validate_items_rejects_missing_icon():
    data = _valid_items()
    del data["c_00"]["u1"][0]["icon"]
    with pytest.raises(SchemaError):
        validate_items(data, known_comp_ids={"c_00"})


def test_enrich_items_with_icons_joins_by_id():
    items = {
        "_metadata": {"known_limitation": "x", "coverage": {}},
        "c_00": {"u1": [{"id": "i1", "name": "Item", "lift": -0.3, "n": 60}]},
    }
    static_items = {"i1": {"name": "Item", "type": "completed", "icon": "https://example.com/i1.png"}}
    enriched = enrich_items_with_icons(items, static_items)
    assert enriched["c_00"]["u1"][0]["icon"] == "https://example.com/i1.png"
    # orijinal alanlar korunuyor, girdi mutasyona uğramıyor
    assert enriched["c_00"]["u1"][0]["lift"] == -0.3
    assert "icon" not in items["c_00"]["u1"][0]
    assert enriched["_metadata"] == items["_metadata"]


def test_enrich_items_with_icons_raises_on_unknown_item_id():
    items = {
        "_metadata": {"known_limitation": "x", "coverage": {}},
        "c_00": {"u1": [{"id": "i_ghost", "name": "Item", "lift": -0.3, "n": 60}]},
    }
    with pytest.raises(SchemaError):
        enrich_items_with_icons(items, static_items={})


# ---------------------------------------------------------------------------
# Self-test generation
# ---------------------------------------------------------------------------


def test_derive_trait_tiers_counts_unique_units_not_star_level():
    units_map = {"u1": {"traits": ["t1"]}, "u2": {"traits": ["t1"]}}
    traits_map = {"t1": {"breakpoints": [2, 4]}}
    raw_units = {"u1": {"tier": 3}, "u2": {"tier": 1}}  # star level must not affect the count
    assert derive_trait_tiers_from_units(raw_units, units_map, traits_map) == {"t1": 1}


def test_derive_trait_tiers_reaches_second_breakpoint():
    units_map = {f"u{i}": {"traits": ["t1"]} for i in range(4)}
    traits_map = {"t1": {"breakpoints": [2, 4]}}
    raw_units = {f"u{i}": {"tier": 1} for i in range(4)}
    assert derive_trait_tiers_from_units(raw_units, units_map, traits_map) == {"t1": 2}


def test_derive_trait_tiers_omits_trait_below_first_breakpoint():
    units_map = {"u1": {"traits": ["t1"]}}
    traits_map = {"t1": {"breakpoints": [2, 4]}}
    raw_units = {"u1": {"tier": 1}}
    assert derive_trait_tiers_from_units(raw_units, units_map, traits_map) == {}


def test_nearest_comp_picks_smallest_cosine_distance():
    vector = np.array([1.0, 0.0])
    centroids = {"c_00": np.array([1.0, 0.0]), "c_01": np.array([0.0, 1.0])}
    comp_id, distance = nearest_comp(vector, centroids)
    assert comp_id == "c_00"
    assert distance == pytest.approx(0.0, abs=1e-9)


def _row(match_id, puuid, raw_units, raw_traits, early_death=False):
    return {
        "match_id": match_id,
        "puuid": puuid,
        "early_death": early_death,
        "raw_units": raw_units,
        "raw_traits": raw_traits,
    }


UNIT_ORDER = ["u1", "u2"]
TRAIT_ORDER = ["t1"]
UNITS_MAP = {"u1": {"traits": ["t1"]}, "u2": {"traits": ["t1"]}}
TRAITS_MAP = {"t1": {"breakpoints": [2]}}


def _vectors_for(rows):
    from src.features import build_vectors

    return build_vectors(rows, UNIT_ORDER, TRAIT_ORDER)


def test_build_selftest_cases_excludes_boards_with_confounded_trait_count():
    # Only u1 is natively on the board (count=1, below breakpoint [2], so
    # pure unit-counting derives no active tier at all) but raw_traits
    # claims t1 is at tier 1 anyway — exactly the emblem confound SPEC.md
    # section 15 describes (Riot's tier_current already counts the emblem's
    # virtual member). Must be excluded, not shipped as a broken fixture.
    rows = [_row("m1", "p1", {"u1": {"tier": 2}}, {"t1": 1})]
    vectors = _vectors_for(rows)
    labels = np.array([0])
    centroids = {"c_00": vectors[0]}
    cases = build_selftest_cases(rows, vectors, labels, UNIT_ORDER, TRAIT_ORDER, UNITS_MAP, TRAITS_MAP, centroids, 0.35)
    assert cases == []


def test_build_selftest_cases_includes_clean_board():
    rows = [_row("m1", "p1", {"u1": {"tier": 2}, "u2": {"tier": 2}}, {"t1": 1})]
    vectors = _vectors_for(rows)
    labels = np.array([0])
    centroids = {"c_00": vectors[0]}
    cases = build_selftest_cases(rows, vectors, labels, UNIT_ORDER, TRAIT_ORDER, UNITS_MAP, TRAITS_MAP, centroids, 0.35)
    assert len(cases) == 1
    assert cases[0]["match_id"] == "m1"
    assert cases[0]["matched_comp"] == "c_00"
    assert cases[0]["input_units"] == [{"id": "u1", "star": 2}, {"id": "u2", "star": 2}]


def test_build_selftest_cases_picks_lowest_sort_key_per_label_deterministically():
    rows = [
        _row("m2", "pB", {"u1": {"tier": 1}}, {}),
        _row("m1", "pA", {"u1": {"tier": 2}}, {}),
    ]
    vectors = _vectors_for(rows)
    labels = np.array([0, 0])
    centroids = {"c_00": vectors[0]}
    cases = build_selftest_cases(rows, vectors, labels, UNIT_ORDER, TRAIT_ORDER, UNITS_MAP, TRAITS_MAP, centroids, 0.35)
    assert len(cases) == 1
    assert cases[0]["match_id"] == "m1"  # ("m1", "pA") sorts before ("m2", "pB")


def test_build_selftest_cases_flags_off_meta_group():
    rows = [_row("m1", "p1", {"u1": {"tier": 1}}, {})]  # far from the only centroid
    vectors = _vectors_for(rows)
    labels = np.array([-2])
    centroids = {"c_00": np.array([0.0, 1.0, 0.0])}
    cases = build_selftest_cases(rows, vectors, labels, UNIT_ORDER, TRAIT_ORDER, UNITS_MAP, TRAITS_MAP, centroids, 0.05)
    assert len(cases) == 1
    assert cases[0]["id"] == "off_meta"
    assert cases[0]["matched_comp"] is None


def test_build_selftest_cases_raises_if_recomputed_vector_disagrees_with_stored_one():
    rows = [_row("m1", "p1", {"u1": {"tier": 2}, "u2": {"tier": 2}}, {"t1": 1})]
    vectors = _vectors_for(rows)
    tampered = vectors.copy()
    tampered[0][0] = 999.0  # simulate features_vectors.npy drifting from features.py's logic
    centroids = {"c_00": vectors[0]}
    labels = np.array([0])
    with pytest.raises(SchemaError):
        build_selftest_cases(rows, tampered, labels, UNIT_ORDER, TRAIT_ORDER, UNITS_MAP, TRAITS_MAP, centroids, 0.35)
