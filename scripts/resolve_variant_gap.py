"""One-off diagnostic (2026-09-02) — resolves the piece-n inconsistency in
measure_variants.py's output and clarifies the merge-distance-gap location
across mcs values.

The "n tutarsızlığı": measure_variants.py's item 1 reported each mcs=200
piece's FULL population n (train + early-death, whoever's nearest to that
piece's own centroid under mcs=200's own reassignment) — e.g. Kayle-12
n=2541. Its item 4 reported "parçalarda-kalan n=962" for the SAME parent
comp — a different population entirely (training-only points whose
PRODUCTION raw label was Kayle AND stayed non-noise under mcs=200's RAW
fit, restricted to piece ids 11/12 specifically). Both numbers were
individually correct for what they measured, but nothing spelled out that
they were counting different things. This script measures the full
provenance breakdown so the two numbers (and everything between them) are
traceable.

A) Each mcs=200 piece's full population, broken down by each point's
   PRODUCTION FINAL label (cluster_labels.npy — the real comps.json-backing
   19-cluster + off_meta scheme): parent comp / off_meta / other comp(s).
B) The same pieces, training-subset only, broken down by production's RAW
   (pre-reassignment) HDBSCAN training label: parent-raw / raw-noise(-1) /
   other-comp-raw(s) — early-death boards never went through raw HDBSCAN at
   all, reported as their own count, not folded into either bucket.
C) For exactly the raw-noise-in-production points found in (B): where did
   PRODUCTION's OWN reassign_noise() (0.35 threshold, already computed and
   baked into cluster_labels.npy — read directly, NOT recomputed) send them?
   matched (to which comp) vs stayed off_meta.
D) Re-derives the mcs=60/90/120/200 condensed trees with FULL original-
   cluster-membership tracking (not just group size, unlike
   sensitivity_mcs.py's tree printer) so the two merge events immediately
   before/after each run's single largest merge-distance gap can be named
   by their actual constituent clusters, not an anonymous "[grup n=X]" tag.

Read-only against config.yaml/clustering.py/data/processed//models/ — the
only writes are HDBSCAN model pickles cached under this session's scratch
directory (NOT the repo, NOT models/), keyed by mcs value, so a further
follow-up asking about the same mcs values doesn't re-pay the ~45min/fit
cost. Every value here (30/60/90/120/200) already had its determinism
verified bit-for-bit in scripts/sensitivity_mcs.py's run this session, so a
cached fit is equivalent to a fresh one.

Usage: python scripts/resolve_variant_gap.py
"""

from __future__ import annotations

import collections
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import yaml
from sklearn.cluster import HDBSCAN

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.clustering import (
    COMPS_PATH,
    META_ROWS_PATH,
    MODEL_PATH,
    OFF_META,
    PROJECT_ROOT,
    STATIC_UNITS_PATH,
    VECTORS_PATH,
    _nearest_centroid_labels,
    _unit_frequency,
    compute_centroids,
    reassign_noise,
    select_core_units,
)

CONFIG_PATH = PROJECT_ROOT / "config.yaml"
CLUSTER_LABELS_PATH = PROJECT_ROOT / "data" / "processed" / "cluster_labels.npy"
OFF_META_CODE = -2

SCRATCH_DIR = Path(
    r"C:\Users\USER\AppData\Local\Temp\PROJECT"
    r"\428e16f3-dfaf-4fa4-a493-03d35d920af8\scratchpad"
)
CACHE_DIR = SCRATCH_DIR / "hdbscan_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

SPLIT_COMP_IDS = ["c_04", "c_02", "c_17", "c_09", "c_06"]
GAP_MCS_VALUES = [60, 90, 120, 200]


def log(*args):
    print(*args)
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Shared load
# ---------------------------------------------------------------------------

config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
noise_distance_threshold = config["clustering"]["noise_distance_threshold"]

with open(META_ROWS_PATH, encoding="utf-8") as f:
    rows = [json.loads(line) for line in f]
vectors = np.load(VECTORS_PATH)
units_static = json.loads(STATIC_UNITS_PATH.read_text(encoding="utf-8"))
comps = json.loads(COMPS_PATH.read_text(encoding="utf-8"))
prod_full_labels = np.load(CLUSTER_LABELS_PATH)
assert len(prod_full_labels) == len(rows)

with open(MODEL_PATH, "rb") as f:
    prod_model = pickle.load(f)
prod_train_labels = prod_model.labels_

