# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```powershell
venv\Scripts\python.exe main.py                          # run the app (venv is committed to the working tree)
venv\Scripts\python.exe -m pytest tests\                 # run all tests
venv\Scripts\python.exe -m pytest tests\test_alignment.py -k spiral   # single test / filter
venv\Scripts\pyinstaller.exe coypu_feeder.spec           # build the Windows one-dir bundle (→ dist/Coypu-Feeder)
```

- Headless GUI checks: set `QT_QPA_PLATFORM=offscreen`. Offscreen runs spew Qt GPU errors to stdout — write your own results to stderr with a grep-able prefix.
- The Windows console is cp1250; run `python -X utf8` when a script prints non-ASCII (box-drawing chars, `→`, etc.).
- When adding a module or data file, also add it to `hiddenimports` / `datas` in `coypu_feeder.spec` — PyInstaller's analyzer misses the dynamic `sys.path` setup in `main.py`.

## Architecture

Desktop PySide6 app converting OSM railway polylines into C1-continuous LandXML 1.2 alignments (Line → Spiral → Arc → Spiral → Line) for the COYPU railway software. `main.py` inserts `src/` on `sys.path`; all imports are rooted there (`geometry.candidates`, `gui.app`, …).

### The PI model is the single source of truth

`src/geometry/candidates.py` holds the core editable model:

- `PIAlignment` (tangent polygon: vertices `V`, reference polyline `xy_ref`, per-vertex `PIData` list, `elements`, `last_stats`) and `PIData` (radius/spiral overrides, `omitted`, merge flags).
- `extract_pi_model(...)` derives the model from a fitted candidate; it **auto-merges same-rotation curves whose gap is < `_PI_MIN_TANGENT` (60 m)** — synthetic test tracks need larger separations or runs vanish before you can test merging.
- `rebuild_from_pi_model(model)` regenerates the element chain from the model. **Elements are never persisted anywhere** — not in `.coypuf` files, not across edits; always regenerate. Rebuild also refreshes `model.last_stats` (deviation stats) — consumers read that instead of recomputing.
- `merge_pi_range`, `merge_intermediate_line`, `undo_merge`, `insert_pi`, `delete_pi`, `move_pi` edit the model *and* leave `model.elements` correct via `rebuild_pi_span` (span-local, bit-identical to a full rebuild — see perf invariants); UI callers must not trigger a second rebuild.
- Curve-run consolidation (`find_consolidation_groups` / `apply_consolidation`, in `Step5Design`'s collapsible "Consolidate curve runs" section) trials merges on a `_light_copy` (NOT `deepcopy` — shares `xy_ref`/`chainages_ref` by reference, copies only what a trial merge mutates), then applies accepted groups **back-to-front** so PI indices stay valid. Automatic consolidation also runs during candidate generation (`auto_consolidate`, gated by the `auto_consolidate` setting) — most runs are already merged by the time Step 5 opens.

`gui/app.py` (`App`) is the **single owner** of the working `_pi_model`/`_elements`. Steps never keep their own copy: `Step5Design` (Refine & Consolidate — manual editing AND curve-run consolidation live in one step/one widget) binds the shared element-table dock to the same model object (so undo/restore must mutate the model in place, not replace it). Navigation is free, not gated: `_step_reasons()` computes prerequisites, edits mark downstream steps (Stations/Cross-section/Export) stale via `_mark_stale()`, and stale steps re-prepare from current geometry on entry. Step indices use the symbolic constants `S_FIND..S_EXPORT` — never bare integers.

Note the step *file names* do not match the 9-step UI numbering (`step6_stations.py`, `step6_crosssection.py`, `step8_vertical.py`, `step7_export.py` are UI steps 6–9; `step5_design.py` is UI step 5, hosting the old Refine+Consolidate widgets merged into one); the stack order in `app.py` is authoritative. `.coypuf` files persist the current step index (`project_io.py`); files older than format 1.3 carry it under the pre-merge 9-step numbering, and files older than 2.0 under the 8-step numbering that predates the Vertical Profile step — `_migrate_step` chains both remappings, and its clamp constant `_MAX_STEP` must stay equal to `N_STEPS - 1` (asserted in `tests/test_step_migration.py`).

### The vertical profile mirrors the PI model

`src/geometry/profile.py` is the vertical counterpart of `candidates.py`, and follows the same contract:

- `VerticalProfile` holds the DEM samples (`s`, `z_raw`) plus the smoothing configuration and the manual `DirectConnection` ranges. `rebuild_profile(profile)` regenerates `z_override` / `z_design` / `pvis` / `stats` — **these are never persisted**, exactly like `elements` (`rebuild_from_pi_model`).
- `App` is the single owner of `_profile`; `Step8Vertical` mutates it **in place** and never replaces it (the canvas holds the same object).
- Terrain is sampled along the **fitted centreline** via `sample_points_along_elements` (one batched `projected_to_wgs84` call), not the raw OSM polyline. This is what puts `<Profile>` and `<CoordGeom>` on the same station axis; the export worker only re-samples the OSM polyline when no confirmed profile was handed to it.
- Both smoothing methods share one PVI back end: `douglas_peucker(z_tol)` → `clamp_grades(max_grade)`. The window filter only adds a pre-filter stage — `<ProfAlign>` cannot take thousands of PVIs, so simplification always runs. `clamp_grades` averages a forward- and a backward-anchored sweep (the feasible set is convex, so the average is feasible *and* balanced), which makes it idempotent and end-symmetric.
- `z_raw` carries `NaN` for DEM misses all the way into `.coypuf` (round-trips as the literal `nan` through `_pnt_list`/`_parse_pnt_list`); `rebuild_profile` bridges the gaps. Never bake a substitute 0 m into the stored samples.
- The profile ↔ map hover link uses `chainage_lookup` (the same sampler, so the same station axis), cached in `App` and keyed on the `_elements` list object; it is armed only in `S_VERTICAL` (`_sync_profile_link`). `ProfileCanvas.set_cursor_station` (map → profile) never emits `hover_changed`, so the two directions cannot echo.

The bottom row under the map is one `QStackedWidget` slot (`_detail_stack`) showing the element table, the Step 6 station table or the profile canvas — `_bottom_page(idx)` decides. Build it through `_make_bottom_splitter`: `setChildrenCollapsible(False)` must precede the first `setSizes`, or a pane given 0 px is flagged collapsed for good (how the profile used to start hidden).

### Stations mirror COYPU's stop records

`Station` (`src/geometry/stationing.py`) carries COYPU's stop-record fields (municipality, population, Os/Sp/Ex flags with Sp/Ex manual overrides), and `apply_pattern_rules` is a port of COYPU's `stops_model` rules — COYPU lives at `D:\MT\thesis-railway`. `write_stations_csv` writes COYPU's stops CSV (`CSV_COLUMNS`) and must stay importable by COYPU's `parseStopsCsv` without turning rule-derived flags into manual overrides; `tests/test_station_rules.py` checks both against COYPU's own module when that checkout exists. Municipality/population come from a separate lookup (`fetch_municipalities` = Overpass `is_in`, then `osm.wikidata.fetch_populations`, OSM `population` tag as fallback) so auto-detect never depends on it; rows the user typed (`info_manual`) are never overwritten.

### Geometry conventions

- All geometry math runs in a projected CRS in meters (transformer via `geometry/projection.py`, `make_transformer` is `lru_cache`d); WGS84 appears only at the map/OSM boundary. COYPU targets EPSG:5514 (S-JTSK) for Czech data.
- Spirals are true clothoids (scipy Fresnel). `_compute_zone_geometry(n_pts=0)` is the fast endpoint-only path used everywhere except `_zone_objective` (which passes `n_pts=40`).
- `.coypuf` project files (`src/project_io.py`) are XML in the LandXML house style: 6 decimals for user-facing values, **9 decimals (`NUM_GEOM`) for internal geometry** — 6 is not enough for exact round-trips. Chainages and elements are recomputed on load.

### Performance invariants (hard-won; do not regress)

A merge click on a 1,700-element line went from 171 s to 0.6 s, then to 15 ms on a 135 km line via span-local rebuild. Keep these:

- Deviation checks go through the vectorized `compute_deviation_stats` / `_dists_to_element_vec` — never per-point Python loops over elements.
- Projection calls are **batched**: one `projected_to_wgs84` call for all element samples / all PIs, not one per element.
- `rebuild_pi_span(model, k_lo, k_hi, ...)` is the interactive-edit fast path: it rebuilds only the zones affected by an edit (proven bit-identical to `rebuild_from_pi_model` in `tests/test_span_rebuild.py`) instead of the whole chain, and updates `model.last_stats` from the cached per-point arrays instead of a fresh E×P pass. On success it sets `model.last_splice = {"start", "removed", "added"}` — the exact element-list splice — which the map layer uses to patch only the changed span (`MapWidget.patch_alignment_segmented`) instead of resampling/reprojecting/redrawing the whole alignment. A full rebuild (`rebuild_from_pi_model`) or a wholesale snapshot restore (`apply_model_snapshot`, undo/redo, Restore default) clears `last_splice` to `None` — consumers must treat `None` as "render everything", never assume a stale splice still applies.
- `element_table._rebuild(regenerate=False)` for handlers whose geometry op already rebuilt the elements; the table has an in-place cell-update fast path keyed on element ids.
- The Leaflet map (`gui/static/map.html`, loaded in QWebEngineView) uses shared `L.canvas()` renderers (`preferCanvas: true` on the map itself), lazily-created glow polylines that are removed (not just hidden) once unselected, and JSON.parse for payloads over ~50 KB instead of a JS literal.

When touching geometry code, prove behavior preservation: fingerprint every element (length/radius/station/coords rounded to 9 decimals) before and after, plus max_dev/mean_dev/rmse/C1/endpoint metrics — bit-identical output is the expectation, not "close". Deviation stats (`compute_deviation_stats`/`metrics_from_stats`) carry `mean_deviation` alongside `max_deviation`/`rmse` — any new dict built from these keys (trial copies, snapshots, empty-case fallbacks) must include it too, or a downstream `**metrics` unpack into `CandidateAlignment` will raise.

### Data sources

- Overpass API with OSM API fallback (`src/osm/query.py`). Overpass **rejects the default python-requests User-Agent with 406** — all HTTP goes through the shared `_http()` session carrying `HTTP_HEADERS`. Overpass mirrors are frequently down or queue requests for 20–30 s under weekday load; keep the fallback path working:
  - Every query declares its server `[timeout:T]` and passes the same `timeout=T` to `_run_query`. The client read timeout is `T + QUEUE_SLACK`. Never let the client give up before the server, because an abandoned query keeps holding one of our IP's slots.
  - `OVERPASS_ENDPOINTS` lists distinct services only (`kumi.systems` is an alias of `private.coffee`). The order is move-to-front per session.
  - All failures surface as `OverpassUnavailable` (per-host attempts and a category). Workers emit `friendly_error(exc)`, so GUI code never parses exception strings.
  - Relation geometry carries its own tags in one round trip (`relation_info_from_data`). The OSM API fallback runs after one Overpass pass.
- The CARTO basemap key lives only in the OS credential vault (`src/basemap_key.py`, a near-verbatim port of COYPU's, camelCase kept for diffing). It must never enter `SettingsDialog.values()`, `_prefs`/QSettings or `.coypuf`. It is appended to CARTO tile URLs only (`map_widget.tile_url`).
- Czech railway list: bundled snapshot `src/data/cz_railways.json` (relation 2332889); a user-updated copy in `<AppData>/COYPU-Feeder/` takes precedence (`src/data/cz_lines.py`).
- Elevation from ČÚZK DMR 5G (per-point `identify`, 8-way concurrency) with OpenTopoData as fallback for points outside its coverage (`src/geometry/elevation.py`).
