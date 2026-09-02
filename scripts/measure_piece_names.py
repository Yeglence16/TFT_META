"""One-off diagnostic (2026-09-02) — naming measurement for the 8 new
subdivision pieces (src/subdivide.py's temp names). Read-only, no HDBSCAN
fit — uses the already-persisted data/processed/cluster_labels.npy from
subdivide.py's run (c_19..c_26). Goal: propose real names a player could
recognize from their OWN board, not centroid-jargon ("zayıf"/"ağırlıklı").

Usage: python scripts/measure_piece_names.py
"""

from __future__ import annotations

import collections
import json
import statistics
import sys
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import DATA_PROCESSED, DATA_STATIC

with open(DATA_PROCESSED / "features_meta.jsonl", encoding="utf-8") as f:
    rows = [json.loads(line) for line in f]
labels = np.load(DATA_PROCESSED / "cluster_labels.npy")
comps = json.loads((DATA_PROCESSED / "comps.json").read_text(encoding="utf-8"))
units_static = json.loads((DATA_STATIC / "units.json").read_text(encoding="utf-8"))
traits_static = json.loads((DATA_STATIC / "traits.json").read_text(encoding="utf-8"))
assert len(labels) == len(rows)


def unit_name(uid: str) -> str:
    return units_static.get(uid, {}).get("name", uid)


def trait_name(tid: str) -> str:
    return traits_static.get(tid, {}).get("name", tid)


def log(*a):
    print(*a)


PIECE_TEMP_NAMES = {
    19: "Kayle-A", 20: "Kayle-B",
    21: "Cassiopeia-A", 22: "Cassiopeia-B", 23: "Cassiopeia-C", 24: "Cassiopeia-D",
    25: "Aphelios-A", 26: "Aphelios-B",
}
piece_idx = {pid: np.where(labels == pid)[0] for pid in PIECE_TEMP_NAMES}
for pid, idx in piece_idx.items():
    log(f"c_{pid:02d} ({PIECE_TEMP_NAMES[pid]}): n={len(idx)}")
log("")


def avg_unit_count(idx) -> float:
    return statistics.mean(len(rows[i]["raw_units"]) for i in idx)


def unit_freq_and_stars(idx, unit_id: str):
    n = len(idx)
    present = 0
    star_counts: collections.Counter = collections.Counter()
    for i in idx:
        info = rows[i]["raw_units"].get(unit_id)
        if info:
            present += 1
            star_counts[info["tier"]] += 1
    freq = present / n
    avg_star = sum(k * v for k, v in star_counts.items()) / present if present else 0.0
    return freq, dict(star_counts), avg_star, present


def all_units_in(idx) -> set:
    s = set()
    for i in idx:
        s.update(rows[i]["raw_units"].keys())
    return s


def unit_freq_diff_table(idx_a, idx_b, label_a: str, label_b: str, top_k: int = 15):
    units_seen = all_units_in(idx_a) | all_units_in(idx_b)
    rows_out = []
    for u in units_seen:
        fa, _, _, _ = unit_freq_and_stars(idx_a, u)
        fb, _, _, _ = unit_freq_and_stars(idx_b, u)
        rows_out.append((u, fa, fb, fa - fb))
    rows_out.sort(key=lambda t: -abs(t[3]))
    log(f"  En büyük frekans farkları ({label_a} - {label_b}), ilk {top_k}:")
    for u, fa, fb, diff in rows_out[:top_k]:
        log(f"    {unit_name(u):20} {label_a}=%{100*fa:5.1f}  {label_b}=%{100*fb:5.1f}  fark={100*diff:+.1f}pp")
    return rows_out


def trait_tier_diff_table(idx_a, idx_b, label_a: str, label_b: str, top_k: int = 10):
    def mean_tiers(idx):
        totals: dict = collections.defaultdict(float)
        for i in idx:
            for t, tier in rows[i]["raw_traits"].items():
                totals[t] += tier
        return {t: v / len(idx) for t, v in totals.items()}

    ma, mb = mean_tiers(idx_a), mean_tiers(idx_b)
    all_traits = set(ma) | set(mb)
    diffs = sorted(((t, ma.get(t, 0), mb.get(t, 0), ma.get(t, 0) - mb.get(t, 0)) for t in all_traits), key=lambda t: -abs(t[3]))
    log(f"  En büyük ortalama-tier farkları ({label_a} - {label_b}), ilk {top_k}:")
    for t, va, vb, diff in diffs[:top_k]:
        log(f"    {trait_name(t):20} {label_a}={va:.3f}  {label_b}={vb:.3f}  fark={diff:+.3f}")
    return diffs


