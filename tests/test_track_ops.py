"""
Track-level split / merge helpers in osm.parser: chain_polylines,
split_track, nearest_track_node_index (Step 2 "Edit tracks" UI).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from osm.parser import (Track, chain_polylines, split_track,  # noqa: E402
                        nearest_track_node_index)


def _track(nodes, name="t", way_ids=None):
    return Track(way_ids=way_ids or [1], nodes=nodes, name=name)


class TestChainPolylines:

    def test_merges_two_close_tracks(self):
        t1 = _track([(50.0, 14.0), (50.001, 14.001), (50.002, 14.002)], "A")
        t2 = _track([(50.0021, 14.0021), (50.003, 14.003), (50.004, 14.004)], "B")
        merged = chain_polylines([t1, t2], gap_tol_m=50.0)
        assert merged is not None
        assert len(merged.nodes) == 6
        assert merged.nodes[0] == t1.nodes[0]
        assert merged.nodes[-1] == t2.nodes[-1]

    def test_reverses_track_when_needed(self):
        t1 = _track([(50.0, 14.0), (50.001, 14.001), (50.002, 14.002)], "A")
        # t2's LAST node is the one near t1's tail -> must be reversed to
        # attach correctly, ending the merge at t2's FIRST node.
        t2 = _track([(50.004, 14.004), (50.003, 14.003), (50.0021, 14.0021)], "B")
        merged = chain_polylines([t1, t2], gap_tol_m=50.0)
        assert merged is not None
        assert len(merged.nodes) == 6
        assert merged.nodes[-1] == t2.nodes[0]

    def test_attaches_at_head_when_closer(self):
        # t2's head sits ~31 m from t1's head (well within tolerance);
        # every other endpoint pairing is >500 m away.
        t1 = _track([(50.00, 14.00), (50.01, 14.01)], "A")
        t2 = _track([(49.9998, 13.9998), (50.005, 14.005)], "B")
        merged = chain_polylines([t1, t2], gap_tol_m=50.0)
        assert merged is not None
        assert merged.nodes[0] == t2.nodes[0]
        assert merged.nodes[-1] == t1.nodes[-1]

    def test_order_independent_three_tracks(self):
        t1 = _track([(50.0, 14.0), (50.001, 14.001)], "A")
        t2 = _track([(50.002, 14.002), (50.003, 14.003)], "B")
        t3 = _track([(50.0011, 14.0011), (50.0019, 14.0019)], "C")
        merged = chain_polylines([t1, t2, t3], gap_tol_m=200.0)
        assert merged is not None
        assert len(merged.nodes) == 6

    def test_rejects_far_tracks(self):
        t1 = _track([(50.0, 14.0), (50.001, 14.001)], "A")
        t2 = _track([(51.0, 15.0), (51.001, 15.001)], "B")
        assert chain_polylines([t1, t2], gap_tol_m=50.0) is None

    def test_single_track_returns_none(self):
        t1 = _track([(50.0, 14.0), (50.001, 14.001)], "A")
        assert chain_polylines([t1], gap_tol_m=50.0) is None

    def test_empty_nodes_returns_none(self):
        t1 = _track([(50.0, 14.0), (50.001, 14.001)], "A")
        t2 = _track([], "empty")
        assert chain_polylines([t1, t2], gap_tol_m=50.0) is None

    def test_way_ids_combined(self):
        t1 = _track([(50.0, 14.0), (50.001, 14.001)], "A", way_ids=[1, 2])
        t2 = _track([(50.0011, 14.0011), (50.002, 14.002)], "B", way_ids=[3])
        merged = chain_polylines([t1, t2], gap_tol_m=50.0)
        assert set(merged.way_ids) == {1, 2, 3}


class TestSplitTrack:

    def test_splits_with_shared_boundary(self):
        t = _track([(50.0, 14.0), (50.001, 14.001), (50.002, 14.002),
                    (50.003, 14.003)], "D")
        parts = split_track(t, 1)
        assert parts is not None
        first, second = parts
        assert first.nodes[-1] == second.nodes[0]     # no gap
        assert first.nodes == t.nodes[:2]
        assert second.nodes == t.nodes[1:]
        assert "(1/2)" in first.name and "(2/2)" in second.name

    def test_rejects_split_at_endpoints(self):
        t = _track([(50.0, 14.0), (50.001, 14.001), (50.002, 14.002),
                    (50.003, 14.003)], "D")
        assert split_track(t, 0) is None
        assert split_track(t, 3) is None

    def test_rejects_too_short_track(self):
        t = _track([(50.0, 14.0), (50.001, 14.001)], "D")
        assert split_track(t, 0) is None
        assert split_track(t, 1) is None

    def test_total_nodes_conserved_plus_one(self):
        t = _track([(50.0, 14.0), (50.001, 14.001), (50.002, 14.002),
                    (50.003, 14.003), (50.004, 14.004)], "D")
        first, second = split_track(t, 2)
        assert len(first.nodes) + len(second.nodes) == len(t.nodes) + 1


class TestNearestTrackNodeIndex:

    def test_exact_match(self):
        t = _track([(50.0, 14.0), (50.001, 14.001), (50.002, 14.002)], "D")
        assert nearest_track_node_index(t, (50.001, 14.001)) == 1

    def test_nearest_for_offset_point(self):
        t = _track([(50.0, 14.0), (50.001, 14.001), (50.002, 14.002)], "D")
        idx = nearest_track_node_index(t, (50.0009, 14.0011))
        assert idx == 1
