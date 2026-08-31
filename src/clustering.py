"""Comp discovery via HDBSCAN (SPEC.md section 9).

There is no labeled comp data — clusters ARE the comps, discovered from the
L2-normalized board vectors features.py produces. K-Means is not used
(comps aren't equal-sized or spherical in general; HDBSCAN also finds its
own cluster count instead of needing one guessed upfront).

**Distance metric is euclidean, not cosine (changed 2026-08-31).** Cosine
worked fine at 3k-match scale (~20k training boards clusters in under 10s)
but doesn't scale: sklearn's HDBSCAN only has a brute-force path for cosine
(`_hdbscan_brute` → a dense NxN `pairwise_distances` matrix, O(n²) memory),
and at 15k-match scale (~100k training boards) that's a 99623x99623 float64
matrix — 73.9 GiB, more than this machine's 31.7 GB RAM. It crashed with
`numpy._core._exceptions._ArrayMemoryError` on a live run.

Euclidean is mathematically equivalent here, not just a convenient
workaround: for L2-normalized vectors, ‖a−b‖² = 2(1−cos(a,b)) — euclidean
distance is a strictly monotonic transform of cosine distance. HDBSCAN's
clustering is built entirely from *relative* distance ordering (mutual
reachability graph + minimum spanning tree), so a monotonic transform can't
change which points end up in which cluster. Verified, not just argued:
sklearn's `algorithm="auto"` (default) routes `metric="euclidean"` to
`_hdbscan_prims` (a KD-tree, since "euclidean" is in `KDTree.valid_metrics`)
instead of the brute-force path — confirmed by reading sklearn 1.8's
`_hdbscan/hdbscan.py` algorithm-selection code, not assumed. Empirically
verified on the reconstructed original 2937-match dataset (both metrics fit
without memory issues at that scale): identical cluster count (22 vs 22),
identical off-meta fraction, **Adjusted Rand Index 0.9995** between the two
label assignments — essentially the same partition. At full 15k-match scale,
euclidean holds steady around ~550-560 MB (vs. cosine's fatal 73.9 GiB) and
completes in ~17 minutes.

Everything downstream of HDBSCAN's own fit (`_nearest_centroid_labels` /
`reassign_noise` for noise/early-death assignment, `compute_centroids`,
`cli.py`'s and `app.js`'s comp-matching) computes its own cosine dot-product
distance independently on the L2-normalized vectors — none of it reads
HDBSCAN's internal metric, so none of `config.yaml`'s
`noise_distance_threshold`, `meta.json`'s `match_threshold`, or
`cluster_selection_epsilon` (never set, stays at sklearn's inactive 0.0
default either way) needed to change alongside this.

min_cluster_size = round(config's clustering.min_cluster_size_fraction *
sample_count) — not a fixed number, so it scales with however much data a
given run has.

Noise handling: HDBSCAN's label -1 points are reassigned to their nearest
cluster centroid IF that cosine distance is within
config's clustering.noise_distance_threshold; otherwise they stay unassigned
("off-meta" — a real, reportable bucket, not silently dropped and not forced
into a cluster it doesn't belong to).

**Two populations, survivorship-bias fix.** features.py now flags every
participant `early_death` instead of dropping them. HDBSCAN trains ONLY on
the non-early-death subset — unchanged from before, a half-built board still
can't define a comp archetype. But after training, early-death boards are
ALSO assigned a label, using the exact same nearest-centroid +
distance-threshold logic as noise reassignment (`_nearest_centroid_labels`,
shared by both paths). Every downstream statistic (comps.json's
avg_placement/top4_rate, matchups.json, comp_traits.json) is computed over
the FULL labeled population, not just the training subset — using only the
training subset was silently biasing every placement-based statistic
optimistic, since early deaths skew hard toward 7th/8th place. Caught via
model.py: a dummy predicting 4.5 for everything should have MAE exactly 2.0
for a uniform 1-8 distribution; it measured 1.81 on the training-only
subset.

comps.json keeps both counts per cluster: `n_full` = how many boards were
part of the actual HDBSCAN training/reassignment (the original, unbiased
sense of "n"), `n_total` = every board (training + early-death) carrying
that final label, which is what avg_placement/top4_rate/core_units are now
computed from.

Automatic naming: for each cluster, per SPEC.md:
  carry unit    = argmax over units of mean(star_level * item_count) across
                  cluster members (0 for members without that unit)
  dominant trait = argmax over traits of mean(tier_current) across cluster
                  members (0 for members without that trait)
  name = "{carry unit} — {dominant trait}"; a repeated name gets a second
  unit appended to disambiguate (SPEC.md: "aynı isim iki cluster'a düşerse
  ikinciye ayırt edici bir ikinci unit ekle").

Cluster names and the review table use display names from
static_data.py's units.json/traits.json (e.g. "Ornn — Elderwood", not the
raw "DA_18_Ornn — DA_18_Elderwood" id). comps.json's core_units/common_traits
stay as raw apiName ids, per SPEC-WEB.md section 3.4's contract — the web
side joins those against units.json/traits.json itself.

**This ran against a ~3000-match dev sample (2937 matches, 23496 boards
total, ~20152 used for training) — not final data.** However many clusters
this finds, however noisy they look, is reported as-is. Don't retune
parameters to make the output "look nice" — the point right now is
verifying the pipeline runs correctly end to end, not producing a
presentable meta report.

Outputs:
  models/hdbscan_model.pkl        the fitted HDBSCAN model (pickle)
  models/centroids.npz            {cluster_id: L2-normalized centroid vector}
  data/processed/comps.json       SPEC-WEB.md section 3.4 shape (name,
                                   centroid, avg_placement, top4_rate,
                                   core_units, common_traits), with `n`
                                   replaced by `n_full`/`n_total` (see
                                   survivorship-bias note above) — plus an
                                   "off_meta" entry (no centroid/name, it
                                   isn't a coherent comp)
  data/processed/cluster_labels.npy   per-board final label for the FULL
                                   population (int32, off-meta = -2),
                                   aligned with features_meta.jsonl — this is
                                   what matchups.py/traits.py/model.py read
  data/processed/cluster_review.txt   human-readable review table (SPEC.md:
                                   "Bu tabloyu gözle inceleyeceğiz")
  data/processed/meta.json        updated in place with match_threshold and
                                   clustering_training_board_count
                                   (SPEC-WEB.md section 3.1)
"""

