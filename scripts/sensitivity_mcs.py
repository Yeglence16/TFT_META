"""One-off diagnostic (2026-09-01) — min_cluster_size sensitivity analysis.

Re-fits HDBSCAN on the EXISTING training feature matrix
(data/processed/features_vectors.npy, early_death boards excluded — same
filter clustering.py's run() uses) at several min_cluster_size values, to see
how cluster count/noise/composition respond, independent of whatever the
current config.yaml value produces.

Read-only / in-memory only. Does NOT modify config.yaml or clustering.py,
does NOT write to data/processed/ or models/ — every HDBSCAN fit here is
local to this script's own process and discarded when it exits. Re-uses
clustering.py's own centroid/noise-reassignment/core-unit functions directly
(imported, not reimplemented) so the "full population" numbers here are
computed exactly the way production does, just parameterized differently.

min_samples is intentionally left unset (sklearn's HDBSCAN default), which is
also what clustering.py's fit_hdbscan() does — per sklearn 1.8 source
(`HDBSCAN.fit`): `self._min_samples = min_cluster_size if min_samples is None
else min_samples`. So min_samples == min_cluster_size for every row below,
verified against the pickled production model (min_samples param is None
there too); this script does not override it, per instruction.

Usage: python scripts/sensitivity_mcs.py
"""

from __future__ import annotations

