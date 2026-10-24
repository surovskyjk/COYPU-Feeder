"""
COYPU stopping patterns and the stops CSV (src/geometry/stationing.py).

The Feeder ports COYPU's stops_model rules — Os everywhere, Sp at the
endpoints and in municipalities at or above the population threshold, Ex at
the endpoints only, hand-set Sp/Ex kept as overrides — and writes COYPU's
stops CSV. What matters is that COYPU reads the file back exactly as the
Feeder showed it: same flags, and no spurious manual overrides. The parity
tests at the bottom check that against COYPU's own stops_model when the
COYPU repository is checked out next to this one (COYPU_REPO, default
D:/MT/thesis-railway); they are skipped otherwise.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from geometry import stationing as stn  # noqa: E402
from geometry.stationing import Station  # noqa: E402


def _st(name, km, pop=None, **kw):
    return Station(name=name, latlon=(50.0, 15.0), chainage_m=km * 1000.0,
                   population=pop, **kw)


def _flags(stations):
    return [(s.name, s.stop_os, s.stop_sp, s.stop_ex) for s in stations]


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

class TestPatternRules:

    def test_defaults(self):
        line = [_st("A", 0.0, 500), _st("B", 3.0, 5000), _st("C", 6.0, 800),
                _st("D", 9.0, None), _st("E", 12.0, 300)]
        stn.apply_pattern_rules(line, threshold=2000)
        assert _flags(line) == [
            ("A", True, True, True),       # endpoint: Sp and Ex
            ("B", True, True, False),      # above the threshold
            ("C", True, False, False),
            ("D", True, False, False),     # unknown population never qualifies
            ("E", True, True, True),       # endpoint
        ]

    def test_threshold_is_inclusive(self):
        line = [_st("A", 0.0), _st("B", 1.0, 2000), _st("C", 2.0)]
        stn.apply_pattern_rules(line, threshold=2000)
        assert line[1].stop_sp

    def test_endpoints_come_from_the_chainage_not_the_list_order(self):
        line = [_st("mid", 5.0), _st("last", 9.0), _st("first", 1.0)]
        stn.apply_pattern_rules(line)
        assert [s.stop_ex for s in line] == [False, True, True]

    def test_a_single_stop_is_both_endpoints(self):
        one = [_st("only", 4.0)]
        stn.apply_pattern_rules(one)
        assert one[0].stop_sp and one[0].stop_ex

    def test_manual_overrides_survive_the_rules(self):
        line = [_st("A", 0.0), _st("B", 1.0, 9000), _st("C", 2.0)]
        line[1].stop_sp, line[1].sp_manual = False, True
        line[0].stop_ex, line[0].ex_manual = False, True
        stn.apply_pattern_rules(line, threshold=2000)
        assert line[1].stop_sp is False
        assert line[0].stop_ex is False

    def test_os_has_no_rule(self):
        line = [_st("A", 0.0), _st("B", 1.0)]
        line[1].stop_os = False
        stn.apply_pattern_rules(line)
        assert [s.stop_os for s in line] == [True, False]

    def test_manual_sp_mode_leaves_sp_alone_but_ex_follows_the_ends(self):
        line = [_st("A", 0.0), _st("B", 1.0, 9000), _st("C", 2.0)]
        stn.apply_pattern_rules(line, threshold=2000, sp_auto=False)
        assert [s.stop_sp for s in line] == [False, False, False]
        assert [s.stop_ex for s in line] == [True, False, True]

    def test_reset_clears_every_override(self):
        line = [_st("A", 0.0), _st("B", 1.0, 9000), _st("C", 2.0)]
        line[1].stop_sp, line[1].sp_manual = False, True
        line[2].stop_ex, line[2].ex_manual = False, True
        stn.reset_overrides(line, threshold=2000)
        assert not any(s.sp_manual or s.ex_manual for s in line)
        assert _flags(line) == [("A", True, True, True), ("B", True, True, False),
                                ("C", True, True, True)]

    def test_counts(self):
        line = [_st("A", 0.0), _st("B", 1.0, 9000), _st("C", 2.0)]
        stn.apply_pattern_rules(line)
        assert stn.pattern_counts(line) == {"Os": 3, "Sp": 3, "Ex": 2}


class TestParsePopulation:

    @pytest.mark.parametrize("text, expected", [
        ("12 500", 12500), ("12,500", 12500), ("12.500", 12500),
        ("12\u00a0500", 12500), ("1 397 880", 1397880), ("33444", 33444),
        (33444, 33444), (" 812 ", 812), ("", None), (None, None), ("n/a", None),
    ])
    def test_cases(self, text, expected):
        assert stn.parse_population(text) == expected


class TestDedupeKeepsThePlace:

    def test_stop_position_wins_the_spot_but_the_halt_names_the_kind(self):
        halt = _st("Zastávka", 1.0, kind="halt", osm_id=11)
        halt.dist_m = 40.0
        stop = _st("Zastávka", 1.02, kind="stop_position", osm_id=22)
        stop.dist_m = 0.5
        (kept,) = stn.dedupe_by_name([halt, stop])
        assert kept is stop
        assert (kept.kind, kept.osm_id) == ("halt", 11)
        assert kept.chainage_m == 1020.0


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

COYPU_HEADER = ["stopName", "stationingKm", "dwellTimeS", "municipalityName",
                "population", "stopInOs", "stopInSp", "stopInEx"]


def _write(tmp_path, stations):
    path = str(tmp_path / "stops.csv")
    n = stn.write_stations_csv(stations, path)
    raw = open(path, "rb").read()
    return n, raw, list(csv.reader(io.StringIO(raw.decode("utf-8"))))


class TestStopsCsv:

    def test_coypu_layout(self, tmp_path):
        line = [_st("Kolín", 61.2344, 33444, municipality="Kolín"),
                _st("Velim", 55.0, None)]
        stn.apply_pattern_rules(line)
        n, raw, rows = _write(tmp_path, line)
        assert n == 2
        assert rows[0] == COYPU_HEADER
        assert rows[1] == ["Velim", "55.000", "30", "", "", "1", "1", "1"]
        assert rows[2] == ["Kolín", "61.234", "30", "Kolín", "33444", "1", "1", "1"]
        assert b"\r\n" not in raw            # COYPU's own line terminator

    def test_header_matches_the_module_constant(self):
        assert list(stn.CSV_COLUMNS) == COYPU_HEADER


# ---------------------------------------------------------------------------
# Parity with COYPU's own stops_model (skipped without the COYPU checkout)
# ---------------------------------------------------------------------------

def _coypu_stops_model():
    path = os.path.join(os.environ.get("COYPU_REPO", "D:/MT/thesis-railway"),
                        "stops_model.py")
    if not os.path.isfile(path):
        pytest.skip("COYPU repository not available")
    spec = importlib.util.spec_from_file_location("coypu_stops_model", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _random_line(rng, n):
    kms = sorted(rng.sample(range(0, 200_000), n))
    return [_st(f"S{i}", km / 1000.0,
                rng.choice([None, 150, 1999, 2000, 2001, 12_000, 400_000]),
                municipality=f"Obec {i}")
            for i, km in enumerate(kms)]


class TestCoypuParity:

    @pytest.mark.parametrize("seed", range(5))
    def test_rules_match_coypu(self, seed):
        sm = _coypu_stops_model()
        rng = random.Random(seed)
        line = _random_line(rng, rng.randint(1, 25))
        threshold = rng.choice([0, 1000, 2000, 5000])
        stn.apply_pattern_rules(line, threshold)
        records = [sm.buildStopRecord(stopName=s.name, stationingKm=s.chainage_m / 1000.0,
                                      population=s.population) for s in line]
        sm.applyPatternRules(records, threshold, True)
        assert [(r["stopInOs"], r["stopInSp"], r["stopInEx"]) for r in records] == \
               [(s.stop_os, s.stop_sp, s.stop_ex) for s in line]

    @pytest.mark.parametrize("seed", range(5))
    def test_coypu_imports_the_csv_unchanged(self, tmp_path, seed):
        sm = _coypu_stops_model()
        rng = random.Random(100 + seed)
        line = _random_line(rng, rng.randint(2, 25))
        stn.apply_pattern_rules(line, sm.DEFAULT_SP_POPULATION_THRESHOLD)
        # One deliberate override each way, which COYPU must see as manual.
        line[0].stop_ex, line[0].ex_manual = False, True
        line[-1].stop_sp, line[-1].sp_manual = False, True
        stn.apply_pattern_rules(line, sm.DEFAULT_SP_POPULATION_THRESHOLD)
        _n, raw, _rows = _write(tmp_path, line)

        parsed = sm.parseStopsCsv(raw.decode("utf-8"))
        assert parsed["isLegacyFormat"] is False and parsed["hasPatternColumns"]
        recs = parsed["records"]
        assert [r["stopName"] for r in recs] == [s.name for s in line]
        assert [r["municipalityName"] for r in recs] == [s.municipality for s in line]
        assert [r["population"] for r in recs] == [s.population for s in line]
        assert [round(r["stationingKm"], 3) for r in recs] == \
               [round(s.chainage_m / 1000.0, 3) for s in line]
        assert [(r["stopInOs"], r["stopInSp"], r["stopInEx"]) for r in recs] == \
               [(s.stop_os, s.stop_sp, s.stop_ex) for s in line]
        # Only the two real overrides arrive as manual ones.
        assert [r["stopInExIsManual"] for r in recs] == \
               [True] + [False] * (len(line) - 1)
        assert [r["stopInSpIsManual"] for r in recs] == \
               [False] * (len(line) - 1) + [True]
