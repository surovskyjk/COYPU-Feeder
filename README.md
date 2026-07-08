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

1. Download `Coypu-Feeder-v1.0.0-windows-x64.zip`
2. Extract to any folder
3. Run `Coypu-Feeder.exe`

No Python installation required.

---

## Features

- **Find any railway** by timetable reference number, name, or direct OSM relation ID
- **Search in map view** — find all railway route-relations visible in the current map window (≤ 20 × 20 km)
- **Czech Railways browser** — lazy-loads all ~300 lines from the *Railways in Czech Republic* OSM collection with instant client-side filtering
- **Interactive Leaflet map** with 6 selectable tile providers and an optional OpenRailwayMap overlay
- **Track selection** — load a relation, select one or more tracks, highlight individual tracks on the map
- **3 alignment levels** — raw polyline / Lines + Arcs / Lines + Spirals + Arcs, compared side by side on the map before export
- **Clothoid transition curves** — entry/exit spirals at every arc, exact Fresnel-based tangent placement
- **Interactive element table** — every Line/Arc/Spiral with parameters; edit arc radius or spiral length with live map update, omit/restore PIs, merge short straights between curves into prolonged symmetric spirals
- **PI display** — Points of Intersection as markers, virtual tangent extensions dashed light-grey
- **Hover inspection** — elements glow on hover; a 3-second hover shows ID, parameters and deviation statistics
- **Stations & stops** — OSM auto-detection (passenger stations/halts only), map click-to-place or manual entry; chainage estimated along the alignment and exported as a `Station,Dwell Time,Name` CSV consistent with the LandXML stationing
- **Cross-section computation** — perpendicular deviations between the chosen candidate and the OSM polyline
- **Elevation sampling** — DEM-based vertical profile at a configurable chainage interval, fitted with parabolic vertical curves
- **LandXML 1.2 export** — COYPU-compatible structure with horizontal + vertical geometry, Cant block (0 mm placeholders) and CRS metadata
- **Dark / light mode** — automatically follows the OS system theme

---

## Screenshots

> *3-column layout: step sidebar | interactive map | step panel*

```
┌──────────────────────────────────────────────────────────────────────┐
│ [1] Find Railway  │        Interactive Leaflet Map         │  Step   │
│ [2] Select        │  (tile provider selector + railway     │  panel  │
│ [3] Configure     │   overlay toggle)                      │ 340 px  │
│ [4] Candidates    │                                        │         │
│ [5] Refine        ├─────────────────────────────────────────┤        │
│ [6] Stations      │  Element table (Step 5): ID · type ·   │         │
│ [7] Cross-section │  station · length · radius · A · defl  │         │
│ [8] Export        │  · spiral L · actions                  │         │
│   200 px sidebar  │                                        │         │
└──────────────────────────────────────────────────────────────────────┘
```

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
Click *Load Czech Railway Lines* once to fetch all members of OSM relation 2332889. The list of ~300 lines loads in a few seconds and is then filtered instantly as you type — by line number, name, or terminal stations.

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
| **Export Settings** | Elevation sample interval | 20 m | Chainage step for DEM elevation queries |
| | Vertical curve length | 100 m | Length of parabolic vertical curves at grade changes |
| | Spiral length | 20 m | Entry/exit clothoid length for *Segment & Fit (Spirals)*; 0 = disabled |
| **Alignment Geometry** | Max deviation (PI tolerance) | 1.0 m | Douglas-Peucker tolerance for extracting the tangent polygon (Points of Intersection) |
| | Minimum curve radius | 150 m | Minimum horizontal curve radius for all circular arcs |
| | Spiral length (Level 3) | 20 m | Clothoid length; auto-shortened where tangents are too short |

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
- The map shows the **PIs** as markers and the **virtual tangent extensions** toward each PI as dashed light-grey lines. Click a table row to highlight that element; hover an element for 3 s to see its ID and deviation statistics.

Click **Accept →** when the geometry is final.

---

### Step 6 — Stations

Build the station/stop list whose chainages are measured along the fitted alignment (identical to the LandXML stationing):

- **⚡ Auto-detect** — queries OpenStreetMap for `railway=station`, `railway=halt` and train `stop_position` nodes within 100 m of the alignment (passenger facilities only; yards, service stations and freight-only sites are excluded). Each name appears once — the closest occurrence wins.
- **📍 Place on map** — click the map; the point is snapped to the alignment and you are asked for a name.
- **＋ Add row** — manual entry; edit the km value directly in the table.

Station [km], Dwell [s] (default 30) and Name are editable; names must be unique. The list is exported in Step 8 as a CSV next to the LandXML file:

```csv
Station,Dwell Time,Name
1.234,30,Praha hl.n.
5.678,30,Řevnice
```

---

### Step 7 — Cross-Section

The perpendicular deviation between the fitted alignment and the OSM polyline is plotted as a profile. Maximum and mean deviation values are displayed. This step is informational — it helps you verify the fit quality before committing to export.

Click **Next → Export** to proceed.

---

### Step 8 — Export

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
  <Application name="COYPU Feeder" version="1.0.0" .../>
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
    │   ├── element_table.py      # Editable element table (bottom dock, Step 5)
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
    │       ├── step6_stations.py # Stations & stops → chainage CSV
    │       ├── step6_crosssection.py  # Deviation profile (step 7 in the UI)
    │       └── step7_export.py   # CRS selection + LandXML/CSV export (step 8)
    ├── osm/
    │   ├── query.py              # Overpass API queries (incl. station detection)
    │   └── parser.py             # OSM way → Track objects
    ├── geometry/
    │   ├── alignment.py          # Element fitting + continuity enforcement
    │   ├── candidates.py         # Level 1/2/3 PI-model construction + editing
    │   ├── stationing.py         # Station chainage projection + CSV writer
    │   ├── curvature.py          # Curvature computation and segmentation
    │   ├── elevation.py          # DEM sampling and vertical geometry
    │   └── projection.py         # CRS transformations (pyproj)
    ├── data/
    │   └── suggested_lines.py    # Curated Czech railway line database
    └── landxml/
        └── builder.py            # LandXML 1.2 tree builder and file writer
```

---

## License

[MIT License](LICENSE) — © 2025 Jakub Surovsky
