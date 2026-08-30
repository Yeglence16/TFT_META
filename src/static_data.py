"""Static data from CommunityDragon (SPEC.md section 4).

Source: https://raw.communitydragon.org/latest/cdragon/tft/en_us.json
(~24MB). Cached to data/static/cdragon_tft.json (gitignored). Filtered to
config.yaml's set_number — the dump covers every set at once.

**Champions vs. PvE/global entities.** The dump's per-set `champions` list
mixes real playable champions with generic creatures that share the same
list (Golem, Krug, Training Dummy, item anvils, Mercenary Chest, ...). Real
champions are distinguished by having a non-empty `traits` list; the
PvE/global entries all have `traits: []`. Verified for Set 18: 91 raw
entries -> 74 with a non-empty traits list (17 dropped — see the log line
this module prints). Note some monster-*looking* names DO stay (e.g.
"DA_18_Sentry"/"Pebbles" and "DA_Sentinel18"/"Sentinel" both carry real
traits ['Riftbeast', ...] and are genuine Set 18 champions, not PvE fodder —
don't filter by name pattern, only by the traits-list check). This was
independently confirmed correct.

**74 vs. the real 65-champion roster — resolved.** Confirmed via multiple
outside sources (including live game files) that Set 18 has 65 champions.
The 9-unit gap is Lux: she has 10 records total (`DA_Lux18_Base` plus 9
origin-variant records like `DA_18_Lux_Coven`, `DA_Lux18_Blackthorn`, ...,
one per origin her "Avatar" trait can copy). `_group_champion_variants()`
collapses these generically — by display-name pattern ("Lux (Coven)" ->
base "Lux"), not by hardcoding "Lux" — down to the one exact-name match as
canonical; the 9 variant apiNames are recorded in unit_aliases.json
(alias apiName -> canonical apiName) rather than dropped, since a real
match's `character_id` could be any of the 10 depending on which origin Lux
copied that game. 65 canonical champions after collapsing = matches.

**TFT18_Gromp — checked, exclusion was correct, no bug.** This id appears 6
times in the match sample and was excluded as "not a champion". Verified:
`TFT18_Gromp` does not exist anywhere in the cdragon dump — not in Set 18,
not in any other set's champion list, not in the items array. The actual
Set 18 champion "Gromp" is `DA_Gromp18_AP` (cost 2, Riftbeast/Adaptor) and
was never excluded — it appears correctly under its own id 1066 times in
the sample. `TFT18_Gromp` matches the exact naming convention of the
generic PvE jungle-camp entities also seen in this dump (`TFT_Krug`,
`TFT9_SLIME_Crab`, `TFT6_MercenaryChest`, ...) — almost certainly the actual
jungle-camp creature "Gromp" fought in PvE rounds, a different in-game
entity from the champion that happens to share the name. Not in cdragon
because it isn't player-facing content.

**Augment tier — SPEC.md assumed a `rarity` field exists. It doesn't, in
this dump.** Every key on every Set 18 augment record was dumped and
checked: no rarity, no tier, nothing rarity-shaped at all. Per SPEC.md's
explicit fallback instruction ("eşleme tutmuyorsa alternatif bir türetme
yolu bul"), tier is derived from the augment's `icon` asset filename
instead, which reliably ends in one of:
    missing-t1/2/3.tex          (placeholder art; digit = tier)
    ...-i/-ii/-iii.tex or ..._i/_ii/_iii.tex     (roman numeral suffix)
    ...<name><1/2/3>.tex        (bare digit, no separator)
1=silver, 2=gold, 3=prismatic. This resolves 595/596 of Set 18's augments;
one holdout with no separator before its roman numeral ("tiniest-titaniii")
is caught by a final bare-suffix fallback. The augment's *own apiName* is
NOT a reliable tier signal — e.g. "DA_18_LuxAugmentII" (looks like tier 2)
actually resolves to prismatic (tier 3) by icon; only the icon was trusted.

**Augment pool — finalized at 254 (596 -> DA_-prefix filter).** cdragon's
raw set-18 augment list is 596 entries; ~342 carry a different-set prefix
(TFT6_/7_/8_/9_/10_/11_/16_) — legacy augment-portal picks bundled into Set
18's pool, not Set 18's own content. Filtering to `apiName.startswith("DA_")`
gives 254, confirmed against the real count (253, off by one — close enough
to treat as correct) with a tier split (silver/gold/prismatic = 68/118/68)
close to the confirmed real split (70/114/69).

**Item type (SPEC.md section 15) — same gap as augments, no `rarity`-
equivalent field.** Classified structurally: `component` = apiName starts
with `DA_Component_` (**10** components this set, not the classic 9 — Set
18 adds `DA_Component_FryingPan` as a second "spatula-like" component, see
below); `emblem` = "emblem" in apiName; `completed` = non-empty
`composition`; everything else -> **`other`** (renamed from an earlier,
wrong `artifact` label — verified false by sampling 30 items: it's a mix of
Radiant-upgrade items, "Artifact_"-prefixed items, and a large set of
support/encounter/mechanic items like "Backrow Star", "Blood Money",
"Journeyman" with no single real category; do not re-derive a confident
sub-classification from this alone). Same `DA_`-prefix filter as augments:
773 raw -> 502 (the rest are cross-set legacy items, same pattern as
champions/augments).

**completed=39 looked wrong, verified correct.** Naive expectation:
9 components -> C(9,2)+9 = 45 combos. Actual: Set 18 has 10 components, and
*two* of them (`Spatula` and the new `FryingPan`) are emblem-granting —
each pairs with the other 8 normal components to produce an emblem (8+8=16
combos correctly classified `emblem`, checked before the composition check,
which is correct — an emblem shouldn't also count as completed). The
remaining 3 combos among {Spatula, FryingPan} themselves (Spatula+Spatula,
FryingPan+FryingPan, Spatula+FryingPan) resolve to real completed items
(Tactician's Crown/Shield/Cape). 55 total composition-pairs - 16 emblems =
39 completed. Exact match — not a bug.

**Emblem -> trait mapping.** No structured field links an emblem to the
trait it grants (`associatedTraits` is empty on every emblem record).
Derived by stripping " Emblem" from the item's own display name and
matching against traits.json's display names — resolves 21/23 Set 18
emblems. "Phantom Emblem" (2 items) is the exception, **root-caused**: a
trait literally named "Phantom" exists in cdragon, but registered under
**Set 17** (`TFTEvent5YR_Phantom`, an anniversary-event trait) — Set 18
genuinely has no such trait among its 35 (36 before extract_traits()
dropped the non-stackable `DA_18_Eclipse` — see that function's docstring;
unrelated to this Phantom finding either way, Set 17's Phantom was never
in Set 18's raw trait list to begin with). The item is cross-set content
bundled into Set 18's item pool via a `DA_`-prefixed apiName (unlike the
champion/augment contamination, which used an obviously different prefix,
so the `DA_` filter alone doesn't catch this one). traits.json's list
otherwise passes through `set_entry["traits"]` unfiltered (no DA_/other
filter, only the minUnits-based non-stackable check) — Phantom is left
unmapped rather than force-matched. Written to `unit_aliases.json`'s
sibling, `emblem_traits.json` — **not currently consumed by features.py**:
verified against real match data that Riot's own `participant.traits`
already includes an emblem-holder's contribution server-side in ~95% of
cases (see items.py's docstring), so adding it again in features.py would
double-count. Kept as static data for its own sake, not wired into a fix.

Outputs (data/static/):
  cdragon_tft.json     the raw cached dump (all sets)
  units.json           {character_id: {name, cost, traits, icon}} — 65 canonical
  unit_aliases.json    {variant_character_id: canonical_character_id} — e.g.
                        Lux's 9 origin variants, so match data using any of
                        them can be folded into the one canonical unit
  traits.json          {trait_id: {name, breakpoints, icon}}
  items.json           {item_id: {name, type, icon}} — 502, DA_-prefixed only
  emblem_traits.json   {emblem_item_id: trait_id}
  augments.json        {augment_id: {name, tier, icon}} — 254, DA_-prefixed only

Icon URLs (SPEC-WEB.md section 4): lowercase the whole cdragon asset path,
swap the extension to .png, prepend the CommunityDragon game-asset root.
Verified live (not assumed) — 4 constructed URLs (a champion, a trait, a
normal augment icon, and a "missing-tN" placeholder augment icon) all
returned 200.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import DATA_STATIC, PROJECT_ROOT, ensure_dirs

logger = logging.getLogger(__name__)

CONFIG_PATH = PROJECT_ROOT / "config.yaml"
CDRAGON_URL = "https://raw.communitydragon.org/latest/cdragon/tft/en_us.json"
CDRAGON_CACHE_PATH = DATA_STATIC / "cdragon_tft.json"
UNITS_PATH = DATA_STATIC / "units.json"
UNIT_ALIASES_PATH = DATA_STATIC / "unit_aliases.json"
TRAITS_PATH = DATA_STATIC / "traits.json"
ITEMS_PATH = DATA_STATIC / "items.json"
EMBLEM_TRAITS_PATH = DATA_STATIC / "emblem_traits.json"
AUGMENTS_PATH = DATA_STATIC / "augments.json"

ICON_ROOT = "https://raw.communitydragon.org/latest/game/"


def _icon_url(path: str) -> str:
    lower = path.lower()
    stem = lower.rsplit(".", 1)[0]
    return ICON_ROOT + stem + ".png"


def download_cdragon(force: bool = False) -> dict:
    ensure_dirs()
    if CDRAGON_CACHE_PATH.exists() and not force:
        logger.info("Using cached %s", CDRAGON_CACHE_PATH)
        return json.loads(CDRAGON_CACHE_PATH.read_text(encoding="utf-8"))

    logger.info("Downloading %s", CDRAGON_URL)
    response = httpx.get(CDRAGON_URL, timeout=60.0)
    response.raise_for_status()
    CDRAGON_CACHE_PATH.write_bytes(response.content)
    logger.info("Cached to %s (%d bytes)", CDRAGON_CACHE_PATH, len(response.content))
    return json.loads(response.content)


def _find_set(cdragon: dict, set_number: int) -> dict:
    for entry in cdragon["setData"]:
        if entry["number"] == set_number:
            return entry
    raise SystemExit(f"No setData entry for set_number={set_number} in the cdragon dump")


def extract_traits(set_entry: dict) -> dict[str, dict]:
    """Drops non-stackable "trait" records — same idea as extract_units()
    dropping PvE/global champion entries via the empty-traits-list check.

    **Found via SPEC-WEB.md's web build validator (2026-08-30):**
    `DA_18_Eclipse` has a single effect with `minUnits: null` and a
    description ("After 10 seconds of combat, the Eclipse kills the lowest
    Health enemy") that's a combat-encounter hazard, not a champion
    origin/class you stack by playing units. It's the only one of Set 18's
    37 raw trait records with this shape — checked directly, every other
    trait's every effect has an int `minUnits`. A trait with zero effects
    left after dropping null `minUnits` has no breakpoint a board's unit
    count could ever satisfy, so it isn't real comp-building content and is
    excluded the same way `TFT18_Gromp` is excluded from units.

    **`minUnits` is deduped, not just sorted (fixed 2026-08-30, found via the
    web build validator).** Two effects can share the same `minUnits` — a
    real cdragon shape, not malformed data — because a single breakpoint can
    carry more than one effect record (e.g. a distinct visual `style` on top
    of the stat effect at that same threshold). `DA_18_Rival`'s raw effects
    are `minUnits=[1, 1, 2]`: two effects both fire at 1 unit, one at 2. A
    plain `sorted()` kept the duplicate, so a board with exactly 1 native
    Rival unit computed as clearing *two* breakpoints (tier 2) instead of
    one — verified against real match data, every sampled case showed
    Riot's own `tier_current` reporting 1 there, not 2. It was the only one
    of Set 18's 35 stackable traits with a repeated `minUnits` value —
    checked directly. Deduping is structural (`set()` over every effect's
    `minUnits`, independent of which trait it belongs to), not a
    `DA_18_Rival`-specific carve-out, so it holds for any future set's
    dump without a code change.
    """
    traits = {}
    dropped = []
    for t in set_entry["traits"]:
        breakpoints = sorted({e["minUnits"] for e in t["effects"] if e.get("minUnits") is not None})
        if not breakpoints:
            dropped.append(f"{t['apiName']} ({t['name']})")
            continue
        traits[t["apiName"]] = {
            "name": t["name"],
            "breakpoints": breakpoints,
            "icon": _icon_url(t["icon"]),
        }
    if dropped:
        logger.info(
            "Traits: %d stackable traits, %d non-stackable entr(ies) dropped (no int minUnits on any effect): %s",
            len(traits), len(dropped), "; ".join(dropped),
        )
    return traits


# Some champions have multiple internal records for the same in-game unit —
# e.g. a champion whose origin trait swaps based on board state gets one
# apiName per possible origin. These follow a display-name convention: the
# canonical record is named plainly ("Lux"), variants are named
# "<canonical> (<variant>)" (e.g. "Lux (Solar)", "Lux (Coven)"). Detected
# generically by that name pattern — not by hardcoding which champion has
# this mechanic — so it isn't a "hardcoded unit name" and works for whatever
# champion happens to have it in any set.
_VARIANT_NAME_RE = re.compile(r"^(.*) \(.+\)$")


def _group_champion_variants(champs: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Returns (canonical_champs, alias_to_canonical_api_name)."""
    by_base_name: dict[str, list[dict]] = {}
    for c in champs:
        match = _VARIANT_NAME_RE.match(c["name"])
        base_name = match.group(1) if match else c["name"]
        by_base_name.setdefault(base_name, []).append(c)

    canonical: list[dict] = []
    aliases: dict[str, str] = {}

    for base_name, group in by_base_name.items():
        if len(group) == 1:
            canonical.append(group[0])
            continue

        exact_matches = [c for c in group if c["name"] == base_name]
        if len(exact_matches) != 1:
            logger.warning(
                "Champion variant group '%s' has %d members but %d exact-name matches "
                "(expected exactly 1 canonical) — keeping all %d as separate units: %s",
                base_name, len(group), len(exact_matches), len(group),
                [c["apiName"] for c in group],
            )
            canonical.extend(group)
            continue

        canon = exact_matches[0]
        canonical.append(canon)
        variant_ids = [c["apiName"] for c in group if c["apiName"] != canon["apiName"]]
        for api_name in variant_ids:
            aliases[api_name] = canon["apiName"]
        logger.info(
            "Collapsed %d variant(s) of '%s' into canonical unit %s: %s",
            len(variant_ids), base_name, canon["apiName"], variant_ids,
        )

    return canonical, aliases


def extract_units(set_entry: dict, trait_name_to_id: dict[str, str]) -> tuple[dict[str, dict], dict[str, str]]:
    traited = []
    dropped = []
    for champ in set_entry["champions"]:
        if not champ.get("traits"):
            dropped.append(f"{champ['apiName']} ({champ['name']})")
            continue
        traited.append(champ)
    logger.info(
        "Units: %d champions with non-empty traits, %d PvE/global entries dropped: %s",
        len(traited), len(dropped), "; ".join(dropped),
    )

    canonical_champs, aliases = _group_champion_variants(traited)

    units = {}
    for champ in canonical_champs:
        trait_ids = [trait_name_to_id[t] for t in champ["traits"] if t in trait_name_to_id]
        unresolved = [t for t in champ["traits"] if t not in trait_name_to_id]
        if unresolved:
            logger.warning("Champion %s has trait name(s) not found in set trait list: %s", champ["apiName"], unresolved)
        units[champ["apiName"]] = {
            "name": champ["name"],
            "cost": champ["cost"],
            "traits": trait_ids,
            "icon": _icon_url(champ["squareIcon"]),
        }
    logger.info("Units: %d canonical champions after collapsing variants (%d aliases)", len(units), len(aliases))
    return units, aliases


def _classify_item_type(item: dict) -> str:
    """No `rarity`-equivalent field exists for items either (same gap as
    augments) — structural signals only:
      component  apiName starts with "DA_Component_" — 10 this set (the 9
                 classic TFT components — B.F. Sword, Chain Vest, Giant's
                 Belt, Needlessly Large Rod, Negatron Cloak, Recurve Bow,
                 Sparring Gloves, Spatula, Tear of the Goddess — plus a new
                 one, Frying Pan, confirmed by name)
      emblem     "emblem" in the apiName
      completed  has a non-empty `composition` (built from 2 components) —
                 checked AFTER emblem, deliberately: Spatula and Frying Pan
                 are both emblem-granting components, so their combinations
                 with the other 8 must resolve to `emblem`, not `completed`
                 (verified: 55 total component-pairs = 16 emblem combos +
                 39 completed combos, exactly matching the counts below)
      other      everything else (~430 items) — renamed from an earlier,
                 unverified `artifact` label. That label was checked by
                 sampling 30 of these items and turned out wrong: it's a
                 mix of "Radiant"-upgrade items, "Artifact_"-prefixed items,
                 and a large set of support/encounter/mechanic items
                 (e.g. "Backrow Star", "Blood Money", "Journeyman") with no
                 single real category — don't re-derive a confident
                 sub-classification from structural signals alone.
    """
    if item["apiName"].startswith("DA_Component_"):
        return "component"
    if "emblem" in item["apiName"].lower():
        return "emblem"
    if item["composition"]:
        return "completed"
    return "other"


def _emblem_trait_id(item_display_name: str, trait_name_to_id: dict[str, str]) -> str | None:
    """Emblem items have no structured field pointing at the trait they
    grant (`associatedTraits` is empty on every one) — derived instead by
    stripping " Emblem" from the item's own display name and matching
    against traits.json's display names. Verified: 21/23 Set 18 emblems
    resolve this way; "Phantom Emblem" (2 items) matches none of the 35
    known traits and is left unmapped — root-caused (not just observed):
    a "Phantom" trait exists in cdragon but belongs to Set 17
    (TFTEvent5YR_Phantom, an anniversary-event trait), not Set 18. The item
    is cross-set content, not a gap in Set 18's own (correct, unfiltered)
    trait list."""
    candidate = item_display_name.replace(" Emblem", "").strip().lower()
    lower_lookup = {name.lower(): trait_id for name, trait_id in trait_name_to_id.items()}
    return lower_lookup.get(candidate)


def extract_items(
    cdragon: dict, set_entry: dict, trait_name_to_id: dict[str, str]
) -> tuple[dict[str, dict], dict[str, str]]:
    """Returns (items, emblem_to_trait). Finalized per the augments
    precedent: cdragon's raw set-18 item list is 773 entries, but the same
    "legacy items from other sets" contamination found in champions/augments
    is present here too (TFT_/TFT5_/TFT7_/... prefixes) — filtering to
    apiName.startswith("DA_") gives 502, used here."""
    items_by_name = {it["apiName"]: it for it in cdragon["items"]}
    items: dict[str, dict] = {}
    emblem_to_trait: dict[str, str] = {}
    unresolved_emblems: list[str] = []
    skipped_other_set = 0
    type_counts: dict[str, int] = {}

    for api_name in set_entry["items"]:
        if not api_name.startswith("DA_"):
            skipped_other_set += 1
            continue
        item = items_by_name.get(api_name)
        if item is None:
            continue

        item_type = _classify_item_type(item)
        type_counts[item_type] = type_counts.get(item_type, 0) + 1
        items[api_name] = {"name": item["name"], "type": item_type, "icon": _icon_url(item["icon"])}

        if item_type == "emblem":
            trait_id = _emblem_trait_id(item["name"], trait_name_to_id)
            if trait_id:
                emblem_to_trait[api_name] = trait_id
            else:
                unresolved_emblems.append(api_name)

    logger.info(
        "Items: %d DA_-prefixed kept (%d other-set-prefixed skipped), by type: %s",
        len(items), skipped_other_set, type_counts,
    )
    logger.info(
        "Emblems: %d mapped to a trait, %d unresolved: %s",
        len(emblem_to_trait), len(unresolved_emblems), unresolved_emblems,
    )
    return items, emblem_to_trait


# See module docstring: cdragon's augment records have no rarity/tier field
# at all in this dump. Tier is derived from the icon filename instead.
_TIER_PATTERNS = [
    re.compile(r"missing-t([123])$"),
    re.compile(r"[-_](iii|ii|i)$"),
    re.compile(r"([123])$"),
    re.compile(r"(iii|ii|i)$"),
]
_ROMAN_TO_TIER = {"i": 1, "ii": 2, "iii": 3}
_TIER_NAME = {1: "silver", 2: "gold", 3: "prismatic"}


def _augment_tier(icon_path: str) -> str | None:
    stem = icon_path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    for pattern in _TIER_PATTERNS:
        match = pattern.search(stem)
        if match:
            token = match.group(1)
            tier_num = int(token) if token.isdigit() else _ROMAN_TO_TIER[token]
            return _TIER_NAME[tier_num]
    return None


def extract_augments(cdragon: dict, set_entry: dict) -> dict[str, dict]:
    """Finalized (confirmed against the user's independently-known Set 18
    augment count): cdragon's raw set-18 augment list is 596 entries, but
    ~342 of those carry a different-set prefix (TFT6_/7_/8_/9_/10_/11_/16_) —
    these are "legacy augment portal" picks bundled into Set 18's pool, not
    Set 18's own augments. Filtering to apiName.startswith("DA_") gives 254,
    matching the confirmed real count of 253 closely enough (off by one) to
    treat as correct, and its tier split (silver/gold/prismatic = 68/118/68)
    is very close to the confirmed real split (70/114/69)."""
    items_by_name = {it["apiName"]: it for it in cdragon["items"]}
    augments = {}
    unresolved_tier = []
    skipped_other_set = 0
    for api_name in set_entry["augments"]:
        if not api_name.startswith("DA_"):
            skipped_other_set += 1
            continue
        aug = items_by_name.get(api_name)
        if aug is None:
            continue
        tier = _augment_tier(aug["icon"])
        if tier is None:
            unresolved_tier.append(api_name)
        augments[api_name] = {"name": aug["name"], "tier": tier, "icon": _icon_url(aug["icon"])}

    tier_counts: dict[str | None, int] = {}
    for a in augments.values():
        tier_counts[a["tier"]] = tier_counts.get(a["tier"], 0) + 1
    logger.info(
        "Augments: %d DA_-prefixed kept (%d other-set-prefixed skipped), tier distribution: %s",
        len(augments), skipped_other_set, tier_counts,
    )
    if unresolved_tier:
        logger.warning("Augments with no derivable tier (%d): %s", len(unresolved_tier), unresolved_tier)
    return augments


def run(config: dict) -> None:
    ensure_dirs()
    set_number = config["set_number"]
    cdragon = download_cdragon()
    set_entry = _find_set(cdragon, set_number)

    trait_name_to_id = {t["name"]: t["apiName"] for t in set_entry["traits"]}

    traits = extract_traits(set_entry)
    units, unit_aliases = extract_units(set_entry, trait_name_to_id)
    items, emblem_traits = extract_items(cdragon, set_entry, trait_name_to_id)
    augments = extract_augments(cdragon, set_entry)

    TRAITS_PATH.write_text(json.dumps(traits, indent=2), encoding="utf-8")
    UNITS_PATH.write_text(json.dumps(units, indent=2), encoding="utf-8")
    UNIT_ALIASES_PATH.write_text(json.dumps(unit_aliases, indent=2), encoding="utf-8")
    ITEMS_PATH.write_text(json.dumps(items, indent=2), encoding="utf-8")
    EMBLEM_TRAITS_PATH.write_text(json.dumps(emblem_traits, indent=2), encoding="utf-8")
    AUGMENTS_PATH.write_text(json.dumps(augments, indent=2), encoding="utf-8")
    logger.info(
        "Wrote units.json (%d), unit_aliases.json (%d), traits.json (%d), items.json (%d), "
        "emblem_traits.json (%d), augments.json (%d) to %s",
        len(units), len(unit_aliases), len(traits), len(items), len(emblem_traits), len(augments), DATA_STATIC,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    run(config)


if __name__ == "__main__":
    main()
