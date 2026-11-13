"""Assembles web/data/ from the pipeline's outputs (SPEC-WEB.md section 2).

The web spec is explicit that web/data/ is written by the pipeline and never
hand-edited, and that a schema mismatch between pipeline and browser "arayüz
hata vermez, sessizce yanlış sonuç gösterir" (SPEC-WEB.md section 3). This
module is the single place that copies the required files out of
data/processed/ and data/static/ into web/data/, validating each one against
SPEC-WEB.md section 3's contract on the way — a shape/type mismatch raises
instead of shipping a file the browser would silently misread.

**Two schema gaps found against real pipeline output, not assumed:**

1. matchups.json (SPEC-WEB.md 3.5) documents every cell as
   `{"rate", "n", "ci"}`. Real cells from matchups.py also include
   `{"n", "insufficient_data": true}` for n<100 pairs (matchups.py keeps
   the pair visible instead of dropping it, per SPEC.md section 10 / the
   "n olmadan hiçbir oran gösterilmeyecek" rule) — SPEC-WEB.md's schema
   never names this field. validate_matchups() accepts both shapes since
   this is real, correct pipeline output; SPEC-WEB.md section 3.5 should
   probably document `insufficient_data` explicitly, flagged separately
   from this task.
2. comps.json's `off_meta` entry (SPEC-WEB.md 3.4) carries only
   `n_full`/`n_total`/`avg_placement`/`top4_rate` — no `name`/`centroid`/
   `core_units`/`common_traits`, unlike every `c_NN` entry. SPEC-WEB.md
   documents comps.json without calling out this exception (items.json's
   `_metadata` gets an explicit "not a normal comp key" callout in 3.8;
   off_meta gets none in 3.4). validate_comps() treats it as a recognized
   special key, same idea as items.json's `_metadata`.

Outputs (web/data/):
  meta.json, comps.json, matchups.json, comp_traits.json
      copied from data/processed/, re-serialized (indent=2).
  units.json, traits.json
      copied from data/static/, re-serialized (indent=2).
  items.json
      data/processed/items.json's {id, name, lift, n} entries, each joined
      against data/static/items.json (which is not itself copied to
      web/data/) to add an `icon` field — SPEC-WEB.md section 3.8 and
      8's item-recommendation UI needs one, see enrich_items_with_icons().
  _selftest.json
      generated fresh each run — see build_selftest_cases()'s docstring.

Idempotent: every step here is a deterministic function of data/processed/ +
data/static/'s current content (plain copy-with-reserialize for six files,
a sorted-by-(match_id, puuid) deterministic pick for _selftest.json) — running
twice with unchanged source data produces byte-identical output.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features import build_vectors
from src.paths import DATA_PROCESSED, DATA_STATIC, WEB_DATA_DIR

logger = logging.getLogger(__name__)

FEATURES_META_PATH = DATA_PROCESSED / "features_meta.jsonl"
FEATURES_VECTORS_PATH = DATA_PROCESSED / "features_vectors.npy"
CLUSTER_LABELS_PATH = DATA_PROCESSED / "cluster_labels.npy"

OFF_META_CODE = -2  # matches clustering.py's OFF_META_CODE
OFF_META = "off_meta"

MIN_SELFTEST_CASES = 20


class SchemaError(ValueError):
    """A data/processed or data/static file doesn't match SPEC-WEB.md section 3."""


# ---------------------------------------------------------------------------
# Validators — one per SPEC-WEB.md section 3.x. Each raises SchemaError with
# a message naming the file and the exact field that's wrong; none of them
# mutate their input.
# ---------------------------------------------------------------------------


