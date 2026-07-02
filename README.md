# LA Street-Light Assessment (District No. 5500) — Map & Analysis

Interactive map and data tooling for the **proposed Streetlight Maintenance
Assessment District No. 5500** — the per-parcel street-light assessments that
Los Angeles property owners **rejected** in the June 2026 Proposition 218
ballot.

🗺️ **Live map: https://bckohan.github.io/la_street_lights/**

## Background

The LA Bureau of Street Lighting (BSL) proposed a single citywide assessment
district (No. 5500) for FY 2026/27 to fund maintenance of the City's ~216,000
street lights — about **$111.76M/yr** to be assessed across **584,001 parcels**.
Under Prop 218 it required a weighted property-owner ballot; ballots were due
June 2, 2026 and the City Council certified the result on June 26, 2026 — the
assessment was **voted down**.

Authoritative record: [City Clerk Council File 26-0331](https://cityclerk.lacity.org/lacityclerkconnect/index.cfm?fa=ccfi.viewrecord&cfnumber=26-0331)
· [BSL Prop 218 page](https://lalights.lacity.org/residents/prop_218.html)

## The map

A static [MapLibre GL JS](https://maplibre.org/) + [PMTiles](https://docs.protomaps.com/pmtiles/)
site (no API key, no server — hosted on GitHub Pages). Features:

- **Choropleth** of each parcel's proposed annual assessment (log-scale color bins).
- **District 5500 footprint** in one flat color at low zoom (parcels are too dense
  to draw individually when zoomed out).
- **BSL street lights** (~220k) as a toggleable point layer.
- **Land-use filters** (Residential, Retail, Office, Industrial, …) that also drive
  a live readout of selected **ballots** and **assessed value** vs. the totals.
- **Address search** over ~791k parcel addresses, fully client-side (no geocoding
  service) — built from the County situs data.
- **Popups** with APN, address, assessment, units, land use, lighting class
  (with an explainer), benefit points, and a link to the LA County tax bill.
- Mobile-friendly collapsible legend.

## Repository layout

```
src/la_street_lights/bin/   # the `street-lights` CLI (Typer)
  cli.py                    # root command; registers the subcommands below
  parse_assessments.py      # assessment-roll PDFs      -> parsed_assessments.csv
  unassessed_apns.py        # City APNs not in the roll -> city_apns_not_in_district.csv
  build_map.py              # builds all web/data/* map assets
  serve.py                  # local dev server (adds the HTTP Range support PMTiles needs)
web/                        # the static site (deployed to GitHub Pages)
  index.html, app.js
  data/                     # generated tiles/index (see below)
sources/                    # input data (large files are gitignored)
docs/superpowers/specs/     # design spec
```

## Requirements

- Python ≥ 3.12 with [uv](https://github.com/astral-sh/uv) (`uv sync`)
- [GDAL](https://gdal.org/) (`ogr2ogr`) and [tippecanoe](https://github.com/felt/tippecanoe)
  on `PATH` — `brew install gdal tippecanoe`
- Source data in `sources/` (not committed — public downloads):
  - `####_####.pdf` — BSL assessment-roll PDFs
  - `LACounty_Parcels_Shapefile.zip` — [LA County parcels](https://egis-lacounty.hub.arcgis.com/)
  - `APNs_in_the_City_of_Los_Angeles_*.csv` — [LA City APNs](https://geohub.lacity.org/)
  - (street lights and the city boundary are fetched at build time)

## Usage

`uv sync` installs the package and a single `street-lights` command with all the
tools as subcommands (run them with `uv run` or from the activated venv):

```bash
uv sync
uv run street-lights --help
```

```bash
# 1. Parse the assessment-roll PDFs into one CSV (~584k rows)
uv run street-lights parse-assessments

# 2. (optional) City APNs that are NOT in the assessment roll
uv run street-lights unassessed-apns

# 3. Build all map assets into web/data/ (reprojects parcels, joins the roll,
#    runs tippecanoe, fetches the city boundary + street lights, computes the
#    color scale and per-land-use stats). ~5-6 min.
uv run street-lights build-map --scale log

# Run it on a single AIN range for a fast test build:
uv run street-lights build-map --scale log --ain-prefix 2004
```

Generated `web/data/` files: `parcels.pmtiles`, `district.pmtiles`,
`streetlights.pmtiles`, `addresses.tsv.gz`, `scale.json`, `landuse_stats.json`,
`city_boundary.geojson`.

### Run the site locally

```bash
uv run street-lights serve     # serves web/ at http://localhost:8765 (with Range support)
```

> Note: a plain `python -m http.server` will not work — it ignores HTTP `Range`
> requests, which PMTiles relies on. Use `street-lights serve`.

### Deploy

Pushing `web/**` to `main` triggers the GitHub Actions workflow
(`.github/workflows/pages.yml`), which publishes `web/` to GitHub Pages
(Pages source must be set to "GitHub Actions").

## Notes

- "Ballots" in the readout = one per assessed parcel; "assessed value" is the sum
  of proposed annual assessments. Totals reconcile with the Engineer's Report
  ($111,762,784 ≈ the stated $111,762,500 balance).
- The committed `*.pmtiles` are regenerated by `street-lights build-map`; they're served by
  Pages (which can't serve Git LFS, so they're normal files).
- The tax-bill popup link opens the LA County Treasurer & Tax Collector
  duplicate-bill page and copies the AIN — TTC exposes no direct, linkable
  bill-PDF URL.

## Data sources

LA Bureau of Street Lighting (assessment roll & Engineer's Report), LA County
Assessor parcels, LA City GeoHub (APNs, street lights), and the U.S. Census
TIGERweb service (city boundary).
