"""
`.coypuf` persistence of the vertical profile, and backward compatibility.

Two things are being protected here:

  * a saved profile comes back byte-for-byte equivalent, including the NaN
    positions of DEM misses (an unbridged gap must survive as a gap, not as a
    fabricated 0 m), and
  * every `.coypuf` written before format 2.0 still opens.

Legacy files are synthesised the way `test_reflex_merge.py` does it — save a
current file, then strip the block and rewrite the version attribute — so the
fixture can never drift away from the real writer.
"""

import io
import os
import re
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import project_io  # noqa: E402
from geometry.profile import (  # noqa: E402
    METHOD_WINDOW, DirectConnection, VerticalProfile, rebuild_profile,
)

# The version attribute this build writes (the legacy fixtures rewrite it).
CURRENT = f'version="{project_io.FILE_VERSION}"'
VP_BLOCK = re.compile(r"\s*<VerticalProfile.*?</VerticalProfile>", re.S)


def _profile():
    s = np.arange(0.0, 4000.0, 20.0)
    z = 250.0 + 0.008 * s + 4.0 * np.sin(s / 300.0)
    z[10:14] = np.nan                      # a DEM gap that must round-trip
    p = VerticalProfile(
        s=s, z_raw=z,
        sample_interval=20.0,
        elevation_source="opentopodata",
        method=METHOD_WINDOW,
        max_grade_pm=12.5,
        z_tol=0.25,
        window_m=170.0,
        poly_order=3,
        vc_enabled=False,
        vc_length=140.0,
        connections=[DirectConnection(500.0, 900.0, "tunel Jablunkov"),
                     DirectConnection(2000.0, 2400.0, "")],
        geom_fingerprint=(42, 3999.5),
    )
    return rebuild_profile(p)


def _save(tmp_path, profile, **extra):
    path = str(tmp_path / "project.coypuf")
    state = {"work_epsg": 5514, "step": 7, "profile": profile}
    state.update(extra)
    project_io.save_project(path, state)
    return path


def _rewrite(tmp_path, text, name="legacy.coypuf"):
    path = str(tmp_path / name)
    io.open(path, "w", encoding="utf-8", newline="\n").write(text)
    return path


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

class TestRoundTrip:

    def test_samples_survive(self, tmp_path):
        p = _profile()
        q = project_io.load_project(_save(tmp_path, p))["profile"]
        np.testing.assert_allclose(q.s, p.s, atol=1e-6)
        finite = np.isfinite(p.z_raw)
        np.testing.assert_allclose(q.z_raw[finite], p.z_raw[finite], atol=1e-6)

    def test_nodata_gaps_survive_as_gaps(self, tmp_path):
        p = _profile()
        q = project_io.load_project(_save(tmp_path, p))["profile"]
        np.testing.assert_array_equal(np.isnan(q.z_raw), np.isnan(p.z_raw))

    def test_nan_is_written_literally(self, tmp_path):
        raw = io.open(_save(tmp_path, _profile()), encoding="utf-8").read()
        assert "nan" in raw

    @pytest.mark.parametrize("field", [
        "sample_interval", "elevation_source", "method", "max_grade_pm",
        "z_tol", "window_m", "poly_order", "vc_enabled", "vc_length",
    ])
    def test_configuration_survives(self, tmp_path, field):
        p = _profile()
        q = project_io.load_project(_save(tmp_path, p))["profile"]
        assert getattr(q, field) == getattr(p, field)

    def test_geom_fingerprint_survives(self, tmp_path):
        p = _profile()
        q = project_io.load_project(_save(tmp_path, p))["profile"]
        assert tuple(q.geom_fingerprint) == (42, 3999.5)

    def test_connections_survive(self, tmp_path):
        p = _profile()
        q = project_io.load_project(_save(tmp_path, p))["profile"]
        assert len(q.connections) == 2
        assert q.connections[0].label == "tunel Jablunkov"
        assert q.connections[0].s_start == pytest.approx(500.0)
        assert q.connections[1].label == ""

    def test_derived_fields_are_regenerated_not_stored(self, tmp_path):
        """Same contract as the element chain: rebuilt on load, never written."""
        p = _profile()
        raw = io.open(_save(tmp_path, p), encoding="utf-8").read()
        assert "z_design" not in raw and "<PVI" not in raw
        q = project_io.load_project(_save(tmp_path, p))["profile"]
        np.testing.assert_allclose(q.z_design, p.z_design, atol=1e-6)
        assert len(q.pvis) == len(p.pvis)
        assert q.stats["n_pvi"] == p.stats["n_pvi"]

    def test_second_round_trip_is_stable(self, tmp_path):
        p = _profile()
        first = io.open(_save(tmp_path, p), encoding="utf-8").read()
        q = project_io.load_project(_save(tmp_path, p))["profile"]
        second_path = str(tmp_path / "again.coypuf")
        project_io.save_project(second_path,
                                {"work_epsg": 5514, "step": 7, "profile": q})
        second = io.open(second_path, encoding="utf-8").read()
        strip = lambda t: re.sub(r'created="[^"]*"', "", t)
        assert strip(first) == strip(second)

    def test_no_profile_means_no_block(self, tmp_path):
        path = str(tmp_path / "empty.coypuf")
        project_io.save_project(path, {"work_epsg": 5514, "profile": None})
        raw = io.open(path, encoding="utf-8").read()
        assert "VerticalProfile" not in raw
        assert project_io.load_project(path)["profile"] is None

    def test_unsampled_profile_is_not_written(self, tmp_path):
        """A default profile the user never sampled carries no data worth
        storing — writing an empty block would only bloat the file."""
        path = str(tmp_path / "unsampled.coypuf")
        project_io.save_project(
            path, {"work_epsg": 5514, "profile": VerticalProfile()})
        assert "VerticalProfile" not in io.open(path, encoding="utf-8").read()
        assert project_io.load_project(path)["profile"] is None


