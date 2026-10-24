"""
Municipality and population lookup for the stations step, fully offline.

  * pick_municipality chooses the obec among the administrative areas
    Overpass' is_in returns (tag sets captured from the live service:
    Kolín, and Prague, where the obec is admin_level 6 and level 9 is a
    city district);
  * fetch_municipalities batches its is_in lookups and splits the
    marker-separated answer back per point;
  * pick_population / fetch_populations read Wikidata P1082 and never raise
    for network trouble;
  * StationInfoWorker prefers Wikidata, falls back to the OSM tag and
    reports an unreachable Overpass with the friendly summary.
"""

from __future__ import annotations

import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import osm.query as q  # noqa: E402
import osm.wikidata as wd  # noqa: E402

# Live is_in answers (2026-10), trimmed to the tags the picker reads.
KOLIN = [
    {"name": "Kolín", "admin_level": "8", "boundary": "administrative",
     "population": "30826", "ref": "CZ0204533165", "wikidata": "Q470369"},
    {"name": "SO POÚ Kolín", "admin_level": "7", "boundary": "administrative"},
]
PRAHA = [
    {"name": "Česko", "admin_level": "2", "population": "10516707", "wikidata": "Q213"},
    {"name": "Praha", "admin_level": "4", "population": "1397880",
     "ref": "PR", "wikidata": "Q1085"},
    {"name": "Praha", "admin_level": "6", "population": "1251933",
     "ref": "CZ0100554782", "wikidata": "Q1085"},
    {"name": "obvod Praha 2", "admin_level": "5", "wikidata": "Q2444636"},
    {"name": "Praha 2", "admin_level": "9", "population": "45405",
     "wikidata": "Q2444636"},
    {"name": "SO Praha 2", "admin_level": "6", "wikidata": "Q2444636"},
]


class TestPickMunicipality:

    def test_kolin(self):
        m = q.pick_municipality(KOLIN)
        assert m == {"name": "Kolín", "wikidata": "Q470369", "population": 30826,
                     "population_year": None, "ref": "CZ0204533165"}

    def test_prague_is_the_lau_coded_obec_not_the_city_district(self):
        m = q.pick_municipality(PRAHA)
        assert (m["name"], m["ref"], m["wikidata"]) == ("Praha", "CZ0100554782", "Q1085")

    @pytest.mark.parametrize("levels, expected", [
        (("9", "8", "7"), "L8"),     # 8 first (CZ, DE, AT)
        (("9", "7"), "L9"),          # then 9 (Slovak obec)
        (("7",), "L7"),              # then 7 (Polish gmina)
        (("6", "4"), None),
    ])
    def test_level_fallback_outside_czechia(self, levels, expected):
        areas = [{"name": f"L{lv}", "admin_level": lv} for lv in levels]
        m = q.pick_municipality(areas)
        assert (m["name"] if m else None) == expected

    def test_unnamed_areas_are_ignored(self):
        assert q.pick_municipality([{"admin_level": "8"}]) is None

    def test_population_tag_and_date(self):
        m = q.pick_municipality([{"name": "X", "admin_level": "8",
                                  "population": "12 345",
                                  "population:date": "2021-03-26"}])
        assert (m["population"], m["population_year"]) == (12345, 2021)

    def test_odd_population_tag_is_unknown(self):
        m = q.pick_municipality([{"name": "X", "admin_level": "8",
                                  "population": "about 300"}])
        assert m["population"] is None


