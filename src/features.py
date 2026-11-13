"""Board feature vectors from filtered match data (SPEC.md section 8).

Each participant in a match is one training example: an L2-normalized
vector of [unit star levels ... | trait active tiers ...]. Items are
excluded from the vector itself (per spec, v1 scope) — comp identity stays
item-independent, since two players on the same comp routinely carry
different items. Each unit's raw `itemNames` list IS still captured in
`raw_units[unit_id]["items"]` for items.py's separate (cluster, unit, item)
analysis layer (SPEC.md section 15) — that's metadata for a downstream
module, not a change to the vector.

**unit_order / trait_order now come from static_data.py (SPEC.md section
4), not from the match sample.** An earlier version derived them
empirically from whatever character_id/trait strings appeared in the data —
that meant vector dimensionality depended on sample size (a full-scale run
seeing different units would silently produce a different-shaped vector,
invalidating any saved centroids) and let non-champion entities (jungle
monsters, item anvils, ...) leak into the unit axis. static_data.py's
units.json/traits.json (cdragon Set 18, filtered to real champions — see
that module's docstring) are now the fixed, sample-independent vocabulary.

Any `character_id` seen in match data but NOT in the static champion list
is dropped from the vector and logged (count per id), never silently
discarded — SPEC.md is explicit about this. In the initial ~3000-match
sample that caught exactly 2 ids: `DA_18_EliseSpider` (an alternate-form
asset, not its own recruitable champion) and `TFT18_Gromp` (a stray
non-"DA_" id — looks like a PvE jungle monster reference, not a champion).

**Early-elimination filter — flags, no longer drops (survivorship-bias
fix).** SPEC.md says "roughly before stage 4", but Set 18's `last_round` is
a flat round counter that doesn't obviously map to classic stage/round
notation. config.yaml's `features.min_last_round` was picked from the
actual data (see that file's comment) rather than guessed.

Originally, participants below that threshold were dropped
entirely. That's the right call for CLUSTERING (a half-built board can't
define a comp archetype) but wrong for anything computed downstream from
the same filtered set — it's a survivorship bias: early deaths skew hard
toward 7th/8th place, so removing them shifts every cluster's average
placement and top-4 rate optimistic, and the same distortion reaches
matchups.py/traits.py/model.py. Caught because a dummy predicting 4.5 for
everything should have MAE exactly 2.0 for a uniform 1-8 placement
distribution, and it measured 1.81 instead.

Fix: every participant is now kept here, tagged `early_death: bool`
(last_round < min_last_round). clustering.py trains HDBSCAN on the
non-early-death subset only (unchanged behavior there), then separately
assigns the early-death boards to their nearest centroid (same
distance-threshold logic used for HDBSCAN noise points) so every board
downstream has a label, and every statistic downstream is computed over
the full labeled population.

**augments:** the `augments` key does not exist at all on any participant in
the raw data — not `null`, structurally absent. Verified by scanning every
raw match in `data/raw/` (not a subsample): 3005 matches, 23984 participants,
0/23984 have the key. Not Set-18-specific either — the same scan's Set 16
(`queue_id=1210`, 11 matches) and Set 17 (`queue_id=1100`/`1160`, 17 matches)
records are missing it too; all of them share `metadata.data_version == "6"`.
Stored here as `None` via `participant.get("augments")`, which is Python's
missing-key default — not a copy of an explicit API null. This is why
`augments.py` (SPEC.md section 11) is a stub, not a "wait for data" TODO.

Outputs:
  data/processed/features_vectors.npy   float32 (N, D), L2-normalized, rows
                                         aligned 1:1 with features_meta.jsonl.
                                         D is now fixed (65 units + 35
                                         traits = 100 for Set 18 — see
                                         static_data.py's docstring on the
                                         74->65 champion-variant collapse and
                                         extract_traits()'s non-stackable-
                                         trait filter), independent of
                                         sample size.
  data/processed/features_meta.jsonl    per-row: match_id, puuid, placement,
                                         augments, last_round, level,
                                         early_death, plus raw_units/raw_traits
                                         (pre-normalization, for clustering.py's
                                         naming step — see SPEC.md section 9)
  data/processed/meta.json              vector schema, SPEC-WEB.md section 3.1
                                         shape (unit_order, trait_order, ...).
                                         `patch` is intentionally omitted — see
                                         config.yaml for why game_version is
                                         unusable right now.
"""