from __future__ import annotations

import json
import logging
import pickle
import sys
from pathlib import Path

import numpy as np
import yaml
from sklearn.cluster import HDBSCAN

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import DATA_PROCESSED, DATA_STATIC, MODELS_DIR, PROJECT_ROOT, ensure_dirs

logger = logging.getLogger(__name__)

CONFIG_PATH = PROJECT_ROOT / "config.yaml"
VECTORS_PATH = DATA_PROCESSED / "features_vectors.npy"
META_ROWS_PATH = DATA_PROCESSED / "features_meta.jsonl"
META_JSON_PATH = DATA_PROCESSED / "meta.json"
STATIC_UNITS_PATH = DATA_STATIC / "units.json"
STATIC_TRAITS_PATH = DATA_STATIC / "traits.json"
MODEL_PATH = MODELS_DIR / "hdbscan_model.pkl"
CENTROIDS_PATH = MODELS_DIR / "centroids.npz"
COMPS_PATH = DATA_PROCESSED / "comps.json"
REVIEW_PATH = DATA_PROCESSED / "cluster_review.txt"
CLUSTER_LABELS_PATH = DATA_PROCESSED / "cluster_labels.npy"

OFF_META = "off_meta"
OFF_META_CODE = -2  # encodes OFF_META in the saved int label array (HDBSCAN noise is -1, already reassigned by here)

# core_units selection (SPEC.md section 9's "en sık 8 unit" — verified against
# real data this session: a fixed top-8 cut sits mid-drop for outlier clusters
# (n_total < ~1000, e.g. c_09) while a >=50%-of-boards frequency threshold
# lines up with each cluster's own natural falloff in 20/22 clusters checked.
# CORE_UNIT_MIN/MAX bound the rare cases where the threshold alone misfires:
# a very gradual decline (no unit ever reaches 50%) or a very sharp one
# (more than 12 units clear 50%, unseen in this data but not impossible).
CORE_UNIT_FREQUENCY_THRESHOLD = 0.5
CORE_UNIT_MIN = 5
CORE_UNIT_MAX = 12


