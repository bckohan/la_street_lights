"""Parse Bureau of Street Lighting assessment-roll PDFs into a single CSV.

The source PDFs (named ``####_####.pdf`` in ``sources/``) are giant, multi-
thousand-page tables of per-parcel streetlight assessments. Each data row has
ten columns::

    APN | Lighting Class | Lot Size | Units
        | Land Use for Land Use Benefit Points
        | Land Use for Parcel Size Benefit Points
        | Land Use Benefit Points | Parcel Size Benefit Points
        | Special Benefit Points | Assessment

The PDFs render as cleanly aligned fixed-layout tables. We extract the text
with ``pdftotext -layout`` (Poppler) and split each data row on runs of two or
more spaces, which reliably yields exactly the ten columns even when a value
contains single internal spaces (e.g. ``MFR 2-4 Units``) or commas (e.g. a
lighting class of ``1,2,3`` or a thousands-separated assessment).
"""

from __future__ import annotations

import csv
import re
import shutil
import subprocess
from pathlib import Path

import typer

app = typer.Typer(
    add_completion=False,
    help="Parse street-light assessment-roll PDFs into CSV.",
)

# Output column names, in order. The two free-text "land use" columns are named
# for the benefit-point column they classify.
FIELDNAMES = [
    "apn",
    "lighting_class",
    "lot_size",
    "units",
    "land_use",  # "Land Use for Land Use Benefit Points" (Residential, Industrial, ...)
    "parcel_size_land_use",  # "Land Use for Parcel Size Benefit Points" (SFR, MFR 2-4 Units, ...)
    "land_use_benefit_points",
    "parcel_size_benefit_points",
    "special_benefit_points",
    "assessment",
    "source_pdf",
]

# A data row begins with an Assessor's Parcel Number: ####-###-###.
APN_RE = re.compile(r"^\d{4}-\d{3}-\d{3}$")
# Source PDFs are named like "2000_2499.pdf" (digits, underscore, digits).
PDF_NAME_RE = re.compile(r"^\d+_\d+\.pdf$")
# Columns are separated by runs of two or more spaces in -layout output.
COLUMN_SPLIT_RE = re.compile(r"\s{2,}")


def _pdf_to_layout_text(pdf: Path) -> str:
    """Return the full ``pdftotext -layout`` rendering of *pdf*."""
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _parse_row(line: str, source: str) -> dict[str, str] | None:
    """Parse one layout line into a row dict, or ``None`` if it is not data."""
    fields = COLUMN_SPLIT_RE.split(line.strip())
    if len(fields) != 10 or not APN_RE.match(fields[0]):
        return None
    # The assessment may carry a leading "$" and thousands separators.
    fields[9] = fields[9].lstrip("$").replace(",", "")
    row = dict(zip(FIELDNAMES, fields))
    row["source_pdf"] = source
    return row


def _iter_rows(pdf: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Return parsed rows and any anomalous (APN-looking but unparsable) lines."""
    rows: list[dict[str, str]] = []
    anomalies: list[str] = []
    for line in _pdf_to_layout_text(pdf).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        row = _parse_row(line, pdf.name)
        if row is not None:
            rows.append(row)
        elif stripped[:4].isdigit() and "-" in stripped[:12]:
            # Looks like it starts with an APN but didn't parse cleanly.
            anomalies.append(stripped)
    return rows, anomalies


@app.command()
def main(
    pdfs: list[Path] = typer.Argument(
        None,
        help="PDF files to parse. Defaults to ####_####.pdf files in --sources-dir.",
    ),
    output: Path = typer.Option(
        Path("sources/parsed_assessments.csv"),
        "--output",
        "-o",
        help="Destination CSV path.",
    ),
    sources_dir: Path = typer.Option(
        Path("sources"),
        "--sources-dir",
        help="Directory scanned for ####_####.pdf files when none are given.",
    ),
) -> None:
    """Parse assessment-roll PDFs and write a combined CSV."""
    if not shutil.which("pdftotext"):
        typer.secho(
            "pdftotext not found. Install Poppler (e.g. `brew install poppler`).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    if not pdfs:
        pdfs = sorted(
            p for p in sources_dir.glob("*.pdf") if PDF_NAME_RE.match(p.name)
        )
    if not pdfs:
        typer.secho("No matching PDFs found.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    output.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    total_anomalies = 0
    with output.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for pdf in pdfs:
            typer.echo(f"Parsing {pdf.name} ...")
            rows, anomalies = _iter_rows(pdf)
            writer.writerows(rows)
            total += len(rows)
            total_anomalies += len(anomalies)
            typer.echo(f"  {len(rows):>7,} rows" + (f"  ({len(anomalies)} anomalies)" if anomalies else ""))
            for bad in anomalies[:5]:
                typer.secho(f"    ! {bad[:100]}", fg=typer.colors.YELLOW, err=True)

    typer.secho(
        f"Wrote {total:,} rows from {len(pdfs)} PDF(s) to {output}"
        + (f" ({total_anomalies} anomalies skipped)" if total_anomalies else ""),
        fg=typer.colors.GREEN,
    )


if __name__ == "__main__":
    app()
