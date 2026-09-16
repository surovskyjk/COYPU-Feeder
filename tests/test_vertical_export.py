"""
The vertical profile reaching LandXML 1.2.

`landxml.builder` already knew how to write `<Profile>` / `<ProfAlign>` /
`<PVI>` / `<ParaCurve>`; what is new is that the elements now come from the
Vertical Profile step instead of a DEM query hidden inside the export worker.
These tests pin the contract between the two — the element dicts
`geometry.profile.build_vertical_elements` produces must be exactly what
`_add_vertical_elements` consumes — and the stationing property that motivated
the change: the profile and the horizontal geometry share one station axis.
"""

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from geometry.profile import (  # noqa: E402
    VerticalProfile, build_vertical_elements, rebuild_profile,
)
from landxml.builder import build_landxml  # noqa: E402

NS = {"l": "http://www.landxml.org/schema/LandXML-1.2"}


def _elements(total=3000.0):
    """A single straight line — enough for a valid <CoordGeom>."""
    return [{
        "type": "Line", "sta_start": 0.0, "length": total,
        "start": [0.0, 0.0], "end": [total, 0.0],
        "direction_rad": 0.0,
    }]


def _build(vertical, elements=None, **kw):
    return build_landxml(
        [{"name": "T1", "elements": elements or _elements(),
          "vertical": vertical, "sta_start": 0.0}],
        output_epsg=5514, **kw)


def _find(root, tag):
    return root.findall(f".//l:{tag}", NS) or root.findall(f".//{tag}")


# ---------------------------------------------------------------------------

class TestProfileBlock:

    def test_profile_is_emitted_when_vertical_is_present(self):
        root = _build(build_vertical_elements(
            np.array([0.0, 1000.0, 2000.0]),
            np.array([100.0, 110.0, 105.0])))
        assert len(_find(root, "Profile")) == 1
        assert len(_find(root, "ProfAlign")) == 1

    def test_no_profile_when_vertical_is_empty(self):
        """The old behaviour for a skipped step / a Level-1 export."""
        root = _build([])
        assert _find(root, "Profile") == []

    def test_pvi_count_and_text_format(self):
        s = np.array([0.0, 1000.0, 2000.0, 3000.0])
        z = np.array([100.0, 110.0, 105.0, 120.0])
        root = _build(build_vertical_elements(s, z, False))
        pvis = _find(root, "PVI")
        assert len(pvis) == 4
        for el, sta, elev in zip(pvis, s, z):
            assert el.text == f"{sta:.6f} {elev:.6f}"

    def test_station_then_elevation_not_reversed(self):
        """`_add_point` writes northing-first for horizontal coordinates; the
        vertical pair is NOT reordered."""
        root = _build(build_vertical_elements(
            np.array([0.0, 1234.0]), np.array([987.0, 654.0]), False))
        first = _find(root, "PVI")[0]
        assert first.text.split()[0].startswith("0.")
        assert first.text.split()[1].startswith("987")

    def test_paracurve_at_every_interior_pvi(self):
        s = np.array([0.0, 1000.0, 2000.0, 3000.0, 4000.0])
        z = np.array([100.0, 110.0, 105.0, 120.0, 115.0])
        root = _build(build_vertical_elements(s, z, True, 100.0))
        assert len(_find(root, "PVI")) == 5
        assert len(_find(root, "ParaCurve")) == 3

    def test_vertical_curves_disabled_yields_none(self):
        s = np.array([0.0, 1000.0, 2000.0, 3000.0])
        z = np.array([100.0, 110.0, 105.0, 120.0])
        root = _build(build_vertical_elements(s, z, False, 100.0))
        assert _find(root, "ParaCurve") == []
        assert len(_find(root, "PVI")) == 4

    def test_paracurve_carries_a_positive_length(self):
        root = _build(build_vertical_elements(
            np.array([0.0, 1000.0, 2000.0]),
            np.array([100.0, 110.0, 105.0]), True, 120.0))
        pc = _find(root, "ParaCurve")[0]
        assert float(pc.get("length")) == pytest.approx(120.0)

    def test_stations_are_monotonic_in_document_order(self):
        s = np.linspace(0.0, 5000.0, 9)
        z = 100.0 + 5.0 * np.sin(np.arange(9))
        root = _build(build_vertical_elements(s, z, True, 200.0))
        prof = _find(root, "ProfAlign")[0]
        stations = [float(child.text.split()[0]) for child in prof]
        assert stations == sorted(stations)


