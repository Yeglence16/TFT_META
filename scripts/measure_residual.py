"""One-off diagnostic (2026-09-02) — closes a gap before implementing
Option B (filtered global mcs=200 fit) for the Kayle/Cassiopeia/Aphelios-
Öncü subdivision plan: measures, from the PRODUCTION comp's own point of
view (not the piece's), how many of its current members actually land in a
piece vs. become "artık" (leftover) — and quantifies 3 candidate ways to
handle the leftover.

Different question from the last two scripts' output: measure_variants.py /
resolve_variant_gap.py both started FROM the mcs=200 piece and asked where
its members came from. This one starts FROM comps.json's current Kayle/
Cassiopeia/Aphelios-Öncü (their full, final-label population — exactly
n_total in comps.json today) and asks where mcs=200 sends them: a piece, its
own raw training-noise (-1), or (for early-death boards) nowhere at all,
since early-death boards never went through ANY HDBSCAN fit.

Read-only. Does NOT modify config.yaml/clustering.py/data/processed/models/.
Loads the mcs=200 model from this session's scratch cache
(hdbscan_cache/mcs_200.pkl, written by scripts/resolve_variant_gap.py) —
falls back to a fresh fit only if that cache is missing.

Usage: python scripts/measure_residual.py
"""

from __future__ import annotations

import collections
import json
import math
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
    compute_centroids,
    reassign_noise,
)
from src.items import MIN_N as ITEM_MIN_N
from src.items import compute_item_cells
from src.matchups import MIN_N as H2H_MIN_N

CONFIG_PATH = PROJECT_ROOT / "config.yaml"
CLUSTER_LABELS_PATH = PROJECT_ROOT / "data" / "processed" / "cluster_labels.npy"
STATIC_ITEMS_PATH = PROJECT_ROOT / "data" / "static" / "items.json"
OFF_META_CODE = -2

SCRATCH_DIR = Path(
    r"C:\Users\USER\AppData\Local\Temp\PROJECT"
    r"\428e16f3-dfaf-4fa4-a493-03d35d920af8\scratchpad"
)
CACHE_DIR = SCRATCH_DIR / "hdbscan_cache"

TARGET_COMPS = ["c_02", "c_04", "c_17"]  # Kayle, Cassiopeia, Aphelios-Öncü


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
static_items = json.loads(STATIC_ITEMS_PATH.read_text(encoding="utf-8"))
comps = json.loads(COMPS_PATH.read_text(encoding="utf-8"))
prod_full_labels = np.load(CLUSTER_LABELS_PATH)
assert len(prod_full_labels) == len(rows)

with open(MODEL_PATH, "rb") as f:
    prod_model = pickle.load(f)
prod_train_labels = prod_model.labels_

training_mask = np.array([not r["early_death"] for r in rows])
training_vectors = vectors[training_mask]
assert len(prod_train_labels) == len(training_vectors)

row_to_train_pos = np.full(len(rows), -1, dtype=np.int64)
row_to_train_pos[training_mask] = np.arange(training_vectors.shape[0])


def unit_name(uid: str) -> str:
    return units_static.get(uid, {}).get("name", uid)


def cache_path(mcs: int) -> Path:
    return CACHE_DIR / f"mcs_{mcs}.pkl"


def fit_or_load(mcs: int):
    cp = cache_path(mcs)
    if cp.exists():
        log(f"mcs={mcs}: cache'ten yüklendi ({cp.name}).")
        with open(cp, "rb") as f:
            return pickle.load(f)
    log(f"mcs={mcs}: cache yok, fit ediliyor...")
    t0 = time.perf_counter()
    model = HDBSCAN(min_cluster_size=mcs, metric="euclidean", copy=False)
    labels = model.fit_predict(training_vectors)
    log(f"  -> {time.perf_counter()-t0:.1f}s")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cp, "wb") as f:
        pickle.dump((model, labels), f)
    return model, labels


model200, labels200 = fit_or_load(200)
centroids200 = compute_centroids(training_vectors, labels200)

pieces_by_comp: dict = {}
for cid in TARGET_COMPS:
    pid = int(cid.split("_")[1])
    comp_train_mask = prod_train_labels == pid
    target_counts = collections.Counter(labels200[comp_train_mask].tolist())
    piece_ids = sorted(t for t, c in target_counts.items() if t != -1 and c >= 100)
    pieces_by_comp[cid] = piece_ids

all_piece_ids = [pid for cid in TARGET_COMPS for pid in pieces_by_comp[cid]]
piece_owner = {pid: cid for cid in TARGET_COMPS for pid in pieces_by_comp[cid]}
log(f"Parçalar: {[(cid, comps[cid]['name'], pieces_by_comp[cid]) for cid in TARGET_COMPS]}\n")