# ---------------------------------------------------------------------------
# 1) Kayle-A (c_19, Hecarim'siz) vs Kayle-B (c_20, Hecarim'li)
# ---------------------------------------------------------------------------

log("=" * 110)
log("1) KAYLE-A (c_19) vs KAYLE-B (c_20) — HECARIM'İN YERİNİ NE ALIYOR")
log("=" * 110)
a, b = piece_idx[19], piece_idx[20]
log(f"Ortalama unit sayısı: Kayle-A={avg_unit_count(a):.3f}  Kayle-B={avg_unit_count(b):.3f}")
fa, sa, avg_a, pa = unit_freq_and_stars(a, "DA_18_Hecarim")
fb, sb, avg_b, pb = unit_freq_and_stars(b, "DA_18_Hecarim")
log(f"Hecarim: Kayle-A %{100*fa:.1f} (n={pa}, ort_yıldız={avg_a:.2f})  Kayle-B %{100*fb:.1f} (n={pb}, ort_yıldız={avg_b:.2f}, dağılım={sb})")
diff_table_kayle = unit_freq_diff_table(a, b, "Kayle-A", "Kayle-B")
log("")
log("Hecarim'in kendi trait'leri: " + ", ".join(trait_name(t) for t in units_static.get("DA_18_Hecarim", {}).get("traits", [])))
trait_tier_diff_table(a, b, "Kayle-A", "Kayle-B")
log("")

# ---------------------------------------------------------------------------
# 2) Aphelios-A (c_25, Alune'suz, n büyük) vs Aphelios-B (c_26, Alune'lu, n küçük)
#    NOT: kullanıcının mesajında "Aphelios-B (Alune'suz)" deniyor ama gerçek
#    atamada Aphelios-B = Alune'LU (küçük parça); Aphelios-A = Alune'SUZ
#    (büyük parça). Ölçüm ASIL soruyu (Alune'un yerini ne alıyor) doğru
#    parça üzerinden yapıyor — isim eşleşmesi aşağıda ayrıca not edildi.
# ---------------------------------------------------------------------------

log("=" * 110)
log("2) APHELIOS-A (c_25, Alune'SUZ) vs APHELIOS-B (c_26, Alune'LU) — ALUNE'UN YERİNİ NE ALIYOR")
log("=" * 110)
log("NOT: kullanıcı mesajında 'Aphelios-B (Alune'suz)' deniyordu — gerçek atamada tam tersi "
    "(Aphelios-B = Alune'lu, küçük parça; Aphelios-A = Alune'suz, büyük parça). Aşağıdaki ölçüm "
    "ALUNE'UN GERÇEKTEN EKSİK OLDUĞU parça (Aphelios-A, c_25) üzerinden yapıldı.\n")
a, b = piece_idx[25], piece_idx[26]
log(f"Ortalama unit sayısı: Aphelios-A={avg_unit_count(a):.3f}  Aphelios-B={avg_unit_count(b):.3f}")
fa, sa, avg_a, pa = unit_freq_and_stars(a, "DA_18_Alune")
fb, sb, avg_b, pb = unit_freq_and_stars(b, "DA_18_Alune")
log(f"Alune: Aphelios-A %{100*fa:.1f} (n={pa})  Aphelios-B %{100*fb:.1f} (n={pb}, ort_yıldız={avg_b:.2f}, dağılım={sb})")
unit_freq_diff_table(a, b, "Aphelios-A", "Aphelios-B")
log("")
log("Alune'un kendi trait'leri: " + ", ".join(trait_name(t) for t in units_static.get("DA_18_Alune", {}).get("traits", [])))
trait_tier_diff_table(a, b, "Aphelios-A", "Aphelios-B")
log("")

# ---------------------------------------------------------------------------
# 3) Cassiopeia'nın 4 parçası
# ---------------------------------------------------------------------------

