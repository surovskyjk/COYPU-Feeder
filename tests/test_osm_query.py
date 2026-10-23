"""
Overpass runner robustness (src/osm/query.py), fully offline: a scripted fake HTTP session
stands in for the mirrors, `time.sleep` is recorded instead of slept, and the disk cache
lives in tmp_path.

Covers: waiting for a 429'd mirror's slot, runtime-error remarks as failures (never cached),
HTTP 400 stopping at once, the per-host OverpassUnavailable summary, the second pass,
move-to-front mirror order, read timeout = declared timeout + slack, the one-round-trip
relation query and its OSM API fallback, stop filtering, and the station fallback worker.
"""

from __future__ import annotations

import json
import os
import sys
import time
import types
from urllib.parse import urlparse

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import osm.query as q  # noqa: E402

QUERY = "[out:json][timeout:10];relation(1);out tags;"
GOOD = {"elements": [{"type": "relation", "id": 1, "tags": {"name": "ok"}}]}


class FakeResponse:
    def __init__(self, status=200, json_data=None, text=None):
        self.status_code = status
        self._json = json_data
        self.text = text if text is not None else (
            json.dumps(json_data) if json_data is not None else "")

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def json(self):
        if self._json is None:
            raise ValueError("not JSON")
        return self._json

    def raise_for_status(self):
        if not self.ok:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeSession:
    """Scripted per endpoint: key "host" for interpreter POSTs, "host/status" for status
    GETs, "api.openstreetmap.org" for OSM API GETs. Each value is a list consumed in order
    (the last entry repeats); an entry is a FakeResponse or an exception to raise.
    An endpoint missing from the script behaves like an unresolvable host."""

    def __init__(self, script: dict):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[tuple] = []   # (method, key, data, timeout)

    def _serve(self, method, url, data, timeout):
        host = urlparse(url).hostname
        key = f"{host}/status" if url.endswith("/status") else host
        self.calls.append((method, key, data, timeout))
        queue = self.script.get(key)
        if not queue:
            raise requests.exceptions.ConnectionError(
                f"HTTPSConnectionPool(host='{host}'): NameResolutionError")
        entry = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(entry, BaseException):
            raise entry
        return entry

    def post(self, url, data=None, timeout=None):
        return self._serve("POST", url, data, timeout)

    def get(self, url, timeout=None):
        return self._serve("GET", url, None, timeout)

    def posts(self):
        return [c[1] for c in self.calls if c[0] == "POST"]


@pytest.fixture
def osm(monkeypatch, tmp_path):
    monkeypatch.setattr(q, "_CACHE_DIR", tmp_path / "cache")
    sleeps: list[float] = []
    monkeypatch.setattr(q, "time", types.SimpleNamespace(
        time=time.time, monotonic=time.monotonic, sleep=sleeps.append))
    q._reset_endpoint_order()

    def use(script: dict) -> FakeSession:
        session = FakeSession(script)
        monkeypatch.setattr(q, "_http", lambda: session)
        return session

    yield types.SimpleNamespace(use=use, sleeps=sleeps, cache=tmp_path / "cache")
    q._reset_endpoint_order()


HOSTS = [urlparse(u).hostname for u in q.OVERPASS_ENDPOINTS]