def validate_meta(data: dict) -> None:
    if not isinstance(data, dict):
        raise SchemaError("meta.json: root is not an object")

    if not isinstance(data.get("set_number"), int):
        raise SchemaError("meta.json: set_number missing or not an int")
    if not isinstance(data.get("regions"), list) or not data["regions"]:
        raise SchemaError("meta.json: regions missing, empty, or not a list")
    if not all(isinstance(r, str) for r in data["regions"]):
        raise SchemaError("meta.json: regions must all be strings")
    for key in ("match_count", "board_count"):
        if not isinstance(data.get(key), int):
            raise SchemaError(f"meta.json: {key} missing or not an int")
    if not isinstance(data.get("elo_range"), str) or not data["elo_range"]:
        raise SchemaError("meta.json: elo_range missing or not a string")
    if not isinstance(data.get("collected_at"), str) or not data["collected_at"]:
        raise SchemaError("meta.json: collected_at missing or not a string")
    # patch is documented as a string (section 3.1's example) but features.py
    # writes null when game_version is unusable (SPEC.md section 7) — both
    # are legitimate, only reject anything else.
    if "patch" not in data or not (data["patch"] is None or isinstance(data["patch"], str)):
        raise SchemaError("meta.json: patch must be present and be a string or null")

    unit_order = data.get("unit_order")
    trait_order = data.get("trait_order")
    if not isinstance(unit_order, list) or not unit_order or not all(isinstance(u, str) for u in unit_order):
        raise SchemaError("meta.json: unit_order missing, empty, or not a list of strings")
    if not isinstance(trait_order, list) or not trait_order or not all(isinstance(t, str) for t in trait_order):
        raise SchemaError("meta.json: trait_order missing, empty, or not a list of strings")
    if len(set(unit_order)) != len(unit_order):
        raise SchemaError("meta.json: unit_order has duplicate ids")
    if len(set(trait_order)) != len(trait_order):
        raise SchemaError("meta.json: trait_order has duplicate ids")

    if not isinstance(data.get("vector_includes_traits"), bool):
        raise SchemaError("meta.json: vector_includes_traits missing or not a bool")
    if not isinstance(data.get("normalization"), str) or not data["normalization"]:
        raise SchemaError("meta.json: normalization missing or not a string")
    if not isinstance(data.get("match_threshold"), (int, float)) or isinstance(data.get("match_threshold"), bool):
        raise SchemaError("meta.json: match_threshold missing or not a number")


def validate_units(data: dict) -> None:
    if not isinstance(data, dict) or not data:
        raise SchemaError("units.json: root is not a non-empty object")
    for unit_id, info in data.items():
        if not isinstance(info, dict):
            raise SchemaError(f"units.json: {unit_id} is not an object")
        if not isinstance(info.get("name"), str) or not info["name"]:
            raise SchemaError(f"units.json: {unit_id}.name missing or not a string")
        if not isinstance(info.get("cost"), int) or not (1 <= info["cost"] <= 5):
            raise SchemaError(f"units.json: {unit_id}.cost missing or not an int in [1, 5]")
        if not isinstance(info.get("traits"), list) or not all(isinstance(t, str) for t in info["traits"]):
            raise SchemaError(f"units.json: {unit_id}.traits missing or not a list of strings")
        if not isinstance(info.get("icon"), str) or not info["icon"].startswith("http"):
            raise SchemaError(f"units.json: {unit_id}.icon missing or not an http(s) URL")


def validate_traits(data: dict) -> None:
    if not isinstance(data, dict) or not data:
        raise SchemaError("traits.json: root is not a non-empty object")
    for trait_id, info in data.items():
        if not isinstance(info, dict):
            raise SchemaError(f"traits.json: {trait_id} is not an object")
        if not isinstance(info.get("name"), str) or not info["name"]:
            raise SchemaError(f"traits.json: {trait_id}.name missing or not a string")
        breakpoints = info.get("breakpoints")
        if not isinstance(breakpoints, list) or not breakpoints or not all(isinstance(b, int) for b in breakpoints):
            raise SchemaError(f"traits.json: {trait_id}.breakpoints missing, empty, or not a list of ints")
        if breakpoints != sorted(breakpoints):
            raise SchemaError(f"traits.json: {trait_id}.breakpoints not ascending")
        if not isinstance(info.get("icon"), str) or not info["icon"].startswith("http"):
            raise SchemaError(f"traits.json: {trait_id}.icon missing or not an http(s) URL")


