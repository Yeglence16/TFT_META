"""One-off diagnostic (2026-09-01) — variant-layer measurement.

Follow-up to scripts/sensitivity_mcs.py: at min_cluster_size=200, 5 of the
19 production comps split into 2+ sub-pieces (Cassiopeia — Savunucu x4,
Kayle — Bombardımancı x2, Aphelios — Öncü x2, Draven — Alev x2,
Tristana — Fey x2 — verified against data/processed/comps.json this
session: c_04, c_02, c_17, c_09, c_06 respectively). This script measures
whether those pieces are statistically/behaviorally distinct ("real
variants") or a cosmetic artifact of a stricter min_cluster_size, and
whether the low-density-then-gap pattern seen in mcs=200's condensed tree
recurs at other tested mcs values.

Read-only / in-memory only. Does NOT modify config.yaml or clustering.py,
does NOT write to data/processed/ or models/. Refits HDBSCAN at
mcs in {30, 60, 90, 120, 200} (needed: 200 for piece definitions, 30 for the
noise-structure check, 60/90/120 for the merge-distance-gap check) — each
value's determinism was already verified bit-for-bit in
scripts/sensitivity_mcs.py's run (2026-09-01), so this script fits each
value only once, not twice.

Usage: python scripts/measure_variants.py
"""

from __future__ import annotations

import collections
import json
import math
import pickle
import statistics
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
    STATIC_TRAITS_PATH,
    STATIC_UNITS_PATH,
    VECTORS_PATH,
    _nearest_centroid_labels,
    _unit_frequency,
    compute_centroids,
    reassign_noise,
    select_core_units,
)
from src.matchups import MIN_N, wilson_ci

CONFIG_PATH = PROJECT_ROOT / "config.yaml"
CLUSTER_LABELS_PATH = PROJECT_ROOT / "data" / "processed" / "cluster_labels.npy"
META_JSON_PATH = PROJECT_ROOT / "data" / "processed" / "meta.json"
OFF_META_CODE = -2

# Verified against data/processed/comps.json this session (grep, not assumed):
# the 5 production comps that split into 2+ non-noise pieces under mcs=200.
SPLIT_COMP_IDS = ["c_04", "c_02", "c_17", "c_09", "c_06"]

REFIT_VALUES = [30, 60, 90, 120, 200]


def log(*args):
    print(*args)
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Shared load — same inputs sensitivity_mcs.py / diag_taric.py read.
# ---------------------------------------------------------------------------

config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
noise_distance_threshold = config["clustering"]["noise_distance_threshold"]

with open(META_ROWS_PATH, encoding="utf-8") as f:
    rows = [json.loads(line) for line in f]
vectors = np.load(VECTORS_PATH)
units_static = json.loads(STATIC_UNITS_PATH.read_text(encoding="utf-8"))
traits_static = json.loads(STATIC_TRAITS_PATH.read_text(encoding="utf-8"))
meta = json.loads(META_JSON_PATH.read_text(encoding="utf-8"))
unit_order = meta["unit_order"]
trait_order = meta["trait_order"]
n_unit_dims = len(unit_order)
dim_ids = unit_order + trait_order


def unit_name(uid: str) -> str:
    return units_static.get(uid, {}).get("name", uid)


def dim_label(idx: int) -> str:
    fid = dim_ids[idx]
    if idx < n_unit_dims:
        return f"unit:{unit_name(fid)}"
    return f"trait:{traits_static.get(fid, {}).get('name', fid)}"


comps = json.loads(COMPS_PATH.read_text(encoding="utf-8"))
prod_full_labels = np.load(CLUSTER_LABELS_PATH)  # aligned w/ rows, int cluster id or OFF_META_CODE
assert len(prod_full_labels) == len(rows), "cluster_labels.npy / features_meta.jsonl hizası bozuk"

with open(MODEL_PATH, "rb") as f:
    prod_model = pickle.load(f)
prod_train_labels = prod_model.labels_  # RAW production HDBSCAN training labels (mcs=299, pre-reassignment)

training_mask = np.array([not r["early_death"] for r in rows])
training_vectors = vectors[training_mask]
early_death_vectors = vectors[~training_mask]
training_row_indices = np.where(training_mask)[0]  # training-position -> original row index
assert len(prod_train_labels) == len(training_vectors), "üretim modeli / mevcut eğitim verisi hizası bozuk"