class TestRunQuery:

    def test_429_waits_for_the_slot_and_retries_the_same_mirror(self, osm):
        s = osm.use({
            "overpass-api.de": [FakeResponse(429), FakeResponse(200, GOOD)],
            "overpass-api.de/status": [FakeResponse(200, text=(
                "Rate limit: 2\nSlot available after: 2026-10-23T08:40:01Z, "
                "in 3 seconds.\n"))],
        })
        assert q._run_query(QUERY, timeout=10) == GOOD
        assert s.posts() == ["overpass-api.de", "overpass-api.de"]
        assert osm.sleeps == [4]

    def test_429_with_a_long_wait_moves_on(self, osm):
        s = osm.use({
            "overpass-api.de": [FakeResponse(429)],
            "overpass-api.de/status": [FakeResponse(200, text="Slot available after: x, in 90 seconds.")],
            "lz4.overpass-api.de": [FakeResponse(200, GOOD)],
        })
        assert q._run_query(QUERY, timeout=10) == GOOD
        assert s.posts() == ["overpass-api.de", "lz4.overpass-api.de"]
        assert osm.sleeps == []

    def test_runtime_remark_is_a_failure_and_never_cached(self, osm):
        remark = {"elements": [], "remark": 'runtime error: Query timed out in "recurse" '
                                            "at line 3 after 10 seconds."}
        s = osm.use({
            "overpass-api.de": [FakeResponse(200, remark)],
            "lz4.overpass-api.de": [FakeResponse(200, GOOD)],
        })
        assert q._run_query(QUERY, timeout=10) == GOOD
        assert s.posts() == ["overpass-api.de", "lz4.overpass-api.de"]
        assert q._cache_get(q._query_key(QUERY), 3600) == GOOD
        # The mirror that ran out of time went to the back, the one that answered to the front.
        order = [urlparse(u).hostname for u in q._endpoints()]
        assert order[0] == "lz4.overpass-api.de" and order[-1] == "overpass-api.de"

    def test_all_remarks_leave_no_cache_entry(self, osm):
        remark = {"elements": [], "remark": "runtime error: Query run out of memory using about 2048 MB of RAM."}
        osm.use({h: [FakeResponse(200, remark)] for h in HOSTS})
        with pytest.raises(q.OverpassUnavailable):
            q._run_query(QUERY, timeout=10, passes=1)
        assert not list(osm.cache.glob("*.json"))

    def test_http_400_stops_after_one_mirror(self, osm):
        s = osm.use({h: [FakeResponse(400)] for h in HOSTS})
        with pytest.raises(q.OverpassUnavailable) as ei:
            q._run_query(QUERY, timeout=10)
        assert ei.value.category == "bad_query"
        assert s.posts() == ["overpass-api.de"]

    def test_total_failure_names_every_host_and_reads_as_busy(self, osm):
        osm.use({
            "overpass-api.de": [FakeResponse(502)],
            "lz4.overpass-api.de": [requests.exceptions.ReadTimeout("read timed out")],
            "z.overpass-api.de": [FakeResponse(504)],
            # private.coffee: unresolvable
            "maps.mail.ru": [FakeResponse(429)],
        })
        with pytest.raises(q.OverpassUnavailable) as ei:
            q._run_query(QUERY, timeout=10, passes=1)
        exc = ei.value
        assert [a[0] for a in exc.attempts] == HOSTS
        assert exc.category == "busy"
        text = q.friendly_error(exc)
        assert text.splitlines()[0] == q.OverpassUnavailable._SUMMARIES["busy"]
        assert "lz4.overpass-api.de: no answer in time" in text
        assert "overpass.private.coffee: unreachable (DNS lookup failed)" in text

    def test_offline_everywhere_reads_as_offline_without_a_second_pass(self, osm):
        s = osm.use({})
        with pytest.raises(q.OverpassUnavailable) as ei:
            q._run_query(QUERY, timeout=10, passes=2)
        assert ei.value.category == "offline"
        assert "internet connection" in q.friendly_error(ei.value).splitlines()[0]
        assert osm.sleeps == []
        assert s.posts() == HOSTS

    def test_second_pass_after_a_pause_when_mirrors_were_only_busy(self, osm):
        script = {h: [FakeResponse(502)] for h in HOSTS}
        script["overpass-api.de"] = [FakeResponse(502), FakeResponse(200, GOOD)]
        s = osm.use(script)
        assert q._run_query(QUERY, timeout=10, passes=2) == GOOD
        assert osm.sleeps == [q.RETRY_PAUSE]
        assert s.posts() == HOSTS + ["overpass-api.de"]

    def test_a_working_mirror_is_asked_first_next_time(self, osm):
        s = osm.use({
            "overpass-api.de": [FakeResponse(502)],
            "lz4.overpass-api.de": [FakeResponse(200, GOOD)],
        })
        q._run_query(QUERY, timeout=10)
        q._run_query(QUERY.replace("relation(1)", "relation(2)"), timeout=10)
        assert s.posts() == ["overpass-api.de", "lz4.overpass-api.de", "lz4.overpass-api.de"]

    def test_read_timeout_is_the_declared_timeout_plus_slack(self, osm):
        s = osm.use({"overpass-api.de": [FakeResponse(200, GOOD)]})
        q._run_query(QUERY, timeout=25)
        assert s.calls[0][3] == (q.CONNECT_TIMEOUT, 25 + q.QUEUE_SLACK)

    def test_cache_hit_skips_the_network(self, osm):
        s = osm.use({"overpass-api.de": [FakeResponse(200, GOOD)]})
        q._run_query(QUERY, timeout=10)
        q._run_query(QUERY, timeout=10)
        assert s.posts() == ["overpass-api.de"]
        assert not list(osm.cache.glob("*.tmp"))   # atomic writes leave nothing behind

    def test_search_text_is_escaped_for_the_ql_string(self, osm):
        s = osm.use({"overpass-api.de": [FakeResponse(200, {"elements": []})]})
        q.search_railways_by_name('Praha "hl.n.\\')
        sent = s.calls[0][2]["data"]
        assert '"name"~"Praha \\"hl.n.\\\\",i]' in sent


