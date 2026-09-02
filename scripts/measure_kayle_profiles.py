"""One-off diagnostic (2026-09-02) — Kayle-A/B (c_19,c_20) board profiles,
diff-only format (matching scripts/measure_piece_profiles.py's metrics, but
collapsing anything within a "same" threshold into one summary line instead
of listing it). Read-only, no HDBSCAN fit.

"Same" thresholds (stated explicitly, not hidden): unit freq diff <=5pp AND
avg-star diff <=0.15; trait avg-tier diff <=0.15; item-carrier same unit in
both top-3 with freq diff <=5pp.

Usage: python scripts/measure_kayle_profiles.py
"""

from __future__ import annotations

import collections
import json
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
items_static = json.loads((DATA_STATIC / "items.json").read_text(encoding="utf-8"))
assert len(labels) == len(rows)


def unit_name(uid):
    return units_static.get(uid, {}).get("name", uid)


def trait_name(tid):
    return traits_static.get(tid, {}).get("name", tid)


def item_name(iid):
    return items_static.get(iid, {}).get("name", iid)


def profile(pid):
    idx = np.where(labels == pid)[0]
    n = len(idx)
    unit_present = collections.Counter()
    unit_star_sum = collections.Counter()
    unit_item_ge1 = collections.Counter()
    unit_item_count_sum = collections.Counter()
    unit_item_names = collections.defaultdict(collections.Counter)
    for i in idx:
        for uid, uinfo in rows[i]["raw_units"].items():
            unit_present[uid] += 1
            unit_star_sum[uid] += uinfo["tier"]
            unit_item_count_sum[uid] += uinfo["item_count"]
            if uinfo["item_count"] >= 1:
                unit_item_ge1[uid] += 1
            for it in uinfo.get("items", []):
                unit_item_names[uid][it] += 1
    trait_tier_sum = collections.defaultdict(float)
    trait_tier_counts = collections.defaultdict(collections.Counter)
    for i in idx:
        for tid, tier in rows[i]["raw_traits"].items():
            trait_tier_sum[tid] += tier
            trait_tier_counts[tid][tier] += 1
    return {
        "n": n, "idx": idx,
        "unit_present": unit_present, "unit_star_sum": unit_star_sum,
        "unit_item_ge1": unit_item_ge1, "unit_item_count_sum": unit_item_count_sum,
        "unit_item_names": unit_item_names,
        "trait_tier_sum": trait_tier_sum, "trait_tier_counts": trait_tier_counts,
        "avg_unit_count": sum(len(rows[i]["raw_units"]) for i in idx) / n,
    }


A, B = 19, 20
pa, pb = profile(A), profile(B)
cid_a, cid_b = f"c_{A:02d}", f"c_{B:02d}"

print("=" * 100)
print(f"ÖZET   {cid_a}: n={pa['n']} avg_placement={comps[cid_a]['avg_placement']} top4_rate={comps[cid_a]['top4_rate']} ort_unit_sayısı={pa['avg_unit_count']:.3f}")
print(f"       {cid_b}: n={pb['n']} avg_placement={comps[cid_b]['avg_placement']} top4_rate={comps[cid_b]['top4_rate']} ort_unit_sayısı={pb['avg_unit_count']:.3f}")
print("=" * 100)

FREQ_THRESH = 5.0  # percentage points
STAR_THRESH = 0.15

top_a = [u for u, _ in pa["unit_present"].most_common(12)]
top_b = [u for u, _ in pb["unit_present"].most_common(12)]
union_units = list(dict.fromkeys(top_a + top_b))  # preserve order, top_a first

print("\nTOP 12 UNIT (frekans%, ort_yıldız) — SADECE FARKLILAR:")
same_units = []
diff_rows = []
for u in union_units:
    na, sa_ = pa["unit_present"].get(u, 0), pa["unit_star_sum"].get(u, 0)
    nb, sb_ = pb["unit_present"].get(u, 0), pb["unit_star_sum"].get(u, 0)
    fa = 100 * na / pa["n"]
    fb = 100 * nb / pb["n"]
    stara = sa_ / na if na else 0.0
    starb = sb_ / nb if nb else 0.0
    if abs(fa - fb) <= FREQ_THRESH and abs(stara - starb) <= STAR_THRESH:
        same_units.append((u, fa, stara))
    else:
        diff_rows.append((u, fa, stara, fb, starb))
