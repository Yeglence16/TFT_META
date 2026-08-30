"""CLI entry point: unit list -> board vector -> comp report (SPEC.md section 14).

Terminology (SPEC.md section 0): everything here is a **head-to-head
placement rate** — "aynı lobide her iki comp da varken A'nın B'den yukarıda
bitirme oranı." Never "counter", never "win rate" — Riot's data can't
support either claim.

Input is a list of unit display names (fuzzy-matched via difflib against
static_data.py's units.json, so a typo or wrong case still resolves).
Star level isn't asked for — defaults to 2 for every entered unit
(SPEC-WEB.md section 5.2: "Varsayılan 2 — veride en sık görülen durum bu"),
and this default is stated in the output, not hidden. Active trait tiers
are computed the same way the game does: count how many entered units share
each trait (from units.json's own `traits` field per champion), then apply
that trait's breakpoints (traits.json) — no trait input needed from the user.

Fewer than 4 units: too sparse to mean anything as a comp, no matching or
prediction is attempted at all.

Distance to the nearest cluster centroid (models/centroids.npz) is compared
against meta.json's `match_threshold`:
  - within threshold: full report (comp name + distance, avg placement +
    top4 + n from comps.json's n_total, head-to-head from matchups.json —
    one ranked list if fewer than 2*TOP_K comps have n>=100 data (so two
    top-TOP_K slices can't overlap and repeat entries between "highest" and
    "lowest"), else highest/lowest-superiority 5 each, plus a note when
    every available matchup sits on the same side of 50% — most-struggled/
    most-comfortable 5 traits from comp_traits.json, model prediction
    (clipped to [1, 8] via src.model.clip_predictions))
  - beyond threshold: "bu board bilinen bir comp'a uymuyor" + the nearest 3
    comps for context, plus the model prediction anyway — that's the whole
    point of having a regression model (SPEC.md section 13): it still
    produces an estimate when the lookup table has nothing to say.

Augments: SPEC.md section 11 was skipped — the `augments` key does not
exist at all on any participant in this dataset, not `null` (see
features.py for the full verification). The augment section of the report
says so explicitly instead of being silently omitted.
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.model import clip_predictions
from src.paths import DATA_PROCESSED, DATA_STATIC, MODELS_DIR, ensure_dirs

logger = logging.getLogger(__name__)

META_JSON_PATH = DATA_PROCESSED / "meta.json"
COMPS_PATH = DATA_PROCESSED / "comps.json"
MATCHUPS_PATH = DATA_PROCESSED / "matchups.json"
COMP_TRAITS_PATH = DATA_PROCESSED / "comp_traits.json"
CENTROIDS_PATH = MODELS_DIR / "centroids.npz"
LGBM_MODEL_PATH = MODELS_DIR / "lightgbm_model.pkl"
STATIC_UNITS_PATH = DATA_STATIC / "units.json"
STATIC_TRAITS_PATH = DATA_STATIC / "traits.json"

DEFAULT_STAR_LEVEL = 2
MIN_UNITS = 4
TOP_K = 5


def _require(path: Path, producer: str) -> None:
    if not path.exists():
        raise SystemExit(f"{path} not found — run {producer} first")


def load_data() -> dict:
    for path, producer in [
        (META_JSON_PATH, "features.py"),
        (COMPS_PATH, "clustering.py"),
        (MATCHUPS_PATH, "matchups.py"),
        (COMP_TRAITS_PATH, "traits.py"),
        (CENTROIDS_PATH, "clustering.py"),
        (LGBM_MODEL_PATH, "model.py"),
        (STATIC_UNITS_PATH, "static_data.py"),
        (STATIC_TRAITS_PATH, "static_data.py"),
    ]:
        _require(path, producer)

    meta = json.loads(META_JSON_PATH.read_text(encoding="utf-8"))
    comps = json.loads(COMPS_PATH.read_text(encoding="utf-8"))
    matchups = json.loads(MATCHUPS_PATH.read_text(encoding="utf-8"))
    comp_traits = json.loads(COMP_TRAITS_PATH.read_text(encoding="utf-8"))
    units_static = json.loads(STATIC_UNITS_PATH.read_text(encoding="utf-8"))
    traits_static = json.loads(STATIC_TRAITS_PATH.read_text(encoding="utf-8"))
    centroids = {key: np.asarray(value) for key, value in np.load(CENTROIDS_PATH).items()}
    with open(LGBM_MODEL_PATH, "rb") as f:
        model = pickle.load(f)

    return {
        "meta": meta,
        "comps": comps,
        "matchups": matchups,
        "comp_traits": comp_traits,
        "units_static": units_static,
        "traits_static": traits_static,
        "centroids": centroids,
        "model": model,
    }


def fuzzy_match_units(input_names: list[str], units_static: dict) -> tuple[list[str], list[str]]:
    """Returns (matched_unit_ids, unmatched_input_names). Exact
    case-insensitive match first, then a fuzzy fallback (difflib) so a typo
    or different capitalization still resolves. Duplicate matches (same
    champion entered twice) are dropped, keeping the first occurrence."""
    lower_to_id = {info["name"].lower(): uid for uid, info in units_static.items()}
    all_lower_names = list(lower_to_id.keys())

    matched: list[str] = []
    unmatched: list[str] = []
    for raw in input_names:
        key = raw.strip().lower()
        unit_id = lower_to_id.get(key)
        if unit_id is None:
            close = difflib.get_close_matches(key, all_lower_names, n=1, cutoff=0.6)
            unit_id = lower_to_id[close[0]] if close else None
        if unit_id is None:
            unmatched.append(raw)
        elif unit_id not in matched:
            matched.append(unit_id)

    return matched, unmatched


def compute_active_traits(unit_ids: list[str], units_static: dict, traits_static: dict) -> dict[str, int]:
    """Same rule the game itself uses: count how many of the given units
    share each trait, then apply that trait's breakpoints — no trait input
    needed from the user."""
    trait_unit_counts: dict[str, int] = {}
    for unit_id in unit_ids:
        for trait_id in units_static[unit_id]["traits"]:
            trait_unit_counts[trait_id] = trait_unit_counts.get(trait_id, 0) + 1

    active: dict[str, int] = {}
    for trait_id, count in trait_unit_counts.items():
        if trait_id not in traits_static:
            continue
        breakpoints = sorted(traits_static[trait_id]["breakpoints"])
        tier_current = sum(1 for bp in breakpoints if count >= bp)
        if tier_current > 0:
            active[trait_id] = tier_current
    return active


def build_board_vector(
    unit_ids: list[str],
    star_level: int,
    unit_order: list[str],
    trait_order: list[str],
    units_static: dict,
    traits_static: dict,
) -> np.ndarray:
    unit_index = {u: i for i, u in enumerate(unit_order)}
    trait_index = {t: i for i, t in enumerate(trait_order)}
    vector = np.zeros(len(unit_order) + len(trait_order), dtype=np.float32)

    for unit_id in unit_ids:
        if unit_id in unit_index:
            vector[unit_index[unit_id]] = star_level

    for trait_id, tier_current in compute_active_traits(unit_ids, units_static, traits_static).items():
        if trait_id in trait_index:
            vector[len(unit_order) + trait_index[trait_id]] = tier_current

    norm = np.linalg.norm(vector)
    return vector / norm if norm > 0 else vector


def nearest_comps(vector: np.ndarray, centroids: dict[str, np.ndarray]) -> list[tuple[str, float]]:
    """Cosine distance to every centroid (both unit-norm -> dot product),
    sorted nearest first."""
    scored = [(comp_id, 1.0 - float(np.dot(vector, centroid))) for comp_id, centroid in centroids.items()]
    return sorted(scored, key=lambda kv: kv[1])


def format_head_to_head(comp_id: str, matchups: dict, comps: dict) -> list[str]:
    entries = matchups.get(comp_id, {})
    valid = [(opp, cell) for opp, cell in entries.items() if "rate" in cell]
    if not valid:
        return ["  (yeterli veri yok)"]

    by_rate = sorted(valid, key=lambda kv: kv[1]["rate"])

    def fmt(opp: str, cell: dict) -> str:
        opp_name = comps.get(opp, {}).get("name", opp)
        return f"    {opp_name}: {cell['rate']:.1%} (n={cell['n']}, CI [{cell['ci'][0]:.1%}, {cell['ci'][1]:.1%}])"

    lines: list[str] = []
    if len(by_rate) < 2 * TOP_K:
        # Fewer than 2*TOP_K comps have n>=100 data — a top-TOP_K/bottom-TOP_K
        # split would necessarily repeat entries on both sides (e.g. 6 valid
        # entries and TOP_K=5 share 4 of them). Show one ranked list instead
        # of two overlapping ones.
        lines.append("  Bilinen head-to-head eşleşmeler (üstünlük oranına göre, yüksekten düşüğe):")
        for opp, cell in reversed(by_rate):
            lines.append(fmt(opp, cell))
    else:
        worst = by_rate[:TOP_K]
        best = list(reversed(by_rate[-TOP_K:]))
        lines.append("  En yüksek üstünlük (head-to-head):")
        for opp, cell in best:
            lines.append(fmt(opp, cell))
        lines.append("  En düşük üstünlük (head-to-head):")
        for opp, cell in worst:
            lines.append(fmt(opp, cell))

    # SPEC.md section 0: this is a placement-superiority rate, never framed
    # as "winning"/"losing" — but a reader could still infer a loss from a
    # "lowest" entry that's actually still above 50%. Say so explicitly.
    min_rate = by_rate[0][1]["rate"]
    max_rate = by_rate[-1][1]["rate"]
    if min_rate >= 0.5:
        lines.append("  Not: yeterli veri olan hiçbir eşleşmede bu comp %50'nin altına düşmüyor.")
    elif max_rate <= 0.5:
        lines.append("  Not: yeterli veri olan hiçbir eşleşmede bu comp %50'nin üstüne çıkmıyor.")

    return lines


def format_traits(comp_id: str, comp_traits: dict, trait_names: dict[str, str]) -> list[str]:
    entry = comp_traits.get(comp_id, {})
    lines = []

    weak = entry.get("weak_against", [])
    lines.append("  Zorlandığı trait'ler (rakipte aktifken placement kötüleşiyor):")
    if weak:
        for item in weak:
            name = trait_names.get(item["trait"], item["trait"])
            lines.append(f"    {name}: delta {item['delta']:+.2f} (n={item['n']})")
    else:
        lines.append("    (yeterli veri yok)")

    strong = entry.get("strong_against", [])
    lines.append("  Rahat ettiği trait'ler (rakipte aktifken placement iyileşiyor):")
    if strong:
        for item in strong:
            name = trait_names.get(item["trait"], item["trait"])
            lines.append(f"    {name}: delta {item['delta']:+.2f} (n={item['n']})")
    else:
        lines.append("    (yeterli veri yok)")

    return lines


def predict_placement(model, vector: np.ndarray) -> float:
    raw = model.predict(vector.reshape(1, -1))
    return float(clip_predictions(raw)[0])


def run(unit_names: list[str]) -> str:
    ensure_dirs()
    data = load_data()
    meta = data["meta"]
    units_static = data["units_static"]
    traits_static = data["traits_static"]
    trait_names = {tid: info["name"] for tid, info in traits_static.items()}

    matched_ids, unmatched = fuzzy_match_units(unit_names, units_static)
    lines: list[str] = []
    if unmatched:
        lines.append(f"Tanınamayan unit isimleri (atlandı): {', '.join(unmatched)}")

    if len(matched_ids) < MIN_UNITS:
        lines.append(
            f"En az {MIN_UNITS} unit gerekli, {len(matched_ids)} tanındı — eşleştirme yapılmadı."
        )
        return "\n".join(lines)

    matched_names = [units_static[uid]["name"] for uid in matched_ids]
    lines.append(f"Board ({len(matched_ids)} unit, hepsi yıldız {DEFAULT_STAR_LEVEL} varsayıldı): {', '.join(matched_names)}")

    vector = build_board_vector(
        matched_ids, DEFAULT_STAR_LEVEL, meta["unit_order"], meta["trait_order"], units_static, traits_static
    )

    model_placement = predict_placement(data["model"], vector)

    ranked = nearest_comps(vector, data["centroids"])
    if not ranked:
        lines.append("Hiç comp centroid'i bulunamadı — clustering.py'yi çalıştırın.")
        return "\n".join(lines)

    best_comp_id, best_distance = ranked[0]
    threshold = meta["match_threshold"]

    if best_distance <= threshold:
        comp = data["comps"][best_comp_id]
        lines.append("")
        lines.append(f"Eşleşen comp: {comp['name']} (mesafe={best_distance:.3f}, eşik={threshold:.3f})")
        lines.append(
            f"  Ortalama placement: {comp['avg_placement']:.2f}  "
            f"Top4 oranı: {comp['top4_rate']:.1%}  n={comp['n_total']}"
        )
        lines.extend(format_head_to_head(best_comp_id, data["matchups"], data["comps"]))
        lines.extend(format_traits(best_comp_id, data["comp_traits"], trait_names))
    else:
        lines.append("")
        lines.append(f"Bu board bilinen bir comp'a uymuyor (en yakın mesafe={best_distance:.3f}, eşik={threshold:.3f}).")
        lines.append("En yakın 3 comp:")
        for comp_id, distance in ranked[:3]:
            comp_name = data["comps"].get(comp_id, {}).get("name", comp_id)
            lines.append(f"    {comp_name}: mesafe={distance:.3f}")

    lines.append("")
    lines.append(f"Model tahmini (placement): {model_placement:.2f}")

    lines.append("")
    lines.append("Augment tavsiyesi: veri yok — Riot API şu an bu maç setinde augment verisi döndürmüyor.")

    return "\n".join(lines)


def main() -> None:
    # Same root cause as tests/test_encoding.py's read_text/write_text bug:
    # print() defaults to the OS locale encoding (cp1254 here), which
    # produces invalid UTF-8 for names like "Kha'Zix" or "—" the moment
    # stdout is redirected or piped instead of drawn straight to a terminal.
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="TFT board -> comp report (SPEC.md section 14)")
    parser.add_argument("units", nargs="+", help="Unit display names, e.g.: Ahri Ornn Sett Sentinel Krug")
    args = parser.parse_args()
    print(run(args.units))


if __name__ == "__main__":
    main()