log(f"Split comp'lar (sensitivity_mcs.py'nin mcs=200 koşusunda 2+ parçaya bölünen 5 üretim cluster'ı):")
for cid in SPLIT_COMP_IDS:
    log(f"  {cid}: {comps[cid]['name']}  (n_total={comps[cid]['n_total']}, avg_placement={comps[cid]['avg_placement']}, "
        f"top4_rate={comps[cid]['top4_rate']}, core_units={[unit_name(u) for u in comps[cid]['core_units']]})")
log("")

# ---------------------------------------------------------------------------
# Refit needed mcs values (once each — determinism already verified in
# sensitivity_mcs.py's run for all of these).
# ---------------------------------------------------------------------------

fits = {}
for mcs in REFIT_VALUES:
    log(f"mcs={mcs} fit ediliyor...")
    t0 = time.perf_counter()
    model = HDBSCAN(min_cluster_size=mcs, metric="euclidean", copy=False)
    labels = model.fit_predict(training_vectors)
    dur = time.perf_counter() - t0
    n_clusters = len(set(labels.tolist()) - {-1})
    fits[mcs] = {"model": model, "labels": labels}
    log(f"  -> {n_clusters} cluster, {dur:.1f}s")

log("")


def full_population_labels(labels_train: np.ndarray, centroids: dict) -> np.ndarray:
    training_final = reassign_noise(training_vectors, labels_train, centroids, noise_distance_threshold)
    early_death_final = _nearest_centroid_labels(early_death_vectors, centroids, noise_distance_threshold)
    full_labels = np.empty(len(rows), dtype=object)
    full_labels[training_mask] = training_final
    full_labels[~training_mask] = early_death_final
    return full_labels


def build_cluster_info(labels_train: np.ndarray) -> tuple[dict, np.ndarray, dict]:
    """Returns (centroids, full_labels, info) where info[cluster_id] holds
    core_units/n_total/indices — indices are ROW-space (aligned with `rows`),
    safe to pass straight into _unit_frequency or rows[i]["placement"]."""
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
        info[cid_int] = {
            "core_units": frozenset(u for u, _ in top_units),
            "n_total": n_total,
            "indices": indices,
        }
    return centroids, full_labels, info


def short_label(core_units: frozenset, n_total: int, k: int = 3) -> str:
    names = sorted(unit_name(u) for u in core_units)[:k]
    suffix = ", ..." if len(core_units) > k else ""
    return f"{{{', '.join(names)}{suffix}}}(n={n_total})"


log("Full-population cluster_info hesaplanıyor (her refit için: noise reassignment + early-death ataması)...")
cluster_info_by_mcs = {}
centroids_by_mcs = {}
for mcs in REFIT_VALUES:
    centroids, full_labels, info = build_cluster_info(fits[mcs]["labels"])
    centroids_by_mcs[mcs] = centroids
    cluster_info_by_mcs[mcs] = info
    fits[mcs]["full_labels"] = full_labels
log("Bitti.\n")

info200 = cluster_info_by_mcs[200]
centroids200 = centroids_by_mcs[200]

# ---------------------------------------------------------------------------
# Pieces + noise per split comp (raw training-level, matches the percentages
# already reported in the sensitivity_mcs.py output: e.g. Kayle %82.6).
# ---------------------------------------------------------------------------

pieces_by_comp: dict = {}
noise_train_positions_by_comp: dict = {}
comp_train_mask_by_comp: dict = {}

for cid in SPLIT_COMP_IDS:
    pid = int(cid.split("_")[1])
    comp_train_mask = prod_train_labels == pid
    comp_train_mask_by_comp[cid] = comp_train_mask
    n_comp_train = int(comp_train_mask.sum())
    target_counts = collections.Counter(fits[200]["labels"][comp_train_mask].tolist())
    piece_ids = sorted(t for t, c in target_counts.items() if t != -1 and c >= 100)
    pieces_by_comp[cid] = piece_ids
    noise_positions = np.where(comp_train_mask & (fits[200]["labels"] == -1))[0]
    noise_train_positions_by_comp[cid] = noise_positions
    noise_pct = 100 * len(noise_positions) / n_comp_train
    log(f"{comps[cid]['name']} ({cid}): eğitimde n={n_comp_train}, {len(piece_ids)} parça "
        f"(mcs=200 raw id'leri {piece_ids}), ham noise n={len(noise_positions)} (%{noise_pct:.1f})")

log("")

# ---------------------------------------------------------------------------
# 1) Her parça için: n, avg_placement + %95 CI, top4_rate, ayırt edici dimension'lar
# ---------------------------------------------------------------------------