RELATION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <node id="1" lat="50.0" lon="14.0"/>
  <node id="2" lat="50.001" lon="14.001">
    <tag k="railway" v="halt"/><tag k="name" v="Zastávka"/>
  </node>
  <node id="3" lat="50.002" lon="14.002"/>
  <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><tag k="railway" v="rail"/></way>
  <relation id="42">
    <member type="way" ref="10" role=""/>
    <tag k="name" v="Test line"/><tag k="operator" v="SŽ"/>
  </relation>
</osm>
"""


class TestRelationFetch:

    def test_one_round_trip_carries_the_relation_tags(self, osm):
        data = {"elements": [
            {"type": "relation", "id": 42, "tags": {"name": "Test line", "network": "CZ"}},
            {"type": "way", "id": 10, "nodes": [1, 2], "tags": {"railway": "rail"}},
            {"type": "node", "id": 1, "lat": 50.0, "lon": 14.0},
            {"type": "node", "id": 2, "lat": 50.001, "lon": 14.001},
        ]}
        s = osm.use({"overpass-api.de": [FakeResponse(200, data)]})
        got = q.fetch_relation_ways(42)
        sent = s.calls[0][2]["data"]
        assert ".r out tags;" in sent and "way(r.r);" in sent and "node(w);" in sent
        assert q.relation_info_from_data(got, 42) == {
            "id": 42, "name": "Test line", "network": "CZ",
            "operator": "", "from": "", "to": ""}
        from osm.parser import parse_tracks
        assert parse_tracks(got)[0].nodes == [(50.0, 14.0), (50.001, 14.001)]

    def test_falls_back_to_the_osm_api_after_one_pass(self, osm):
        s = osm.use({"api.openstreetmap.org": [FakeResponse(200, text=RELATION_XML)]})
        data = q.fetch_relation_ways(42)
        assert osm.sleeps == []                         # one pass, no retry pause
        assert s.posts() == HOSTS                       # each mirror asked once
        assert q.relation_info_from_data(data, 42)["name"] == "Test line"
        stops = q.stops_from_elements(data["elements"])
        assert [st["name"] for st in stops] == ["Zastávka"]
        # Cached under the real query's key (one entry, readable back).
        files = list(osm.cache.glob("*.json"))
        assert len(files) == 1
        assert json.loads(files[0].read_text(encoding="utf-8")) == data

    def test_relation_unknown_to_the_osm_api_reads_as_not_found(self, osm):
        osm.use({"api.openstreetmap.org": [FakeResponse(404)]})
        with pytest.raises(requests.exceptions.HTTPError) as ei:
            q.fetch_relation_ways(42)
        assert "not found" in q.friendly_error(ei.value)

    def test_an_osm_api_failure_joins_the_attempt_list(self, osm):
        osm.use({"api.openstreetmap.org": [FakeResponse(503)]})
        with pytest.raises(q.OverpassUnavailable) as ei:
            q.fetch_relation_ways(42)
        assert ei.value.attempts[-1] == (q.OSM_API_HOST, "busy", "overloaded (HTTP 503)")

    def test_an_empty_relation_result_is_not_cached(self, osm):
        osm.use({"overpass-api.de": [FakeResponse(200, {"elements": []})]})
        assert q.fetch_relation_ways(42) == {"elements": []}
        assert not list(osm.cache.glob("*.json"))


class TestFriendlyError:

    def test_requests_errors_by_type(self):
        assert "internet connection" in q.friendly_error(
            requests.exceptions.ConnectTimeout("x"))
        assert "did not answer in time" in q.friendly_error(
            requests.exceptions.ReadTimeout("x"))
        assert "internet connection" in q.friendly_error(
            requests.exceptions.ConnectionError("x"))

    def test_other_exceptions_show_their_first_line(self):
        assert q.friendly_error(RuntimeError("first\nsecond")) == "first"


class TestStopsFromElements:

    def test_selects_passenger_stops_only(self):
        els = [
            {"type": "node", "id": 1, "lat": 1, "lon": 2, "tags": {"railway": "station", "name": "A"}},
            {"type": "node", "id": 2, "lat": 1, "lon": 2, "tags": {"railway": "halt", "name": "B"}},
            {"type": "node", "id": 3, "lat": 1, "lon": 2,
             "tags": {"public_transport": "stop_position", "train": "yes", "name": "C"}},
            {"type": "node", "id": 4, "lat": 1, "lon": 2,
             "tags": {"public_transport": "stop_position", "bus": "yes", "name": "bus"}},
            {"type": "node", "id": 5, "lat": 1, "lon": 2, "tags": {"railway": "switch"}},
            {"type": "node", "id": 6, "lat": 1, "lon": 2,
             "tags": {"railway": "station", "passenger": "no", "name": "freight"}},
            {"type": "node", "id": 7, "lat": 1, "lon": 2,
             "tags": {"railway": "station", "usage": "freight", "name": "yard"}},
            {"type": "way", "id": 8, "tags": {"railway": "station"}},
            {"type": "node", "id": 1, "lat": 1, "lon": 2, "tags": {"railway": "station", "name": "A"}},
            {"type": "node", "id": 9, "lat": 1, "lon": 2},
        ]
        got = q.stops_from_elements(els)
        assert [(s["name"], s["kind"]) for s in got] == [
            ("A", "station"), ("B", "halt"), ("C", "stop_position")]


class TestStationFallbackWorker:

    @pytest.fixture(autouse=True)
    def _qapp(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        yield app

    @pytest.fixture
    def worker_env(self, monkeypatch):
        """Overpass down; snapping replaced by "every stop sits on the alignment"."""
        import geometry.stationing as stationing

        def _down(*a, **k):
            raise q.OverpassUnavailable([("overpass-api.de", "busy", "overloaded (HTTP 502)")])

        def _snap(stations, elements, epsg):
            for st in stations:
                st.dist_m = 0.0

        monkeypatch.setattr(q, "fetch_stations_in_bbox", _down)
        monkeypatch.setattr(stationing, "snap_stations", _snap)

    def _run(self, fallback):
        from PySide6.QtCore import QEventLoop, QTimer
        from gui.worker import StationDetectWorker
        from osm.parser import Track
        track = Track(nodes=[(50.0, 14.0), (50.1, 14.1)])
        worker = StationDetectWorker([{"type": "Line"}], [track], 5514,
                                     fallback_stops=fallback)
        loop = QEventLoop()
        result = {}
        worker.stations_ready.connect(
            lambda st, note: (result.update(stations=st, note=note), loop.quit()))
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

    def test_uses_on_track_stops_inside_the_box_with_a_note(self, worker_env):
        fallback = [
            {"name": "Inside", "lat": 50.05, "lon": 14.05, "kind": "halt"},
            {"name": "Far away", "lat": 49.0, "lon": 16.0, "kind": "station"},
        ]
        result = self._run(fallback)
        assert [s.name for s in result["stations"]] == ["Inside"]
        assert result["note"].startswith("Overpass unavailable")

    def test_without_a_fallback_reports_the_friendly_summary(self, worker_env):
        result = self._run([])
        assert result["error"].splitlines()[0] == q.OverpassUnavailable._SUMMARIES["busy"]
        assert "overpass-api.de: overloaded (HTTP 502)" in result["error"]
