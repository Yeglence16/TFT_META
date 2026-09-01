"""One-off diagnostic (2026-09-01) — investigates whether the current
unweighted unit-presence board vector produces spuriously-close clusters,
using the 6 clusters that carry DA_Taric18 as a >=50%-frequency core unit
as the case study (Taric is a low-priority/bench-ish unit — SPEC.md section
9's "core_unit_avg_stars" finding: he averages 1.2-1.55 stars in every one
of these 6 clusters, never close to a real investment target).

Read-only. Does NOT modify any pipeline code, config.yaml, or written
output — everything here re-derives from data/processed/, data/static/, and
models/ already on disk, and writes only to stdout (and, if requested,
scratch files the caller names explicitly). No feature, config, or
clustering.py change is made or proposed here; this is measurement only.

Usage: python scripts/diag_taric.py
"""

from __future__ import annotations

import itertools
import json
import pickle
import sys
from pathlib import Path

import numpy as np

# stdout defaults to the OS locale encoding (cp1254 here) on Windows, which
# mangles Turkish unit/trait names the moment output is redirected/piped —
# same root cause as tests/test_encoding.py, clustering.py's main(), etc.
sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import DATA_PROCESSED, DATA_STATIC, MODELS_DIR

TARIC = "DA_Taric18"


def log(*args):
    print(*args)


# ---------------------------------------------------------------------------
# Load everything read-only
# ---------------------------------------------------------------------------

comps = json.loads((DATA_PROCESSED / "comps.json").read_text(encoding="utf-8"))
meta = json.loads((DATA_PROCESSED / "meta.json").read_text(encoding="utf-8"))
units_static = json.loads((DATA_STATIC / "units.json").read_text(encoding="utf-8"))
traits_static = json.loads((DATA_STATIC / "traits.json").read_text(encoding="utf-8"))
vectors = np.load(DATA_PROCESSED / "features_vectors.npy").astype(np.float64)
with open(DATA_PROCESSED / "features_meta.jsonl", encoding="utf-8") as f:
    rows = [json.loads(line) for line in f]
with open(MODELS_DIR / "hdbscan_model.pkl", "rb") as f:
    model = pickle.load(f)

unit_order = meta["unit_order"]
trait_order = meta["trait_order"]
n_unit_dims = len(unit_order)
dim_ids = unit_order + trait_order
assert vectors.shape[1] == len(dim_ids), "vector width doesn't match unit_order+trait_order — data drifted, stop"


def dim_label(idx: int) -> str:
    fid = dim_ids[idx]
    if idx < n_unit_dims:
        return f"unit:{units_static.get(fid, {}).get('name', fid)}"
    return f"trait:{traits_static.get(fid, {}).get('name', fid)}"


real_comp_ids = [cid for cid in comps if cid not in ("_metadata", "off_meta")]

# Exact reconstruction of the population compute_centroids() actually
# averaged (verified byte-identical against the stored centroid before this
# script does anything else — if this assert ever fails, everything below is
# built on a wrong foundation and must not be trusted).
training_mask = np.array([not r["early_death"] for r in rows])
training_vectors = vectors[training_mask]
training_rows = [r for r, keep in zip(rows, training_mask) if keep]
hdbscan_labels = model.labels_  # aligned with training_vectors, -1 = HDBSCAN noise
assert len(hdbscan_labels) == len(training_vectors)

_cid_to_num = lambda cid: int(cid.split("_")[1])


def cluster_points(cluster_id_num: int) -> np.ndarray:
    return training_vectors[hdbscan_labels == cluster_id_num]


def recompute_centroid(cluster_id_num: int) -> np.ndarray:
    member_vectors = cluster_points(cluster_id_num)
    mean = member_vectors.mean(axis=0)
    norm = np.linalg.norm(mean)
    return mean / norm if norm > 0 else mean


# Rigor check: every real comp's stored centroid must be exactly reproducible.
for cid in real_comp_ids:
    recomputed = recompute_centroid(_cid_to_num(cid))
    stored = np.array(comps[cid]["centroid"])
    max_diff = float(np.max(np.abs(recomputed - stored)))
    # 1e-4 tolerance matches SPEC-WEB.md's own suggested_vector_tolerance
    # (_selftest.json) — floating-point summation-order noise, not a real
    # mismatch; verified for c_07 at exactly 0.0 with float32 vectors, this
    # small residual only appears after the explicit float64 upcast above.
    assert max_diff < 1e-4, f"{cid}: centroid reconstruction mismatch ({max_diff}) — cannot trust downstream analysis"
