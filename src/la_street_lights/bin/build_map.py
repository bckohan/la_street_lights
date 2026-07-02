"""Build the static data assets for the parcel-assessment map.

Produces, under ``web/data/``:

* ``parcels.pmtiles`` — a vector-tile pyramid of every parcel in
  ``city ∪ roll`` (~838k parcels), each carrying ``ain``, ``assessment``
  (or absent), ``in_city`` and ``in_district`` properties.
* ``city_boundary.geojson`` — the City of Los Angeles boundary.
* ``scale.json`` — quantile breaks + a viridis color ramp for the choropleth,
  computed once here so the web page does no runtime statistics.

Pipeline: parcel geometry comes from the LA County parcel shapefile (joined on
its ``AIN`` field). ``ogr2ogr`` streams the whole layer as newline-delimited
GeoJSON reprojected to EPSG:4326; this command keeps only the parcels in the
``city ∪ roll`` set, rewrites their properties, and pipes the result into
``tippecanoe``. The City boundary is fetched from the Census TIGERweb service.

Requires ``ogr2ogr`` (GDAL) and ``tippecanoe`` on PATH.
"""

from __future__ import annotations

import csv
import gzip
import json
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="Build parcel-map data assets.")

# Inner path to the shapefile within the distributed zip.
SHP_IN_ZIP = "/vsizip/{zip}/LACounty_Parcels_Shapefile/LACounty_Parcels.shp"

# City of Los Angeles (GEOID 0644000) from Census TIGERweb incorporated places.
BOUNDARY_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
    "Places_CouSub_ConCity_SubMCD/MapServer/4/query"
    "?where=GEOID%3D%270644000%27&outFields=NAME,GEOID"
    "&returnGeometry=true&geometryPrecision=6&outSR=4326&f=geojson"
)

# City of LA GeoHub "Street Lights" (Bureau of Street Lighting) point service.
STREETLIGHTS_URL = (
    "https://maps.lacity.org/lahub/rest/services/"
    "Bureau_of_Street_Lighting/MapServer/0/query"
)
# Keep only lights in the BSL system (drop those flagged "Not BSL Maintained").
STREETLIGHTS_WHERE = "STATUS<>'Not BSL Maintained'"

# Viridis anchors (0..1) used to sample an N-color ramp.
_VIRIDIS = [
    (0.00, (68, 1, 84)),
    (0.13, (72, 40, 120)),
    (0.25, (62, 73, 137)),
    (0.38, (49, 104, 142)),
    (0.50, (38, 130, 142)),
    (0.63, (31, 158, 137)),
    (0.75, (53, 183, 121)),
    (0.88, (110, 206, 88)),
    (1.00, (253, 231, 37)),
]


def _viridis(t: float) -> str:
    """Sample the viridis ramp at ``t`` in [0, 1] and return a hex color."""
    t = max(0.0, min(1.0, t))
    for (t0, c0), (t1, c1) in zip(_VIRIDIS, _VIRIDIS[1:]):
        if t <= t1:
            f = 0 if t1 == t0 else (t - t0) / (t1 - t0)
            r, g, b = (round(a + (b_ - a) * f) for a, b_ in zip(c0, c1))
            return f"#{r:02x}{g:02x}{b:02x}"
    return "#fde725"


def _normalize(apn: str) -> str:
    """Reduce an APN/AIN to its bare ten-digit form."""
    return re.sub(r"\D", "", apn or "")


