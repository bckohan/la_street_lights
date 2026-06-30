# LA Street-Light Assessment Parcel Map — Design

**Date:** 2026-06-29
**Status:** Approved (design); pending implementation plan

## 1. Goal

Produce a static web page that renders Los Angeles parcels on a vector-tile
map and colors them by their annual street-light assessment value (a choropleth
"heatmap"). Specifically render:

- The **City of Los Angeles boundary** as an outline.
- **All APNs inside the city** (from `APNs_in_the_City_of_Los_Angeles_*.csv`).
- **All APNs outside the city that appear in the parsed assessment roll**
  (`parsed_assessments.csv`).

Parcels with an assessment value are colored on a value scale; parcels inside
the city with no assessment are drawn in a neutral style.

## 2. Decisions (locked)

| Topic | Decision |
|---|---|
| Render technique | Choropleth (per-parcel fill). Hybrid heatmap (C) held in reserve if the citywide view is too slow. |
| Renderer / tiles | **MapLibre GL JS + PMTiles** (open source, no API token, static hosting). |
| Color scale | **Quantile/log** binning of the `assessment` value so low-value variation is visible. |
| No-value parcels | In-city parcels not in the roll (~254k) rendered **light gray / outline** — visible, distinct from the value ramp. |
| City boundary | **Fetch official** boundary (LA GeoHub; Census TIGER place as fallback). |
| Interactivity | **Click popup** (APN, assessment value, in/out district) **+ legend**. |
| Palette default | Viridis (sequential), easily swappable. |
| Basemap | Free OpenFreeMap "positron" style (no key); plain background fallback for offline. |
| `parcels.pmtiles` in git | Gitignored by default (regenerated via the build command). |

## 3. Data inputs

| Input | Role | Key facts |
|---|---|---|
| `sources/LACounty_Parcels_Shapefile.zip` | Parcel geometry | 2,429,053 polygons; CRS EPSG:2229 (CA State Plane Zone 5, ftUS); join field `AIN` (String[10]). |
| `sources/APNs_in_the_City_of_Los_Angeles_*.csv` | City membership | 836,834 unique 10-digit APNs (2 malformed rows ignored). |
| `sources/parsed_assessments.csv` | Assessment values | 584,001 unique APNs (dashed `####-###-###`); `assessment` column is the value. |

Parcel set to render = `city ∪ roll` ≈ **838,359** AINs
(582,476 in both, 254,358 city-only, 1,525 roll-only).

APN normalization: reduce all APNs/AINs to bare 10 digits (strip dashes) for
joining — consistent with the `unassessed_apns` command.

## 4. Architecture

Two parts: an offline **build command** that produces static assets, and a
**static web app** that renders them. No server-side application code; the only
runtime is a static file host serving HTTP range requests (for PMTiles).

```
sources/ (inputs) ──► build_map (Python/typer) ──► web/data/*  ──► MapLibre app (browser)
```

### 4.1 Build command — `src/la_street_lights/bin/build_map.py`

A typer command consistent with existing `bin` commands. Stages:

1. **Select parcels.** Read `AIN` + geometry from the shapefile via GDAL
   (`ogr2ogr` over `/vsizip/...`), filtered to the `city ∪ roll` AIN set.
2. **Join attributes** per parcel:
   - `ain` (string, 10 digit)
   - `assessment` (float; null if not in roll)
   - `in_district` (bool; AIN ∈ roll)
   - `in_city` (bool; AIN ∈ city list)
3. **Reproject** EPSG:2229 → EPSG:4326.
4. **Emit intermediate** GeoJSONSeq (or FlatGeobuf), then run **tippecanoe**:
   - output `web/data/parcels.pmtiles`, layer `parcels`
   - zoom ≈ `-Z6 -z16`
   - `--drop-densest-as-needed --coalesce-smallest-as-needed --simplification=…`
     to keep low-zoom tiles small (the A→C safety valve).
5. **City boundary.** Fetch from LA GeoHub (Census TIGER place fallback), cache
   raw to `sources/la_city_boundary.geojson`, emit `web/data/city_boundary.geojson`.
6. **Quantile breaks.** Compute from the 584,001 `assessment` values; write
   `web/data/scale.json` (`{breaks: [...], colors: [...]}`) so the page does no
   runtime statistics.

Behavior:
- Reports counts of unmatched AINs (city/roll APNs with no geometry in the
  shapefile) rather than failing.
- Idempotent: re-running regenerates `web/data/*`.
- Flags/options: input paths, output dir, zoom range, palette name, and a way
  to limit to an AIN range (e.g. `2xxx`) for fast test builds.

### 4.2 Web app — `web/`

- `web/index.html` + `web/app.js` using MapLibre GL JS + the PMTiles protocol
  plugin (both from CDN).
- Loads `parcels.pmtiles` as a static file via the `pmtiles://` protocol.
- **Layers:**
  - `parcels-value` — fill via a `step` expression over `assessment` using the
    quantile breaks/colors from `scale.json`.
  - `parcels-novalue` — `in_city && !in_district` parcels in light gray.
  - `parcels-outline` — thin stroke, visible at high zoom only.
  - `city-boundary` — bold line from `city_boundary.geojson`.
- **Legend** keyed to the quantile bins plus a "no assessment" swatch.
- **Click popup**: APN, assessment value (or "not assessed"), in/out district.
- On load: center on LA, fit bounds to the city boundary.
- Basemap: OpenFreeMap "positron" style; plain background fallback.
- Local serving: `python -m http.server` from `web/` (range requests supported).

## 5. Project layout

```
web/
  index.html
  app.js
  data/
    parcels.pmtiles          # gitignored (regenerated)
    city_boundary.geojson
    scale.json
src/la_street_lights/bin/
  build_map.py               # the build command
sources/
  la_city_boundary.geojson   # cached raw fetch (gitignored)
```

## 6. Dependencies & tooling

- **tippecanoe** — new system dependency (`brew install tippecanoe`); recent
  versions write `.pmtiles` directly.
- **GDAL** (`ogr2ogr`/`ogrinfo`) — already installed.
- Python: keep deps light by shelling out to `ogr2ogr`/`tippecanoe`; a small
  in-process join (csv + json). `geopandas` only if the join proves awkward.
- MapLibre GL JS + PMTiles plugin — via CDN in the page.

## 7. Data-volume expectations

- Raw subset geometry ≈ 225 MB (shapefile) / 0.5–1 GB (unsimplified GeoJSON) —
  never shipped to the browser.
- `parcels.pmtiles` total ≈ **60–150 MB** (estimate; to be confirmed by a real
  tippecanoe run), generated once, hosted as one static file.
- Browser loads only viewport tiles: ≈ 0.5–2 MB citywide (z10–12),
  ≈ 1–4 MB at neighborhood zoom (z14–16).

## 8. Verification

- `build_map` on a single AIN range (e.g. `2xxx`) yields a valid PMTiles file;
  `ogrinfo`/`pmtiles` show expected feature counts and the `parcels` layer.
- Reported matched-AIN count ≈ expected set size minus known unmatched.
- Spot-check several parcels in the browser: known SFR (~$147) low color, a
  high-value commercial parcel hot color, a no-value in-city parcel gray, an
  out-of-city roll parcel still rendered, and the city outline correct.
- Confirm citywide zoom renders smoothly (else enable the C hybrid fallback).

## 9. Out of scope (YAGNI)

Search box, address geocoding, time/animation, multiple selectable value
fields, deploy/hosting config. Addable later without redesign.