log(f"Sanity check: {len(real_comp_ids)}/{len(real_comp_ids)} cluster centroidi tam olarak yeniden üretildi (max diff < 1e-4).\n")


def cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    return 1.0 - float(np.dot(a, b))


# ---------------------------------------------------------------------------
# 1) Taric'i core_unit olarak taşıyan cluster'lar
# ---------------------------------------------------------------------------

log("=" * 78)
log("1) TARIC'İ CORE_UNIT OLARAK TAŞIYAN CLUSTER'LAR")
log("=" * 78)

taric_clusters = [cid for cid in real_comp_ids if TARIC in comps[cid]["core_units"]]
log(f"\n{len(taric_clusters)}/{len(real_comp_ids)} cluster Taric'i core_unit olarak taşıyor: {taric_clusters}\n")

for cid in taric_clusters:
    info = comps[cid]
    core_names = [units_static.get(u, {}).get("name", u) for u in info["core_units"]]
    log(f"{cid}  {info['name']}")
    log(f"    n_total={info['n_total']}  avg_placement={info['avg_placement']}  top4_rate={info['top4_rate']}")
    log(f"    core_units: {core_names}")
    taric_star = info["core_unit_avg_stars"][TARIC]
    log(f"    Taric core_unit_avg_star: avg={taric_star['avg']}  n={taric_star['n']}")

log("\nCentroid'ler arası cosine distance matrisi (Taric cluster'ları):")
header = "        " + "".join(f"{c:>9}" for c in taric_clusters)
log(header)
taric_centroids = {cid: np.array(comps[cid]["centroid"]) for cid in taric_clusters}
for a in taric_clusters:
    row_str = f"{a:>8}"
    for b in taric_clusters:
        d = cosine_distance(taric_centroids[a], taric_centroids[b]) if a != b else 0.0
        row_str += f"{d:>9.4f}"
    log(row_str)

# for reference: distance distribution across ALL 171 pairs, to know if
# Taric-pairs are unusually close or just ordinary
all_centroids = {cid: np.array(comps[cid]["centroid"]) for cid in real_comp_ids}
all_pairs = list(itertools.combinations(real_comp_ids, 2))
all_dists = sorted(cosine_distance(all_centroids[a], all_centroids[b]) for a, b in all_pairs)
taric_pairs = list(itertools.combinations(taric_clusters, 2))
taric_dists = sorted(cosine_distance(taric_centroids[a], taric_centroids[b]) for a, b in taric_pairs)
log(f"\nTüm 171 çiftin mesafe dağılımı: min={all_dists[0]:.4f} p10={all_dists[len(all_dists)//10]:.4f} "
    f"median={all_dists[len(all_dists)//2]:.4f} max={all_dists[-1]:.4f}")
log(f"Taric-Taric {len(taric_pairs)} çiftin mesafe dağılımı: min={taric_dists[0]:.4f} "
    f"median={taric_dists[len(taric_dists)//2]:.4f} max={taric_dists[-1]:.4f}")

# En yakın Taric-çiftinin, TÜM 171 çift arasındaki gerçek sırası (1 = en yakın çift).
sorted_all = sorted(all_dists)
closest_taric_rank = sum(1 for d in sorted_all if d < taric_dists[0]) + 1
log(f"En yakın Taric çifti ({taric_dists[0]:.4f}), tüm 171 çift arasında {closest_taric_rank}. sırada "
    f"({'en yakın 10 içinde' if closest_taric_rank <= 10 else 'en yakın 10 içinde değil'}).")

# ---------------------------------------------------------------------------
# 2) Cosine benzerliğinin boyut-bazlı ayrıştırılması
# ---------------------------------------------------------------------------

