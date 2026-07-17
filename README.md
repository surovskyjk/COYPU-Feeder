# Coypu-Feeder

**Coypu-Feeder** is a desktop application that queries railway route data from [OpenStreetMap](https://www.openstreetmap.org/) via the Overpass API and converts it into a geometric alignment in the [LandXML 1.2](http://www.landxml.org/) format — the standard exchange format used by civil engineering and railway design software (e.g. Bentley OpenRail, Autodesk Civil 3D, Novapoint).

The fitted alignment follows the canonical railway geometry sequence:

```
Line → Transition Spiral (Clothoid) → Circular Arc → Transition Spiral → Line → …
```

Elevation is sampled from the [Open-Elevation](https://open-elevation.com/) DEM service and fitted as a vertical alignment with parabolic vertical curves.

---

## Download

Pre-built Windows x64 binaries are published as [GitHub Releases](https://github.com/surovskyjk/COYPU-Feeder/releases/latest).

1. Download `COYPU-Feeder-v1.1.0-windows-x64.zip`
2. Extract to any folder
3. Run `Coypu-Feeder.exe`

No Python installation required.

---

## Features

- **Find any railway** by timetable reference number, name, or direct OSM relation ID
- **Search in map view** — find all railway route-relations visible in the current map window (≤ 20 × 20 km)
- **Czech Railways browser** — all ~240 lines of the *Railways in Czech Republic* OSM collection from a **bundled offline snapshot** (instant, no network), refreshable with 🔄 Update
- **Interactive Leaflet map** with 6 selectable tile providers and an optional OpenRailwayMap overlay
- **Track selection** — load a relation, select one or more tracks, highlight individual tracks on the map
- **3 alignment levels** — raw polyline / Lines + Arcs / Lines + Spirals + Arcs, compared side by side on the map before export
- **Clothoid transition curves** — entry/exit spirals at every arc, exact Fresnel-based tangent placement
- **Interactive element table** — every Line/Arc/Spiral with parameters; edit arc radius or spiral length with live map update, omit/restore PIs, merge short straights between curves into prolonged symmetric spirals, or replace a whole PI range with one curve
- **Curve consolidation** — scan for runs of same-direction curves joined by short straights and replace each with a single transition–circular–transition curve, within a deviation tolerance you choose
- **Free navigation** — the steps *suggest* an order but never lock you in: edit the alignment at any point, and the later steps refresh themselves
- **Project files (`.coypu`)** — save and resume work; self-contained (OSM tracks + editable PI model + stations), so reopening needs no network and no re-fitting
- **PI display** — Points of Intersection as markers, virtual tangent extensions dashed light-grey
- **Hover inspection** — elements glow on hover; a 3-second hover shows ID, parameters and deviation statistics
- **Stations & stops** — OSM auto-detection (passenger stations/halts only), map click-to-place or manual entry; chainage estimated along the alignment and exported as a `Station,Dwell Time,Name` CSV consistent with the LandXML stationing
- **Cross-section computation** — perpendicular deviations between the chosen candidate and the OSM polyline
- **Elevation sampling** — DEM-based vertical profile at a configurable chainage interval, fitted with parabolic vertical curves
- **LandXML 1.2 export** — COYPU-compatible structure with horizontal + vertical geometry, Cant block (0 mm placeholders) and CRS metadata
- **Dark / light mode** — automatic, or forced from Settings ▸ Preferences (with adjustable font size)

---

## Screenshots

> *3-column layout: step sidebar | interactive map | step panel*

```
┌──────────────────────────────────────────────────────────────────────┐
│ File  View  Settings  Help                                           │
├───────────────────┬────────────────────────────────────────┬─────────┤
│ [1] Find Railway  │        Interactive Leaflet Map         │  Step   │
│ [2] Select        │  (tile provider selector + railway     │  panel  │
│ [3] Configure     │   overlay toggle)                      │ 340 px  │
│ [4] Candidates    │                                        │         │
│ [5] Refine        ├──────────────┬─────────────────────────┤         │
│ [6] Consolidate   │   Log        │  Element table:         │         │
│ [7] Stations      │  (timestamped│  ID · type · station ·  │         │
│ [8] Cross-section │   activity + │  length · radius · A ·  │         │
│ [9] Export        │   step tips) │  defl · spiral L · ⋯    │         │
│   200 px sidebar  │              │                         │         │
└───────────────────┴──────────────┴─────────────────────────┴─────────┘
```

---

## Working with projects

The nine steps **suggest** an order — they do not lock you into one. Any step whose data exists is clickable in the sidebar at any time (the recommended next one is accented ▸; a locked step's tooltip says what it needs). Edit the alignment in *Refine* or *Consolidate* whenever you like — even after exporting — and the later steps refresh themselves from the current geometry when you open them (station chainages are re-snapped automatically).

Save your work with **File ▸ Save project** (`Ctrl+S`) to a `.coypu` file and pick it up later with **File ▸ Open project** (`Ctrl+O`) or the *Recent projects* list. The file is XML in the same house style as the LandXML output and is **self-contained** — the OSM track nodes, the editable PI model (every radius/spiral override, omitted PI and merge state), settings and stations. Reopening needs no network and re-runs no fitting: the element chain is regenerated from the model exactly as you left it. The title bar shows the project name and marks unsaved changes with `*`.

---

## Usage — Step by Step

### Step 1 — Find Railway

Three ways to find a railway:

**Search tab**
- *Timetable line number (ref tag)* — exact match on the OSM `ref` tag (e.g. `210`)
- *Name* — partial name search (e.g. `Praha`)
- *Number in relation name* — finds lines whose name contains the number, even when the `ref` tag is missing
- *By relation ID* — direct fetch using the numeric OSM relation ID (e.g. `3128446`)

**In View tab**
Zoom the map to an area ≤ 20 × 20 km, then click *Search Railway Lines in Current View*. Returns all railway route-relations whose geometry intersects the visible map area.

**Czech Railways tab**
All members of OSM relation 2332889 are listed **immediately from a bundled offline snapshot** — no network needed — and filtered instantly as you type, by line number, name, or terminal stations. The header shows the snapshot date and whether it is the bundled copy or your own; **🔄 Update list** re-fetches it from OpenStreetMap and stores the result in your user data folder, which is preferred from then on. A failed update keeps the existing snapshot.

Double-click any result or select it and click *Fetch selected* to load the full relation geometry.

---

### Step 2 — Select Section

The loaded tracks appear in a list and are drawn on the map in distinct colours. You can:

- Select individual tracks or use *Select all* / *Clear selection*
- Click a track row to highlight it on the map in yellow
- Click *📍 Fit map to tracks* to zoom the map to show all tracks

Click **Next → Configure** when your selection is ready.

---

### Step 3 — Configure

Configure geometry parameters before running the fitting algorithms:

| Group | Setting | Default | Description |
|---|---|---|---|
| **Project** | Project name | `Railway Alignment` | Written into the LandXML `<Project>` element |
| **Alignment Geometry** | Max deviation (PI tolerance) | 1.0 m | Douglas-Peucker tolerance for extracting the tangent polygon (Points of Intersection) |
| | Minimum curve radius | 150 m | Minimum horizontal curve radius for all circular arcs |
| | Spiral length (Level 3) | 20 m | Clothoid length; auto-shortened where tangents are too short |
| **Export Settings** | Elevation sample interval | 20 m | Chainage step for DEM elevation queries |
| | Vertical curve length | 100 m | Length of parabolic vertical curves at grade changes |

---

### Step 4 — Candidates

Three alignment **levels** are computed in a background thread. Results appear on the map as coloured overlays as each finishes:

| Colour | Level | Description |
|---|---|---|
| 🟣 Purple | **Level 1 — OSM Polyline** | The raw OSM geometry, one Line per vertex pair. Exact but no C1 continuity (reference only) |
| 🟠 Orange | **Level 2 — Lines + Arcs** | Tangent polygon from the OSM line; a circular curve tangent to both tangents at every PI. Fully C1-continuous |
| 🩵 Cyan | **Level 3 — Lines + Spirals + Arcs** | As Level 2, plus clothoid transition spirals at every curve. Spiral radius matches the arc exactly |

Construction principle (Levels 2 & 3): the OSM polyline is simplified into a **tangent polygon** whose interior vertices are the Points of Intersection (PIs). Split curves are automatically re-merged, the curve radius is estimated from the genuinely curved OSM points, and the alignment **starts and ends exactly at the original OSM endpoints**. For Level 3 the spiral tangent length uses the exact Fresnel-based shift: `T_s = (R + p)·tan(|δ|/2) + k`, so every Line–Spiral–Arc–Spiral–Line zone closes on the outgoing tangent by construction. Where a spiral cannot fit, the curve falls back to a plain arc — C1 continuity is never sacrificed.

Each result card shows the element count, maximum deviation, RMSE, and the worst C1 heading mismatch (green < 0.1°). Step 4 automatically tries three PI tolerances per level and keeps the best result. Select one level and click **Next →** to proceed.

---

### Step 5 — Refine

The full **element table** appears under the map: every Line, Arc and Spiral with its ID, station [km], length, radius, clothoid A and deflection.

- **Edit** an arc *Radius* or a *Spiral L* value — the alignment rebuilds and the map updates automatically (values are clamped to what the tangent geometry allows; C1 continuity is preserved by construction).
- **Omit PI** removes a curve; the neighbouring curves absorb its deflection. Omitted PIs stay listed (struck through) and can be restored.
- **Merge spirals ↔** appears on short straights (< 30 m) sandwiched between two transition curves: the straight is removed by prolonging the adjacent spirals, and the spirals on the far side of each circular curve prolong identically so both curves stay **symmetrical**. *Undo merge* restores the previous state.
- **Merge PI range → single curve** replaces every PI between the two selected tangents with one curve: select the first and last tangent (Ctrl+click works directly on the map) and press the button.
- The map shows the **PIs** as markers and the **virtual tangent extensions** toward each PI as dashed light-grey lines. Click a table row to highlight that element, or click an element on the map to select its row (Ctrl+click multi-selects); hover an element for 3 s to see its ID and deviation statistics.

Click **Accept →** when the geometry is final — though you can return here and keep editing at any later point.

---

### Step 6 — Consolidate

Level 2/3 sometimes split one physical curve into a run of consecutive curves. This step replaces such a run with a single transition–circular–transition curve.

| Setting | Default | Meaning |
|---|---|---|
| Max intermediate straight | 30 m | Curves separated by a longer straight are left alone (0 = only curves that touch directly) |
| Max deviation from OSM | 2.0 m | The merged curve must stay within this of the OSM polyline, otherwise the run is rejected |

**🔍 Scan** lists every run of same-direction curves that matches the rules: `PIs | Curves | R before → after | Max dev | Status`. Runs within tolerance are ticked; rejected runs are greyed **with the reason shown** rather than hidden. Untick anything you want to keep, then **✅ Apply selected** — or **↩ Undo** to restore. Selecting a row highlights that run on the map.

> Runs separated by less than ~60 m are already merged automatically while the alignment is built; this step exists for the wider cases you want to control yourself.

---

### Step 7 — Stations

Build the station/stop list whose chainages are measured along the fitted alignment (identical to the LandXML stationing):

- **⚡ Auto-detect** — queries OpenStreetMap for `railway=station`, `railway=halt` and train `stop_position` nodes within 100 m of the alignment (passenger facilities only; yards, service stations and freight-only sites are excluded). Each name appears once — the closest occurrence wins.
- **📍 Place on map** — click the map; the point is snapped to the alignment and you are asked for a name.
- **＋ Add row** — manual entry; edit the km value directly in the table.

Station [km], Dwell [s] (default 30) and Name are editable; names must be unique. The list is exported in Step 9 as a CSV next to the LandXML file (💾 Export CSV… saves it any time):

```csv
Station,Dwell Time,Name
1.234,30,Praha hl.n.
5.678,30,Řevnice
```

---

### Step 8 — Cross-Section

The perpendicular deviation between the fitted alignment and the OSM polyline is plotted as a profile. Maximum and mean deviation values are displayed. This step is informational — it helps you verify the fit quality before committing to export.

Click **Next → Export** to proceed.

---

### Step 9 — Export

1. Choose an output CRS from the preset list or enter a custom EPSG code
2. Click **Browse…** and choose an output `.xml` file path
3. Click **▶ Start Export** — the LandXML and the stations CSV (`same-name.csv`) are written together

**Available CRS presets:**

| Preset | EPSG |
|---|---|
| WGS 84 | 4326 |
| S-JTSK / Krovak East North | 5514 |
| UTM zone 32N | 32632 |
| UTM zone 33N | 32633 |
| UTM zone 34N | 32634 |
| ETRS89 / UTM zone 32N | 25832 |
| ETRS89 / UTM zone 33N | 25833 |
| Auto UTM (from track centroid) | — |

The progress bar tracks the export stages (Projecting → Fitting → Querying elevation → Building XML → Writing). On completion, the exported alignment is drawn on the map as a **bright red line** with a white glow halo.

---

## Output Format

The output is a valid **LandXML 1.2** file structured for direct use in
**COYPU** (GeoTEL Rail-style layout — coordinate points as child elements,
spirals with tangent PI, and a Cant block):

```xml
<LandXML version="1.2" ...>
  <Application name="COYPU Feeder" version="1.1.0" .../>
  <Project name="Railway Alignment" desc="Generated by COYPU Feeder from OpenStreetMap data …"/>
  <Units><Metric areaUnit="squareMeter" linearUnit="meter" .../></Units>
  <CoordinateSystem epsgCode="5514" .../>
  <Alignments name="alignments">
    <Alignment name="..." length="..." staStart="0.000000">
      <CoordGeom>
        <Line staStart="...">
          <Start>… …</Start><End>… …</End>
        </Line>
        <Spiral staStart="..." desc="TangentToCurve" length="..." radiusStart="INF"
                radiusEnd="450.000000" rot="cw" spiType="clothoid" constant="...">
          <Start>… …</Start><PI>… …</PI><End>… …</End>
        </Spiral>
        <Curve staStart="..." rot="cw" crvType="arc" radius="450.000000">
          <Start>… …</Start><Center>… …</Center><End>… …</End>
        </Curve>
        <Spiral desc="CurveToTangent" radiusStart="450.000000" radiusEnd="INF" .../>
        ...
      </CoordGeom>
      <Profile staStart="...">
        <ProfAlign name="...">
          <PVI>… …</PVI>
          <ParaCurve length="100.000000">… …</ParaCurve>
        </ProfAlign>
      </Profile>
      <Cant name="..." gauge="1.435000" superelevationBase="1.500000"
            rotationPoint="insideRail" coPlane="false" stationType="centreline">
        <CantStation station="..." appliedCant="0.000000" rotationPoint="insideRail"
                     appliedGauge="1.435000" appliedSuperelevation="1.500000"/>
        ...
      </Cant>
    </Alignment>
  </Alignments>
</LandXML>
```

Key properties guaranteed by the geometry engine:
- **Radius continuity** at every element boundary: `Line → Spiral (radiusStart=∞)`, `Spiral → Arc (radiusEnd = arc radius)`, etc.
- **Clothoid parameter** `A = √(R × L)` (the `constant` attribute) recomputed from the actual fitted radius and spiral length; each spiral carries its tangent **PI**
- **Cant block**: one `CantStation` per element boundary with `appliedCant="0"` — cant cannot be derived from OSM and is meant to be designed downstream in COYPU
- `staStart` present on every element; all values with 6-decimal precision

---

## Requirements (source installation)

### Python

Python **3.10 or newer**.

### Dependencies

```bash
pip install -r requirements.txt
```

| Package | Purpose |
|---|---|
| `PySide6` | GUI framework (Qt 6), WebEngine for the map |
| `requests` | Overpass API and elevation API HTTP calls |
| `pyproj` | WGS84 ↔ projected CRS transformations |
| `numpy` | Curvature computation, SVD line fitting |
| `scipy` | Savitzky-Golay smoothing, Fresnel integrals |
| `lxml` | LandXML file generation |
| `shapely` | Geometry utilities |
| `overpy` | Overpass API helper |

### Internet access

| Service | Used for |
|---|---|
| Overpass API (`overpass-api.de`, `overpass.kumi.systems`) | Railway geometry and relation metadata |
| CARTO / OpenStreetMap / OpenTopoMap / Esri / OpenRailwayMap tile servers | Map background tiles |
| Open-Elevation API (`api.open-elevation.com`) | DEM elevation sampling |

---

## Installation (from source)

```bash
git clone https://github.com/surovskyjk/COYPU-Feeder.git
cd COYPU-Feeder
python -m venv venv

# Windows
venv\Scripts\activate
# Linux / macOS
source venv/bin/activate

pip install -r requirements.txt
python main.py
```

---

## Project Structure

```
COYPU-Feeder/
├── main.py                       # Entry point
├── requirements.txt
├── coypu_feeder.spec             # PyInstaller build spec
├── LICENSE
└── src/
    ├── gui/
    │   ├── app.py                # QMainWindow, signal wiring
    │   ├── map_widget.py         # Leaflet map via QWebEngineView + local HTTP server
    │   ├── element_table.py      # Editable element table (bottom dock)
│   ├── log_panel.py         # Timestamped activity log (bottom-left)
│   ├── branding.py          # App icon + splash artwork (painted)
│   ├── dialogs.py           # About / Settings dialogs
    │   ├── step_sidebar.py       # Numbered step sidebar
    │   ├── theme.py              # Dark/light Fusion palette + stylesheet
    │   ├── worker.py             # QThread workers (Search, Fetch, Candidates, Stations, Export)
    │   ├── static/               # Leaflet 1.9.4 JS + CSS (served locally)
    │   └── steps/
    │       ├── step1_find.py     # Search / In View / Czech Railways tabs
    │       ├── step2_section.py  # Track selection
    │       ├── step3_configure.py# Geometry parameters
    │       ├── step4_candidates.py  # Level 1/2/3 comparison
    │       ├── step5_refine.py   # Interactive editing (with element table)
│       ├── step6_consolidate.py # Merge same-direction curve runs
    │       ├── step6_stations.py # Stations & stops → chainage CSV (step 7)
    │       ├── step6_crosssection.py  # Deviation profile (step 8)
    │       └── step7_export.py   # CRS selection + LandXML/CSV export (step 9)
    ├── osm/
    │   ├── query.py              # Overpass API queries (incl. station detection)
    │   └── parser.py             # OSM way → Track objects
    ├── geometry/
    │   ├── alignment.py          # Element fitting + continuity enforcement
    │   ├── candidates.py         # Level 1/2/3 PI-model + editing + consolidation
    │   ├── stationing.py         # Station chainage projection + CSV writer
    │   ├── curvature.py          # Curvature computation and segmentation
    │   ├── elevation.py          # DEM sampling and vertical geometry
    │   └── projection.py         # CRS transformations (pyproj)
    ├── app_meta.py              # Name / version / license / data sources
    ├── project_io.py            # .coypu project save / load
    ├── data/
    │   ├── suggested_lines.py    # Curated Czech railway line database
    │   ├── cz_lines.py          # Offline CZ list loader (+ user copy)
    │   └── cz_railways.json     # Bundled snapshot of relation 2332889
    └── landxml/
        └── builder.py            # LandXML 1.2 tree builder and file writer
```

---

## License

[MIT License](LICENSE) — © 2025 Jakub Surovsky