def validate_avg_placement_tiers(metadata: dict) -> None:
    """comps.json's `_metadata.avg_placement_tiers` (SPEC-WEB.md section 8;
    stdev-based S/A/B/C/D bands) — meta-tier band boundaries, recomputed every
    run by clustering.py, never hardcoded downstream."""
    tiers = metadata.get("avg_placement_tiers")
    if not isinstance(tiers, dict):
        raise SchemaError("comps.json: _metadata.avg_placement_tiers missing or not an object")
    for key in ("mean", "stdev"):
        if not isinstance(tiers.get(key), (int, float)) or isinstance(tiers.get(key), bool):
            raise SchemaError(f"comps.json: _metadata.avg_placement_tiers.{key} missing or not a number")
    if tiers["stdev"] < 0:
        raise SchemaError(f"comps.json: _metadata.avg_placement_tiers.stdev is negative: {tiers['stdev']}")
    if not isinstance(tiers.get("n_comps"), int) or tiers["n_comps"] <= 0:
        raise SchemaError("comps.json: _metadata.avg_placement_tiers.n_comps missing or not a positive int")
    bounds = tiers.get("bounds")
    if not isinstance(bounds, dict):
        raise SchemaError("comps.json: _metadata.avg_placement_tiers.bounds missing or not an object")
    for key in ("s_max", "a_max", "b_max", "c_max"):
        if not isinstance(bounds.get(key), (int, float)) or isinstance(bounds.get(key), bool):
            raise SchemaError(f"comps.json: _metadata.avg_placement_tiers.bounds.{key} missing or not a number")
    if not (bounds["s_max"] <= bounds["a_max"] <= bounds["b_max"] <= bounds["c_max"]):
        raise SchemaError(
            f"comps.json: _metadata.avg_placement_tiers.bounds not ascending "
            f"(s_max<=a_max<=b_max<=c_max): {bounds}"
        )