from __future__ import annotations

import collections
import gzip
import json
import logging
import sys
from datetime import date
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import DATA_PROCESSED, DATA_STATIC, PROJECT_ROOT, ensure_dirs

logger = logging.getLogger(__name__)

CONFIG_PATH = PROJECT_ROOT / "config.yaml"
FILTERED_MATCHES_PATH = DATA_PROCESSED / "matches_filtered.jsonl.gz"
VECTORS_PATH = DATA_PROCESSED / "features_vectors.npy"
META_ROWS_PATH = DATA_PROCESSED / "features_meta.jsonl"
META_JSON_PATH = DATA_PROCESSED / "meta.json"
STATIC_UNITS_PATH = DATA_STATIC / "units.json"
STATIC_TRAITS_PATH = DATA_STATIC / "traits.json"
STATIC_UNIT_ALIASES_PATH = DATA_STATIC / "unit_aliases.json"


def _load_static_vocab() -> tuple[list[str], list[str], dict[str, str]]:
    if not STATIC_UNITS_PATH.exists() or not STATIC_TRAITS_PATH.exists():
        raise SystemExit(f"{STATIC_UNITS_PATH} / {STATIC_TRAITS_PATH} not found — run static_data.py first")
    units = json.loads(STATIC_UNITS_PATH.read_text(encoding="utf-8"))
    traits = json.loads(STATIC_TRAITS_PATH.read_text(encoding="utf-8"))
    aliases = json.loads(STATIC_UNIT_ALIASES_PATH.read_text(encoding="utf-8")) if STATIC_UNIT_ALIASES_PATH.exists() else {}
    return sorted(units.keys()), sorted(traits.keys()), aliases


def _load_participants(
    min_last_round: int, known_units: set[str], known_traits: set[str], unit_aliases: dict[str, str]
) -> tuple[list[dict], int, collections.Counter, collections.Counter]:
    """Reads every participant from every filtered match — ALL of them,
    early deaths included (see module docstring on the survivorship-bias
    fix). Returns (all_participants, early_death_count, unknown_unit_counts,
    unknown_trait_counts).

    unknown_unit_counts / unknown_trait_counts tally character_id / trait
    names seen in the data but absent from the static champion/trait lists —
    these are dropped from the vector, not silently kept."""
    if not FILTERED_MATCHES_PATH.exists():
        raise SystemExit(f"{FILTERED_MATCHES_PATH} not found — run scrape_matches.py first")

    all_participants: list[dict] = []
    early_death_count = 0
    match_count = 0
    unknown_units: collections.Counter = collections.Counter()
    unknown_traits: collections.Counter = collections.Counter()

    with gzip.open(FILTERED_MATCHES_PATH, "rt", encoding="utf-8") as f:
        for line in f:
            match = json.loads(line)
            match_count += 1
            # Verified against real data: the field is metadata.match_id
            # (snake_case), not matchId.
            match_id = match["metadata"]["match_id"]

            for participant in match["info"]["participants"]:
                last_round = participant["last_round"]
                early_death = last_round < min_last_round
                if early_death:
                    early_death_count += 1

                raw_units = {}
                for unit in participant["units"]:
                    char_id = unit_aliases.get(unit["character_id"], unit["character_id"])
                    if char_id not in known_units:
                        unknown_units[char_id] += 1
                        continue
                    item_names = unit.get("itemNames", [])
                    raw_units[char_id] = {
                        "tier": unit["tier"],
                        "item_count": len(item_names),
                        "items": item_names,
                    }

                raw_traits = {}
                for trait in participant["traits"]:
                    if trait["tier_current"] <= 0:
                        continue
                    if trait["name"] not in known_traits:
                        unknown_traits[trait["name"]] += 1
                        continue
                    raw_traits[trait["name"]] = trait["tier_current"]

                all_participants.append(
                    {
                        "match_id": match_id,
                        "puuid": participant["puuid"],
                        "placement": participant["placement"],
                        "augments": participant.get("augments"),
                        "last_round": last_round,
                        "level": participant["level"],
                        "early_death": early_death,
                        "raw_units": raw_units,
                        "raw_traits": raw_traits,
                    }
                )

    return all_participants, early_death_count, unknown_units, unknown_traits