log("\n" + "=" * 78)
log("2) COSİNE BENZERLİĞİNİN BOYUT-BAZLI AYRIŞTIRILMASI (Taric çiftleri)")
log("=" * 78)
log("""
Vektörler L2-unit-norm olduğu için cosine similarity = dot product =
sum_i(cA[i] * cB[i]). Bir boyutun katkısı sadece HER İKİ centroid'de de
sıfırdan farklıysa anlamlı olabilir (biri sıfırsa çarpım zaten sıfır) —
yani matematiksel olarak "ortak olmayan" bir boyutun toplam benzerliğe
katkısı zaten yapısal olarak sıfırdır. Asıl soru bu yüzden şu: en çok katkı
veren boyutlar, HER İKİ cluster'ın da core_unit'i mi (ikisi için de
"tanımlayıcı"), yoksa sadece birinin core_unit'i olup diğerinde tesadüfen
(<%50 sıklıkla) mı bulunuyor?
""")

for a, b in taric_pairs:
    ca, cb = taric_centroids[a], taric_centroids[b]
    contributions = ca * cb  # elementwise — dot product'ın toplamı budur
    total = float(contributions.sum())
    order = np.argsort(-np.abs(contributions))[:10]
    log(f"\n--- {a} ({comps[a]['name']}) vs {b} ({comps[b]['name']}) — cosine similarity={total:.4f} (distance={1-total:.4f}) ---")
    core_a, core_b = set(comps[a]["core_units"]), set(comps[b]["core_units"])
    sum_both_core = 0.0
    sum_one_core = 0.0
    sum_neither_core = 0.0
    for idx in range(len(dim_ids)):
        c = float(contributions[idx])
        fid = dim_ids[idx]
        in_a, in_b = fid in core_a, fid in core_b
        if in_a and in_b:
            sum_both_core += c
        elif in_a or in_b:
            sum_one_core += c
        else:
            sum_neither_core += c
    log(f"  Katkı kırılımı: her ikisinde de core={sum_both_core:+.4f} ({100*sum_both_core/total:.1f}%)  "
        f"sadece birinde core={sum_one_core:+.4f} ({100*sum_one_core/total:.1f}%)  "
        f"ikisinde de core değil={sum_neither_core:+.4f} ({100*sum_neither_core/total:.1f}%)")
    log("  En çok katkı veren 10 boyut:")
    for idx in order:
        fid = dim_ids[idx]
        tag = "İKİSİNDE_DE_CORE" if (fid in core_a and fid in core_b) else ("BİR_CORE" if (fid in core_a or fid in core_b) else "-")
        log(f"    {dim_label(idx):28} a={ca[idx]:.3f} b={cb[idx]:.3f} katkı={contributions[idx]:+.4f} ({100*contributions[idx]/total:5.1f}%)  [{tag}]")

# ---------------------------------------------------------------------------
# 3 & 4) Karşı-olgusal ağırlıklandırma testleri
#
# İki ağırlıklandırma, hem Taric çiftlerine (3) hem TÜM 19 cluster'a (4) —
# aynı fonksiyonlarla, tek bir yerden hesaplanıp iki farklı alt kümede
# raporlanıyor ki iki test arasında bir tutarsızlık riski olmasın.
#
# Ağırlıklı centroid'in TÜRETİLİŞİ: orijinal `compute_centroids()` önce
# normalize edilmiş board vektörlerinin ORTALAMASINI alıp SONRA bir kez
# renormalize ediyordu. Sabit-per-dimension bir ağırlık (cost gibi) için
# "her board'u ağırlıklandır-normalize et-sonra ortala" ile "ortalamayı al,
# SONRA ağırlıklandır" matematiksel olarak denk (ağırlıklandırma ve ortalama
# değişmeli işlemler — w*mean(x) = mean(w*x)); renormalize tek bir kez en
# sonda yapılıyor, orijinal pipeline'la aynı yapı. İtem-sahibi ağırlıklandırma
# board-bazlı (bir unit'in item'ı olup olmadığı board'a göre değişir), o
# yüzden board vektörleri tek tek ağırlıklandırılıp SONRA ortalanıyor.
# ---------------------------------------------------------------------------

log("\n" + "=" * 78)
log("3 & 4) KARŞI-OLGUSAL AĞIRLIKLANDIRMA TESTLERİ")
log("=" * 78)

unit_dim_idx = {uid: i for i, uid in enumerate(unit_order)}
unit_cost_weight = np.array(
    [float(units_static.get(uid, {}).get("cost", 1)) for uid in unit_order] + [1.0] * len(trait_order)
)
log(f"\nCost ağırlık vektörü (unit boyutları) — min={unit_cost_weight[:n_unit_dims].min():.0f} "
    f"max={unit_cost_weight[:n_unit_dims].max():.0f}, trait boyutları 1.0'da sabit.")


