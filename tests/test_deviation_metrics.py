"""mean_deviation propagation across the deviation-stats pipeline (added for
the persistent metrics chip — Δmean must be correct and present everywhere
max_deviation/rmse already were)."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import geometry.candidates as C  # noqa: E402
from test_span_rebuild import RUN3, build_model  # noqa: E402


def test_mean_deviation_matches_point_dist_mean():
    xy = np.array([[0.0, 0.0], [1.0, 0.5], [2.0, -0.5], [3.0, 0.0]])
    elements = [{
        "type": "Line", "start": [0.0, 0.0], "end": [3.0, 0.0],
        "length": 3.0,
    }]
    stats = C.compute_deviation_stats(elements, xy)
    assert "mean_deviation" in stats
    assert stats["mean_deviation"] == pytest.approx(stats["point_dist"].mean())
    # sanity: strictly between 0 and max for this asymmetric offset pattern
    assert 0.0 < stats["mean_deviation"] < stats["max_deviation"]


def test_mean_deviation_empty_inputs():
    assert C.compute_deviation_stats([], np.zeros((0, 2)))["mean_deviation"] == 0.0
    assert C.compute_deviation_stats(
        [{"type": "Line", "start": [0, 0], "end": [1, 0], "length": 1.0}],
        np.zeros((0, 2)))["mean_deviation"] == 0.0


def test_metrics_from_stats_carries_mean():
    stats = {"max_deviation": 1.0, "rmse": 0.5, "mean_deviation": 0.3}
    m = C.metrics_from_stats(stats, [])
    assert m["mean_deviation"] == 0.3


def test_evaluate_candidate_has_mean_deviation():
    xy, ch = build_model(RUN3).xy_ref, build_model(RUN3).chainages_ref
    elements = build_model(RUN3).elements
    m = C.evaluate_candidate(elements, xy, ch)
    assert "mean_deviation" in m
    assert 0.0 <= m["mean_deviation"] <= m["max_deviation"]


def test_candidate_alignment_accepts_mean_deviation_kwarg():
    """CandidateAlignment must accept every key evaluate_candidate() returns
    via **metrics — this used to crash generation once mean_deviation was
    added to the stats dict but not to the dataclass."""
    c = C.CandidateAlignment(
        "level3", "Level 3", [],
        max_deviation=1.0, rmse=0.5, mean_deviation=0.3,
        n_elements=0, max_heading_jump_deg=0.0,
    )
    assert c.mean_deviation == 0.3


def test_span_snapshot_preserves_mean_deviation():
    """A failed-merge rollback (_span_snapshot/restore_span_snapshot) must
    not silently drop mean_deviation back to 0."""
    model = build_model(RUN3)
    assert model.last_stats.get("mean_deviation", 0.0) > 0.0
    snap = C._span_snapshot(model)
    assert snap["stats"]["mean_deviation"] == pytest.approx(
        model.last_stats["mean_deviation"])