# ---------------------------------------------------------------------------
# Part 1 — full accounting from the PRODUCTION comp's own point of view
# ---------------------------------------------------------------------------

log("=" * 118)
log("1) ÜRETİMDEKİ TAM POPÜLASYON, mcs=200 AKIBETİNE GÖRE")
log("=" * 118)

artik_row_idx_by_comp: dict = {}  # cid -> list of row indices (raw-noise + early-death)
raw_noise_row_idx_by_comp: dict = {}
early_death_row_idx_by_comp: dict = {}
other_cluster_row_idx_by_comp: dict = {}

for cid in TARGET_COMPS:
    pid = int(cid.split("_")[1])
    full_indices = np.where(prod_full_labels == pid)[0]
    assert len(full_indices) == comps[cid]["n_total"], (
        f"{cid}: prod_full_labels count ({len(full_indices)}) != comps.json n_total ({comps[cid]['n_total']})"
    )

    piece_counts = {pid2: 0 for pid2 in pieces_by_comp[cid]}
    raw_noise_idx = []
    early_death_idx = []
    other_cluster_idx: dict = collections.defaultdict(list)

    for i in full_indices:
        tp = row_to_train_pos[i]
        if tp == -1:
            early_death_idx.append(int(i))
            continue
        raw_label = int(labels200[tp])
        if raw_label in piece_counts:
            piece_counts[raw_label] += 1
        elif raw_label == -1:
            raw_noise_idx.append(int(i))
        else:
            other_cluster_idx[raw_label].append(int(i))

    raw_noise_row_idx_by_comp[cid] = raw_noise_idx
    early_death_row_idx_by_comp[cid] = early_death_idx
    other_cluster_row_idx_by_comp[cid] = other_cluster_idx
    artik_row_idx_by_comp[cid] = raw_noise_idx + early_death_idx

    n_total = len(full_indices)
    log(f"{comps[cid]['name']} ({cid}): n_total={n_total}")
    for pid2, c in piece_counts.items():
        log(f"    -> parça {pid2}: n={c} (%{100*c/n_total:.1f})")
    log(f"    -> mcs=200 ham-noise(-1): n={len(raw_noise_idx)} (%{100*len(raw_noise_idx)/n_total:.1f})")
    log(f"    -> early-death (mcs=200 fit'ine hiç girmedi): n={len(early_death_idx)} (%{100*len(early_death_idx)/n_total:.1f})")
    other_total = sum(len(v) for v in other_cluster_idx.values())
    if other_total:
        log(f"    -> BAŞKA bir mcs=200 cluster'ına düştü: n={other_total} (%{100*other_total/n_total:.1f})")
        for oc, idxs in sorted(other_cluster_idx.items(), key=lambda kv: -len(kv[1]))[:5]:
            log(f"        -> mcs=200 cluster {oc}: n={len(idxs)}")
    else:
        log("    -> BAŞKA bir mcs=200 cluster'ına düşen yok.")
    check_sum = sum(piece_counts.values()) + len(raw_noise_idx) + len(early_death_idx) + other_total
    assert check_sum == n_total, f"{cid}: sayım tutmuyor ({check_sum} != {n_total})"
    log(f"    (toplam kontrol: {check_sum} == {n_total} OK)\n")

# ---------------------------------------------------------------------------
# Part 2 — "artık" (raw-noise + early-death) profile
# ---------------------------------------------------------------------------

log("=" * 118)
log("2) 'ARTIK' (ham-noise + early-death) NOKTALARININ PROFİLİ")
log("=" * 118)


def vector_of(row_idx: int) -> np.ndarray:
    return vectors[row_idx]


def nearest_piece(vec: np.ndarray):
    best_pid, best_dist = None, 2.0
    for pid2 in all_piece_ids:
        d = 1.0 - float(np.dot(vec, centroids200[pid2]))
        if d < best_dist:
            best_dist, best_pid = d, pid2
    return best_pid, best_dist


for cid in TARGET_COMPS:
    for label, idx_list in (("ham-noise", raw_noise_row_idx_by_comp[cid]), ("early-death", early_death_row_idx_by_comp[cid])):
        if not idx_list:
            log(f"{comps[cid]['name']} {label}: n=0")
            continue
        placements = [rows[i]["placement"] for i in idx_list]
        n = len(placements)
        avg = sum(placements) / n
        nearest = [nearest_piece(vector_of(i)) for i in idx_list]
        dists = [d for _, d in nearest]
        same_family = sum(1 for pid2, _ in nearest if piece_owner[pid2] == cid)
        nearest_counts = collections.Counter(pid2 for pid2, _ in nearest)
        within_035 = sum(1 for d in dists if d <= 0.35)
        log(f"{comps[cid]['name']} {label}: n={n}  avg_placement={avg:.3f}")
        log(f"    en-yakın-parça mesafesi: mean={np.mean(dists):.4f} median={np.median(dists):.4f} "
            f"min={np.min(dists):.4f} max={np.max(dists):.4f}")
        log(f"    en yakın parça AYNI aile: {same_family}/{n} (%{100*same_family/n:.1f})  "
            f"mesafe<=0.35: {within_035}/{n} (%{100*within_035/n:.1f})")
        log(f"    en-yakın-parça dağılımı: {dict(sorted(nearest_counts.items(), key=lambda kv: -kv[1]))}")
    log("")