def cost_weighted_centroid(cluster_id_num: int) -> np.ndarray:
    raw_mean = cluster_points(cluster_id_num).mean(axis=0)
    reweighted = raw_mean * unit_cost_weight
    norm = np.linalg.norm(reweighted)
    return reweighted / norm if norm > 0 else reweighted


def item_weighted_centroid(cluster_id_num: int) -> np.ndarray:
    idxs = np.where(hdbscan_labels == cluster_id_num)[0]
    acc = np.zeros(len(dim_ids))
    for i in idxs:
        vec = training_vectors[i].copy()
        for unit_id, unit_info in training_rows[i]["raw_units"].items():
            dim_idx = unit_dim_idx.get(unit_id)
            if dim_idx is None:
                continue
            if unit_info.get("item_count", 0) > 0:
                vec[dim_idx] *= 3.0
        acc += vec
    mean = acc / len(idxs)
    norm = np.linalg.norm(mean)
    return mean / norm if norm > 0 else mean


log("Ağırlıklı centroid'ler hesaplanıyor (19 cluster x 2 ağırlıklandırma)...")
baseline_centroids = all_centroids  # already loaded, exact stored values
cost_centroids = {cid: cost_weighted_centroid(_cid_to_num(cid)) for cid in real_comp_ids}
item_centroids = {cid: item_weighted_centroid(_cid_to_num(cid)) for cid in real_comp_ids}
log("Bitti.\n")


def report_pairs(pairs, label):
    log(f"--- {label} ---")
    log(f"{'Çift':20}{'baseline':>12}{'cost-ağırlıklı':>16}{'item-ağırlıklı':>16}")
    baseline_ds, cost_ds, item_ds = [], [], []
    for a, b in pairs:
        d0 = cosine_distance(baseline_centroids[a], baseline_centroids[b])
        d1 = cosine_distance(cost_centroids[a], cost_centroids[b])
        d2 = cosine_distance(item_centroids[a], item_centroids[b])
        baseline_ds.append(d0)
        cost_ds.append(d1)
        item_ds.append(d2)
        log(f"{a}-{b:16}{d0:12.4f}{d1:16.4f}{d2:16.4f}   Δcost={d1-d0:+.4f}  Δitem={d2-d0:+.4f}")
    log(f"\n  Ortalama mesafe: baseline={np.mean(baseline_ds):.4f}  "
        f"cost-ağırlıklı={np.mean(cost_ds):.4f} (Δ={np.mean(cost_ds)-np.mean(baseline_ds):+.4f})  "
        f"item-ağırlıklı={np.mean(item_ds):.4f} (Δ={np.mean(item_ds)-np.mean(baseline_ds):+.4f})")
    return baseline_ds, cost_ds, item_ds


log("### 3) Sadece Taric çiftleri (15 çift) ###\n")
report_pairs(taric_pairs, "Taric çiftleri")

log("\n### 4) TÜM 19 cluster çifti (171 çift) — yan etki kontrolü ###\n")
b_all, c_all, i_all = [], [], []
regressions_cost, regressions_item = [], []
for a, b in all_pairs:
    d0 = cosine_distance(baseline_centroids[a], baseline_centroids[b])
    d1 = cosine_distance(cost_centroids[a], cost_centroids[b])
    d2 = cosine_distance(item_centroids[a], item_centroids[b])
    b_all.append(d0)
    c_all.append(d1)
    i_all.append(d2)
    if d1 < d0 - 1e-6:
        regressions_cost.append((a, b, d0, d1))
    if d2 < d0 - 1e-6:
        regressions_item.append((a, b, d0, d2))

log(f"Ortalama mesafe (171 çift): baseline={np.mean(b_all):.4f}  "
    f"cost-ağırlıklı={np.mean(c_all):.4f} (Δ={np.mean(c_all)-np.mean(b_all):+.4f})  "
    f"item-ağırlıklı={np.mean(i_all):.4f} (Δ={np.mean(i_all)-np.mean(b_all):+.4f})")