def validate_comps(data: dict, *, vector_dim: int) -> None:
    """`vector_dim` = len(unit_order) + len(trait_order) from meta.json,
    cross-checked against every centroid's length."""
    if not isinstance(data, dict) or not data:
        raise SchemaError("comps.json: root is not a non-empty object")

    if "_metadata" not in data:
        raise SchemaError("comps.json: missing _metadata key (SPEC-WEB.md section 8, avg_placement_tiers)")
    validate_avg_placement_tiers(data["_metadata"])

    for comp_id, info in data.items():
        if comp_id == "_metadata":
            continue
        if not isinstance(info, dict):
            raise SchemaError(f"comps.json: {comp_id} is not an object")

        for key in ("n_full", "n_total"):
            if not isinstance(info.get(key), int) or info[key] < 0:
                raise SchemaError(f"comps.json: {comp_id}.{key} missing or not a non-negative int")
        if not isinstance(info.get("avg_placement"), (int, float)) or not (1.0 <= info["avg_placement"] <= 8.0):
            raise SchemaError(f"comps.json: {comp_id}.avg_placement missing or not in [1, 8]")
        if not isinstance(info.get("top4_rate"), (int, float)) or not (0.0 <= info["top4_rate"] <= 1.0):
            raise SchemaError(f"comps.json: {comp_id}.top4_rate missing or not in [0, 1]")

        if comp_id == OFF_META:
            # No centroid/name/core_units/common_traits — it isn't a coherent
            # comp (see module docstring, gap #2).
            continue

        if not comp_id.startswith("c_"):
            raise SchemaError(f"comps.json: unrecognized comp key {comp_id!r} (expected 'c_NN' or 'off_meta')")
        if not isinstance(info.get("name"), str) or not info["name"]:
            raise SchemaError(f"comps.json: {comp_id}.name missing or not a string")
        centroid = info.get("centroid")
        if not isinstance(centroid, list) or not all(isinstance(v, (int, float)) for v in centroid):
            raise SchemaError(f"comps.json: {comp_id}.centroid missing or not a list of numbers")
        if len(centroid) != vector_dim:
            raise SchemaError(
                f"comps.json: {comp_id}.centroid has length {len(centroid)}, expected {vector_dim} "
                "(len(unit_order) + len(trait_order) from meta.json)"
            )
        core_units = info.get("core_units")
        if not isinstance(core_units, list) or not core_units or not all(isinstance(u, str) for u in core_units):
            raise SchemaError(f"comps.json: {comp_id}.core_units missing, empty, or not a list of strings")
        # SPEC-WEB.md 3.4: the frequency-threshold selection bounds this to
        # [5, 12] (clustering.py's CORE_UNIT_MIN/MAX) — not an arbitrary
        # choice here, it's documenting the actual pipeline invariant.
        if not (5 <= len(core_units) <= 12):
            raise SchemaError(f"comps.json: {comp_id}.core_units has {len(core_units)} entries, expected 5-12")
        avg_stars = info.get("core_unit_avg_stars")
        if not isinstance(avg_stars, dict) or set(avg_stars.keys()) != set(core_units):
            raise SchemaError(f"comps.json: {comp_id}.core_unit_avg_stars missing or its keys don't match core_units exactly")
        for unit_id, entry in avg_stars.items():
            # Shape is {"avg": float, "n": int} — the star-badge
            # tooltip (SPEC-WEB.md section 8.3) needs a real sample size
            # alongside the average, not just the average alone.
            if not isinstance(entry, dict):
                raise SchemaError(f"comps.json: {comp_id}.core_unit_avg_stars[{unit_id!r}] is not an object")
            avg = entry.get("avg")
            if not isinstance(avg, (int, float)) or isinstance(avg, bool) or not (1.0 <= avg <= 3.0):
                raise SchemaError(f"comps.json: {comp_id}.core_unit_avg_stars[{unit_id!r}].avg missing or not in [1.0, 3.0]")
            n = entry.get("n")
            if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
                raise SchemaError(f"comps.json: {comp_id}.core_unit_avg_stars[{unit_id!r}].n missing or not a positive int")
        common_traits = info.get("common_traits")
        if not isinstance(common_traits, list) or not common_traits:
            raise SchemaError(f"comps.json: {comp_id}.common_traits missing or empty")
        for pair in common_traits:
            if not (isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str) and isinstance(pair[1], int)):
                raise SchemaError(f"comps.json: {comp_id}.common_traits entry {pair!r} is not [trait_id, count]")


def validate_matchups(data: dict, *, known_comp_ids: set[str]) -> None:
    if not isinstance(data, dict) or not data:
        raise SchemaError("matchups.json: root is not a non-empty object")
    for a, inner in data.items():
        if a not in known_comp_ids:
            raise SchemaError(f"matchups.json: outer key {a!r} not a known comps.json id")
        if not isinstance(inner, dict) or not inner:
            raise SchemaError(f"matchups.json: {a} maps to an empty/non-object value")
        for b, cell in inner.items():
            if b not in known_comp_ids:
                raise SchemaError(f"matchups.json: {a}.{b} — {b!r} not a known comps.json id")
            if not isinstance(cell, dict) or not isinstance(cell.get("n"), int) or cell["n"] < 0:
                raise SchemaError(f"matchups.json: {a}.{b}.n missing or not a non-negative int")
            # Two legitimate shapes (see module docstring, gap #1):
            # a suppressed n<100 cell, or a full rate/n/ci cell.
            if cell.get("insufficient_data") is True:
                continue
            if not isinstance(cell.get("rate"), (int, float)) or not (0.0 <= cell["rate"] <= 1.0):
                raise SchemaError(f"matchups.json: {a}.{b}.rate missing or not in [0, 1]")
            ci = cell.get("ci")
            if not (isinstance(ci, list) and len(ci) == 2 and all(isinstance(v, (int, float)) for v in ci)):
                raise SchemaError(f"matchups.json: {a}.{b}.ci missing or not a 2-element list")
            if not (0.0 <= ci[0] <= ci[1] <= 1.0):
                raise SchemaError(f"matchups.json: {a}.{b}.ci is not an ascending [lo, hi] within [0, 1]")


