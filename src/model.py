"""Placement regressor (SPEC.md section 13).

Purpose (SPEC.md): when a user's board doesn't land near any cluster
centroid, the lookup-table approach (section 9/14) has nothing to say. This
model still produces a placement estimate for any board vector, cluster
match or not.

**Split is by match_id, not by participant.** All 8 participants of a match
share a match_id; if some end up in train and others in test, the model
can leak information about that lobby's outcome distribution across the
split and the reported metric would be optimistic and wrong. Unique
match_ids are split 80/20 first, then every participant row is assigned to
whichever side its match landed on — no match ever has participants on both
sides.

Baseline: a dummy that predicts 4.5 (the midpoint of 1-8) for everything.
If the real model doesn't beat this, that's reported as-is — SPEC.md
explicitly says not to tune parameters to force a win, and a ~3000-match
dev sample is not expected to produce a strong model.

Metrics: MAE and Spearman rank correlation (rank-based, doesn't assume a
linear relationship between predicted and actual placement).

**Predictions are clipped to [1, 8] (bulgu + düzeltme, 2026-08-30).** Raw
LightGBM output isn't constrained to a valid TFT placement — on this
dataset the unclipped test-set range measured [0.66, 8.68], mathematically
impossible values. `clip_predictions()` is defined once, here, and is the
only place that owns this rule — `cli.py` imports it rather than clipping
on its own. `evaluate()` reports both pre-clip and post-clip MAE/Spearman
side by side so a regression from clipping (there wasn't one — see
`run()`'s log) would be caught, not assumed away.

Output: models/lightgbm_model.pkl, and feature importances printed with
display names (static_data.py's units.json/traits.json), not raw ids.
"""

from __future__ import annotations

import json
import logging
import pickle
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
from scipy.stats import spearmanr
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import DATA_PROCESSED, DATA_STATIC, MODELS_DIR, ensure_dirs

logger = logging.getLogger(__name__)

VECTORS_PATH = DATA_PROCESSED / "features_vectors.npy"
META_ROWS_PATH = DATA_PROCESSED / "features_meta.jsonl"
STATIC_UNITS_PATH = DATA_STATIC / "units.json"
STATIC_TRAITS_PATH = DATA_STATIC / "traits.json"
META_JSON_PATH = DATA_PROCESSED / "meta.json"
MODEL_PATH = MODELS_DIR / "lightgbm_model.pkl"

BASELINE_PREDICTION = 4.5
TEST_FRACTION = 0.2
RANDOM_STATE = 0  # fixed for a reproducible split, not for tuning toward a nicer score
PLACEMENT_MIN = 1.0
PLACEMENT_MAX = 8.0