# En yakın 10 çiftin sırası değişti mi?
def top10_closest(dists_list, pairs_list):
    idx_sorted = np.argsort(dists_list)[:10]
    return [pairs_list[i] for i in idx_sorted]

top10_baseline = top10_closest(b_all, all_pairs)
top10_cost = top10_closest(c_all, all_pairs)
top10_item = top10_closest(i_all, all_pairs)
log(f"\nEn yakın 10 çift (baseline):        {top10_baseline}")
log(f"En yakın 10 çift (cost-ağırlıklı):  {top10_cost}")
log(f"En yakın 10 çift (item-ağırlıklı):  {top10_item}")
log(f"Baseline vs cost'ta aynı kalan çift sayısı: {len(set(top10_baseline) & set(top10_cost))}/10")
log(f"Baseline vs item'da aynı kalan çift sayısı: {len(set(top10_baseline) & set(top10_item))}/10")

log(f"\n*** REGRESYON KONTROLÜ (Sadece Taric düzelip başka yer bozulursa iş yaramaz) ***")
log(f"Cost-ağırlıklandırmayla BEKLENMEDİK şekilde birbirine yaklaşan çift sayısı: {len(regressions_cost)}")
for a, b, d0, d1 in sorted(regressions_cost, key=lambda t: t[2] - t[3], reverse=True)[:15]:
    log(f"    {a}-{b}: baseline={d0:.4f} -> cost={d1:.4f}  (Δ={d1-d0:+.4f})")
log(f"Item-ağırlıklandırmayla BEKLENMEDİK şekilde birbirine yaklaşan çift sayısı: {len(regressions_item)}")
for a, b, d0, d2 in sorted(regressions_item, key=lambda t: t[2] - t[3], reverse=True)[:15]:
    log(f"    {a}-{b}: baseline={d0:.4f} -> item={d2:.4f}  (Δ={d2-d0:+.4f})")

# ---------------------------------------------------------------------------
# 5) Condensed tree / hiyerarşi
# ---------------------------------------------------------------------------

log("\n" + "=" * 78)
log("5) CONDENSED TREE / HİYERARŞİ (19 cluster arası)")
log("=" * 78)
log("""
Not — yöntem: sklearn'ün HDBSCAN'i (ayrı `hdbscan` paketinin aksine) condense
edilmiş ağacı dışarıya bir nesne olarak vermiyor; sadece ham
`_single_linkage_tree_` (tekli-bağlantı hiyerarşisi, scipy-linkage benzeri
structured array) ve private `_condense_tree()`/`_do_labelling()`
fonksiyonları var — ikisi arasında condensed-node-ID'den nihai cluster
etiketine (0..18) giden stabil bir public eşleme yok. Bu yüzden burada ham
`_single_linkage_tree_` DOĞRUDAN kullanıldı: her cluster için `probabilities_`
değeri en yüksek (HDBSCAN'in "en tipik" saydığı) BİR temsilci nokta seçilip,
birleşme mesafesi artan sırayla tekrar oynatılarak (union-find) bu 19
temsilcinin birbirine hangi mesafede/sırada bağlandığı ölçüldü. Bu, gerçek
condensed tree'nin node-ID'lerini birebir yeniden üretmez ama YOĞUNLUK-TABANLI
BİRLEŞME SIRASINI — hangi cluster çiftinin/grubunun önce, hangi mesafede ortak
bir ataya kavuştuğunu — HDBSCAN'in kendi ham ağacından, ek varsayım
eklemeden doğru şekilde verir.
""")

probabilities = model.probabilities_
representative = {}
for cid in real_comp_ids:
    idxs = np.where(hdbscan_labels == _cid_to_num(cid))[0]
    best = idxs[np.argmax(probabilities[idxs])]
    representative[cid] = int(best)

rep_to_cid = {v: k for k, v in representative.items()}
n_leaves = len(hdbscan_labels)
tree = model._single_linkage_tree_
log(f"_single_linkage_tree_: {len(tree)} birleşme satırı, {n_leaves} yaprak (eğitim noktası).")

parent = list(range(2 * n_leaves - 1))


def find(x: int) -> int:
    root = x
    while parent[root] != root:
        root = parent[root]
    while parent[x] != root:
        parent[x], x = root, parent[x]
    return root