# ---------------------------------------------------------------------------
# Part 3 — three options, simulated
# ---------------------------------------------------------------------------

log("=" * 118)
log("3) ÜÇ SEÇENEK — PARÇA n'LERİ VE h2h n<100 HÜCRE SAYISI")
log("=" * 118)
log(f"h2h MIN_N (src.matchups)={H2H_MIN_N}, item MIN_N (src.items)={ITEM_MIN_N}\n")


def build_scheme_labels(scheme: str) -> list:
    """Returns a per-row label list: piece raw id (int, only for the 8
    pieces), production's own label string for everything else, or
    OFF_META. scheme in {"baseline", "i", "ii", "iii"}."""
    labels = [None] * len(rows)
    for i in range(len(rows)):
        pid_full = int(prod_full_labels[i])
        if pid_full == OFF_META_CODE:
            labels[i] = OFF_META
            continue
        cid = f"c_{pid_full:02d}"
        if cid not in TARGET_COMPS:
            labels[i] = cid
            continue
        tp = row_to_train_pos[i]
        if tp != -1 and int(labels200[tp]) in pieces_by_comp[cid]:
            labels[i] = int(labels200[tp])
            continue
        # this row is "artık" for its comp — resolve per scheme
        is_early_death = tp == -1
        if scheme == "baseline":
            labels[i] = OFF_META
        elif scheme == "i":
            nearest_pid, dist = nearest_piece(vector_of(i))
            labels[i] = nearest_pid if dist <= noise_distance_threshold else OFF_META
        elif scheme == "ii":
            labels[i] = OFF_META
        elif scheme == "iii":
            if is_early_death:
                nearest_pid, dist = nearest_piece(vector_of(i))
                labels[i] = nearest_pid if dist <= noise_distance_threshold else OFF_META
            else:
                labels[i] = OFF_META
    return labels


def piece_ns(labels: list) -> dict:
    counts = collections.Counter(l for l in labels if isinstance(l, int) and l in all_piece_ids)
    return {pid: counts.get(pid, 0) for pid in all_piece_ids}