def _num(s: str):
    """Parse a numeric string to int/float, else return None."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        f = float(s)
        return int(f) if f.is_integer() else f
    except ValueError:
        return None


# Assessment-roll columns surfaced on each parcel (CSV name -> tile property).
ROLL_FIELDS = [
    "lighting_class", "lot_size", "units", "land_use", "parcel_size_land_use",
    "land_use_benefit_points", "parcel_size_benefit_points",
    "special_benefit_points", "assessment",
]
# Roll columns that are text (kept verbatim); the rest are parsed as numbers.
ROLL_TEXT_FIELDS = {"lighting_class", "land_use", "parcel_size_land_use"}


def _landuse_stats(roll_csv: Path) -> dict:
    """Per land-use totals from the roll: vote (ballot) count and assessed $.

    One ballot per assessed parcel; assessed value is the sum of annual
    assessments. The browser sums the selected categories for live percentages.
    """
    cats: dict[str, dict] = {}
    with roll_csv.open(newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                val = float(row["assessment"])
            except (TypeError, ValueError):
                continue
            c = cats.setdefault(row["land_use"], {"votes": 0, "assessed": 0.0})
            c["votes"] += 1
            c["assessed"] += val
    for c in cats.values():
        c["assessed"] = round(c["assessed"], 2)
    return {
        "categories": cats,
        "total": {
            "votes": sum(c["votes"] for c in cats.values()),
            "assessed": round(sum(c["assessed"] for c in cats.values()), 2),
        },
    }


def _load_keep(city_csv: Path, roll_csv: Path, prefix: str | None):
    """Build {ain: (roll_row_or_None, in_city, in_district)} and roll values.

    ``roll_row`` is the parcel's assessment record (the ROLL_FIELDS as a dict).
    Returns ``(keep, assessment_values)`` where ``assessment_values`` is every
    assessment in the roll (used for the color scale, independent of ``prefix``).
    """
    roll: dict[str, dict] = {}
    values: list[float] = []
    with roll_csv.open(newline="") as fh:
        for row in csv.DictReader(fh):
            ain = _normalize(row["apn"])
            try:
                val = float(row["assessment"])
            except (TypeError, ValueError):
                continue
            rec = {}
            for f in ROLL_FIELDS:
                raw = row.get(f, "")
                rec[f] = raw.strip() if f in ROLL_TEXT_FIELDS else _num(raw)
            rec["assessment"] = round(val, 2)
            roll[ain] = rec
            values.append(val)

    city: set[str] = set()
    with city_csv.open(newline="") as fh:
        for row in csv.DictReader(fh):
            ain = _normalize(row.get("APN", ""))
            if len(ain) == 10:
                city.add(ain)

    keep: dict[str, tuple[dict | None, bool, bool]] = {}
    for ain in city | set(roll):
        if prefix and not ain.startswith(prefix):
            continue
        keep[ain] = (roll.get(ain), ain in city, ain in roll)
    return keep, values


def _log_breaks(values: list[float], bins: int) -> list[float]:
    """Return ``bins - 1`` log-spaced cut points between p2 and p98."""
    import math

    if not values:
        return []
    s = sorted(values)
    n = len(s)
    lo = max(0.01, s[int(0.02 * n)])
    hi = s[min(n - 1, int(0.98 * n))]
    l0, l1 = math.log(lo), math.log(hi)
    out: list[float] = []
    for i in range(1, bins):
        b = round(math.exp(l0 + (l1 - l0) * i / bins), 2)
        if not out or b > out[-1]:
            out.append(b)
    return out


def _quantile_breaks(values: list[float], bins: int) -> list[float]:
    """Return ``bins - 1`` quantile cut points over sorted ``values``."""
    if not values:
        return []
    s = sorted(values)
    n = len(s)
    breaks = []
    for i in range(1, bins):
        idx = min(n - 1, int(round(i / bins * n)))
        breaks.append(round(s[idx], 2))
    # De-duplicate while preserving order (skewed data can repeat a cut point).
    out: list[float] = []
    for b in breaks:
        if not out or b > out[-1]:
            out.append(b)
    return out


def _write_scale(out_dir: Path, breaks: list[float], novalue: str) -> Path:
    """Write scale.json (breaks + one color per bin) and return its path."""
    colors = [_viridis(i / max(1, len(breaks))) for i in range(len(breaks) + 1)]
    scale = {
        "field": "assessment",
        "breaks": breaks,
        "colors": colors,
        "novalue_color": novalue,
    }
    path = out_dir / "scale.json"
    path.write_text(json.dumps(scale, indent=2))
    return path


def _fetch_boundary(url: str, raw_cache: Path, out_path: Path) -> None:
    """Fetch the city boundary GeoJSON, cache it, and copy to the web dir."""
    with urllib.request.urlopen(url, timeout=120) as resp:
        data = resp.read()
    obj = json.loads(data)
    if not obj.get("features"):
        raise RuntimeError(f"boundary fetch returned no features from {url}")
    raw_cache.write_bytes(data)
    out_path.write_bytes(data)


def _centroid(geom: dict) -> list[float]:
    """Approximate centroid (mean of the outer ring) of a (Multi)Polygon."""
    coords = geom["coordinates"]
    ring = coords[0] if geom["type"] == "Polygon" else coords[0][0]
    n = len(ring)
    return [sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n]


def _stream_filtered(
    shp: str, keep: dict, where: str | None,
    filtered: Path, district: Path, addresses: Path,
) -> tuple[int, int, int]:
    """Stream parcels through ogr2ogr, writing:

    * wanted parcel polygons (with ``ain`` and, if in the roll, the assessment
      fields) to ``filtered``;
    * District 5500 polygons to ``district`` for the single-color layer;
    * an ``address<TAB>ain<TAB>lng<TAB>lat`` index to ``addresses`` for the
      client-side address search.

    Returns ``(total_seen, matched, addressed)``.
    """
    cmd = [
        "ogr2ogr", "-f", "GeoJSONSeq", "/vsistdout/", shp,
        "-select", "AIN,SitusFullA", "-t_srs", "EPSG:4326",
    ]
    if where:
        cmd += ["-where", where]

    total = matched = addressed = 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    with filtered.open("w") as out, district.open("w") as dout, addresses.open("w") as aout:
        for line in proc.stdout:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            total += 1
            feat = json.loads(line)
            props_in = feat["properties"]
            ain = props_in.get("AIN")
            info = keep.get(ain) if ain else None
            if info is None:
                continue
            row, in_city, in_district = info
            addr = (props_in.get("SitusFullA") or "").strip()
            if addr:
                # Drop the redundant city (every parcel here is City of LA).
                addr = re.sub(r"\s+", " ", addr.replace(" LOS ANGELES CA", "")).strip()
            # Address is NOT stored on the tile (kept only in addresses.tsv.gz);
            # the popup looks it up by AIN from that index to keep tiles small.
            props = {"ain": ain}
            if row is not None:
                props.update(row)  # assessment + units + land use + benefit points
            if in_district:
                # Carry land_use so the district layer is filterable by type;
                # adjacent same-land_use parcels still coalesce at low zoom.
                dout.write(json.dumps(
                    {"type": "Feature",
                     "properties": {"land_use": (row or {}).get("land_use", "")},
                     "geometry": feat["geometry"]},
                    separators=(",", ":")) + "\n")
            if addr:
                c = _centroid(feat["geometry"])
                aout.write(f"{addr}\t{ain}\t{c[0]:.6f}\t{c[1]:.6f}\n")
                addressed += 1
            feat["properties"] = props
            out.write(json.dumps(feat, separators=(",", ":")) + "\n")
            matched += 1
    if proc.wait() != 0:
        raise RuntimeError("ogr2ogr failed")
    return total, matched, addressed


def _run_tippecanoe(
    filtered: Path, pmtiles: Path, min_zoom: int, max_zoom: int
) -> None:
    """Run tippecanoe to produce the PMTiles pyramid."""
    cmd = [
        "tippecanoe", "-o", str(pmtiles), "-l", "parcels", "-P", "--force",
        f"-Z{min_zoom}", f"-z{max_zoom}",
        "--drop-densest-as-needed", "--coalesce-smallest-as-needed",
        "--extend-zooms-if-still-dropping", "--simplification=10",
        str(filtered),
    ]
    subprocess.run(cmd, check=True)


def _fetch_streetlights(url: str, where: str, raw_cache: Path, out_seq: Path) -> int:
    """Page through the ArcGIS streetlight service, writing GeoJSONSeq points.

    Also caches a single combined GeoJSON FeatureCollection to ``raw_cache``.
    Returns the number of lights written.
    """
    fields = "SLID,STATUS,POSTDESC,LAMPA,TOOLTIP"
    page = 5000
    offset = 0
    count = 0
    raw_features: list[dict] = []
    with out_seq.open("w") as out:
        while True:
            params = {
                "where": where,
                "outFields": fields,
                "outSR": "4326",
                "f": "geojson",
                "orderByFields": "OBJECTID",
                "resultOffset": str(offset),
                "resultRecordCount": str(page),
            }
            req = f"{url}?{urllib.parse.urlencode(params)}"
            with urllib.request.urlopen(req, timeout=120) as resp:
                obj = json.loads(resp.read())
            feats = obj.get("features", [])
            if not feats:
                break
            for f in feats:
                out.write(json.dumps(f, separators=(",", ":")) + "\n")
                raw_features.append(f)
            count += len(feats)
            offset += len(feats)
            if len(feats) < page:
                break
    raw_cache.write_text(
        json.dumps({"type": "FeatureCollection", "features": raw_features})
    )
    return count


def _run_tippecanoe_simple(
    seq: Path, pmtiles: Path, layer: str, min_zoom: int, max_zoom: int
) -> None:
    """Tile a GeoJSONSeq of points into a PMTiles layer (drop-densest at low zoom)."""
    cmd = [
        "tippecanoe", "-o", str(pmtiles), "-l", layer, "-P", "--force",
        f"-Z{min_zoom}", f"-z{max_zoom}", "--drop-densest-as-needed",
        str(seq),
    ]
    subprocess.run(cmd, check=True)


def _run_tippecanoe_district(
    seq: Path, pmtiles: Path, min_zoom: int, max_zoom: int
) -> None:
    """Build the single-color District 5500 coverage layer.

    Each polygon carries ``land_use`` (so the layer is filterable by type);
    tippecanoe coalesces adjacent same-land_use parcels into solid regions so
    the footprint survives low zoom (where individual parcels would drop out).
    """
    cmd = [
        "tippecanoe", "-o", str(pmtiles), "-l", "district", "-P", "--force",
        f"-Z{min_zoom}", f"-z{max_zoom}",
        "--coalesce-densest-as-needed", "--coalesce-smallest-as-needed",
        "--extend-zooms-if-still-dropping", "--simplification=10",
        str(seq),
    ]
    subprocess.run(cmd, check=True)


@app.command()
def main(
    shapefile_zip: Path = typer.Option(
        Path("sources/LACounty_Parcels_Shapefile.zip"), "--shapefile-zip"
    ),
    city_csv: Path = typer.Option(
        Path("sources/APNs_in_the_City_of_Los_Angeles_20260627.csv"), "--city-csv"
    ),
    roll_csv: Path = typer.Option(
        Path("sources/parsed_assessments.csv"), "--roll-csv"
    ),
    out_dir: Path = typer.Option(Path("web/data"), "--out-dir"),
    boundary_url: str = typer.Option(BOUNDARY_URL, "--boundary-url"),
    bins: int = typer.Option(7, "--bins", help="Number of choropleth color bins."),
    extra_breaks: str = typer.Option(
        "1000,3000,8000", "--extra-breaks",
        help="Comma-separated high-end break values appended above the computed scale.",
    ),
    scale_kind: str = typer.Option(
        "quantile", "--scale", help="Color-break method: 'quantile' or 'log'."
    ),
    min_zoom: int = typer.Option(6, "--min-zoom"),
    max_zoom: int = typer.Option(16, "--max-zoom"),
    novalue_color: str = typer.Option("#d9d9d9", "--novalue-color"),
    ain_prefix: str = typer.Option(
        "", "--ain-prefix", help="Limit to AINs with this prefix (fast test build)."
    ),
    skip_boundary: bool = typer.Option(False, "--skip-boundary"),
    skip_tiles: bool = typer.Option(False, "--skip-tiles"),
    skip_streetlights: bool = typer.Option(False, "--skip-streetlights"),
    streetlights_where: str = typer.Option(
        STREETLIGHTS_WHERE, "--streetlights-where",
        help="ArcGIS WHERE clause selecting which lights to include.",
    ),
) -> None:
    """Build parcels.pmtiles, city_boundary.geojson, and scale.json."""
    for tool in ("ogr2ogr", "tippecanoe"):
        if not shutil.which(tool):
            typer.secho(f"{tool} not found on PATH.", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
    if not shapefile_zip.exists():
        typer.secho(f"Not found: {shapefile_zip}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = ain_prefix or None

    keep, values = _load_keep(city_csv, roll_csv, prefix)
    typer.echo(f"Parcels to keep: {len(keep):,}  (roll values: {len(values):,})")

    if scale_kind == "log":
        breaks = _log_breaks(values, bins)
    else:
        breaks = _quantile_breaks(values, bins)
    # Append explicit high-end breaks (extra bins above the computed scale) so
    # high-value parcels aren't all lumped into one top bin.
    for b in (float(x) for x in extra_breaks.split(",") if x.strip()):
        if not breaks or b > breaks[-1]:
            breaks.append(round(b, 2))
    breaks = sorted(set(breaks))

    scale_path = _write_scale(out_dir, breaks, novalue_color)
    typer.echo(f"Wrote {scale_path}  ({scale_kind}) breaks={breaks}")

    stats = _landuse_stats(roll_csv)
    (out_dir / "landuse_stats.json").write_text(json.dumps(stats))
    typer.echo(f"Wrote {out_dir / 'landuse_stats.json'}  "
               f"({stats['total']['votes']:,} votes, ${stats['total']['assessed']:,.0f})")

    if not skip_boundary:
        raw = Path("sources/la_city_boundary.geojson")
        _fetch_boundary(boundary_url, raw, out_dir / "city_boundary.geojson")
        typer.secho(f"Fetched city boundary -> {out_dir / 'city_boundary.geojson'}",
                    fg=typer.colors.GREEN)

    if not skip_tiles:
        shp = SHP_IN_ZIP.format(zip=shapefile_zip)
        where = f"AIN LIKE '{prefix}%'" if prefix else None
        filtered = out_dir / "parcels.geojsonseq"
        distseq = out_dir / "district.geojsonseq"
        addresses = out_dir / "addresses.tsv"
        typer.echo("Streaming parcels through ogr2ogr (this scans ~2.4M features)...")
        total, matched, addressed = _stream_filtered(
            shp, keep, where, filtered, distseq, addresses)
        typer.echo(f"  scanned {total:,} parcels, matched {matched:,}, "
                   f"addresses {addressed:,}")
        unmatched = len(keep) - matched
        if unmatched:
            typer.secho(f"  {unmatched:,} kept AINs had no geometry in the shapefile",
                        fg=typer.colors.YELLOW)
        pmtiles = out_dir / "parcels.pmtiles"
        _run_tippecanoe(filtered, pmtiles, min_zoom, max_zoom)
        typer.secho(f"Wrote {pmtiles} ({pmtiles.stat().st_size/1e6:.1f} MB)",
                    fg=typer.colors.GREEN)
        district_pm = out_dir / "district.pmtiles"
        _run_tippecanoe_district(distseq, district_pm, min_zoom, max_zoom)
        typer.secho(f"Wrote {district_pm} ({district_pm.stat().st_size/1e6:.1f} MB)",
                    fg=typer.colors.GREEN)
        # Gzip the address index for the browser (fetched + decompressed
        # client-side) and drop the uncompressed copy.
        addr_gz = out_dir / "addresses.tsv.gz"
        with addresses.open("rb") as f_in, gzip.open(addr_gz, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
        addresses.unlink(missing_ok=True)
        typer.secho(
            f"Wrote {addr_gz} ({addr_gz.stat().st_size/1e6:.1f} MB gzipped)",
            fg=typer.colors.GREEN,
        )
        filtered.unlink(missing_ok=True)
        distseq.unlink(missing_ok=True)

    if not skip_streetlights:
        typer.echo("Fetching BSL streetlights (paginated)...")
        raw = Path("sources/bsl_streetlights.geojson")
        seq = out_dir / "streetlights.geojsonseq"
        n = _fetch_streetlights(STREETLIGHTS_URL, streetlights_where, raw, seq)
        sl_pm = out_dir / "streetlights.pmtiles"
        _run_tippecanoe_simple(seq, sl_pm, "streetlights", min_zoom, max_zoom)
        seq.unlink(missing_ok=True)
        typer.secho(f"Wrote {sl_pm} ({n:,} lights, {sl_pm.stat().st_size/1e6:.1f} MB)",
                    fg=typer.colors.GREEN)

    typer.secho("Done.", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