# Riot's generic ranked ladder order (not TFT set content, so this isn't a
# "hardcoded unit/trait/item/augment" the spec bans) — used only to find the
# lowest tier config.yaml's ladder block actually pulls from, for meta.json's
# elo_range.
_TIER_RANK = [
    "iron", "bronze", "silver", "gold", "platinum", "emerald",
    "diamond", "master", "grandmaster", "challenger",
]


def _lowest_ladder_tier(ladder_config: dict) -> str:
    tiers = [t.lower() for t in ladder_config.get("paginated_tiers", []) + ladder_config.get("high_tiers", [])]
    known = [t for t in tiers if t in _TIER_RANK]
    if not known:
        return tiers[0] if tiers else "unknown"
    return min(known, key=_TIER_RANK.index)


def build_vectors(participants: list[dict], unit_order: list[str], trait_order: list[str]) -> np.ndarray:
    unit_index = {u: i for i, u in enumerate(unit_order)}
    trait_index = {t: i for i, t in enumerate(trait_order)}
    dim = len(unit_order) + len(trait_order)

    vectors = np.zeros((len(participants), dim), dtype=np.float32)
    for row, p in enumerate(participants):
        for unit_id, info in p["raw_units"].items():
            vectors[row, unit_index[unit_id]] = info["tier"]
        for trait_name, tier_current in p["raw_traits"].items():
            vectors[row, len(unit_order) + trait_index[trait_name]] = tier_current

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0  # avoid div-by-zero for an all-zero board (shouldn't happen post-filter, but don't crash)
    return vectors / norms


def run(config: dict) -> None:
    ensure_dirs()
    min_last_round = config["features"]["min_last_round"]

    unit_order, trait_order, unit_aliases = _load_static_vocab()
    logger.info(
        "Vocabulary loaded from static_data.py: %d units, %d traits (vector dim = %d, fixed regardless of sample), %d unit aliases",
        len(unit_order), len(trait_order), len(unit_order) + len(trait_order), len(unit_aliases),
    )
    known_units, known_traits = set(unit_order), set(trait_order)

    participants, early_death_count, unknown_units, unknown_traits = _load_participants(
        min_last_round, known_units, known_traits, unit_aliases
    )
    logger.info(
        "Early-death flag (last_round < %d): %d/%d participants flagged — kept in the dataset "
        "(model.py trains on all of it), excluded only from clustering.py's HDBSCAN training",
        min_last_round, early_death_count, len(participants),
    )
    if unknown_units:
        logger.warning(
            "Non-champion character_id(s) seen in match data, excluded from the vector "
            "(%d occurrences across %d distinct ids): %s",
            sum(unknown_units.values()), len(unknown_units), dict(unknown_units),
        )
    if unknown_traits:
        logger.warning(
            "Trait name(s) seen in match data but not in the static trait list, excluded "
            "(%d occurrences across %d distinct names): %s",
            sum(unknown_traits.values()), len(unknown_traits), dict(unknown_traits),
        )
    if not participants:
        raise SystemExit("No participants found in the filtered match data — nothing to build features from")

    vectors = build_vectors(participants, unit_order, trait_order)
    np.save(VECTORS_PATH, vectors)
    logger.info("Wrote %s (%s)", VECTORS_PATH, vectors.shape)

    with open(META_ROWS_PATH, "w", encoding="utf-8") as f:
        for p in participants:
            f.write(json.dumps(p) + "\n")
    logger.info("Wrote %s", META_ROWS_PATH)

    match_ids = {p["match_id"] for p in participants}
    lowest_tier = _lowest_ladder_tier(config["ladder"])
    meta = {}
    if META_JSON_PATH.exists():
        meta = json.loads(META_JSON_PATH.read_text(encoding="utf-8"))
    meta.update(
        {
            "set_number": config["set_number"],
            "patch": None,  # unusable right now — see config.yaml comment on queue_id/patch_prefix
            "regions": config["regions"],
            "elo_range": f"{lowest_tier}+ (dev sample, ranked reset in progress)",
            "match_count": len(match_ids),
            "board_count": len(participants),
            "collected_at": date.today().isoformat(),
            "unit_order": unit_order,
            "trait_order": trait_order,
            "vector_includes_traits": True,
            "normalization": "l2",
        }
    )
    META_JSON_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    logger.info("Wrote %s", META_JSON_PATH)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    run(config)


if __name__ == "__main__":
    main()
