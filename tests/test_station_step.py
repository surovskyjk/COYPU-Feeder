"""
Step 6 (Stations) offscreen, and `.coypuf` persistence of the station data.

  * loading or re-preparing stations applies the COYPU rules without
    counting as an edit (the project must not turn dirty on its own);
  * ticking Sp/Ex in the table is a manual override that survives a
    threshold change and is marked in the table;
  * a population typed by hand drives the Sp rule and protects the row
    from the lookup, which also skips stations deleted while it ran;
  * municipality, population, pattern flags and the rule settings
    round-trip through `.coypuf`, and a pre-2.1 file still opens.
"""

from __future__ import annotations

import io
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QTableWidget  # noqa: E402

import project_io  # noqa: E402
from geometry.stationing import Station  # noqa: E402
from gui.steps.step6_stations import (  # noqa: E402
    COL_EX, COL_MUNI, COL_POP, COL_SP, COLS, Step6Stations,
)

_app = QApplication.instance() or QApplication([])
_KEEP: list = []


def teardown_module(_module):
    _KEEP.clear()


def _step() -> Step6Stations:
    w = Step6Stations()
    _KEEP.append(w)
    return w


def _st(name, km, pop=None, **kw):
    return Station(name=name, latlon=(50.0 + km / 100.0, 15.0), chainage_m=km * 1000.0,
                   population=pop, **kw)


def _line():
    return [_st("A", 0.0, 500), _st("B", 3.0, 5000), _st("C", 6.0, 800), _st("D", 9.0)]


def _flags(w):
    return [(s.name, s.stop_os, s.stop_sp, s.stop_ex) for s in w.stations()]


def _edits(w):
    got = []
    w.stations_edited.connect(lambda: got.append(1))
    return got


class TestRulesInTheStep:

    def test_loading_applies_the_rules_but_is_not_an_edit(self):
        w = _step()
        edits = _edits(w)
        w.set_stations(_line())
        assert _flags(w) == [("A", True, True, True), ("B", True, True, False),
                             ("C", True, False, False), ("D", True, True, True)]
        w.prepare([], [], 5514)
        assert edits == []
        assert w._counts_lbl.text() == "Os 4 · Sp 3 · Ex 2"

    def test_table_columns_and_cells(self):
        w = _step()
        w.set_stations(_line())
        t = w._table
        assert [t.horizontalHeaderItem(i).text() for i in range(t.columnCount())] == COLS
        assert t.rowCount() == 4
        assert t.item(1, COL_POP).text() == "5 000"
        assert t.item(3, COL_POP).text() == ""
        assert t.item(1, COL_SP).checkState() == Qt.CheckState.Checked
        assert t.item(2, COL_SP).checkState() == Qt.CheckState.Unchecked

    def test_ticking_sp_is_an_override_that_survives_the_threshold(self):
        w = _step()
        w.set_stations(_line())
        edits = _edits(w)
        w._table.item(2, COL_SP).setCheckState(Qt.CheckState.Checked)
        c = w.stations()[2]
        assert c.stop_sp and c.sp_manual
        assert edits == [1]
        cell = w._table.item(2, COL_SP)
        assert cell.font().italic() and cell.text() == "✎"

        w._threshold_spin.setValue(10_000)          # B (5 000) drops out of Sp
        assert [s.stop_sp for s in w.stations()] == [True, False, True, True]

    def test_unticking_ex_at_an_endpoint(self):
        w = _step()
        w.set_stations(_line())
        w._table.item(0, COL_EX).setCheckState(Qt.CheckState.Unchecked)
        a = w.stations()[0]
        assert not a.stop_ex and a.ex_manual

    def test_reset_overrides(self):
        w = _step()
        w.set_stations(_line())
        w._table.item(2, COL_SP).setCheckState(Qt.CheckState.Checked)
        w._table.item(0, COL_EX).setCheckState(Qt.CheckState.Unchecked)
        w._reset_btn.click()
        assert not any(s.sp_manual or s.ex_manual for s in w.stations())
        assert _flags(w)[0] == ("A", True, True, True)
        assert _flags(w)[2] == ("C", True, False, False)

    def test_manual_sp_mode(self):
        w = _step()
        w.set_stations(_line())
        w._auto_check.setChecked(False)
        assert not w._threshold_spin.isEnabled() and not w._reset_btn.isEnabled()
        w._threshold_spin.setValue(100)             # no effect without the rule
        assert [s.stop_sp for s in w.stations()] == [True, True, False, True]
        assert w.rule_settings() == {"sp_threshold": 100, "sp_auto": False}

    def test_a_typed_population_drives_sp_and_protects_the_row(self):
        w = _step()
        w.set_stations(_line())
        w._table.item(2, COL_POP).setText("12 500")
        c = w.stations()[2]
        assert (c.population, c.population_source, c.info_manual) == (12500, "manual", True)
        assert c.stop_sp

    def test_clearing_both_values_hands_the_row_back_to_the_lookup(self):
        w = _step()
        w.set_stations([_st("A", 0.0), _st("B", 1.0, 900, municipality="X",
                                            info_manual=True)])
        w._table.item(1, COL_MUNI).setText("")
        assert w.stations()[1].info_manual          # population still typed
        w._table.item(1, COL_POP).setText("")
        b = w.stations()[1]
        assert (b.municipality, b.population, b.info_manual) == ("", None, False)

    def test_an_unreadable_population_is_ignored(self):
        w = _step()
        w.set_stations(_line())
        w._table.item(1, COL_POP).setText("lots")
        assert w.stations()[1].population == 5000