def validate_comp_traits(data: dict, *, known_comp_ids: set[str], known_trait_ids: set[str]) -> None:
    if not isinstance(data, dict) or not data:
        raise SchemaError("comp_traits.json: root is not a non-empty object")
    for comp_id, info in data.items():
        if comp_id not in known_comp_ids:
            raise SchemaError(f"comp_traits.json: outer key {comp_id!r} not a known comps.json id")
        if not isinstance(info, dict):
            raise SchemaError(f"comp_traits.json: {comp_id} is not an object")
        for key in ("strong_against", "weak_against"):
            entries = info.get(key)
            if not isinstance(entries, list):
                raise SchemaError(f"comp_traits.json: {comp_id}.{key} missing or not a list")
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("trait") not in known_trait_ids:
                    raise SchemaError(f"comp_traits.json: {comp_id}.{key} entry has an unknown/missing trait id")
                if not isinstance(entry.get("delta"), (int, float)):
                    raise SchemaError(f"comp_traits.json: {comp_id}.{key} entry.delta missing or not a number")
                if not isinstance(entry.get("n"), int) or entry["n"] < 0:
                    raise SchemaError(f"comp_traits.json: {comp_id}.{key} entry.n missing or not a non-negative int")


def validate_items(data: dict, *, known_comp_ids: set[str]) -> None:
    if not isinstance(data, dict) or not data:
        raise SchemaError("items.json: root is not a non-empty object")
    if "_metadata" not in data:
        raise SchemaError("items.json: missing _metadata key (SPEC-WEB.md section 3.8)")
    metadata = data["_metadata"]
    if not isinstance(metadata.get("known_limitation"), str) or not metadata["known_limitation"]:
        raise SchemaError("items.json: _metadata.known_limitation missing or not a string")
    if not isinstance(metadata.get("coverage"), dict):
        raise SchemaError("items.json: _metadata.coverage missing or not an object")

    for comp_id, per_unit in data.items():
        if comp_id == "_metadata":
            continue
        if comp_id not in known_comp_ids:
            raise SchemaError(f"items.json: outer key {comp_id!r} not a known comps.json id")
        if not isinstance(per_unit, dict):
            raise SchemaError(f"items.json: {comp_id} is not an object")
        for unit_id, entries in per_unit.items():
            if not isinstance(entries, list):
                raise SchemaError(f"items.json: {comp_id}.{unit_id} is not a list")
            if len(entries) > 3:
                raise SchemaError(f"items.json: {comp_id}.{unit_id} has more than 3 entries")
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
                    raise SchemaError(f"items.json: {comp_id}.{unit_id} entry missing id")
                if not isinstance(entry.get("name"), str) or not entry["name"]:
                    raise SchemaError(f"items.json: {comp_id}.{unit_id} entry missing name")
                if not isinstance(entry.get("lift"), (int, float)):
                    raise SchemaError(f"items.json: {comp_id}.{unit_id} entry.lift missing or not a number")
                if not isinstance(entry.get("n"), int) or entry["n"] < 50:
                    raise SchemaError(f"items.json: {comp_id}.{unit_id} entry.n missing or below the documented min (50)")
                if not isinstance(entry.get("icon"), str) or not entry["icon"].startswith("http"):
                    raise SchemaError(f"items.json: {comp_id}.{unit_id} entry.icon missing or not an http(s) URL")