log("=" * 118)
log("1) PARÇA İSTATİSTİKLERİ")
log("=" * 118)
log("avg_placement %95 CI: normal yaklaşıklık (mean ± 1.96*sd/sqrt(n)) — top4_rate CI: Wilson score "
    f"(src.matchups.wilson_ci, aynı proje-standardı fonksiyon). MIN_N={MIN_N} (matchups.py'den).\n")


def piece_stats(indices: list) -> dict:
    placements = [rows[i]["placement"] for i in indices]
    n = len(placements)
    avg = statistics.mean(placements)
    sd = statistics.stdev(placements) if n > 1 else float("nan")
    sem = sd / math.sqrt(n) if n > 1 else float("nan")
    ci = (avg - 1.96 * sem, avg + 1.96 * sem) if n > 1 else (avg, avg)
    top4 = sum(1 for p in placements if p <= 4)
    top4_rate = top4 / n
    w_ci = wilson_ci(top4, n)
    return {"n": n, "avg": avg, "sd": sd, "ci": ci, "top4_rate": top4_rate, "top4_ci": w_ci}


piece_stats_by_key: dict = {}  # (cid, piece_id) -> stats dict

for cid in SPLIT_COMP_IDS:
    log(f"--- {comps[cid]['name']} ({cid}) — üretimde: n_total={comps[cid]['n_total']}, "
        f"avg_placement={comps[cid]['avg_placement']}, top4_rate={comps[cid]['top4_rate']} ---")
    for pcid in pieces_by_comp[cid]:
        info = info200[pcid]
        stats = piece_stats(info["indices"])
        piece_stats_by_key[(cid, pcid)] = stats
        names = sorted(unit_name(u) for u in info["core_units"])
        log(f"  Parça (mcs=200 id={pcid}): n={stats['n']}  avg_placement={stats['avg']:.3f} "
            f"CI95=[{stats['ci'][0]:.3f}, {stats['ci'][1]:.3f}]  sd={stats['sd']:.3f}  "
            f"top4_rate={stats['top4_rate']:.3f} CI95=[{stats['top4_ci'][0]:.3f}, {stats['top4_ci'][1]:.3f}]")
        log(f"    core_units: {names}")
    log("")

# --- dimension diff between pieces of the same comp ---

log("Parçaları ayıran dimension'lar (centroid_A - centroid_B, |fark| ile sıralı ilk 10):\n")

for cid in SPLIT_COMP_IDS:
    pids = pieces_by_comp[cid]
    if len(pids) < 2:
        continue
    for a, b in [(pids[i], pids[j]) for i in range(len(pids)) for j in range(i + 1, len(pids))]:
        va, vb = centroids200[a], centroids200[b]
        diff = va - vb
        order = np.argsort(-np.abs(diff))[:10]
        na, nb = short_label(info200[a]["core_units"], info200[a]["n_total"]), short_label(info200[b]["core_units"], info200[b]["n_total"])
        log(f"--- {comps[cid]['name']}: parça {a} {na}  vs  parça {b} {nb} ---")
        for idx in order:
            log(f"    {dim_label(idx):28} a={va[idx]:.3f}  b={vb[idx]:.3f}  fark(a-b)={diff[idx]:+.4f}")
        log("")

# ---------------------------------------------------------------------------
# 2) İstatistiksel anlamlılık — CI çakışması + iki-örneklem z-testi
# ---------------------------------------------------------------------------

log("=" * 118)
log("2) PARÇALAR ARASI avg_placement FARKI — CI ÇAKIŞMASI + Z-TESTİ")
log("=" * 118)


def ci_overlap(ci1, ci2) -> bool:
    return not (ci1[1] < ci2[0] or ci2[1] < ci1[0])


def two_sample_z(s1, s2):
    se = math.sqrt((s1["sd"] ** 2) / s1["n"] + (s2["sd"] ** 2) / s2["n"])
    if se == 0:
        return float("nan"), float("nan")
    z = (s1["avg"] - s2["avg"]) / se
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return z, p


for cid in SPLIT_COMP_IDS:
    pids = pieces_by_comp[cid]
    if len(pids) < 2:
        continue
    log(f"--- {comps[cid]['name']} ---")
    for a, b in [(pids[i], pids[j]) for i in range(len(pids)) for j in range(i + 1, len(pids))]:
        sa, sb = piece_stats_by_key[(cid, a)], piece_stats_by_key[(cid, b)]
        overlap = ci_overlap(sa["ci"], sb["ci"])
        z, p = two_sample_z(sa, sb)
        log(f"  parça {a} (avg={sa['avg']:.3f}, CI=[{sa['ci'][0]:.3f},{sa['ci'][1]:.3f}])  vs  "
            f"parça {b} (avg={sb['avg']:.3f}, CI=[{sb['ci'][0]:.3f},{sb['ci'][1]:.3f}])  "
            f"-> CI çakışıyor: {'EVET' if overlap else 'HAYIR'}, z={z:.3f}, p={p:.4g}")
    log("")

