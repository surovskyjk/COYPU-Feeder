"""
Unit tests for src/geometry/elevation.py

Tests cover:
  - _identify_one: ČÚZK DMR 5G per-point response parsing (success, NoData,
    error key, HTTP failure) — mocked, no network.
  - _sample_cuzk_dmr5g: result order preserved under concurrency.
  - sample_elevations: source dispatch and the cuzk_dmr5g -> opentopodata
    per-point fallback for missing values.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from geometry import elevation


# ---------------------------------------------------------------------------
# _identify_one
# ---------------------------------------------------------------------------

def _fake_session(json_body=None, status_code=200, raise_exc=None):
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    if raise_exc is not None:
        resp.raise_for_status.side_effect = raise_exc
    else:
        resp.raise_for_status.return_value = None
    session.get.return_value = resp
    return session


def test_identify_one_success():
    session = _fake_session({"value": "247.433"})
    assert elevation._identify_one(session, 50.0755, 14.4378) == pytest.approx(247.433)


def test_identify_one_nodata():
    session = _fake_session({"value": "NoData"})
    assert elevation._identify_one(session, 48.2, 16.37) is None


def test_identify_one_error_key():
    session = _fake_session({"error": {"code": 400, "message": "bad geometry"}})
    assert elevation._identify_one(session, 0.0, 0.0) is None


def test_identify_one_http_failure():
    session = _fake_session(status_code=500, raise_exc=Exception("server error"))
    assert elevation._identify_one(session, 50.0, 14.0) is None


def test_identify_one_missing_value_key():
    session = _fake_session({"objectId": 0})
    assert elevation._identify_one(session, 50.0, 14.0) is None


# ---------------------------------------------------------------------------
# _sample_cuzk_dmr5g — order preserved under concurrency
# ---------------------------------------------------------------------------

def test_sample_cuzk_dmr5g_preserves_order(monkeypatch):
    coords = [(50.0 + i * 0.01, 14.0 + i * 0.01) for i in range(20)]

    def fake_identify_one(session, lat, lon, retry=True):
        # Deterministic value derived from the coordinate, independent of
        # completion order under the thread pool.
        return round((lat - 50.0) * 1000, 3)

    monkeypatch.setattr(elevation, "_identify_one", fake_identify_one)
    results = elevation._sample_cuzk_dmr5g(coords)
    expected = [round(i * 10, 3) for i in range(20)]
    assert results == expected


def test_sample_cuzk_dmr5g_progress_cb(monkeypatch):
    coords = [(50.0, 14.0), (50.1, 14.1), (50.2, 14.2)]
    monkeypatch.setattr(elevation, "_identify_one", lambda *a, **k: 1.0)

    calls = []
    elevation._sample_cuzk_dmr5g(coords, progress_cb=lambda done, tot: calls.append((done, tot)))
    assert len(calls) == 3
    assert calls[-1] == (3, 3)


def test_sample_cuzk_dmr5g_empty_input():
    assert elevation._sample_cuzk_dmr5g([]) == []


# ---------------------------------------------------------------------------
# sample_elevations — source dispatch + fallback
# ---------------------------------------------------------------------------

def test_sample_elevations_cuzk_backfills_missing(monkeypatch):
    coords = [(50.0, 14.0), (48.2, 16.37), (50.1, 14.1)]

    monkeypatch.setattr(elevation, "_sample_cuzk_dmr5g", lambda c, progress_cb=None: [10.0, None, 30.0])

    captured = {}
    def fake_opentopodata(c, progress_cb=None):
        captured["coords"] = c
        return [99.0]

    monkeypatch.setattr(elevation, "_sample_opentopodata", fake_opentopodata)

    result = elevation.sample_elevations(coords, source="cuzk_dmr5g")
    assert result == [10.0, 99.0, 30.0]
    assert captured["coords"] == [coords[1]]


def test_sample_elevations_cuzk_no_missing_skips_fallback(monkeypatch):
    coords = [(50.0, 14.0), (50.1, 14.1)]
    monkeypatch.setattr(elevation, "_sample_cuzk_dmr5g", lambda c, progress_cb=None: [10.0, 20.0])

    def fail_if_called(*a, **k):
        raise AssertionError("OpenTopoData fallback should not be called")

    monkeypatch.setattr(elevation, "_sample_opentopodata", fail_if_called)
    result = elevation.sample_elevations(coords, source="cuzk_dmr5g")
    assert result == [10.0, 20.0]


def test_sample_elevations_opentopodata_only_skips_cuzk(monkeypatch):
    coords = [(50.0, 14.0), (50.1, 14.1)]

    def fail_if_called(*a, **k):
        raise AssertionError("ČÚZK should not be called when source='opentopodata'")

    monkeypatch.setattr(elevation, "_sample_cuzk_dmr5g", fail_if_called)
    monkeypatch.setattr(elevation, "_sample_opentopodata", lambda c, progress_cb=None: [11.0, 22.0])

    result = elevation.sample_elevations(coords, source="opentopodata")
    assert result == [11.0, 22.0]