def h2h_insufficient_count(labels: list) -> tuple[int, list]:
    """Counts (piece, opponent) unordered pairs with n < H2H_MIN_N, opponent
    ranging over every OTHER real comp (other 7 pieces + 16 unchanged
    comps) — mirrors matchups.py's own pairing rule (same match, both a
    real label, off_meta excluded)."""
    by_match: dict = collections.defaultdict(list)
    for i, row in enumerate(rows):
        lbl = labels[i]
        if lbl == OFF_META:
            continue
        key = lbl if isinstance(lbl, int) else lbl  # already c_XX string or piece int
        by_match[row["match_id"]].append(key)

    pair_n: dict = collections.defaultdict(int)
    for participants in by_match.values():
        for i in range(len(participants)):
            for j in range(len(participants)):
                if i == j:
                    continue
                a, b = participants[i], participants[j]
                if a not in all_piece_ids and b not in all_piece_ids:
                    continue  # only care about cells touching one of the 8 pieces
                key = tuple(sorted((str(a), str(b))))
                pair_n[key] += 1

    # each unordered pair counted twice (i,j) and (j,i) with identical n — halve
    seen = set()
    insufficient = []
    for (a, b), n in pair_n.items():
        if (a, b) in seen:
            continue
        seen.add((a, b))
        if a == b:
            continue
        if n < H2H_MIN_N * 2:  # n counted twice (both directions), so compare to 2*MIN_N
            insufficient.append((a, b, n // 2))
    return len(insufficient), insufficient


baseline_labels = build_scheme_labels("baseline")
baseline_ns = piece_ns(baseline_labels)
baseline_insuff, _ = h2h_insufficient_count(baseline_labels)

log(f"BASELINE (artık'a hiç dokunulmadı — sadece kendi comp'undan gelen ham-atanmış noktalar):")
for pid in all_piece_ids:
    log(f"    parça {pid} ({piece_owner[pid]}): n={baseline_ns[pid]}")
log(f"    h2h n<{H2H_MIN_N} hücre sayısı (8 parça x diğer 23 comp arası): {baseline_insuff}")
_, baseline_insuff_list = h2h_insufficient_count(baseline_labels)
for a, b, n in sorted(baseline_insuff_list, key=lambda t: t[2]):
    log(f"        yetersiz: {a} - {b} (n={n})")
log("")

for scheme_name, desc in [
    ("i", "artık (ham-noise + early-death) -> 0.35 eşiğiyle en yakın parçaya, aşarsa off_meta"),
    ("ii", "artık (ham-noise + early-death) -> tamamı off_meta"),
    ("iii", "early-death -> 0.35 eşiğiyle en yakın parçaya (aşarsa off_meta); ham-noise -> HER ZAMAN off_meta"),
]:
    labels = build_scheme_labels(scheme_name)
    ns = piece_ns(labels)
    insuff, insuff_list = h2h_insufficient_count(labels)
    log(f"SEÇENEK ({scheme_name}): {desc}")
    for pid in all_piece_ids:
        delta = ns[pid] - baseline_ns[pid]
        log(f"    parça {pid} ({piece_owner[pid]}): n={ns[pid]} (Δ={delta:+d})")
    log(f"    h2h n<{H2H_MIN_N} hücre sayısı: {insuff} (baseline'a göre Δ={insuff - baseline_insuff:+d})")
    for a, b, n in sorted(insuff_list, key=lambda t: t[2]):
        log(f"        yetersiz: {a} - {b} (n={n})")
    log("")

# ---------------------------------------------------------------------------
# items.py — cells_below_min_n_fraction for the 8 pieces
# ---------------------------------------------------------------------------

log("=" * 118)
log("4) items.py — cells_below_min_n_fraction, 8 PARÇA İÇİN (BASELINE piece n'leriyle: n=2541 tarzı, "
    "measure_variants.py'nin info200 tanımı — mcs=200'ün KENDİ standart reassignment'ı, tüm noktalar üzerinde)")
log("=" * 118)

early_death_vectors = vectors[~training_mask]


def full_population_labels_all(labels_train, centroids):
    training_final = reassign_noise(training_vectors, labels_train, centroids, noise_distance_threshold)
    early_death_final = _nearest_centroid_labels(early_death_vectors, centroids, noise_distance_threshold)
    full_labels = np.empty(len(rows), dtype=object)
    full_labels[training_mask] = training_final
    full_labels[~training_mask] = early_death_final
    return full_labels


full_labels200 = full_population_labels_all(labels200, centroids200)

# rows for compute_item_cells need row["cluster_label"] set; only the 8
# pieces get a real int label, everything else -> OFF_META_CODE so it's
# excluded (compute_item_cells skips OFF_META_CODE rows).
item_rows = []
piece_n_full = collections.Counter()
for i, row in enumerate(rows):
    lbl = full_labels200[i]
    new_row = dict(row)
    if lbl in all_piece_ids:
        new_row["cluster_label"] = int(lbl)
        piece_n_full[int(lbl)] += 1
    else:
        new_row["cluster_label"] = OFF_META_CODE
    item_rows.append(new_row)

completed_item_ids = {item_id for item_id, info in static_items.items() if info["type"] == "completed"}
cells = compute_item_cells(item_rows, completed_item_ids)

per_piece_cells: dict = collections.defaultdict(lambda: [0, 0])  # pid -> [total, suppressed]
for (cluster_id, unit_id, item_id), placements in cells.items():
    if cluster_id not in all_piece_ids:
        continue
    per_piece_cells[cluster_id][0] += 1
    if len(placements) < ITEM_MIN_N:
        per_piece_cells[cluster_id][1] += 1

for cid in TARGET_COMPS:
    log(f"--- {comps[cid]['name']} ({cid}) ---")
    for pid in pieces_by_comp[cid]:
        total, suppressed = per_piece_cells.get(pid, [0, 0])
        frac = suppressed / total if total else float("nan")
        core_units = comps[cid]["core_units"]  # reference only, not restricted to
        log(f"    parça {pid}: n(full)={piece_n_full[pid]}  toplam (cluster,unit,item) hücresi={total}  "
            f"n<{ITEM_MIN_N} elenen={suppressed}  elenme oranı=%{100*frac:.1f}")

log("\n(Referans: SPEC.md'nin belgelediği global elenme oranı ~%86, 19 comp'un TAMAMI üzerinden, 14513 maçlık koşuda.)")

log("\n" + "=" * 118)
log("BİTTİ — bu script config.yaml, clustering.py veya data/processed/, models/ altındaki hiçbir dosyayı değiştirmedi.")
log("=" * 118)