# root_members: aktif union-find kökü -> o kökün altındaki temsilci cid'ler
# group_index: aktif kök -> scipy-linkage tarzı düğüm indeksi (0..18 = yapraklar
#              [19 temsilci nokta], 19.. = bu 19 nokta arasındaki gerçek birleşmeler)
root_members: dict[int, set] = {}
group_index: dict[int, int] = {}
for k, cid in enumerate(real_comp_ids):
    leaf = representative[cid]
    root_members[leaf] = {cid}
    group_index[leaf] = k

linkage_rows = []  # (idx_l, idx_r, dist, combined_size, cids_l, cids_r)
for i, row in enumerate(tree):
    left, right, dist = int(row["left_node"]), int(row["right_node"]), float(row["value"])
    new_id = n_leaves + i
    rl, rr = find(left), find(right)
    ml, mr = root_members.pop(rl, None), root_members.pop(rr, None)
    if ml and mr:
        idx_l, idx_r = group_index.pop(rl), group_index.pop(rr)
        new_idx = len(real_comp_ids) + len(linkage_rows)
        linkage_rows.append((idx_l, idx_r, dist, ml | mr, sorted(ml), sorted(mr)))
        group_index[new_id] = new_idx
        root_members[new_id] = ml | mr
    elif ml or mr:
        surviving_root = rl if ml else rr
        group_index[new_id] = group_index.pop(surviving_root)
        root_members[new_id] = ml or mr
    parent[rl] = new_id
    parent[rr] = new_id
    parent[new_id] = new_id

assert len(linkage_rows) == len(real_comp_ids) - 1, (
    f"beklenen {len(real_comp_ids) - 1} birleşme, bulunan {len(linkage_rows)} — "
    "temsilci noktalardan biri tekli-bağlantı ağacında kayıp/yinelenmiş olabilir"
)
log(f"\n19 temsilci nokta arasında {len(linkage_rows)} birleşme olayı bulundu (beklenen: 18) — union-find tutarlı.\n")

log("Birleşme sırası (yoğunluk mesafesi artan sırada — yani önce/en yakın birleşenler üstte):")
log(f"{'#':>3}  {'mesafe':>8}  {'grup boyutu':>12}  birleşen gruplar")
label_of_idx = {k: cid for k, cid in enumerate(real_comp_ids)}
for i, (idx_l, idx_r, dist, combined, cids_l, cids_r) in enumerate(linkage_rows):
    node_idx = len(real_comp_ids) + i
    label_of_idx[node_idx] = f"[{'+'.join(sorted(combined))}]"


def render_group(idx: int) -> str:
    return label_of_idx[idx]


for i, (idx_l, idx_r, dist, combined, cids_l, cids_r) in enumerate(linkage_rows):
    log(f"{i+1:>3}  {dist:>8.4f}  {len(combined):>12}  {render_group(idx_l)}  +  {render_group(idx_r)}  ->  {{{', '.join(sorted(combined))}}}")

log("\nTaric cluster'larının bu birleşme sırasındaki konumu:")
taric_set = set(taric_clusters)
for i, (idx_l, idx_r, dist, combined, cids_l, cids_r) in enumerate(linkage_rows):
    newly_combined_taric = combined & taric_set
    if len(newly_combined_taric) >= 2 and (set(cids_l) & taric_set) and (set(cids_r) & taric_set):
        log(f"  Birleşme #{i+1} (mesafe={dist:.4f}): Taric grupları {sorted(set(cids_l) & taric_set)} "
            f"ile {sorted(set(cids_r) & taric_set)} ortak bir atada buluşuyor.")

log("\nKarşılaştırma için: en yakın 3 birleşme (union-find, temsilci-noktalar üzerinden) "
    "ile Bölüm 1'deki centroid-cosine-distance en yakın çiftler örtüşüyor mu diye bakmak "
    "isteyen okuyucu için ilk 5 birleşmenin cid çiftleri:")
for i, (idx_l, idx_r, dist, combined, cids_l, cids_r) in enumerate(linkage_rows[:5]):
    log(f"  #{i+1}: {sorted(cids_l)} + {sorted(cids_r)}  (mesafe={dist:.4f})")

log("\n" + "=" * 78)
log("BİTTİ — bu script hiçbir dosyayı değiştirmedi, sadece data/processed/, "
    "data/static/ ve models/ altındaki mevcut çıktıları okudu.")
log("=" * 78)
