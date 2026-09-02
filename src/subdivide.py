"""Phase 2: comp subdivision (SPEC.md — pending write-up).

Runs AFTER clustering.py, BEFORE matchups.py/traits.py/items.py/
build_web_data.py. Splits selected phase-1 comps into finer pieces via a
SEPARATE probe HDBSCAN fit — "Option B" from the design discussion this
session: the probe fit runs on the SAME full training population
clustering.py already used (not a comp-local subset — a comp-local fit
would use a completely different, unmeasured relative density threshold,
see the design report), and only the pieces belonging to configured target
comps are kept; everything else (the other real comps, off_meta) passes
through byte-identical to phase 1's own output.

Does NOT touch config.yaml's clustering.min_cluster_size_fraction or
clustering.py's own fit_hdbscan() call — phase 1 is untouched, this is a
strictly additive second pass over its OWN already-written output
(data/processed/comps.json, data/processed/cluster_labels.npy,
models/centroids.npz).

**Piece qualification (C2 criterion, config's clustering.subdivide.
purity_threshold).** A probe HDBSCAN cluster only becomes an official
"piece" of a target comp if BOTH hold:
  1. Dominance: this target comp contributes the MAJORITY (>50%) of that
     probe cluster's own training members (rules out a probe cluster that's
     mostly some OTHER comp with a small contamination from this one).
  2. Purity: after the probe fit's own full-population reassignment
     (train noise + early-death, same noise_distance_threshold as phase 1),
     >= purity_threshold of that probe cluster's FULL population carries
     this target comp's PRODUCTION FINAL label (not the probe fit's own
     raw label — the real, already-decided phase-1 comp membership).
Measured directly before being encoded here (resolve_variant_gap.py's Part
A, this session): the 8 real pieces measured 96.1%-100% purity; a sibling
comp's (Draven-Alev) own weakest incidental mcs=200 sub-piece measured only
69.7% and is correctly excluded (also excluded because it isn't in
target_comps).

**"Artık" (leftover) handling — Option (i), the ONLY scheme applied here.**
For a target comp's OWN points that don't land in one of its own pieces
(the probe fit's raw training noise, or early-death boards which never went
through ANY HDBSCAN fit): nearest-piece cosine distance is computed against
ALL accepted pieces (not just this comp's own — measured to make zero
difference: every single leftover point's nearest piece was from its own
family, 100% across all 6 categories, resolve_variant_gap.py's follow-up
measurement). Within noise_distance_threshold (the SAME 0.35 phase 1 uses
for its own noise/early-death reassignment — not a new number): assigned to
that piece. Beyond it: off_meta. Points belonging to any OTHER comp are
NEVER reassigned into a piece, regardless of geometric proximity — only a
target comp's own members are ever candidates for this step.

**ID scheme.** Retired: the target comps' own c_XX ids (freed, never
reused — a stable id must never silently change meaning). New pieces get
fresh ids continuing past the current max, ordered by target_comps' config
order, then by descending full-population size within each comp (a
deterministic tiebreak that doesn't depend on the probe fit's own arbitrary
raw label numbering).

**Names.** Temporary — Kayle-A/B, Cassiopeia-A/B/C/D, Aphelios-A/B (config
order within each comp, matching the same descending-size ordering as the
id assignment). Real names are a separate follow-up (SPEC.md pending).

Outputs (overwrites phase-1's own, all gitignored — regenerable):
  data/processed/comps.json      16 unchanged comps' entries byte-copied,
                                  target comps' entries replaced by their
                                  pieces, _metadata.avg_placement_tiers
                                  recomputed over the new 24-comp set
  data/processed/cluster_labels.npy   only rows whose phase-1 final label
                                  was a retired comp change value; every
                                  other row's value is byte-identical
  models/centroids.npz            16 unchanged comps' arrays byte-copied,
                                  target comps' arrays replaced by their
                                  pieces' probe centroids
If clustering.subdivide.enabled is false, this is a no-op (logs and exits
without touching any output file) — phase 1's own 19-comp output stands.
"""

from __future__ import annotations

import collections
import json
import logging
import pickle
import sys
from pathlib import Path

