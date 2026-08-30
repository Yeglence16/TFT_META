import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.model import (
    BASELINE_PREDICTION,
    PLACEMENT_MAX,
    PLACEMENT_MIN,
    clip_predictions,
    evaluate,
    match_id_train_test_split,
)


def test_match_id_split_never_splits_a_single_match_across_train_and_test():
    # 100 matches, 8 participant rows each, same as real data shape.
    match_ids = [f"m{i}" for i in range(100) for _ in range(8)]
    train_idx, test_idx = match_id_train_test_split(match_ids, test_fraction=0.25, random_state=1)

    train_matches = {match_ids[i] for i in train_idx}
    test_matches = {match_ids[i] for i in test_idx}
    assert train_matches.isdisjoint(test_matches)

    # every row accounted for exactly once
    assert len(train_idx) + len(test_idx) == len(match_ids)
    assert set(train_idx.tolist()) | set(test_idx.tolist()) == set(range(len(match_ids)))


def test_match_id_split_is_reproducible_with_fixed_seed():
    match_ids = [f"m{i}" for i in range(50) for _ in range(8)]
    train1, test1 = match_id_train_test_split(match_ids, random_state=42)
    train2, test2 = match_id_train_test_split(match_ids, random_state=42)
    assert list(train1) == list(train2)
    assert list(test1) == list(test2)


class _StubModel:
    """Predicts a fixed value per row, so evaluate()'s MAE/Spearman math can
    be checked against hand-computed numbers without training a real model."""

    def __init__(self, predictions):
        self._predictions = np.asarray(predictions, dtype=float)

    def predict(self, X):
        return self._predictions


def test_evaluate_mae_and_baseline_comparison():
    y_test = [1, 4, 8]
    model = _StubModel([1, 4, 8])  # perfect predictions
    metrics = evaluate(model, X_test=np.zeros((3, 1)), y_test=y_test)

    assert metrics["model_mae"] == pytest.approx(0.0)
    # baseline always predicts 4.5: |1-4.5|+|4-4.5|+|8-4.5| = 3.5+0.5+3.5=7.5, /3 = 2.5
    assert metrics["baseline_mae"] == pytest.approx(2.5)
    assert metrics["beats_baseline"] is True
    assert metrics["spearman"] == pytest.approx(1.0)


def test_evaluate_reports_honestly_when_model_loses_to_baseline():
    y_test = [1, 2, 3, 4, 5, 6, 7, 8]
    # A deliberately bad model: predicts the reverse rank each time.
    model = _StubModel([8, 7, 6, 5, 4, 3, 2, 1])
    metrics = evaluate(model, X_test=np.zeros((8, 1)), y_test=y_test)

    assert metrics["beats_baseline"] is False
    assert metrics["spearman"] == pytest.approx(-1.0)


def test_baseline_prediction_constant_is_midpoint_of_placement_range():
    assert BASELINE_PREDICTION == 4.5


def test_placement_bounds_match_tft_range():
    assert PLACEMENT_MIN == 1.0
    assert PLACEMENT_MAX == 8.0


def test_clip_predictions_clamps_out_of_range_values():
    predictions = np.array([-5.0, 0.5, 1.0, 4.5, 8.0, 9.2, 20.0])
    assert clip_predictions(predictions).tolist() == [1.0, 1.0, 1.0, 4.5, 8.0, 8.0, 8.0]


def test_clip_predictions_leaves_in_range_values_unchanged():
    predictions = np.array([1.0, 2.3, 4.5, 6.7, 8.0])
    assert clip_predictions(predictions).tolist() == predictions.tolist()


def test_evaluate_clips_predictions_before_scoring():
    y_test = [1, 1, 1]
    # A model that overshoots below the valid range on every row — this is
    # the real, observed failure mode (raw test-set range measured
    # [0.66, 8.68] on the actual dataset).
    model = _StubModel([-3.0, -3.0, -3.0])
    metrics = evaluate(model, X_test=np.zeros((3, 1)), y_test=y_test)

    assert metrics["model_mae"] == pytest.approx(0.0)  # clipped to 1.0 == actual placement 1
    assert metrics["raw_mae"] == pytest.approx(4.0)  # |1 - (-3)| = 4, unclipped
    assert metrics["raw_pred_range"] == (-3.0, -3.0)
    assert metrics["clipped_pred_range"] == (1.0, 1.0)


def test_evaluate_reports_identical_clipped_and_raw_metrics_when_already_in_range():
    y_test = [1, 4, 8]
    model = _StubModel([1, 4, 8])
    metrics = evaluate(model, X_test=np.zeros((3, 1)), y_test=y_test)
    assert metrics["model_mae"] == pytest.approx(metrics["raw_mae"])
    assert metrics["spearman"] == pytest.approx(metrics["raw_spearman"])