# ---------------------------------------------------------------------------
# Legacy ingestion
# ---------------------------------------------------------------------------

class TestLegacy:

    @pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
    def test_pre_2_0_file_loads_without_a_profile(self, tmp_path, version):
        raw = io.open(_save(tmp_path, _profile()), encoding="utf-8").read()
        doc = VP_BLOCK.sub("", raw).replace(CURRENT,
                                            f'version="{version}"')
        st = project_io.load_project(_rewrite(tmp_path, doc))
        assert st["profile"] is None
        assert st["version_warning"] is None

    def test_missing_version_attribute_defaults_to_1_0(self, tmp_path):
        raw = io.open(_save(tmp_path, _profile()), encoding="utf-8").read()
        doc = VP_BLOCK.sub("", raw).replace(' ' + CURRENT, "")
        assert project_io.load_project(_rewrite(tmp_path, doc))["profile"] is None

    def test_block_present_but_empty(self, tmp_path):
        """Defensive: a hand-edited or truncated block must not raise."""
        raw = io.open(_save(tmp_path, _profile()), encoding="utf-8").read()
        doc = VP_BLOCK.sub("\n  <VerticalProfile/>", raw)
        p = project_io.load_project(_rewrite(tmp_path, doc))["profile"]
        assert p is not None and len(p.s) == 0 and p.pvis == []

    def test_block_without_connections(self, tmp_path):
        raw = io.open(_save(tmp_path, _profile()), encoding="utf-8").read()
        doc = re.sub(r"\s*<Connections>.*?</Connections>", "", raw, flags=re.S)
        p = project_io.load_project(_rewrite(tmp_path, doc))["profile"]
        assert p is not None and p.connections == []

    def test_unknown_attributes_are_ignored(self, tmp_path):
        raw = io.open(_save(tmp_path, _profile()), encoding="utf-8").read()
        doc = raw.replace("<VerticalProfile ",
                          '<VerticalProfile futureKnob="7" ')
        assert project_io.load_project(_rewrite(tmp_path, doc))["profile"] is not None

    def test_a_newer_file_warns_but_still_opens(self, tmp_path):
        raw = io.open(_save(tmp_path, _profile()), encoding="utf-8").read()
        doc = raw.replace(CURRENT, 'version="3.5"')
        st = project_io.load_project(_rewrite(tmp_path, doc))
        assert st["profile"] is not None
        assert "newer build" in (st["version_warning"] or "")

    def test_upgrading_a_legacy_project_preserves_everything_else(self, tmp_path):
        """Re-saving a pre-2.0 project must keep its horizontal data intact and
        only add the new block."""
        from osm.parser import Track
        track = Track()
        track.name = "Trať 323"
        track.nodes = [(49.8, 18.2), (49.81, 18.21), (49.82, 18.22)]

        legacy_path = str(tmp_path / "old.coypuf")
        project_io.save_project(legacy_path, {
            "work_epsg": 5514, "step": 7, "profile": None,
            "project_name": "Legacy", "tracks": [track],
            "selected_indices": [0], "settings": {"vc_length": 120.0},
        })
        doc = io.open(legacy_path, encoding="utf-8").read().replace(
            CURRENT, 'version="1.3"')
        st = project_io.load_project(_rewrite(tmp_path, doc))
        assert st["profile"] is None
        assert st["step"] == 8                       # old Export → new Export
        assert st["tracks"][0].name == "Trať 323"
        assert st["settings"]["vc_length"] == 120.0

        upgraded = str(tmp_path / "upgraded.coypuf")
        st["profile"] = _profile()
        project_io.save_project(upgraded, {
            "work_epsg": st["work_epsg"], "step": st["step"],
            "profile": st["profile"], "project_name": st["project_name"],
            "tracks": st["tracks"], "selected_indices": st["selected_indices"],
            "settings": st["settings"],
        })
        back = project_io.load_project(upgraded)
        assert back["tracks"][0].name == "Trať 323"
        assert back["tracks"][0].nodes == track.nodes
        assert back["settings"]["vc_length"] == 120.0
        assert back["profile"] is not None
        assert len(back["profile"].connections) == 2
        raw = io.open(upgraded, encoding="utf-8").read()
        assert CURRENT in raw