import numpy as np
import yaml
from sklearn.cluster import HDBSCAN

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.clustering import (
    CENTROIDS_PATH,
    CLUSTER_LABELS_PATH,
    COMPS_PATH,
    META_ROWS_PATH,
    MODEL_PATH,
    OFF_META,
    OFF_META_CODE,
    VECTORS_PATH,
    _load_display_names,
    _nearest_centroid_labels,
    build_cluster_report,
    compute_avg_placement_tiers,
    compute_centroids,
    reassign_noise,
)
from src.paths import PROJECT_ROOT, ensure_dirs

logger = logging.getLogger(__name__)

CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def _load_rows() -> list[dict]:
    with open(META_ROWS_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def match_target_comps(comps: dict, target_comps_cfg: list[dict]) -> dict[str, dict]:
    """core_units SET match (order-independent, robust to a rerun's
    frequency-tie reordering) — raw c_XX ids are never used as the key,
    per SPEC.md's established "meaningless across runs" finding."""
    matched: dict[str, dict] = {}
    for target in target_comps_cfg:
        target_units = set(target["core_units"])
        found = None
        for cid, info in comps.items():
            if cid in (OFF_META, "_metadata"):
                continue
            if set(info["core_units"]) == target_units:
                found = cid
                break
        if found is None:
            raise SystemExit(
                f"subdivide.py: config's target_comps entry {target['name']!r} "
                f"(core_units={sorted(target_units)}) matches no comp in comps.json — "
                "data drifted since this config was written, stop and re-measure before subdividing"
            )
        if comps[found]["name"] != target["name"]:
            logger.warning(
                "subdivide.py: %s matched by core_units but name differs (config: %r, comps.json: %r) — "
                "core_units is the real key, this is just a heads-up",
                found, target["name"], comps[found]["name"],
            )
        matched[found] = target
    return matched


def run(config: dict) -> None:
    ensure_dirs()
    subdivide_cfg = config["clustering"].get("subdivide")
    if not subdivide_cfg or not subdivide_cfg.get("enabled"):
        logger.info("subdivide.py: clustering.subdivide disabled or absent — no-op, phase 1's output stands untouched.")
        return

    probe_mcs = subdivide_cfg["probe_min_cluster_size"]
    purity_threshold = subdivide_cfg["purity_threshold"]
    noise_distance_threshold = config["clustering"]["noise_distance_threshold"]

    comps = json.loads(COMPS_PATH.read_text(encoding="utf-8"))
    matched = match_target_comps(comps, subdivide_cfg["target_comps"])
    target_pid_by_cid = {cid: int(cid.split("_")[1]) for cid in matched}
    logger.info("Target comps matched: %s", {cid: comps[cid]["name"] for cid in matched})

    rows = _load_rows()
    vectors = np.load(VECTORS_PATH)
    unit_names, trait_names = _load_display_names()

    phase1_full_labels = np.load(CLUSTER_LABELS_PATH)
    if len(phase1_full_labels) != len(rows):
        raise SystemExit("subdivide.py: cluster_labels.npy / features_meta.jsonl row count mismatch — rerun clustering.py")

    with open(MODEL_PATH, "rb") as f:
        phase1_model = pickle.load(f)
    phase1_train_labels = phase1_model.labels_  # raw, pre-reassignment, aligned with training_vectors

    training_mask = np.array([not r["early_death"] for r in rows])
    training_vectors = vectors[training_mask]
    early_death_vectors = vectors[~training_mask]
    if len(phase1_train_labels) != len(training_vectors):
        raise SystemExit("subdivide.py: hdbscan_model.pkl / current training population size mismatch — rerun clustering.py")
    row_to_train_pos = np.full(len(rows), -1, dtype=np.int64)
    row_to_train_pos[training_mask] = np.arange(training_vectors.shape[0])

    # --- probe fit (the ONE new HDBSCAN call this module makes) ---
    logger.info("Probe fit: HDBSCAN(min_cluster_size=%d, metric=euclidean) on the %d-point training population...", probe_mcs, len(training_vectors))
    probe_model = HDBSCAN(min_cluster_size=probe_mcs, metric="euclidean", copy=False)
    probe_train_labels = probe_model.fit_predict(training_vectors)
    n_probe_clusters = len(set(probe_train_labels.tolist()) - {-1})
    logger.info("Probe fit: %d clusters found.", n_probe_clusters)

    probe_centroids = compute_centroids(training_vectors, probe_train_labels)
    probe_training_final = reassign_noise(training_vectors, probe_train_labels, probe_centroids, noise_distance_threshold)
    probe_early_death_final = _nearest_centroid_labels(early_death_vectors, probe_centroids, noise_distance_threshold)
    probe_full_labels = np.empty(len(rows), dtype=object)
    probe_full_labels[training_mask] = probe_training_final
    probe_full_labels[~training_mask] = probe_early_death_final

    probe_cluster_total_size = collections.Counter(probe_train_labels[probe_train_labels != -1].tolist())

    # --- C2: dominance + purity ---
    pieces_by_comp: dict[str, list[int]] = {}
    for cid, pid in target_pid_by_cid.items():
        comp_train_mask = phase1_train_labels == pid
        contribution = collections.Counter(probe_train_labels[comp_train_mask].tolist())
        candidates = [
            raw_id for raw_id, c in contribution.items()
            if raw_id != -1 and c > 0.5 * probe_cluster_total_size[raw_id]
        ]
        accepted = []
        for raw_id in candidates:
            member_idx = np.where(probe_full_labels == raw_id)[0]
            purity = float(np.mean(phase1_full_labels[member_idx] == pid))
            if purity >= purity_threshold:
                accepted.append(raw_id)
            else:
                logger.info("subdivide.py: %s candidate probe cluster %d rejected — purity %.3f < %.3f", cid, raw_id, purity, purity_threshold)
        pieces_by_comp[cid] = sorted(accepted)
        logger.info("%s (%s): %d piece(s) accepted (probe raw ids %s)", comps[cid]["name"], cid, len(accepted), pieces_by_comp[cid])

    if not any(pieces_by_comp.values()):
        raise SystemExit("subdivide.py: no comp qualified for subdivision under the current C2 threshold — nothing to do, stop and check config/data")

    # --- id assignment: config order, then descending full-population size ---
    all_piece_ids: list[int] = []
    for cid in matched:
        all_piece_ids.extend(pieces_by_comp[cid])
    piece_full_size = {pid: int(np.sum(probe_full_labels == pid)) for pid in all_piece_ids}

    next_id = max(int(c.split("_")[1]) for c in comps if c.startswith("c_")) + 1
    new_id_of: dict[int, int] = {}
    temp_name_of: dict[int, str] = {}
    letters = "ABCDEFGH"
    for cid in matched:
        ordered = sorted(pieces_by_comp[cid], key=lambda pid: -piece_full_size[pid])
        base_label = comps[cid]["name"].split(" — ")[0]  # e.g. "Kayle", "Cassiopeia", "Aphelios"
        for i, raw_id in enumerate(ordered):
            new_id_of[raw_id] = next_id
            temp_name_of[raw_id] = f"{base_label}-{letters[i]}"
            next_id += 1

    logger.info("New id assignment: %s", {f"c_{new_id_of[r]:02d}": temp_name_of[r] for r in all_piece_ids})

    # --- scoped final population per piece (Option i) ---
    piece_id_set = set(all_piece_ids)
    piece_centroid_list = [(pid, probe_centroids[pid]) for pid in all_piece_ids]

    def nearest_piece(vec: np.ndarray) -> tuple[int, float]:
        best_pid, best_dist = -1, 2.0
        for pid, centroid in piece_centroid_list:
            d = 1.0 - float(np.dot(vec, centroid))
            if d < best_dist:
                best_dist, best_pid = d, pid
        return best_pid, best_dist

    # new_full_labels uses build_cluster_report's own convention throughout
    # (object dtype: an int cluster id, or the STRING OFF_META) — matches
    # exactly how clustering.py's own `full_labels` is shaped internally.
    # OFF_META_CODE (the int -2) is only used at the very end, encoding this
    # array for cluster_labels.npy — same split clustering.py's run() makes.
    new_full_labels = np.empty(len(rows), dtype=object)
    for i in range(len(rows)):
        old = phase1_full_labels[i]
        if old == OFF_META_CODE:
            new_full_labels[i] = OFF_META
            continue
        old_cid = f"c_{int(old):02d}"
        if old_cid not in matched:
            new_full_labels[i] = int(old)  # unchanged — byte-identical to phase 1
            continue
        tp = row_to_train_pos[i]
        if tp != -1 and int(probe_train_labels[tp]) in piece_id_set:
            new_full_labels[i] = new_id_of[int(probe_train_labels[tp])]
            continue
        best_pid, best_dist = nearest_piece(vectors[i])
        if best_dist <= noise_distance_threshold:
            new_full_labels[i] = new_id_of[best_pid]
        else:
            new_full_labels[i] = OFF_META

    # --- recompute ALL 24 real comps + off_meta via clustering.py's own report
    # builder, over new_full_labels in full (not just the 8 pieces) — this is
    # deliberate, not just convenient: off_meta's own population CHANGES (it
    # gains whichever "artık" points missed the 0.35 threshold above), so its
    # n_total/avg_placement/top4_rate must be recomputed too, not byte-copied
    # from phase 1. Recomputing the 16 UNCHANGED comps the same way (instead
    # of a blind byte-copy) is a deliberate cross-check: their result MUST
    # equal phase 1's own stored values exactly, verified below before
    # anything is written — if it doesn't, something about this module's
    # understanding of phase 1's own computation is wrong and writing output
    # would silently corrupt data.
    all_centroids_dict: dict = {}
    for cid, info in comps.items():
        if cid in matched or cid in (OFF_META, "_metadata"):
            continue
        all_centroids_dict[int(cid.split("_")[1])] = np.array(info["centroid"])
    for raw_id in all_piece_ids:
        all_centroids_dict[new_id_of[raw_id]] = probe_centroids[raw_id]

    n_full_by_cluster: dict = collections.defaultdict(int)
    for i in np.where(training_mask)[0]:
        n_full_by_cluster[new_full_labels[i]] += 1

    recomputed_comps, _ = build_cluster_report(rows, new_full_labels, all_centroids_dict, unit_names, trait_names, dict(n_full_by_cluster))

    for cid, info in comps.items():
        if cid in matched or cid in (OFF_META, "_metadata"):
            continue
        if recomputed_comps.get(cid) != info:
            raise SystemExit(
                f"subdivide.py: recomputed {cid} does not byte-match phase 1's own stored comps.json entry — "
                "stopping before any file is written. This means subdivide.py's understanding of "
                "build_cluster_report's inputs doesn't actually reproduce phase 1's own computation; "
                "investigate before proceeding, do not silently overwrite."
            )
    logger.info("Cross-check passed: all %d unchanged comps recomputed byte-identical to phase 1's stored comps.json.", len(comps) - len(matched) - 2)

    final_comps: dict = {}
    for cid in comps:
        if cid in matched or cid == "_metadata":
            continue
        final_comps[cid] = recomputed_comps[cid]
    for raw_id in all_piece_ids:
        new_cid = f"c_{new_id_of[raw_id]:02d}"
        entry = recomputed_comps[new_cid]
        entry["name"] = temp_name_of[raw_id]  # temporary — real names are a separate follow-up
        final_comps[new_cid] = entry

    final_comps["_metadata"] = {"avg_placement_tiers": compute_avg_placement_tiers(final_comps)}

    COMPS_PATH.write_text(json.dumps(final_comps, indent=2), encoding="utf-8")
    logger.info("Wrote %s (%d comps: %d unchanged + %d new pieces)", COMPS_PATH, len(final_comps) - 1, len(comps) - len(matched) - 1, len(all_piece_ids))

    encoded_labels = np.array([OFF_META_CODE if v == OFF_META else int(v) for v in new_full_labels], dtype=np.int32)
    np.save(CLUSTER_LABELS_PATH, encoded_labels)
    logger.info("Wrote %s", CLUSTER_LABELS_PATH)

    old_centroids = {key: np.asarray(value) for key, value in np.load(CENTROIDS_PATH).items()}
    new_centroids = {k: v for k, v in old_centroids.items() if k not in matched}
    for raw_id in all_piece_ids:
        new_centroids[f"c_{new_id_of[raw_id]:02d}"] = probe_centroids[raw_id]
    np.savez(CENTROIDS_PATH, **new_centroids)
    logger.info("Wrote %s (%d entries: %d unchanged + %d new pieces)", CENTROIDS_PATH, len(new_centroids), len(old_centroids) - len(matched), len(all_piece_ids))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    run(config)


if __name__ == "__main__":
    main()