# ---------------------------------------------------------------------------
# 3) Head-to-head profilleri — parça vs aynı rakip production comp'ları
# ---------------------------------------------------------------------------

log("=" * 118)
log("3) HEAD-TO-HEAD — PARÇALAR AYNI RAKİPLERE KARŞI FARKLI MI")
log("=" * 118)
log(f"Yöntem matchups.py ile aynı (aynı maçtaki katılımcı çiftleri, wilson_ci, MIN_N={MIN_N} altı 'yetersiz veri'). "
    "'Rakip' tarafı her zaman üretimin 19 comp'luk şeması (cluster_labels.npy); 'biz' tarafı sadece bu 5 comp'un "
    "parçaları için piece-bazlı, geri kalan her şey üretim etiketiyle kalıyor.\n")

us_labels = [None] * len(rows)
for cid in SPLIT_COMP_IDS:
    for pcid in pieces_by_comp[cid]:
        for i in info200[pcid]["indices"]:
            us_labels[i] = f"piece:{cid}:{pcid}"
for i in range(len(rows)):
    if us_labels[i] is None:
        us_labels[i] = "off_meta" if prod_full_labels[i] == OFF_META_CODE else f"c_{int(prod_full_labels[i]):02d}"

by_match: dict = collections.defaultdict(list)
for i, row in enumerate(rows):
    if us_labels[i] == "off_meta":
        continue
    by_match[row["match_id"]].append((us_labels[i], row["placement"]))

pair_counts: dict = collections.defaultdict(lambda: [0, 0])  # (a,b) -> [n, a_above_b]
for participants in by_match.values():
    for i in range(len(participants)):
        a_label, a_place = participants[i]
        if not a_label.startswith("piece:"):
            continue
        for j in range(len(participants)):
            if i == j:
                continue
            b_label, b_place = participants[j]
            pair_counts[(a_label, b_label)][0] += 1
            if a_place < b_place:
                pair_counts[(a_label, b_label)][1] += 1

for cid in SPLIT_COMP_IDS:
    pids = pieces_by_comp[cid]
    if len(pids) < 2:
        continue
    log(f"--- {comps[cid]['name']} — parçalar rakip comp'lara karşı ---")
    opponents = [c for c in comps if c not in ("_metadata", OFF_META) and c != cid]
    header = f"{'rakip':22}" + "".join(f"parça_{p:>10}" for p in pids)
    log(header)
    for opp in opponents:
        cells = []
        any_sufficient = False
        for pcid in pids:
            key = (f"piece:{cid}:{pcid}", opp)
            n, above = pair_counts.get(key, [0, 0])
            if n < MIN_N:
                cells.append(f"n={n}(yetersiz)")
            else:
                rate = above / n
                lo, hi = wilson_ci(above, n)
                cells.append(f"{rate:.3f}[{lo:.3f},{hi:.3f}]n={n}")
                any_sufficient = True
        if any_sufficient:
            log(f"{opp:22}" + "".join(f"{c:>22}" for c in cells))
    log("")

# ---------------------------------------------------------------------------
# 4) Gürültüye dağılan parça — rastgele mi, yapılı mı
# ---------------------------------------------------------------------------

log("=" * 118)
log("4) GÜRÜLTÜYE DAĞILAN KISIM — YAPI VAR MI (mcs=30 ile çapraz kontrol)")
log("=" * 118)