class TestStationAxisAgreement:
    """The bug this feature fixes: the vertical profile used to be fitted
    against the raw OSM polyline's chainage while <CoordGeom> used the fitted
    chain, so the two ended at different stations."""

    # Deliberately including lengths that are NOT a multiple of the sample
    # interval — a real alignment's length essentially never is, and the raw
    # sampler walks a fixed grid that stops short of the end.
    @pytest.mark.parametrize("total", [3000.0, 2990.0, 1995.5, 137.0, 19.0])
    def test_profile_spans_the_whole_alignment(self, total):
        from geometry.profile import sample_points_along_elements

        elements = _elements(total)
        s, latlon = sample_points_along_elements(elements, 5514, 20.0)
        assert len(s) == len(latlon)
        assert s[-1] == pytest.approx(total, abs=1e-6)

        p = VerticalProfile(s=s, z_raw=200.0 + 0.004 * s)
        rebuild_profile(p)

        root = _build(p.pvis, elements=elements)
        alignment = _find(root, "Alignment")[0]
        last_station = float(_find(root, "PVI")[-1].text.split()[0])
        assert last_station == pytest.approx(float(alignment.get("length")),
                                             abs=1e-6)

    def test_multi_element_chain_reaches_the_end(self):
        from geometry.profile import sample_points_along_elements

        elements = [
            {"type": "Line", "sta_start": 0.0, "length": 503.0,
             "start": [0.0, 0.0], "end": [503.0, 0.0], "direction_rad": 0.0},
            {"type": "Line", "sta_start": 503.0, "length": 297.7,
             "start": [503.0, 0.0], "end": [800.7, 0.0], "direction_rad": 0.0},
        ]
        s, _ = sample_points_along_elements(elements, 5514, 20.0)
        assert s[-1] == pytest.approx(800.7, abs=1e-6)
        assert np.all(np.diff(s) > 0)          # np.interp needs strict monotonicity

    def test_first_pvi_is_at_the_alignment_start(self):
        s = np.arange(0.0, 2001.0, 20.0)
        p = VerticalProfile(s=s, z_raw=200.0 + 0.004 * s)
        rebuild_profile(p)
        root = _build(p.pvis, elements=_elements(2000.0))
        assert float(_find(root, "PVI")[0].text.split()[0]) == pytest.approx(0.0)


class TestEndToEnd:

    def test_a_smoothed_profile_serialises(self):
        rng = np.random.default_rng(7)
        s = np.arange(0.0, 8000.0, 20.0)
        z = 300.0 + 20.0 * np.sin(s / 1500.0) + rng.normal(0.0, 0.3, len(s))
        p = VerticalProfile(s=s, z_raw=z, max_grade_pm=15.0, z_tol=0.4)
        rebuild_profile(p)

        root = _build(p.pvis, elements=_elements(7980.0))
        pvis = _find(root, "PVI")
        assert len(pvis) == p.stats["n_pvi"] >= 2

        stations = [float(e.text.split()[0]) for e in pvis]
        elevations = [float(e.text.split()[1]) for e in pvis]
        grades = [abs((elevations[i + 1] - elevations[i])
                      / (stations[i + 1] - stations[i]))
                  for i in range(len(stations) - 1)]
        # What the step's read-out promised must survive into the file.
        assert max(grades) * 1000.0 <= 15.0 + 1e-6
        assert all(math.isfinite(v) for v in elevations)

    def test_nodata_never_reaches_the_file(self):
        s = np.arange(0.0, 2000.0, 20.0)
        z = 200.0 + 0.005 * s
        z[20:30] = np.nan
        p = VerticalProfile(s=s, z_raw=z)
        rebuild_profile(p)
        root = _build(p.pvis, elements=_elements(1980.0))
        for el in _find(root, "PVI") + _find(root, "ParaCurve"):
            assert "nan" not in el.text.lower()