def enrich_items_with_icons(items: dict, static_items: dict) -> dict:
    """SPEC-WEB.md section 3.8 originally shipped items.json entries as
    {id, name, lift, n} — no icon. The item-recommendation UI (section 8)
    needs one, and data/static/items.json (static_data.py's output, never
    previously copied to web/data/) already has it via the same _icon_url()
    derivation used for units/traits (section 4) — verified:
    10 random completed-item URLs, 10/10 returned 200. This joins that icon
    onto every entry rather than shipping the whole 502-item static catalog
    to the browser, since items.json entries already carry their own name.
    Returns a new dict; does not mutate `items`."""
    enriched: dict = {"_metadata": items["_metadata"]}
    for comp_id, per_unit in items.items():
        if comp_id == "_metadata":
            continue
        enriched[comp_id] = {}
        for unit_id, entries in per_unit.items():
            new_entries = []
            for entry in entries:
                static_entry = static_items.get(entry["id"])
                if static_entry is None or "icon" not in static_entry:
                    raise SchemaError(
                        f"enrich_items_with_icons: item {entry['id']!r} (in {comp_id}.{unit_id}) "
                        "not found in data/static/items.json or missing an icon"
                    )
                new_entries.append({**entry, "icon": static_entry["icon"]})
            enriched[comp_id][unit_id] = new_entries
    return enriched


# ---------------------------------------------------------------------------
# Copy + validate
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"{path} not found — run the pipeline stage that produces it first")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def copy_processed_and_static(web_data_dir: Path) -> dict:
    """Copies+validates the six files SPEC-WEB.md section 2 lists as pipeline
    output (units.json/traits.json come from data/static/, the rest from
    data/processed/). Returns the loaded {name: data} dict so the caller
    (run()) can reuse it for _selftest.json generation instead of re-reading
    from disk."""
    meta = _load_json(DATA_PROCESSED / "meta.json")
    validate_meta(meta)
    _write_json(web_data_dir / "meta.json", meta)

    units = _load_json(DATA_STATIC / "units.json")
    validate_units(units)
    _write_json(web_data_dir / "units.json", units)

    traits = _load_json(DATA_STATIC / "traits.json")
    validate_traits(traits)
    _write_json(web_data_dir / "traits.json", traits)

    vector_dim = len(meta["unit_order"]) + len(meta["trait_order"])
    comps = _load_json(DATA_PROCESSED / "comps.json")
    validate_comps(comps, vector_dim=vector_dim)
    _write_json(web_data_dir / "comps.json", comps)

    known_comp_ids = {k for k in comps.keys() if k != "_metadata"}
    known_trait_ids = set(traits.keys())

    matchups = _load_json(DATA_PROCESSED / "matchups.json")
    validate_matchups(matchups, known_comp_ids=known_comp_ids)
    _write_json(web_data_dir / "matchups.json", matchups)

    comp_traits = _load_json(DATA_PROCESSED / "comp_traits.json")
    validate_comp_traits(comp_traits, known_comp_ids=known_comp_ids, known_trait_ids=known_trait_ids)
    _write_json(web_data_dir / "comp_traits.json", comp_traits)

    items = _load_json(DATA_PROCESSED / "items.json")
    static_items = _load_json(DATA_STATIC / "items.json")
    items = enrich_items_with_icons(items, static_items)
    validate_items(items, known_comp_ids=known_comp_ids)
    _write_json(web_data_dir / "items.json", items)

    logger.info("Copied+validated 6 files into %s", web_data_dir)
    return {"meta": meta, "units": units, "traits": traits, "comps": comps}


# ---------------------------------------------------------------------------
# _selftest.json (SPEC-WEB.md section 7.2)
# ---------------------------------------------------------------------------


def derive_trait_tiers_from_units(raw_units: dict, units_map: dict, traits_map: dict) -> dict[str, int]:
    """Reimplements SPEC-WEB.md section 7.1's client-side active-trait rule:
    count each trait's *unique* units on the board (star level irrelevant),
    then take the highest breakpoint cleared as the tier. Used only to pick
    which real boards are safe self-test material — see
    build_selftest_cases()'s docstring on why."""
    unit_counts: dict[str, int] = {}
    for unit_id in raw_units:
        for trait_id in units_map.get(unit_id, {}).get("traits", []):
            unit_counts[trait_id] = unit_counts.get(trait_id, 0) + 1

    tiers: dict[str, int] = {}
    for trait_id, count in unit_counts.items():
        breakpoints = traits_map.get(trait_id, {}).get("breakpoints", [])
        tier = sum(1 for bp in breakpoints if count >= bp)
        if tier >= 1:
            tiers[trait_id] = tier
    return tiers


