import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.static_data import _classify_item_type, _emblem_trait_id


def test_classify_item_type_component():
    item = {"apiName": "DA_Component_BFSword", "composition": []}
    assert _classify_item_type(item) == "component"


def test_classify_item_type_emblem():
    item = {"apiName": "DA_18_EmblemBlackthorn", "composition": []}
    assert _classify_item_type(item) == "emblem"


def test_classify_item_type_completed_has_composition():
    item = {"apiName": "DA_BrambleVest", "composition": ["DA_Component_ChainVest", "DA_Component_ChainVest"]}
    assert _classify_item_type(item) == "completed"


def test_classify_item_type_other_is_the_fallback():
    # Renamed from "artifact" — verified false by sampling 30 real items
    # from this bucket (Radiant upgrades, "Artifact_"-prefixed items, and a
    # large set of support/encounter items with no single real category).
    item = {"apiName": "DA_18_AfterShock", "composition": []}
    assert _classify_item_type(item) == "other"


def test_classify_item_type_emblem_checked_before_completed():
    # Spatula/Frying Pan combos are emblem-granting components — their
    # pairings with normal components must resolve to "emblem", not
    # "completed", even though `composition` is non-empty (verified: this
    # is exactly why completed=39 instead of a naively-expected 45/55).
    item = {"apiName": "DA_18_EmblemBrawler", "composition": ["DA_Component_FryingPan", "DA_Component_GiantsBelt"]}
    assert _classify_item_type(item) == "emblem"


def test_emblem_trait_id_matches_despite_case_difference():
    # Regression: trait_name_to_id preserves cdragon's original casing
    # ("Blackthorn"), but the emblem's own display name candidate is
    # lowercased for matching ("blackthorn") — a naive .get() without
    # normalizing both sides resolves 0/23 real emblems instead of 21/23.
    trait_name_to_id = {"Blackthorn": "DA_18_Blackthorn", "Ravager": "DA_18_Slayer"}
    assert _emblem_trait_id("Blackthorn Emblem", trait_name_to_id) == "DA_18_Blackthorn"
    assert _emblem_trait_id("Ravager Emblem", trait_name_to_id) == "DA_18_Slayer"


def test_emblem_trait_id_returns_none_when_no_matching_trait():
    trait_name_to_id = {"Blackthorn": "DA_18_Blackthorn"}
    assert _emblem_trait_id("Phantom Emblem", trait_name_to_id) is None