diff_rows.sort(key=lambda t: -abs(t[1] - t[3]))
for u, fa, stara, fb, starb in diff_rows:
    print(f"  {unit_name(u):20} {cid_a}=%{fa:5.1f}/{stara:.2f}★   {cid_b}=%{fb:5.1f}/{starb:.2f}★")
if same_units:
    names = ", ".join(f"{unit_name(u)}(%{f:.0f}/{s:.1f}★)" for u, f, s in same_units)
    print(f"  [İKİSİNDE DE AYNI, ±{FREQ_THRESH}pp/±{STAR_THRESH}★ içinde]: {names}")

print("\nAKTİF TRAİT'LER (ort_tier>0.05 en az birinde) — SADECE FARKLILAR:")
all_traits = set(t for t, v in pa["trait_tier_sum"].items() if v / pa["n"] > 0.05) | set(t for t, v in pb["trait_tier_sum"].items() if v / pb["n"] > 0.05)
same_traits = []
diff_traits = []
for t in all_traits:
    va = pa["trait_tier_sum"].get(t, 0) / pa["n"]
    vb = pb["trait_tier_sum"].get(t, 0) / pb["n"]
    if abs(va - vb) <= STAR_THRESH:
        same_traits.append((t, va, vb))
    else:
        mode_a = pa["trait_tier_counts"][t].most_common(1)[0][0] if pa["trait_tier_counts"][t] else 0
        mode_b = pb["trait_tier_counts"][t].most_common(1)[0][0] if pb["trait_tier_counts"][t] else 0
        bps = traits_static.get(t, {}).get("breakpoints", [])
        bp_a = f"{bps[mode_a-1]}+ unit" if 0 < mode_a <= len(bps) else "0"
        bp_b = f"{bps[mode_b-1]}+ unit" if 0 < mode_b <= len(bps) else "0"
        diff_traits.append((t, va, mode_a, bp_a, vb, mode_b, bp_b))
diff_traits.sort(key=lambda t: -abs(t[1] - t[4]))
for t, va, ma, bpa, vb, mb, bpb in diff_traits:
    print(f"  {trait_name(t):20} {cid_a}=ort{va:.3f} (en_sık tier{ma}, {bpa})   {cid_b}=ort{vb:.3f} (en_sık tier{mb}, {bpb})")
if same_traits:
    names = ", ".join(f"{trait_name(t)}({va:.2f})" for t, va, vb in same_traits)
    print(f"  [İKİSİNDE DE AYNI, ±{STAR_THRESH} içinde]: {names}")

print("\nİTEM TAŞIYAN İLK 3 UNIT — SADECE FARKLILAR:")
top3_a = sorted(pa["unit_item_ge1"].items(), key=lambda kv: -kv[1])[:3]
top3_b = sorted(pb["unit_item_ge1"].items(), key=lambda kv: -kv[1])[:3]
units_a = {u: c for u, c in top3_a}
units_b = {u: c for u, c in top3_b}
carrier_union = list(dict.fromkeys([u for u, _ in top3_a] + [u for u, _ in top3_b]))
for u in carrier_union:
    ca = units_a.get(u, 0)
    cb = units_b.get(u, 0)
    fa = 100 * ca / pa["n"] if ca else 0.0
    fb = 100 * cb / pb["n"] if cb else 0.0
    items_a = pa["unit_item_names"][u].most_common(3)
    items_b = pb["unit_item_names"][u].most_common(3)
    in_top3_a = u in units_a
    in_top3_b = u in units_b
    if in_top3_a and in_top3_b and abs(fa - fb) <= FREQ_THRESH:
        print(f"  [İKİSİNDE DE AYNI, top-3'te] {unit_name(u):20} %{fa:.0f} vs %{fb:.0f}  {[item_name(i) for i,_ in items_a]}")
    else:
        loc_a = f"%{fa:.0f} (top3)" if in_top3_a else f"%{fa:.0f} (top3 dışı)"
        loc_b = f"%{fb:.0f} (top3)" if in_top3_b else f"%{fb:.0f} (top3 dışı)"
        print(f"  {unit_name(u):20} {cid_a}={loc_a} {[item_name(i) for i,_ in items_a] if in_top3_a else ''}   "
              f"{cid_b}={loc_b} {[item_name(i) for i,_ in items_b] if in_top3_b else ''}")