for cid in SPLIT_COMP_IDS:
    noise_pos = noise_train_positions_by_comp[cid]
    comp_train_mask = comp_train_mask_by_comp[cid]
    non_noise_pos = np.where(comp_train_mask & (fits[200]["labels"] != -1))[0]
    if len(noise_pos) == 0:
        continue

    noise_rows = training_row_indices[noise_pos]
    non_noise_rows = training_row_indices[non_noise_pos]

    noise_stats = piece_stats(noise_rows.tolist())
    non_noise_stats = piece_stats(non_noise_rows.tolist())

    prod_centroid = np.array(comps[cid]["centroid"])
    noise_dist = 1.0 - training_vectors[noise_pos] @ prod_centroid
    non_noise_dist = 1.0 - training_vectors[non_noise_pos] @ prod_centroid

    log(f"--- {comps[cid]['name']} ({cid}) — gürültü n={len(noise_pos)} vs parçalarda-kalan n={len(non_noise_pos)} ---")
    log(f"  avg_placement: gürültü={noise_stats['avg']:.3f} CI=[{noise_stats['ci'][0]:.3f},{noise_stats['ci'][1]:.3f}]  "
        f"vs parçalarda-kalan={non_noise_stats['avg']:.3f} CI=[{non_noise_stats['ci'][0]:.3f},{non_noise_stats['ci'][1]:.3f}]")
    log(f"  top4_rate: gürültü={noise_stats['top4_rate']:.3f}  vs parçalarda-kalan={non_noise_stats['top4_rate']:.3f}")
    log(f"  üretim centroid'ine cosine mesafe: gürültü mean={float(noise_dist.mean()):.4f} median={float(np.median(noise_dist)):.4f}  "
        f"vs parçalarda-kalan mean={float(non_noise_dist.mean()):.4f} median={float(np.median(non_noise_dist)):.4f}")

    mcs30_breakdown = collections.Counter(fits[30]["labels"][noise_pos].tolist())
    total = len(noise_pos)
    log(f"  mcs=30'da (en ince test edilen granülerlik) bu {total} noktanın dağılımı (ilk 8, %'ye göre sıralı):")
    for label30, cnt in sorted(mcs30_breakdown.items(), key=lambda kv: -kv[1])[:8]:
        pct = 100 * cnt / total
        if label30 == -1:
            tag = "GÜRÜLTÜ (mcs=30'da da noise kaldı)"
        else:
            info30 = cluster_info_by_mcs[30].get(label30)
            tag = short_label(info30["core_units"], info30["n_total"]) if info30 else f"[full-pop'ta yok, train_id={label30}]"
        log(f"      {tag}  {cnt}/{total} (%{pct:.1f})")
    log("")

# ---------------------------------------------------------------------------
# 5) 0.21-0.43 mesafe boşluğu diğer mcs değerlerinde var mı
# ---------------------------------------------------------------------------

log("=" * 118)
log("5) BİRLEŞME-MESAFESİ BOŞLUĞU — mcs=60/90/120/200 KARŞILAŞTIRMASI")
log("=" * 118)
log("Yöntem: her run'ın kendi _single_linkage_tree_'i üzerinde, her cluster'ın probabilities_'i en yüksek "
    "temsilci noktası ile union-find birleşme sırası (sensitivity_mcs.py ile aynı yöntem). Ardışık birleşme "
    "mesafeleri arasındaki en büyük 3 boşluk raporlanıyor.\n")


def condensed_merge_distances(mcs: int) -> list[float]:
    labels_train = fits[mcs]["labels"]
    probabilities = fits[mcs]["model"].probabilities_
    real_cluster_ids = sorted(set(labels_train.tolist()) - {-1})

    representative = {}
    for cid_int in real_cluster_ids:
        idxs = np.where(labels_train == cid_int)[0]
        best = idxs[np.argmax(probabilities[idxs])]
        representative[cid_int] = int(best)

    n_leaves = len(labels_train)
    tree = fits[mcs]["model"]._single_linkage_tree_
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

    distances = []
    for i, row in enumerate(tree):
        left, right, dist = int(row["left_node"]), int(row["right_node"]), float(row["value"])
        new_id = n_leaves + i
        rl, rr = find(left), find(right)
        ml, mr = root_members.pop(rl, None), root_members.pop(rr, None)
        if ml and mr:
            distances.append(dist)
            root_members[new_id] = ml | mr
        elif ml or mr:
            root_members[new_id] = ml or mr
        parent[rl] = new_id
        parent[rr] = new_id
        parent[new_id] = new_id

    return distances


for mcs in [60, 90, 120, 200]:
    distances = condensed_merge_distances(mcs)
    n = len(distances)
    log(f"--- mcs={mcs}: {n} birleşme mesafesi (sıralı) ---")
    log("  " + ", ".join(f"{d:.4f}" for d in distances))
    gaps = sorted(
        [(distances[i + 1] - distances[i], distances[i], distances[i + 1]) for i in range(n - 1)],
        key=lambda t: -t[0],
    )
    log("  En büyük 3 boşluk:")
    for gap, lo, hi in gaps[:3]:
        log(f"    [{lo:.4f} -> {hi:.4f}]  boşluk={gap:.4f}")
    log("")

log("=" * 118)
log("BİTTİ — bu script config.yaml, clustering.py veya data/processed/, models/ altındaki hiçbir dosyayı değiştirmedi.")
log("=" * 118)