class TestLookupResults:

    def test_hand_edited_and_deleted_rows_are_left_alone(self):
        w = _step()
        w.set_stations(_line())
        w.stations()[1].info_manual = True
        w._info_targets = w.stations()
        w._on_delete(3)                              # deleted while it ran
        edits = _edits(w)
        info = {"municipality": "Obec", "population": 2500,
                "population_year": 2025, "population_source": "wikidata"}
        w._on_info_result([info, dict(info), None, dict(info)], "")
        a, b, c = w.stations()
        assert (a.municipality, a.population, a.population_year,
                a.population_source) == ("Obec", 2500, 2025, "wikidata")
        assert (b.municipality, b.population) == ("", 5000)
        assert (c.municipality, c.population) == ("", 800)
        assert w._status_lbl.text() == (
            "Municipality lookup: 1 updated · 1 kept (edited by hand) · 1 not found")
        assert edits == [1]
        assert w._info_btn.isEnabled() and w._info_targets == []

    def test_a_partial_wikidata_answer_is_flagged(self):
        w = _step()
        w.set_stations(_line()[:1])
        w._info_targets = w.stations()
        w._on_info_result([None], "Wikidata did not answer for 1 municipality")
        assert w._status_lbl.text().startswith("⚠ Wikidata did not answer")

    def test_a_failed_lookup_keeps_the_data(self):
        w = _step()
        w.set_stations(_line())
        w._info_targets = w.stations()
        w._on_info_failed("All Overpass servers are busy.\nhost: detail")
        assert w._status_lbl.text() == (
            "⚠ Municipality lookup failed: All Overpass servers are busy.")
        assert [s.population for s in w.stations()] == [500, 5000, 800, None]


class TestTablePanel:

    def test_the_table_lives_outside_the_step_column(self):
        w = _step()
        panel = w.table_panel()
        assert w._table in panel.findChildren(QTableWidget)
        assert not w.isAncestorOf(panel)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _full_station():
    return Station(
        name="Kolín", latlon=(50.026312, 15.201544), chainage_m=61234.5, dwell_s=45,
        source="auto", osm_id=123456789, kind="station", municipality="Kolín",
        population=33444, population_year=2025, population_source="wikidata",
        info_manual=False, stop_os=True, stop_sp=False, stop_ex=True,
        sp_manual=True, ex_manual=False)


FIELDS = ("name", "chainage_m", "dwell_s", "source", "osm_id", "kind",
          "municipality", "population", "population_year", "population_source",
          "info_manual", "stop_os", "stop_sp", "stop_ex", "sp_manual", "ex_manual")


class TestPersistence:

    def _save(self, tmp_path, stations, rules=None):
        path = str(tmp_path / "p.coypuf")
        state = {"work_epsg": 5514, "stations": stations}
        if rules is not None:
            state["station_rules"] = rules
        project_io.save_project(path, state)
        return path

    def test_round_trip(self, tmp_path):
        st = _full_station()
        path = self._save(tmp_path, [st, _st("Velim", 55.0)],
                          {"sp_threshold": 5000, "sp_auto": False})
        back = project_io.load_project(path)
        a = back["stations"][0]
        assert {f: getattr(a, f) for f in FIELDS} == {f: getattr(st, f) for f in FIELDS}
        assert a.latlon == pytest.approx(st.latlon, abs=1e-6)
        b = back["stations"][1]
        assert (b.osm_id, b.kind, b.population, b.population_year) == (None, "", None, None)
        assert back["station_rules"] == {"sp_threshold": 5000, "sp_auto": False}

    def test_a_zero_threshold_survives(self, tmp_path):
        path = self._save(tmp_path, [], {"sp_threshold": 0, "sp_auto": True})
        assert project_io.load_project(path)["station_rules"]["sp_threshold"] == 0

    def test_pre_2_1_file_loads_with_defaults(self, tmp_path):
        raw = io.open(self._save(tmp_path, [_full_station()],
                                 {"sp_threshold": 5000, "sp_auto": False}),
                      encoding="utf-8").read()
        new_attrs = ("osmId", "kind", "municipality", "population", "populationYear",
                     "populationSource", "infoManual", "os", "sp", "ex", "spManual",
                     "exManual", "spThreshold", "spAuto")
        doc = re.sub(r' (%s)="[^"]*"' % "|".join(new_attrs), "", raw)
        doc = doc.replace(f'version="{project_io.FILE_VERSION}"', 'version="2.0"')
        legacy = tmp_path / "legacy.coypuf"
        io.open(legacy, "w", encoding="utf-8", newline="\n").write(doc)

        back = project_io.load_project(str(legacy))
        a = back["stations"][0]
        assert (a.name, a.dwell_s, a.source) == ("Kolín", 45, "auto")
        assert (a.municipality, a.population, a.osm_id) == ("", None, None)
        assert (a.stop_os, a.stop_sp, a.stop_ex, a.sp_manual, a.ex_manual) == \
               (True, False, False, False, False)
        assert back["station_rules"] == {"sp_threshold": 2000, "sp_auto": True}
        assert back["version_warning"] is None

    def test_the_step_restores_the_rules_from_a_project(self, tmp_path):
        path = self._save(tmp_path, [_st("A", 0.0), _st("B", 1.0, 3000), _st("C", 2.0)],
                          {"sp_threshold": 5000, "sp_auto": True})
        back = project_io.load_project(path)
        w = _step()
        w.set_stations(back["stations"], rules=back["station_rules"])
        assert w._threshold_spin.value() == 5000
        assert [s.stop_sp for s in w.stations()] == [True, False, True]