training_mask = np.array([not r["early_death"] for r in rows])
training_vectors = vectors[training_mask]
early_death_vectors = vectors[~training_mask]
assert len(prod_train_labels) == len(training_vectors)

row_to_train_pos = np.full(len(rows), -1, dtype=np.int64)
row_to_train_pos[training_mask] = np.arange(training_vectors.shape[0])


def unit_name(uid: str) -> str:
    return units_static.get(uid, {}).get("name", uid)


def comp_label(int_id_or_code: int) -> str:
    if int_id_or_code == OFF_META_CODE:
        return "off_meta"
    cid = f"c_{int(int_id_or_code):02d}"
    return f"{cid} ({comps[cid]['name']})" if cid in comps else cid


def short_label(core_units: frozenset, n_total: int, k: int = 3) -> str:
    names = sorted(unit_name(u) for u in core_units)[:k]
    suffix = ", ..." if len(core_units) > k else ""
    return f"{{{', '.join(names)}{suffix}}}(n={n_total})"


def full_population_labels(labels_train: np.ndarray, centroids: dict) -> np.ndarray:
    training_final = reassign_noise(training_vectors, labels_train, centroids, noise_distance_threshold)
    early_death_final = _nearest_centroid_labels(early_death_vectors, centroids, noise_distance_threshold)
    full_labels = np.empty(len(rows), dtype=object)
    full_labels[training_mask] = training_final
    full_labels[~training_mask] = early_death_final
    return full_labels


def build_cluster_info(labels_train: np.ndarray) -> dict:
    centroids = compute_centroids(training_vectors, labels_train)
    full_labels = full_population_labels(labels_train, centroids)
    members: dict = {}
    for i, label in enumerate(full_labels):
        if label == OFF_META:
            continue
        members.setdefault(label, []).append(i)
    info = {}
    for cid_int, indices in members.items():
        n_total = len(indices)
        unit_freq = _unit_frequency(rows, indices)
        top_units = select_core_units(unit_freq, n_total)
        info[cid_int] = {"core_units": frozenset(u for u, _ in top_units), "n_total": n_total, "indices": indices}
    return info


def fit_or_load(mcs: int):
    cache_path = CACHE_DIR / f"mcs_{mcs}.pkl"
    if cache_path.exists():
        log(f"mcs={mcs}: cache'ten yüklendi ({cache_path.name}) — determinizm sensitivity_mcs.py'de doğrulandı, yeniden fit edilmedi.")
        with open(cache_path, "rb") as f:
            model, labels = pickle.load(f)
        return model, labels
    log(f"mcs={mcs}: cache yok, fit ediliyor...")
    t0 = time.perf_counter()
    model = HDBSCAN(min_cluster_size=mcs, metric="euclidean", copy=False)
    labels = model.fit_predict(training_vectors)
    dur = time.perf_counter() - t0
    n_clusters = len(set(labels.tolist()) - {-1})
    log(f"  -> {n_clusters} cluster, {dur:.1f}s")
    with open(cache_path, "wb") as f:
        pickle.dump((model, labels), f)
    return model, labels


# ---------------------------------------------------------------------------
# mcs=200 piece definitions (reused from measure_variants.py's method)
# ---------------------------------------------------------------------------

log("=" * 118)
log("mcs=200 PARÇA TANIMLARI")
log("=" * 118)

model200, labels200 = fit_or_load(200)
info200 = build_cluster_info(labels200)

pieces_by_comp: dict = {}
for cid in SPLIT_COMP_IDS:
    pid = int(cid.split("_")[1])
    comp_train_mask = prod_train_labels == pid
    target_counts = collections.Counter(labels200[comp_train_mask].tolist())
    piece_ids = sorted(t for t, c in target_counts.items() if t != -1 and c >= 100)
    pieces_by_comp[cid] = piece_ids
    log(f"{comps[cid]['name']} ({cid}): parçalar (mcs=200 id) = {piece_ids}")

# ---------------------------------------------------------------------------
# A) Full-population provenance by production's FINAL label
# ---------------------------------------------------------------------------

log("\n" + "=" * 118)
log("A) HER PARÇANIN ÜRETİMDEKİ (19 CLUSTER, FİNAL) KÖKEN DAĞILIMI")
log("=" * 118)
log("Her parçanın TAM (train+early-death) popülasyonu, her noktanın üretimin KENDİ final "
    "etiketiyle (cluster_labels.npy — production'ın 0.35 eşikli reassignment'ı sonrası, doğrudan okundu) "
    "sınıflandırılması.\n")