def nearest_comp(vector: np.ndarray, centroids: dict[str, np.ndarray]) -> tuple[str, float]:
    """SPEC-WEB.md section 7.3's matching rule: cosine distance to every
    real (non-off_meta) centroid, smallest wins. Both vector and centroid
    rows are already L2-unit-norm, so cosine similarity is a plain dot
    product. This is deliberately independent of cluster_labels.npy — that
    array records HDBSCAN's density-based training label, which for a
    genuinely-clustered (non-noise) point isn't guaranteed to be the
    *nearest* centroid by cosine distance among ALL centroids. The web app
    never runs HDBSCAN; it only ever does this nearest-centroid comparison,
    so this function — not cluster_labels.npy — is the ground truth
    _selftest.json needs to encode."""
    comp_ids = list(centroids.keys())
    matrix = np.stack([centroids[c] for c in comp_ids])
    similarities = matrix @ vector
    best = int(np.argmax(similarities))
    return comp_ids[best], float(1.0 - similarities[best])


def _load_features_meta_rows() -> list[dict]:
    if not FEATURES_META_PATH.exists():
        raise SystemExit(f"{FEATURES_META_PATH} not found — run features.py first")
    with open(FEATURES_META_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def build_selftest_cases(
    rows: list[dict],
    vectors: np.ndarray,
    cluster_labels: np.ndarray,
    unit_order: list[str],
    trait_order: list[str],
    units_map: dict,
    traits_map: dict,
    centroids: dict[str, np.ndarray],
    match_threshold: float,
) -> list[dict]:
    """Picks real boards to ship as SPEC-WEB.md section 7.2's cross-check
    fixture: JS builds a board vector from a raw unit+star list the same way
    features.py did, and this file says what that vector (and its comp
    match) should come out to.

    **Why boards get filtered before selection.** The vector's trait
    component is Riot's own `tier_current` (features.py), which SPEC.md
    section 15 found already folds in emblem bonuses on ~95% of boards that
    carry one (a Cassiopeia with a Flora Fatalis Emblem reports
    `num_units=2` for a trait she's not natively on). SPEC-WEB.md section
    7.1's client-side rule has no item/emblem input at all in v1 (section
    11) — it can only count native unit membership. For a board where an
    emblem (or some other trait-granting item) actually changed a tier, the
    real pipeline vector and a v1 JS implementation's vector would
    correctly, unavoidably disagree, and a self-test built from that board
    would fail for a reason that has nothing to do with a JS bug — worse,
    it would train whoever's debugging it to distrust a correct
    implementation. So: only boards where derive_trait_tiers_from_units()
    (pure native-membership counting) reproduces the stored `raw_traits`
    exactly are eligible. This also happens to be exactly the ~95% case
    SPEC.md already measured, so eligible boards remain plentiful.

    Selection is grouped by cluster_labels.npy purely for *diversity* — to
    spread the sample across many different comps' vector neighborhoods —
    not as a correctness oracle (see nearest_comp()'s docstring for why
    that array isn't one). Within each group the lowest (match_id, puuid)
    pair is picked, so re-running this against unchanged input data always
    picks the same boards.
    """
    eligible_by_label: dict[int, list[int]] = {}
    for i, row in enumerate(rows):
        derived = derive_trait_tiers_from_units(row["raw_units"], units_map, traits_map)
        if derived != row["raw_traits"]:
            continue
        eligible_by_label.setdefault(int(cluster_labels[i]), []).append(i)

    def sort_key(i: int) -> tuple[str, str]:
        return (rows[i]["match_id"], rows[i]["puuid"])

    cases: list[dict] = []
    for label in sorted(eligible_by_label):
        indices = sorted(eligible_by_label[label], key=sort_key)
        i = indices[0]
        row = rows[i]

        vector = build_vectors([row], unit_order, trait_order)[0]
        if not np.allclose(vector, vectors[i], atol=1e-6):
            raise SchemaError(
                f"build_selftest_cases: recomputed vector for row {i} (match_id={row['match_id']}) "
                "doesn't match features_vectors.npy — features.py and this module have drifted apart"
            )

        matched_id, distance = nearest_comp(vector, centroids)
        group_label = OFF_META if label == OFF_META_CODE else f"c_{label:02d}"

        # No match_id in the output: web/data/ is published in
        # a public repo, and a real Riot match ID lets anyone look up the
        # players in that lobby via the API. The self-test never read it —
        # it's still used above as a deterministic sort key, just not shipped.
        cases.append(
            {
                "id": group_label,
                "early_death": row["early_death"],
                "input_units": [
                    {"id": unit_id, "star": info["tier"]} for unit_id, info in sorted(row["raw_units"].items())
                ],
                "expected_vector": [round(float(v), 8) for v in vector],
                "nearest_comp": matched_id,
                "distance": round(distance, 8),
                "matched_comp": matched_id if distance <= match_threshold else None,
            }
        )

    return cases


def generate_selftest(context: dict) -> dict:
    meta = context["meta"]
    comps = context["comps"]
    unit_order, trait_order = meta["unit_order"], meta["trait_order"]

    rows = _load_features_meta_rows()
    vectors = np.load(FEATURES_VECTORS_PATH)
    cluster_labels = np.load(CLUSTER_LABELS_PATH)
    if not (len(rows) == len(vectors) == len(cluster_labels)):
        raise SchemaError(
            "generate_selftest: features_meta.jsonl / features_vectors.npy / cluster_labels.npy row counts disagree"
        )

    centroids = {
        comp_id: np.array(info["centroid"], dtype=np.float64)
        for comp_id, info in comps.items()
        if comp_id.startswith("c_")  # skips "off_meta" (no centroid) and "_metadata" (no centroid either)
    }

    cases = build_selftest_cases(
        rows, vectors, cluster_labels, unit_order, trait_order,
        context["units"], context["traits"], centroids, meta["match_threshold"],
    )
    if len(cases) < MIN_SELFTEST_CASES:
        raise SchemaError(
            f"generate_selftest: only found {len(cases)} eligible self-test boards, "
            f"need at least {MIN_SELFTEST_CASES} — investigate before shipping"
        )

    logger.info("Generated %d self-test cases (%d distinct cluster labels found eligible)", len(cases), len(cases))

    return {
        "note": (
            "SPEC-WEB.md section 7.2 cross-check fixture. Each case's input_units is what a user "
            "would drag onto the board; expected_vector/nearest_comp/distance/matched_comp are what "
            "features.py's vector + a nearest-centroid cosine match (section 7.3) produce for it. "
            "Boards were pre-filtered to exclude any where an emblem or other trait-granting item "
            "would make a pure unit-count trait derivation disagree with the real pipeline vector — "
            "see build_web_data.py's build_selftest_cases() docstring."
        ),
        "match_threshold": meta["match_threshold"],
        "vector_dim": len(unit_order) + len(trait_order),
        "suggested_vector_tolerance": 1e-4,
        "cases": cases,
    }


# ---------------------------------------------------------------------------


def run() -> None:
    WEB_DATA_DIR.mkdir(parents=True, exist_ok=True)
    context = copy_processed_and_static(WEB_DATA_DIR)
    selftest = generate_selftest(context)
    _write_json(WEB_DATA_DIR / "_selftest.json", selftest)
    logger.info("Wrote %s (%d cases)", WEB_DATA_DIR / "_selftest.json", len(selftest["cases"]))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()


if __name__ == "__main__":
    main()