log("=" * 110)
log("3) CASSIOPEIA'NIN 4 PARÇASI (c_21 A, c_22 B, c_23 C, c_24 D)")
log("=" * 110)
cass_ids = [21, 22, 23, 24]
for pid in cass_ids:
    idx = piece_idx[pid]
    log(f"--- {PIECE_TEMP_NAMES[pid]} (c_{pid:02d}, n={len(idx)}) ---")
    for unit_id, label in [("DA_18_Lillia", "Lillia"), ("DA_18_Soraka", "Soraka")]:
        freq, stars, avg_star, present = unit_freq_and_stars(idx, unit_id)
        star_pct = {k: f"%{100*v/present:.0f}" for k, v in sorted(stars.items())} if present else {}
        log(f"    {label}: frekans=%{100*freq:.1f} (n={present})  ort_yıldız={avg_star:.2f}  yıldız_dağılımı(1/2/3)={star_pct}")
    log(f"    ortalama unit sayısı: {avg_unit_count(idx):.3f}")

log("\nCentroid farkının kaynağı (frekans mı yıldız mı):")
log("  20 vs 21 (Lillia farkı) karşılığı burada: Cassiopeia-B(c_22) vs Cassiopeia-A(c_21)")
for u, label in [("DA_18_Lillia", "Lillia")]:
    for pid_x, pid_y in [(21, 22)]:
        fx, sx, avgx, nx = unit_freq_and_stars(piece_idx[pid_x], u)
        fy, sy, avgy, ny = unit_freq_and_stars(piece_idx[pid_y], u)
        log(f"    {label}: {PIECE_TEMP_NAMES[pid_x]} freq=%{100*fx:.1f} ort_yıldız={avgx:.2f}  vs  "
            f"{PIECE_TEMP_NAMES[pid_y]} freq=%{100*fy:.1f} ort_yıldız={avgy:.2f}")
for u, label in [("DA_18_Soraka", "Soraka")]:
    for pid_x, pid_y in [(23, 24)]:
        fx, sx, avgx, nx = unit_freq_and_stars(piece_idx[pid_x], u)
        fy, sy, avgy, ny = unit_freq_and_stars(piece_idx[pid_y], u)
        log(f"    {label}: {PIECE_TEMP_NAMES[pid_x]} freq=%{100*fx:.1f} ort_yıldız={avgx:.2f}  vs  "
            f"{PIECE_TEMP_NAMES[pid_y]} freq=%{100*fy:.1f} ort_yıldız={avgy:.2f}")

log("\nDiğer eksenler — parçalar arası trait-tier farkları:")
for pid_x, pid_y in [(21, 22), (21, 23), (21, 24), (22, 23), (22, 24), (23, 24)]:
    log(f"\n  {PIECE_TEMP_NAMES[pid_x]} vs {PIECE_TEMP_NAMES[pid_y]}:")
    trait_tier_diff_table(piece_idx[pid_x], piece_idx[pid_y], PIECE_TEMP_NAMES[pid_x], PIECE_TEMP_NAMES[pid_y], top_k=5)

log("\nİtem dağılımı — her parçada en çok item taşıyan (carry) unit ve en sık kullanılan item'lar:")
for pid in cass_ids:
    idx = piece_idx[pid]
    item_carrier_counts: dict = collections.defaultdict(int)
    item_counts: dict = collections.defaultdict(int)
    for i in idx:
        for unit_id, info in rows[i]["raw_units"].items():
            if info["item_count"] >= 2:
                item_carrier_counts[unit_id] += 1
            for it in info.get("items", []):
                item_counts[(unit_id, it)] += 1
    top_carrier = sorted(item_carrier_counts.items(), key=lambda kv: -kv[1])[:3]
    log(f"  {PIECE_TEMP_NAMES[pid]}: en sık 2+ item taşıyan unit'ler: {[(unit_name(u), c) for u, c in top_carrier]}")

log("")

# ---------------------------------------------------------------------------
# 4) İsim önerileri
# ---------------------------------------------------------------------------

log("=" * 110)
log("4) ÖLÇÜM SONRASI İSİM ÖNERİLERİ")
log("=" * 110)
log("Aşağıdaki bölümde metin raporu var (script çıktısı değil, chat yanıtında sunulacak).")