for cid in SPLIT_COMP_IDS:
    parent_pid = int(cid.split("_")[1])
    for pcid in pieces_by_comp[cid]:
        indices = info200[pcid]["indices"]
        n = len(indices)
        origin_counts = collections.Counter(int(prod_full_labels[i]) for i in indices)
        parent_n = origin_counts.get(parent_pid, 0)
        offmeta_n = origin_counts.get(OFF_META_CODE, 0)
        other_n = n - parent_n - offmeta_n
        log(f"{comps[cid]['name']}-{pcid} (n={n}): üretim-{cid} %{100*parent_n/n:.1f} (n={parent_n})  "
            f"üretim-off_meta %{100*offmeta_n/n:.1f} (n={offmeta_n})  diğer %{100*other_n/n:.1f} (n={other_n})")
        if other_n > 0:
            others = sorted(((k, v) for k, v in origin_counts.items() if k not in (parent_pid, OFF_META_CODE)), key=lambda kv: -kv[1])
            for k, v in others:
                log(f"      diğer -> {comp_label(k)}: n={v} (%{100*v/n:.1f})")

# ---------------------------------------------------------------------------
# B) Same pieces, training-only, by production's RAW (pre-reassignment) label
# ---------------------------------------------------------------------------

log("\n" + "=" * 118)
log("B) AYNI PARÇALAR — SADECE EĞİTİM ALT-KÜMESİ, ÜRETİMİN HAM (RAW) HDBSCAN ETİKETİYLE")
log("=" * 118)
log("'Ham noise (-1)' kavramı sadece eğitim popülasyonunda var — early-death board'lar hiç HDBSCAN'den "
    "geçmedi, kendi sayıları ayrı gösteriliyor.\n")

noise_row_idx_by_piece: dict = {}

for cid in SPLIT_COMP_IDS:
    parent_pid = int(cid.split("_")[1])
    for pcid in pieces_by_comp[cid]:
        indices = info200[pcid]["indices"]
        n = len(indices)
        train_idx = [i for i in indices if row_to_train_pos[i] != -1]
        n_train = len(train_idx)
        n_early = n - n_train
        raw_labels = [int(prod_train_labels[row_to_train_pos[i]]) for i in train_idx]
        raw_counts = collections.Counter(raw_labels)
        parent_raw = raw_counts.get(parent_pid, 0)
        noise_raw = raw_counts.get(-1, 0)
        other_raw = n_train - parent_raw - noise_raw
        noise_row_idx_by_piece[(cid, pcid)] = [i for i in train_idx if int(prod_train_labels[row_to_train_pos[i]]) == -1]

        log(f"{comps[cid]['name']}-{pcid} (n={n}, eğitim={n_train}, early-death={n_early}):")
        log(f"    eğitimde: üretim-{cid}-ham %{100*parent_raw/n_train:.1f} (n={parent_raw})  "
            f"üretim-ham-noise(-1) %{100*noise_raw/n_train:.1f} (n={noise_raw})  diğer-ham %{100*other_raw/n_train:.1f} (n={other_raw})")
        if other_raw > 0:
            other_counts = sorted(((k, v) for k, v in raw_counts.items() if k not in (parent_pid, -1)), key=lambda kv: -kv[1])
            for k, v in other_counts[:5]:
                log(f"        diğer-ham -> c_{k:02d}: n={v}")

# ---------------------------------------------------------------------------
# C) Where did production's own raw-noise points (within these pieces) match?
# ---------------------------------------------------------------------------

log("\n" + "=" * 118)
log("C) ÜRETİM-HAM-NOISE NOKTALARI — ÜRETİMİN KENDİ 0.35 EŞİĞİYLE NEREYE EŞLEŞTİ")
log("=" * 118)
log(f"noise_distance_threshold (config.yaml, değiştirilmedi): {noise_distance_threshold}. "
    "Bu, production'ın KENDİ reassign_noise() çağrısının SONUCU (cluster_labels.npy'de zaten var) — "
    "yeniden hesaplanmadı, doğrudan okundu.\n")