class TestFetchMunicipalities:

    def test_query_shape(self):
        text = q._municipality_query([(50.0263, 15.2015), (50.0831, 14.4353)])
        assert text.startswith(f"[out:json][timeout:{q.T_MUNICIPALITY}]")
        assert 'make stn idx="0"; out;' in text and 'make stn idx="1"; out;' in text
        assert "is_in(50.0263000,15.2015000)" in text
        assert '["admin_level"~"^[6-9]$"]' in text

    def test_markers_split_the_answer_per_point(self):
        elements = (
            [{"type": "stn", "id": 1, "tags": {"idx": "0"}}]
            + [{"type": "area", "id": 3600000001 + i, "tags": t} for i, t in enumerate(KOLIN)]
            + [{"type": "stn", "id": 2, "tags": {"idx": "1"}}]
            + [{"type": "stn", "id": 3, "tags": {"idx": "2"}}]
            + [{"type": "area", "id": 3600000101 + i, "tags": t} for i, t in enumerate(PRAHA)]
        )
        groups = q._areas_by_marker(elements, 3)
        assert [len(g) for g in groups] == [2, 0, 6]

    def test_batches_and_keeps_the_point_order(self, monkeypatch):
        calls = []

        def fake_run(query, progress_cb=None, ttl=None, *, timeout=None, **kw):
            calls.append((query, ttl, timeout))
            n = query.count("make stn")
            els = []
            for i in range(n):
                els.append({"type": "stn", "id": i + 1, "tags": {"idx": str(i)}})
                if i % 2 == 0:
                    els.append({"type": "area", "id": 1,
                                "tags": {"name": f"M{len(calls)}-{i}", "admin_level": "8"}})
            return {"elements": els}

        monkeypatch.setattr(q, "_run_query", fake_run)
        progress = []
        pts = [(50.0 + i * 1e-3, 15.0) for i in range(q.MUNICIPALITY_BATCH + 5)]
        got = q.fetch_municipalities(pts, progress_cb=progress.append)

        assert len(calls) == 2
        assert [c[0].count("make stn") for c in calls] == [q.MUNICIPALITY_BATCH, 5]
        assert all(c[2] == q.T_MUNICIPALITY for c in calls)      # declared == client
        assert all(c[1] == q._CACHE_TTL_RELATION for c in calls)
        assert len(got) == len(pts)
        assert got[0]["name"] == "M1-0" and got[1] is None
        assert got[q.MUNICIPALITY_BATCH]["name"] == "M2-0"
        assert progress[0] == "Looking up municipalities (1/2)…"


# ---------------------------------------------------------------------------
# Wikidata
# ---------------------------------------------------------------------------

def _claim(amount, time=None, rank="normal", snaktype="value"):
    c = {"rank": rank, "mainsnak": {"snaktype": snaktype}}
    if snaktype == "value":
        c["mainsnak"]["datavalue"] = {"value": {"amount": f"+{amount}", "unit": "1"}}
    if time:
        c["qualifiers"] = {"P585": [{"snaktype": "value",
                                     "datavalue": {"value": {"time": time}}}]}
    return c


class TestPickPopulation:

    def test_preferred_rank_wins(self):
        claims = [_claim(33229, "+2024-01-01T00:00:00Z"),
                  _claim(33444, "+2025-01-01T00:00:00Z", rank="preferred"),
                  _claim(30922, "+2011-00-00T00:00:00Z")]
        assert wd.pick_population(claims) == (33444, 2025)

    def test_latest_point_in_time_among_normal_claims(self):
        claims = [_claim(31026, "+2014-01-01T00:00:00Z"),
                  _claim(32046, "+2022-01-01T00:00:00Z"),
                  _claim(9473, "+1869-01-01T00:00:00Z"),
                  _claim(30000)]                             # no date: oldest
        assert wd.pick_population(claims) == (32046, 2022)

    def test_deprecated_and_valueless_claims_are_ignored(self):
        claims = [_claim(99999, "+2030-01-01T00:00:00Z", rank="deprecated"),
                  _claim(0, "+2029-01-01T00:00:00Z", snaktype="somevalue"),
                  _claim(812, "+2025-01-01T00:00:00Z")]
        assert wd.pick_population(claims) == (812, 2025)

    def test_nothing_usable(self):
        assert wd.pick_population([]) is None
        assert wd.pick_population(None) is None


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class _WikiSession:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def get(self, url, timeout=None, params=None):
        self.calls.append((url, timeout, dict(params or {})))
        ans = self.answers.get(params["entity"])
        if isinstance(ans, BaseException):
            raise ans
        return ans