def _load_data() -> tuple[np.ndarray, list[str], list[int]]:
    if not VECTORS_PATH.exists() or not META_ROWS_PATH.exists():
        raise SystemExit(f"{VECTORS_PATH} / {META_ROWS_PATH} not found — run features.py first")

    vectors = np.load(VECTORS_PATH)
    with open(META_ROWS_PATH, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    if len(rows) != len(vectors):
        raise SystemExit(f"features_meta.jsonl ({len(rows)}) and features_vectors.npy ({len(vectors)}) are out of sync — rerun features.py")

    match_ids = [row["match_id"] for row in rows]
    placements = [row["placement"] for row in rows]
    return vectors, match_ids, placements


def match_id_train_test_split(
    match_ids: list[str], test_fraction: float = TEST_FRACTION, random_state: int = RANDOM_STATE
) -> tuple[np.ndarray, np.ndarray]:
    """Returns (train_indices, test_indices) into the original row order,
    split so that every row for a given match_id lands on the same side."""
    unique_matches = sorted(set(match_ids))
    train_matches, test_matches = train_test_split(unique_matches, test_size=test_fraction, random_state=random_state)
    train_matches, test_matches = set(train_matches), set(test_matches)

    train_idx = np.array([i for i, m in enumerate(match_ids) if m in train_matches])
    test_idx = np.array([i for i, m in enumerate(match_ids) if m in test_matches])
    return train_idx, test_idx


def _load_display_names() -> dict[str, str]:
    names: dict[str, str] = {}
    if STATIC_UNITS_PATH.exists():
        units = json.loads(STATIC_UNITS_PATH.read_text(encoding="utf-8"))
        names.update({uid: info["name"] for uid, info in units.items()})
    if STATIC_TRAITS_PATH.exists():
        traits = json.loads(STATIC_TRAITS_PATH.read_text(encoding="utf-8"))
        names.update({tid: info["name"] for tid, info in traits.items()})
    return names


def train_model(X_train: np.ndarray, y_train: list[int], feature_ids: list[str]) -> lgb.LGBMRegressor:
    model = lgb.LGBMRegressor(objective="regression", random_state=RANDOM_STATE, verbosity=-1)
    model.fit(X_train, y_train, feature_name=feature_ids)
    return model


def clip_predictions(predictions: np.ndarray) -> np.ndarray:
    """Raw LightGBM output isn't constrained to [PLACEMENT_MIN,
    PLACEMENT_MAX] — see module docstring for the measured unclipped range.
    Defined once here so cli.py can import it instead of clipping itself."""
    return np.clip(predictions, PLACEMENT_MIN, PLACEMENT_MAX)


def evaluate(model: lgb.LGBMRegressor, X_test: np.ndarray, y_test: list[int]) -> dict:
    y_test = np.asarray(y_test)
    raw_predictions = model.predict(X_test)
    predictions = clip_predictions(raw_predictions)
    baseline_predictions = np.full_like(y_test, BASELINE_PREDICTION, dtype=float)

    model_mae = mean_absolute_error(y_test, predictions)
    raw_mae = mean_absolute_error(y_test, raw_predictions)
    baseline_mae = mean_absolute_error(y_test, baseline_predictions)
    spearman_corr, _ = spearmanr(y_test, predictions)
    raw_spearman_corr, _ = spearmanr(y_test, raw_predictions)

    return {
        "model_mae": model_mae,
        "raw_mae": raw_mae,
        "baseline_mae": baseline_mae,
        "beats_baseline": model_mae < baseline_mae,
        "spearman": spearman_corr,
        "raw_spearman": raw_spearman_corr,
        "n_test": len(y_test),
        "raw_pred_range": (float(raw_predictions.min()), float(raw_predictions.max())),
        "clipped_pred_range": (float(predictions.min()), float(predictions.max())),
    }


def run() -> None:
    ensure_dirs()
    vectors, match_ids, placements = _load_data()
    n_unique_matches = len(set(match_ids))
    logger.info("Loaded %d participant rows across %d unique matches", len(vectors), n_unique_matches)

    train_idx, test_idx = match_id_train_test_split(match_ids)
    train_match_set = {match_ids[i] for i in train_idx}
    test_match_set = {match_ids[i] for i in test_idx}
    overlap = train_match_set & test_match_set
    if overlap:
        raise RuntimeError(f"Match-id split leaked {len(overlap)} match(es) into both train and test — this must never happen")
    logger.info(
        "Split by match_id: %d matches / %d rows train, %d matches / %d rows test (no overlap, confirmed)",
        len(train_match_set), len(train_idx), len(test_match_set), len(test_idx),
    )

    X_train, X_test = vectors[train_idx], vectors[test_idx]
    y_train = [placements[i] for i in train_idx]
    y_test = [placements[i] for i in test_idx]

    unit_order: list[str] = []
    trait_order: list[str] = []
    if META_JSON_PATH.exists():
        meta = json.loads(META_JSON_PATH.read_text(encoding="utf-8"))
        unit_order = meta.get("unit_order", [])
        trait_order = meta.get("trait_order", [])
    feature_ids = unit_order + trait_order
    if len(feature_ids) != vectors.shape[1]:
        raise SystemExit(
            f"meta.json has {len(feature_ids)} feature ids but features_vectors.npy has {vectors.shape[1]} "
            "columns — rerun features.py so they're back in sync"
        )

    model = train_model(X_train, y_train, feature_ids)
    metrics = evaluate(model, X_test, y_test)

    logger.info(
        "Model MAE: clipped=%.4f  raw(unclipped)=%.4f  vs baseline (always 4.5) MAE=%.4f -> model %s baseline",
        metrics["model_mae"], metrics["raw_mae"], metrics["baseline_mae"],
        "beats" if metrics["beats_baseline"] else "does NOT beat",
    )
    logger.info(
        "Spearman correlation (predicted vs actual placement): clipped=%.4f  raw=%.4f (n=%d)",
        metrics["spearman"], metrics["raw_spearman"], metrics["n_test"],
    )
    logger.info(
        "Prediction range: raw=[%.4f, %.4f] -> clipped=[%.4f, %.4f]",
        metrics["raw_pred_range"][0], metrics["raw_pred_range"][1],
        metrics["clipped_pred_range"][0], metrics["clipped_pred_range"][1],
    )
    if not metrics["beats_baseline"]:
        logger.warning(
            "Model does not beat the dummy baseline. Reported as-is per SPEC.md — "
            "not retuning parameters to force a better number on a ~%d-match dev sample.",
            n_unique_matches,
        )

    with open(MODEL_PATH, "wb") as f:
        pickle.dump(model, f)
    logger.info("Wrote %s", MODEL_PATH)

    display_names = _load_display_names()
    importances = model.feature_importances_
    ranked = sorted(zip(feature_ids, importances), key=lambda kv: -kv[1])
    logger.info("Top 20 feature importances:")
    for feature_id, importance in ranked[:20]:
        if importance == 0:
            continue
        logger.info("  %-25s %d", display_names.get(feature_id, feature_id), importance)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()


if __name__ == "__main__":
    main()