for cid in SPLIT_COMP_IDS:
    for pcid in pieces_by_comp[cid]:
        noise_row_idx = noise_row_idx_by_piece[(cid, pcid)]
        n_noise = len(noise_row_idx)
        if n_noise == 0:
            log(f"{comps[cid]['name']}-{pcid}: üretim-ham-noise n=0 — bu parçada üretimin kendi raw noise'undan hiç yok.")
            continue
        final_counts = collections.Counter(int(prod_full_labels[i]) for i in noise_row_idx)
        matched = sum(v for k, v in final_counts.items() if k != OFF_META_CODE)
        unmatched = final_counts.get(OFF_META_CODE, 0)
        log(f"{comps[cid]['name']}-{pcid}: üretim-ham-noise n={n_noise} -> eşleşen %{100*matched/n_noise:.1f} (n={matched}), "
            f"unmatched(off_meta kaldı) %{100*unmatched/n_noise:.1f} (n={unmatched})")
        matched_breakdown = sorted(((k, v) for k, v in final_counts.items() if k != OFF_META_CODE), key=lambda kv: -kv[1])
        for k, v in matched_breakdown[:5]:
            log(f"      eşleşti -> {comp_label(k)}: n={v} (%{100*v/n_noise:.1f})")

# ---------------------------------------------------------------------------
# D) Gap clarification — full original-cluster membership on both sides
# ---------------------------------------------------------------------------

log("\n" + "=" * 118)
log("D) BOŞLUK NETLEŞTİRME — EN BÜYÜK BOŞLUĞUN İKİ YANINDAKİ CLUSTER'LAR (İSİMLERİYLE)")
log("=" * 118)
log("Yöntem sensitivity_mcs.py / measure_variants.py ile aynı (temsilci nokta + union-find), farkı: her "
    "grup TÜM orijinal cluster üyelerini taşıyor (anonim '[grup n=X]' değil) — boşluğun her iki tarafı "
    "TAM olarak hangi orijinal cluster'lardan oluşuyor, isimleriyle gösteriliyor.\n")


def merge_events_full_membership(mcs: int):
    model, labels_train = fit_or_load(mcs)
    info = build_cluster_info(labels_train)
    probabilities = model.probabilities_
    real_cluster_ids = sorted(set(labels_train.tolist()) - {-1})

    representative = {}
    for cid_int in real_cluster_ids:
        idxs = np.where(labels_train == cid_int)[0]
        best = idxs[np.argmax(probabilities[idxs])]
        representative[cid_int] = int(best)

    n_leaves = len(labels_train)
    tree = model._single_linkage_tree_
    parent = list(range(2 * n_leaves - 1))

    def find(x: int) -> int:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    root_members: dict = {}
    for cid_int in real_cluster_ids:
        root_members[representative[cid_int]] = {cid_int}

    events = []
    for i, row in enumerate(tree):
        left, right, dist = int(row["left_node"]), int(row["right_node"]), float(row["value"])
        new_id = n_leaves + i
        rl, rr = find(left), find(right)
        ml, mr = root_members.pop(rl, None), root_members.pop(rr, None)
        if ml and mr:
            events.append((dist, ml, mr))
            root_members[new_id] = ml | mr
        elif ml or mr:
            root_members[new_id] = ml or mr
        parent[rl] = new_id
        parent[rr] = new_id
        parent[new_id] = new_id

    return events, info


def side_label(info: dict, cluster_id_set: set) -> str:
    parts = [short_label(info[cid]["core_units"], info[cid]["n_total"]) for cid in sorted(cluster_id_set)]
    return " + ".join(parts)


for mcs in GAP_MCS_VALUES:
    events, info = merge_events_full_membership(mcs)
    distances = [d for d, _, _ in events]
    gap_list = sorted(((distances[i + 1] - distances[i], i) for i in range(len(distances) - 1)), key=lambda t: -t[0])
    gap_size, gap_idx = gap_list[0]
    before_event = events[gap_idx]
    after_event = events[gap_idx + 1]

    log(f"\n--- mcs={mcs}: en büyük boşluk [{distances[gap_idx]:.4f} -> {distances[gap_idx + 1]:.4f}] (Δ={gap_size:.4f}) ---")
    log(f"  Boşluktan HEMEN ÖNCEKİ birleşme (mesafe={before_event[0]:.4f}):")
    log(f"    Taraf A: {side_label(info, before_event[1])}")
    log(f"    Taraf B: {side_label(info, before_event[2])}")
    log(f"  Boşluktan HEMEN SONRAKİ birleşme (mesafe={after_event[0]:.4f}):")
    log(f"    Taraf A: {side_label(info, after_event[1])}")
    log(f"    Taraf B: {side_label(info, after_event[2])}")

log("\n" + "=" * 118)
log("BİTTİ — bu script config.yaml, clustering.py veya data/processed/, models/ altındaki hiçbir dosyayı "
    "değiştirmedi. HDBSCAN model cache'i sadece scratch dizininde.")
log("=" * 118)