class TestFetchPopulations:

    def test_reads_each_item_once(self, monkeypatch):
        sess = _WikiSession({
            "Q470369": _Resp({"claims": {"P1082": [_claim(33444, "+2025-01-01T00:00:00Z")]}}),
            "Q1085": _Resp({"claims": {"P1082": [_claim(1397880, "+2025-01-01T00:00:00Z")]}}),
            "Q5": _Resp({"claims": {}}),
        })
        monkeypatch.setattr(wd, "_http", lambda: sess)
        found, failed = wd.fetch_populations(
            ["Q470369", "Q1085", "Q470369", "Q5", "", "not-a-qid"])
        assert found == {"Q470369": (33444, 2025), "Q1085": (1397880, 2025)}
        assert failed == 0
        assert sorted(c[2]["entity"] for c in sess.calls) == ["Q1085", "Q470369", "Q5"]
        assert all(c[2]["property"] == "P1082" and c[2]["action"] == "wbgetclaims"
                   for c in sess.calls)
        assert all(c[1] == wd.WIKIDATA_TIMEOUT for c in sess.calls)

    def test_network_trouble_is_counted_not_raised(self, monkeypatch):
        sess = _WikiSession({
            "Q1": requests.exceptions.ConnectionError("offline"),
            "Q2": _Resp({}, status=503),
            "Q3": _Resp({"claims": {"P1082": [_claim(10)]}}),
        })
        monkeypatch.setattr(wd, "_http", lambda: sess)
        found, failed = wd.fetch_populations(["Q1", "Q2", "Q3"])
        assert failed >= 2                   # Q1 (offline) and Q2 (HTTP 503)
        assert set(found) <= {"Q3"}          # Q3 may be skipped once offline

    def test_nothing_to_ask(self):
        assert wd.fetch_populations([None, ""]) == ({}, 0)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class TestStationInfoWorker:

    @pytest.fixture(autouse=True)
    def _qapp(self):
        from PySide6.QtWidgets import QApplication
        yield QApplication.instance() or QApplication([])

    def _run(self, points):
        from PySide6.QtCore import QEventLoop, QTimer
        from gui.worker import StationInfoWorker
        worker = StationInfoWorker(points)
        loop = QEventLoop()
        result = {}
        worker.info_ready.connect(
            lambda res, note: (result.update(results=res, note=note), loop.quit()))
        worker.failed.connect(lambda e: (result.update(error=e), loop.quit()))
        watchdog = QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(loop.quit)
        watchdog.start(10000)
        worker.start()
        loop.exec()
        worker.wait(2000)
        assert result, "worker neither finished nor failed within the watchdog timeout"
        return result

    def test_wikidata_first_then_the_osm_tag(self, monkeypatch):
        monkeypatch.setattr(q, "fetch_municipalities", lambda pts, progress_cb=None: [
            {"name": "Kolín", "wikidata": "Q470369", "population": 30826,
             "population_year": None, "ref": ""},
            {"name": "Velim", "wikidata": "Q9", "population": 3200,
             "population_year": 2020, "ref": ""},
            None,
            {"name": "Nowhere", "wikidata": "", "population": None,
             "population_year": None, "ref": ""},
        ])
        monkeypatch.setattr(wd, "fetch_populations", lambda qids, progress_cb=None: (
            {"Q470369": (33444, 2025)}, 1))
        r = self._run([(50.0, 15.0)] * 4)
        assert r["results"] == [
            {"municipality": "Kolín", "population": 33444,
             "population_year": 2025, "population_source": "wikidata"},
            {"municipality": "Velim", "population": 3200,
             "population_year": 2020, "population_source": "osm"},
            None,
            {"municipality": "Nowhere", "population": None,
             "population_year": None, "population_source": ""},
        ]
        assert "Wikidata did not answer for 1 municipality" in r["note"]

    def test_overpass_down_reports_the_friendly_summary(self, monkeypatch):
        def _down(*a, **k):
            raise q.OverpassUnavailable([("overpass-api.de", "busy", "overloaded (HTTP 502)")])
        monkeypatch.setattr(q, "fetch_municipalities", _down)
        r = self._run([(50.0, 15.0)])
        assert r["error"].splitlines()[0] == q.OverpassUnavailable._SUMMARIES["busy"]
