"""One-off diagnostic (2026-09-02) — full board profiles for the 6
not-yet-named subdivision pieces (c_21..c_26). Read-only, no HDBSCAN fit —
uses the already-persisted data/processed/cluster_labels.npy.

Usage: python scripts/measure_piece_profiles.py
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


PIECES = [21, 22, 23, 24, 25, 26]

for pid in PIECES:
    cid = f"c_{pid:02d}"
    idx = np.where(labels == pid)[0]
    n = len(idx)
    info = comps[cid]
    print("=" * 100)
    print(f"{cid}  n={n}  avg_placement={info['avg_placement']}  top4_rate={info['top4_rate']}")
    print("=" * 100)

    # --- top 12 units by frequency ---
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

    print("\nTOP 12 UNIT (frekans, ort_yıldız):")
    for uid, cnt in unit_present.most_common(12):
        freq = 100 * cnt / n
        avg_star = unit_star_sum[uid] / cnt
        print(f"  {unit_name(uid):20} freq=%{freq:5.1f}  ort_yıldız={avg_star:.2f}")

    # --- active traits: avg tier + mode breakpoint ---
    trait_tier_sum = collections.defaultdict(float)
    trait_tier_counts = collections.defaultdict(collections.Counter)
    for i in idx:
        for tid, tier in rows[i]["raw_traits"].items():
            trait_tier_sum[tid] += tier
            trait_tier_counts[tid][tier] += 1
    active = [(tid, s / n) for tid, s in trait_tier_sum.items() if s / n > 0.05]
    active.sort(key=lambda t: -t[1])
    print("\nAKTİF TRAIT'LER (ort_tier > 0.05), en sık 10:")
    for tid, avg_tier in active[:10]:
        mode_tier, mode_count = trait_tier_counts[tid].most_common(1)[0]
        bps = traits_static.get(tid, {}).get("breakpoints", [])
        bp_desc = f"{bps[mode_tier-1]}+ unit" if 0 < mode_tier <= len(bps) else "0"
        print(f"  {trait_name(tid):20} ort_tier={avg_tier:.3f}  en_sık_tier={mode_tier} (%{100*mode_count/n:.0f} board, {bp_desc})")

    # --- top 3 item-carrying units ---
    print("\nİTEM TAŞIYAN İLK 3 UNIT (item_count>=1 olan board oranı):")
    ranked = sorted(unit_item_ge1.items(), key=lambda kv: -kv[1])[:3]
    for uid, cnt in ranked:
        freq = 100 * cnt / n
        avg_items = unit_item_count_sum[uid] / unit_present[uid] if unit_present[uid] else 0
        top_items = unit_item_names[uid].most_common(3)
        items_str = ", ".join(f"{item_name(it)}({c})" for it, c in top_items)
        print(f"  {unit_name(uid):20} item%{freq:5.1f} (n={cnt})  ort_item_sayısı={avg_items:.2f}  top items: {items_str}")
    print()