def _load_rows() -> list[dict]:
    if not META_ROWS_PATH.exists():
        raise SystemExit(f"{META_ROWS_PATH} not found — run features.py first")
    with open(META_ROWS_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def _load_display_names() -> tuple[dict[str, str], dict[str, str]]:
    if not STATIC_UNITS_PATH.exists() or not STATIC_TRAITS_PATH.exists():
        raise SystemExit(f"{STATIC_UNITS_PATH} / {STATIC_TRAITS_PATH} not found — run static_data.py first")
    units = json.loads(STATIC_UNITS_PATH.read_text(encoding="utf-8"))
    traits = json.loads(STATIC_TRAITS_PATH.read_text(encoding="utf-8"))
    return {uid: info["name"] for uid, info in units.items()}, {tid: info["name"] for tid, info in traits.items()}


def fit_hdbscan(vectors: np.ndarray, min_cluster_size_fraction: float) -> tuple[HDBSCAN, np.ndarray, int]:
    # metric="euclidean", not "cosine" — see module docstring (2026-08-31):
    # mathematically equivalent for L2-normalized vectors (monotonic
    # transform, HDBSCAN is rank-order-based), but avoids cosine's brute-force
    # O(n²) memory path, which crashes past ~a few 10k training points.
    min_cluster_size = max(2, round(min_cluster_size_fraction * len(vectors)))
    model = HDBSCAN(min_cluster_size=min_cluster_size, metric="euclidean", copy=False)
    labels = model.fit_predict(vectors)
    return model, labels, min_cluster_size


def compute_centroids(vectors: np.ndarray, labels: np.ndarray) -> dict[int, np.ndarray]:
    centroids: dict[int, np.ndarray] = {}
    for cluster_id in sorted(set(labels.tolist())):
        if cluster_id == -1:
            continue
        member_vectors = vectors[labels == cluster_id]
        centroid = member_vectors.mean(axis=0)
        norm = np.linalg.norm(centroid)
        centroids[cluster_id] = centroid / norm if norm > 0 else centroid
    return centroids


def _nearest_centroid_labels(
    vectors: np.ndarray, centroids: dict[int, np.ndarray], distance_threshold: float
) -> np.ndarray:
    """For each vector, finds the nearest centroid by cosine similarity
    (a plain dot product, since both vectors and centroid rows are
    unit-norm) and assigns it if within distance_threshold, else OFF_META.
    Shared by HDBSCAN noise-point reassignment and by post-hoc assignment of
    early-death boards that were excluded from training (see module
    docstring) — same rule, two different input sets."""
    if len(vectors) == 0:
        return np.array([], dtype=object)
    if not centroids:
        return np.full(len(vectors), OFF_META, dtype=object)

    cluster_ids = list(centroids.keys())
    centroid_matrix = np.stack([centroids[cid] for cid in cluster_ids])  # (K, D)
    similarities = vectors @ centroid_matrix.T  # (N, K)
    best_idx = np.argmax(similarities, axis=1)
    best_sim = similarities[np.arange(len(vectors)), best_idx]
    distances = 1.0 - best_sim

    labels = np.empty(len(vectors), dtype=object)
    for i in range(len(vectors)):
        labels[i] = cluster_ids[best_idx[i]] if distances[i] <= distance_threshold else OFF_META
    return labels


def reassign_noise(
    vectors: np.ndarray, labels: np.ndarray, centroids: dict[int, np.ndarray], distance_threshold: float
) -> np.ndarray:
    """Returns a new label array where -1 (HDBSCAN noise) points are either
    reassigned to their nearest centroid (if within distance_threshold) or
    moved to OFF_META. Already-clustered points are untouched."""
    noise_mask = labels == -1
    final_labels = np.array(labels, dtype=object)  # box int cluster ids, keep as-is

    if noise_mask.any():
        noise_labels = _nearest_centroid_labels(vectors[noise_mask], centroids, distance_threshold)
        final_labels[noise_mask] = noise_labels
        reassigned = int(sum(1 for label in noise_labels if label != OFF_META))
    else:
        reassigned = 0
    n_noise = int(noise_mask.sum())

    logger.info(
        "Noise reassignment (threshold=%.2f): %d/%d noise points reassigned to a cluster, %d stayed off-meta",
        distance_threshold, reassigned, n_noise, n_noise - reassigned,
    )
    return final_labels


def _weighted_unit_scores(rows: list[dict], indices: list[int]) -> dict[str, float]:
    """mean(star_level * item_count) per unit across the given rows, 0 for
    rows missing that unit."""
    totals: dict[str, float] = {}
    for i in indices:
        for unit_id, info in rows[i]["raw_units"].items():
            totals[unit_id] = totals.get(unit_id, 0.0) + info["tier"] * info["item_count"]
    n = len(indices)
    return {unit_id: total / n for unit_id, total in totals.items()}


def _mean_trait_tiers(rows: list[dict], indices: list[int]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for i in indices:
        for trait_name, tier_current in rows[i]["raw_traits"].items():
            totals[trait_name] = totals.get(trait_name, 0.0) + tier_current
    n = len(indices)
    return {trait_name: total / n for trait_name, total in totals.items()}


def _unit_frequency(rows: list[dict], indices: list[int]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for i in indices:
        for unit_id in rows[i]["raw_units"]:
            counts[unit_id] = counts.get(unit_id, 0) + 1
    return counts


def select_core_units(unit_freq: dict[str, int], n_total: int) -> list[tuple[str, int]]:
    """Units appearing in >= CORE_UNIT_FREQUENCY_THRESHOLD of the cluster's
    boards, bounded to [CORE_UNIT_MIN, CORE_UNIT_MAX]. Falls back to the
    top-N by frequency when the threshold alone would give too few or too
    many (see the constants' comment for why bounds exist at all)."""
    ranked = sorted(unit_freq.items(), key=lambda kv: -kv[1])
    above_threshold = [(unit_id, count) for unit_id, count in ranked if count / n_total >= CORE_UNIT_FREQUENCY_THRESHOLD]

    if len(above_threshold) < CORE_UNIT_MIN:
        return ranked[:CORE_UNIT_MIN]
    if len(above_threshold) > CORE_UNIT_MAX:
        return above_threshold[:CORE_UNIT_MAX]
    return above_threshold


def _trait_frequency(rows: list[dict], indices: list[int]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for i in indices:
        for trait_name in rows[i]["raw_traits"]:
            counts[trait_name] = counts.get(trait_name, 0) + 1
    return counts


def name_clusters(
    rows: list[dict],
    members_by_cluster: dict[int, list[int]],
    unit_names: dict[str, str],
    trait_names: dict[str, str],
) -> dict[int, str]:
    base_info: dict[int, tuple[str, str, dict[str, float]]] = {}
    for cluster_id, indices in members_by_cluster.items():
        unit_scores = _weighted_unit_scores(rows, indices)
        trait_scores = _mean_trait_tiers(rows, indices)
        carry_unit = max(unit_scores, key=unit_scores.get) if unit_scores else "unknown_unit"
        dominant_trait = max(trait_scores, key=trait_scores.get) if trait_scores else "unknown_trait"
        base_info[cluster_id] = (carry_unit, dominant_trait, unit_scores)

    names: dict[int, str] = {}
    used_names: set[str] = set()
    for cluster_id in sorted(members_by_cluster):
        carry_unit, dominant_trait, unit_scores = base_info[cluster_id]
        carry_display = unit_names.get(carry_unit, carry_unit)
        trait_display = trait_names.get(dominant_trait, dominant_trait)
        name = f"{carry_display} — {trait_display}"
        if name in used_names:
            second_unit = next(
                (u for u, _ in sorted(unit_scores.items(), key=lambda kv: -kv[1]) if u != carry_unit),
                None,
            )
            if second_unit:
                second_display = unit_names.get(second_unit, second_unit)
                name = f"{carry_display} & {second_display} — {trait_display}"
        used_names.add(name)
        names[cluster_id] = name

    return names


def build_cluster_report(
    rows: list[dict],
    labels: np.ndarray,
    centroids: dict[int, np.ndarray],
    unit_names: dict[str, str],
    trait_names: dict[str, str],
    n_full_by_cluster: dict[int, int],
) -> tuple[dict, list[dict]]:
    """`labels` is the FULL combined label array (training subset's final
    labels + early-death boards' post-hoc assignments) — every stat here
    (avg_placement, top4_rate, core_units, common_traits, naming) is
    computed over that full population (n_total). `n_full_by_cluster` is
    the count from the training subset alone, kept as a separate field for
    transparency (see module docstring)."""
    members_by_cluster: dict = {}
    for i, label in enumerate(labels):
        members_by_cluster.setdefault(label, []).append(i)

    real_cluster_ids = [cid for cid in members_by_cluster if cid != OFF_META]
    names = name_clusters(rows, {cid: members_by_cluster[cid] for cid in real_cluster_ids}, unit_names, trait_names)

    comps: dict = {}
    review_rows: list[dict] = []

    for cluster_id in sorted(real_cluster_ids, key=lambda c: -len(members_by_cluster[c])):
        indices = members_by_cluster[cluster_id]
        n_total = len(indices)
        n_full = n_full_by_cluster.get(cluster_id, 0)
        placements = [rows[i]["placement"] for i in indices]
        avg_placement = sum(placements) / n_total
        top4_rate = sum(1 for p in placements if p <= 4) / n_total

        unit_freq = _unit_frequency(rows, indices)
        trait_freq = _trait_frequency(rows, indices)
        top_units = select_core_units(unit_freq, n_total)
        top_traits = sorted(trait_freq.items(), key=lambda kv: -kv[1])[:3]

        comp_id = f"c_{cluster_id:02d}"
        comps[comp_id] = {
            "name": names[cluster_id],
            "centroid": centroids[cluster_id].tolist(),
            "n_full": n_full,
            "n_total": n_total,
            "avg_placement": round(avg_placement, 3),
            "top4_rate": round(top4_rate, 3),
            "core_units": [u for u, _ in top_units],
            "common_traits": [[t, c] for t, c in top_traits],
        }
        review_rows.append(
            {
                "id": comp_id,
                "name": names[cluster_id],
                "n_full": n_full,
                "n_total": n_total,
                "avg_placement": avg_placement,
                "top4_rate": top4_rate,
                "top_units": top_units,
                "top_traits": top_traits,
            }
        )

    if OFF_META in members_by_cluster:
        indices = members_by_cluster[OFF_META]
        n_total = len(indices)
        n_full = n_full_by_cluster.get(OFF_META, 0)
        placements = [rows[i]["placement"] for i in indices]
        avg_placement = sum(placements) / n_total
        top4_rate = sum(1 for p in placements if p <= 4) / n_total
        comps[OFF_META] = {
            "n_full": n_full,
            "n_total": n_total,
            "avg_placement": round(avg_placement, 3),
            "top4_rate": round(top4_rate, 3),
        }
        review_rows.append(
            {
                "id": OFF_META,
                "name": "(off-meta — no coherent comp)",
                "n_full": n_full,
                "n_total": n_total,
                "avg_placement": avg_placement,
                "top4_rate": top4_rate,
                "top_units": [],
                "top_traits": [],
            }
        )

    return comps, review_rows


def format_review_table(review_rows: list[dict], unit_names: dict[str, str], trait_names: dict[str, str]) -> str:
    lines = []
    for row in review_rows:
        lines.append(f"{row['id']}  {row['name']}")
        lines.append(
            f"  n_full={row['n_full']}  n_total={row['n_total']}  "
            f"avg_placement={row['avg_placement']:.2f}  top4_rate={row['top4_rate']:.1%}"
        )
        if row["top_units"]:
            units_str = ", ".join(f"{unit_names.get(u, u)}({c})" for u, c in row["top_units"])
            lines.append(f"  top units: {units_str}")
        if row["top_traits"]:
            traits_str = ", ".join(f"{trait_names.get(t, t)}({c})" for t, c in row["top_traits"])
            lines.append(f"  top traits: {traits_str}")
        lines.append("")
    return "\n".join(lines)


def run(config: dict) -> None:
    ensure_dirs()
    clustering_cfg = config["clustering"]

    rows = _load_rows()
    vectors = np.load(VECTORS_PATH)
    if len(rows) != len(vectors):
        raise SystemExit(f"features_meta.jsonl ({len(rows)} rows) and features_vectors.npy ({len(vectors)} rows) are out of sync — rerun features.py")
    unit_names, trait_names = _load_display_names()

    # Train ONLY on the non-early-death subset (SPEC.md section 8's filter,
    # unchanged) — early-death boards are handled separately below, so every
    # downstream statistic can still cover the full population without
    # ever letting a half-built board influence where a centroid sits.
    training_mask = np.array([not row["early_death"] for row in rows])
    training_vectors = vectors[training_mask]
    n_early_death = int((~training_mask).sum())
    logger.info(
        "Training population: %d/%d boards (%d early-death boards held out of training, assigned post-hoc below)",
        len(training_vectors), len(vectors), n_early_death,
    )

    model, labels, min_cluster_size = fit_hdbscan(training_vectors, clustering_cfg["min_cluster_size_fraction"])
    n_clusters = len(set(labels.tolist()) - {-1})
    n_noise = int((labels == -1).sum())
    logger.info(
        "HDBSCAN: min_cluster_size=%d -> %d clusters, %d/%d training points labeled noise (%.1f%%)",
        min_cluster_size, n_clusters, n_noise, len(labels), 100 * n_noise / len(labels),
    )

    centroids = compute_centroids(training_vectors, labels)
    training_final_labels = reassign_noise(training_vectors, labels, centroids, clustering_cfg["noise_distance_threshold"])

    early_death_vectors = vectors[~training_mask]
    early_death_labels = _nearest_centroid_labels(early_death_vectors, centroids, clustering_cfg["noise_distance_threshold"])
    n_assigned = int(sum(1 for label in early_death_labels if label != OFF_META))
    logger.info(
        "Early-death boards: %d/%d assigned to a real cluster (within distance threshold), %d stayed off-meta",
        n_assigned, n_early_death, n_early_death - n_assigned,
    )

    # n_full: how many TRAINING-subset boards carry each final label — the
    # original, pre-fix meaning of "n". Kept separately in comps.json.
    n_full_by_cluster: dict = {}
    for label in training_final_labels:
        n_full_by_cluster[label] = n_full_by_cluster.get(label, 0) + 1

    # Combine into one label array aligned with `rows`/`vectors`' original
    # (full) row order — this is what every downstream stat/module reads.
    full_labels = np.empty(len(rows), dtype=object)
    full_labels[training_mask] = training_final_labels
    full_labels[~training_mask] = early_death_labels

    comps, review_rows = build_cluster_report(rows, full_labels, centroids, unit_names, trait_names, n_full_by_cluster)

    encoded_labels = np.array(
        [OFF_META_CODE if label == OFF_META else int(label) for label in full_labels], dtype=np.int32
    )
    np.save(CLUSTER_LABELS_PATH, encoded_labels)
    logger.info(
        "Wrote %s (per-participant final cluster label for the FULL population, aligned with features_meta.jsonl; off-meta = %d)",
        CLUSTER_LABELS_PATH, OFF_META_CODE,
    )

    with open(MODEL_PATH, "wb") as f:
        pickle.dump(model, f)
    logger.info("Wrote %s", MODEL_PATH)

    np.savez(CENTROIDS_PATH, **{f"c_{cid:02d}": vec for cid, vec in centroids.items()})
    logger.info("Wrote %s", CENTROIDS_PATH)

    COMPS_PATH.write_text(json.dumps(comps, indent=2), encoding="utf-8")
    logger.info("Wrote %s", COMPS_PATH)

    review_text = format_review_table(review_rows, unit_names, trait_names)
    REVIEW_PATH.write_text(review_text, encoding="utf-8")
    logger.info("Wrote %s", REVIEW_PATH)
    print(review_text)

    if META_JSON_PATH.exists():
        meta = json.loads(META_JSON_PATH.read_text(encoding="utf-8"))
    else:
        meta = {}
    meta["match_threshold"] = clustering_cfg["noise_distance_threshold"]
    meta["clustering_training_board_count"] = int(training_mask.sum())
    META_JSON_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    logger.info("Updated %s with match_threshold and clustering_training_board_count", META_JSON_PATH)


def main() -> None:
    # print(review_text) below can contain names like "Kha'Zix" or "—" —
    # stdout defaults to the OS locale encoding (cp1254 here), which can't
    # represent them and produces invalid UTF-8 the moment output is
    # redirected or piped (same root cause as tests/test_encoding.py).
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    run(config)


if __name__ == "__main__":
    main()