import json
import pickle
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import yaml
from sklearn.cluster import HDBSCAN
from sklearn.metrics import adjusted_rand_score

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.clustering import (
    COMPS_PATH,
    MODEL_PATH,
    META_ROWS_PATH,
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
TEST_VALUES = [30, 45, 60, 90, 120, 200]


def log(*args):
    print(*args)


# ---------------------------------------------------------------------------
# Load everything read-only — same inputs clustering.py's run() reads.
# ---------------------------------------------------------------------------

config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
noise_distance_threshold = config["clustering"]["noise_distance_threshold"]
prod_min_cluster_size_fraction = config["clustering"]["min_cluster_size_fraction"]

with open(META_ROWS_PATH, encoding="utf-8") as f:
    rows = [json.loads(line) for line in f]
vectors = np.load(VECTORS_PATH)
units_static = json.loads(STATIC_UNITS_PATH.read_text(encoding="utf-8"))


def unit_name(uid: str) -> str:
    return units_static.get(uid, {}).get("name", uid)


training_mask = np.array([not row["early_death"] for row in rows])
training_vectors = vectors[training_mask]
early_death_vectors = vectors[~training_mask]
log(f"Eğitim popülasyonu: {len(training_vectors)}/{len(vectors)} board "
    f"({int((~training_mask).sum())} early-death, eğitim dışı tutuldu — clustering.py'nin run() ile aynı filtre).")
log(f"noise_distance_threshold (config.yaml, değiştirilmedi): {noise_distance_threshold}")
log(f"Üretim min_cluster_size_fraction (config.yaml, değiştirilmedi): {prod_min_cluster_size_fraction} "
    f"-> min_cluster_size={round(prod_min_cluster_size_fraction * len(training_vectors))}\n")

comps = json.loads(COMPS_PATH.read_text(encoding="utf-8"))
prod_cluster_ids = [cid for cid in comps if cid not in ("_metadata", OFF_META)]
prod_core_units = {cid: frozenset(comps[cid]["core_units"]) for cid in prod_cluster_ids}

with open(MODEL_PATH, "rb") as f:
    prod_model = pickle.load(f)
prod_labels = prod_model.labels_  # raw HDBSCAN training labels, aligned with training_vectors
assert len(prod_labels) == len(training_vectors), "üretim modeli ile mevcut eğitim verisi hizası uyuşmuyor — features.py/clustering.py yeniden çalıştırılmış olabilir, dur"
prod_n_clusters = len(set(prod_labels.tolist()) - {-1})
log(f"Üretim referansı doğrulandı: model.labels_ -> {prod_n_clusters} cluster (comps.json'daki {len(prod_cluster_ids)} ile eşleşiyor: {prod_n_clusters == len(prod_cluster_ids)}).\n")


def full_population_labels(labels_train: np.ndarray, centroids: dict) -> np.ndarray:
    """Replays clustering.py's run(): training noise reassignment + early-death
    nearest-centroid assignment, exactly as production does (same imported
    functions, same config threshold) — just for this run's own labels/centroids."""
    training_final = reassign_noise(training_vectors, labels_train, centroids, noise_distance_threshold)
    early_death_final = _nearest_centroid_labels(early_death_vectors, centroids, noise_distance_threshold)
    full_labels = np.empty(len(rows), dtype=object)
    full_labels[training_mask] = training_final
    full_labels[~training_mask] = early_death_final
    return full_labels


def cluster_core_units(full_labels: np.ndarray) -> dict:
    members: dict = {}
    for i, label in enumerate(full_labels):
        if label == OFF_META:
            continue
        members.setdefault(label, []).append(i)
    result = {}
    for cid, indices in members.items():
        n_total = len(indices)
        unit_freq = _unit_frequency(rows, indices)
        top_units = select_core_units(unit_freq, n_total)
        result[cid] = {"core_units": frozenset(u for u, _ in top_units), "n_total": n_total}
    return result


def short_label(core_units: frozenset, n_total: int, k: int = 3) -> str:
    names = sorted(unit_name(u) for u in core_units)[:k]
    return f"{{{', '.join(names)}, ...}}(n={n_total})" if len(core_units) > k else f"{{{', '.join(names)}}}(n={n_total})"


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

results = []
for mcs in TEST_VALUES:
    log(f"mcs={mcs} çalıştırılıyor (2 kez, determinizm için)...")

    t0 = time.perf_counter()
    model1 = HDBSCAN(min_cluster_size=mcs, metric="euclidean", copy=False)
    labels1 = model1.fit_predict(training_vectors)
    dur1 = time.perf_counter() - t0

    t0 = time.perf_counter()
    model2 = HDBSCAN(min_cluster_size=mcs, metric="euclidean", copy=False)
    labels2 = model2.fit_predict(training_vectors)
    dur2 = time.perf_counter() - t0

    deterministic = bool(np.array_equal(labels1, labels2))

    n_clusters = len(set(labels1.tolist()) - {-1})
    raw_noise_frac = float((labels1 == -1).sum()) / len(labels1)
    ari = float(adjusted_rand_score(prod_labels, labels1))

    centroids = compute_centroids(training_vectors, labels1)
    full_labels = full_population_labels(labels1, centroids)
    cluster_info = cluster_core_units(full_labels)
    off_meta_n = int((full_labels == OFF_META).sum())
    final_off_meta_frac = off_meta_n / len(full_labels)

    sizes = sorted(info["n_total"] for info in cluster_info.values())

    results.append({
        "mcs": mcs,
        "n_clusters": n_clusters,
        "raw_noise_frac": raw_noise_frac,
        "final_off_meta_frac": final_off_meta_frac,
        "sizes": sizes,
        "ari": ari,
        "dur1": dur1,
        "dur2": dur2,
        "deterministic": deterministic,
        "labels_train": labels1,
        "model": model1,
        "probabilities": model1.probabilities_,
        "cluster_info": cluster_info,
    })

    log(f"  -> {n_clusters} cluster, ham_noise=%{100*raw_noise_frac:.2f}, nihai_off_meta=%{100*final_off_meta_frac:.2f}, "
        f"ARI={ari:.4f}, run1={dur1:.1f}s run2={dur2:.1f}s determinist={'EVET' if deterministic else 'HAYIR'}\n")

# ---------------------------------------------------------------------------
# Ana tablo
# ---------------------------------------------------------------------------

log("\n" + "=" * 118)
log("ANA TABLO")
log("=" * 118)
log(f"{'mcs':>5}{'min_samples':>13}{'n_cluster':>11}{'ham_noise%':>12}{'off_meta%':>11}"
    f"{'boyut_min':>11}{'boyut_medyan':>14}{'boyut_max':>11}{'ARI(üretim)':>13}{'run1(s)':>9}{'run2(s)':>9}{'determinist':>13}")
for r in results:
    sizes = r["sizes"]
    smin = sizes[0] if sizes else 0
    smed = statistics.median(sizes) if sizes else 0
    smax = sizes[-1] if sizes else 0
    log(f"{r['mcs']:>5}{r['mcs']:>13}{r['n_clusters']:>11}{100*r['raw_noise_frac']:>11.2f}%{100*r['final_off_meta_frac']:>10.2f}%"
        f"{smin:>11}{smed:>14.1f}{smax:>11}{r['ari']:>13.4f}{r['dur1']:>9.1f}{r['dur2']:>9.1f}{('EVET' if r['deterministic'] else 'HAYIR'):>13}")
log(f"\n(Referans satır — üretim: min_cluster_size_fraction={prod_min_cluster_size_fraction} -> "
    f"min_cluster_size={round(prod_min_cluster_size_fraction * len(training_vectors))} -> {prod_n_clusters} cluster.)")
log("min_samples sütunu mcs'yle özdeş: HDBSCAN.fit, min_samples=None iken min_cluster_size'a düşüyor (sklearn 1.8 kaynağından doğrulandı), bu script min_samples'ı hiç set etmiyor.")
log("boyut_min/medyan/max ve off_meta%: TAM popülasyon (n_total, noise-reassignment + early-death ataması sonrası). ham_noise%: sadece HDBSCAN fit'inin eğitim popülasyonu üzerindeki ham -1 oranı.")

# ---------------------------------------------------------------------------
# 19'luk çözümle bir alt / bir üst kademe karşılaştırması
# ---------------------------------------------------------------------------

log("\n" + "=" * 118)
log("19'LUK ÇÖZÜMLE BİR ALT / BİR ÜST KADEME KARŞILAŞTIRMASI")
log("=" * 118)

below = [r for r in results if r["n_clusters"] < prod_n_clusters]
above = [r for r in results if r["n_clusters"] > prod_n_clusters]
equal = [r for r in results if r["n_clusters"] == prod_n_clusters]

pick_below = max(below, key=lambda r: r["n_clusters"]) if below else None
pick_above = min(above, key=lambda r: r["n_clusters"]) if above else None

if pick_below:
    log(f"19'a en yakın ALT (daha az cluster): mcs={pick_below['mcs']} ({pick_below['n_clusters']} cluster)")
else:
    log(f"19'a en yakın ALT: test edilen 6 değerin ({TEST_VALUES}) hiçbiri {prod_n_clusters}'dan az cluster üretmedi.")
if pick_above:
    log(f"19'a en yakın ÜST (daha çok cluster): mcs={pick_above['mcs']} ({pick_above['n_clusters']} cluster)")
else:
    log(f"19'a en yakın ÜST: test edilen 6 değerin hiçbiri {prod_n_clusters}'dan çok cluster üretmedi.")
if equal:
    log(f"Tam {prod_n_clusters} cluster üreten test değeri/değerleri: {[r['mcs'] for r in equal]}")


def contingency(prod_lab: np.ndarray, run_lab: np.ndarray) -> tuple[dict, dict]:
    """Point-level (eğitim popülasyonu, ham label) kesişim — jaccard/core_units
    yaklaşıklaması değil, üretim ve bu run'ın AYNI 99623 noktayı nasıl
    böldüğünün tam sayımı."""
    prod_to_run: dict = {}
    for cid in sorted(set(prod_lab.tolist()) - {-1}):
        mask = prod_lab == cid
        counter = Counter(run_lab[mask].tolist())
        prod_to_run[cid] = (int(mask.sum()), sorted(counter.items(), key=lambda kv: -kv[1]))
    run_to_prod: dict = {}
    for rcid in sorted(set(run_lab.tolist()) - {-1}):
        mask = run_lab == rcid
        counter = Counter(prod_lab[mask].tolist())
        run_to_prod[rcid] = (int(mask.sum()), sorted(counter.items(), key=lambda kv: -kv[1]))
    return prod_to_run, run_to_prod


def run_cluster_label(run_result: dict, rcid) -> str:
    if rcid == -1:
        return "GÜRÜLTÜ(-1, eğitimde noise kaldı)"
    info = run_result["cluster_info"].get(rcid)
    if info is None:
        return f"[full-population'da bulunamadı, train_id={rcid}]"
    return short_label(info["core_units"], info["n_total"])


def print_bracket_comparison(run_result: dict):
    mcs = run_result["mcs"]
    n_clusters = run_result["n_clusters"]
    log(f"\n--- mcs={mcs} ({n_clusters} cluster) vs üretim ({prod_n_clusters} cluster) — nokta-bazlı kesişim (eğitim popülasyonu) ---")
    prod_to_run, run_to_prod = contingency(prod_labels, run_result["labels_train"])

    log("\n[Üretim cluster'ı -> bu run'da nereye dağıldı]")
    for i, cid in enumerate(sorted(set(prod_labels.tolist()) - {-1})):
        prod_cid_str = f"c_{cid:02d}"
        prod_name = comps.get(prod_cid_str, {}).get("name", prod_cid_str)
        prod_units = ", ".join(sorted(unit_name(u) for u in prod_core_units[prod_cid_str]))
        total, breakdown = prod_to_run[cid]
        log(f"  [{prod_name}] core_units={{{prod_units}}} (n_eğitim={total})")
        for rcid, cnt in breakdown[:5]:
            pct = 100 * cnt / total
            log(f"      -> {run_cluster_label(run_result, rcid)}  {cnt}/{total} (%{pct:.1f})")

    log("\n[Bu run'daki cluster -> üretimde nereden geliyor]")
    for rcid, (total, breakdown) in sorted(run_to_prod.items(), key=lambda kv: -kv[1][0]):
        log(f"  [{run_cluster_label(run_result, rcid)}] (n_eğitim={total})")
        for cid, cnt in breakdown[:5]:
            pct = 100 * cnt / total
            prod_cid_str = f"c_{cid:02d}"
            prod_name = comps.get(prod_cid_str, {}).get("name", prod_cid_str)
            log(f"      <- [{prod_name}]  {cnt}/{total} (%{pct:.1f})")


if pick_below:
    print_bracket_comparison(pick_below)
if pick_above:
    print_bracket_comparison(pick_above)

# ---------------------------------------------------------------------------
# Her koşu için condensed tree / hiyerarşi
# ---------------------------------------------------------------------------

log("\n" + "=" * 118)
log("HER KOŞU İÇİN CONDENSED TREE / HİYERARŞİ")
log("=" * 118)
log("""
Yöntem diag_taric.py'deki 5. bölümle aynı: sklearn HDBSCAN condensed tree'yi
public API olarak vermiyor, bu yüzden her run'ın kendi ham
`_single_linkage_tree_`'i üzerinde, her cluster'dan `probabilities_`'i en
yüksek BİR temsilci nokta seçilip birleşme mesafesi artan sırayla tekrar
oynatılıyor (union-find). Etiketler core_units'ten türetilmiş kısa isimler —
run'lar arası ham label numaraları anlamsız olduğu için hiçbir yerde
kullanılmıyor.
""")


def print_condensed_tree(run_result: dict):
    mcs = run_result["mcs"]
    labels_train = run_result["labels_train"]
    probabilities = run_result["probabilities"]
    model = run_result["model"]
    cluster_info = run_result["cluster_info"]
    real_cluster_ids = sorted(set(labels_train.tolist()) - {-1})

    log(f"\n--- mcs={mcs}: {len(real_cluster_ids)} cluster arası birleşme sırası ---")

    representative = {}
    for cid in real_cluster_ids:
        idxs = np.where(labels_train == cid)[0]
        best = idxs[np.argmax(probabilities[idxs])]
        representative[cid] = int(best)

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
    group_index: dict = {}
    group_labels: dict = {}
    for k, cid in enumerate(real_cluster_ids):
        leaf = representative[cid]
        root_members[leaf] = {cid}
        group_index[leaf] = k
        info = cluster_info.get(cid)
        group_labels[k] = short_label(info["core_units"], info["n_total"]) if info else f"train_id={cid}"

    linkage_rows = []
    for i, row in enumerate(tree):
        left, right, dist = int(row["left_node"]), int(row["right_node"]), float(row["value"])
        new_id = n_leaves + i
        rl, rr = find(left), find(right)
        ml, mr = root_members.pop(rl, None), root_members.pop(rr, None)
        if ml and mr:
            idx_l, idx_r = group_index.pop(rl), group_index.pop(rr)
            new_idx = len(real_cluster_ids) + len(linkage_rows)
            linkage_rows.append((idx_l, idx_r, dist, ml | mr))
            group_index[new_id] = new_idx
            group_labels[new_idx] = f"[grup n={len(ml | mr)}]"
            root_members[new_id] = ml | mr
        elif ml or mr:
            surviving_root = rl if ml else rr
            group_index[new_id] = group_index.pop(surviving_root)
            root_members[new_id] = ml or mr
        parent[rl] = new_id
        parent[rr] = new_id
        parent[new_id] = new_id

    if len(linkage_rows) != len(real_cluster_ids) - 1:
        log(f"  UYARI: beklenen {len(real_cluster_ids)-1} birleşme, bulunan {len(linkage_rows)} — "
            f"temsilci noktalardan biri ağaçta çakışmış/kayıp olabilir, aşağıdaki sıralama eksik olabilir.")

    for i, (idx_l, idx_r, dist, combined) in enumerate(linkage_rows):
        log(f"  #{i+1:>3}  mesafe={dist:.4f}  grup_boyutu={len(combined):>3}   "
            f"{group_labels[idx_l]}  +  {group_labels[idx_r]}")


for r in results:
    print_condensed_tree(r)

log("\n" + "=" * 118)
log("BİTTİ — bu script config.yaml, clustering.py veya data/processed/, models/ altındaki hiçbir dosyayı değiştirmedi.")
log("=" * 118)
