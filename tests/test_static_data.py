import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.static_data import _classify_item_type, _emblem_trait_id, extract_traits


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
    assert _emblem_trait_id("Blackthorn Emblem", trait_name_to_id, " Emblem") == "DA_18_Blackthorn"
    assert _emblem_trait_id("Ravager Emblem", trait_name_to_id, " Emblem") == "DA_18_Slayer"


def test_emblem_trait_id_returns_none_when_no_matching_trait():
    trait_name_to_id = {"Blackthorn": "DA_18_Blackthorn"}
    assert _emblem_trait_id("Phantom Emblem", trait_name_to_id, " Emblem") is None


def test_emblem_trait_id_works_with_a_non_english_suffix():
    # Regression: the suffix used to be hardcoded to " Emblem" — Turkish
    # emblems follow "<Trait Türkçe Adı> Amblemi" instead (verified against
    # a tr_tr CommunityDragon dump). The matching logic
    # itself is locale-agnostic; only the suffix string needs to vary.
    trait_name_to_id = {"Karadiken": "DA_18_Blackthorn"}
    assert _emblem_trait_id("Karadiken Amblemi", trait_name_to_id, " Amblemi") == "DA_18_Blackthorn"


def test_extract_traits_keeps_sorted_breakpoints():
    set_entry = {
        "traits": [
            {
                "apiName": "DA_18_Brawler",
                "name": "Brawler",
                "icon": "ASSETS/foo.tex",
                "effects": [{"minUnits": 4}, {"minUnits": 2}, {"minUnits": 6}],
            }
        ]
    }
    traits = extract_traits(set_entry)
    assert traits["DA_18_Brawler"]["breakpoints"] == [2, 4, 6]


def test_extract_traits_drops_trait_with_only_null_min_units():
    # Regression: DA_18_Eclipse is a combat-encounter hazard ("kills the
    # lowest Health enemy"), not a stackable champion origin/class — its one
    # effect has minUnits: null in the raw cdragon dump. A trait with no
    # int-minUnits effect left has no breakpoint a board could ever satisfy
    # and must be dropped, the same way non-champion units are dropped.
    set_entry = {
        "traits": [
            {
                "apiName": "DA_18_Eclipse",
                "name": "Eclipse",
                "icon": "ASSETS/eclipse.tex",
                "effects": [{"minUnits": None}],
            },
            {
                "apiName": "DA_18_Brawler",
                "name": "Brawler",
                "icon": "ASSETS/brawler.tex",
                "effects": [{"minUnits": 2}],
            },
        ]
    }
    traits = extract_traits(set_entry)
    assert "DA_18_Eclipse" not in traits
    assert "DA_18_Brawler" in traits


def test_extract_traits_drops_null_effect_but_keeps_real_ones_if_mixed():
    set_entry = {
        "traits": [
            {
                "apiName": "DA_18_Mixed",
                "name": "Mixed",
                "icon": "ASSETS/mixed.tex",
                "effects": [{"minUnits": None}, {"minUnits": 3}],
            },
        ]
    }
    traits = extract_traits(set_entry)
    assert traits["DA_18_Mixed"]["breakpoints"] == [3]


def test_extract_traits_dedupes_repeated_min_units():
    # Regression: DA_18_Rival's raw effects are minUnits=[1, 1, 2] (two
    # effects fire at the same 1-unit threshold, e.g. a style variant on top
    # of the stat effect). A plain sorted() kept the duplicate, producing
    # breakpoints=[1, 1, 2] — a board with 1 native unit then computed as
    # clearing two breakpoints (tier 2) instead of one, disagreeing with
    # Riot's real tier_current. The fix is structural (dedupe every trait's
    # minUnits via set()), so this must hold for any trait, not just Rival —
    # asserted here with a generic apiName to keep it that way.
    set_entry = {
        "traits": [
            {
                "apiName": "DA_18_SomeTrait",
                "name": "Some Trait",
                "icon": "ASSETS/some.tex",
                "effects": [{"minUnits": 1}, {"minUnits": 1}, {"minUnits": 2}],
            },
        ]
    }
    traits = extract_traits(set_entry)
    assert traits["DA_18_SomeTrait"]["breakpoints"] == [1, 2]
